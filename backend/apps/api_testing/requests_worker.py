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


def _install_offline_network_guard() -> None:
    """Block IPv4/IPv6 socket connections only in the explicit offline harness."""
    if os.environ.get("AITS_OFFLINE_TEST_NETWORK") != "blocked":
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _blocked_connect(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("AITS offline test guard: network connections are forbidden")
        return original_connect(sock, address)

    def _blocked_connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("AITS offline test guard: network connections are forbidden")
        return original_connect_ex(sock, address)

    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex


def _result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return build_error_report("", "worker 输入必须是 JSON 对象")
    script_id = str(payload.get("script_id", ""))
    if "case" not in payload:
        return build_error_report(script_id, "worker 输入缺少 case")
    return run_case(
        script_id, payload["case"], base_url=payload.get("base_url"), options=payload.get("options"),
    )


def main() -> int:
    _install_offline_network_guard()
    try:
        payload = json.loads(sys.stdin.read())
        result = _result(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        result = build_error_report("", f"worker 输入 JSON 无效：{exc}")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
