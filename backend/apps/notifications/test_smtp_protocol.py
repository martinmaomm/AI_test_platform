"""Loopback SMTP protocol tests using a temporary, test-only self-signed CA."""

from __future__ import annotations

import base64
import ipaddress
import os
import socketserver
import ssl
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.parser import BytesParser
from email import policy
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.test import TestCase

from .delivery import NotificationDeliveryError, send_email, test_smtp_connection
from .models import EmailConfig


def _write_test_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "smtp-cert.pem"
    key_path = directory / "smtp-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


class _SMTPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.sock = self.request
        self.sock.settimeout(10)
        if self.server.implicit_tls:
            self.sock = self.server.tls_context.wrap_socket(self.sock, server_side=True)
            self.server.tls_handshakes.append("implicit")
        self.reader = self.sock.makefile("rb")
        self._reply("220 aits-loopback-smtp")
        pending_plain_auth = False
        try:
            while line := self.reader.readline():
                command = line.decode("utf-8", "replace").strip()
                upper = command.upper()
                if pending_plain_auth:
                    pending_plain_auth = False
                    self._authenticate_plain(command)
                elif upper.startswith("EHLO") or upper.startswith("HELO"):
                    capabilities = ["localhost"]
                    if not self.server.implicit_tls and not self.server.starttls_complete:
                        capabilities.append("STARTTLS")
                    capabilities.extend(["AUTH PLAIN", "SIZE 1048576"])
                    self._multiline(250, capabilities)
                elif upper == "STARTTLS":
                    self._reply("220 begin TLS")
                    self.reader.close()
                    self.sock = self.server.tls_context.wrap_socket(self.sock, server_side=True)
                    self.reader = self.sock.makefile("rb")
                    self.server.starttls_complete = True
                    self.server.tls_handshakes.append("starttls")
                elif upper.startswith("AUTH PLAIN"):
                    encoded = command[10:].strip()
                    if encoded:
                        self._authenticate_plain(encoded)
                    else:
                        pending_plain_auth = True
                        self._reply("334 ")
                elif upper.startswith("MAIL FROM:"):
                    self._reply("250 sender accepted")
                elif upper.startswith("RCPT TO:"):
                    start = command.find("<")
                    end = command.find(">", start + 1)
                    recipient = command[start + 1:end] if start >= 0 and end > start else command[8:].strip()
                    self.server.recipients.append(recipient)
                    if recipient.casefold() in self.server.rejected_recipients:
                        self._reply("550 recipient rejected")
                    else:
                        self._reply("250 recipient accepted")
                elif upper == "DATA":
                    self._reply("354 end data with <CR><LF>.<CR><LF>")
                    lines = []
                    while True:
                        data_line = self.reader.readline()
                        if not data_line:
                            return  # A disconnected client must not spin forever.
                        if data_line == b".\r\n":
                            break
                        lines.append(data_line)
                    self.server.messages.append(b"".join(lines))
                    self._reply("250 message accepted")
                elif upper == "QUIT":
                    self._reply("221 bye")
                    return
                else:
                    self._reply("250 ok")
        finally:
            self.reader.close()
            self.sock.close()

    def _authenticate_plain(self, encoded: str):
        try:
            _, username, password = base64.b64decode(encoded).decode("utf-8").split("\x00", 2)
        except Exception:
            self._reply("535 authentication failed")
            return
        self.server.auth_attempts.append((username, password))
        if self.server.accept_auth and (username, password) == self.server.expected_credentials:
            self._reply("235 authentication successful")
        else:
            self._reply("535 authentication failed")

    def _reply(self, text: str):
        self.sock.sendall((text + "\r\n").encode("ascii"))

    def _multiline(self, code: int, capabilities: list[str]):
        for index, capability in enumerate(capabilities):
            separator = " " if index == len(capabilities) - 1 else "-"
            self._reply(f"{code}{separator}{capability}")


class _LoopbackSMTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextmanager
def _smtp_server(*, implicit_tls: bool, accept_auth: bool = True, rejected_recipients=()):
    with tempfile.TemporaryDirectory(prefix="aits-loopback-smtp-") as directory:
        certificate, key = _write_test_certificate(Path(directory))
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(certificate, key)
        server = _LoopbackSMTPServer(("127.0.0.1", 0), _SMTPHandler)
        server.tls_context = server_context
        server.implicit_tls = implicit_tls
        server.starttls_complete = False
        server.accept_auth = accept_auth
        server.expected_credentials = ("sender@example.test", "test-only-password")
        server.rejected_recipients = {recipient.casefold() for recipient in rejected_recipients}
        server.auth_attempts = []
        server.tls_handshakes = []
        server.recipients = []
        server.messages = []
        previous_port = os.environ.get("AITS_OFFLINE_ALLOWED_LOOPBACK_PORT")
        os.environ["AITS_OFFLINE_ALLOWED_LOOPBACK_PORT"] = str(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client_context = ssl.create_default_context(cafile=str(certificate))
        try:
            yield server, client_context
        finally:
            if previous_port is None:
                os.environ.pop("AITS_OFFLINE_ALLOWED_LOOPBACK_PORT", None)
            else:
                os.environ["AITS_OFFLINE_ALLOWED_LOOPBACK_PORT"] = previous_port
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class LocalSMTPProtocolTests(TestCase):
    def _config(self, server, *, use_ssl: bool):
        return EmailConfig.objects.create(
            name="loopback SMTP",
            smtp_server="127.0.0.1",
            port=server.server_address[1],
            sender_email="sender@example.test",
            smtp_password="test-only-password",
            use_ssl=use_ssl,
        )

    @contextmanager
    def _trust_test_certificate_only(self, client_context):
        with patch("django.core.mail.backends.smtp.ssl.create_default_context", return_value=client_context):
            yield

    def test_implicit_tls_authentication_and_utf8_mime_delivery(self):
        with _smtp_server(implicit_tls=True) as (server, client_context):
            self._config(server, use_ssl=True)
            with self._trust_test_certificate_only(client_context):
                send_email(
                    "邮件主题",
                    "UTF-8 正文：测试通过",
                    ["one@example.test", "two@example.test"],
                    html_body="<p>HTML 正文：测试通过</p>",
                )

            self.assertEqual(server.tls_handshakes, ["implicit"])
            self.assertEqual(server.auth_attempts, [("sender@example.test", "test-only-password")])
            self.assertEqual(server.recipients, ["one@example.test", "two@example.test"])
            parsed = BytesParser(policy=policy.default).parsebytes(server.messages[0])
            self.assertEqual(str(make_header(decode_header(parsed["Subject"]))), "邮件主题")
            content = [part.get_content() for part in parsed.walk() if part.get_content_maintype() == "text"]
            self.assertIn("UTF-8 正文：测试通过", content)
            self.assertIn("<p>HTML 正文：测试通过</p>", content)

    def test_starttls_connection_authenticates_without_sending_a_message(self):
        with _smtp_server(implicit_tls=False) as (server, client_context):
            config = self._config(server, use_ssl=False)
            with self._trust_test_certificate_only(client_context):
                test_smtp_connection(config)

            self.assertEqual(server.tls_handshakes, ["starttls"])
            self.assertEqual(server.auth_attempts, [("sender@example.test", "test-only-password")])
            self.assertEqual(server.messages, [])

    def test_authentication_rejection_is_safe(self):
        with _smtp_server(implicit_tls=True, accept_auth=False) as (server, client_context):
            config = self._config(server, use_ssl=True)
            with self._trust_test_certificate_only(client_context):
                with self.assertRaisesRegex(NotificationDeliveryError, "认证失败") as caught:
                    test_smtp_connection(config)

            self.assertNotIn("test-only-password", caught.exception.safe_message)
            self.assertEqual(server.tls_handshakes, ["implicit"])
            self.assertEqual(len(server.auth_attempts), 1)

    def test_partial_rcpt_refusal_reports_ambiguity_without_retrying(self):
        with _smtp_server(implicit_tls=True, rejected_recipients={"two@example.test"}) as (server, client_context):
            self._config(server, use_ssl=True)
            with self._trust_test_certificate_only(client_context):
                with self.assertRaisesRegex(NotificationDeliveryError, "部分收件人") as caught:
                    send_email("subject", "body", ["one@example.test", "two@example.test"])

            self.assertIn("不要直接重复发送", caught.exception.safe_message)
            self.assertEqual(server.recipients, ["one@example.test", "two@example.test"])
            self.assertEqual(len(server.messages), 1)
