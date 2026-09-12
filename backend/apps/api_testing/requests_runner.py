"""Hard-deadline requests entry point for the macOS/Linux platform workers.

The parent uses POSIX-selectable subprocess pipes. Windows pipe support is not
part of this implementation's deployment contract.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from typing import Any, Mapping

from .requests_runtime import (
    CaseContractError, build_error_report, hard_timeout_report,
    interrupted_report, normalize_case, resolve_total_timeout,
)


PROCESS_GRACE_SECONDS = 2.0
CONTROL_POLL_SECONDS = 0.1
STDERR_TAIL_BYTES = 8192
MAX_PROTOCOL_LINE_BYTES = 16 * 1024 * 1024
MAX_WORKER_PAYLOAD_BYTES = 16 * 1024 * 1024


def _preflight(script_id: str, script_content: Any, base_url: str | None,
               options: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], float] | dict[str, Any]:
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
        json.dumps(
            {"script_id": str(script_id), "case": case, "base_url": base_url, "options": option_data},
            ensure_ascii=False,
        )
        return case, option_data, timeout
    except (CaseContractError, TypeError, ValueError) as exc:
        return build_error_report(str(script_id), str(exc), type(exc).__name__)


def _cancel_requested(callback: Any) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        # Losing the cancellation signal must fail closed before another request.
        return True


def _notify_progress(callback: Any, report: Mapping[str, Any]) -> None:
    if callback is None:
        return
    partial = deepcopy(dict(report))
    partial["success"] = False
    partial["details"] = [{"step_datas": deepcopy(partial.get("step_datas", []))}]
    callback(partial)


def _kill_and_reap(process: subprocess.Popen[Any]) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:  # pragma: no cover
        try:
            process.kill()
        except OSError:
            pass
        process.wait()


def _cancelled_report(script_id: str, case: Mapping[str, Any],
                      partial_report: Mapping[str, Any] | None) -> dict[str, Any]:
    message = (
        "执行已取消；已终止并回收本地 worker。"
        "当前请求结果及副作用状态未知；未执行步骤已标记 skipped，未强行执行 cleanup。"
    )
    return interrupted_report(
        script_id, case, "Cancelled", message, partial_report=partial_report,
    )


def requests_runner(
    script_id: str,
    script_content: str,
    base_url: str | None = None,
    options: Mapping[str, Any] | None = None,
    hard_timeout_seconds: float | None = None,
    on_progress: Any = None,
    should_cancel: Any = None,
) -> dict[str, Any]:
    """Run canonical JSON in a reaped subprocess with checkpoints and a hard deadline.

    The progress and cancellation callbacks execute only in this parent process;
    neither callback nor caller Python is transported into the worker.
    """
    prepared = _preflight(script_id, script_content, base_url, options)
    if isinstance(prepared, dict):
        return prepared
    case, option_data, total_timeout = prepared
    if hard_timeout_seconds is not None:
        try:
            hard_timeout = float(hard_timeout_seconds)
        except (TypeError, ValueError):
            return build_error_report(
                str(script_id), "hard_timeout_seconds 必须是正秒数", "CaseContractError",
                name=str(case["config"].get("name") or ""),
            )
        if hard_timeout <= 0:
            return hard_timeout_report(str(script_id), case, 0)
        total_timeout = min(total_timeout, hard_timeout)
        option_data["total_timeout"] = total_timeout
    if _cancel_requested(should_cancel):
        return _cancelled_report(str(script_id), case, None)

    payload = json.dumps(
        {"script_id": str(script_id), "case": case, "base_url": base_url, "options": option_data},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_WORKER_PAYLOAD_BYTES:
        return build_error_report(
            str(script_id), "requests worker 输入超过 16 MiB 上限", "CaseContractError",
            name=str(case["config"].get("name") or ""),
        )
    worker = Path(__file__).with_name("requests_worker.py")
    process_timeout = total_timeout if hard_timeout_seconds is not None else total_timeout + PROCESS_GRACE_SECONDS
    deadline = time.monotonic() + process_timeout
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(worker)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
    except OSError as exc:
        return build_error_report(
            str(script_id), f"无法启动 requests worker：{exc}", type(exc).__name__,
            name=str(case["config"].get("name") or ""),
        )

    last_partial: dict[str, Any] | None = None
    final_report: dict[str, Any] | None = None
    protocol_error = ""
    evidence_error = ""
    checkpoint_deadline_expired = False
    cancellation_requested = False
    stderr_tail = b""
    stdout_buffer = b""
    selector = selectors.DefaultSelector()

    def handle_line(raw_line: bytes) -> None:
        nonlocal last_partial, final_report, protocol_error, evidence_error
        nonlocal cancellation_requested, checkpoint_deadline_expired
        if protocol_error or evidence_error or checkpoint_deadline_expired:
            return
        if not raw_line.strip():
            return
        if len(raw_line) > MAX_PROTOCOL_LINE_BYTES:
            protocol_error = "requests worker JSONL 单行超过协议上限"
            return
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            protocol_error = f"requests worker 返回了无效 JSONL：{exc}"
            return
        if not isinstance(message, Mapping):
            protocol_error = "requests worker JSONL 消息必须是对象"
            return
        report = message.get("report")
        if not isinstance(report, Mapping):
            protocol_error = "requests worker JSONL 消息缺少 report 对象"
            return
        if type(report.get("success")) is not bool or not isinstance(report.get("step_datas"), list):
            protocol_error = "requests worker report 缺少 success/step_datas 契约字段"
            return
        if not isinstance(report.get("replay_safety"), Mapping):
            protocol_error = "requests worker report 缺少 replay_safety 契约字段"
            return
        if message.get("type") == "checkpoint":
            last_partial = deepcopy(dict(report))
            try:
                _notify_progress(on_progress, last_partial)
            except Exception as exc:
                evidence_error = f"进度证据持久化失败：{type(exc).__name__}: {exc}"
                return
            cancellation_requested = _cancel_requested(should_cancel)
            checkpoint_deadline_expired = time.monotonic() >= deadline
            if not cancellation_requested and not checkpoint_deadline_expired:
                try:
                    assert process.stdin is not None
                    process.stdin.write(b'{"action":"continue"}\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    protocol_error = f"无法确认 worker checkpoint：{exc}"
        elif message.get("type") == "result":
            final_report = deepcopy(dict(report))
        else:
            protocol_error = f"requests worker 返回未知消息类型：{message.get('type')!r}"

    try:
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(payload + b"\n")
        process.stdin.flush()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or process.poll() is None:
            if evidence_error:
                _kill_and_reap(process)
                return interrupted_report(
                    str(script_id), case, "EvidencePersistenceError", evidence_error,
                    partial_report=last_partial,
                )
            if protocol_error:
                _kill_and_reap(process)
                return interrupted_report(
                    str(script_id), case, "WorkerProtocolError", protocol_error,
                    partial_report=last_partial,
                )
            if final_report is None and (cancellation_requested or _cancel_requested(should_cancel)):
                _kill_and_reap(process)
                return _cancelled_report(str(script_id), case, last_partial)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_and_reap(process)
                if final_report is not None:
                    return final_report
                return hard_timeout_report(
                    str(script_id), case, total_timeout, partial_report=last_partial,
                )
            events = selector.select(min(CONTROL_POLL_SECONDS, remaining))
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    try:
                        selector.unregister(key.fileobj)
                    except Exception:
                        pass
                    if key.data == "stdout" and stdout_buffer:
                        handle_line(stdout_buffer)
                        stdout_buffer = b""
                    continue
                if key.data == "stderr":
                    stderr_tail = (stderr_tail + chunk)[-STDERR_TAIL_BYTES:]
                    continue
                stdout_buffer += chunk
                if len(stdout_buffer) > MAX_PROTOCOL_LINE_BYTES:
                    protocol_error = "requests worker JSONL 缓冲超过协议上限"
                    break
                while b"\n" in stdout_buffer:
                    line, stdout_buffer = stdout_buffer.split(b"\n", 1)
                    handle_line(line)
                    if protocol_error or evidence_error or checkpoint_deadline_expired:
                        break
        returncode = process.wait(timeout=0)
    except (BrokenPipeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return interrupted_report(
            str(script_id), case, "WorkerProtocolError", f"requests worker 管道失败：{exc}",
            partial_report=last_partial,
        )
    finally:
        selector.close()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            _kill_and_reap(process)

    detail = stderr_tail.decode("utf-8", errors="replace").strip()
    if returncode != 0:
        message = f"requests worker 异常退出（{returncode}）"
        if detail:
            message = f"{message}: {detail[-500:]}"
        return interrupted_report(
            str(script_id), case, "WorkerExitError", message,
            partial_report=last_partial,
        )
    if final_report is None:
        message = protocol_error or "requests worker 未返回最终结果"
        if detail:
            message = f"{message}；worker 输出：{detail[-500:]}"
        return interrupted_report(
            str(script_id), case, "WorkerProtocolError", message,
            partial_report=last_partial,
        )
    return final_report


__all__ = ["requests_runner", "PROCESS_GRACE_SECONDS", "CONTROL_POLL_SECONDS"]
