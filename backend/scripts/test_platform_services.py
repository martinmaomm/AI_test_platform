"""Isolated platform supervisor tests using only local dummy processes."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from unittest.mock import call, patch

import psutil

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from platform_services import (  # noqa: E402
    EngineOptions,
    ManagerServer,
    RuntimePaths,
    STOP_ORDER,
    SupervisorEngine,
    _default_names,
    _print_status,
    canonical_command,
)


SLEEP_CODE = "import time; time.sleep(60)"
CRASH_CODE = "raise SystemExit(7)"


class PlatformServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="platform-services-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.paths = RuntimePaths(self.root / "state", self.root / "logs")
        self.engines: list[SupervisorEngine] = []
        self.external: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for engine in reversed(self.engines):
            names = [name for name in engine.specs if engine._running(name)]
            if names:
                engine.stop(names)
            engine.close(stop_services=False)
        for process in self.external:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def spec(self, name, *, code=SLEEP_CODE, enabled=True, ports=(), env=None, timeout=1.0):
        marker = f"fixture-{self.root.name}-{name}"
        return {
            "label": name,
            "command": [sys.executable, "-c", code, marker],
            "cwd": str(self.root),
            "env": dict(os.environ, PYTHONUNBUFFERED="1", **(env or {})),
            "enabled": enabled,
            "disabled_reason": "测试配置明确禁用",
            "stop_timeout": timeout,
            "restart_delay": 0.01,
            "ports": list(ports),
        }

    def engine(self, specs, *, options=None, preflight=None, spec_loader=None):
        instance = SupervisorEngine(
            specs,
            paths=self.paths,
            options=options or EngineOptions(),
            preflight=preflight,
            spec_loader=spec_loader,
        )
        self.engines.append(instance)
        return instance

    @staticmethod
    def wait_until(predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_start_is_idempotent_and_stop_does_not_resurrect(self):
        engine = self.engine({"backend": self.spec("backend")})
        first = engine.start(["backend"])["backend"]
        second = engine.start(["backend"])["backend"]
        self.assertTrue(first["ok"])
        self.assertEqual(first["pid"], second["pid"])

        stopped = engine.stop(["backend"])["backend"]
        self.assertTrue(stopped["ok"])
        for _ in range(5):
            engine.tick()
            time.sleep(0.02)
        self.assertFalse(engine.status()["services"]["backend"]["running"])
        self.assertFalse(engine.records["backend"]["desired"])

    def test_single_service_restart_preserves_other_pid(self):
        engine = self.engine({"backend": self.spec("backend"), "celery": self.spec("celery")})
        started = engine.start(["backend", "celery"])
        old_backend = started["backend"]["pid"]
        celery_pid = started["celery"]["pid"]

        restarted = engine.restart(["backend"])["backend"]

        self.assertTrue(restarted["ok"])
        self.assertNotEqual(old_backend, restarted["pid"])
        self.assertEqual(celery_pid, engine.status()["services"]["celery"]["pid"])

    def test_full_restart_stops_then_starts_in_dependency_order(self):
        specs = {name: self.spec(name) for name in ("backend", "celery", "controller", "caddy")}
        engine = self.engine(specs)
        engine.start(specs)
        old_pids = {name: engine.records[name]["pid"] for name in specs}

        with patch.object(engine, "stop", wraps=engine.stop) as stop, patch.object(engine, "start", wraps=engine.start) as start:
            results = engine.restart(["backend", "caddy", "controller", "celery"])

        stop.assert_called_once_with(["controller", "celery", "caddy", "backend"])
        self.assertEqual(
            [call(["backend"], manual=True), call(["celery"], manual=True),
             call(["controller"], manual=True), call(["caddy"], manual=True)],
            start.call_args_list,
        )
        self.assertTrue(all(item["ok"] for item in results.values()))
        self.assertTrue(all(engine.records[name]["pid"] != old_pids[name] for name in specs))

    def test_crash_backoff_limit_and_manual_restart_reset(self):
        options = EngineOptions(
            poll_interval=0.01,
            restart_window=5,
            restart_limit=2,
            minimum_restart_delay=0.01,
            controller_restart_delay=0.02,
            max_restart_delay=0.04,
        )
        engine = self.engine({"backend": self.spec("backend", code=CRASH_CODE)}, options=options)
        self.assertTrue(engine.start(["backend"])["backend"]["ok"])

        def blocked():
            engine.tick()
            return engine.records["backend"]["restart_blocked"]

        self.assertTrue(self.wait_until(blocked), engine.records["backend"])
        self.assertEqual(2, len(engine.records["backend"]["restart_attempts"]))
        self.assertEqual("abnormal", engine.records["backend"]["state"])

        restarted = engine.restart(["backend"])["backend"]
        self.assertTrue(restarted["ok"])
        self.assertFalse(engine.records["backend"]["restart_blocked"])
        self.assertEqual([], engine.records["backend"]["restart_attempts"])

    def test_stale_pid_is_never_stopped(self):
        engine = self.engine({"backend": self.spec("backend")})
        unrelated = subprocess.Popen(
            [sys.executable, "-c", SLEEP_CODE, "unrelated"],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.external.append(unrelated)
        engine.records["backend"].update(
            pid=unrelated.pid,
            create_time=psutil.Process(unrelated.pid).create_time() - 10,
            desired=True,
            state="running",
        )

        result = engine.stop(["backend"])["backend"]

        self.assertTrue(result["ok"])
        self.assertIsNone(unrelated.poll())

    def test_equivalent_external_process_is_reported_but_not_killed(self):
        spec = self.spec("backend")
        manual = subprocess.Popen(
            spec["command"],
            cwd=spec["cwd"],
            env=spec["env"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.external.append(manual)
        engine = self.engine({"backend": spec})

        result = engine.start(["backend"])["backend"]

        self.assertFalse(result["ok"])
        self.assertIn("未纳管", result["detail"])
        self.assertIsNone(manual.poll())

    def test_port_conflict_is_reported_but_not_touched(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        spec = self.spec("backend", ports=[{"host": "127.0.0.1", "port": str(port)}])
        engine = self.engine({"backend": spec})

        result = engine.start(["backend"])["backend"]

        self.assertFalse(result["ok"])
        self.assertIn("端口", result["detail"])
        self.assertEqual(port, listener.getsockname()[1])

    def test_new_manager_adopts_verified_orphan_without_duplicate(self):
        specs = {"backend": self.spec("backend")}
        first = self.engine(specs)
        original_pid = first.start(["backend"])["backend"]["pid"]
        # A real killed manager disappears without running Python cleanup. Keep
        # this Popen handle until teardown so unittest does not emit a false leak warning.

        successor = self.engine(specs)
        result = successor.start(["backend"])["backend"]

        self.assertTrue(result["ok"])
        self.assertEqual(original_pid, result["pid"])
        self.assertEqual(original_pid, successor.status()["services"]["backend"]["pid"])

    def test_fallback_port_check_distinguishes_live_listener_from_time_wait(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        address = listener.getsockname()
        listener.listen(1)
        engine = self.engine({"backend": self.spec("backend", ports=[{"host": address[0], "port": address[1]}])})
        with patch('platform_services.psutil.net_connections', side_effect=psutil.AccessDenied()):
            self.assertTrue(engine._port_conflicts('backend'))
            with socket.create_connection(address, timeout=1) as client:
                accepted, _ = listener.accept()
                accepted.close()
                self.assertEqual(b'', client.recv(1))
            listener.close()
            self.assertEqual([], engine._port_conflicts('backend'))

    def test_reload_uses_current_child_environment_without_persisting_it(self):
        current = {"backend": self.spec("backend", env={"ROTATING_VALUE": "first", "PRIVATE_FIXTURE": "do-not-store"})}

        def loader():
            return {name: dict(spec) for name, spec in current.items()}

        engine = self.engine(current, spec_loader=loader)
        first_pid = engine.start(["backend"])["backend"]["pid"]
        current["backend"] = self.spec("backend", env={"ROTATING_VALUE": "second", "PRIVATE_FIXTURE": "new-secret"})
        restarted = engine.restart(["backend"])["backend"]

        self.assertTrue(restarted["ok"])
        self.assertNotEqual(first_pid, restarted["pid"])
        self.assertEqual("second", engine.specs["backend"]["env"]["ROTATING_VALUE"])
        persisted = self.paths.state_file.read_text(encoding="utf-8")
        self.assertNotIn("ROTATING_VALUE", persisted)
        self.assertNotIn("PRIVATE_FIXTURE", persisted)
        self.assertNotIn("new-secret", persisted)

    def test_disabled_and_preflight_errors_are_chinese_and_do_not_leak_env(self):
        secret = "fixture-password-never-print"
        disabled = self.spec("controller", enabled=False, env={"DB_PASSWORD": secret})
        engine = self.engine({"controller": disabled})
        result = engine.start(["controller"])["controller"]
        serialized = json.dumps({"result": result, "status": engine.status()}, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertIn("已禁用", result["detail"])
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, self.paths.state_file.read_text(encoding="utf-8"))
        self.assertEqual(0o700, self.paths.state_dir.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.paths.state_file.stat().st_mode & 0o777)

    def test_status_text_separates_liveness_health_and_prints_detail(self):
        payload = {
            "manager": {"running": True},
            "services": {
                "celery": {
                    "state": "running",
                    "pid": 123,
                    "healthy": False,
                    "restart_attempts": 0,
                    "restart_limit": 5,
                    "log_path": "/tmp/celery.log",
                    "detail": "进程存活",
                    "health_detail": "Worker 未及时响应，可能正忙",
                }
            },
        }
        output = io.StringIO()
        with redirect_stdout(output):
            _print_status(payload)
        text = output.getvalue()
        self.assertIn("运行中", text)
        self.assertIn("不健康", text)
        self.assertIn("Worker 未及时响应，可能正忙", text)

    def test_celery_binary_and_python_module_forms_are_equivalent(self):
        cwd = str(self.root)
        module = canonical_command([sys.executable, "-m", "celery", "-A", "config", "worker"], cwd)
        binary = canonical_command(["celery", "-A", "config", "worker"], cwd)
        self.assertEqual(module, binary)

    def test_macos_python_app_executable_matches_venv_script_semantics(self):
        cwd = str(self.root)
        expected = canonical_command([str(self.root / ".venv/bin/python"), "manage.py", "runserver"], cwd)
        observed = canonical_command(
            ["/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python", "manage.py", "runserver"],
            cwd,
        )
        self.assertEqual(expected, observed)

    def test_caddy_external_detection_uses_complete_command_across_cwd(self):
        managed_cwd = self.root / "backend"
        manual_cwd = self.root / "repository-root"
        managed_cwd.mkdir()
        manual_cwd.mkdir()
        spec = self.spec("caddy")
        spec.update(command=["/usr/bin/tail", "-f", "/dev/null"], cwd=str(managed_cwd))
        manual = subprocess.Popen(
            spec["command"],
            cwd=manual_cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.external.append(manual)
        engine = self.engine({"caddy": spec})

        status = engine.status()["services"]["caddy"]
        result = engine.start(["caddy"])["caddy"]

        self.assertEqual("unmanaged", status["state"])
        self.assertIn(str(manual.pid), status["detail"])
        self.assertFalse(result["ok"])
        self.assertIsNone(manual.poll())

    def test_default_full_stop_order_preserves_controller_shutdown_path(self):
        specs = {
            name: self.spec(name, enabled=name != "caddy")
            for name in ("backend", "celery", "controller", "caddy")
        }
        state = {"services": {"caddy": {"pid": 123}}}
        self.assertEqual(list(STOP_ORDER), _default_names(specs, "stop", state))

    def test_warm_stop_timeout_keeps_verified_process_state_for_successor(self):
        stubborn_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        specs = {"celery": self.spec("celery", code=stubborn_code, timeout=0.05)}
        first = self.engine(specs)
        started = first.start(["celery"])["celery"]
        handle = first.process_handles["celery"]
        self.external.append(handle)
        time.sleep(0.1)  # Let the dummy process install its SIGTERM handler.
        result = first.stop(["celery"])["celery"]

        self.assertFalse(result["ok"])
        self.assertEqual("stopping", first.records["celery"]["state"])
        self.assertFalse(first.records["celery"]["desired"])
        self.assertEqual(started["pid"], first.records["celery"]["pid"])

        successor = self.engine(specs)
        self.assertEqual("stopping", successor.records["celery"]["state"])
        self.assertEqual(started["pid"], successor.records["celery"]["pid"])

    def test_disconnected_client_does_not_escape_or_undo_completed_action(self):
        class FakeEngine:
            specs = {"backend": {}}
            records = {"backend": {"desired": True}}

            def __init__(self):
                self.calls = 0

            def start(self, names):
                self.calls += 1
                return {names[0]: {"ok": True, "detail": "已启动"}}

            def status(self, names=None):
                return {"services": {}}

        manager = ManagerServer.__new__(ManagerServer)
        manager.engine = FakeEngine()
        manager.shutdown_when_idle = False
        client, server = socket.socketpair()
        client.sendall(b'{"action":"start","services":["backend"]}\n')
        client.close()
        with server:
            manager._serve_one(server, read_timeout=0.1)
        self.assertEqual(1, manager.engine.calls)

        # A later client remains serviceable after the vanished first client.
        client, server = socket.socketpair()
        client.sendall(b'{"action":"status","services":["backend"]}\n')
        with client, server:
            manager._serve_one(server, read_timeout=0.1)
            self.assertIn(b'"ok": true', client.recv(4096))

    def test_silent_socketpair_timeout_is_isolated(self):
        manager = ManagerServer.__new__(ManagerServer)
        manager.engine = type("FakeEngine", (), {"specs": {}, "records": {}})()
        manager.shutdown_when_idle = False
        client, server = socket.socketpair()
        started = time.monotonic()
        with client, server:
            manager._serve_one(server, read_timeout=0.02)
        self.assertLess(time.monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
