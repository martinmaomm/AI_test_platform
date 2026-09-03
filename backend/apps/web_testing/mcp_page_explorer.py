"""One-agent, one-session Playwright MCP exploration for v4."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.tools import StructuredTool
from mcp_use import MCPClient
from pydantic import BaseModel, ConfigDict, Field

from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from ai_core.webui_playwright_agent import MCP_BROWSER_TOOL_CALL_LIMIT, MCP_MAX_STEPS, MCPBrowserToolGuard, _classify_mcp_error, _get_mcp_error_message

from .exploration_policy import ExplorationPolicy
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import (
    FINALIZATION_TOOL_NAME,
    ExplorationTrace,
    ExplorationTraceRecorder,
    FinalizedAction,
    FinalizedAssertion,
    _tool_failed,
    recoverable_locator_failure,
)
from .generation_contracts import GenerationContractError, ScenarioPlan
from .generation_preflight import prepare_playwright_mcp_output_config, validate_generation_output_id

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你负责在一个连续浏览器会话中完成完整测试场景探索，绝不生成 Python、JavaScript 或测试脚本。
只创建一次连续上下文：按 instructions 顺序登录、导航、操作、验证和清理；业务步骤之间不得回到 start_path，除非确认会话丢失。
所有 playwright_navigate 调用必须传 JSON 布尔值 headless: true。浏览器工具硬上限 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。达到探索预算后，立即停止浏览器调用并只用本地定稿工具收尾。
callback 轨迹是平台唯一事实来源；最终文本不提供事件、选择器、HTML 或脚本。
平台自动记录每个 Playwright callback；失败、绕路和不稳定定位只用于诊断，绝不可静默删除。接近结束时先调用 aits_get_path_candidates，读取安全候选摘要；随后只调用一次 aits_finalize_path 提交最终主动作、所有 assertion_id 对应的观察事件、可选清理动作及简短中文步骤名。平台自动将首次成功 playwright_navigate 作为入口，不要手工选择 navigate。最终定稿后不得再调用浏览器工具，否则定稿会失效且必须重新定稿。
只选择成功、顺序递增且有稳定 locator 的真实 callback。fill/select 只有精确匹配 runtime_input_values 或 aits_declare_generated_input 返回的 input ref 时才可选择；候选摘要标为 unmapped_input 的事件不可选择。每条 assertion requirement 都必须绑定成功的带 selector observation，且其安全观察摘要确实满足 visible/contains/not_contains 语义。清理动作必须在主场景后，清理验证必须在最后一个清理动作后。
发现计划外必填输入时，先调用 aits_declare_generated_input 声明通用动态变量，再原样使用该工具返回的值；不能使用凭据变量或自行猜值代替。每次成功操作后优先一次目标 visible-text 观察；只有可见文本不足以定位或验证时才读取 HTML，不重复读取未变化页面。SPA 路由变化后优先用能返回页面状态/URL 的观察工具确认当前位置。结束前保留验证和清理页面证据。
不允许审批、付款、发布、上传、下载或未授权外部操作。只在 allow_test_data_writes=true 时操作本轮 namespace 测试数据；结果未知时停止，不得重试。
runtime_input_values 是平台提供的唯一输入值映射。仅在实际需要填充或选择时原样使用；不得猜测 ref、不得改写值。测试环境模式允许凭据随 callback、日志和截图保留。
元素未找到、不可见、未启用、严格模式冲突或尚未加载时，先重新观察当前页面并在预算内调整定位；不要把确认未执行的定位失败当作写入结果未知。
不得输出完整 URL、截图 Base64 或 HTML。"""

READ_ONLY_DISABLED_TOOL_MESSAGES = {
    'playwright_evaluate': '页面探索不允许执行页面 JavaScript。',
    'playwright_upload_file': '页面探索不允许上传文件。',
    'playwright_close': '页面探索不允许关闭浏览器。',
}
_HIGH_RISK_MARKERS = ('审批', '付款', '支付', '发布', '上传', '下载', 'approve', 'pay', 'publish', 'upload', 'download')


class FinalizationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    main_actions: list[FinalizedAction] = Field(default_factory=list, max_length=120)
    assertions: list[FinalizedAssertion] = Field(default_factory=list, max_length=20)
    cleanup_actions: list[FinalizedAction] = Field(default_factory=list, max_length=120)

class CandidateSummaryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DynamicGeneratedInputRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value_kind: str = Field(pattern=r'^(?:text|email|password|integer)$')


def build_finalization_tools(recorder: ExplorationTraceRecorder) -> list[StructuredTool]:
    def candidates() -> dict[str, Any]:
        """Read a redacted, callback-owned candidate summary before finalizing."""
        return recorder.candidate_summary()

    def finalize_path(
        main_actions: list[FinalizedAction],
        assertions: list[FinalizedAssertion],
        cleanup_actions: list[FinalizedAction],
    ) -> dict[str, str]:
        """Submit the one final callback-backed replay path for deterministic validation."""
        try:
            return recorder.finalize_path(
                main_actions=main_actions, assertions=assertions, cleanup_actions=cleanup_actions,
            )
        except GenerationContractError as exc:
            # A rejected proposal is actionable agent feedback, not an agent-run failure.
            return {'status': 'rejected', 'error_code': str(exc)}

    return [
        StructuredTool.from_function(
            func=candidates, name='aits_get_path_candidates',
            description='读取安全候选事件摘要；必须在最终路径定稿前调用。',
            args_schema=CandidateSummaryInput,
        ),
        StructuredTool.from_function(
            func=finalize_path, name=FINALIZATION_TOOL_NAME,
            description='一次提交最终路径。入口 navigate 由平台自动加入，不要提交它。',
            args_schema=FinalizationInput,
        ),
    ]


def build_dynamic_input_tools(
    recorder: ExplorationTraceRecorder,
    value_factory: Callable[[str], str],
) -> list[StructuredTool]:
    def declare_generated_input(value_kind: str) -> dict[str, str]:
        """Declare one discovered required input and return its in-memory value."""
        runtime_value = value_factory(value_kind)
        spec = recorder.declare_dynamic_input(
            value_kind=value_kind, runtime_value=runtime_value,
        )
        return {
            'name': spec.name,
            'source': spec.source,
            'value_kind': spec.value_kind,
            'value': runtime_value,
        }

    return [StructuredTool.from_function(
        func=declare_generated_input,
        name='aits_declare_generated_input',
        description='声明页面新发现的必填输入，返回仅限本次会话使用的动态变量和值。',
        args_schema=DynamicGeneratedInputRequest,
    )]


MCP_FINALIZATION_BROWSER_CALL_LIMIT = 60
MCP_FINALIZATION_IGNORED_BROWSER_CALL_LIMIT = 3


class FinalizationOnlyBrowserToolError(RuntimeError):
    """Recoverable feedback: browser work ended but local finalization remains allowed."""


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Global budget guard that only terminates genuine unknown-write outcomes."""

    def __init__(self, max_tool_calls: int = MCP_BROWSER_TOOL_CALL_LIMIT, *, finalization_browser_call_limit: int = MCP_FINALIZATION_BROWSER_CALL_LIMIT, policy: ExplorationPolicy | None = None, trace_recorder: ExplorationTraceRecorder | None = None):
        super().__init__(max_tool_calls)
        if finalization_browser_call_limit <= 0:
            raise ValueError('finalization browser budget must be positive')
        self.finalization_browser_call_limit = min(finalization_browser_call_limit, max_tool_calls)
        self.policy = policy or ExplorationPolicy.read_only()
        self.trace_recorder = trace_recorder or ExplorationTraceRecorder()
        self.model_call_count = 0
        self._potential_write_tool_calls = 0
        self._blocked_write_tool_calls = 0
        self._possible_write_runs: set[Any] = set()
        self._finalization_only_mode = False
        self._finalization_only_blocked_calls = 0

    def on_chat_model_start(self, serialized, messages, **kwargs):
        with self._lock:
            self.model_call_count += 1

    def _tool_output_failed(self, tool_name: str, output: Any) -> bool:
        return _tool_failed(output, tool_name=tool_name)

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        run_id = kwargs.get('run_id')
        self.trace_recorder.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        tool_name = str((serialized or {}).get('name') or '').lower()
        text = json.dumps(inputs, ensure_ascii=False).lower() if isinstance(inputs, dict) else str(input_str or '').lower()
        try:
            with self._lock:
                if (
                    self.finalization_browser_call_limit < self.max_tool_calls
                    and self._is_browser_tool(tool_name)
                    and self.total_tool_calls >= self.finalization_browser_call_limit
                ):
                    self._finalization_only_mode = True
                    self._finalization_only_blocked_calls += 1
                    if self._finalization_only_blocked_calls >= MCP_FINALIZATION_IGNORED_BROWSER_CALL_LIMIT:
                        self._raise_guard(
                            'finalization_browser_budget_exhausted',
                            '已进入最终路径定稿阶段，连续忽略收尾提示调用浏览器工具，任务已停止。',
                            blocked_before_execution=True, tool_name=tool_name,
                        )
                    raise FinalizationOnlyBrowserToolError(
                        '浏览器探索预算已用完；请立即调用 aits_get_path_candidates 和 aits_finalize_path 收尾。'
                    )
                if tool_name in READ_ONLY_DISABLED_TOOL_MESSAGES:
                    self._blocked_write_tool_calls += 1
                    self._raise_guard('read_only_violation', READ_ONLY_DISABLED_TOOL_MESSAGES[tool_name], blocked_before_execution=True, tool_name=tool_name)
                if tool_name.endswith(('_click', '_press_key')) and any(marker in text for marker in _HIGH_RISK_MARKERS):
                    self._blocked_write_tool_calls += 1
                    self._raise_guard('extra_risk_action', '探索阶段不允许额外高风险操作。', blocked_before_execution=True, tool_name=tool_name)
                if tool_name.endswith(('_click', '_press_key')):
                    if self.policy.may_write():
                        self._potential_write_tool_calls += 1
                        self._possible_write_runs.add(run_id)
                    elif self.policy.explicit_read_only and ('enter' in text or 'submit' in text):
                        self._blocked_write_tool_calls += 1
                        self._raise_guard('read_only_violation', '当前场景是观察性目标，禁止可能提交表单的操作。', blocked_before_execution=True, tool_name=tool_name)
            return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)
        except FinalizationOnlyBrowserToolError:
            self.trace_recorder.discard_active(run_id)
            raise
        except BaseException as exc:
            self.trace_recorder.mark_blocked(serialized, input_str, run_id=run_id, inputs=inputs, error=exc)
            raise

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_end(output, run_id=run_id)
        super().on_tool_end(output, run_id=run_id, **kwargs)
        if run_id in self._possible_write_runs and _tool_failed(
            output, tool_name='playwright_click',
        ) and not recoverable_locator_failure(output) and self._terminal_error is None:
            self._raise_guard('write_result_unknown', '可能写入未获得可确认结果，已停止以避免重复操作。')

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        self.trace_recorder.on_tool_error(error, run_id=run_id)
        super().on_tool_error(error, run_id=run_id, **kwargs)
        if run_id in self._possible_write_runs and not recoverable_locator_failure(error) and self._terminal_error is None:
            self._raise_guard('write_result_unknown', '可能写入发生工具错误，已停止以避免重复操作。')

    def get_stats(self):
        stats = super().get_stats()
        stats.update({
            'potential_write_tool_calls': self._potential_write_tool_calls,
            'blocked_write_tool_calls': self._blocked_write_tool_calls,
            'finalization_only_mode': self._finalization_only_mode,
            'finalization_only_blocked_calls': self._finalization_only_blocked_calls,
            'finalization_browser_call_limit': self.finalization_browser_call_limit,
        })
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
        self._async_cancel_check = self.cancel_check if asyncio.iscoroutinefunction(self.cancel_check) else sync_to_async(self.cancel_check, thread_sensitive=True)
        self.generation_id = generation_id
        self.user_constraints = str(user_constraints or '')
        self.exploration_timeout_seconds = exploration_total_timeout_seconds() if exploration_timeout_seconds is None else float(exploration_timeout_seconds)
        self.output_generation_id = validate_generation_output_id(generation_id)
        self.policy = ExplorationPolicy.read_only()
        self.trace_recorder = ExplorationTraceRecorder()
        self.guard = ReadOnlyMCPBrowserToolGuard(policy=self.policy, trace_recorder=self.trace_recorder)
        self._started_at: float | None = None

    def _configure(self, plan: ScenarioPlan, start_path: str, credentials: dict[str, str] | None):
        self.policy = ExplorationPolicy.for_plan(plan, generation_id=self.output_generation_id, user_constraints=self.user_constraints)
        trace_file = Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp' / f'{self.output_generation_id}.trace.jsonl'
        self.trace_recorder = ExplorationTraceRecorder(start_path, trace_file=trace_file, runtime_namespace=self.policy.namespace)
        self.trace_recorder.configure_plan(plan)
        self.guard = ReadOnlyMCPBrowserToolGuard(policy=self.policy, trace_recorder=self.trace_recorder)
        runtime_values: dict[str, str] = {}
        for spec in plan.input_refs:
            if spec.source == 'credential':
                if credentials and credentials.get(spec.credential_slot):
                    runtime_values[spec.name] = str(credentials[spec.credential_slot])
                continue
            if spec.source == 'runtime':
                # Runtime values are supplied only when the generated script
                # executes. Inventing one here would turn an input contract
                # into persisted-looking test evidence.
                continue
            runtime_values[spec.name] = self._generated_runtime_value(spec.value_kind, spec.name)
        self.trace_recorder.configure_runtime(runtime_values, plan.input_sources())
        return runtime_values

    def _generated_runtime_value(self, value_kind: str, name: str = 'value') -> str:
        scope = hashlib.sha256(
            f'{self.policy.namespace}:{name}'.encode('utf-8'),
        ).hexdigest()[:8]
        token = secrets.token_hex(8)
        if value_kind == 'email':
            return f'aits-{scope}-{token}@example.com'
        if value_kind == 'password':
            return f'Aits!{token}9'
        if value_kind == 'integer':
            return str(secrets.randbelow(900000) + 100000)
        return f'aits-{scope}-{token}'

    async def explore_until_complete(self, *, plan: ScenarioPlan, start_path: str, target_url_safe: str, temporary_credentials: dict[str, str] | None = None) -> ExplorationTrace:
        if await self._is_cancelled():
            raise self._failure('TASK_CANCELLED', '用户已取消任务。')
        runtime_values = self._configure(plan, start_path, temporary_credentials)
        self._started_at = time.monotonic()
        deadline = self._started_at + self.exploration_timeout_seconds
        client = None
        try:
            client = MCPClient.from_dict(prepare_playwright_mcp_output_config(
                self.mcp_config,
                self.output_generation_id,
            ))
            await client.create_all_sessions()
            disallowed_tools = list(READ_ONLY_DISABLED_TOOL_MESSAGES)
            agent = MCPAgent(llm=self.llm_model, client=client, max_steps=MCP_MAX_STEPS, additional_instructions=EXPLORER_CONSTRAINTS, disallowed_tools=disallowed_tools, callbacks=[self.guard])
            await self._await_task(asyncio.create_task(agent.initialize()), deadline, start_path)
            await self._await_task(
                asyncio.create_task(agent.register_local_tools([
                    *build_finalization_tools(self.trace_recorder),
                    *build_dynamic_input_tools(self.trace_recorder, self._generated_runtime_value),
                ])),
                deadline,
                start_path,
            )
            prompt = json.dumps({
                'scenario_plan': plan.model_dump(mode='json'),
                'navigation_target_url': target_url_safe,
                'start_path': start_path,
                'current_relative_path': self.trace_recorder.start_path,
                'runtime_input_values': runtime_values,
                'scope_policy': self.policy.prompt_scope(),
                'finalization_protocol': {
                    'candidate_tool': 'aits_get_path_candidates',
                    'finalization_tool': FINALIZATION_TOOL_NAME,
                    'entry_navigation': 'the platform automatically adds the first successful navigate callback',
                    'input_rule': 'only callback-mapped runtime_input_values or aits_declare_generated_input refs may be selected for fill/select',
                    'staleness': 'any browser callback after finalization invalidates it',
                    'finalization_browser_call_limit': MCP_FINALIZATION_BROWSER_CALL_LIMIT,
                },
                'instruction': '一次连续完成完整场景；先观察，按自然业务状态前进。结束前读取候选摘要并完成一次最终路径定稿。最终文本只简要说明完成或停止原因，不承担任何契约。',
            }, ensure_ascii=False)
            with suppress_mcp_raw_query_logs():
                await self._await_task(asyncio.create_task(agent.run(prompt, manage_connector=False)), deadline, start_path)
            return self._snapshot()
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
                    logger.warning('v4 MCP 会话清理失败', exc_info=True)

    async def _await_task(self, task: asyncio.Task, deadline: float, start_path: str) -> None:
        await asyncio.sleep(0)
        while not task.done():
            if await self._is_cancelled():
                await self._cancel_task(task)
                raise self._failure('TASK_CANCELLED', '用户已取消任务。', start_path)
            if time.monotonic() >= deadline:
                await self._cancel_task(task)
                raise self._failure('exploration_timeout', '页面探索已达到总时限。', start_path)
            if self.guard.terminal_error is not None:
                await self._cancel_task(task)
                raise self._failure(self.guard.terminal_error.error_kind, str(self.guard.terminal_error), start_path)
            await asyncio.wait({task}, timeout=0.25)
        await task

    async def _is_cancelled(self) -> bool:
        return bool(await self._async_cancel_check())

    @staticmethod
    async def _cancel_task(task: asyncio.Task) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _snapshot(self) -> ExplorationTrace:
        stats = self.guard.get_stats()
        elapsed = max(0, time.monotonic() - self._started_at) if self._started_at is not None else 0
        duration_seconds = (
            max(0.001, round(elapsed, 3)) if self._started_at is not None else 0
        )
        return self.trace_recorder.build(tool_stats={
            'total_tool_calls': stats['total_tool_calls'], 'tool_counts': stats['tool_counts'],
            'failed_tool_calls': stats['failed_tool_calls'], 'termination_reason': stats['termination_reason'] or '',
            'duration_seconds': duration_seconds, 'model_calls': self.guard.model_call_count,
            'potential_write_tool_calls': stats['potential_write_tool_calls'], 'blocked_write_tool_calls': stats['blocked_write_tool_calls'],
            'finalization_only_mode': stats['finalization_only_mode'],
            'finalization_only_blocked_calls': stats['finalization_only_blocked_calls'],
            'finalization_browser_call_limit': stats['finalization_browser_call_limit'],
        }, termination_reason=stats['termination_reason'] or '')

    def _failure(self, code: str, message: str, start_path: str = '/') -> MCPPageExplorerError:
        if not self.trace_recorder.events and self.trace_recorder.start_path != (start_path or '/'):
            self.trace_recorder = ExplorationTraceRecorder(start_path)
        trace = self._snapshot()
        return MCPPageExplorerError(code, message, tool_stats=trace.tool_stats, snapshot=trace)
