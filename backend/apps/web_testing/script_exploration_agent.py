"""Incremental v5 Python-draft generation in one MCP browser session.

This module deliberately does not reuse the v4 finalization protocol.  The
agent writes a complete, replace-in-place Python draft while it explores and
the callback checkpoint owns durable persistence.
"""

from __future__ import annotations

import asyncio
import ast
import json
import logging
import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.tools import StructuredTool
from mcp_use import MCPClient
from pydantic import BaseModel, ConfigDict, Field

from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from ai_core.mcp_exploration_runtime import ExplorationRuntimeMiddleware, compact_saved_evidence
from ai_core.webui_playwright_agent import (
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_INTERACTION_CORRECTION_LIMIT,
    MCP_INTERACTION_REPEAT_LIMIT,
    MCP_MAX_STEPS,
    _classify_mcp_error,
    _get_mcp_error_message,
)
from common.parsers import extract_python_from_output

from .exploration_policy import ExplorationPolicy
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import ExplorationTraceRecorder, _tool_failed
from .exploration_diagnostics import failure_context
from .script_draft_edits import apply_script_edits
from .generation_preflight import (
    prepare_playwright_mcp_output_config,
    validate_generation_output_id,
)
from .mcp_page_explorer import (
    READ_ONLY_DISABLED_TOOL_MESSAGES,
    ReadOnlyMCPBrowserToolGuard,
    suppress_mcp_raw_query_logs,
)
from .draft_quality import evaluate_draft
from .target_urls import target_origin, validate_target_url


logger = logging.getLogger(__name__)

SCRIPT_SAVE_TOOL_NAME = 'save_script_draft'
_CHECKPOINT_INTERVAL_SECONDS = 3.0
_RAW_MODEL_OUTPUT_LIMIT = 20000
_MODEL_OUTPUT_SUMMARY_LIMIT = 2000
_MAX_SCRIPT_CHARS = 200000
_PENDING_STEP_PREFIX = '# PENDING_STEP:'
_PENDING_ASSERTION_PREFIX = '# PENDING_ASSERTION:'
_BASE64_RE = re.compile(r'(?<![a-z0-9+/=])[a-z0-9+/]{2048,}={0,2}', re.I)
_SCREENSHOT_DATA_RE = re.compile(r'data:image/[^;,\s]+;base64,[a-z0-9+/=\s]+', re.I)


EXPLORATION_SCRIPT_CONSTRAINTS = f"""你在一个连续的 Playwright MCP 浏览器上下文中探索并增量编写 Python 草稿。
所有工具操作按顺序串行执行，包括填写、点击、读取页面和保存草稿；前一个完成后再执行下一个。需要根据页面结果决定的后续操作，应等待本轮工具结果后再发出。
生成的 Python 脚本也必须按业务步骤逐个 await 浏览器操作，不使用 asyncio.gather、create_task、线程池或进程池并发执行测试步骤。
浏览器调用上限 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，模型调用上限 {MCP_MAX_STEPS} 次；接近预算时停止浏览器操作，使用 save_script_draft 保存当前完整草稿和真实剩余步骤。
每完成一个有意义的业务子步骤或修复后，都保存草稿。首次或整体重写调用 save_script_draft，后续优先 patch_script_draft 局部更新，避免反复输出整份代码；需要准确片段时调用 read_script_draft。保存工具会返回修订号和静态检查反馈；按反馈继续完善，不只在最终文本给代码。
save_script_draft 的 code 必须是完整可替换 Python 草稿，保留顶部中文“场景/目标”说明和主要步骤注释，入口为 async def run(page, variables)，不得自行启动或关闭浏览器。脚本首次 page.goto 必须使用 target_url 完整网址，原样保留路径、查询参数和 # 路由；后续导航也必须使用完整 HTTP(S) 网址，禁止依赖 '/'、相对路径、base_url 或测试环境。MCP 的 playwright_navigate 也使用完整网址并显式传 JSON 布尔值 headless: true。登录账号和密码只从原始测试描述理解，不存在独立登录信息表单或测试环境配置；缺少信息时明确说明，不编造账号。固定数据值和可选 variables 可以混用，仅需唯一值时使用 time.time_ns()。原始用户描述不可改写为虚构业务。
点击、导航、按键等关键动作后，平台在工具结果中尽可能附带一次当前页面观察。先利用该观察决定下一步；只有观察缺失、失败、被截断或仍在加载时才补充读取。已观察表单的独立字段可以顺序连续填写，不要求每填一个字段都再读整页；联动字段改变结构时仍应观察。不得为了写脚本而刷新入口或重复已确认的流程。完成业务子步骤后用保存或局部修改工具持久化，不要等到最终回复。
操作表单前先检查当前可见结构、控件状态及约束，根据用户目标和已提供数据完成填写，再提交；不要为了查找字段而先点击尚未检查的提交按钮。不要依赖固定语言的按钮文案或固定字段名推断业务意图。HTML 中仍有表单不代表它在屏幕上可见；以平台浏览器现场的 visible/enabled 状态为准。提交后弹窗可能已关闭，不得继续填写隐藏表单或仅更换定位写法重试；先观察当前可见页面，再按目标打开正确表单。不得用 force 或 JavaScript 绕过可见性。平台只读诊断不会替你打开弹窗，也不代表业务操作已验证成功。
工具返回点击成功只说明动作已执行，不代表认证或业务成功；页面仍有表单也不代表认证失败。提交后先观察实际校验提示、控件状态及目标结果；页面仍在加载时先等待和观察，不连续点击。
发现校验未通过或填错时，使用已有证据和用户提供的数据修正输入后允许重试；不得猜测凭据。只有成功执行且值确实变化的填写/选择才算输入纠正，重复填入同值、失败的填写、重复读取页面都不算。相同页面状态与输入状态下，同一操作最多执行 {MCP_INTERACTION_REPEAT_LIMIT} 次；相同页面状态下，即使修改输入，同一非输入操作累计也最多执行 {MCP_INTERACTION_CORRECTION_LIMIT} 次。达到上限前保存草稿和具体未完成原因，不能以轮换输入、变换同一元素的定位写法或反复刷新规避限制。缺少可靠结果证据时标记未确认，不编造成功或账号错误结论。
只根据真实观察生成 goto、定位器和断言。未实际完成的操作必须在代码中保留 # PENDING_STEP: {{\"reason\":\"...\"}}；未知断言使用 # PENDING_ASSERTION: ...。存在 pending step 或 remaining_steps 时不可声称 complete。
每条真实的 Python assert 或 await expect(...).to_*/not_to_* 前，必须紧邻写 # 验证：简洁中文业务结果，仅作为每条成功断言的可读标识。不得 print 平台的通过或测试完成日志，统一执行器会在实际成功时输出它们。
若真实完成并确认某一待补充操作或断言，只移除该项对应 marker；仅删除 marker 不构成完成证明，绝不自动清除平台侧状态。只有全部目标工作和待补充项均已真实完成时，才以 completion=complete、remaining_steps=[] 保存；否则保持 partial 并列出具体剩余项。completed_steps、remaining_steps 和 marker 的 reason 使用简洁中文。
不得伪造按钮、页面文字、定位器或断言。禁止 playwright_evaluate、上传、关闭浏览器、外域导航，以及审批、付款、发布、下载等未授权高风险操作。浏览器只可访问本次目标站点。
页面或定位器的名称不等于操作授权；按用户目标和实际页面证据区分查看页面与产生业务副作用，不因页面文案直接判定风险。无法确认授权范围的动作不要执行，先保存草稿并说明具体原因。
若 brief.recovery_context 存在，这是用户明确确认后的中断恢复，不是从头重跑。当前浏览器是新会话，旧轨迹仅是历史证据；先观察当前现场，按需重新登录、导航和只读查询，核对用户补充说明及已完成步骤。不得直接执行整个已保存脚本或重复新增、修改、删除；结果不明确时先查询确认，无法确认则保存 partial 草稿并停止。只探索明确尚未完成且仍在原目标授权内的操作，保留已有代码和待补充标记。
无需也不得调用任何路径定稿工具或基于 event id 的完成协议。最终回复只用中文简短说明已保存的草稿状态和剩余项，不输出推理过程；草稿的权威版本来自 save_script_draft。"""


@dataclass(frozen=True)
class ScriptExplorationResult:
    script_draft: str
    snapshot: dict[str, Any]
    error_code: str = ''
    error_message: str = ''
    final_message: str = ''
    completion: str = 'unknown'


class ScriptSaveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: str = Field(min_length=1, max_length=_MAX_SCRIPT_CHARS)
    completed_steps: list[str] = Field(default_factory=list, max_length=100)
    remaining_steps: list[str] = Field(default_factory=list, max_length=100)
    variables: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    completion: str = Field(default='partial', pattern=r'^(?:partial|complete|unknown)$')


class ScriptPatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, strict=True)
    edits: list[dict[str, str]] = Field(min_length=1, max_length=20)
    completed_steps: list[str] | None = Field(default=None, max_length=100)
    remaining_steps: list[str] | None = Field(default=None, max_length=100)
    variables: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    completion: str | None = Field(default=None, pattern=r'^(?:partial|complete|unknown)$')


class ScriptReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=100, ge=1, le=150)


class ScriptExplorationAgentError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class ScriptExplorationToolGuard(ReadOnlyMCPBrowserToolGuard):
    """Add task/origin constraints to the generic interaction recovery guard.

    Authentication and other business outcomes are not inferred from labels,
    field names, or the presence/absence of a form.  The shared guard bounds
    repeated operations; the agent must verify outcomes from actual evidence.
    """

    def __init__(
        self,
        *,
        policy: ExplorationPolicy,
        trace_recorder: ExplorationTraceRecorder,
        target_url: str,
        progress_notifier: Callable[[], None],
        stop_check: Callable[[], str],
    ) -> None:
        super().__init__(
            max_tool_calls=MCP_BROWSER_TOOL_CALL_LIMIT,
            finalization_browser_call_limit=MCP_BROWSER_TOOL_CALL_LIMIT,
            policy=policy,
            trace_recorder=trace_recorder,
        )
        self._allowed_origin = target_origin(target_url)
        self._progress_notifier = progress_notifier
        self._stop_check = stop_check

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        if reason := self._stop_check():
            raise ScriptExplorationAgentError('CHECKPOINT_FAILED', reason)
        tool_name = str((serialized or {}).get('name') or '').lower()
        if tool_name == 'playwright_navigate' and isinstance(inputs, dict):
            candidate = str(inputs.get('url') or inputs.get('target') or inputs.get('href') or '')
            try:
                origin = target_origin(candidate)
            except ValueError:
                self._raise_guard(
                    'invalid_target_url', '页面导航必须使用完整 HTTP(S) 网址，不能使用相对路径。',
                    blocked_before_execution=True, tool_name=tool_name,
                )
            if origin != self._allowed_origin:
                self._raise_guard(
                    'external_domain_blocked', '页面探索不允许导航到目标站点以外的域名。',
                    blocked_before_execution=True, tool_name=tool_name,
                )
        return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        try:
            return super().on_tool_end(output, run_id=run_id, **kwargs)
        finally:
            self._progress_notifier()

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        try:
            return super().on_tool_error(error, run_id=run_id, **kwargs)
        finally:
            self._progress_notifier()


class ScriptExplorationAgent:
    """One MCP agent and one browser session with incremental Python drafts."""

    def __init__(
        self,
        llm_model: Any,
        mcp_config: dict[str, Any],
        generation_id: str | None,
        cancel_check: Callable[[], bool] | None,
        exploration_timeout_seconds: float | None,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.llm_model = llm_model
        self.mcp_config = dict(mcp_config or {})
        self.generation_id = str(generation_id or '')
        self.cancel_check = cancel_check or (lambda: False)
        self._async_cancel_check = (
            self.cancel_check if asyncio.iscoroutinefunction(self.cancel_check)
            else sync_to_async(self.cancel_check, thread_sensitive=True)
        )
        self.exploration_timeout_seconds = (
            exploration_total_timeout_seconds()
            if exploration_timeout_seconds is None else max(0.01, float(exploration_timeout_seconds))
        )
        self.checkpoint_callback = checkpoint_callback
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        self._brief: dict[str, Any] = {}
        self._start_path = '/'
        self._target_url = ''
        self._trace_recorder = ExplorationTraceRecorder('/')
        self._guard: ScriptExplorationToolGuard | None = None
        self._trace_path = ''
        self._prior_attempts: list[dict[str, Any]] = []
        self._efficiency_stats = Counter()
        self._last_valid_script = ''
        self._latest_candidate = ''
        self._artifact = {
            'revision': 0,
            'completion': 'unknown',
            'completed_steps': [],
            'remaining_steps': [],
            'variables': [],
        }
        self._durable_script = ''
        self._durable_artifact = deepcopy(self._artifact)
        self._final_message = ''
        self._raw_model_output = ''
        # Keep the unmodified model reply only for immediate local extraction.
        # Checkpoints and snapshots must use ``_raw_model_output`` instead.
        self._model_output_for_extraction = ''
        self._candidate_error_code = ''
        self._candidate_error_message = ''
        self._warnings: list[str] = []
        self._termination_reason = ''
        self._checkpoint_failure = ''
        self._last_checkpoint_at = 0.0
        self._checkpoint_requested = False
        self._save_count = 0
        self._latest_candidate_feedback: dict[str, Any] = {}
        self._saved_trace_data: dict[str, Any] = {
            'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {},
        }
        self._saved_repair_diagnostics: Any = {}
        self._restored_last_valid_script = ''
        self._seed_is_current = False

    async def generate(
        self,
        *,
        brief: dict,
        target_url: str,
        saved_snapshot: dict | None = None,
        script_draft: str = '',
        code_only: bool = False,
    ) -> ScriptExplorationResult:
        self._reset_run_state()
        self._brief = dict(brief or {})
        try:
            self._target_url = validate_target_url(target_url)
        except ValueError as exc:
            return self._result('INVALID_TARGET_URL', str(exc))
        parsed = urlsplit(self._target_url)
        self._start_path = parsed.path or '/'
        if parsed.query:
            self._start_path += '?' + parsed.query
        if parsed.fragment:
            self._start_path += '#' + parsed.fragment
        self._restore_snapshot(saved_snapshot)
        if not code_only and self._brief.get('recovery_context') and saved_snapshot:
            # Keep attempt-local event IDs separate. Historical observations
            # must not masquerade as evidence from the new browser session.
            self._prior_attempts.append({
                key: self._saved_trace_data.get(key)
                for key in ('events', 'page_states', 'locator_evidence', 'tool_stats', 'artifact', 'trace_path')
            })
        self._last_valid_script = str(script_draft or self._restored_last_valid_script or '').strip()
        if self._last_valid_script:
            # A pre-existing draft is a rollback candidate even when it needs
            # repair; a bad later model proposal must never erase it.
            if self._artifact['completion'] == 'unknown':
                self._artifact['completion'] = 'partial'
        self._durable_script = self._last_valid_script
        self._durable_artifact = deepcopy(self._artifact)

        if await self._is_cancelled():
            return self._result('TASK_CANCELLED', '用户已取消任务。')

        if code_only:
            return await self._generate_code_only()

        client = None
        try:
            if not self._last_valid_script:
                self._install_entry_seed()
                # This is intentionally before client/guard construction: an
                # early browser/model failure still leaves a safe, executable
                # entry draft rather than an empty generation record.
                if not await self._persist_checkpoint(force=True):
                    return self._result('CHECKPOINT_FAILED', self._checkpoint_failure)
            output_generation_id = validate_generation_output_id(self.generation_id or None)
            self._configure_trace(output_generation_id=output_generation_id)
            deadline = time.monotonic() + self.exploration_timeout_seconds
            output_config = prepare_playwright_mcp_output_config(
                self.mcp_config, output_generation_id,
            )
            playwright_config = (output_config.get('mcpServers') or {}).get('playwright')
            if isinstance(playwright_config, dict):
                playwright_config.setdefault('env', {})['MCP_PAGE_DIAGNOSTICS'] = '1'
            client = MCPClient.from_dict(output_config)
            await self._await_task(asyncio.create_task(client.create_all_sessions()), deadline)
            agent = MCPAgent(
                llm=self.llm_model,
                client=client,
                max_steps=MCP_MAX_STEPS,
                additional_instructions=EXPLORATION_SCRIPT_CONSTRAINTS,
                disallowed_tools=list(READ_ONLY_DISABLED_TOOL_MESSAGES),
                callbacks=[self._guard],
                runtime_factory=self._runtime_factory,
            )
            await self._await_task(asyncio.create_task(agent.initialize()), deadline)
            await self._await_task(
                asyncio.create_task(agent.register_local_tools([
                    self._save_tool(), self._patch_tool(), self._read_tool(),
                ])), deadline,
            )
            with suppress_mcp_raw_query_logs():
                model_result = await self._await_task(
                    asyncio.create_task(agent.run(self._prompt(), manage_connector=False)),
                    deadline,
                )
            self._record_model_output(model_result)
            await self._submit_final_text_candidate()
            if not await self._persist_checkpoint(force=True):
                return self._result('CHECKPOINT_FAILED', self._checkpoint_failure)
            return self._result(self._candidate_error_code, self._candidate_error_message)
        except Exception as exc:
            logger.exception('连续探索中断，保留已保存草稿: generation_id=%s', self.generation_id)
            error_code = exc.error_code if isinstance(exc, ScriptExplorationAgentError) else _classify_mcp_error(exc)
            message = str(exc) if isinstance(exc, ScriptExplorationAgentError) else _get_mcp_error_message(exc)
            self._termination_reason = error_code
            self._final_message = self._final_message or message
            await self._persist_checkpoint(force=True)
            return self._result(error_code, message)
        finally:
            if client is not None:
                try:
                    async with asyncio.timeout(10):
                        await client.close_all_sessions()
                except Exception:
                    logger.warning('v5 MCP 会话清理失败', exc_info=True)

    def _configure_trace(self, *, output_generation_id: str) -> None:
        explicit_read_only = bool(self._brief.get('explicit_read_only'))
        policy = ExplorationPolicy(
            namespace=f'automation-script-{self.generation_id or "local"}',
            data_scope='scenario_namespace',
            explicit_read_only=explicit_read_only,
            allow_test_data_writes=bool(self._brief.get('allow_test_data_writes')) and not explicit_read_only,
            cleanup_expected=bool(self._brief.get('cleanup_expected')) and not explicit_read_only,
        )
        # The recorder truncates its file on creation. A new attempt must never
        # erase the previous attempt's forensic evidence.
        trace_file = Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp' / f'{output_generation_id}.{uuid4().hex}.script-v5.trace.jsonl'
        self._trace_path = str(trace_file)
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        self._trace_recorder = ExplorationTraceRecorder(
            self._start_path, runtime_namespace=policy.namespace, trace_file=trace_file,
        )
        # Repair is the sole flow allowed to pass one-time execution values to
        # the model/MCP.  Register them as runtime (never credential) values
        # before any callback so trace files and snapshots retain {{NAME}}, not
        # their concrete value.  Ordinary generation intentionally keeps its
        # existing test-environment credential behavior unchanged.
        if self._brief.get('repair_only'):
            supplied = self._brief.get('runtime_input_values')
            if isinstance(supplied, dict):
                runtime_values = {
                    str(name): str(value) for name, value in supplied.items()
                    if str(name).strip() and value not in (None, '')
                }
                self._trace_recorder.configure_runtime(
                    runtime_values, {name: 'runtime' for name in runtime_values},
                )
        self._guard = ScriptExplorationToolGuard(
            policy=policy,
            trace_recorder=self._trace_recorder,
            target_url=self._target_url,
            progress_notifier=self._request_checkpoint,
            stop_check=lambda: self._checkpoint_failure,
        )

    def _save_tool(self) -> StructuredTool:
        async def save_script(
            code: str,
            completed_steps: list[str] | None = None,
            remaining_steps: list[str] | None = None,
            variables: list[dict[str, Any]] | None = None,
            completion: str = 'partial',
        ) -> dict[str, Any]:
            self._efficiency_stats['full_save_calls'] += 1
            self._efficiency_stats['draft_submitted_chars'] += len(code)
            feedback = self._consider_candidate(
                code,
                completed_steps=completed_steps or [],
                remaining_steps=remaining_steps or [],
                variables=variables or [],
                completion=completion,
                source='save_tool',
            )
            if feedback['status'] != 'accepted':
                return feedback
            if not await self._persist_checkpoint(force=True):
                return {
                    **feedback,
                    'status': 'unsaved',
                    'error_code': 'CHECKPOINT_FAILED',
                    'message': self._checkpoint_failure,
                }
            return feedback

        return StructuredTool.from_function(
            coroutine=save_script,
            name=SCRIPT_SAVE_TOOL_NAME,
            description=(
                '保存完整 Python 草稿并取得静态检查反馈。每次探索或修复后调用；'
                'code 必填，remaining_steps 非空时 completion 必须为 partial。'
            ),
            args_schema=ScriptSaveInput,
        )

    def _runtime_factory(self, tools):
        try:
            context_chars = max(12000, min(200000, int(os.environ.get('WEBUI_EXPLORATION_CONTEXT_CHARS', 48000))))
        except (ValueError, TypeError):
            context_chars = 48000
        return ExplorationRuntimeMiddleware(
            tools, checkpoint=lambda: dict(self._artifact), failed=_tool_failed,
            stats=self._efficiency_stats, context_chars=context_chars,
            auto_observe=os.environ.get('WEBUI_EXPLORATION_AUTO_OBSERVE', 'true').lower() not in {'0', 'false', 'no'},
        )

    def _read_tool(self):
        async def read_script(start_line=1, line_count=100):
            lines = self._last_valid_script.splitlines(keepends=True)
            excerpt = ''.join(lines[start_line - 1:start_line - 1 + line_count])
            return {
                **self._artifact, 'start_line': start_line, 'total_lines': len(lines),
                'code': excerpt[:16000], 'truncated': len(excerpt) > 16000,
                'note': '片段不是完整脚本；如截断请缩小行范围读取，不能用截断片段覆盖完整草稿。',
            }
        return StructuredTool.from_function(
            coroutine=read_script, name='read_script_draft', args_schema=ScriptReadInput,
            description='按行读取当前权威草稿及修订号。局部修改前按需读取；只读，不打开浏览器。',
        )

    def _patch_tool(self):
        async def patch_script(expected_revision, edits, completed_steps=None, remaining_steps=None, variables=None, completion=None):
            self._efficiency_stats['patch_calls'] += 1
            self._efficiency_stats['draft_submitted_chars'] += len(json.dumps(edits, ensure_ascii=False))
            try:
                candidate = apply_script_edits(
                    self._last_valid_script, current_revision=self._artifact['revision'],
                    expected_revision=expected_revision, edits=edits, max_chars=_MAX_SCRIPT_CHARS,
                )
            except ValueError as exc:
                return {'status': 'rejected', 'revision': self._artifact['revision'], 'message': str(exc)}
            feedback = self._consider_candidate(
                candidate,
                completed_steps=self._artifact['completed_steps'] if completed_steps is None else completed_steps,
                remaining_steps=self._artifact['remaining_steps'] if remaining_steps is None else remaining_steps,
                variables=self._artifact['variables'] if variables is None else variables,
                completion=self._artifact['completion'] if completion is None else completion,
                source='patch_tool',
            )
            if feedback['status'] == 'accepted':
                if not await self._persist_checkpoint(force=True):
                    return {**feedback, 'status': 'unsaved', 'error_code': 'CHECKPOINT_FAILED', 'message': self._checkpoint_failure}
                self._efficiency_stats['patch_accepted'] += 1
                self._efficiency_stats['patch_result_chars'] += len(candidate)
            return feedback
        return StructuredTool.from_function(
            coroutine=patch_script, name='patch_script_draft', args_schema=ScriptPatchInput,
            description=(
                '增量保存草稿：expected_revision 必须匹配；edits 中 old 在当前代码恰好出现一次，替换为 new。'
                '所有项通过后才保存；省略进度字段表示保留。先用 read_script_draft 获取准确片段，勿猜测；'
                '完成待办时需同时修改对应代码标记和 remaining_steps，不自动宣称已验证。'
            ),
        )

    def _consider_candidate(
        self,
        code: str,
        *,
        completed_steps: list[str],
        remaining_steps: list[str],
        variables: list[dict[str, Any]],
        completion: str,
        source: str,
    ) -> dict[str, Any]:
        candidate = str(code or '').strip()
        if len(candidate) > _MAX_SCRIPT_CHARS:
            # Direct model-text fallbacks do not pass through ScriptSaveInput,
            # so enforce the same source-size contract before storing or
            # parsing an oversized candidate.  Do not retain a truncated copy
            # as though it were a usable draft.
            feedback = {
                'status': 'rejected', 'source': source,
                'error_code': 'SCRIPT_TOO_LONG',
                'message': f'候选 Python 草稿超过 {_MAX_SCRIPT_CHARS} 字符，已拒绝保存并保留原草稿。',
                'retained_revision': self._artifact['revision'],
                'retained_completion': self._artifact['completion'],
            }
            self._latest_candidate_feedback = feedback
            return feedback
        self._latest_candidate = candidate
        report = self._quality_report(candidate)
        blockers = list(report.get('blockers') or [])
        normalized_completion = completion if completion in {'partial', 'complete'} else 'partial'
        remaining = self._string_list(remaining_steps)
        completed = self._string_list(completed_steps)
        normalized_variables = self._variables(variables)
        auto_pending_step = False
        if remaining:
            normalized_completion = 'partial'
        # A quality report can disprove completeness (for example no assertion),
        # but its positive result never proves the business flow actually ran.
        if report.get('completion') != 'complete':
            normalized_completion = 'partial'
        assertion_state = report.get('assertion_state') or {}
        pending_items = assertion_state.get('pending') if isinstance(assertion_state, dict) else []
        pending_items = pending_items if isinstance(pending_items, list) else []
        has_pending_step = any(item.get('kind') == 'step' for item in pending_items if isinstance(item, dict))
        if normalized_completion != 'complete':
            # The assertion-state parser only reports tokenizer COMMENT tokens.
            # A marker-looking string literal must not suppress a real marker.
            # The platform never removes a real marker automatically; a model
            # or user may explicitly remove one only after resolving that item.
            if remaining and not has_pending_step:
                candidate = self._append_pending_step(candidate, remaining[0])
                auto_pending_step = True
            elif not remaining and pending_items:
                reasons = self._pending_reasons(pending_items)
                remaining = reasons or ['草稿保留了待补充项，请根据对应标记完成后重新检查。']
            elif not remaining and int(assertion_state.get('confirmed_count') or 0) == 0:
                reason = '草稿尚无真实断言，需补充可验证结果。'
                candidate = self._append_pending_assertion(candidate, reason)
                remaining = [reason]
            elif not remaining and not (
                source in {'code_only_model', 'final_text_fallback'}
                and report.get('completion') == 'complete'
            ):
                # A static-complete model-text candidate is still unverified
                # until the independent runner executes it.  Keep its partial
                # status, but do not manufacture a generic pending step solely
                # from the fallback's fixed ``completion='partial'``.
                reason = '智能体未确认完成，未说明具体缺少项。'
                candidate = self._append_pending_step(candidate, reason)
                remaining = [reason]
                auto_pending_step = True
            report = self._quality_report(candidate)
            blockers = list(report.get('blockers') or [])
        if blockers:
            feedback = {
                'status': 'rejected', 'source': source,
                'retained_revision': self._artifact['revision'],
                'retained_completion': self._artifact['completion'],
                'static_feedback': self._bounded_report(report, blockers=blockers),
            }
            self._latest_candidate_feedback = feedback
            return feedback
        changed = candidate != self._last_valid_script
        self._last_valid_script = candidate
        if changed:
            self._artifact['revision'] += 1
        self._artifact.update({
            'completion': normalized_completion,
            'completed_steps': completed,
            'remaining_steps': remaining,
            'variables': normalized_variables,
        })
        self._save_count += 1
        if source != 'entry_seed':
            self._seed_is_current = False
        feedback = {
            'status': 'accepted', 'revision': self._artifact['revision'],
            'completion': normalized_completion,
            'pending_step_inserted': auto_pending_step,
            'static_feedback': self._bounded_report(report),
        }
        self._latest_candidate_feedback = feedback
        return feedback

    async def _submit_final_text_candidate(self) -> None:
        # A locally saved draft is authoritative.  An extra final reply must
        # not downgrade or replace it merely because the reply is incomplete.
        if (self._last_valid_script and not self._seed_is_current) or not self._model_output_for_extraction:
            return
        candidate = extract_python_from_output(self._model_output_for_extraction)
        if not candidate or candidate.strip() == self._last_valid_script:
            return
        feedback = self._consider_candidate(
            candidate,
            completed_steps=self._artifact['completed_steps'],
            # Pending markers in this candidate, not an earlier artifact,
            # describe what remains after a text-only repair.
            remaining_steps=[],
            variables=self._artifact['variables'],
            completion='partial',
            source='final_text_fallback',
        )
        if feedback['status'] == 'accepted':
            self._warnings.append('最终文本草稿仅作为增量保存失败时的 partial 回退，未声明已完成。')
        elif feedback.get('error_code') == 'SCRIPT_TOO_LONG':
            self._warnings.append(str(feedback['message']))
            self._candidate_error_code = str(feedback['error_code'])
            self._candidate_error_message = str(feedback['message'])

    async def _generate_code_only(self) -> ScriptExplorationResult:
        """Ask the configured model to repair only callback-owned saved evidence.

        No MCP client, browser tool, or MCPAgent is constructed in this path.
        A missing draft still gets one bounded model repair attempt from the
        saved trace; it becomes ``NO_SCRIPT_DRAFT`` only if that cannot yield
        a syntactically saveable, evidence-grounded script.
        """
        diagnostics = self._quality_report(self._last_valid_script) if self._last_valid_script else {
            'status': 'needs_review', 'blockers': [], 'warnings': [],
            'assertion_state': {}, 'completion': 'partial',
        }
        prompt = json.dumps({
            'mode': 'code_only',
            'brief': self._brief,
            'target_url': self._target_url,
            'saved_trace': compact_saved_evidence(self._saved_trace_data),
            'existing_script_draft': self._last_valid_script,
            'diagnostics': diagnostics,
            'repair_diagnostics': self._saved_repair_diagnostics,
            'rules': [
                '只能整理、修复已有草稿和 saved_trace 中实际观察到的操作。',
                '不得创建 MCP/client/browser，不得补造未知定位器、按钮、断言或业务操作。',
                '输出完整 Python 草稿；保留或补充顶部中文场景说明和步骤注释。',
                'page.goto 必须使用完整 HTTP(S) 网址，首次打开 target_url，保留原路径、参数和 # 路由；不依赖 base_url 或测试环境。未知操作或断言必须保留 PENDING_STEP 或 PENDING_ASSERTION 注释。',
                '每条真实的 Python assert 或 await expect(...).to_*/not_to_* 前必须紧邻写 # 验证：简洁中文业务结果；不得 print 平台的通过或测试完成日志，统一执行器会输出。',
            ],
        }, ensure_ascii=False)
        try:
            deadline = time.monotonic() + self.exploration_timeout_seconds
            model_result = await self._await_task(
                asyncio.create_task(self.llm_model.ainvoke(prompt)), deadline,
            )
            self._record_model_output(model_result)
            candidate = extract_python_from_output(self._model_output_for_extraction)
            if candidate:
                feedback = self._consider_candidate(
                    candidate,
                    completed_steps=self._artifact['completed_steps'],
                    # Only pending markers still present in the current
                    # candidate may carry into the repaired artifact.
                    remaining_steps=[],
                    variables=self._artifact['variables'],
                    # Repair candidates may already be complete.  Static quality
                    # never proves that claim; the independent runner does.
                    completion='complete' if self._brief.get('repair_only') else 'partial',
                    source='code_only_model',
                )
                if feedback['status'] == 'rejected':
                    self._warnings.append(str(feedback.get(
                        'message', 'code_only 模型候选未通过静态检查，已保留原草稿。',
                    )))
                    if feedback.get('error_code') == 'SCRIPT_TOO_LONG':
                        self._candidate_error_code = str(feedback['error_code'])
                        self._candidate_error_message = str(feedback['message'])
            else:
                self._warnings.append('code_only 模型未返回可提取的 Python 草稿，已保留原草稿。')
            if not await self._persist_checkpoint(force=True):
                return self._result('CHECKPOINT_FAILED', self._checkpoint_failure)
            return self._result(self._candidate_error_code, self._candidate_error_message)
        except Exception as exc:
            logger.exception('基于证据整理脚本中断: generation_id=%s', self.generation_id)
            error_code = exc.error_code if isinstance(exc, ScriptExplorationAgentError) else _classify_mcp_error(exc)
            message = str(exc) if isinstance(exc, ScriptExplorationAgentError) else _get_mcp_error_message(exc)
            self._termination_reason = error_code
            self._final_message = self._final_message or message
            await self._persist_checkpoint(force=True)
            return self._result(error_code, message)

    async def _await_task(self, task: asyncio.Task, deadline: float):
        while not task.done():
            if await self._is_cancelled():
                await self._cancel_task(task)
                raise ScriptExplorationAgentError('TASK_CANCELLED', '用户已取消任务。')
            if time.monotonic() >= deadline:
                await self._cancel_task(task)
                raise ScriptExplorationAgentError('exploration_timeout', '页面探索已达到总时限。')
            if self._guard is not None and self._guard.terminal_error is not None:
                await self._cancel_task(task)
                raise self._guard.terminal_error
            if self._checkpoint_failure:
                await self._cancel_task(task)
                raise ScriptExplorationAgentError('CHECKPOINT_FAILED', self._checkpoint_failure)
            await self._persist_checkpoint()
            await asyncio.wait({task}, timeout=0.25)
        result = await task
        # A fast graph can finish between polling ticks. An automatic read may
        # already have hit a guard even if the model immediately says "done".
        if self._guard is not None and self._guard.terminal_error is not None:
            raise self._guard.terminal_error
        if self._checkpoint_failure:
            raise ScriptExplorationAgentError('CHECKPOINT_FAILED', self._checkpoint_failure)
        if time.monotonic() >= deadline:
            raise ScriptExplorationAgentError('exploration_timeout', '页面探索已达到总时限。')
        return result

    async def _is_cancelled(self) -> bool:
        return bool(await self._async_cancel_check())

    @staticmethod
    async def _cancel_task(task: asyncio.Task) -> None:
        if task.done():
            # The guard can fail the running task in the same event-loop turn
            # in which we notice its terminal state.  Consume that exception
            # before returning the guard's original reason to the caller.
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _request_checkpoint(self) -> None:
        self._checkpoint_requested = True

    async def _persist_checkpoint(self, *, force: bool = False) -> bool:
        if self.checkpoint_callback is None:
            self._durable_script = self._last_valid_script
            self._durable_artifact = deepcopy(self._artifact)
            return True
        if self._checkpoint_failure:
            return False
        now = time.monotonic()
        if not force and (
            not self._checkpoint_requested or now - self._last_checkpoint_at < _CHECKPOINT_INTERVAL_SECONDS
        ):
            return True
        self._checkpoint_requested = False
        self._last_checkpoint_at = now
        payload = {
            'script_draft': self._last_valid_script,
            'snapshot': self._snapshot(),
        }
        try:
            saved = await sync_to_async(self.checkpoint_callback, thread_sensitive=True)(payload)
            if saved is False:
                raise RuntimeError('checkpoint_callback 返回 False')
            persisted_artifact = payload['snapshot']['artifact']
            if persisted_artifact['revision'] >= self._durable_artifact['revision']:
                self._durable_script = payload['script_draft']
                self._durable_artifact = deepcopy(persisted_artifact)
            return True
        except Exception:
            logger.warning('v5 草稿 checkpoint 失败，停止探索并恢复最后持久化草稿；失败候选保留用于诊断。', exc_info=True)
            self._last_valid_script = self._durable_script
            self._artifact = deepcopy(self._durable_artifact)
            self._checkpoint_failure = '草稿 checkpoint 未保存，任务已停止；请以最后一次成功持久化版本为准。'
            self._termination_reason = 'CHECKPOINT_FAILED'
            self._warnings.append(self._checkpoint_failure)
            return False

    def _snapshot(self) -> dict[str, Any]:
        if self._guard is None:
            trace_data = self._saved_trace_data
            stats = dict(trace_data.get('tool_stats') or {})
            events = list(trace_data.get('events') or [])
            page_states = list(trace_data.get('page_states') or [])
            locator_evidence = list(trace_data.get('locator_evidence') or [])
        else:
            stats = self._guard.get_stats()
            data = self._trace_recorder.evidence_snapshot()
            stats = {
                    **stats,
                    'model_calls': self._guard.model_call_count,
                    'browser_call_limit': MCP_BROWSER_TOOL_CALL_LIMIT,
                    'model_call_limit': MCP_MAX_STEPS,
            }
            events = data['events']
            page_states = data['page_states']
            locator_evidence = data['locator_evidence']
        stats['efficiency'] = dict(self._efficiency_stats)
        raw_output = self._raw_model_output
        termination_reason = self._termination_reason or str(stats.get('termination_reason') or '')
        return {
            'schema_version': 5,
            'trace_path': self._trace_path or self._saved_trace_data.get('trace_path', ''),
            'prior_attempts': self._prior_attempts,
            'target_url': self._target_url,
            'events': events,
            'page_states': page_states,
            'locator_evidence': locator_evidence,
            'tool_stats': stats,
            'termination_reason': termination_reason,
            'failure_context': failure_context(events, termination_reason, self._final_message),
            'final_message': self._final_message,
            'model_output_raw': raw_output,
            'model_output_summary': self._summary(raw_output),
            'warnings': list(dict.fromkeys(self._warnings)),
            'draft_state': {
                'last_valid_script': self._last_valid_script,
                'latest_candidate': self._latest_candidate,
                'latest_candidate_feedback': dict(self._latest_candidate_feedback),
            },
            'repair_diagnostics': self._saved_repair_diagnostics,
            'artifact': dict(self._artifact),
        }

    def _result(self, error_code: str = '', error_message: str = '') -> ScriptExplorationResult:
        completion = self._artifact['completion']
        if self._artifact['remaining_steps']:
            completion = 'partial'
        if completion == 'unknown' and self._last_valid_script:
            completion = 'partial'
        if not self._last_valid_script and not error_code:
            error_code, error_message = 'NO_SCRIPT_DRAFT', '智能体没有保存可用 Python 草稿。'
        return ScriptExplorationResult(
            script_draft=self._last_valid_script,
            snapshot=self._snapshot(),
            error_code=error_code,
            error_message=error_message,
            final_message=self._final_message,
            completion=completion,
        )

    def _record_model_output(self, value: Any) -> None:
        text = self._output_text(value)
        self._model_output_for_extraction = text
        self._raw_model_output = self._bounded_raw(text)
        self._final_message = self._summary(self._raw_model_output)

    def _prompt(self) -> str:
        return json.dumps({
            'brief': self._brief,
            'target_url': self._target_url,
            'saved_snapshot': compact_saved_evidence(self._saved_trace_data),
            'existing_script_draft': self._last_valid_script if len(self._last_valid_script) <= 16000 else '草稿较长，使用 read_script_draft 分段读取当前代码。',
            'artifact': self._artifact,
            'scope': {
                'allow_test_data_writes': bool(self._brief.get('allow_test_data_writes')),
                'explicit_read_only': bool(self._brief.get('explicit_read_only')),
                'forbidden_actions': self._brief.get('forbidden_actions') or [],
            },
        }, ensure_ascii=False)

    def _restore_snapshot(self, snapshot: dict | None) -> None:
        value = snapshot if isinstance(snapshot, dict) else {}
        self._saved_trace_data = dict(value)
        self._prior_attempts = [item for item in value.get('prior_attempts', []) if isinstance(item, dict)] if isinstance(value.get('prior_attempts'), list) else []
        for key, default in (
            ('events', []), ('page_states', []), ('locator_evidence', []), ('tool_stats', {}),
        ):
            if not isinstance(self._saved_trace_data.get(key), type(default)):
                self._saved_trace_data[key] = default
        artifact = value.get('artifact') if isinstance(value.get('artifact'), dict) else {}
        try:
            revision = max(0, int(artifact.get('revision', 0)))
        except (TypeError, ValueError):
            revision = 0
        completion = str(artifact.get('completion') or 'unknown')
        self._artifact.update({
            'revision': revision,
            'completion': completion if completion in {'partial', 'complete', 'unknown'} else 'unknown',
            'completed_steps': self._string_list(artifact.get('completed_steps') or []),
            'remaining_steps': self._string_list(artifact.get('remaining_steps') or []),
            'variables': self._variables(artifact.get('variables') or []),
        })
        draft_state = value.get('draft_state') if isinstance(value.get('draft_state'), dict) else {}
        self._restored_last_valid_script = str(draft_state.get('last_valid_script') or '').strip()
        self._latest_candidate = str(draft_state.get('latest_candidate') or '').strip()
        feedback = draft_state.get('latest_candidate_feedback')
        self._latest_candidate_feedback = dict(feedback) if isinstance(feedback, dict) else {}
        self._saved_repair_diagnostics = value.get('repair_diagnostics', value.get('diagnostics', {}))

    def _install_entry_seed(self) -> None:
        """Create the smallest honest draft before any browser activity.

        It deliberately contains no locator, interaction, or assertion.  It
        is a durability fallback only; the first accepted model/tool draft
        replaces it normally and may then become the final authority.
        """

        title = self._safe_comment_text(self._brief.get('title') or 'WebUI 探索草稿', 160)
        objective = self._safe_comment_text(
            self._brief.get('original_user_target') or self._brief.get('objective') or '原目标待探索',
            500,
        )
        reason = f'原目标待探索：{objective}；当前仅生成入口，尚未观察页面元素或业务结果。'
        self._last_valid_script = (
            repr(f'场景：{title}\n目标：{objective}') + '\n\n'
            'async def run(page, variables):\n'
            '    # 步骤 1：进入已知入口（尚未确认页面状态）\n'
            f"    await page.goto({self._target_url!r})\n"
            f'    {_PENDING_STEP_PREFIX} {json.dumps({"reason": reason}, ensure_ascii=False)}\n'
        )
        self._latest_candidate = self._last_valid_script
        self._artifact.update({
            'revision': max(1, int(self._artifact.get('revision') or 0)),
            'completion': 'partial',
            'completed_steps': [],
            'remaining_steps': [reason],
        })
        self._seed_is_current = True
        self._latest_candidate_feedback = {
            'status': 'accepted', 'source': 'entry_seed', 'completion': 'partial',
            'message': '已保存仅含入口的草稿；页面元素、业务操作与断言仍待真实探索。',
        }

    @staticmethod
    def _string_list(values: list[str]) -> list[str]:
        return [str(item).strip() for item in values if str(item).strip()][:100]

    @staticmethod
    def _variables(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in values[:100]:
            if not isinstance(item, dict) or not str(item.get('name') or '').strip():
                continue
            result.append({
                'name': str(item['name']).strip()[:128],
                'value': item.get('value'),
                'is_secret': bool(item.get('is_secret')),
                'required': bool(item.get('required')),
                'description': str(item.get('description') or '').strip()[:500],
            })
        return result

    def _pending_reasons(self, pending_items: list[Any]) -> list[str]:
        reasons: list[str] = []
        seen: set[str] = set()
        for item in pending_items:
            if not isinstance(item, dict):
                continue
            reason = str(item.get('reason') or '').strip()
            if reason and reason not in seen:
                seen.add(reason)
                reasons.append(reason)
        return reasons[:100]

    def _quality_report(self, script: str) -> dict[str, Any]:
        try:
            report = evaluate_draft(script, target_url=self._target_url, snapshot=self._snapshot_without_quality())
        except Exception as exc:
            return {
                'status': 'needs_review',
                'blockers': [{'level': 'blocker', 'code': 'DRAFT_QUALITY_ERROR', 'message': str(exc), 'line': None}],
                'warnings': [], 'assertion_state': {}, 'completion': 'partial',
            }
        return report if isinstance(report, dict) else {
            'status': 'needs_review',
            'blockers': [{'level': 'blocker', 'code': 'DRAFT_QUALITY_INVALID', 'message': '质量检查返回格式无效。', 'line': None}],
            'warnings': [], 'assertion_state': {}, 'completion': 'partial',
        }

    def _snapshot_without_quality(self) -> dict[str, Any]:
        # Avoid recursive quality -> snapshot -> quality calls while still
        # giving the paired quality module current, callback-owned evidence.
        if self._guard is None:
            return {'schema_version': 5, **self._saved_trace_data}
        trace = self._trace_recorder.build(tool_stats=self._guard.get_stats())
        data = trace.model_dump(mode='json')
        return {
            'schema_version': 5, 'target_url': self._target_url,
            'events': data['events'], 'page_states': data['page_states'],
            'locator_evidence': data['locator_evidence'], 'tool_stats': data['tool_stats'],
        }

    @staticmethod
    def _bounded_report(report: dict[str, Any], *, blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            'status': report.get('status', 'needs_review'),
            'blockers': list(blockers if blockers is not None else report.get('blockers') or [])[:12],
            'warnings': list(report.get('warnings') or [])[:12],
            'assertion_state': dict(report.get('assertion_state') or {}),
            'completion': report.get('completion', 'partial'),
        }

    @staticmethod
    def _output_text(value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ('output', 'result', 'content', 'text', 'message'):
                if key in value:
                    return ScriptExplorationAgent._output_text(value[key])
            return json.dumps(value, ensure_ascii=False, default=str)
        if isinstance(value, (list, tuple)):
            return '\n'.join(ScriptExplorationAgent._output_text(item) for item in value)
        if hasattr(value, 'content'):
            return ScriptExplorationAgent._output_text(value.content)
        return str(value)

    @staticmethod
    def _bounded_raw(text: str) -> str:
        sanitized = _SCREENSHOT_DATA_RE.sub('<screenshot-data>', str(text or ''))
        sanitized = _BASE64_RE.sub('<base64-omitted>', sanitized)
        return sanitized[:_RAW_MODEL_OUTPUT_LIMIT]

    @staticmethod
    def _summary(text: str) -> str:
        return re.sub(r'\s+', ' ', str(text or '')).strip()[:_MODEL_OUTPUT_SUMMARY_LIMIT]

    @staticmethod
    def _append_pending_step(script: str, reason: str) -> str:
        payload = json.dumps({'reason': str(reason or '待根据已保存 trace 补充。')}, ensure_ascii=False)
        return f'{script.rstrip()}\n\n{_PENDING_STEP_PREFIX} {payload}\n'

    @staticmethod
    def _append_pending_assertion(script: str, reason: str) -> str:
        payload = json.dumps({
            'criterion': '补充可验证结果',
            'reason': str(reason or '草稿尚无真实断言，需补充可验证结果。'),
        }, ensure_ascii=False)
        return f'{script.rstrip()}\n\n{_PENDING_ASSERTION_PREFIX} {payload}\n'

    @staticmethod
    def _safe_comment_text(value: Any, limit: int) -> str:
        return re.sub(r'\s+', ' ', str(value or '')).strip().replace('"""', "''")[:limit] or '待探索'
