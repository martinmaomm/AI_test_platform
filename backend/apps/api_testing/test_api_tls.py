"""Loopback TLS regression coverage for API business requests only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from api_testing.requests_runner import requests_runner
from api_testing.requests_runtime import export_python


class _TLSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


class _SilentTLSHTTPServer(ThreadingHTTPServer):
    def handle_error(self, _request, _client_address):
        # The control request deliberately aborts a self-signed TLS handshake.
        return


def _write_self_signed_certificate(directory: Path) -> tuple[Path, Path]:
    certificate_path = directory / "certificate.pem"
    private_key_path = directory / "private-key.pem"
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        openssl = shutil.which("openssl")
        if not openssl:
            raise unittest.SkipTest("缺少 cryptography 和 openssl，无法生成本地测试证书")
        result = subprocess.run(
            [
                openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(private_key_path), "-out", str(certificate_path),
                "-days", "1", "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise unittest.SkipTest("openssl 无法生成本地测试证书")
        return certificate_path, private_key_path

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, private_key_path


class APIBusinessTLSRegressionTests(unittest.TestCase):
    @staticmethod
    def _allow_loopback_without_proxy(key: str) -> None:
        current = os.environ.get(key, "")
        values = [value.strip() for value in current.split(",") if value.strip()]
        for value in ("127.0.0.1", "localhost"):
            if value not in values:
                values.append(value)
        os.environ[key] = ",".join(values)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.certificate_directory = tempfile.TemporaryDirectory(prefix="automation-api-tls-")
        try:
            certificate_path, private_key_path = _write_self_signed_certificate(Path(cls.certificate_directory.name))
        except BaseException:
            cls.certificate_directory.cleanup()
            raise
        cls.server = _SilentTLSHTTPServer(("127.0.0.1", 0), _TLSHandler)
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(certfile=certificate_path, keyfile=private_key_path)
        cls.server.socket = tls_context.wrap_socket(cls.server.socket, server_side=True)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.previous_loopback_port = os.environ.get("OFFLINE_ALLOWED_LOOPBACK_PORT")
        cls.previous_no_proxy = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
        os.environ["OFFLINE_ALLOWED_LOOPBACK_PORT"] = str(cls.server.server_port)
        for key in cls.previous_no_proxy:
            cls._allow_loopback_without_proxy(key)
        cls.base_url = f"https://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)
        if cls.previous_loopback_port is None:
            os.environ.pop("OFFLINE_ALLOWED_LOOPBACK_PORT", None)
        else:
            os.environ["OFFLINE_ALLOWED_LOOPBACK_PORT"] = cls.previous_loopback_port
        for key, previous_value in cls.previous_no_proxy.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value
        cls.certificate_directory.cleanup()
        super().tearDownClass()

    def test_self_signed_loopback_is_only_accepted_by_api_business_runtime(self):
        with self.assertRaises(requests.exceptions.SSLError):
            requests.get(f"{self.base_url}/health", timeout=2)

        legacy_case = {
            "config": {"base_url": self.base_url, "verify": True, "verify_ssl": True},
            "teststeps": [{"request": {"method": "GET", "url": "/health", "verify": True, "verify_ssl": True}}],
        }
        legacy_options = {"verify": True, "verify_ssl": True, "total_timeout": 5}
        result = requests_runner("tls-runtime", legacy_case, options=legacy_options)
        self.assertTrue(result["success"], result)

        source = export_python(legacy_case)
        namespace = {"__name__": "exported_tls_regression"}
        exec(compile(source, "exported_tls_regression.py", "exec"), namespace)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
            exported_result = namespace["run_case"]("tls-export", namespace["CASE"], options=legacy_options)
        self.assertTrue(exported_result["success"], exported_result)
