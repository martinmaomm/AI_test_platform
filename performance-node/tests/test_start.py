from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from performance_node.__main__ import main
from performance_node.bootstrap import start_agent
from performance_node.ca import download_ca
from performance_node.errors import AgentStopped, RetryExhausted, StateError
from performance_node.state import StateStore


def _certificate_fixture(root: Path) -> tuple[bytes, bytes, bytes]:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "start-test-ca")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM)
    cert_pem = server_certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (root / "server.pem").write_bytes(cert_pem)
    (root / "server-key.pem").write_bytes(key_pem)
    return ca_pem, cert_pem, key_pem


class InstallationHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.get_count += 1
        if self.path != f"{self.server.base_path}/api/v1/performance-agent/install/ca.pem":
            self.send_error(404)
            return
        if self.server.ca_mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        body = self.server.ca_body
        if self.server.ca_mode == "large":
            body = b"x" * (64 * 1024 + 1)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-pem-file")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == f"{self.server.base_path}/api/v1/performance-agent/enroll/":
            self.server.enroll_count += 1
            self.server.enroll_bodies.append(body)
            if self.server.enroll_mode == "drop":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.server.enroll_mode == "unauthorized":
                self.send_response(401)
                self.end_headers()
                return
            payload = self.server.common(
                node_id=self.server.node_id,
                agent_token=f"{self.server.node_id}.long-lived-secret",
            )
            self._json(200, {"success": True, "data": payload})
            return
        if self.path == f"{self.server.base_path}/api/v1/performance-agent/heartbeat/":
            self.server.heartbeat_count += 1
            self.server.heartbeat_authorizations.append(self.headers.get("Authorization"))
            payload = self.server.common(
                node_id=self.server.node_id,
                server_time="2026-09-18T00:00:00Z",
                command={"type": "idle"},
            )
            self._json(200, {"success": True, "data": payload})
            return
        self.send_error(404)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class LocalInstallationServer:
    def __init__(self, root: Path, node_id: str):
        ca_pem, _cert_pem, key_pem = _certificate_fixture(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), InstallationHandler)
        server.node_id = node_id
        server.base_path = ""
        server.ca_body = ca_pem
        server.ca_mode = "ok"
        server.enroll_mode = "ok"
        server.get_count = 0
        server.enroll_count = 0
        server.heartbeat_count = 0
        server.enroll_bodies = []
        server.heartbeat_authorizations = []

        def common(**values):
            result = {
                "protocol_version": 2,
                "heartbeat_interval_seconds": 5,
                "lease_seconds": 30,
                "execution_enabled": False,
            }
            result.update(values)
            return result

        server.common = common
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "server.pem", root / "server-key.pem")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        self.server = server
        self.ca_pem = ca_pem
        self.key_pem = key_pem
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"https://localhost:{self.server.server_port}{self.server.base_path}"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.server.ca_body).hexdigest()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def heartbeat_once(client, _stop_event) -> int:
    result = client.heartbeat()
    if result.command != {"type": "idle"}:
        raise AssertionError("unexpected heartbeat command")
    return 0


class StartIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "state"
        self.node_id = str(uuid.uuid4())
        self.environment = patch.dict(
            os.environ,
            {"PERFORMANCE_NODE_STATE_DIR": str(self.state_dir)},
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_first_start_registers_once_and_restart_reuses_identity_for_heartbeat(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            with self.assertLogs("performance_node.bootstrap", level="INFO") as first_logs:
                self.assertEqual(start_agent(
                    fixture.url, self.node_id, "one-time-token", fixture.fingerprint,
                    runtime=heartbeat_once, handler_installer=lambda _event: None,
                ), 0)
            with self.assertLogs("performance_node.bootstrap", level="INFO") as restart_logs:
                self.assertEqual(start_agent(
                    fixture.url, self.node_id, "expired-token", fixture.fingerprint,
                    runtime=heartbeat_once, handler_installer=lambda _event: None,
                ), 0)
            self.assertIn("首次注册成功", "\n".join(first_logs.output))
            self.assertIn("复用已有节点身份", "\n".join(restart_logs.output))
            self.assertIn("启动节点心跳", "\n".join(first_logs.output + restart_logs.output))
            self.assertNotIn("one-time-token", "\n".join(first_logs.output))
            self.assertNotIn("expired-token", "\n".join(restart_logs.output))
            self.assertEqual(fixture.server.enroll_count, 1)
            self.assertEqual(fixture.server.heartbeat_count, 2)
            self.assertEqual(fixture.server.get_count, 1)
            self.assertTrue(all(
                value == f"Node {self.node_id}.long-lived-secret"
                for value in fixture.server.heartbeat_authorizations
            ))
            store = StateStore(self.state_dir)
            self.assertEqual(store.load().node_id, self.node_id)
            self.assertEqual(store.ca_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.binding_path.stat().st_mode & 0o777, 0o600)

    def test_platform_path_prefix_is_used_for_ca_enroll_and_heartbeat(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            fixture.server.base_path = "/base"
            start_agent(
                fixture.url, self.node_id, "one-time-token", fixture.fingerprint,
                runtime=heartbeat_once, handler_installer=lambda _event: None,
            )
            self.assertEqual(
                (fixture.server.get_count, fixture.server.enroll_count, fixture.server.heartbeat_count),
                (1, 1, 1),
            )

    def test_wrong_fingerprint_redirect_large_private_key_and_invalid_ca_never_enroll(self):
        cases = (
            ("wrong-fingerprint", "ok", None, "0" * 64),
            ("redirect", "redirect", None, None),
            ("large", "large", None, None),
            ("private-key", "ok", "private", None),
            ("invalid", "ok", b"not-a-ca", None),
        )
        for name, mode, body_kind, explicit_fingerprint in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as case_directory:
                case_root = Path(case_directory)
                state_dir = case_root / "state"
                with patch.dict(os.environ, {"PERFORMANCE_NODE_STATE_DIR": str(state_dir)}):
                    with LocalInstallationServer(case_root, self.node_id) as fixture:
                        fixture.server.ca_mode = mode
                        if body_kind == "private":
                            fixture.server.ca_body = fixture.ca_pem + fixture.key_pem
                        elif isinstance(body_kind, bytes):
                            fixture.server.ca_body = body_kind
                        fingerprint = explicit_fingerprint or fixture.fingerprint
                        with self.assertRaises(AgentStopped):
                            start_agent(
                                fixture.url, self.node_id, "never-send-this", fingerprint,
                                runtime=lambda *_args: 0,
                                handler_installer=lambda _event: None,
                            )
                        self.assertEqual(fixture.server.enroll_count, 0)

    def test_401_marker_prevents_same_token_restart_from_posting_again(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            fixture.server.enroll_mode = "unauthorized"
            for _attempt in range(2):
                with self.assertRaises(AgentStopped):
                    start_agent(
                        fixture.url, self.node_id, "rejected-token", fixture.fingerprint,
                        runtime=lambda *_args: 0,
                        handler_installer=lambda _event: None,
                    )
            self.assertEqual(fixture.server.enroll_count, 1)
            marker = StateStore(self.state_dir).load_attempt()
            self.assertEqual(marker.status, "rejected")
            self.assertNotIn("rejected-token", StateStore(self.state_dir).attempt_path.read_text())

            fixture.server.enroll_mode = "ok"
            start_agent(
                fixture.url, self.node_id, "genuinely-new-token", fixture.fingerprint,
                runtime=lambda *_args: 0,
                handler_installer=lambda _event: None,
            )
            self.assertEqual(fixture.server.enroll_count, 2)
            self.assertTrue(StateStore(self.state_dir).exists())

    def test_dropped_registration_is_ambiguous_and_same_token_is_not_resent(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            fixture.server.enroll_mode = "drop"
            for _attempt in range(2):
                with self.assertRaises(AgentStopped):
                    start_agent(
                        fixture.url, self.node_id, "ambiguous-token", fixture.fingerprint,
                        runtime=lambda *_args: 0,
                        handler_installer=lambda _event: None,
                    )
            self.assertEqual(fixture.server.enroll_count, 1)
            self.assertEqual(StateStore(self.state_dir).load_attempt().status, "ambiguous")

    def test_binding_rejects_other_node_or_platform_before_network(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            start_agent(
                fixture.url, self.node_id, "first-token", fixture.fingerprint,
                runtime=lambda *_args: 0, handler_installer=lambda _event: None,
            )
            counts = (fixture.server.get_count, fixture.server.enroll_count)
            for server, node in (
                (fixture.url, str(uuid.uuid4())),
                (fixture.url + "/other-platform", self.node_id),
            ):
                with self.assertRaises(AgentStopped):
                    start_agent(
                        server, node, "other-token", fixture.fingerprint,
                        runtime=lambda *_args: 0, handler_installer=lambda _event: None,
                    )
            self.assertEqual((fixture.server.get_count, fixture.server.enroll_count), counts)

    def test_start_lock_is_held_until_runtime_returns(self):
        with LocalInstallationServer(self.root, self.node_id) as fixture:
            def nested_start(_client, _event):
                with self.assertRaises(StateError):
                    start_agent(
                        fixture.url, self.node_id, "not-used", fixture.fingerprint,
                        runtime=lambda *_args: 0, handler_installer=lambda _event: None,
                    )
                return 0

            start_agent(
                fixture.url, self.node_id, "first-token", fixture.fingerprint,
                runtime=nested_start, handler_installer=lambda _event: None,
            )
            self.assertEqual(fixture.server.enroll_count, 1)

    def test_corrupt_or_symlinked_installation_state_is_rejected(self):
        store = StateStore(self.state_dir)
        store._ensure_directory()
        store.binding_path.write_text("not-json", encoding="utf-8")
        os.chmod(store.binding_path, 0o600)
        with self.assertRaises(StateError):
            start_agent(
                "https://platform.example", self.node_id, "token", None,
                runtime=lambda *_args: 0, handler_installer=lambda _event: None,
            )
        store.binding_path.unlink()
        store.binding_path.symlink_to(self.root / "elsewhere")
        with self.assertRaises(StateError):
            start_agent(
                "https://platform.example", self.node_id, "token", None,
                runtime=lambda *_args: 0, handler_installer=lambda _event: None,
            )


class SafeCliTests(unittest.TestCase):
    def test_unknown_arguments_never_echo_token_or_unknown_value(self):
        secret = "one-time-super-secret"
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            main([
                "start", "--server", "https://platform.example",
                "--node-id", str(uuid.uuid4()), "--token", secret,
                "--unknown", secret,
            ])
        self.assertNotIn(secret, errors.getvalue())
        self.assertIn("参数无效", errors.getvalue())

    def test_registration_failure_log_never_contains_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            node_id = str(uuid.uuid4())
            secret = "logger-must-never-print-this-token"
            with LocalInstallationServer(root, node_id) as fixture:
                fixture.server.enroll_mode = "unauthorized"
                with patch.dict(os.environ, {"PERFORMANCE_NODE_STATE_DIR": str(state_dir)}):
                    with self.assertLogs(level="ERROR") as captured:
                        result = main([
                            "start", "--server", fixture.url,
                            "--node-id", node_id, "--token", secret,
                            "--ca-sha256", fixture.fingerprint,
                        ])
            self.assertEqual(result, 2)
            self.assertNotIn(secret, "\n".join(captured.output))


class CaDownloadBoundaryTests(unittest.TestCase):
    def test_body_read_has_total_deadline_and_prefixed_fixed_path(self):
        class Socket:
            def settimeout(self, _seconds):
                pass

        class Response:
            status = 200

            def getheader(self, _name):
                return None

            def read1(self, _size):
                raise AssertionError("deadline must be checked before body read")

        class Connection:
            def __init__(self):
                self.sock = Socket()
                self.request_path = None

            def putrequest(self, method, path, **_kwargs):
                self.request_path = (method, path)

            def putheader(self, *_args):
                pass

            def endheaders(self):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        connection = Connection()
        with (
            patch("performance_node.ca.http.client.HTTPSConnection", return_value=connection),
            patch("performance_node.ca.time.monotonic", side_effect=[0, 31]),
            self.assertRaises(RetryExhausted),
        ):
            download_ca("https://platform.example/base")
        self.assertEqual(
            connection.request_path,
            ("GET", "/base/api/v1/performance-agent/install/ca.pem"),
        )


if __name__ == "__main__":
    unittest.main()
