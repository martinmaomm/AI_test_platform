"""One-agent, one-session Playwright MCP exploration for v4."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.tools import StructuredTool
from mcp_use import MCPClient
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from ai_core.webui_playwright_agent import MCP_BROWSER_TOOL_CALL_LIMIT, MCP_MAX_STEPS, MCPBrowserToolGuard, _classify_mcp_error, _get_mcp_error_message

from .exploration_policy import ExplorationPolicy
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import (
    CHECKPOINT_TOOL_NAME,
    ExplorationTrace,
    ExplorationTraceRecorder,
    _tool_failed,
    recoverable_locator_failure,
)
from .generation_contracts import ScenarioPlan
from .generation_preflight import prepare_playwright_mcp_output_config, validate_generation_output_id

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你负责在一个连续浏览器会话中完成完整测试场景探索，绝不生成 Python、JavaScript 或测试脚本。
只创建一次连续上下文：按 instructions 顺序登录、导航、操作、验证和清理；业务步骤之间不得回到 start_path，除非确认会话丢失。
所有 playwright_navigate 调用必须传 JSON 布尔值 headless: true。浏览器工具最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。
callback 轨迹是平台唯一事实来源；最终文本不提供事件、选择器、HTML 或脚本。
平台提供 aits_record_checkpoint 本地工具。只有在“下一次 Playwright callback”属于最终主路径、机器断言或真实清理时才先调用 checkpoint；失败 callback 会消费该 checkpoint，重试前必须重新标记。未标记的探索、绕路和定位尝试只保留诊断，不会进入脚本。
主路径动作使用 phase=main,intent=replay；主断言使用 phase=assertion,intent=evidence 并传 ScenarioPlan 已声明的 main assertion_id，随后用带 selector 的 playwright_get_visible_html/text 观察；清理动作使用 phase=cleanup,intent=cleanup，动作成功后还必须使用 phase=cleanup,intent=evidence 和 cleanup assertion_id 绑定后续页面观察。checkpoint 本身不证明 selector、动作、断言或清理成功。
每次成功操作后观察页面；SPA 路由变化后优先用能返回页面状态/URL 的观察工具确认当前位置。结束前保留验证和清理页面证据。
不允许审批、付款、发布、上传、下载或未授权外部操作。只在 allow_test_data_writes=true 时操作本轮 namespace 测试数据；结果未知时停止，不得重试。
runtime_input_values 是平台提供的唯一输入值映射。仅在实际需要填充或选择时原样使用；不得猜测 ref、不得改写值、不得在最终文本中复述这些值。
提供临时凭据时不要调用截图工具，避免把登录值或会话状态写入图片文件。
元素未找到、不可见、未启用、严格模式冲突或尚未加载时，先重新观察当前页面并在预算内调整定位；不要把确认未执行的定位失败当作写入结果未知。
不得输出用户名、密码、Token、Cookie、完整 URL、截图 Base64 或 HTML。"""

READ_ONLY_DISABLED_TOOL_MESSAGES = {
    'playwright_evaluate': '页面探索不允许执行页面 JavaScript。',
    'playwright_upload_file': '页面探索不允许上传文件。',
    'playwright_close': '页面探索不允许关闭浏览器。',
}
_HIGH_RISK_MARKERS = ('审批', '付款', '支付', '发布', '上传', '下载', 'approve', 'pay', 'publish', 'upload', 'download')
_RUNTIME_REF_TOKEN_RE = re.compile(r'[^a-zA-Z0-9_-]+')


class ExplorationCheckpointInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phase: Literal['main', 'assertion', 'cleanup']
    intent: Literal['replay', 'evidence', 'cleanup']
    assertion_id: str = Field(default='', pattern=r'^(?:|A[1-9][0-9]*)$')

    @model_validator(mode='after')
    def _valid_pair(self):
        valid_pairs = {
            ('main', 'replay'), ('assertion', 'evidence'),
            ('cleanup', 'cleanup'), ('cleanup', 'evidence'),
        }
        if (self.phase, self.intent) not in valid_pairs:
            raise ValueError('phase 与 intent 不匹配')
        if (self.intent == 'evidence') != bool(self.assertion_id):
            raise ValueError('evidence intent 必须且只能提供 assertion_id')
        return self


def build_exploration_checkpoint_tool(plan: ScenarioPlan) -> StructuredTool:
    requirements = {item.assertion_id: item for item in plan.assertion_requirements}

    def record_checkpoint(
        phase: str, intent: str, assertion_id: str = '',
    ) -> str:
        """Mark how the next real Playwright callback may be used."""

        if assertion_id and assertion_id not in requirements:
            raise ValueError('assertion_id is not declared by ScenarioPlan')
        if phase == 'cleanup' and not plan.cleanup_expected:
            raise ValueError('cleanup checkpoint requires cleanup_expected=true')
        if assertion_id:
            expected_phase = 'assertion' if requirements[assertion_id].phase == 'main' else 'cleanup'
            if phase != expected_phase:
                raise ValueError('assertion_id phase does not match ScenarioPlan')
        return 'checkpoint accepted; it must bind the next Playwright callback'

    return StructuredTool.from_function(
        func=record_checkpoint,
        name=CHECKPOINT_TOOL_NAME,
        description=(
            'Classify only the next real Playwright callback. Use main/replay for a required '
            'final-path action, assertion/evidence with a declared assertion_id for a semantic '
            'observation, cleanup/cleanup for a real cleanup action, or cleanup/evidence with a '
            'declared cleanup assertion_id for the later cleanup verification. This marker is not evidence.'
        ),
        args_schema=ExplorationCheckpointInput,
    )


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Global budget guard that only terminates genuine unknown-write outcomes."""

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

    def _tool_output_failed(self, tool_name: str, output: Any) -> bool:
        return _tool_failed(output, tool_name=tool_name)

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
                    self._raise_guard('extra_risk_action', '探索阶段不允许额外高风险操作。', blocked_before_execution=True, tool_name=tool_name)
                if tool_name.endswith(('_click', '_press_key')):
                    if self.policy.may_write():
                        self._potential_write_tool_calls += 1
                        self._possible_write_runs.add(run_id)
                    elif self.policy.explicit_read_only and ('enter' in text or 'submit' in text):
                        self._blocked_write_tool_calls += 1
                        self._raise_guard('read_only_violation', '当前场景是观察性目标，禁止可能提交表单的操作。', blocked_before_execution=True, tool_name=tool_name)
            return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)
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
        self._async_cancel_check = self.cancel_check if asyncio.iscoroutinefunction(self.cancel_check) else sync_to_async(self.cancel_check, thread_sensitive=True)
        self.generation_id = generation_id
        self.user_constraints = str(user_constraints or '')
        self.exploration_timeout_seconds = exploration_total_timeout_seconds() if exploration_timeout_seconds is None else float(exploration_timeout_seconds)
        self.output_generation_id = validate_generation_output_id(generation_id)
        self.policy = ExplorationPolicy.read_only()
        self.trace_recorder = ExplorationTraceRecorder()
        self.guard = ReadOnlyMCPBrowserToolGuard(policy=self.policy, trace_recorder=self.trace_recorder)
        self._started_at: float | None = None
        self._sensitive_runtime_present = False

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
            safe_ref = _RUNTIME_REF_TOKEN_RE.sub('-', spec.name).strip('-')[:48] or 'value'
            runtime_values[spec.name] = f'{self.policy.namespace}-{safe_ref}-{secrets.token_hex(8)}'
        self.trace_recorder.configure_runtime(runtime_values, plan.input_sources())
        self._sensitive_runtime_present = any(
            spec.source == 'credential' and spec.name in runtime_values
            for spec in plan.input_refs
        )
        return runtime_values

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
                sensitive_runtime=self._sensitive_runtime_present,
            ))
            await client.create_all_sessions()
            disallowed_tools = list(READ_ONLY_DISABLED_TOOL_MESSAGES)
            if self._sensitive_runtime_present:
                disallowed_tools.append('playwright_screenshot')
            agent = MCPAgent(llm=self.llm_model, client=client, max_steps=MCP_MAX_STEPS, additional_instructions=EXPLORER_CONSTRAINTS, disallowed_tools=disallowed_tools, callbacks=[self.guard])
            await self._await_task(asyncio.create_task(agent.initialize()), deadline, start_path)
            await self._await_task(
                asyncio.create_task(agent.register_local_tools([
                    build_exploration_checkpoint_tool(plan),
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
                'checkpoint_protocol': {
                    'main_replay': {'phase': 'main', 'intent': 'replay'},
                    'assertion_evidence': {
                        'phase': 'assertion', 'intent': 'evidence',
                        'assertion_id': 'use an id declared in scenario_plan.assertion_requirements',
                    },
                    'cleanup': {'phase': 'cleanup', 'intent': 'cleanup'},
                    'cleanup_verification': {
                        'phase': 'cleanup', 'intent': 'evidence',
                        'assertion_id': 'use a cleanup-phase id declared in assertion_requirements',
                    },
                    'binding': 'exactly the next Playwright callback; failure consumes the marker',
                    'unmarked_callbacks': 'diagnostic exploration only; never replayed',
                },
                'instruction': '一次连续完成完整场景；先观察，按自然业务状态前进。最终文本只简要说明完成或停止原因，不承担任何契约。',
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
        }, termination_reason=stats['termination_reason'] or '')

    def _failure(self, code: str, message: str, start_path: str = '/') -> MCPPageExplorerError:
        if not self.trace_recorder.events and self.trace_recorder.start_path != (start_path or '/'):
            self.trace_recorder = ExplorationTraceRecorder(start_path)
        trace = self._snapshot()
        return MCPPageExplorerError(code, message, tool_stats=trace.tool_stats, snapshot=trace)
