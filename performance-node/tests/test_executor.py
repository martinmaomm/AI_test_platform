from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from shutil import which
from unittest.mock import patch

import psutil

from performance_node.config import NodeConfig
from performance_node.errors import AgentStopped, ProtocolError
from performance_node.executor import RunExecutor, RuntimeContract, validate_prepare_command
from performance_node.locust_runtime import canonical_sha256, validate_snapshot


def make_snapshot(run_id: str, node_id: str) -> dict:
    return {
        "schema_version": 4,
        "run_id": run_id,
        "nodes": [{"node_id": node_id, "users": 1, "validation_key": "a" * 64}],
        "engine_version": "2.43.3",
        "plan_name": "fixture",
        "base_url": "https://target.example",
        "allowed_methods": ["GET"],
        "mode": "validation",
        "users": 1,
        "spawn_rate": 1,
        "duration_seconds": 5,
        "wait_seconds": 0.1,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 30,
        "variables": {},
        "unique_variables": [],
        "steps": [{
            "name": "health", "phase": "main", "method": "GET", "path": "/health",
            "query": {}, "headers": {}, "body_type": "none", "body": None,
            "extract": [], "assertions": [
                {"check": "status_code", "comparator": "eq", "expected": 200},
            ],
        }],
    }


@unittest.skipUnless(which("openssl"), "openssl is required for executor certificate fixtures")
class RunExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.node_id = str(uuid.uuid4())
        self.run_id = str(uuid.uuid4())

        self.engine = self.root / "engine.py"
        self.engine.write_text(
            "import signal, time\n"
            "stopped = [False]\n"
            "signal.signal(signal.SIGTERM, lambda *_: stopped.__setitem__(0, True))\n"
            "signal.signal(signal.SIGINT, lambda *_: stopped.__setitem__(0, True))\n"
            "while not stopped[0]: time.sleep(.05)\n",
            encoding="utf-8",
        )
        self.tunnel = self.root / "stunnel"
        self.tunnel.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        self.tunnel.chmod(0o700)

        certificate_config = self.root / "certificate.cnf"
        certificate_config.write_text(
            "[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n"
            "[dn]\nCN=fixture-client\n[v3]\nbasicConstraints=critical,CA:true\n"
            "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
            "subjectAltName=DNS:localhost,IP:127.0.0.1\n",
            encoding="utf-8",
        )
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(self.root / "key.pem"), "-out", str(self.root / "cert.pem"),
            "-config", str(certificate_config),
        ], check=True, capture_output=True)
        self.cert_pem = (self.root / "cert.pem").read_text(encoding="utf-8")
        self.key_pem = (self.root / "key.pem").read_text(encoding="utf-8")
        self.config = NodeConfig(
            "https://platform.example", self.state_dir, True, str(self.tunnel),
        )
        self.contract = RuntimeContract(validate_snapshot, canonical_sha256, self.engine)
        self.contract_patch = patch("performance_node.executor.load_runtime_contract", return_value=self.contract)
        self.contract_patch.start()

    def tearDown(self):
        self.contract_patch.stop()
        self.temporary.cleanup()

    def command(self, **overrides) -> dict:
        snapshot = make_snapshot(self.run_id, self.node_id)
        source = self.engine.read_text(encoding="utf-8")
        result = {
            "type": "prepare",
            "run_id": self.run_id,
            "node_id": self.node_id,
            "snapshot": snapshot,
            "snapshot_sha256": canonical_sha256(snapshot),
            "script_source": source,
            "script_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "lease_seconds": 15,
            "max_seconds": 80,
            "tls": {
                "host": "127.0.0.1", "port": 9443, "server_name": "localhost",
                "ca_pem": self.cert_pem, "cert_pem": self.cert_pem, "key_pem": self.key_pem,
            },
            "handshake_token": "A" * 32,
        }
        result.update(overrides)
        return result

    def test_prepare_is_idempotent_renews_lease_and_terminal_run_never_replays(self):
        identity = self.state_dir / "identity.json"
        identity.write_text("identity-sentinel", encoding="utf-8")
        identity.chmod(0o600)
        executor = RunExecutor(self.config, self.node_id)
        try:
            command = self.command()
            executor.handle_command(command)
            active = dict(executor.state["active"])
            self.assertEqual(active["state"], "ready")
            run_dir = Path(active["lease_file"]).parent
            self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(run_dir.parent.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(
                path.stat().st_mode & 0o077 == 0
                for path in run_dir.iterdir()
                if path.is_file()
            ))
            supervisor_pid = active["supervisor_pid"]
            self.assertTrue(psutil.pid_exists(supervisor_pid))
            lease = Path(active["lease_file"])
            self.assertTrue((lease.parent / "client-key.pem").is_file())
            self.assertTrue((lease.parent / "runtime.json").is_file())
            old_mtime = lease.stat().st_mtime_ns
            time.sleep(0.01)
            executor.handle_command(command)
            self.assertEqual(executor.state["active"]["supervisor_pid"], supervisor_pid)
            self.assertGreater(lease.stat().st_mtime_ns, old_mtime)

            first = executor.report_for_heartbeat()
            self.assertEqual((first["sequence"], first["state"]), (1, "ready"))
            executor.handle_command({"type": "stop", "run_id": self.run_id, "reason": "done"})
            terminal = executor.report_for_heartbeat()
            self.assertEqual((terminal["sequence"], terminal["state"]), (2, "stopped"))
            self.assertFalse((lease.parent / "client-key.pem").exists())
            self.assertFalse((lease.parent / "runtime.json").exists())
            self.assertTrue((lease.parent / "locust_runtime.py").is_file())
            self.assertTrue((lease.parent / "engine.log").is_file())
            self.assertEqual(identity.read_text(encoding="utf-8"), "identity-sentinel")
            executor.handle_command({"type": "stop", "run_id": self.run_id, "reason": "ack"})
            self.assertIsNone(executor.state["report"])
            self.assertFalse(psutil.pid_exists(supervisor_pid))

            executor.handle_command(command)
            replay = executor.report_for_heartbeat()
            self.assertEqual((replay["sequence"], replay["state"]), (3, "stopped"))
            self.assertIsNone(executor.state["active"])
            self.assertFalse(psutil.pid_exists(supervisor_pid))
        finally:
            executor.shutdown()

    def test_restart_reaps_verified_supervisor_and_marks_run_failed_without_replay(self):
        first = RunExecutor(self.config, self.node_id)
        first.handle_command(self.command())
        supervisor_pid = first.state["active"]["supervisor_pid"]
        first.close()  # Simulate an Agent restart while its independent supervisor lives.
        self.assertTrue(psutil.pid_exists(supervisor_pid))

        second = RunExecutor(self.config, self.node_id)
        try:
            self.assertIsNone(second.state["active"])
            report = second.report_for_heartbeat()
            self.assertEqual(report["state"], "failed")
            self.assertEqual(report["reason_code"], "agent_restarted")
            self.assertFalse(psutil.pid_exists(supervisor_pid))
            second.handle_command(self.command())
            self.assertIsNone(second.state["active"])
        finally:
            second.shutdown()

    def test_supervisor_expires_lease_without_agent_polling(self):
        executor = RunExecutor(self.config, self.node_id)
        try:
            executor.handle_command(self.command())
            active = dict(executor.state["active"])
            supervisor_pid = active["supervisor_pid"]
            lease = Path(active["lease_file"])
            os.utime(lease, (time.time() - 30, time.time() - 30))
            deadline = time.monotonic() + 4
            while psutil.pid_exists(supervisor_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(psutil.pid_exists(supervisor_pid))
            executor.poll()
            report = executor.report_for_heartbeat()
            self.assertEqual(report["state"], "failed")
            self.assertEqual(report["reason_code"], "lease_expired")
        finally:
            executor.shutdown()

    def test_fixed_script_snapshot_and_handshake_are_strict(self):
        command = self.command()
        for mutation in (
            lambda value: value.update(script_source=value["script_source"] + "\n"),
            lambda value: value.update(snapshot_sha256="0" * 64),
            lambda value: value.update(handshake_token="short"),
            lambda value: value.update(max_seconds=126),
        ):
            candidate = json.loads(json.dumps(command))
            mutation(candidate)
            with self.assertRaises(ProtocolError):
                validate_prepare_command(candidate, self.node_id)

    def test_invalid_tls_material_is_persisted_as_failed_and_never_started(self):
        executor = RunExecutor(self.config, self.node_id)
        try:
            command = self.command()
            command["tls"]["key_pem"] = "not-a-private-key"
            with self.assertLogs("performance_node.executor", level="ERROR") as captured:
                executor.handle_command(command)
            self.assertIsNone(executor.state["active"])
            report = executor.report_for_heartbeat()
            self.assertEqual(report["state"], "failed")
            self.assertEqual(report["reason_code"], "prepare_material_failed")
            self.assertIn("exception_type=SSLError", "\n".join(captured.output))
        finally:
            executor.shutdown()

    def test_missing_stunnel_reports_fixed_safe_prepare_stage(self):
        config = NodeConfig(
            "https://platform.example", self.state_dir, True,
            str(self.root / "missing-stunnel"),
        )
        executor = RunExecutor(config, self.node_id)
        try:
            command = self.command()
            with self.assertLogs("performance_node.executor", level="ERROR") as captured:
                executor.handle_command(command)
            report = executor.report_for_heartbeat()
            self.assertEqual(report["reason_code"], "prepare_supervisor_config_failed")
            combined = "\n".join(captured.output) + json.dumps(report, ensure_ascii=False)
            self.assertIn("exception_type=ProtocolError", combined)
            self.assertNotIn(command["handshake_token"], combined)
            self.assertNotIn("PRIVATE KEY", combined)
        finally:
            executor.shutdown()

    def test_default_stunnel_name_resolves_once_from_trusted_agent_path(self):
        config = NodeConfig("https://platform.example", self.state_dir, True)
        with patch.dict(os.environ, {"PATH": f"{self.root}:{os.environ.get('PATH', '')}"}):
            executor = RunExecutor(config, self.node_id)
            try:
                self.assertEqual(Path(executor._stunnel_binary()).resolve(), self.tunnel.resolve())
            finally:
                executor.shutdown()

    def test_execution_lock_prevents_two_agents_from_starting_the_same_state(self):
        first = RunExecutor(self.config, self.node_id)
        try:
            with self.assertRaises(AgentStopped):
                RunExecutor(self.config, self.node_id)
        finally:
            first.shutdown()

    @unittest.skipUnless(which("stunnel"), "stunnel is required for local mTLS fixture")
    def test_real_stunnel_validates_local_mtls_server_without_load(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(8)
        observed: list[bytes] = []
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(self.root / "cert.pem", self.root / "key.pem")
        server_context.verify_mode = ssl.CERT_REQUIRED
        server_context.load_verify_locations(cafile=str(self.root / "cert.pem"))

        def serve():
            try:
                connection, _address = listener.accept()
                with server_context.wrap_socket(connection, server_side=True) as secure:
                    self.assertIsNotNone(secure.getpeercert())
                    observed.append(secure.recv(4))
                    secure.sendall(b"pong")
            finally:
                listener.close()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        self.engine.write_text(
            "import socket, sys, time\n"
            "port = int(sys.argv[sys.argv.index('--master-port') + 1])\n"
            "with socket.create_connection(('127.0.0.1', port), timeout=5) as client:\n"
            "    client.sendall(b'ping')\n"
            "    assert client.recv(4) == b'pong'\n"
            "time.sleep(.5)\n",
            encoding="utf-8",
        )
        config = NodeConfig(
            "https://platform.example", self.state_dir, True, which("stunnel"),
        )
        command = self.command()
        command["tls"]["port"] = listener.getsockname()[1]
        executor = RunExecutor(config, self.node_id)
        try:
            executor.handle_command(command)
            deadline = time.monotonic() + 5
            while executor.state["active"] is not None and time.monotonic() < deadline:
                executor.poll()
                time.sleep(0.05)
            server.join(timeout=2)
            self.assertEqual(observed, [b"ping"])
            report = executor.report_for_heartbeat()
            self.assertEqual((report["state"], report["reason_code"]), ("stopped", "finished"))
            run_dir = self.state_dir / "runs" / self.run_id
            self.assertFalse((run_dir / "client-key.pem").exists())
            self.assertFalse((run_dir / "runtime.json").exists())
        finally:
            executor.shutdown()


if __name__ == "__main__":
    unittest.main()
