from __future__ import annotations

import json
import os
import signal
import ssl
import subprocess
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from performance_node import PROTOCOL_VERSION
from performance_node.client import PerformanceNodeClient
from performance_node.config import NodeConfig, platform_base_url
from performance_node.errors import AgentStopped, ProtocolError, RetryExhausted, StateError
from performance_node.runner import install_stop_handlers, run_forever
from performance_node.state import NodeIdentity, StateStore
from performance_node.transport import AgentTransport

NODE_ID = str(uuid.uuid4())
TOKEN = f"{NODE_ID}.a-long-secret"


def _openssl_available() -> bool:
    from shutil import which
    return which("openssl") is not None


def wrapped(data: dict) -> dict:
    return {"success": True, "data": data}


def common(**values: object) -> dict:
    result = {"protocol_version": PROTOCOL_VERSION, "heartbeat_interval_seconds": 5,
              "lease_seconds": 30, "execution_enabled": False}
    result.update(values)
    return result


class Response:
    def __init__(self, status: int, payload: object, redirect: bool = False):
        self.status_code, self.payload, self.is_redirect = status, payload, redirect

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, responses: list[object]):
        self.responses, self.calls = responses, []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ConfigAndTransportTests(unittest.TestCase):
    def test_platform_url_rejects_non_https_credentials_query_and_fragment(self):
        for url in ("http://platform.example", "https://:443", "https://platform.example:0", "https://u:p@platform.example", "https://platform.example/?a=1", "https://platform.example/?", "https://platform.example/#x", "https://platform.example/#", "https://platform.example/a\\b", "https://platform.example/a\n"):
            with self.assertRaises(Exception):
                platform_base_url(url)
        self.assertEqual(platform_base_url("https://platform.example/platform/"), "https://platform.example/platform")

    def test_redirect_is_rejected_and_never_followed(self):
        session = Session([Response(302, {}, redirect=True)])
        with self.assertRaises(AgentStopped):
            AgentTransport(session=session).post("https://example.test/enroll/", {})
        self.assertIs(session.calls[0][1]["allow_redirects"], False)

    def test_transport_direct_use_cannot_bypass_https_url_rules(self):
        transport = AgentTransport(session=Session([]))
        for url in ("http://example.test/", "https://:443", "https://example.test:0", "https://u:p@example.test/", "https://example.test/?", "https://example.test/a\\b"):
            with self.assertRaises(ProtocolError):
                transport.post(url, {})

    def test_retry_for_429_and_5xx_is_bounded(self):
        session = Session([Response(429, {}), Response(503, {}), Response(200, wrapped({}))])
        pauses: list[float] = []
        self.assertEqual(AgentTransport(session=session, sleep=pauses.append).post("https://example.test/", {}), {})
        self.assertEqual(pauses, [1, 2])
        session = Session([Response(503, {}), Response(503, {}), Response(503, {})])
        with self.assertRaises(RetryExhausted):
            AgentTransport(session=session, sleep=lambda _seconds: None).post("https://example.test/", {})

    def test_protocol_wrapper_is_strict(self):
        session = Session([Response(200, {"success": True, "data": {}, "extra": 1})])
        with self.assertRaises(ProtocolError):
            AgentTransport(session=session).post("https://example.test/", {})

    def test_tls_verify_cannot_be_disabled(self):
        with self.assertRaises(ValueError):
            AgentTransport(verify=False)

    def test_session_does_not_trust_process_proxy_or_netrc(self):
        self.assertFalse(AgentTransport().session.trust_env)


class StateTests(unittest.TestCase):
    def test_atomic_state_has_restrictive_permissions_and_reuses_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state")
            identity = NodeIdentity(NODE_ID, TOKEN)
            store.save(identity)
            self.assertEqual(stat_mode(store.state_dir), 0o700)
            self.assertEqual(stat_mode(store.path), 0o600)
            self.assertEqual(store.state_dir.stat().st_uid, os.getuid())
            self.assertEqual(store.load(), identity)

    def test_existing_identity_blocks_repeat_enroll_and_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state")
            store.save(NodeIdentity(NODE_ID, TOKEN))
            with self.assertRaises(StateError):
                store.save(NodeIdentity(str(uuid.uuid4()), f"{uuid.uuid4()}.other"))
            store.path.unlink()
            store.path.symlink_to(Path(directory) / "elsewhere")
            with self.assertRaises(StateError):
                store.exists()

    def test_damaged_or_insecure_state_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state")
            store._ensure_directory()
            for damaged in ("not-json", "[]", '{"version":true,"node_id":"x","agent_token":"x"}', '{"version":1,"node_id":[],"agent_token":"x"}'):
                store.path.write_text(damaged, encoding="utf-8")
                os.chmod(store.path, 0o600)
                with self.assertRaises(StateError):
                    store.load()

    def test_existing_wide_state_directory_is_rejected_without_permission_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "shared"
            state_dir.mkdir(mode=0o755)
            os.chmod(state_dir, 0o755)
            with self.assertRaises(StateError):
                StateStore(state_dir).exists()
            self.assertEqual(stat_mode(state_dir), 0o755)

    def test_token_prefix_must_match_node_id(self):
        with self.assertRaises(StateError):
            NodeIdentity(NODE_ID, f"{uuid.uuid4()}.a-long-secret")

    def test_node_id_is_normalized_to_a_stable_uuid(self):
        node_id = str(uuid.UUID(NODE_ID)).upper()
        identity = NodeIdentity(node_id, f"{node_id}.a-long-secret")
        self.assertEqual(identity.node_id, NODE_ID)

    def test_concurrent_saves_never_overwrite_the_first_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state")
            first = NodeIdentity(NODE_ID, TOKEN)
            second_node = str(uuid.uuid4())
            second = NodeIdentity(second_node, f"{second_node}.second-secret")
            barrier = threading.Barrier(2)
            outcomes = []

            def save(identity):
                barrier.wait()
                try:
                    store.save(identity)
                    outcomes.append("saved")
                except StateError:
                    outcomes.append("blocked")

            threads = [threading.Thread(target=save, args=(identity,)) for identity in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(outcomes), ["blocked", "saved"])
            self.assertIn(store.load(), (first, second))


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class ClientTests(unittest.TestCase):
    def config(self, directory: str) -> NodeConfig:
        return NodeConfig("https://platform.example", Path(directory) / "state", True)

    def test_enroll_persists_and_second_enroll_does_not_send_token(self):
        with tempfile.TemporaryDirectory() as directory:
            response = common(node_id=NODE_ID, agent_token=TOKEN)
            session = Session([Response(200, wrapped(response))])
            client = PerformanceNodeClient(self.config(directory), AgentTransport(session=session))
            client.enroll("enrollment-secret")
            with self.assertRaises(AgentStopped):
                client.enroll("different-secret")
            self.assertEqual(len(session.calls), 1)

    def test_unknown_command_and_identity_mismatch_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            client = PerformanceNodeClient(self.config(directory))
            client.store.save(NodeIdentity(NODE_ID, TOKEN))
            for payload in (
                common(node_id=NODE_ID, server_time="2026-09-16T00:00:00Z", command={"type": "execute"}),
                common(node_id=str(uuid.uuid4()), server_time="2026-09-16T00:00:00Z", command={"type": "idle"}),
            ):
                client.transport = AgentTransport(session=Session([Response(200, wrapped(payload))]))
                with self.assertRaises(AgentStopped):
                    client.heartbeat()

    def test_boolean_protocol_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            client = PerformanceNodeClient(self.config(directory))
            client.store.save(NodeIdentity(NODE_ID, TOKEN))
            payload = common(node_id=NODE_ID, server_time="2026-09-16T00:00:00Z", command={"type": "idle"})
            payload["protocol_version"] = True
            client.transport = AgentTransport(session=Session([Response(200, wrapped(payload))]))
            with self.assertRaises(AgentStopped):
                client.heartbeat()

    def test_heartbeat_uses_node_auth_and_never_logs_token(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session([Response(200, wrapped(common(node_id=NODE_ID, server_time="2026-09-16T00:00:00Z", command={"type": "idle"})))])
            client = PerformanceNodeClient(self.config(directory), AgentTransport(session=session))
            client.store.save(NodeIdentity(NODE_ID, TOKEN))
            client.heartbeat()
            self.assertEqual(session.calls[0][1]["headers"]["Authorization"], f"Node {TOKEN}")

    def test_v2_heartbeat_sends_run_report_and_returns_execution_command(self):
        with tempfile.TemporaryDirectory() as directory:
            run_id = str(uuid.uuid4())
            report = {
                "run_id": run_id, "sequence": 3, "state": "ready",
                "reason_code": "", "reason": "",
            }
            command = {"type": "stop", "run_id": run_id, "reason": "fixture"}
            response = common(
                node_id=NODE_ID, server_time="2026-09-16T00:00:00Z",
                command=command, execution_enabled=True,
            )
            session = Session([Response(200, wrapped(response))])
            client = PerformanceNodeClient(self.config(directory), AgentTransport(session=session))
            client.store.save(NodeIdentity(NODE_ID, TOKEN))
            result = client.heartbeat(run_report=report)
            self.assertEqual(result.command, command)
            self.assertEqual(session.calls[0][1]["json"]["run_report"], report)

    def test_heartbeat_does_not_reuse_a_strict_report_sequence_on_transport_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session([Response(503, {}), Response(200, wrapped({}))])
            client = PerformanceNodeClient(self.config(directory), AgentTransport(session=session, sleep=lambda _seconds: None))
            client.store.save(NodeIdentity(NODE_ID, TOKEN))
            with self.assertRaises(RetryExhausted):
                client.heartbeat(run_report=None)
            self.assertEqual(len(session.calls), 1)


class RunnerTests(unittest.TestCase):
    def test_first_successful_heartbeat_is_logged_once(self):
        class Store:
            def load(self):
                return NodeIdentity(NODE_ID, TOKEN)

        class Result:
            interval_seconds = 1
            command = {"type": "idle"}

        class Client:
            store = Store()

            def heartbeat(self, _identity, _run_report):
                return Result()

        class Executor:
            def report_for_heartbeat(self):
                return None

            def handle_command(self, _command):
                pass

            def shutdown(self):
                pass

            def close(self):
                pass

        with self.assertLogs("performance_node.runner", level="INFO") as captured:
            self.assertEqual(run_forever(
                Client(), threading.Event(), lambda _seconds: True, executor=Executor(),
            ), 0)
        combined = "\n".join(captured.output)
        self.assertEqual(combined.count("节点首次心跳成功"), 1)
        self.assertNotIn(TOKEN, combined)

    def test_unknown_command_stops_loop_and_sigterm_handler_sets_event(self):
        event = threading.Event()
        registered = {}
        with patch("performance_node.runner.signal.signal", side_effect=lambda name, handler: registered.setdefault(name, handler)):
            install_stop_handlers(event)
        registered[signal.SIGTERM](signal.SIGTERM, None)
        self.assertTrue(event.is_set())

        class Store:
            def load(self):
                return NodeIdentity(NODE_ID, TOKEN)

        class Client:
            store = Store()
            def heartbeat(self, _identity, _run_report):
                raise AgentStopped("收到未知或不可执行命令，已停止")

        class Executor:
            def report_for_heartbeat(self):
                return None

            def stop_active(self, *_args, **_kwargs):
                pass

            def close(self):
                pass

        with self.assertLogs("performance_node.runner", level="ERROR") as captured:
            self.assertEqual(run_forever(
                Client(), threading.Event(), lambda _seconds: False, executor=Executor(),
            ), 2)
        self.assertNotIn(TOKEN, "\n".join(captured.output))


class LocalTlsHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps(wrapped({})).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class TlsTests(unittest.TestCase):
    @unittest.skipUnless(_openssl_available(), "openssl is required for local TLS fixture")
    def test_private_ca_succeeds_and_default_verify_rejects_unknown_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "openssl.cnf"
            config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost\n", encoding="utf-8")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem"), "-config", str(config)], check=True, capture_output=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalTlsHandler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / "cert.pem", root / "key.pem")
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"https://localhost:{server.server_port}/"
            try:
                self.assertEqual(AgentTransport(verify=str(root / "cert.pem")).post(url, {}), {})
                with self.assertRaises(RetryExhausted):
                    AgentTransport(sleep=lambda _seconds: None).post(url, {})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
