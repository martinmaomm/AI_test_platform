"""Secure, atomic persistence for the long-lived agent identity."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import StateError

_STATE_FILENAME = "identity.json"


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

    def _ensure_directory(self) -> None:
        if self.state_dir.is_symlink():
            raise StateError("状态目录不能是符号链接")
        try:
            existed = self.state_dir.exists()
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            details = self.state_dir.stat()
        except OSError as exc:
            raise StateError("无法创建安全的状态目录") from exc
        if not stat.S_ISDIR(details.st_mode) or details.st_mode & 0o077:
            if existed:
                raise StateError("现有状态目录权限不安全；请配置专用的 0700 目录")
            raise StateError("新建状态目录权限不安全")

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
