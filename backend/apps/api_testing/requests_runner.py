"""Hard-deadline platform entry point for the API workspace requests runtime."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from .requests_runtime import (
    CaseContractError,
    build_error_report,
    hard_timeout_report,
    normalize_case,
    resolve_total_timeout,
)


PROCESS_GRACE_SECONDS = 2.0


def _preflight(script_id: str, script_content: Any, base_url: str | None, options: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], float] | dict[str, Any]:
    """Validate and freeze JSON input before starting the controlled worker."""
    try:
        if options is not None and not isinstance(options, Mapping):
            raise CaseContractError("options 必须是 JSON 对象")
        if base_url is not None and not isinstance(base_url, str):
            raise CaseContractError("base_url 必须是字符串")
        case = normalize_case(script_content)
        if not case["teststeps"]:
            raise CaseContractError("用例至少需要一个测试步骤才能执行")
        option_data = dict(options or {})
        if not isinstance(option_data.get("variables", {}), Mapping):
            raise CaseContractError("options.variables 必须是 JSON 对象")
        timeout = resolve_total_timeout(case, option_data)
        # Stdin carries only the JSON data contract, never Python data.
        json.dumps({"script_id": str(script_id), "case": case, "base_url": base_url, "options": option_data}, ensure_ascii=False)
        return case, option_data, timeout
    except (CaseContractError, TypeError, ValueError) as exc:
        return build_error_report(str(script_id), str(exc), type(exc).__name__)


def requests_runner(
    script_id: str,
    script_content: str,
    base_url: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run canonical JSON in a reaped subprocess with a hard wall deadline.

    The worker accepts a JSON payload over stdin and runs no caller code. The
    workflow remains serial: process isolation is a timeout/recovery boundary,
    not business-level concurrency.
    """
    prepared = _preflight(script_id, script_content, base_url, options)
    if isinstance(prepared, dict):
        return prepared
    case, option_data, total_timeout = prepared
    payload = json.dumps(
        {"script_id": str(script_id), "case": case, "base_url": base_url, "options": option_data},
        ensure_ascii=False, separators=(",", ":"),
    )
    worker = Path(__file__).with_name("requests_worker.py")
    try:
        completed = subprocess.run(
            [sys.executable, "-u", str(worker)], input=payload, text=True,
            capture_output=True, check=False, timeout=total_timeout + PROCESS_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return hard_timeout_report(str(script_id), case, total_timeout)
    except OSError as exc:
        return build_error_report(str(script_id), f"无法启动 requests worker：{exc}", type(exc).__name__, name=str(case["config"].get("name") or ""))

    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        detail = (completed.stderr or "").strip()
        suffix = f"；worker 输出：{detail[-500:]}" if detail else ""
        return build_error_report(str(script_id), f"requests worker 返回了无效 JSON：{exc}{suffix}", "WorkerProtocolError", name=str(case["config"].get("name") or ""))
    if not isinstance(result, dict):
        return build_error_report(str(script_id), "requests worker 返回结果必须是 JSON 对象", "WorkerProtocolError", name=str(case["config"].get("name") or ""))
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        message = f"requests worker 异常退出（{completed.returncode}）"
        if detail:
            message = f"{message}: {detail[-500:]}"
        return build_error_report(str(script_id), message, "WorkerExitError", name=str(case["config"].get("name") or ""))
    return result


__all__ = ["requests_runner", "PROCESS_GRACE_SECONDS"]
