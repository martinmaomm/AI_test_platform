"""Read-only Playwright MCP exploration for the V2 generation pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
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
from mcp_use import MCPAgent, MCPClient

from .generation_contracts import (
    ExplorationSnapshot,
    GenerationContractError,
    parse_exploration_snapshot_json,
)
from .generation_security import redact_metadata

logger = logging.getLogger(__name__)

EXPLORER_CONSTRAINTS = f"""你只负责只读页面探索，绝对不要生成 Python、JavaScript、Markdown 或测试脚本。
所有 playwright_navigate 调用必须传 JSON 布尔值 `headless: true`。
浏览器工具总数最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次，智能体最多 {MCP_MAX_STEPS} 步。
允许打开页面、读取可见文本、打开菜单/Tab/查询条件/表单弹窗；如提供登录信息，只能用于本次登录。
禁止提交新增、编辑、删除、审批、付款、发布、上传、下载等任何业务写操作。
不得输出用户名、密码、Token、Cookie、HTML、截图 Base64 或完整 URL。
优先通过页面导航、打开菜单和打开表单解决 discovery_targets；不得因为探索前不知道字段、入口、提示或路径就要求用户回答。
只有完成只读探索后仍无法从页面证据确定的问题，才写入 unresolved_questions。
最终只输出一个 JSON 对象，字段必须是 start_url_path、visited_paths、page_states、elements、navigation_paths、step_evidence、unresolved_steps、unresolved_questions、warnings、tool_stats。"""

_WRITE_ACTION_MARKERS = (
    '提交', '保存', '确认删除', '删除', '审批', '付款', '支付', '发布', '上传',
    '下载', 'submit', 'save', 'delete', 'approve', 'pay', 'publish', 'upload',
    'download',
)


class ReadOnlyMCPBrowserToolGuard(MCPBrowserToolGuard):
    """Keep legacy budgets while blocking definite business-write actions."""

    def on_tool_start(self, serialized, input_str, *, inputs=None, **kwargs):
        tool_name = str((serialized or {}).get('name') or '').strip().lower()
        input_text = self._read_only_input_text(inputs, input_str)
        if tool_name in {'playwright_upload_file', 'playwright_evaluate'}:
            self._raise_guard(
                'read_only_violation',
                '只读探索不允许上传文件或执行页面脚本。请在后续脚本执行阶段处理该操作。',
            )
        if tool_name.endswith('_press_key') and any(
            key in input_text for key in ('enter', 'numpadenter')
        ):
            self._raise_guard(
                'read_only_violation',
                '只读探索不允许通过 Enter 提交表单，以防止新增或编辑数据被写入。',
            )
        if tool_name.endswith(('_click', '_press_key')) and any(
            marker in input_text for marker in _WRITE_ACTION_MARKERS
        ):
            self._raise_guard(
                'read_only_violation',
                '只读探索检测到可能提交业务写操作，已终止以保护现有数据。',
            )
        return super().on_tool_start(serialized, input_str, inputs=inputs, **kwargs)

    @staticmethod
    def _read_only_input_text(inputs: Any, input_str: str) -> str:
        if isinstance(inputs, dict):
            try:
                return json.dumps(inputs, ensure_ascii=False).lower()
            except (TypeError, ValueError):
                return str(inputs).lower()
        return str(input_str or '').lower()


class MCPPageExplorerError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
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
        pom_context: str,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.llm_model = llm_model
        self.mcp_config = mcp_config
        self.pom_context = pom_context
        self.cancel_check = cancel_check or (lambda: False)
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)

    async def explore(
        self,
        *,
        scenario,
        start_path: str,
        target_url_safe: str,
        temporary_credentials: dict[str, str] | None = None,
    ) -> ExplorationSnapshot:
        if self.cancel_check():
            raise MCPPageExplorerError('TASK_CANCELLED', '用户已取消任务。')

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
        )
        return await self._explore_with_prompt(prompt, start_path)

    async def _explore_with_prompt(self, prompt: str, start_path: str) -> ExplorationSnapshot:
        # The directed supplement has its own bounded budget.  It is invoked at
        # most once by the orchestrator and never retries an MCP failure.
        self.guard = ReadOnlyMCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        client = None
        started_at = time.monotonic()
        try:
            client = MCPClient.from_dict(self.mcp_config)
            await client.create_all_sessions()
            agent = MCPAgent(
                llm=self.llm_model,
                client=client,
                max_steps=MCP_MAX_STEPS,
                additional_instructions=EXPLORER_CONSTRAINTS,
                callbacks=[self.guard],
            )
            with suppress_mcp_raw_query_logs():
                output = await self._run_with_cancel(agent, prompt)
            return self._parse_snapshot(output, start_path, time.monotonic() - started_at)
        except MCPPageExplorerError:
            raise
        except Exception as exc:
            error_kind = _classify_mcp_error(exc)
            raise MCPPageExplorerError(error_kind, _get_mcp_error_message(exc)) from exc
        finally:
            if client is not None:
                try:
                    await client.close_all_sessions()
                except Exception:
                    logger.warning('V2 MCP 会话清理失败', exc_info=True)

    async def _run_with_cancel(self, agent, prompt: str) -> str:
        run_task = asyncio.create_task(agent.run(prompt))
        while not run_task.done():
            if self.cancel_check():
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass
                raise MCPPageExplorerError('TASK_CANCELLED', '用户已取消任务。')
            await asyncio.sleep(0.25)
        return await run_task

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
            'project_pom_candidates': self.pom_context,
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
            'discovery_targets': list(dict.fromkeys([
                *scenario.discovery_targets,
                *scenario.ambiguities,
            ])),
            'project_pom_candidates': self.pom_context,
            'login_context': login_context,
            'instruction': '只补充 requested_step_ids 的页面证据；不要重新探索完整流程，不要执行写操作。',
        }, ensure_ascii=False)

    def _parse_snapshot(self, raw_output: str, start_path: str, duration_seconds: float) -> ExplorationSnapshot:
        if 'async def' in str(raw_output).lower() or '```python' in str(raw_output).lower():
            raise MCPPageExplorerError('SCRIPT_FORMAT_INVALID', 'MCP 探索器返回了脚本而非结构化页面证据。')
        try:
            payload = json.loads(str(raw_output).strip().removeprefix('```json').removesuffix('```').strip())
        except (TypeError, ValueError) as exc:
            raise MCPPageExplorerError('EVIDENCE_INSUFFICIENT', 'MCP 未返回可解析的页面探索证据。') from exc
        stats = self.guard.get_stats()
        payload['start_url_path'] = start_path
        payload['tool_stats'] = {
            'total_tool_calls': stats['total_tool_calls'],
            'tool_counts': stats['tool_counts'],
            'failed_tool_calls': 0,
            'termination_reason': stats['termination_reason'],
            'duration_seconds': round(duration_seconds, 3),
        }
        try:
            return parse_exploration_snapshot_json(json.dumps(redact_metadata(payload), ensure_ascii=False))
        except GenerationContractError as exc:
            raise MCPPageExplorerError('EVIDENCE_INSUFFICIENT', str(exc)) from exc
