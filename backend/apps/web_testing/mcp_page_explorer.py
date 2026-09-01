"""Same-session, Goal-scoped Playwright MCP exploration for v3."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from django.conf import settings
from mcp_use import MCPClient

from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from ai_core.webui_playwright_agent import MCP_BROWSER_TOOL_CALL_LIMIT, MCP_MAX_STEPS, MCPBrowserToolGuard, _classify_mcp_error, _get_mcp_error_message

from .exploration_policy import ExplorationPolicy
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import ExplorationTrace, ExplorationTraceRecorder, evaluate_goal_events
from .generation_contracts import Goal, GoalPlan
from .generation_preflight import prepare_playwright_mcp_output_config, validate_generation_output_id

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你只负责当前 Goal 的页面探索，绝不生成 Python、JavaScript 或测试脚本。
所有 playwright_navigate 调用必须传 JSON 布尔值 headless: true。浏览器工具最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。
平台在 callback 发生时绑定当前 Goal；最终文本不提供事件、选择器、HTML 或脚本。每个 Goal 结束前必须进行新的页面观察。
不允许审批、付款、发布、上传、下载或未授权外部操作。只在 active_goal_may_write=true 的当前 Goal 内操作本轮 namespace 测试数据；结果未知时停止，不得重试。
runtime_input_values 是平台为当前 Goal 提供的唯一输入值映射。仅在实际需要填充或选择时原样使用其中的值；不得猜测 ref、不得改写值、不得在最终文本中复述这些值。
当前 Goal 若包含 verification，结束前必须调用带具体 selector 的 playwright_get_visible_html 作为验证 probe。contains_ref/not_contains_ref 必须以该 selector 的原始工具输出中对应 runtime ref 值存在/不存在为依据；不能以 playwright_get_visible_text 替代。
不得输出用户名、密码、Token、Cookie、完整 URL、截图 Base64 或 HTML。"""

READ_ONLY_DISABLED_TOOL_MESSAGES = {
    'playwright_evaluate': '页面探索不允许执行页面 JavaScript。',
    'playwright_upload_file': '页面探索不允许上传文件。',
    'playwright_close': '页面探索不允许关闭浏览器。',
}
_HIGH_RISK_MARKERS = ('审批', '付款', '支付', '发布', '上传', '下载', 'approve', 'pay', 'publish', 'upload', 'download')
_RUNTIME_REF_TOKEN_RE = re.compile(r'[^a-zA-Z0-9_-]+')


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Budget/safety guard that attributes potential writes from Goal metadata."""
    def __init__(self, max_tool_calls: int = MCP_BROWSER_TOOL_CALL_LIMIT, *, policy: ExplorationPolicy | None = None, trace_recorder: ExplorationTraceRecorder | None = None):
        super().__init__(max_tool_calls)
        self.policy = policy or ExplorationPolicy.read_only()
        self.trace_recorder = trace_recorder or ExplorationTraceRecorder()
        self.model_call_count = 0
        self._potential_write_tool_calls = 0
        self._blocked_write_tool_calls = 0
        self._possible_write_runs: set[Any] = set()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        with self._lock:
            self.model_call_count += 1

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        run_id = kwargs.get('run_id')
        self.trace_recorder.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        tool_name = str((serialized or {}).get('name') or '').lower()
        text = json.dumps(inputs, ensure_ascii=False).lower() if isinstance(inputs, dict) else str(input_str or '').lower()
        try:
            with self._lock:
                if tool_name in READ_ONLY_DISABLED_TOOL_MESSAGES:
                    self._blocked_write_tool_calls += 1
                    self._raise_guard('read_only_violation', READ_ONLY_DISABLED_TOOL_MESSAGES[tool_name], blocked_before_execution=True, tool_name=tool_name)
                if tool_name.endswith(('_click', '_press_key')) and any(marker in text for marker in _HIGH_RISK_MARKERS):
                    self._blocked_write_tool_calls += 1
                    self._raise_guard('extra_risk_action', '探索阶段不允许审批、付款、发布、上传或下载等高风险操作。', blocked_before_execution=True, tool_name=tool_name)
                # A generic click is potentially writing only because the
                # active Goal declared test_data/cleanup semantics, never
                # because a page label happened to contain a CRUD word.
                if tool_name.endswith(('_click', '_press_key')):
                    if self.policy.current_goal_may_write():
                        self._potential_write_tool_calls += 1
                        self._possible_write_runs.add(run_id)
                    elif self.policy.explicit_read_only and ('enter' in text or 'submit' in text):
                        self._blocked_write_tool_calls += 1
                        self._raise_guard('read_only_violation', '当前 Goal 是观察性目标，禁止可能提交表单的操作。', blocked_before_execution=True, tool_name=tool_name)
            return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)
        except BaseException as exc:
            self.trace_recorder.mark_blocked(serialized, input_str, run_id=run_id, inputs=inputs, error=exc)
            raise

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_end(output, run_id=run_id)
        super().on_tool_end(output, run_id=run_id, **kwargs)
        if run_id in self._possible_write_runs and self._is_failed_output(output) and self._terminal_error is None:
            self._raise_guard('write_result_unknown', '可能写入未获得可确认结果，已停止以避免重复操作。')

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_error(error, run_id=run_id)
        super().on_tool_error(error, run_id=run_id, **kwargs)
        if run_id in self._possible_write_runs and self._terminal_error is None:
            self._raise_guard('write_result_unknown', '可能写入发生工具错误，已停止以避免重复操作。')

    def get_stats(self):
        stats = super().get_stats()
        stats.update({'potential_write_tool_calls': self._potential_write_tool_calls, 'blocked_write_tool_calls': self._blocked_write_tool_calls})
        return stats


class MCPPageExplorerError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, tool_stats: dict[str, Any] | None = None, snapshot: ExplorationTrace | None = None):
        self.error_code = error_code
        self.tool_stats = dict(tool_stats or {})
        self.snapshot = snapshot
        super().__init__(message)


@contextmanager
def suppress_mcp_raw_query_logs():
    loggers = [logging.getLogger(name) for name in ('mcp_use', 'mcpagent')]
    levels = [item.level for item in loggers]
    for item in loggers:
        item.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for item, level in zip(loggers, levels):
            item.setLevel(level)


class MCPPageExplorer:
    def __init__(self, *, llm_model: Any, mcp_config: dict[str, Any], cancel_check: Callable[[], bool] | None = None, generation_id: str | None = None, user_constraints: str | None = None, exploration_timeout_seconds: float | None = None):
        self.llm_model = llm_model
        self.mcp_config = mcp_config
        self.cancel_check = cancel_check or (lambda: False)
        self.generation_id = generation_id
        self.user_constraints = str(user_constraints or '')
        self.exploration_timeout_seconds = exploration_total_timeout_seconds() if exploration_timeout_seconds is None else float(exploration_timeout_seconds)
        self.output_generation_id = validate_generation_output_id(generation_id)
        self.policy = ExplorationPolicy.read_only()
        self.trace_recorder = ExplorationTraceRecorder()
        self.guard = ReadOnlyMCPBrowserToolGuard(policy=self.policy, trace_recorder=self.trace_recorder)
        self._plan_runtime_values: dict[str, str] = {}
        self._plan_input_sources: dict[str, str] = {}

    def _configure(self, plan: GoalPlan, start_path: str, credentials: dict[str, str] | None):
        self.policy = ExplorationPolicy.for_plan(plan, generation_id=self.output_generation_id, user_constraints=self.user_constraints)
        trace_file = None
        if self.output_generation_id:
            trace_file = Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp' / f'{self.output_generation_id}.trace.jsonl'
        self.trace_recorder = ExplorationTraceRecorder(start_path, sensitive_values=tuple(str(value) for value in (credentials or {}).values() if value), trace_file=trace_file, runtime_namespace=self.policy.namespace)
        self.guard = ReadOnlyMCPBrowserToolGuard(policy=self.policy, trace_recorder=self.trace_recorder)
        self._plan_runtime_values = {}
        self._plan_input_sources = plan.input_sources()
        for goal in plan.goals:
            for spec in goal.input_refs:
                if spec.name in self._plan_runtime_values:
                    continue
                if spec.source == 'credential':
                    if credentials and credentials.get(spec.credential_slot):
                        self._plan_runtime_values[spec.name] = str(credentials[spec.credential_slot])
                    continue
                safe_ref = _RUNTIME_REF_TOKEN_RE.sub('-', spec.name).strip('-')[:48] or 'value'
                self._plan_runtime_values[spec.name] = f'{self.policy.namespace}-{safe_ref}-{secrets.token_hex(8)}'

    def _runtime_values_for_goal(self, goal: Goal, credentials: dict[str, str] | None) -> tuple[dict[str, str], dict[str, str]]:
        """Build an in-memory-only, value-unique map for one Goal execution."""
        values: dict[str, str] = {}
        sources: dict[str, str] = {}
        for spec in goal.input_refs:
            ref = spec.name
            sources[ref] = spec.source
            if ref in self._plan_runtime_values:
                values[ref] = self._plan_runtime_values[ref]
        return values, sources

    async def explore_until_complete(self, *, plan: GoalPlan, start_path: str, target_url_safe: str, temporary_credentials: dict[str, str] | None = None) -> ExplorationTrace:
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。')
        self._configure(plan, start_path, temporary_credentials)
        started = time.monotonic()
        deadline = started + self.exploration_timeout_seconds
        client = None
        try:
            runtime_config = prepare_playwright_mcp_output_config(self.mcp_config, self.output_generation_id)
            client = MCPClient.from_dict(runtime_config)
            await client.create_all_sessions()
            for goal in plan.goals:
                await self._run_goal(client, plan, goal, start_path, target_url_safe, temporary_credentials, deadline, supplement=False)
                run = evaluate_goal_events(goal, self.trace_recorder.events)
                self.trace_recorder.record_goal_run(run)
                if run.status == 'uncertain':
                    await self._run_goal(client, plan, goal, start_path, target_url_safe, temporary_credentials, deadline, supplement=True)
                    self.trace_recorder.record_goal_run(evaluate_goal_events(goal, self.trace_recorder.events))
                current = next(item for item in self.trace_recorder.build(tool_stats={}).goal_runs if item.goal_id == goal.id)
                if current.status != 'completed':
                    break
            return self._snapshot(start_path, time.monotonic() - started)
        except MCPPageExplorerError:
            raise
        except Exception as exc:
            raise self._failure(_classify_mcp_error(exc), _get_mcp_error_message(exc), start_path) from exc
        finally:
            if client is not None:
                try:
                    async with asyncio.timeout(10):
                        await client.close_all_sessions()
                except Exception:
                    logger.warning('v3 MCP 会话清理失败', exc_info=True)

    async def _run_goal(self, client, plan: GoalPlan, goal: Goal, start_path: str, target_url_safe: str, credentials: dict[str, str] | None, deadline: float, *, supplement: bool):
        if self.cancel_check():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。', start_path)
        if time.monotonic() >= deadline:
            raise self._failure('exploration_timeout', '页面探索已达到总时限。', start_path)
        self.policy.set_active_goal(goal.id)
        runtime_values, runtime_sources = self._runtime_values_for_goal(goal, credentials)
        self.trace_recorder.set_active_goal(goal.id, runtime_values, runtime_sources, goal.verification.model_dump(mode='json') if goal.verification else None)
        remaining_model_steps = MCP_MAX_STEPS - self.guard.model_call_count
        if remaining_model_steps <= 0:
            raise self._failure(
                'MODEL_STEP_BUDGET', '页面探索已用完模型总步数，未继续额外调用。', start_path,
            )
        agent = MCPAgent(llm=self.llm_model, client=client, max_steps=remaining_model_steps, additional_instructions=EXPLORER_CONSTRAINTS, disallowed_tools=list(READ_ONLY_DISABLED_TOOL_MESSAGES), callbacks=[self.guard])
        prompt = json.dumps({
            'goal': goal.model_dump(mode='json'),
            'completed_goal_runs': [item.model_dump(mode='json') for item in self.trace_recorder.build(tool_stats={}).goal_runs],
            'current_relative_path': self.trace_recorder.build(tool_stats={}).last_location,
            'remaining_model_steps': remaining_model_steps,
            'navigation_target_url': target_url_safe,
            'start_path': start_path,
            'login_context': '当前 Goal 已声明 credential refs；仅在页面确实要求登录时使用 runtime_input_values 中的对应值。' if any(spec.source == 'credential' for spec in goal.input_refs) else '当前 Goal 未声明 credential ref。',
            'runtime_input_values': runtime_values,
            'verification_probe': goal.verification.model_dump(mode='json') if goal.verification else None,
            'scope_policy': self.policy.prompt_scope(),
            'instruction': (
                '这是一次有界补探，只补当前 Goal 的 locator-backed HTML verification probe。'
                if supplement and goal.verification else
                '这是一次有界补探，只补当前 Goal 的可观察完成标准。'
                if supplement else
                '只执行当前 Goal；结束前必须完成带 selector 的 playwright_get_visible_html verification probe。'
                if goal.verification else
                '只执行当前 Goal；结束前必须产生新的页面观察。'
            ),
        }, ensure_ascii=False)
        init_task = asyncio.create_task(agent.initialize())
        # Let connector initialization enter its cancellable await before the
        # first cancellation poll. Otherwise a task cancelled before scheduling
        # never gives the connector a chance to release its partial state.
        await asyncio.sleep(0)
        while not init_task.done():
            if self.cancel_check():
                init_task.cancel()
                try:
                    await init_task
                except asyncio.CancelledError:
                    pass
                raise self._failure('TASK_CANCELLED', '用户已取消任务。', start_path)
            if time.monotonic() >= deadline:
                init_task.cancel()
                try:
                    await init_task
                except asyncio.CancelledError:
                    pass
                raise self._failure('exploration_timeout', '页面探索已达到总时限。', start_path)
            await asyncio.wait({init_task}, timeout=0.25)
        await init_task
        with suppress_mcp_raw_query_logs():
            run_task = asyncio.create_task(agent.run(prompt, manage_connector=False))
            while not run_task.done():
                if self.cancel_check():
                    await self._cancel_task(run_task)
                    raise self._failure('TASK_CANCELLED', '用户已取消任务。', start_path)
                if time.monotonic() >= deadline:
                    await self._cancel_task(run_task)
                    raise self._failure('exploration_timeout', '页面探索已达到总时限。', start_path)
                if self.guard.terminal_error is not None:
                    await self._cancel_task(run_task)
                    raise self._failure(self.guard.terminal_error.error_kind, str(self.guard.terminal_error), start_path)
                await asyncio.wait({run_task}, timeout=0.25)
            await run_task

    @staticmethod
    async def _cancel_task(task: asyncio.Task) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _snapshot(self, start_path: str, elapsed: float) -> ExplorationTrace:
        stats = self.guard.get_stats()
        return self.trace_recorder.build(tool_stats={
            'total_tool_calls': stats['total_tool_calls'], 'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'], 'termination_reason': stats['termination_reason'] or '',
            'duration_seconds': round(max(0, elapsed), 3), 'model_calls': self.guard.model_call_count,
            'potential_write_tool_calls': stats['potential_write_tool_calls'], 'blocked_write_tool_calls': stats['blocked_write_tool_calls'],
        }, termination_reason=stats['termination_reason'] or '', cleanup={'status': 'unknown' if stats['potential_write_tool_calls'] else 'not_required', 'attempted': False, 'residuals': [], 'reason': 'possible writes require visible cleanup confirmation' if stats['potential_write_tool_calls'] else ''})

    def _failure(self, code: str, message: str, start_path: str = '/') -> MCPPageExplorerError:
        trace = self._snapshot(start_path, 0)
        return MCPPageExplorerError(code, message, tool_stats=trace.tool_stats, snapshot=trace)
