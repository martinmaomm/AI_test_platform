"""JSON-only subprocess worker used by :mod:`requests_runner`."""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Mapping
from typing import Any

try:  # Supports direct execution by the parent without PYTHONPATH assumptions.
    from .requests_runtime import build_error_report, run_case
except ImportError:  # pragma: no cover - exercised only by the subprocess boundary
    from requests_runtime import build_error_report, run_case


MAX_WORKER_PAYLOAD_BYTES = 16 * 1024 * 1024


def _install_offline_network_guard() -> None:
    """Block IPv4/IPv6 socket connections only in the explicit offline harness."""
    if os.environ.get("OFFLINE_TEST_NETWORK") != "blocked":
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _blocked_connect(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1", "localhost"}:
                return original_connect(sock, address)
            raise RuntimeError("Automation Platform offline test guard: network connections are forbidden")
        return original_connect(sock, address)

    def _blocked_connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1", "localhost"}:
                return original_connect_ex(sock, address)
            raise RuntimeError("Automation Platform offline test guard: network connections are forbidden")
        return original_connect_ex(sock, address)

    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex


def _write_message(message: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(message), ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _checkpoint(message: Mapping[str, Any]) -> None:
    """Send one checkpoint and wait until the parent permits further work."""
    _write_message({"type": "checkpoint", **message})
    raw_control = sys.stdin.readline()
    if not raw_control:
        raise RuntimeError("parent closed checkpoint control channel")
    try:
        control = json.loads(raw_control)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid parent checkpoint control: {exc}") from exc
    if not isinstance(control, Mapping) or control.get("action") != "continue":
        raise RuntimeError("parent rejected checkpoint continuation")


def _result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return build_error_report("", "worker 输入必须是 JSON 对象")
    script_id = str(payload.get("script_id", ""))
    if "case" not in payload:
        return build_error_report(script_id, "worker 输入缺少 case")
    return run_case(
        script_id, payload["case"], base_url=payload.get("base_url"),
        options=payload.get("options"),
        on_checkpoint=_checkpoint,
    )


def main() -> int:
    _install_offline_network_guard()
    try:
        raw_payload = sys.stdin.readline(MAX_WORKER_PAYLOAD_BYTES + 1)
        if len(raw_payload.encode("utf-8")) > MAX_WORKER_PAYLOAD_BYTES:
            raise ValueError("worker 输入超过 16 MiB 上限")
        payload = json.loads(raw_payload)
        result = _result(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        result = build_error_report("", f"worker 输入 JSON 无效：{exc}")
    _write_message({"type": "result", "report": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
