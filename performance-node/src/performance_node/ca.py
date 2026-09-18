"""Pinned platform-CA bootstrap for the docker-run installation flow."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import re
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography import x509

from .errors import AgentStopped, RetryExhausted
from .state import StateStore

CA_INSTALL_PATH = "/api/v1/performance-agent/install/ca.pem"
MAX_CA_BYTES = 64 * 1024
CA_DOWNLOAD_DEADLINE_SECONDS = 30
_CERTIFICATE_BLOCK = re.compile(
    br"-----BEGIN CERTIFICATE-----\r?\n(?:[A-Za-z0-9+/=]+\r?\n)+-----END CERTIFICATE-----"
)


def download_ca(platform_url: str) -> bytes:
    """Download only the public CA from the platform origin without ambient state."""
    parsed = urlsplit(platform_url)
    install_path = f"{parsed.path.rstrip('/')}{CA_INSTALL_PATH}"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        parsed.port or 443,
        timeout=15,
        context=context,
    )
    deadline = time.monotonic() + CA_DOWNLOAD_DEADLINE_SECONDS
    try:
        connection.putrequest("GET", install_path, skip_accept_encoding=True)
        connection.putheader("Accept", "application/x-pem-file")
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise AgentStopped("平台 CA 下载重定向被拒绝")
        if response.status != 200:
            raise AgentStopped(f"平台 CA 下载失败（HTTP {response.status}）")
        content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise AgentStopped("平台 CA 响应长度无效") from exc
            if declared_length < 0 or declared_length > MAX_CA_BYTES:
                raise AgentStopped("平台 CA 超过 64KiB 限制")
        payload = bytearray()
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise RetryExhausted("平台 CA 下载超过 30 秒总时限；未发送注册凭证")
            if connection.sock is not None:
                connection.sock.settimeout(min(15, remaining_seconds))
            chunk = response.read1(min(8192, MAX_CA_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_CA_BYTES:
                raise AgentStopped("平台 CA 超过 64KiB 限制")
        return bytes(payload)
    except AgentStopped:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise RetryExhausted("平台 CA 下载网络失败；未发送注册凭证") from exc
    finally:
        connection.close()


def validate_ca_pem(payload: bytes) -> None:
    if not payload or len(payload) > MAX_CA_BYTES:
        raise AgentStopped("平台 CA 内容为空或超过 64KiB 限制")
    if b"PRIVATE KEY" in payload:
        raise AgentStopped("平台 CA 响应不得包含私钥")

    blocks: list[bytes] = []
    cursor = 0
    for match in _CERTIFICATE_BLOCK.finditer(payload):
        if payload[cursor:match.start()].strip():
            raise AgentStopped("平台 CA PEM 包含非证书内容")
        blocks.append(match.group(0) + b"\n")
        cursor = match.end()
    if not blocks or payload[cursor:].strip():
        raise AgentStopped("平台 CA PEM 无效")

    now = datetime.now(timezone.utc)
    for block in blocks:
        try:
            certificate = x509.load_pem_x509_certificate(block)
            constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        except (ValueError, x509.ExtensionNotFound) as exc:
            raise AgentStopped("平台 CA PEM 不是有效 CA 证书") from exc
        if not constraints.ca:
            raise AgentStopped("平台 CA PEM 包含非 CA 证书")
        try:
            key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            key_usage = None
        if key_usage is not None and not key_usage.key_cert_sign:
            raise AgentStopped("平台 CA 证书不允许签发证书")
        if now < certificate.not_valid_before_utc or now > certificate.not_valid_after_utc:
            raise AgentStopped("平台 CA 证书尚未生效或已过期")


def resolve_ca(
    store: StateStore,
    platform_url: str,
    expected_sha256: str | None,
) -> str | bool:
    """Return requests.verify value after pinning and validating a custom CA."""
    if expected_sha256 is None:
        return True

    if store.ca_exists():
        payload = store.load_ca()
    else:
        payload = download_ca(platform_url)

    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual, expected_sha256):
        raise AgentStopped("平台 CA SHA256 指纹不匹配；未发送注册凭证")
    validate_ca_pem(payload)
    if not store.ca_exists():
        store.save_ca(payload)
    return str(store.ca_path)
