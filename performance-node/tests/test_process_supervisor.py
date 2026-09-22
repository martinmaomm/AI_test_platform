from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

from performance_node.process_supervisor import (
    ProcessSupervisor,
    SupervisorConfigError,
    load_config,
)


class ProcessSupervisorTests(unittest.TestCase):
    def make_config(
        self,
        root: Path,
        engine_argv: list[str],
        *,
        tunnel_argv: list[str] | None = None,
        lease_seconds: int = 10,
        max_seconds: int = 5,
    ) -> Path:
        lease = root / "lease"
        lease.touch()
        processes = []
        if tunnel_argv is not None:
            processes.append({
                "name": "tunnel", "argv": tunnel_argv, "cwd": str(root),
                "log_path": str(root / "tunnel.log"),
            })
        processes.append({
            "name": "engine", "argv": engine_argv, "cwd": str(root),
            "log_path": str(root / "engine.log"),
        })
        path = root / "supervisor.json"
        path.write_text(json.dumps({
            "processes": processes,
            "lease_file": str(lease),
            "lease_seconds": lease_seconds,
            "max_seconds": max_seconds,
            "result_file": str(root / "result.json"),
        }), encoding="utf-8")
        return path

    def test_engine_zero_is_finished_and_child_environment_is_allowlisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment_file = root / "environment.json"
            script = root / "engine.py"
            script.write_text(
                "import json, os, sys\n"
                "json.dump(dict(os.environ), open(sys.argv[1], 'w'))\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "PERFORMANCE_NODE_ENROLLMENT_TOKEN": "do-not-leak",
                "HTTPS_PROXY": "http://proxy.invalid",
                "UNRELATED_SECRET": "do-not-leak",
            }):
                config = load_config(self.make_config(
                    root, [sys.executable, str(script), str(environment_file)],
                ))
                self.assertEqual(ProcessSupervisor(config).run(), "finished")

            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result, {"reason": "finished", "exit_codes": {"engine": 0}})
            child_environment = json.loads(environment_file.read_text(encoding="utf-8"))
            self.assertEqual(
                set(child_environment),
                {"PATH", "LANG", "LC_ALL", "PYTHONUNBUFFERED"}
                | ({"TMPDIR"} if "TMPDIR" in child_environment else set())
                | ({"PYTHONPATH"} if "PYTHONPATH" in child_environment else set())
                | ({"PYTHONDONTWRITEBYTECODE"} if "PYTHONDONTWRITEBYTECODE" in child_environment else set())
                | ({"LOCUST_WORKER_LOG_REPORT_INTERVAL"} if "LOCUST_WORKER_LOG_REPORT_INTERVAL" in child_environment else set())
                | ({"__CF_USER_TEXT_ENCODING"} if "__CF_USER_TEXT_ENCODING" in child_environment else set()),
            )
            self.assertNotIn("PERFORMANCE_NODE_ENROLLMENT_TOKEN", child_environment)
            self.assertNotIn("HTTPS_PROXY", child_environment)
            self.assertEqual((root / "result.json").stat().st_mode & 0o777, 0o600)

    def test_tunnel_exit_fails_and_reaps_running_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_pid = root / "engine.pid"
            engine = [sys.executable, "-c", (
                "import os,pathlib,time; "
                f"pathlib.Path({str(engine_pid)!r}).write_text(str(os.getpid())); "
                "time.sleep(60)"
            )]
            tunnel = [sys.executable, "-c", "import time; time.sleep(0.8)"]
            config = load_config(self.make_config(root, engine, tunnel_argv=tunnel))
            self.assertEqual(ProcessSupervisor(config).run(), "child_failed")
            pid = int(engine_pid.read_text(encoding="utf-8"))
            self.assertFalse(psutil.pid_exists(pid))
            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["reason"], "child_failed")
            self.assertNotEqual(result["exit_codes"]["tunnel"], None)
            self.assertNotEqual(result["exit_codes"]["engine"], None)

    def test_expired_lease_reaps_engine_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pids_file = root / "pids"
            script = root / "tree.py"
            script.write_text(
                "import os, pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}')\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            config = load_config(self.make_config(
                root, [sys.executable, str(script), str(pids_file)], lease_seconds=1,
            ))
            supervisor = ProcessSupervisor(config)
            thread = threading.Thread(target=supervisor.run)
            thread.start()
            deadline = time.monotonic() + 3
            while not pids_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pids_file.exists())
            os.utime(config.lease_file, (time.time() - 5, time.time() - 5))
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            parent_pid, child_pid = map(int, pids_file.read_text(encoding="utf-8").split())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and (psutil.pid_exists(parent_pid) or psutil.pid_exists(child_pid)):
                time.sleep(0.02)
            self.assertFalse(psutil.pid_exists(parent_pid))
            self.assertFalse(psutil.pid_exists(child_pid))
            result = json.loads(config.result_file.read_text(encoding="utf-8"))
            self.assertEqual(result["reason"], "lease_expired")

    def test_total_timeout_reaps_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(self.make_config(
                root, [sys.executable, "-c", "import time; time.sleep(60)"], max_seconds=1,
            ))
            self.assertEqual(ProcessSupervisor(config).run(), "timeout")
            self.assertEqual(
                json.loads(config.result_file.read_text(encoding="utf-8"))["reason"],
                "timeout",
            )

    def test_requested_stop_keeps_tunnel_until_engine_gracefully_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_done = root / "engine.done"
            tunnel_observed = root / "tunnel-observed"
            engine = [sys.executable, "-c", (
                "import pathlib,signal,time; stopped=[False]; "
                "signal.signal(signal.SIGTERM,lambda *_:stopped.__setitem__(0,True)); "
                "\nwhile not stopped[0]: time.sleep(.02)\n"
                f"pathlib.Path({str(engine_done)!r}).write_text('done')"
            )]
            tunnel = [sys.executable, "-c", (
                "import pathlib,signal,time; stopped=[False]; "
                "signal.signal(signal.SIGTERM,lambda *_:stopped.__setitem__(0,True)); "
                "\nwhile not stopped[0]: time.sleep(.02)\n"
                f"pathlib.Path({str(tunnel_observed)!r}).write_text(str(pathlib.Path({str(engine_done)!r}).exists()))"
            )]
            config = load_config(self.make_config(root, engine, tunnel_argv=tunnel))
            supervisor = ProcessSupervisor(config)
            thread = threading.Thread(target=supervisor.run)
            thread.start()
            deadline = time.monotonic() + 3
            while len(supervisor.children) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(supervisor.children), 2)
            time.sleep(0.2)
            supervisor.request_stop()
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(tunnel_observed.read_text(encoding="utf-8"), "True")

    def test_config_rejects_remote_shape_shell_strings_and_excessive_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.make_config(root, [sys.executable, "-c", "pass"])
            raw = json.loads(path.read_text(encoding="utf-8"))
            for mutate in (
                lambda value: value["processes"][0].update(argv="python -c pass"),
                lambda value: value.update(max_seconds=721),
                lambda value: value["processes"][0].update(name="remote"),
            ):
                candidate = json.loads(json.dumps(raw))
                mutate(candidate)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(SupervisorConfigError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
