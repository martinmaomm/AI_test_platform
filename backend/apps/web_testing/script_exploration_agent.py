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
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.tools import StructuredTool
from mcp_use import MCPClient
from pydantic import BaseModel, ConfigDict, Field

from ai_core.mcp_agent_budget import BudgetedMCPAgent as MCPAgent
from ai_core.webui_playwright_agent import (
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_MAX_STEPS,
    _classify_mcp_error,
    _get_mcp_error_message,
)
from common.parsers import extract_python_from_output

from .exploration_policy import ExplorationPolicy
from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import ExplorationTraceRecorder
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


logger = logging.getLogger(__name__)

SCRIPT_SAVE_TOOL_NAME = 'aits_save_script'
_CHECKPOINT_INTERVAL_SECONDS = 3.0
_RAW_MODEL_OUTPUT_LIMIT = 20000
_MODEL_OUTPUT_SUMMARY_LIMIT = 2000
_PENDING_STEP_PREFIX = '# AITS_PENDING_STEP:'
_BASE64_RE = re.compile(r'(?<![a-z0-9+/=])[a-z0-9+/]{2048,}={0,2}', re.I)
_SCREENSHOT_DATA_RE = re.compile(r'data:image/[^;,\s]+;base64,[a-z0-9+/=\s]+', re.I)
_LOGIN_MARKERS = ('登录', 'login', 'sign in', 'signin')
_HIDDEN_STYLE_RE = re.compile(
    r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', re.I,
)
_VOID_HTML_TAGS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr',
})


def _mcp_html_payload(output: Any) -> str | None:
    """Extract an HTML payload from known MCP wrappers without evaluating text.

    Some MCP versions stringify a list of ``TextContent`` objects, for example
    ``[TextContent(..., text='HTML content:\\n<html>...')]``.  That is not JSON
    and must never be evaluated.  Parsing its Python representation and reading
    only ``ast.Constant`` string values is sufficient to recover the text field.
    """

    candidates: list[str] = []
    seen: set[int] = set()

    def collect(value: Any) -> None:
        if value is None or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, str):
            candidates.append(value)
            return
        if isinstance(value, dict):
            for key in ('text', 'content', 'output', 'result', 'data'):
                if key in value:
                    collect(value[key])
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
            return
        for attribute in ('text', 'content', 'output', 'result', 'data'):
            if hasattr(value, attribute):
                try:
                    collect(getattr(value, attribute))
                except Exception:
                    continue
        # Keep the diagnostic representation as a last resort.  It is parsed
        # below, never executed.
        candidates.append(str(value))

    def as_html(text: str) -> str | None:
        marker = text.lower().find('html content:')
        if marker >= 0:
            candidate = text[marker + len('html content:'):].lstrip()
            if '<' in candidate:
                return candidate[candidate.find('<'):]
        if re.search(r'<(?:html|body|form|section|main)\b', text, re.I):
            return text[text.find('<'):]
        return None

    collect(output)
    for value in candidates:
        # The repr form contains escaped HTML attribute quotes.  Recover its
        # actual TextContent.text constant before trying to parse markup.
        is_text_content_repr = 'TextContent(' in value
        if not is_text_content_repr:
            if html := as_html(value):
                return html
        # Bounds parsing work on unfamiliar, potentially huge error output.
        if len(value) > 500_000:
            continue
        try:
            tree = ast.parse(value, mode='eval')
        except (SyntaxError, ValueError, TypeError):
            if not is_text_content_repr and (html := as_html(value)):
                return html
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if html := as_html(node.value):
                    return html
        if is_text_content_repr:
            continue
        if html := as_html(value):
            return html
    return None


class _LoginFormVisibilityParser(HTMLParser):
    """Recognize a visible username/password form while respecting ancestors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._stack: list[tuple[str, bool, dict[str, Any] | None]] = []
        self.saw_tag = False

    @staticmethod
    def _is_hidden(attrs: dict[str, str]) -> bool:
        return (
            'hidden' in attrs
            or attrs.get('aria-hidden', '').strip().lower() == 'true'
            or bool(_HIDDEN_STYLE_RE.search(attrs.get('style', '')))
        )

    @staticmethod
    def _has_login_marker(text: str) -> bool:
        normalized = text.lower()
        return any(marker in normalized for marker in _LOGIN_MARKERS)

    def _current_form(self) -> dict[str, Any] | None:
        for _tag, _hidden, form in reversed(self._stack):
            if form is not None:
                return form
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.saw_tag = True
        tag = tag.lower()
        attributes = {str(key).lower(): str(value or '') for key, value in attrs}
        inherited_hidden = self._stack[-1][1] if self._stack else False
        hidden = inherited_hidden or self._is_hidden(attributes)
        form: dict[str, Any] | None = None
        if tag == 'form':
            attr_text = ' '.join(attributes.values())
            form = {
                'hidden': hidden,
                'login_marker': self._has_login_marker(attr_text),
                'username': False,
                'password': False,
                'visible_username': False,
                'visible_password': False,
            }
            self.forms.append(form)
        elif tag == 'input' and (form := self._current_form()) is not None:
            input_text = ' '.join(attributes.values()).lower()
            input_type = attributes.get('type', '').lower()
            is_password = input_type == 'password' or 'password' in input_text or '密码' in input_text
            is_username = any(marker in input_text for marker in (
                'username', 'user-name', '用户名', 'account', 'email', '邮箱',
            ))
            if is_password:
                form['password'] = True
                form['visible_password'] |= not hidden and input_type != 'hidden'
            if is_username:
                form['username'] = True
                form['visible_username'] |= not hidden and input_type != 'hidden'
        if tag not in _VOID_HTML_TAGS:
            self._stack.append((tag, hidden, form))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if form := self._current_form():
            form['login_marker'] |= self._has_login_marker(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return

    def visible_login_form(self) -> bool:
        return any(
            form['login_marker']
            and form['username']
            and form['password']
            and not form['hidden']
            and form['visible_username']
            and form['visible_password']
            for form in self.forms
        )


def _visible_login_form_state(output: Any) -> bool | None:
    """Return a structural login-page decision, or None for non-HTML output."""

    html = _mcp_html_payload(output)
    if not html:
        return None
    parser = _LoginFormVisibilityParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None
    return parser.visible_login_form() if parser.saw_tag else None


EXPLORATION_SCRIPT_CONSTRAINTS = f"""你在一个连续的 Playwright MCP 浏览器上下文中探索并增量编写 Python 草稿。
浏览器调用上限 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，模型调用上限 {MCP_MAX_STEPS} 次；接近预算时停止浏览器操作，使用 aits_save_script 保存当前完整草稿和真实剩余步骤。
每次得到足够的页面证据或修复草稿后，都必须调用 aits_save_script。该工具会返回静态检查反馈；按反馈继续完善，而不是只在最终文本一次性给代码。
aits_save_script 的 code 必须是完整可替换 Python 草稿，保留顶部中文“场景/目标”说明和主要步骤注释，入口为 async def run(page, variables)，不得自行启动或关闭浏览器。脚本 page.goto 必须使用相对路径；MCP 的 playwright_navigate 必须显式传 JSON 布尔值 headless: true。固定数据值和 variables 可以混用，不要求每次 fill 都预先声明 input ref；仅需唯一值时使用 time.time_ns()。原始用户描述不可改写为虚构业务。
任何会改变页面状态的提交、点击、导航或填写后，先用一次可见文本或 HTML 观察确认当前状态，再决定下一步；不得为了写脚本而刷新入口、返回 start_path 或重复已确认的流程。每完成一个业务子步骤（包含登录、导航、提交、验证或清理），立即调用 aits_save_script 持久化最新完整草稿，不要等到最终回复。
只根据真实观察生成 goto、定位器和断言。未实际完成的操作必须在代码中保留 # AITS_PENDING_STEP: {{\"reason\":\"...\"}}；未知断言使用 # AITS_PENDING_ASSERTION: ...。存在 pending step 或 remaining_steps 时不可声称 complete。
不得伪造按钮、页面文字、定位器或断言。禁止 playwright_evaluate、上传、关闭浏览器、外域导航，以及审批、付款、发布、下载等未授权高风险操作。浏览器只可访问本次目标站点。
无需也不得调用任何路径定稿工具或基于 event id 的完成协议。最终回复只简短说明；草稿的权威版本来自 aits_save_script。"""


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

    code: str = Field(min_length=1, max_length=200000)
    completed_steps: list[str] = Field(default_factory=list, max_length=100)
    remaining_steps: list[str] = Field(default_factory=list, max_length=100)
    variables: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    completion: str = Field(default='partial', pattern=r'^(?:partial|complete|unknown)$')


class ScriptExplorationAgentError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class ScriptExplorationToolGuard(ReadOnlyMCPBrowserToolGuard):
    """Use the established safety guard without its old 60-call tail mode.

    The base guard's login heuristic treats any page text containing credentials
    as a login page.  MCP visible-text output can include hidden templates, so
    this subclass accepts login failure evidence only from visible HTML form
    structure.  It intentionally leaves repeat-operation and budget safeguards
    in the base guard intact.
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
        parsed = urlsplit(target_url)
        self._allowed_origin = (
            f'{parsed.scheme.lower()}://{parsed.netloc.lower()}'
            if parsed.scheme and parsed.netloc else ''
        )
        self._progress_notifier = progress_notifier
        self._stop_check = stop_check

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        if reason := self._stop_check():
            raise ScriptExplorationAgentError('CHECKPOINT_FAILED', reason)
        tool_name = str((serialized or {}).get('name') or '').lower()
        if tool_name == 'playwright_navigate' and isinstance(inputs, dict):
            candidate = str(inputs.get('url') or inputs.get('target') or inputs.get('href') or '')
            parsed = urlsplit(candidate)
            origin = f'{parsed.scheme.lower()}://{parsed.netloc.lower()}' if parsed.scheme and parsed.netloc else ''
            if origin and origin != self._allowed_origin:
                self._raise_guard(
                    'external_domain_blocked', '页面探索不允许导航到目标站点以外的域名。',
                    blocked_before_execution=True, tool_name=tool_name,
                )
        return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)

    def _is_login_submission(self, tool_name: str, inputs: Any, input_str: str) -> bool:
        """Keep explicit login submits, never classify every later click as one."""

        text = json.dumps(inputs, ensure_ascii=False, default=str).lower() if isinstance(inputs, dict) else str(input_str or '').lower()
        if tool_name.endswith('_click'):
            return any(marker in text for marker in _LOGIN_MARKERS)
        if tool_name.endswith('_press_key'):
            return (
                'enter' in text
                and self.login_page_detected
                and self.login_form_seen
                and not self.login_verified
            )
        return super()._is_login_submission(tool_name, inputs, input_str)

    def _record_page_check(self, tool_name: str, output: Any, *, failed: bool | None = None):
        """Advance login state only when visible HTML can prove it.

        Plain visible text is deliberately ignored here: pages may expose a
        hidden login template in their text payload.  A structurally visible
        login form can still produce the original two-check login failure.
        Conversely, an HTML page without a visible login form confirms that a
        prior explicit login submit is no longer on that form.
        """

        if tool_name != 'playwright_get_visible_html' or failed is True:
            return
        state = _visible_login_form_state(output)
        if state is None:
            return
        if state:
            self.login_page_detected = True
            self.login_form_seen = True
            if self.login_attempts and not self.login_verified:
                self.login_checks_since_attempt += 1
                if self.login_checks_since_attempt >= 2:
                    self._raise_guard(
                        'login_failed',
                        '登录失败：提交登录后连续两次可见 HTML 页面检查仍显示登录表单，已终止脚本生成。请检查登录流程后重试。',
                        tool_name=tool_name,
                    )
            return
        if self.login_attempts or self.login_form_seen:
            self.login_page_detected = False
            self.login_verified = True
            self.login_checks_since_attempt = 0

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
        self._last_valid_script = ''
        self._latest_candidate = ''
        self._artifact = {
            'revision': 0,
            'completion': 'unknown',
            'completed_steps': [],
            'remaining_steps': [],
            'variables': [],
        }
        self._final_message = ''
        self._raw_model_output = ''
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
        start_path: str,
        target_url: str,
        credentials: dict | None,
        saved_snapshot: dict | None = None,
        script_draft: str = '',
        code_only: bool = False,
    ) -> ScriptExplorationResult:
        self._reset_run_state()
        self._brief = dict(brief or {})
        self._start_path = self._relative_path(start_path)
        self._target_url = str(target_url or '')
        self._restore_snapshot(saved_snapshot)
        self._last_valid_script = str(script_draft or self._restored_last_valid_script or '').strip()
        if self._last_valid_script:
            # A pre-existing draft is a rollback candidate even when it needs
            # repair; a bad later model proposal must never erase it.
            if self._artifact['completion'] == 'unknown':
                self._artifact['completion'] = 'partial'

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
            self._configure_trace(credentials, output_generation_id=output_generation_id)
            deadline = time.monotonic() + self.exploration_timeout_seconds
            client = MCPClient.from_dict(prepare_playwright_mcp_output_config(
                self.mcp_config, output_generation_id,
            ))
            await self._await_task(asyncio.create_task(client.create_all_sessions()), deadline)
            agent = MCPAgent(
                llm=self.llm_model,
                client=client,
                max_steps=MCP_MAX_STEPS,
                additional_instructions=EXPLORATION_SCRIPT_CONSTRAINTS,
                disallowed_tools=list(READ_ONLY_DISABLED_TOOL_MESSAGES),
                callbacks=[self._guard],
            )
            await self._await_task(asyncio.create_task(agent.initialize()), deadline)
            await self._await_task(
                asyncio.create_task(agent.register_local_tools([self._save_tool()])), deadline,
            )
            with suppress_mcp_raw_query_logs():
                model_result = await self._await_task(
                    asyncio.create_task(agent.run(self._prompt(credentials), manage_connector=False)),
                    deadline,
                )
            self._record_model_output(model_result)
            await self._submit_final_text_candidate()
            if not await self._persist_checkpoint(force=True):
                return self._result('CHECKPOINT_FAILED', self._checkpoint_failure)
            return self._result()
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

    def _configure_trace(self, credentials: dict | None, *, output_generation_id: str) -> None:
        explicit_read_only = bool(self._brief.get('explicit_read_only'))
        policy = ExplorationPolicy(
            namespace=f'aits-script-{self.generation_id or "local"}',
            data_scope='scenario_namespace',
            explicit_read_only=explicit_read_only,
            allow_test_data_writes=bool(self._brief.get('allow_test_data_writes')) and not explicit_read_only,
            cleanup_expected=bool(self._brief.get('cleanup_expected')) and not explicit_read_only,
        )
        trace_file = Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp' / f'{output_generation_id}.script-v5.trace.jsonl'
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        self._trace_recorder = ExplorationTraceRecorder(
            self._start_path, runtime_namespace=policy.namespace, trace_file=trace_file,
        )
        runtime_values = {
            str(name): str(value) for name, value in (credentials or {}).items()
            if value is not None
        }
        self._trace_recorder.configure_runtime(
            runtime_values,
            {name: 'credential' for name in runtime_values},
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
        if normalized_completion != 'complete' and _PENDING_STEP_PREFIX not in candidate:
            reason = remaining[0] if remaining else '当前草稿仍需根据已保存 trace 补充未确认操作或断言。'
            candidate = self._append_pending_step(candidate, reason)
            auto_pending_step = True
            if not remaining:
                remaining = [reason]
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
        if (self._last_valid_script and not self._seed_is_current) or not self._raw_model_output:
            return
        candidate = extract_python_from_output(self._raw_model_output)
        if not candidate or candidate.strip() == self._last_valid_script:
            return
        feedback = self._consider_candidate(
            candidate,
            completed_steps=self._artifact['completed_steps'],
            remaining_steps=self._artifact['remaining_steps'] or ['根据已保存 trace 复核最终文本'],
            variables=self._artifact['variables'],
            completion='partial',
            source='final_text_fallback',
        )
        if feedback['status'] == 'accepted':
            self._warnings.append('最终文本草稿仅作为增量保存失败时的 partial 回退，未声明已完成。')

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
            'start_path': self._start_path,
            'saved_trace': self._saved_trace_data,
            'existing_script_draft': self._last_valid_script,
            'diagnostics': diagnostics,
            'repair_diagnostics': self._saved_repair_diagnostics,
            'rules': [
                '只能整理、修复已有草稿和 saved_trace 中实际观察到的操作。',
                '不得创建 MCP/client/browser，不得补造未知定位器、按钮、断言或业务操作。',
                '输出完整 Python 草稿；保留或补充顶部中文场景说明和步骤注释。',
                'page.goto 只能使用相对路径；未知操作或断言必须保留 AITS_PENDING_STEP 或 AITS_PENDING_ASSERTION 注释。',
            ],
        }, ensure_ascii=False)
        try:
            deadline = time.monotonic() + self.exploration_timeout_seconds
            model_result = await self._await_task(
                asyncio.create_task(self.llm_model.ainvoke(prompt)), deadline,
            )
            self._record_model_output(model_result)
            candidate = extract_python_from_output(self._raw_model_output)
            if candidate:
                feedback = self._consider_candidate(
                    candidate,
                    completed_steps=self._artifact['completed_steps'],
                    remaining_steps=self._artifact['remaining_steps'],
                    variables=self._artifact['variables'],
                    completion='partial',
                    source='code_only_model',
                )
                if feedback['status'] == 'rejected':
                    self._warnings.append('code_only 模型候选未通过静态检查，已保留原草稿。')
            else:
                self._warnings.append('code_only 模型未返回可提取的 Python 草稿，已保留原草稿。')
            if not await self._persist_checkpoint(force=True):
                return self._result('CHECKPOINT_FAILED', self._checkpoint_failure)
            return self._result()
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
        return await task

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
            return True
        except Exception:
            logger.warning('v5 草稿 checkpoint 失败，探索继续保留内存中的最后有效草稿。', exc_info=True)
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
            trace = self._trace_recorder.build(
                tool_stats={
                    **stats,
                    'model_calls': self._guard.model_call_count,
                    'browser_call_limit': MCP_BROWSER_TOOL_CALL_LIMIT,
                    'model_call_limit': MCP_MAX_STEPS,
                },
                termination_reason=self._termination_reason or str(stats.get('termination_reason') or ''),
                warnings=self._warnings,
            )
            data = trace.model_dump(mode='json')
            events = data['events']
            page_states = data['page_states']
            locator_evidence = data['locator_evidence']
            stats = data['tool_stats']
        raw_output = self._bounded_raw(self._raw_model_output)
        return {
            'schema_version': 5,
            'start_path': self._start_path,
            'events': events,
            'page_states': page_states,
            'locator_evidence': locator_evidence,
            'tool_stats': stats,
            'termination_reason': self._termination_reason or str(stats.get('termination_reason') or ''),
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
        self._raw_model_output = self._bounded_raw(text)
        self._final_message = self._summary(text)

    def _prompt(self, credentials: dict | None) -> str:
        return json.dumps({
            'brief': self._brief,
            'start_path': self._start_path,
            'target_url': self._target_url,
            'credentials': dict(credentials or {}),
            'saved_snapshot': self._saved_trace_data,
            'existing_script_draft': self._last_valid_script,
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
            f"    await page.goto({self._start_path!r})\n"
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
    def _relative_path(value: str) -> str:
        text = str(value or '/').strip()
        return text if text.startswith('/') else '/'

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

    def _quality_report(self, script: str) -> dict[str, Any]:
        try:
            report = evaluate_draft(script, start_path=self._start_path, snapshot=self._snapshot_without_quality())
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
            'schema_version': 5, 'start_path': self._start_path,
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
    def _safe_comment_text(value: Any, limit: int) -> str:
        return re.sub(r'\s+', ' ', str(value or '')).strip().replace('"""', "''")[:limit] or '待探索'
