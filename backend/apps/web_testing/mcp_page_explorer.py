"""Read-only Playwright MCP exploration for the V2 generation pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Callable

from ai_core.webui_playwright_agent import (
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_MAX_STEPS,
    MCPBrowserToolGuard,
    _classify_mcp_error,
    _get_mcp_error_message,
)
from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from mcp_use import MCPClient

from .exploration_output import (
    ExplorationOutputError,
    output_summary,
    parse_exploration_output,
)
from .generation_contracts import (
    ExplorationToolStats,
    ExplorationSnapshot,
    GenerationContractError,
    parse_exploration_snapshot_json,
)
from .exploration_completion import assess_exploration_completion
from .generation_preflight import (
    prepare_playwright_mcp_output_config,
    validate_generation_output_id,
)
from .generation_security import redact_exploration_metadata

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你只负责只读页面探索，绝对不要生成 Python、JavaScript、Markdown 或测试脚本。
所有 playwright_navigate 调用必须传 JSON 布尔值 `headless: true`。
浏览器工具总数最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。
允许打开页面、读取可见文本、打开菜单/Tab/查询条件/表单弹窗；如提供登录信息，只能用于本次登录。
切换页面或打开、关闭弹窗后，使用 playwright_get_visible_text 或 playwright_get_visible_html 确认新的页面状态；不要在未观察到变化时反复执行相同交互。
工具可用性以当前只读探索能力为准：通过 playwright_get_visible_text 或 playwright_get_visible_html 正常读取，无法确认的内容记录为未确认，不得用 JavaScript 绕过。
禁止提交新增、编辑、删除、审批、付款、发布、上传、下载等任何业务写操作。
禁止调用 playwright_close；浏览器会话由平台在本次探索结束时统一清理。
不得输出用户名、密码、Token、Cookie、HTML、截图 Base64 或完整 URL。
优先通过页面导航、打开菜单和打开表单解决 discovery_targets；不得因为探索前不知道字段、入口、提示或路径就要求用户回答。
只有完成只读探索后仍无法从页面证据确定的问题，才写入 unresolved_questions。
最终只输出一个 JSON 对象，字段必须是 start_url_path、visited_paths、page_states、elements、navigation_paths、step_evidence、unresolved_steps、unresolved_questions、warnings；不要输出 tool_stats、checkpoints 或任何计数，这些仅由平台 callback 记录。"""

_WRITE_ACTION_MARKERS = (
    '提交', '保存', '确认删除', '删除', '审批', '付款', '支付', '发布', '上传',
    '下载', 'submit', 'save', 'delete', 'approve', 'pay', 'publish', 'upload',
    'download',
)

READ_ONLY_DISABLED_TOOL_MESSAGES = {
    'playwright_evaluate': '只读探索不允许执行页面 JavaScript。请通过页面读取工具获取证据，无法确认的内容请记录为未确认。',
    'playwright_upload_file': '只读探索不允许上传文件。请在后续脚本执行阶段处理该操作。',
    'playwright_close': '只读探索不允许关闭浏览器。浏览器会话将在探索结束后由平台统一清理。',
}

EXPLORATION_TOTAL_MODEL_STEPS = MCP_MAX_STEPS


def exploration_total_timeout_seconds() -> float:
    """Use the established LLM timeout unless the WebUI override is configured."""
    raw_value = (
        os.getenv('WEBUI_EXPLORATION_TOTAL_TIMEOUT_SECONDS')
        or os.getenv('AITS_LLM_TIMEOUT_SECONDS')
        or '600'
    )
    try:
        return float(min(1800, max(60, int(raw_value))))
    except (TypeError, ValueError):
        logger.warning('Invalid exploration timeout configuration; using 600 seconds.')
        return 600.0


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Keep legacy budgets while blocking definite business-write actions."""

    def __init__(self, max_tool_calls: int = MCP_BROWSER_TOOL_CALL_LIMIT):
        super().__init__(max_tool_calls)
        self._safe_checkpoints: list[dict[str, Any]] = []
        self.model_call_count = 0

    def on_chat_model_start(self, serialized, messages, **kwargs):
        # Count actual model invocations across both exploration rounds. The
        # prompt never supplies this value and it is not derived from output.
        with self._lock:
            self.model_call_count += 1

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        tool_name = str((serialized or {}).get('name') or '').strip().lower()
        input_text = self._read_only_input_text(inputs, input_str)
        with self._lock:
            disabled_message = READ_ONLY_DISABLED_TOOL_MESSAGES.get(tool_name)
            if disabled_message is not None:
                self._raise_guard(
                    'read_only_violation',
                    disabled_message,
                    blocked_before_execution=True,
                    tool_name=tool_name,
                )
            if tool_name.endswith('_press_key') and any(
                key in input_text for key in ('enter', 'numpadenter')
            ):
                self._raise_guard(
                    'read_only_violation',
                    '只读探索不允许通过 Enter 提交表单，以防止新增或编辑数据被写入。',
                    blocked_before_execution=True,
                    tool_name=tool_name,
                )
            if tool_name.endswith(('_click', '_press_key')) and any(
                marker in input_text for marker in _WRITE_ACTION_MARKERS
            ):
                self._raise_guard(
                    'read_only_violation',
                    '只读探索检测到可能提交业务写操作，已终止以保护现有数据。',
                    blocked_before_execution=True,
                    tool_name=tool_name,
                )
        return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        super().on_tool_end(output, run_id=run_id, **kwargs)
        self._record_safe_checkpoint()

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        super().on_tool_error(error, run_id=run_id, **kwargs)
        self._record_safe_checkpoint()

    def _record_safe_checkpoint(self):
        """Persist only completed operation metadata, never input or output."""
        stats = self.get_stats()
        operation = stats.get('last_operation')
        if not isinstance(operation, dict) or operation.get('status') not in {'succeeded', 'failed'}:
            return
        checkpoint = {
            'tool_name': str(operation.get('tool_name') or 'browser_tool'),
            'call_index': int(operation.get('call_index') or 0),
            'status': str(operation['status']),
        }
        if checkpoint['call_index'] > 0 and checkpoint not in self._safe_checkpoints:
            self._safe_checkpoints.append(checkpoint)

    def safe_checkpoints(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._safe_checkpoints]

    @staticmethod
    def _read_only_input_text(inputs: Any, input_str: str) -> str:
        if isinstance(inputs, dict):
            try:
                return json.dumps(inputs, ensure_ascii=False).lower()
            except (TypeError, ValueError):
                return str(inputs).lower()
        return str(input_str or '').lower()


class MCPPageExplorerError(RuntimeError):
    """A safe explorer failure carrying failure-only tool statistics."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        tool_stats: dict[str, Any] | None = None,
        snapshot: ExplorationSnapshot | None = None,
    ):
        self.error_code = error_code
        self.tool_stats = dict(tool_stats or {})
        self.snapshot = snapshot
        super().__init__(message)


@contextmanager
def suppress_mcp_raw_query_logs():
    """Prevent mcp_use from writing its raw `Received query` payload at INFO."""
    mcp_loggers = [logging.getLogger(name) for name in ('mcp_use', 'mcpagent')]
    old_levels = [mcp_logger.level for mcp_logger in mcp_loggers]
    for mcp_logger in mcp_loggers:
        mcp_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for mcp_logger, old_level in zip(mcp_loggers, old_levels):
            mcp_logger.setLevel(old_level)


class MCPPageExplorer:
    """A small MCP wrapper that reuses guard/error semantics without old script generation."""

    def __init__(
        self,
        *,
        llm_model: Any,
        mcp_config: dict[str, Any],
        cancel_check: Callable[[], bool] | None = None,
        generation_id: str | None = None,
    ):
        self.llm_model = llm_model
        self.mcp_config = mcp_config
        self.cancel_check = cancel_check or (lambda: False)
        self.generation_id = generation_id
        self.output_generation_id = validate_generation_output_id(generation_id)
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        self._active_start_path = '/'

    async def explore(
        self,
        *,
        scenario,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationSnapshot:
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', 0)
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        return await self._explore_with_prompt(
            self._build_prompt(scenario, start_path, target_url_safe, temporary_credentials),
            start_path,
        )

    async def explore_missing_evidence(
        self,
        *,
        scenario,
        existing_snapshot: ExplorationSnapshot,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationSnapshot:
        """Perform one narrow, read-only evidence supplement, never a full rerun."""
        requested_steps = [
            step_id for step_id in existing_snapshot.unresolved_steps
            if step_id in {step.id for step in scenario.steps}
        ]
        if not requested_steps:
            requested_steps = [
                step.id for step in scenario.steps if step.id not in existing_snapshot.step_evidence
            ]
        if not requested_steps:
            return existing_snapshot
        prompt = self._build_supplemental_prompt(
            scenario, start_path, target_url_safe, requested_steps, temporary_credentials,
            existing_snapshot=existing_snapshot,
        )
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        return await self._explore_with_prompt(prompt, start_path)

    async def explore_until_complete(
        self,
        *,
        scenario,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationSnapshot:
        """Run primary plus at most one targeted supplement in one MCP session."""
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', 0)
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        self._active_start_path = start_path
        deadline = time.monotonic() + exploration_total_timeout_seconds()
        client = None
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    runtime_mcp_config = prepare_playwright_mcp_output_config(
                        self.mcp_config, self.output_generation_id,
                    )
                    client = MCPClient.from_dict(runtime_mcp_config)
                    await client.create_all_sessions()
                    snapshot = await self._explore_with_prompt(
                        self._build_prompt(scenario, start_path, target_url_safe, temporary_credentials),
                        start_path,
                        client=client,
                        max_steps=EXPLORATION_TOTAL_MODEL_STEPS,
                        deadline=deadline,
                    )
                    snapshot = assess_exploration_completion(scenario, snapshot)
                    if snapshot.completion.status != 'needs_targeted_exploration':
                        return snapshot
                    remaining_model_calls = EXPLORATION_TOTAL_MODEL_STEPS - self.guard.model_call_count
                    if (
                        self.guard.get_stats()['total_tool_calls'] >= MCP_BROWSER_TOOL_CALL_LIMIT
                        or time.monotonic() >= deadline
                        or remaining_model_calls <= 0
                    ):
                        return assess_exploration_completion(
                            scenario, snapshot, targeted_rounds=0, budget_exhausted=True,
                        )
                    requested_steps = [item.id for item in scenario.steps if item.id in snapshot.unresolved_steps]
                    # A discovery-target-only gap has no unresolved step yet.
                    # Revisit the known scenario steps in the same session so
                    # the page can expose the target through its navigation.
                    if not requested_steps:
                        requested_steps = [item.id for item in scenario.steps]
                    supplemental = await self._explore_with_prompt(
                        self._build_supplemental_prompt(
                            scenario, start_path, target_url_safe, requested_steps, temporary_credentials,
                            existing_snapshot=snapshot,
                        ),
                        start_path,
                        client=client,
                        max_steps=remaining_model_calls,
                        deadline=deadline,
                    )
                    supplemental = self._with_delta_tool_stats(
                        supplemental,
                        previous=snapshot.tool_stats,
                    )
                    from .generation_contracts import merge_exploration_snapshots
                    snapshot = merge_exploration_snapshots(
                        snapshot, supplemental, scenario=scenario, target_step_ids=set(requested_steps),
                    )
                    actual_budget_exhausted = (
                        self.guard.get_stats()['total_tool_calls'] >= MCP_BROWSER_TOOL_CALL_LIMIT
                        or self.guard.model_call_count >= EXPLORATION_TOTAL_MODEL_STEPS
                        or time.monotonic() >= deadline
                    )
                    return assess_exploration_completion(
                        scenario,
                        snapshot,
                        targeted_rounds=1,
                        budget_exhausted=actual_budget_exhausted,
                        supplement_round_limit_reached=not actual_budget_exhausted,
                    )
            except TimeoutError:
                raise self._failure(
                    'exploration_timeout', '只读探索已达到总时限，未继续重试。', 0,
                ) from None
        finally:
            if client is not None:
                try:
                    # Cleanup is bounded independently from the exploration
                    # deadline so an expired task budget cannot leak its
                    # browser process/session.
                    async with asyncio.timeout(10):
                        await client.close_all_sessions()
                except TimeoutError:
                    logger.warning('V2 MCP 会话清理达到独立清理时限')
                except Exception:
                    logger.warning('V2 MCP 会话清理失败', exc_info=True)

    async def _explore_with_prompt(
        self,
        prompt: str,
        start_path: str,
        *,
        client=None,
        max_steps: int = MCP_MAX_STEPS,
        deadline: float | None = None,
    ) -> ExplorationSnapshot:
        """Run one bounded prompt, optionally in a caller-owned browser session."""
        owns_client = client is None
        started_at = time.monotonic()
        self._active_start_path = start_path
        try:
            if client is None:
                runtime_mcp_config = prepare_playwright_mcp_output_config(
                    self.mcp_config, self.output_generation_id,
                )
                client = MCPClient.from_dict(runtime_mcp_config)
                await client.create_all_sessions()
            agent = MCPAgent(
                llm=self.llm_model,
                client=client,
                max_steps=max(1, max_steps),
                additional_instructions=EXPLORER_CONSTRAINTS,
                disallowed_tools=list(READ_ONLY_DISABLED_TOOL_MESSAGES),
                callbacks=[self.guard],
            )
            with suppress_mcp_raw_query_logs():
                output = await self._run_with_cancel(agent, prompt, deadline=deadline)
            return await self._parse_snapshot_with_repair(
                output, start_path, time.monotonic() - started_at, deadline=deadline,
            )
        except MCPPageExplorerError as exc:
            error = self._failure(exc.error_code, str(exc), time.monotonic() - started_at)
            self._log_failure(error)
            raise error
        except Exception as exc:
            terminal_error = self.guard.terminal_error
            if terminal_error is not None:
                error = self._failure(
                    terminal_error.error_kind, str(terminal_error), time.monotonic() - started_at,
                )
            else:
                error_kind = _classify_mcp_error(exc)
                error = self._failure(
                    error_kind, _get_mcp_error_message(exc), time.monotonic() - started_at,
                )
            self._log_failure(error)
            raise error from exc
        finally:
            if owns_client and client is not None:
                try:
                    await client.close_all_sessions()
                except Exception:
                    logger.warning('V2 MCP 会话清理失败', exc_info=True)

    @staticmethod
    def _with_delta_tool_stats(
        snapshot: ExplorationSnapshot,
        *,
        previous: ExplorationToolStats,
    ) -> ExplorationSnapshot:
        """Convert same-session cumulative counters into one round's delta."""
        current = snapshot.tool_stats
        counts = {
            name: max(0, count - previous.tool_counts.get(name, 0))
            for name, count in current.tool_counts.items()
        }
        payload = snapshot.model_dump(mode='json')
        payload['tool_stats'] = ExplorationToolStats(
            total_tool_calls=max(0, current.total_tool_calls - previous.total_tool_calls),
            tool_counts={name: count for name, count in counts.items() if count},
            failed_tool_calls=max(0, current.failed_tool_calls - previous.failed_tool_calls),
            termination_reason=current.termination_reason,
            duration_seconds=current.duration_seconds,
            model_calls=max(0, current.model_calls - previous.model_calls),
        ).model_dump(mode='json')
        return ExplorationSnapshot.model_validate(payload)

    def _failure(self, error_code: str, message: str, duration_seconds: float) -> MCPPageExplorerError:
        stats = self.guard.get_stats()
        safe_stats = {
            'total_tool_calls': stats['total_tool_calls'],
            'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'],
            'termination_reason': stats['termination_reason'] or error_code,
            'duration_seconds': round(max(0, duration_seconds), 3),
            'model_calls': self.guard.model_call_count,
        }
        if stats['last_operation'] is not None:
            safe_stats['last_operation'] = stats['last_operation']
        if stats['blocked_tool_calls']:
            safe_stats['blocked_tool_calls'] = stats['blocked_tool_calls']
        if stats['last_blocked_operation'] is not None:
            safe_stats['last_blocked_operation'] = stats['last_blocked_operation']
        snapshot = ExplorationSnapshot.model_validate({
            'start_url_path': self._active_start_path,
            'tool_stats': {
                key: safe_stats[key]
                for key in ('total_tool_calls', 'tool_counts', 'failed_tool_calls', 'termination_reason', 'duration_seconds', 'model_calls')
            },
            'checkpoints': self.guard.safe_checkpoints(),
        })
        return MCPPageExplorerError(error_code, message, tool_stats=safe_stats, snapshot=snapshot)

    def _guard_failure(self, duration_seconds: float) -> MCPPageExplorerError | None:
        terminal_error = self.guard.terminal_error
        if terminal_error is None:
            return None
        return self._failure(terminal_error.error_kind, str(terminal_error), duration_seconds)

    def _log_failure(self, error: MCPPageExplorerError):
        logger.warning(
            'V2 MCP exploration failed: generation_id=%s error_code=%s tool_stats=%s',
            self.generation_id or '<unknown>', error.error_code, error.tool_stats,
        )

    async def _cancel_and_await(self, run_task):
        if not run_task.done():
            run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run_with_cancel(self, agent, prompt: str, *, deadline: float | None = None) -> str:
        run_task = asyncio.create_task(self._initialize_and_run(agent, prompt))
        started_at = time.monotonic()
        try:
            while True:
                guard_failure = self._guard_failure(time.monotonic() - started_at)
                if guard_failure is not None:
                    await self._cancel_and_await(run_task)
                    raise guard_failure
                if self.cancel_check():
                    await self._cancel_and_await(run_task)
                    guard_failure = self._guard_failure(time.monotonic() - started_at)
                    if guard_failure is not None:
                        raise guard_failure
                    raise self._failure('TASK_CANCELLED', '用户已取消任务。', time.monotonic() - started_at)
                if deadline is not None and time.monotonic() >= deadline:
                    await self._cancel_and_await(run_task)
                    raise self._failure('exploration_timeout', '只读探索已达到总时限，未继续重试。', time.monotonic() - started_at)
                if run_task.done():
                    try:
                        output = await run_task
                    except Exception:
                        guard_failure = self._guard_failure(time.monotonic() - started_at)
                        if guard_failure is not None:
                            raise guard_failure
                        raise
                    guard_failure = self._guard_failure(time.monotonic() - started_at)
                    if guard_failure is not None:
                        raise guard_failure
                    return output
                await asyncio.wait({run_task}, timeout=0.25)
        except BaseException:
            await self._cancel_and_await(run_task)
            guard_failure = self._guard_failure(time.monotonic() - started_at)
            if guard_failure is not None:
                raise guard_failure
            raise

    async def _initialize_and_run(self, agent, prompt: str) -> str:
        # Sessions are owned by this Explorer so its finally block closes them
        # exactly once for normal, error, and cancellation paths. Keeping this
        # lifecycle in the monitored task also makes adapter initialization
        # cancellable before any browser tool can run.
        await agent.initialize()
        return await agent.run(prompt, manage_connector=False)

    def _build_prompt(
        self,
        scenario,
        start_path: str,
        target_url_safe: str,
        credentials: dict[str, str] | None,
    ) -> str:
        login_context = '无临时登录信息；如页面要求登录，请仅记录未确认项。'
        if credentials:
            login_context = (
                '仅在页面确实要求登录时使用以下本次临时信息，不得在输出中复述：'
                f"用户名={credentials['username']}，密码={credentials['password']}"
            )
        return json.dumps({
            'constraints': EXPLORER_CONSTRAINTS,
            'navigation_target_url': target_url_safe,
            'start_url_path': start_path,
            'scenario': scenario.model_dump(mode='json'),
            'output_schema': self._model_output_schema(),
            'discovery_targets': list(dict.fromkeys([
                *scenario.discovery_targets,
                *scenario.ambiguities,
            ])),
            'login_context': login_context,
            'instruction': '先自行探索并补齐页面可观察信息；只把探索后仍无法确定且必须由用户决策的问题放入 unresolved_questions。',
        }, ensure_ascii=False)

    def _build_supplemental_prompt(
        self,
        scenario,
        start_path: str,
        target_url_safe: str,
        step_ids: list[str],
        credentials: dict[str, str] | None,
        *,
        existing_snapshot: ExplorationSnapshot | None = None,
    ) -> str:
        steps = [step.model_dump(mode='json') for step in scenario.steps if step.id in step_ids]
        login_context = '无临时登录信息；如页面要求登录，请仅记录未确认项。'
        if credentials:
            login_context = (
                '仅在页面确实要求登录时使用以下本次临时信息，不得在输出中复述：'
                f"用户名={credentials['username']}，密码={credentials['password']}"
            )
        return json.dumps({
            'constraints': EXPLORER_CONSTRAINTS,
            'mode': 'directed_missing_evidence_only',
            'navigation_target_url': target_url_safe,
            'start_url_path': start_path,
            'requested_step_ids': step_ids,
            'requested_steps': steps,
            'missing_targets': [
                item.model_dump(mode='json')
                for item in existing_snapshot.completion.missing_targets
            ] if existing_snapshot else [],
            'already_observed_pages': [
                {'name': page.name, 'path': page.path, 'key_regions': page.key_regions}
                for page in existing_snapshot.page_states
            ] if existing_snapshot else [],
            'output_schema': self._model_output_schema(),
            'discovery_targets': list(dict.fromkeys([
                *scenario.discovery_targets,
                *scenario.ambiguities,
            ])),
            'login_context': login_context,
            'instruction': '只补充 missing_targets 和 requested_step_ids 的页面证据；先观察当前浏览器页面，不要无故重新登录或重新探索完整流程。不要执行写操作。',
        }, ensure_ascii=False)

    @staticmethod
    def _model_output_schema() -> dict[str, Any]:
        """Hide callback-owned fields so a model never fabricates counters."""
        schema = ExplorationSnapshot.model_json_schema()
        properties = schema.get('properties') or {}
        for field in ('tool_stats', 'checkpoints'):
            properties.pop(field, None)
        schema['required'] = [
            field for field in schema.get('required', [])
            if field not in {'tool_stats', 'checkpoints'}
        ]
        return schema

    def _check_output_active(self, duration_seconds: float):
        guard_failure = self._guard_failure(duration_seconds)
        if guard_failure is not None:
            raise guard_failure
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', duration_seconds)

    def _log_output(self, raw_output, *, stage: str, kind: str, repaired: bool, offset=None, diagnostics=()):
        output_type, length = output_summary(raw_output)
        logger.info(
            'V2 MCP output: generation_id=%s stage=%s type=%s length=%s failure_kind=%s json_offset=%s repair_attempted=%s contract_diagnostics=%s',
            self.generation_id or '<unknown>', stage, output_type, length, kind, offset, repaired, diagnostics,
        )

    def _output_failure(self, error: ExplorationOutputError, duration_seconds: float):
        if error.kind == 'script':
            return self._failure('SCRIPT_FORMAT_INVALID', 'MCP 探索器返回了脚本而非结构化页面证据。', duration_seconds)
        if error.kind == 'empty':
            message = 'MCP 未返回页面探索证据（空输出）。'
        elif error.kind in {'invalid_format', 'invalid_wrapper'}:
            message = 'MCP 页面探索证据格式错误，无法安全解析。'
        else:
            message = 'MCP 页面探索证据结构错误或证据不足，无法安全使用。'
        if error.diagnostics:
            diagnostic = error.diagnostics[0]
            message += f" 字段：{diagnostic['path']}；类型：{diagnostic['type']}。"
        return self._failure('EVIDENCE_INSUFFICIENT', message, duration_seconds)

    def _snapshot_from_payload(self, payload: dict, start_path: str, duration_seconds: float) -> ExplorationSnapshot:
        payload = dict(payload)
        stats = self.guard.get_stats()
        payload['start_url_path'] = start_path
        payload['tool_stats'] = {
            'total_tool_calls': stats['total_tool_calls'],
            'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'],
            'termination_reason': stats['termination_reason'],
            'duration_seconds': round(duration_seconds, 3),
            'model_calls': self.guard.model_call_count,
        }
        payload['checkpoints'] = self.guard.safe_checkpoints()
        try:
            return parse_exploration_snapshot_json(json.dumps(redact_exploration_metadata(payload), ensure_ascii=False))
        except GenerationContractError as exc:
            raise ExplorationOutputError('schema_invalid', diagnostics=exc.diagnostics) from None
        except (ValueError, TypeError, RecursionError):
            raise ExplorationOutputError('schema_invalid') from None

    def _parse_snapshot(self, raw_output: str, start_path: str, duration_seconds: float) -> ExplorationSnapshot:
        """Decode once, with at most one deterministic punctuation recovery."""
        self._check_output_active(duration_seconds)
        stage = 'original'
        repaired = False
        diagnostic_output = raw_output
        try:
            try:
                payload = parse_exploration_output(raw_output)
            except ExplorationOutputError as exc:
                if exc.repair_payload is None:
                    raise
                self._check_output_active(duration_seconds)
                self._log_output(raw_output, stage='original', kind=exc.kind, offset=exc.offset, repaired=True)
                stage, repaired = 'repaired', True
                # The bounded lexer has already proved completeness and preserved
                # every value/container. No model or second browser run is needed.
                payload = exc.repair_payload
                diagnostic_output = json.dumps(payload, ensure_ascii=False)
            snapshot = self._snapshot_from_payload(payload, start_path, duration_seconds)
            if repaired and not snapshot.step_evidence:
                raise ExplorationOutputError('missing_evidence')
            self._check_output_active(duration_seconds)
        except ExplorationOutputError as exc:
            self._check_output_active(duration_seconds)
            self._log_output(
                diagnostic_output, stage=stage, kind=exc.kind, offset=exc.offset,
                repaired=repaired, diagnostics=exc.diagnostics,
            )
            raise self._output_failure(exc, duration_seconds) from None
        except MCPPageExplorerError:
            self._log_output(diagnostic_output, stage=stage, kind='interrupted', repaired=repaired)
            raise
        self._log_output(diagnostic_output, stage=stage, kind='none', repaired=repaired)
        return snapshot

    async def _parse_snapshot_with_repair(
        self,
        raw_output,
        start_path: str,
        duration_seconds: float,
        *,
        deadline: float | None = None,
    ) -> ExplorationSnapshot:
        """Bound local format recovery by the same task-wide deadline."""
        if deadline is None:
            return self._parse_snapshot(raw_output, start_path, duration_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._failure('exploration_timeout', '只读探索已达到总时限，未继续重试。', duration_seconds)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._parse_snapshot, raw_output, start_path, duration_seconds),
                timeout=remaining,
            )
        except TimeoutError:
            raise self._failure('exploration_timeout', '只读探索已达到总时限，未继续重试。', duration_seconds) from None
