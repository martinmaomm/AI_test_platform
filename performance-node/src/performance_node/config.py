"""Environment-only configuration and safe platform URL validation."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigurationError

_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def platform_base_url(value: str) -> str:
    """Validate an HTTPS deployment URL while preserving an optional path prefix."""
    if not value or any(char == "\\" or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 包含不安全字符")
    if "?" in value or "#" in value:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 不允许查询或片段")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 无效") from exc
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 必须是 HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 不允许用户信息")
    # urlsplit accepts a few malformed ports until .port is read.
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 端口无效") from exc
    if port == 0:
        raise ConfigurationError("PERFORMANCE_PLATFORM_URL 端口无效")
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigurationError(f"缺少环境变量 {name}")
    return value


def enrollment_token_from_env() -> str:
    direct = os.environ.get("PERFORMANCE_NODE_ENROLLMENT_TOKEN")
    file_name = os.environ.get("PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE")
    if direct and file_name:
        raise ConfigurationError("注册凭证只能通过一个环境来源提供")
    if direct:
        return direct.strip()
    if file_name:
        try:
            token = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigurationError("无法读取注册凭证文件") from exc
        if token:
            return token
    raise ConfigurationError("缺少注册凭证环境变量或凭证文件")


def node_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigurationError("--node-id 必须是有效 UUID") from exc


def enrollment_token(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ConfigurationError("--token 格式无效")
    return value


def ca_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not _HEX_SHA256.fullmatch(value):
        raise ConfigurationError("--ca-sha256 必须是 64 位十六进制 SHA256")
    return value.lower()


@dataclass(frozen=True)
class NodeConfig:
    platform_url: str
    state_dir: Path
    ca_bundle: str | bool
    stunnel_binary: str = "stunnel"

    @classmethod
    def from_env(cls) -> "NodeConfig":
        ca_file = os.environ.get("PERFORMANCE_NODE_CA_BUNDLE")
        if ca_file and not Path(ca_file).is_file():
            raise ConfigurationError("PERFORMANCE_NODE_CA_BUNDLE 必须是可读取证书文件")
        return cls(
            platform_url=platform_base_url(_required_env("PERFORMANCE_PLATFORM_URL")),
            state_dir=Path(_required_env("PERFORMANCE_NODE_STATE_DIR")),
            ca_bundle=ca_file or True,
            stunnel_binary=os.environ.get("PERFORMANCE_STUNNEL_BINARY", "stunnel"),
        )

    @classmethod
    def from_start(cls, server: str, ca_bundle: str | bool = True) -> "NodeConfig":
        """Build start-mode config without inheriting legacy CA configuration."""
        return cls(
            platform_url=platform_base_url(server),
            state_dir=Path(_required_env("PERFORMANCE_NODE_STATE_DIR")),
            ca_bundle=ca_bundle,
            stunnel_binary=os.environ.get("PERFORMANCE_STUNNEL_BINARY", "stunnel"),
        )

    def endpoint(self, name: str) -> str:
        return f"{self.platform_url}/api/v1/performance-agent/{name}/"
