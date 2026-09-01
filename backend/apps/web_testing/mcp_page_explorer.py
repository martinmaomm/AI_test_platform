"""Target-driven Playwright MCP exploration for the V2 generation pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from django.conf import settings

from ai_core.webui_playwright_agent import (
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_MAX_STEPS,
    MCPBrowserToolGuard,
    _classify_mcp_error,
    _get_mcp_error_message,
)
from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from mcp_use import MCPClient

from .exploration_trace import (
    ExplorationTrace,
    ExplorationTraceRecorder,
    assess_trace_coverage,
)
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_policy import ExplorationPolicy
from .generation_preflight import (
    prepare_playwright_mcp_output_config,
    validate_generation_output_id,
)

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你只负责目标驱动的页面探索，绝对不要生成 Python、JavaScript 或测试脚本。
所有 playwright_navigate 调用必须传 JSON 布尔值 `headless: true`。
浏览器工具总数最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。
允许打开页面、读取可见文本、打开菜单/Tab/查询条件/表单弹窗；仅在 scope_policy 允许的 CRUD 目标内执行写操作；如提供登录信息，只能用于本次登录。
切换页面或打开、关闭弹窗后，使用 playwright_get_visible_text 或 playwright_get_visible_html 确认新的页面状态；不要在未观察到变化时反复执行相同交互。
工具可用性以当前探索能力为准：通过可见页面工具获取证据，无法确认的内容记录为未确认，不得用 JavaScript 绕过。
不得执行审批、付款、发布、下载或未授权文件操作；不得超出 scope_policy 的 CRUD 操作或数据范围。
禁止调用 playwright_close；浏览器会话由平台在本次探索结束时统一清理。
不得输出用户名、密码、Token、Cookie、HTML、截图 Base64 或完整 URL。
优先通过页面导航、打开菜单和打开表单解决 discovery_targets；不得因为探索前不知道字段、入口、提示或路径就要求用户回答。
只有完成页面探索后仍无法从页面证据确定的问题，才写入 unresolved_questions。
每次实际提交后必须先观察结果；结果未知时记录 unknown，不得盲目重试。完成目标后必须尝试 cleanup，并如实报告 residual 或 unknown。
最终回复只需简短说明已停止探索；普通文本、空文本或 Markdown 都可以。平台会从真实 Playwright 工具调用自动记录探索轨迹，绝不能在最终回复复述页面内容、定位器、凭据、Token、Cookie、HTML、截图 Base64 或完整 URL。"""

_WRITE_ACTION_MARKERS = (
    '提交', '保存', '确认删除', '删除', '审批', '付款', '支付', '发布', '上传',
    '下载', 'submit', 'save', 'delete', 'approve', 'pay', 'publish', 'upload',
    'download',
)
_EXTRA_RISK_ACTION_MARKERS = (
    '审批', '付款', '支付', '发布', '上传', '下载',
    'approve', 'pay', 'publish', 'upload', 'download',
)

READ_ONLY_DISABLED_TOOL_MESSAGES = {
    'playwright_evaluate': '页面探索不允许执行页面 JavaScript。请通过页面读取工具获取证据，无法确认的内容请记录为未确认。',
    'playwright_upload_file': '页面探索不允许上传文件。请在后续脚本执行阶段处理该操作。',
    'playwright_close': '页面探索不允许关闭浏览器。浏览器会话将在探索结束后由平台统一清理。',
}

EXPLORATION_TOTAL_MODEL_STEPS = MCP_MAX_STEPS


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Keep budgets and static tool bans while enforcing explicit write scope.

    The historical name remains import-compatible.  A browser tool only sees
    selector/input metadata, so this guard blocks recognisable forbidden
    submits and records only submit tools that enter execution; page-specific row ownership remains
    an instruction-and-ledger obligation rather than a claimed sandbox.
    """

    def __init__(
        self,
        max_tool_calls: int = MCP_BROWSER_TOOL_CALL_LIMIT,
        *,
        policy: ExplorationPolicy | None = None,
        trace_recorder: ExplorationTraceRecorder | None = None,
    ):
        super().__init__(max_tool_calls)
        self._legacy_read_only = policy is None
        self.policy = policy or ExplorationPolicy.read_only()
        self._safe_checkpoints: list[dict[str, Any]] = []
        self._potential_write_tool_calls = 0
        self._blocked_write_tool_calls = 0
        self._potential_write_runs: dict[Any, tuple[str, str]] = {}
        self._successful_write_operations: list[str] = []
        self.model_call_count = 0
        self.trace_recorder = trace_recorder or ExplorationTraceRecorder()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        # Count actual model invocations across both exploration rounds. The
        # prompt never supplies this value and it is not derived from output.
        with self._lock:
            self.model_call_count += 1

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        run_id = kwargs.get('run_id')
        self.trace_recorder.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        tool_name = str((serialized or {}).get('name') or '').strip().lower()
        input_text = self._read_only_input_text(inputs, input_str)
        is_login_submission = self._is_login_submission(tool_name, inputs, input_str)
        try:
            with self._lock:
                disabled_message = READ_ONLY_DISABLED_TOOL_MESSAGES.get(tool_name)
                if disabled_message is not None:
                    self._raise_guard(
                        'read_only_violation', disabled_message,
                        blocked_before_execution=True, tool_name=tool_name,
                    )
                is_enter = tool_name.endswith('_press_key') and any(
                    key in input_text for key in ('enter', 'numpadenter')
                )
                operation = self.policy.operation_from_tool_input(input_text)
                if tool_name.endswith(('_click', '_press_key')) and any(
                    marker in input_text for marker in _EXTRA_RISK_ACTION_MARKERS
                ):
                    self._blocked_write_tool_calls += 1
                    self._raise_guard(
                        'extra_risk_action', '探索阶段不允许执行审批、付款、发布、上传或下载等额外风险操作。',
                        blocked_before_execution=True, tool_name=tool_name,
                    )
                is_recognisable_submit = not is_login_submission and (is_enter or (
                    tool_name.endswith(('_click', '_press_key')) and any(
                        marker in input_text for marker in _WRITE_ACTION_MARKERS
                    )
                ))
                if is_recognisable_submit and not self.policy.allows(operation):
                    self._blocked_write_tool_calls += 1
                    if self._legacy_read_only:
                        message = (
                            '只读探索不允许通过 Enter 提交表单，以防止新增或编辑数据被写入。'
                            if is_enter else '只读探索检测到可能提交业务写操作，已终止以保护现有数据。'
                        )
                        error_kind = 'read_only_violation'
                    else:
                        message = '探索策略不允许该提交操作：请遵守用户的只读/禁止动作约束或声明的 CRUD 目标。'
                        error_kind = 'write_scope_violation'
                    self._raise_guard(
                        error_kind, message, blocked_before_execution=True, tool_name=tool_name,
                    )
            result = super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)
        except BaseException as exc:
            self.trace_recorder.mark_blocked(serialized, input_str, run_id=run_id, inputs=inputs, error=exc)
            raise
        if is_recognisable_submit:
            with self._lock:
                self._potential_write_tool_calls += 1
                self._potential_write_runs[kwargs.get('run_id')] = (tool_name, operation or 'unknown')
        return result

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_end(output, run_id=run_id)
        super().on_tool_end(output, run_id=run_id, **kwargs)
        self._record_safe_checkpoint()
        with self._lock:
            write_run = self._potential_write_runs.pop(run_id, None)
            tool_name, operation = write_run or ('', '')
            if tool_name and not self._is_failed_output(output):
                self._successful_write_operations.append(operation)
            if tool_name and self._is_failed_output(output) and self._terminal_error is None:
                self._raise_guard(
                    'write_result_unknown',
                    '可能提交的数据操作未获得可确认结果，已终止本轮探索以避免盲目重试。',
                    tool_name=tool_name,
                )

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_error(error, run_id=run_id)
        super().on_tool_error(error, run_id=run_id, **kwargs)
        self._record_safe_checkpoint()
        with self._lock:
            write_run = self._potential_write_runs.pop(run_id, None)
            tool_name = write_run[0] if write_run else ''
            if tool_name and self._terminal_error is None:
                self._raise_guard(
                    'write_result_unknown',
                    '可能提交的数据操作发生工具错误，已终止本轮探索以避免盲目重试。',
                    tool_name=tool_name,
                )

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

    def get_stats(self):
        stats = super().get_stats()
        stats['potential_write_tool_calls'] = self._potential_write_tool_calls
        stats['blocked_write_tool_calls'] = self._blocked_write_tool_calls
        stats['successful_write_operations'] = list(self._successful_write_operations)
        return stats

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
        snapshot: ExplorationTrace | None = None,
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
        user_constraints: str | None = None,
        exploration_timeout_seconds: float | None = None,
    ):
        self.llm_model = llm_model
        self.mcp_config = mcp_config
        self.cancel_check = cancel_check or (lambda: False)
        self.generation_id = generation_id
        self.user_constraints = str(user_constraints or '')
        self.exploration_timeout_seconds = (
            exploration_total_timeout_seconds()
            if exploration_timeout_seconds is None
            else float(exploration_timeout_seconds)
        )
        self.output_generation_id = validate_generation_output_id(generation_id)
        self.policy = ExplorationPolicy.read_only()
        self.trace_recorder = ExplorationTraceRecorder()
        self.guard = ReadOnlyMCPBrowserToolGuard(
            MCP_BROWSER_TOOL_CALL_LIMIT, policy=self.policy, trace_recorder=self.trace_recorder,
        )
        self._active_start_path = '/'

    def _configure_policy(
        self,
        scenario,
        *,
        temporary_credentials: dict[str, str] | None = None,
    ) -> None:
        self.policy = ExplorationPolicy.for_scenario(
            scenario,
            generation_id=self.output_generation_id,
            user_constraints=self.user_constraints,
        )
        trace_file = None
        if self.generation_id and self.output_generation_id:
            trace_file = (
                Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp'
                / f'{self.output_generation_id}.trace.jsonl'
            )
        sensitive_values = tuple(str(value) for value in (temporary_credentials or {}).values() if value)
        self.trace_recorder = ExplorationTraceRecorder(
            self._active_start_path,
            sensitive_values=sensitive_values,
            trace_file=trace_file,
        )
        self.guard = ReadOnlyMCPBrowserToolGuard(
            MCP_BROWSER_TOOL_CALL_LIMIT, policy=self.policy, trace_recorder=self.trace_recorder,
        )

    async def explore(
        self,
        *,
        scenario,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationTrace:
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', 0)
        self._active_start_path = start_path
        self._configure_policy(scenario, temporary_credentials=temporary_credentials)
        return await self._explore_with_prompt(
            self._build_prompt(scenario, start_path, target_url_safe, temporary_credentials),
            start_path,
        )

    async def explore_until_complete(
        self,
        *,
        scenario,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationTrace:
        """Run one callback-recorded exploration session.

        A model's final response is intentionally ignored.  Partial but useful
        traces are returned to the orchestrator; hard browser/security failures
        still surface as ``MCPPageExplorerError`` with the trace attached.
        """
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', 0)
        self._active_start_path = start_path
        self._configure_policy(scenario, temporary_credentials=temporary_credentials)
        deadline = time.monotonic() + self.exploration_timeout_seconds
        client = None
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    runtime_mcp_config = prepare_playwright_mcp_output_config(
                        self.mcp_config, self.output_generation_id,
                    )
                    client = MCPClient.from_dict(runtime_mcp_config)
                    await client.create_all_sessions()
                    trace = await self._explore_with_prompt(
                        self._build_prompt(scenario, start_path, target_url_safe, temporary_credentials),
                        start_path,
                        client=client,
                        max_steps=EXPLORATION_TOTAL_MODEL_STEPS,
                        deadline=deadline,
                    )
                    return assess_trace_coverage(scenario, trace)
            except TimeoutError:
                error = self._failure(
                    'exploration_timeout', '页面探索已达到总时限，未继续重试。', 0,
                )
                raise error from None
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
    ) -> ExplorationTrace:
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
                await self._run_with_cancel(agent, prompt, deadline=deadline)
            return self._trace_snapshot(start_path, time.monotonic() - started_at)
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

    def _trace_snapshot(self, start_path: str, duration_seconds: float, *, termination_reason: str = '') -> ExplorationTrace:
        stats = self.guard.get_stats()
        payload = {
            'total_tool_calls': stats['total_tool_calls'],
            'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'],
            'termination_reason': termination_reason or stats['termination_reason'] or '',
            'duration_seconds': round(max(0, duration_seconds), 3),
            'model_calls': self.guard.model_call_count,
            'potential_write_tool_calls': stats['potential_write_tool_calls'],
            'blocked_write_tool_calls': stats['blocked_write_tool_calls'],
        }
        cleanup_status = (
            'cleaned' if 'delete' in stats['successful_write_operations']
            else ('unknown' if stats['potential_write_tool_calls'] else 'not_required')
        )
        cleanup = {
            'status': cleanup_status,
            'attempted': 'delete' in stats['successful_write_operations'],
            'residuals': [],
            'reason': (
                '' if not stats['potential_write_tool_calls'] or 'delete' in stats['successful_write_operations']
                else '探索在可能写入后结束，无法确认清理结果。'
            ),
        }
        return self.trace_recorder.build(
            tool_stats=payload, termination_reason=payload['termination_reason'], cleanup=cleanup,
            warnings=['探索在可能写入后结束，必须人工确认残留。'] if cleanup_status == 'unknown' else [],
        ).model_copy(update={'start_path': start_path})

    def _failure(self, error_code: str, message: str, duration_seconds: float) -> MCPPageExplorerError:
        stats = self.guard.get_stats()
        safe_stats = {
            'total_tool_calls': stats['total_tool_calls'],
            'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'],
            'termination_reason': stats['termination_reason'] or error_code,
            'duration_seconds': round(max(0, duration_seconds), 3),
            'model_calls': self.guard.model_call_count,
            'potential_write_tool_calls': stats['potential_write_tool_calls'],
            'blocked_write_tool_calls': stats['blocked_write_tool_calls'],
        }
        if stats['last_operation'] is not None:
            safe_stats['last_operation'] = stats['last_operation']
        if stats['blocked_tool_calls']:
            safe_stats['blocked_tool_calls'] = stats['blocked_tool_calls']
        if stats['last_blocked_operation'] is not None:
            safe_stats['last_blocked_operation'] = stats['last_blocked_operation']
        trace = self._trace_snapshot(self._active_start_path, duration_seconds, termination_reason=error_code)
        return MCPPageExplorerError(error_code, message, tool_stats=safe_stats, snapshot=trace)

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
                    raise self._failure('exploration_timeout', '页面探索已达到总时限，未继续重试。', time.monotonic() - started_at)
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
            'discovery_targets': list(dict.fromkeys([
                *scenario.discovery_targets,
                *scenario.ambiguities,
            ])),
            'login_context': login_context,
            'scope_policy': self.policy.prompt_scope(),
            'instruction': '先自行探索并补齐页面可观察信息。仅在 scope_policy 允许时执行目标 CRUD；平台会自动记录工具调用，最终回复不需要输出 JSON、页面证据或脚本。',
        }, ensure_ascii=False)
