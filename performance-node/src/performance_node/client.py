"""Phase-one node protocol. It intentionally has no Worker or load execution code."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import psutil

from . import ENGINE_VERSION, PROTOCOL_VERSION, __version__
from .config import NodeConfig
from .errors import AgentStopped, ProtocolError
from .state import NodeIdentity, StateStore
from .transport import AgentTransport


@dataclass(frozen=True)
class HeartbeatResult:
    interval_seconds: int
    lease_seconds: int
    command: dict[str, Any]


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"服务端 {name} 不符合协议")
    return value


def _node_id(value: object, name: str = "node_id") -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"服务端 {name} 不符合协议")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ProtocolError(f"服务端 {name} 不符合协议") from exc


class PerformanceNodeClient:
    def __init__(self, config: NodeConfig, transport: AgentTransport | None = None):
        self.config = config
        self.store = StateStore(config.state_dir)
        self.transport = transport or AgentTransport(config.ca_bundle)

    def enroll(
        self,
        enrollment_token: str,
        *,
        expected_node_id: str | None = None,
        retry_transient: bool = True,
    ) -> NodeIdentity:
        if self.store.exists():
            raise AgentStopped("已有节点身份；不会自动重新注册")
        data = self.transport.post(
            self.config.endpoint("enroll"),
            {"enrollment_token": enrollment_token, "protocol_version": PROTOCOL_VERSION,
             "agent_version": __version__, "engine_version": ENGINE_VERSION},
            retry_transient=retry_transient,
        )
        self._validate_common(data)
        node_id = _node_id(data.get("node_id"))
        if expected_node_id is not None and node_id != expected_node_id:
            raise ProtocolError("注册响应的节点身份与安装命令不一致")
        token = data.get("agent_token")
        try:
            identity = NodeIdentity(node_id=node_id, agent_token=token)
        except Exception as exc:
            raise ProtocolError("服务端 agent_token 不符合协议") from exc
        self.store.save(identity)
        return identity

    def heartbeat(
        self,
        identity: NodeIdentity | None = None,
        run_report: dict[str, Any] | None = None,
    ) -> HeartbeatResult:
        identity = identity or self.store.load()
        started_at = time.monotonic()
        data = self.transport.post(
            self.config.endpoint("heartbeat"),
            {"protocol_version": PROTOCOL_VERSION, "agent_version": __version__,
             "engine_version": ENGINE_VERSION, "resources": self.resources(),
             "run_report": run_report},
            token=identity.agent_token,
            # A timeout after server commit is ambiguous. Reusing a strictly
            # increasing report sequence inside transport retries can turn that
            # ambiguity into a protocol conflict, so the outer loop retries with
            # a newly persisted sequence instead.
            retry_transient=False,
        )
        self._validate_common(data)
        if _node_id(data.get("node_id")) != identity.node_id:
            raise AgentStopped("服务端节点身份不匹配")
        if not isinstance(data.get("server_time"), str) or not data["server_time"]:
            raise ProtocolError("服务端 server_time 不符合协议")
        command = data.get("command")
        if not isinstance(command, dict) or command.get("type") not in {"idle", "prepare", "stop"}:
            raise AgentStopped("收到未知或不可执行命令，已停止")
        if command.get("type") == "prepare" and time.monotonic() - started_at > 15:
            raise AgentStopped("prepare 命令响应超过 15 秒接受窗口，已停止且不会执行")
        return HeartbeatResult(
            interval_seconds=_positive_int(data.get("heartbeat_interval_seconds"), "heartbeat_interval_seconds"),
            lease_seconds=_positive_int(data.get("lease_seconds"), "lease_seconds"),
            command=command,
        )

    @staticmethod
    def resources() -> dict[str, float]:
        return {
            "cpu_percent": float(psutil.cpu_percent(interval=None)),
            "memory_percent": float(psutil.virtual_memory().percent),
        }

    @staticmethod
    def _validate_common(data: dict[str, Any]) -> None:
        protocol_version = data.get("protocol_version")
        if isinstance(protocol_version, bool) or not isinstance(protocol_version, int) or protocol_version != PROTOCOL_VERSION:
            raise AgentStopped("服务端协议版本不匹配")
        if not isinstance(data.get("execution_enabled"), bool):
            raise AgentStopped("服务端执行能力状态不符合协议")
        _positive_int(data.get("heartbeat_interval_seconds"), "heartbeat_interval_seconds")
        _positive_int(data.get("lease_seconds"), "lease_seconds")
