"""Secure, atomic persistence for the long-lived agent identity."""

from __future__ import annotations

import json
import fcntl
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import StateError

_STATE_FILENAME = "identity.json"
_BINDING_FILENAME = "installation.json"
_ATTEMPT_FILENAME = "enrollment-attempt.json"
_CA_FILENAME = "platform-ca.pem"
_START_LOCK_FILENAME = "start.lock"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT_STATUSES = {"in_progress", "rejected", "ambiguous"}


@dataclass(frozen=True)
class NodeIdentity:
    node_id: str
    agent_token: str

    def __post_init__(self) -> None:
        try:
            node_uuid = str(uuid.UUID(self.node_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise StateError("状态文件中的节点身份无效") from exc
        token_uuid = _token_node_id(self.agent_token)
        if token_uuid is None or token_uuid != node_uuid:
            raise StateError("状态文件中的节点身份无效")
        object.__setattr__(self, "node_id", node_uuid)


@dataclass(frozen=True)
class InstallationBinding:
    platform_url: str
    node_id: str
    ca_sha256: str | None


@dataclass(frozen=True)
class EnrollmentAttempt:
    token_sha256: str
    status: str


def _token_node_id(token: object) -> str | None:
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        return None
    prefix, separator, secret = token.partition(".")
    if not separator or not secret:
        return None
    try:
        return str(uuid.UUID(prefix))
    except ValueError:
        return None


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.path = state_dir / _STATE_FILENAME
        self.binding_path = state_dir / _BINDING_FILENAME
        self.attempt_path = state_dir / _ATTEMPT_FILENAME
        self.ca_path = state_dir / _CA_FILENAME
        self.start_lock_path = state_dir / _START_LOCK_FILENAME

    def _ensure_directory(self) -> None:
        if self.state_dir.is_symlink():
            raise StateError("状态目录不能是符号链接")
        try:
            existed = self.state_dir.exists()
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            details = self.state_dir.stat()
        except OSError as exc:
            raise StateError("无法创建安全的状态目录") from exc
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_mode & 0o077
            or details.st_uid != os.geteuid()
        ):
            if existed:
                raise StateError("现有状态目录权限不安全；请配置专用的 0700 目录")
            raise StateError("新建状态目录权限不安全")

    def _secure_file_details(self, path: Path, description: str) -> os.stat_result:
        try:
            details = path.lstat()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise StateError(f"无法读取{description}") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_mode & 0o077
            or details.st_uid != os.geteuid()
        ):
            raise StateError(f"{description}类型、所有者或权限不安全")
        return details

    def _read_json(self, path: Path, description: str) -> object:
        self._secure_file_details(path, description)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StateError(f"{description}已损坏；已停止以保护节点状态") from exc

    def _atomic_write(self, path: Path, payload: bytes, *, replace: bool) -> None:
        descriptor: int | None = None
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=self.state_dir)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if replace:
                try:
                    self._secure_file_details(path, "现有状态文件")
                except FileNotFoundError:
                    pass
                os.replace(temporary_name, path)
            else:
                try:
                    os.link(temporary_name, path)
                except FileExistsError as exc:
                    raise StateError("已有状态；不会覆盖现有文件") from exc
            directory_descriptor = os.open(self.state_dir, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except StateError:
            raise
        except OSError as exc:
            raise StateError("无法安全保存节点状态") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    @contextmanager
    def start_lock(self) -> Iterator[None]:
        """Hold the installation/runtime lock for the complete start lifecycle."""
        self._ensure_directory()
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            existed = self.start_lock_path.exists()
            descriptor = os.open(self.start_lock_path, flags, 0o600)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or (existed and details.st_mode & 0o077)
            ):
                raise StateError("启动锁文件类型、所有者或权限不安全")
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "a+b", buffering=0)
            descriptor = -1
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise StateError("同一状态卷已有性能节点正在运行") from exc
        except StateError:
            raise
        except OSError as exc:
            raise StateError("无法安全获取节点启动锁") from exc
        finally:
            if "descriptor" in locals() and descriptor >= 0:
                os.close(descriptor)
        try:
            yield
        finally:
            handle.close()

    def exists(self) -> bool:
        self._ensure_directory()
        try:
            details = self.path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(details.st_mode):
            raise StateError("状态文件不能是符号链接")
        return True

    def load(self) -> NodeIdentity:
        self._ensure_directory()
        try:
            details = self.path.lstat()
        except FileNotFoundError as exc:
            raise StateError("未找到节点身份；请先执行 enroll") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise StateError("状态文件类型不安全")
        if details.st_mode & 0o077:
            raise StateError("状态文件权限不安全")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                set(data) != {"version", "node_id", "agent_token"}
                or isinstance(data["version"], bool)
                or not isinstance(data["version"], int)
                or data["version"] != 1
            ):
                raise ValueError
            return NodeIdentity(node_id=data["node_id"], agent_token=data["agent_token"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError, StateError) as exc:
            raise StateError("状态文件已损坏；已停止以保护节点身份") from exc

    def binding_exists(self) -> bool:
        self._ensure_directory()
        try:
            self._secure_file_details(self.binding_path, "安装绑定文件")
            return True
        except FileNotFoundError:
            return False

    def load_binding(self) -> InstallationBinding:
        data = self._read_json(self.binding_path, "安装绑定文件")
        try:
            if not isinstance(data, dict) or set(data) != {"version", "platform_url", "node_id", "ca_sha256"}:
                raise ValueError
            if data["version"] != 1 or isinstance(data["version"], bool):
                raise ValueError
            node = str(uuid.UUID(data["node_id"]))
            fingerprint = data["ca_sha256"]
            if fingerprint is not None and (
                not isinstance(fingerprint, str) or not _HEX_SHA256.fullmatch(fingerprint)
            ):
                raise ValueError
            if not isinstance(data["platform_url"], str) or not data["platform_url"]:
                raise ValueError
            return InstallationBinding(data["platform_url"], node, fingerprint)
        except (ValueError, TypeError, AttributeError) as exc:
            raise StateError("安装绑定文件已损坏；已停止以防止节点接管") from exc

    def save_binding(self, binding: InstallationBinding) -> None:
        self._ensure_directory()
        payload = json.dumps(
            {
                "version": 1,
                "platform_url": binding.platform_url,
                "node_id": binding.node_id,
                "ca_sha256": binding.ca_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._atomic_write(self.binding_path, payload, replace=False)

    def load_attempt(self) -> EnrollmentAttempt | None:
        self._ensure_directory()
        try:
            data = self._read_json(self.attempt_path, "注册尝试标记")
        except FileNotFoundError:
            return None
        try:
            if not isinstance(data, dict) or set(data) != {"version", "token_sha256", "status"}:
                raise ValueError
            if data["version"] != 1 or isinstance(data["version"], bool):
                raise ValueError
            if not isinstance(data["token_sha256"], str) or not _HEX_SHA256.fullmatch(data["token_sha256"]):
                raise ValueError
            if data["status"] not in _ATTEMPT_STATUSES:
                raise ValueError
            return EnrollmentAttempt(data["token_sha256"], data["status"])
        except (ValueError, TypeError) as exc:
            raise StateError("注册尝试标记已损坏；已停止以防止重复注册") from exc

    def save_attempt(self, attempt: EnrollmentAttempt) -> None:
        self._ensure_directory()
        payload = json.dumps(
            {"version": 1, "token_sha256": attempt.token_sha256, "status": attempt.status},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._atomic_write(self.attempt_path, payload, replace=self.attempt_path.exists())

    def clear_attempt(self) -> None:
        try:
            self._secure_file_details(self.attempt_path, "注册尝试标记")
            self.attempt_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StateError("无法清除注册尝试标记") from exc

    def ca_exists(self) -> bool:
        self._ensure_directory()
        try:
            self._secure_file_details(self.ca_path, "平台 CA 文件")
            return True
        except FileNotFoundError:
            return False

    def load_ca(self) -> bytes:
        self._secure_file_details(self.ca_path, "平台 CA 文件")
        try:
            return self.ca_path.read_bytes()
        except OSError as exc:
            raise StateError("无法读取平台 CA 文件") from exc

    def save_ca(self, pem: bytes) -> None:
        self._ensure_directory()
        self._atomic_write(self.ca_path, pem, replace=False)

    def save(self, identity: NodeIdentity) -> None:
        self._ensure_directory()
        if self.exists():
            raise StateError("已有节点身份；不会自动重新注册")
        payload = json.dumps(
            {"version": 1, "node_id": identity.node_id, "agent_token": identity.agent_token},
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = None
        temporary_name = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".identity-", dir=self.state_dir)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # link(2) is atomic no-clobber publication: a concurrent agent cannot
            # replace an identity that has already been published.
            try:
                os.link(temporary_name, self.path)
            except FileExistsError as exc:
                raise StateError("已有节点身份；不会覆盖现有状态") from exc
            directory_descriptor = os.open(self.state_dir, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise StateError("无法安全保存节点身份") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
