"""Durable worker core for saved WebUI script assistant sessions."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import queue
import threading
from contextlib import suppress
from difflib import unified_diff
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage

from ai_core.model_manager import get_llm_manager
from ai_core.models import LLMConfiguration, ModelType
from .ai_assisted_debugging import (
    bounded_candidate_diff,
    candidate_leaks_runtime_values,
    failure_evidence,
    is_non_code_failure,
    redact_runtime_values,
    requires_directed_mcp,
)
from .execution_variables import (
    ExecutionVariableError,
    get_repair_runtime_variables,
    merge_execution_variables,
    runtime_variable_names,
)
from .model_service_errors import classify_model_service_error
from .models import (
    WebUIScriptAssistant,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
)
from .script_contract import (
    ScriptContractError,
    normalize_for_storage,
    store_script_content,
)
from .script_repair_policy import (
    validate_assertion_preservation,
    validate_targeted_repair,
)

logger = logging.getLogger(__name__)
MAX_REPAIR_ROUNDS = 2
SCRIPT_ASSISTANT_TOTAL_TIMEOUT_SECONDS = 10 * 60
_POLL_SECONDS = 0.25
_MAX_STREAM_CHARS = 200000
_MAX_STREAM_QUEUE = 64


class ScriptAssistantConflict(ValueError):
    pass


def case_edit_version(case: WebUITestCase) -> str:
    payload = {
        "title": case.title,
        "description": case.description,
        "variables": case.variables or [],
        "script": case.test_script_content or "",
        "source": case.script_source,
        "framework": case.script_framework,
        "module_id": case.module_id,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def candidate_hash(script: str) -> str:
    return hashlib.sha256((script or "").encode()).hexdigest()


def model_info(config: LLMConfiguration) -> dict[str, Any]:
    return {
        "config_id": config.id,
        "provider": config.provider,
        "provider_name": config.provider_name or config.get_provider_display(),
        "model_name": config.model_name,
    }


def _append_message(session, role, content):
    session.messages = [
        *list(session.messages or []),
        {
            "role": role,
            "content": str(content),
            "created_at": timezone.now().isoformat(),
        },
    ][-20:]


def _extract_code(text: str) -> str:
    # Source code is stored/prompted byte-for-byte.  Only the generated fenced response is unwrapped.
    text = str(text or "")
    if "```" not in text:
        return text
    for part in text.split("```")[1::2]:
        code = part.removeprefix("python").removeprefix("Python").strip()
        if "async def run(" in code:
            return code
    return text


def _diff(before, after):
    return bounded_candidate_diff(
        "".join(
            unified_diff(
                (before or "").splitlines(keepends=True),
                (after or "").splitlines(keepends=True),
                fromfile="source.py",
                tofile="candidate.py",
            )
        )
    )


def _cancel(task_id):
    return f"celery:cancel:{task_id}"


def _claim(session_id, revision, task_id):
    with transaction.atomic():
        item = WebUIScriptAssistant.objects.select_for_update().get(pk=session_id)
        if item.deadline_at and item.deadline_at <= timezone.now():
            WebUIScriptAssistant.objects.filter(
                pk=session_id,
                revision=revision,
                task_id=task_id,
                status__in=["queued", "running"],
            ).update(
                status="failed",
                phase="deadline",
                message="助手任务已超过总时限，请重新发起。",
            )
            return None
        if (
            item.revision != revision
            or item.task_id != task_id
            or item.status != "queued"
        ):
            return None
        update_fields = ["status", "phase", "message", "updated_at"]
        if item.operation == "verify":
            verification = dict(item.verification or {})
            # Keep the queue authorization intact while the worker is running;
            # _verify_candidate consumes it before replacing verification with
            # the immutable result.
            verification["status"] = "running"
            item.verification = verification
            item.status, item.phase, item.message = (
                "running",
                "verifying",
                "正在运行候选验证",
            )
            update_fields.append("verification")
        else:
            item.status, item.phase, item.message = (
                "running",
                "model",
                "正在生成候选脚本",
            )
        item.save(update_fields=update_fields)
        return item


def _still_current(session_id, revision, task_id):
    return (
        not cache.get(_cancel(task_id))
        and WebUIScriptAssistant.objects.filter(
            pk=session_id,
            revision=revision,
            task_id=task_id,
            status="running",
            deadline_at__gt=timezone.now(),
        ).exists()
    )


def expire_if_needed(session):
    if session.deadline_at and session.deadline_at <= timezone.now():
        WebUIScriptAssistant.objects.filter(
            pk=session.pk,
            revision=session.revision,
            task_id=session.task_id,
            status__in=["queued", "running"],
            deadline_at__lte=timezone.now(),
        ).update(
            status="failed",
            phase="deadline",
            message="助手任务已超过总时限，请重新发起。",
        )
        session.refresh_from_db()
    return session


def _remaining(session_id, revision, task_id):
    row = (
        WebUIScriptAssistant.objects.filter(
            pk=session_id, revision=revision, task_id=task_id, status="running"
        )
        .values("deadline_at")
        .first()
    )
    if not row or cache.get(_cancel(task_id)) or not row["deadline_at"]:
        return 0
    return max(0, (row["deadline_at"] - timezone.now()).total_seconds())


def _incomplete_stream_chunk(chunk):
    """Reject only explicit provider truncation/filter signals when available."""
    metadata = getattr(chunk, "response_metadata", None)
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("finish_reason") or "").lower() in {
        "length",
        "content_filter",
    }


def _stream_text(config_id, prompt, *, session_id, revision, task_id):
    """Use the selected low-level streaming API; no non-stream fallback or late write."""
    manager = get_llm_manager(config_id)
    manager._ensure_initialized()
    llm = manager.current_llm
    messages = [
        SystemMessage(
            content="你是 Playwright Python 脚本助手。仅输出完整 Python 代码，入口必须是 async def run(page) 或 async def run(page, variables)。"
        ),
        HumanMessage(content=prompt),
    ]
    stream, astream = getattr(llm, "stream", None), getattr(llm, "astream", None)
    if callable(stream):
        output, events, stop = (
            [],
            queue.Queue(maxsize=_MAX_STREAM_QUEUE),
            threading.Event(),
        )
        iterator = [None]

        def put(kind, value):
            while not stop.is_set():
                try:
                    events.put((kind, value), timeout=_POLL_SECONDS)
                    return
                except queue.Full:
                    continue

        def consume():
            try:
                iterator[0] = stream(messages)
                for chunk in iterator[0]:
                    if stop.is_set():
                        return
                    put("chunk", chunk)
                put("done", None)
            except BaseException as exc:
                put("error", exc)

        threading.Thread(
            target=consume, daemon=True, name="script-assistant-stream"
        ).start()
        try:
            while True:
                if (
                    not _still_current(session_id, revision, task_id)
                    or _remaining(session_id, revision, task_id) <= 0
                ):
                    raise ScriptAssistantConflict("任务已取消或超过总时限。")
                try:
                    kind, value = events.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                if kind == "done":
                    return "".join(output)
                if kind == "error":
                    raise value
                if _incomplete_stream_chunk(value):
                    raise ScriptAssistantConflict(
                        "模型响应被截断或内容过滤，未生成完整候选脚本。"
                    )
                output.append(manager._extract_stream_chunk_content(value))
                if sum(map(len, output)) > _MAX_STREAM_CHARS:
                    raise ScriptAssistantConflict("模型输出超过候选脚本大小限制。")
        finally:
            stop.set()
            close = getattr(iterator[0], "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
    if not callable(astream):
        raise RuntimeError("所选模型不支持流式响应")

    async def collect():
        output, iterator = [], astream(messages).__aiter__()
        while True:
            remaining = await sync_to_async(_remaining, thread_sensitive=True)(
                session_id, revision, task_id
            )
            if remaining <= 0 or not await sync_to_async(
                _still_current, thread_sensitive=True
            )(session_id, revision, task_id):
                raise ScriptAssistantConflict("任务已取消或超过总时限。")
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return "".join(output)
            except asyncio.TimeoutError as exc:
                raise ScriptAssistantConflict("模型流式响应超时，请重新发起。") from exc
            if _incomplete_stream_chunk(chunk):
                raise ScriptAssistantConflict(
                    "模型响应被截断或内容过滤，未生成完整候选脚本。"
                )
            output.append(manager._extract_stream_chunk_content(chunk))
            if sum(map(len, output)) > _MAX_STREAM_CHARS:
                raise ScriptAssistantConflict("模型输出超过候选脚本大小限制。")

    return asyncio.run(collect())


async def _await_current(coro, session, task_id):
    task = asyncio.create_task(coro)
    try:
        while not task.done():
            remaining = await sync_to_async(_remaining, thread_sensitive=True)(
                str(session.id), session.revision, task_id
            )
            if remaining <= 0 or not await sync_to_async(
                _still_current, thread_sensitive=True
            )(str(session.id), session.revision, task_id):
                task.cancel()
                raise ScriptAssistantConflict("任务已取消或超过总时限。")
            await asyncio.wait({task}, timeout=min(_POLL_SECONDS, remaining))
        return await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def directed_mcp_inspect(session, task_id, evidence, runtime_variables):
    """Trust callback trace only; model text never fabricates locator evidence."""
    from ai_core.mcp_agent_budget import BudgetedMCPAgent
    from mcp_use import MCPClient
    from .exploration_policy import ExplorationPolicy
    from .exploration_trace import ExplorationTraceRecorder
    from .generation_preflight import (
        prepare_playwright_mcp_output_config,
        resolve_active_playwright_mcp_config,
    )
    from .mcp_page_explorer import (
        READ_ONLY_DISABLED_TOOL_MESSAGES,
        ReadOnlyMCPBrowserToolGuard,
        suppress_mcp_raw_query_logs,
    )

    selected = resolve_active_playwright_mcp_config(session.user_id)
    if selected is None:
        return "未配置可用 MCP；仅依据失败日志生成候选，未证实定位器不能自动运行。", {}
    _, raw = selected
    prepared = prepare_playwright_mcp_output_config(raw, str(session.id))
    playwright = (prepared.get("mcpServers") or {}).get("playwright")
    if not isinstance(playwright, dict):
        return (
            "MCP 配置缺少可用 Playwright 服务；仅依据失败日志生成候选，未证实定位器不能自动运行。",
            {},
        )
    # An account-level MCP config may include database/filesystem services.
    # Repair inspection is deliberately a Playwright-only client session.
    payload = {"mcpServers": {"playwright": copy.deepcopy(playwright)}}
    manager = get_llm_manager(session.model_config_id)
    manager._ensure_initialized()

    async def inspect():
        client, recorder = MCPClient.from_dict(payload), ExplorationTraceRecorder()
        values = {x["name"]: x["value"] for x in runtime_variables if x.get("value")}
        recorder.configure_runtime(values, {k: "runtime" for k in values})
        guard = ReadOnlyMCPBrowserToolGuard(
            max_tool_calls=20,
            policy=ExplorationPolicy(
                namespace=f"aits-repair-{session.id}",
                data_scope="source_script",
                explicit_read_only=False,
                allow_test_data_writes=True,
                cleanup_expected=False,
            ),
            trace_recorder=recorder,
        )
        try:
            await _await_current(client.create_all_sessions(), session, task_id)
            agent = BudgetedMCPAgent(
                llm=manager.current_llm,
                client=client,
                max_steps=25,
                callbacks=[guard],
                disallowed_tools=list(READ_ONLY_DISABLED_TOOL_MESSAGES),
                additional_instructions="仅复现来源脚本已有登录、导航、打开故障界面和既有动作；不得扩展业务路径、审批、付款、发布、上传、下载、删除或调用页面 JavaScript。页面结论必须来自工具回调。",
            )
            await _await_current(agent.initialize(), session, task_id)
            with suppress_mcp_raw_query_logs():
                result = await _await_current(
                    agent.run(
                        json.dumps(
                            {
                                "task": "定位失败脚本中的页面证据，仅复现原脚本路径。",
                                "source_script": session.source_script,
                                "failure": evidence,
                                "runtime_input_values": values,
                            },
                            ensure_ascii=False,
                        ),
                        manage_connector=False,
                    ),
                    session,
                    task_id,
                )
            return str(result)[:12000], recorder.build(
                tool_stats=guard.get_stats()
            ).model_dump(mode="json")
        finally:
            try:
                await asyncio.wait_for(client.close_all_sessions(), timeout=5)
            except Exception:
                logger.exception("关闭脚本助手 MCP 会话失败: session_id=%s", session.id)

    try:
        return asyncio.run(inspect())
    except ScriptAssistantConflict:
        raise
    except Exception:
        logger.exception("脚本助手 MCP 定向检查失败: session_id=%s", session.id)
        return (
            "MCP 定向检查未完成；仅依据失败日志生成候选，未证实定位器不能自动运行。",
            {},
        )


def _prompt(session, failure=None):
    base = (
        session.candidate_script
        if session.pending_use_candidate and session.candidate_script
        else (session.pending_script or session.source_script)
    )
    variables = (
        session.pending_variables
        if session.mode == "edit"
        else session.source_variables
    )
    history = [
        {"role": x.get("role", ""), "content": x.get("content", "")}
        for x in list(session.messages or [])[-12:]
    ]
    parts = [
        "当前完整脚本（原文保持，不要截断）：\n" + base,
        "变量定义（不含一次性运行变量值）：\n"
        + json.dumps(variables or [], ensure_ascii=False),
        "场景描述：\n" + (session.pending_description or ""),
        "对话历史：\n" + json.dumps(history, ensure_ascii=False),
        "本次用户要求：\n" + (session.pending_message or "请改进这段脚本。"),
        "保留业务步骤及已有断言；只输出完整替换脚本。",
    ]
    if failure:
        parts.append("失败证据：\n" + json.dumps(failure, ensure_ascii=False))
    return "\n\n".join(parts)


def _source_failure(session, variables):
    if session.suite_case_id:
        x = session.suite_case
        return failure_evidence(
            stdout=x.stdout,
            log=x.log,
            fallback=x.error_message,
            runtime_variables=variables,
        )
    try:
        x = session.execution.case_execution_detail
    except WebUITestCaseExecutionDetail.DoesNotExist as exc:
        raise ScriptAssistantConflict("执行详情缺失，无法读取修复快照。") from exc
    return failure_evidence(
        log=x.log, fallback=x.error_message, runtime_variables=variables
    )


def _validate_candidate(session, candidate, variables=None):
    blockers = []
    try:
        normalize_for_storage(candidate)
    except ScriptContractError as exc:
        blockers.append({"code": "SCRIPT_CONTRACT_INVALID", "message": str(exc)})
    if session.mode == "repair":
        blockers += validate_assertion_preservation(session.source_script, candidate)
        # ``{}`` is not a trace schema and made no-MCP repair fail before a
        # candidate could be shown.  A real empty trace carries *no* locator
        # evidence, so the policy still blocks any unproven locator change.
        from .exploration_trace import ExplorationTraceRecorder

        trace = getattr(
            session, "_directed_mcp_snapshot", None
        ) or ExplorationTraceRecorder().build(tool_stats={})
        blockers += validate_targeted_repair(session.source_script, candidate, trace)
    if variables and candidate_leaks_runtime_values(
        candidate, variables, baseline_script=session.source_script
    ):
        blockers.append(
            {
                "code": "RUNTIME_VALUE_LEAK",
                "message": "候选脚本不得写入一次性运行变量。",
            }
        )
    return blockers


def _persist(session_id, revision, task_id, **updates):
    updates["updated_at"] = timezone.now()
    return bool(
        WebUIScriptAssistant.objects.filter(
            pk=session_id,
            revision=revision,
            task_id=task_id,
            status="running",
            deadline_at__gt=timezone.now(),
        ).update(**updates)
    )


def _variables(session, revision):
    values = get_repair_runtime_variables(
        session.id, revision, candidate_hash(session.source_script)
    )
    verification = session.verification or {}
    quality = session.quality_report or {}
    names = (
        verification.get("runtime_variable_names")
        or quality.get("runtime_variable_names")
        or []
    )
    supplied = {item.get("name") for item in values}
    if names and not set(names).issubset(supplied):
        raise ExecutionVariableError("一次性运行变量已过期或不完整，请重新输入后验证。")
    if verification.get("runtime_variables_present") and not values:
        raise ExecutionVariableError("一次性运行变量已过期，请重新输入后验证。")
    return values


def _candidate_updates(
    session, candidate, blockers, attempts=None, message="候选脚本已生成"
):
    digest = candidate_hash(candidate)
    return {
        "status": "candidate_ready",
        "phase": "candidate",
        "message": message,
        "candidate_script": candidate,
        "candidate_hash": digest,
        "candidate_diff": _diff(session.source_script, candidate),
        "blockers": blockers,
        "attempts": list(session.attempts or []) if attempts is None else attempts,
        "verification": {
            "status": "unverified",
            "candidate_hash": digest,
            "summary": "候选尚未执行验证。",
        },
    }


def _summary(*parts):
    """Keep user-facing facts short; never persist raw runner/model exception text."""
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())[
        :2000
    ]


def _outcome(result):
    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    success = result.get("operation_success", payload.get("operation_success"))
    status = result.get("evaluation_status", payload.get("evaluation_status"))
    count = result.get(
        "runtime_assertion_count", payload.get("runtime_assertion_count")
    )
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if not isinstance(success, bool) or status not in {
        "passed",
        "failed",
        "error",
        "incomplete",
        "stopped",
    }:
        return {
            "status": "error",
            "operation_success": False,
            "runtime_assertion_count": 0,
            "payload": payload,
            "message": "执行器未返回完整验证结果，候选不能判定为通过。",
        }
    if status == "passed" and (not success or count <= 0):
        return {
            "status": "incomplete",
            "operation_success": success,
            "runtime_assertion_count": count,
            "payload": payload,
            "message": "执行器未提供有效运行时断言证据，候选不能判定为通过。",
        }
    return {
        "status": status,
        "operation_success": success,
        "runtime_assertion_count": count,
        "payload": payload,
        "message": "",
    }


def _execute_candidate(session, candidate, variables, round_number, *, label="AI 修复"):
    """Fresh execution only; a runner exception always settles both records."""
    from .tasks import (
        _failure_screenshot_paths,
        _normalize_persisted_screenshot_path,
        _raw_execution_log,
        _run_test_script,
    )

    if not _still_current(str(session.id), session.revision, session.task_id):
        raise ScriptAssistantConflict("任务已取消或超过总时限，候选未执行。")
    started = timezone.now()
    suffix = f"第 {round_number} 轮" if label == "AI 修复" else ""
    execution = WebUITestExecution.objects.create(
        exec_type="case",
        name=f"{session.test_case.title}（{label}{suffix}）",
        description=f"{label}候选验证",
        executor=session.user,
        project=session.project,
        trigger_type="llm",
        status="running",
        start_time=started,
    )
    detail_snapshot = dict(
        execution=execution,
        test_case=session.test_case,
        status="running",
        start_time=started,
        source_script=candidate,
        source_script_version=session.source_script_version,
        source_variables=copy.deepcopy(session.source_variables),
        execution_options=copy.deepcopy(session.source_options),
        source_edit_version=session.source_edit_version,
        runtime_variable_names=runtime_variable_names(variables),
    )
    detail = WebUITestCaseExecutionDetail.objects.create(**detail_snapshot)
    absolute, relative = _failure_screenshot_paths(
        execution.id,
        f'assistant_{"repair" if label=="AI 修复" else "verify"}_{round_number}.png',
    )
    try:
        effective_options = copy.deepcopy(session.source_options)
        remaining = max(
            1, int(_remaining(str(session.id), session.revision, session.task_id))
        )
        effective_options["timeout"] = min(
            int(effective_options.get("timeout") or remaining), remaining
        )
        result = _run_test_script(
            candidate,
            effective_options,
            failure_screenshot_path=absolute,
            environment_variables=merge_execution_variables(
                session.source_variables, variables
            ),
        )
        outcome = _outcome(result)
        evidence = failure_evidence(
            stdout=outcome["payload"].get("stdout", ""),
            stderr=outcome["payload"].get("stderr", ""),
            fallback=outcome["message"] or result.get("error", ""),
            runtime_variables=variables,
        )
        summary = (
            ""
            if outcome["status"] == "passed"
            else (
                outcome["message"]
                or (
                    "操作已完成，但验证未通过。"
                    if outcome["operation_success"]
                    else evidence["summary"]
                )
            )
        )
    except Exception as exc:
        logger.exception(
            "脚本助手候选执行异常: session_id=%s execution_id=%s",
            session.id,
            execution.id,
        )
        outcome = {
            "status": "error",
            "operation_success": False,
            "runtime_assertion_count": 0,
            "payload": {},
            "message": "",
        }
        evidence = failure_evidence(
            fallback=f"候选执行器异常：{exc}", runtime_variables=variables
        )
        summary = evidence["summary"]
    ended = timezone.now()
    payload = {
        **outcome["payload"],
        "stdout": redact_runtime_values(
            outcome["payload"].get("stdout", ""), variables
        ),
        "stderr": redact_runtime_values(
            outcome["payload"].get("stderr", ""), variables
        ),
    }
    persisted = _normalize_persisted_screenshot_path(
        execution.id, payload.get("screenshot_path") or relative
    )
    screenshot = bool(
        persisted and os.path.exists(os.path.join(str(settings.MEDIA_ROOT), persisted))
    )
    (
        execution.status,
        execution.error_message,
        execution.end_time,
        execution.duration,
        outcome_log,
    ) = (
        outcome["status"],
        summary[:2000],
        ended,
        (ended - started).total_seconds(),
        payload.get("test_file") or "",
    )
    execution.log_path = outcome_log
    execution.save(
        update_fields=[
            "status",
            "error_message",
            "end_time",
            "duration",
            "log_path",
            "updated_at",
        ]
    )
    (
        detail.status,
        detail.error_message,
        detail.end_time,
        detail.duration,
        detail.log,
    ) = (
        outcome["status"],
        summary[:2000] or None,
        ended,
        execution.duration,
        _raw_execution_log(payload),
    )
    if screenshot:
        detail.screenshot_path = persisted
    detail.save(
        update_fields=[
            "status",
            "error_message",
            "end_time",
            "duration",
            "log",
            "screenshot_path",
        ]
    )
    return {
        "status": outcome["status"],
        "execution_id": execution.id,
        "summary": summary[:2000],
        "has_screenshot": screenshot,
        "runtime_assertion_count": outcome["runtime_assertion_count"],
        "operation_success": outcome["operation_success"],
        "diagnostics": [
            {
                "code": str(evidence.get("category") or "RUNTIME_FAILURE").upper(),
                "message": summary[:1000],
            }
        ],
        "evidence": evidence,
    }


def _verify_candidate(session, revision, task_id):
    authorization = (session.verification or {}).get("authorization") or {}
    action = verify_action_state(session, allow_running=True)
    if (
        session.status != "running"
        or not session.candidate_script
        or candidate_hash(session.candidate_script) != session.candidate_hash
        or not action["can_verify"]
        or authorization.get("candidate_hash") != session.candidate_hash
        or authorization.get("revision") != revision
        or authorization.get("task_id") != task_id
        or not isinstance(authorization.get("acknowledge_review"), bool)
        or (
            action["requires_acknowledge_review"]
            and authorization.get("acknowledge_review") is not True
        )
    ):
        raise ScriptAssistantConflict("当前候选已变化或存在限制，不能执行验证。")
    attempt = _execute_candidate(
        session,
        session.candidate_script,
        _variables(session, revision),
        1,
        label="AI 候选验证",
    )
    digest = candidate_hash(session.candidate_script)
    row = {
        "round": len(session.attempts or []) + 1,
        "candidate_hash": digest,
        "execution_id": attempt["execution_id"],
        "execution_status": attempt["status"],
        "summary": attempt["summary"],
        "has_screenshot": attempt["has_screenshot"],
        "runtime_assertion_count": attempt["runtime_assertion_count"],
        "diagnostics": attempt["diagnostics"],
        # verification is replaced by the result below; retain the exact
        # one-time queue authorization with this immutable attempt record.
        "authorization": dict(authorization),
    }
    verification = {
        "status": attempt["status"],
        "execution_id": attempt["execution_id"],
        "summary": attempt["summary"],
        "candidate_hash": digest,
        "runtime_assertion_count": attempt["runtime_assertion_count"],
        "diagnostics": attempt["diagnostics"],
    }
    passed = attempt["status"] == "passed"
    _persist(
        session.id,
        revision,
        task_id,
        attempts=[*list(session.attempts or []), row],
        verification=verification,
        status="candidate_passed" if passed else "candidate_ready",
        phase="verified",
        message="候选验证通过" if passed else "候选验证未通过，请查看执行详情。",
        summary=_summary(
            "本次候选已实际验证通过。"
            if passed
            else f"本次候选实际验证未通过：{attempt['summary']}"
        ),
    )
    return {
        "success": attempt["status"] == "passed",
        "execution_id": attempt["execution_id"],
        "status": attempt["status"],
    }


def _persist_repair_progress(
    session,
    revision,
    task_id,
    candidate,
    attempts,
    verification,
    *,
    phase,
    message,
    summary,
):
    """Checkpoint a running repair so a later model failure cannot hide round evidence."""
    return _persist(
        session.id,
        revision,
        task_id,
        candidate_script=candidate,
        candidate_hash=candidate_hash(candidate),
        candidate_diff=_diff(session.source_script, candidate),
        attempts=attempts,
        verification=verification,
        phase=phase,
        message=message,
        summary=summary,
    )


def _run_repair_rounds(session, revision, task_id, candidate, variables, evidence):
    config = LLMConfiguration.objects.get(
        id=session.model_config_id, is_active=True, model_type=ModelType.LLM
    )
    attempts = list(session.attempts or [])
    original_failure_summary = str((evidence or {}).get("summary") or "").strip()
    for number in range(1, MAX_REPAIR_ROUNDS + 1):
        if not _still_current(session.id, revision, task_id):
            return {"success": False, "ignored": True}
        if number > 1:
            session.candidate_script, session.pending_use_candidate = candidate, True
            previous_verification = {
                "status": attempts[-1]["execution_status"],
                "execution_id": attempts[-1]["execution_id"],
                "summary": attempts[-1]["summary"],
                "candidate_hash": candidate_hash(candidate),
                "runtime_assertion_count": attempts[-1]["runtime_assertion_count"],
                "diagnostics": attempts[-1]["diagnostics"],
            }
            _persist_repair_progress(
                session,
                revision,
                task_id,
                candidate,
                attempts,
                previous_verification,
                phase="model",
                message="第 1 轮候选未通过，正在生成第 2 轮候选。",
                summary=_summary(
                    original_failure_summary, "第 1 轮候选未通过，正在生成第 2 轮候选。"
                ),
            )
            candidate = _extract_code(
                _stream_text(
                    config.id,
                    _prompt(session, evidence),
                    session_id=str(session.id),
                    revision=revision,
                    task_id=task_id,
                )
            )
            blockers = _validate_candidate(session, candidate, variables)
            if blockers:
                updates = _candidate_updates(
                    session, candidate, blockers, attempts, "第二轮候选需要人工检查"
                )
                updates["summary"] = _summary(
                    original_failure_summary, "第 2 轮候选存在限制，尚未执行验证。"
                )
                _persist(session.id, revision, task_id, **updates)
                return {"success": False, "blockers": blockers}
        _persist_repair_progress(
            session,
            revision,
            task_id,
            candidate,
            attempts,
            {
                "status": "unverified",
                "candidate_hash": candidate_hash(candidate),
                "summary": f"第 {number} 轮候选正在执行验证。",
            },
            phase="verifying",
            message=f"正在验证第 {number} 轮候选。",
            summary=_summary(
                original_failure_summary, f"第 {number} 轮候选正在执行验证。"
            ),
        )
        attempt = _execute_candidate(session, candidate, variables, number)
        attempts.append(
            {
                "round": number,
                "candidate_hash": candidate_hash(candidate),
                "execution_id": attempt["execution_id"],
                "execution_status": attempt["status"],
                "summary": attempt["summary"],
                "has_screenshot": attempt["has_screenshot"],
                "runtime_assertion_count": attempt["runtime_assertion_count"],
                "diagnostics": attempt["diagnostics"],
            }
        )
        verification = {
            "status": attempt["status"],
            "execution_id": attempt["execution_id"],
            "summary": attempt["summary"],
            "candidate_hash": candidate_hash(candidate),
            "runtime_assertion_count": attempt["runtime_assertion_count"],
            "diagnostics": attempt["diagnostics"],
        }
        _persist_repair_progress(
            session,
            revision,
            task_id,
            candidate,
            attempts,
            verification,
            phase="verified",
            message=f"第 {number} 轮候选验证已结束。",
            summary=_summary(
                original_failure_summary,
                (
                    f"第 {number} 轮候选实际验证未通过。"
                    if attempt["status"] != "passed"
                    else f"第 {number} 轮候选已实际验证通过。"
                ),
            ),
        )
        if attempt["status"] == "passed":
            _persist(
                session.id,
                revision,
                task_id,
                status="candidate_passed",
                phase="verified",
                message="修复候选验证通过",
                candidate_script=candidate,
                candidate_hash=candidate_hash(candidate),
                candidate_diff=_diff(session.source_script, candidate),
                blockers=[],
                attempts=attempts,
                verification=verification,
                summary=_summary(
                    original_failure_summary, f"第 {number} 轮候选已实际验证通过。"
                ),
            )
            return {"success": True, "execution_id": attempt["execution_id"]}
        evidence = attempt["evidence"]
        if is_non_code_failure(evidence):
            updates = _candidate_updates(
                session,
                candidate,
                [],
                attempts,
                "候选未通过，失败原因不属于可自动修复的脚本问题。",
            )
            updates.update(
                verification=verification,
                summary=_summary(
                    original_failure_summary,
                    f"第 {number} 轮候选未通过，失败原因不属于可自动修复的脚本问题。",
                ),
            )
            _persist(session.id, revision, task_id, **updates)
            return {
                "success": False,
                "reason": "non_code_failure",
                "execution_id": attempt["execution_id"],
            }
    updates = _candidate_updates(
        session, candidate, [], attempts, "两轮修复候选均未通过，请人工检查。"
    )
    updates.update(
        phase="verified",
        verification=verification,
        summary=_summary(original_failure_summary, "两轮候选均未通过，需人工检查。"),
    )
    _persist(session.id, revision, task_id, **updates)
    return {"success": False, "execution_id": attempt["execution_id"]}


def run_script_assistant_operation(session_id, revision, task_id):
    session = _claim(session_id, revision, task_id)
    if session is None:
        return {"success": False, "ignored": True}
    try:
        # Verification executes the already persisted candidate only.  It must
        # remain available after a model configuration is disabled.
        if session.operation == "verify":
            return _verify_candidate(session, revision, task_id)
        config = LLMConfiguration.objects.get(
            id=session.model_config_id, is_active=True, model_type=ModelType.LLM
        )
        variables = []
        evidence = None
        if session.mode == "repair":
            variables = _variables(session, revision)
            evidence = _source_failure(session, variables)
            if is_non_code_failure(evidence):
                _persist(
                    session.id,
                    revision,
                    task_id,
                    status="failed",
                    phase="failure",
                    message=evidence["summary"],
                    summary=_summary(
                        evidence["summary"], "失败原因不属于可自动修复的脚本问题。"
                    ),
                    blockers=[
                        {"code": "NON_CODE_FAILURE", "message": evidence["summary"]}
                    ],
                )
                return {"success": False, "reason": "non_code_failure"}
            if requires_directed_mcp(evidence):
                if not _still_current(session.id, revision, task_id):
                    return {"success": False, "ignored": True}
                evidence = dict(evidence)
                mcp_text, session._directed_mcp_snapshot = directed_mcp_inspect(
                    session, task_id, evidence, variables
                )
                evidence["directed_mcp"] = mcp_text
        candidate = _extract_code(
            _stream_text(
                config.id,
                _prompt(session, evidence),
                session_id=str(session.id),
                revision=revision,
                task_id=task_id,
            )
        )
        if not _still_current(session.id, revision, task_id):
            return {"success": False, "ignored": True}
        blockers = _validate_candidate(session, candidate, variables)
        _append_message(
            session,
            "assistant",
            "已生成候选脚本。" if not blockers else "候选存在需要人工检查的限制。",
        )
        if session.mode == "repair" and not blockers:
            return _run_repair_rounds(
                session, revision, task_id, candidate, variables, evidence
            )
        updates = _candidate_updates(session, candidate, blockers)
        updates.update(
            quality_report={
                **dict(session.quality_report or {}),
                "safe_to_verify": not blockers,
                "model": model_info(config),
            },
            messages=session.messages,
            summary=_summary(
                (evidence or {}).get("summary"),
                (
                    "候选已生成，尚未执行验证。"
                    if not blockers
                    else "候选存在限制，尚未执行验证。"
                ),
            ),
        )
        return {
            "success": _persist(session.id, revision, task_id, **updates),
            "candidate_hash": candidate_hash(candidate),
            "blockers": blockers,
        }
    except ScriptAssistantConflict as exc:
        logger.exception("脚本助手任务冲突或中止: session_id=%s", session_id)
        item = WebUIScriptAssistant.objects.filter(pk=session_id).first()
        if item is not None and item.deadline_at and item.deadline_at <= timezone.now():
            item = expire_if_needed(item)
            return {"success": False, "message": item.message}
        if (
            item is None
            or item.revision != revision
            or item.task_id != task_id
            or item.status != "running"
            or cache.get(_cancel(task_id))
        ):
            return {"success": False, "ignored": True, "message": str(exc)}
        message = str(exc) or "脚本助手未能完成当前操作。"
        _persist(
            session_id,
            revision,
            task_id,
            status="failed",
            phase="failure",
            message=message,
            summary=_summary(item.summary, message),
            blockers=[{"code": "ASSISTANT_OPERATION_ABORTED", "message": message}],
        )
        return {"success": False, "message": message}
    except Exception as exc:
        logger.exception("脚本助手执行失败: session_id=%s", session_id)
        code, message = (
            ("RUNTIME_VARIABLES_UNAVAILABLE", str(exc))
            if isinstance(exc, ExecutionVariableError)
            else (
                classify_model_service_error(exc, stage="script_assistant")
                or ("ASSISTANT_FAILED", "脚本助手执行失败，请检查模型或稍后重试。")
            )
        )
        _persist(
            session_id,
            revision,
            task_id,
            status="failed",
            phase="failure",
            message=message,
            blockers=[{"code": code, "message": message}],
        )
        return {"success": False, "message": message}


def apply_repair(
    session,
    *,
    expected_edit_version,
    expected_hash,
    expected_revision=None,
    acknowledge_review=False,
):
    """Persist a repair candidate after automatic or explicit manual review.

    Scope changes remain blocked from every automatic repair and verification
    path.  This function only permits a user to save that narrow, already
    generated candidate after a separate explicit acknowledgement.
    """
    if session.mode != "repair":
        raise ScriptAssistantConflict("编辑会话只能采用到编辑器，不能直接写入用例。")
    if not isinstance(acknowledge_review, bool):
        raise ScriptAssistantConflict("acknowledge_review 必须是布尔值。")
    with transaction.atomic():
        session = WebUIScriptAssistant.objects.select_for_update().get(pk=session.pk)
        if expected_revision is not None and session.revision != expected_revision:
            raise ScriptAssistantConflict("会话已变化，请刷新后重试。")
        if (
            expected_hash != session.candidate_hash
            or not session.candidate_script
            or candidate_hash(session.candidate_script) != session.candidate_hash
        ):
            raise ScriptAssistantConflict("候选脚本已变化，请刷新后重试。")
        try:
            normalize_for_storage(session.candidate_script)
        except ScriptContractError as exc:
            raise ScriptAssistantConflict(f"候选脚本不符合保存契约：{exc}") from exc

        adoption = repair_adoption_state(session)
        if not adoption["can_apply"]:
            raise ScriptAssistantConflict("当前会话不能采用候选。")
        if adoption["requires_acknowledge_review"] and not acknowledge_review:
            raise ScriptAssistantConflict(
                "该候选未通过自动修复范围检查，必须人工确认完整代码后才能保存。"
            )
        if (
            session.status == "candidate_passed"
            and (session.verification or {}).get("candidate_hash")
            != session.candidate_hash
        ):
            raise ScriptAssistantConflict("候选验证记录不属于当前脚本，请重新验证。")
        case = WebUITestCase.objects.select_for_update().get(
            pk=session.test_case_id, project_id=session.project_id
        )
        if (
            case_edit_version(case) != expected_edit_version
            or case_edit_version(case) != session.source_edit_version
        ):
            raise ScriptAssistantConflict(
                "用例已被其他编辑修改，请保留当前编辑并刷新后处理。"
            )
        store_script_content(case, session.candidate_script, source="manual")
        if adoption["requires_acknowledge_review"]:
            quality_report = dict(session.quality_report or {})
            quality_report["manual_adoption"] = {
                "acknowledged_by": session.user_id,
                "acknowledged_at": timezone.now().isoformat(),
                "candidate_hash": session.candidate_hash,
                "reason": "REPAIR_SCOPE_CHANGED",
            }
            session.quality_report = quality_report
        session.status = "applied"
        session.save(update_fields=["status", "quality_report", "updated_at"])
        return case


def repair_adoption_state(session) -> dict[str, Any]:
    """Return the server-authoritative save path for a repair candidate."""
    valid_candidate = bool(session.candidate_script and session.candidate_hash)
    if valid_candidate:
        valid_candidate = (
            candidate_hash(session.candidate_script) == session.candidate_hash
        )
    if valid_candidate:
        try:
            normalize_for_storage(session.candidate_script)
        except ScriptContractError:
            valid_candidate = False

    blockers = list(session.blockers or [])
    blocker_codes = [
        item.get("code") if isinstance(item, dict) else None for item in blockers
    ]
    automatic = (
        session.mode == "repair"
        and session.status in {"candidate_ready", "candidate_passed"}
        and valid_candidate
        and not blockers
    )
    manual_review = (
        session.mode == "repair"
        and session.status in {"candidate_ready", "candidate_passed"}
        and valid_candidate
        and bool(blocker_codes)
        and all(code == "REPAIR_SCOPE_CHANGED" for code in blocker_codes)
    )
    return {
        "kind": (
            "automatic"
            if automatic
            else ("manual_review" if manual_review else "unavailable")
        ),
        "can_apply": automatic or manual_review,
        "requires_acknowledge_review": manual_review,
    }


def verify_action_state(session, *, allow_running: bool = False) -> dict[str, Any]:
    """Return the server-authoritative permission for one candidate run."""
    valid_candidate = bool(session.candidate_script and session.candidate_hash)
    if valid_candidate:
        valid_candidate = (
            candidate_hash(session.candidate_script) == session.candidate_hash
        )
    if valid_candidate:
        try:
            normalize_for_storage(session.candidate_script)
        except ScriptContractError:
            valid_candidate = False

    valid_statuses = {"candidate_ready", "candidate_passed"}
    if allow_running:
        valid_statuses.add("running")
    candidate_state = session.status in valid_statuses and valid_candidate
    blockers = list(session.blockers or [])
    blocker_codes = [
        item.get("code") if isinstance(item, dict) else None for item in blockers
    ]
    automatic = candidate_state and not blockers
    manual_review = (
        candidate_state
        and session.mode == "repair"
        and bool(blocker_codes)
        and all(code == "REPAIR_SCOPE_CHANGED" for code in blocker_codes)
    )
    return {
        "can_verify": automatic or manual_review,
        "requires_acknowledge_review": manual_review,
    }
