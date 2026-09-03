"""
WebUI Playwright智能体
用于生成WebUI测试脚本的智能体
使用mcp-use集成MCP访问能力，支持流式输出
"""

import logging
import asyncio
import hashlib
import json
import os
import re
import shutil
import platform
import subprocess
import threading
from collections import Counter, deque
from typing import TypedDict, Dict, Any, Optional
from datetime import datetime
from django.conf import settings
from django.core.cache import cache
from .models import MCPConfiguration
from langchain_core.callbacks import BaseCallbackHandler
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langgraph.graph import StateGraph, END
from langgraph.errors import GraphRecursionError
from common.websocket import websocket_message_service, send_node_start_notification_helper
from common.parsers import extract_python_from_output
from .model_manager import get_llm_manager
from mcp_use import MCPClient
from .mcp_agent_budget import BudgetedMCPAgent as MCPAgent

logger = logging.getLogger(__name__)

MCP_MAX_STEPS = 100
MCP_BROWSER_TOOL_CALL_LIMIT = 72

MCP_EXPLORATION_CONSTRAINTS = f"""- 调用 `playwright_navigate` 时必须显式传入 JSON 布尔值 `headless: true`，不得省略，也不得传字符串 `\"true\"`。
- 所有浏览器工具调用合计最多 {MCP_BROWSER_TOOL_CALL_LIMIT} 次；仅在缺少必要页面结构、可见文本或定位器时调用工具。
- 获取生成脚本所需的页面结构后，立即停止所有工具调用，只输出完整、可提取的 Python Playwright 脚本。
- 单个业务操作失败后最多补充一次页面检查；生成阶段不要求真实跑通完整业务流程，也不要反复重放已失败的操作。
- 不要为了验证脚本而继续截图、读取 HTML、检查控制台或重复登录；最终回复只能是完整 Python 脚本。"""

MCP_AGENT_ADDITIONAL_INSTRUCTIONS = f"""这是生成任务的系统级稳定性约束，优先级高于用户任务中的探索性要求：
{MCP_EXPLORATION_CONSTRAINTS}
"""

MCP_ERROR_GRAPH_RECURSION = "graph_recursion"
MCP_ERROR_TOOL_PARAMETER = "tool_parameter"
MCP_ERROR_BROWSER = "browser"
MCP_ERROR_RATE_LIMIT = "rate_limit"
MCP_ERROR_TRANSIENT = "transient"
MCP_ERROR_OTHER = "other"
MCP_ERROR_TOOL_BUDGET = "tool_budget"
MCP_ERROR_REPEATED_INTERACTION = "repeated_interaction"
MCP_ERROR_INTERACTION_FAILURE = "interaction_failure"
MCP_ERROR_LOGIN_FAILED = "login_failed"

_PLAYWRIGHT_TOOL_PARAMETER_ERROR_MARKERS = (
    "headless: expected boolean",
    "headless expected boolean",
    "headless must be boolean",
    "headless must be a boolean",
    "invalid headless",
)
_NON_RETRYABLE_MCP_ERROR_MARKERS = (
    "executable doesn't exist",
    "failed to initialize browser",
    "browser executable",
    "failed to launch browser",
    "browser was not found",
    "missing executable",
    "executable file not found",
)
_TRANSIENT_MCP_ERROR_MARKERS = (
    "connection refused",
    "connection reset",
    "connection closed",
    "server disconnected",
    "broken pipe",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)
_HTTP_STATUS_PATTERN = re.compile(r"\b(429|503|504)\b")


class MCPToolGuardError(RuntimeError):
    """不可重试的 MCP 浏览器工具守卫异常。"""

    retryable = False

    def __init__(self, error_kind: str, message: str):
        self.error_kind = error_kind
        super().__init__(message)


_MCP_TOOL_GUARD_ERROR_KINDS = {
    MCP_ERROR_TOOL_BUDGET,
    MCP_ERROR_REPEATED_INTERACTION,
    MCP_ERROR_INTERACTION_FAILURE,
    MCP_ERROR_LOGIN_FAILED,
}

_PAGE_CHECK_TOOLS = {"playwright_get_visible_text", "playwright_get_visible_html"}
_READ_ONLY_BROWSER_TOOLS = _PAGE_CHECK_TOOLS | {
    "playwright_screenshot",
    "playwright_console_logs",
    "browser_console_logs",
}
_LOCATOR_INTERACTION_TOOLS = {
    "playwright_click",
    "playwright_iframe_click",
    "playwright_click_and_switch_tab",
    "playwright_fill",
    "playwright_iframe_fill",
    "playwright_select",
    "playwright_hover",
    "playwright_upload_file",
    "playwright_drag",
    "playwright_press_key",
}
_LOGIN_MARKERS = ("登录", "login", "sign in", "signin")
_SENSITIVE_INPUT_KEY = re.compile(
    r"password|passwd|pwd|token|secret|authorization|api[_-]?key|密码|口令",
    re.IGNORECASE,
)


def _guard_tool_name(serialized: Optional[Dict[str, Any]]) -> str:
    if not isinstance(serialized, dict):
        return ""
    return str(serialized.get("name") or "").strip().lower()


def _guard_input_text(inputs: Any, input_str: str = "") -> str:
    if isinstance(inputs, dict):
        try:
            return json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(inputs)
    return str(input_str or "")


def _normalize_guard_value(value: Any, key: str = "") -> Any:
    if _SENSITIVE_INPUT_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _normalize_guard_value(item_value, str(item_key))
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_guard_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_guard_value(item) for item in value)
    return value


def _normalize_guard_input(inputs: Any, input_str: str = "") -> str:
    source = inputs if isinstance(inputs, dict) else input_str
    normalized = _normalize_guard_value(source)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _guard_output_text(output: Any) -> str:
    if hasattr(output, "content"):
        return _guard_output_text(output.content)
    if isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(output, (list, tuple)):
        return " ".join(_guard_output_text(item) for item in output)
    return str(output or "")


class MCPBrowserToolGuard(BaseCallbackHandler):
    """在 LangChain 工具执行前后限制 MCP 浏览器探索行为。"""

    raise_error = True
    run_inline = True

    def __init__(self, max_tool_calls: int = MCP_BROWSER_TOOL_CALL_LIMIT):
        super().__init__()
        self.max_tool_calls = max_tool_calls
        self.total_tool_calls = 0
        self.tool_call_counts = Counter()
        self.interaction_call_counts = Counter()
        self.failed_tool_calls = 0
        self.blocked_tool_calls = 0
        self.consecutive_interaction_failures = 0
        self.login_page_detected = False
        self.login_form_seen = False
        self.login_attempts = 0
        self.login_checks_since_attempt = 0
        self.login_verified = False
        self.termination_reason = None
        self._terminal_error = None
        self._observed_page_state_fingerprints = {}
        self._page_state_version = 0
        self._active_tools = {}
        self._last_operation = None
        self._last_blocked_operation = None
        self._lock = threading.RLock()

    @staticmethod
    def _is_browser_tool(tool_name: str) -> bool:
        return tool_name.startswith("playwright_") or tool_name == "browser_console_logs"

    @staticmethod
    def _is_interaction_tool(tool_name: str) -> bool:
        return (
            MCPBrowserToolGuard._is_browser_tool(tool_name)
            and tool_name not in _READ_ONLY_BROWSER_TOOLS
        )

    @staticmethod
    def _is_login_page(output: Any) -> bool:
        text = _guard_output_text(output).lower()
        if not text.strip():
            return False
        has_login_marker = any(marker in text for marker in _LOGIN_MARKERS)
        has_username_field = "请输入用户名" in text or "username" in text
        has_password_field = "请输入密码" in text or "password" in text
        has_login_form = (
            (has_username_field and has_password_field)
            or "获取体验账号" in text
            or "login-form" in text
        )
        return has_login_marker and has_login_form

    @staticmethod
    def _is_meaningful_page_check(output: Any) -> bool:
        text = _guard_output_text(output).strip().lower()
        return bool(text) and not MCPBrowserToolGuard._is_failed_output(output)

    @staticmethod
    def _is_failed_output(output: Any) -> bool:
        if isinstance(output, dict) and (
            output.get("error")
            or output.get("isError")
            or output.get("is_error")
            or output.get("status") == "error"
        ):
            return True
        if (
            getattr(output, "isError", False)
            or getattr(output, "is_error", False)
            or getattr(output, "status", None) == "error"
        ):
            return True
        text = _guard_output_text(output).strip().lower()
        return bool(
            re.search(
                r"operation failed|error executing tool|timeout .* exceeded|"
                r"failed to|could not|invalid .*selector|exception|tool[ _-]?error",
                text,
            )
        )

    def _tool_output_failed(self, tool_name: str, output: Any) -> bool:
        """Allow a scoped agent to apply tool-aware callback error semantics."""

        del tool_name
        return self._is_failed_output(output)

    @staticmethod
    def _state_fingerprint(output: Any, *, failed: bool | None = None) -> str | None:
        """Keep an in-memory state marker without retaining page content."""
        if failed is True or (
            failed is None and MCPBrowserToolGuard._is_failed_output(output)
        ):
            return None
        text = re.sub(r"\s+", " ", _guard_output_text(output).strip())
        if not text:
            return None
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

    def _record_observed_state(self, tool_name: str, output: Any, *, failed: bool | None = None):
        # Only page observations establish progress.  Generic tool responses,
        # ToolMessage IDs, screenshots, and alternating HTML/text formats do
        # not demonstrate a changed page or modal state.
        if tool_name not in _PAGE_CHECK_TOOLS:
            return
        if failed is True:
            return
        fingerprint = self._state_fingerprint(output, failed=failed)
        if fingerprint is None:
            return
        previous = self._observed_page_state_fingerprints.get(tool_name)
        self._observed_page_state_fingerprints[tool_name] = fingerprint
        if previous is not None and previous != fingerprint:
            self._page_state_version += 1

    def _is_login_submission(self, tool_name: str, inputs: Any, input_str: str) -> bool:
        text = _guard_input_text(inputs, input_str).lower()
        if tool_name.endswith("_press_key"):
            return "enter" in text and self.login_form_seen
        if tool_name.endswith("_click"):
            return (
                any(marker in text for marker in _LOGIN_MARKERS)
                or (self.login_page_detected and self.login_form_seen)
            )
        if tool_name == "playwright_evaluate":
            return (
                any(marker in text for marker in _LOGIN_MARKERS)
                and any(marker in text for marker in ("fetch", "submit", ".click", "/login"))
            )
        return False

    def _raise_guard(
        self,
        error_kind: str,
        message: str,
        *,
        blocked_before_execution: bool = False,
        tool_name: str = '',
    ):
        if self._terminal_error is None:
            self.termination_reason = error_kind
            if blocked_before_execution:
                self.blocked_tool_calls += 1
                self._last_blocked_operation = {
                    'tool_name': tool_name or 'browser_tool',
                    'call_index': self.total_tool_calls + 1,
                    'status': 'blocked',
                }
            self._terminal_error = MCPToolGuardError(error_kind, message)
        raise self._terminal_error

    @property
    def terminal_error(self) -> MCPToolGuardError | None:
        with self._lock:
            return self._terminal_error

    def _record_interaction_failure(self, tool_name: str):
        self.consecutive_interaction_failures += 1
        if self.consecutive_interaction_failures >= 3:
            self._raise_guard(
                MCP_ERROR_INTERACTION_FAILURE,
                "浏览器定位交互连续失败 3 次，已终止脚本生成。请检查定位器、页面状态或登录结果后重试。",
                tool_name=tool_name,
            )

    def _record_page_check(self, tool_name: str, output: Any, *, failed: bool | None = None):
        meaningful = (
            self._is_meaningful_page_check(output)
            if failed is None
            else bool(_guard_output_text(output).strip()) and not failed
        )
        if (
            tool_name not in _PAGE_CHECK_TOOLS
            or not meaningful
        ):
            return
        if self._is_login_page(output):
            self.login_page_detected = True
            if self.login_attempts and not self.login_verified:
                self.login_checks_since_attempt += 1
                if self.login_checks_since_attempt >= 2:
                    self._raise_guard(
                        MCP_ERROR_LOGIN_FAILED,
                        "登录失败：提交登录后连续两次页面检查仍停留在登录页，已终止脚本生成。请检查登录流程后重试。",
                        tool_name=tool_name,
                    )
        elif self.login_attempts or self.login_form_seen:
            self.login_verified = True
            self.login_checks_since_attempt = 0

    def on_tool_start(
        self,
        serialized: Optional[Dict[str, Any]],
        input_str: str,
        *,
        run_id=None,
        parent_run_id=None,
        inputs=None,
        **kwargs,
    ):
        tool_name = _guard_tool_name(serialized)
        if not self._is_browser_tool(tool_name):
            return
        with self._lock:
            if self._terminal_error is not None:
                raise self._terminal_error
            if self.total_tool_calls >= self.max_tool_calls:
                self._raise_guard(
                    MCP_ERROR_TOOL_BUDGET,
                    f"浏览器工具调用已达到本次任务上限（{self.max_tool_calls} 次），已终止脚本生成。请缩短探索范围后重试。",
                    blocked_before_execution=True,
                    tool_name=tool_name,
                )

            if tool_name == "playwright_fill":
                text = _guard_input_text(inputs, input_str).lower()
                if "password" in text or "密码" in text or "username" in text or "用户名" in text:
                    self.login_form_seen = True

            if self._is_login_submission(tool_name, inputs, input_str) and not self.login_verified:
                if self.login_attempts >= 1:
                    self._raise_guard(
                        MCP_ERROR_LOGIN_FAILED,
                        "登录失败：尚未确认登录成功前再次提交登录，已终止脚本生成。请检查登录流程后重试。",
                        blocked_before_execution=True,
                        tool_name=tool_name,
                    )
                self.login_attempts += 1
                self.login_checks_since_attempt = 0

            interaction_key = None
            if self._is_interaction_tool(tool_name):
                interaction_key = (
                    tool_name,
                    _normalize_guard_input(inputs, input_str),
                    self._page_state_version,
                )
                if self.interaction_call_counts[interaction_key] >= 2:
                    self._raise_guard(
                        MCP_ERROR_REPEATED_INTERACTION,
                        "未观察到页面状态变化，且相同的交互操作及参数已执行 2 次，已终止脚本生成。请检查定位器或操作流程后重试。",
                        blocked_before_execution=True,
                        tool_name=tool_name,
                    )

            self.total_tool_calls += 1
            self.tool_call_counts[tool_name] += 1
            self._last_operation = {
                "tool_name": tool_name,
                "call_index": self.total_tool_calls,
                "status": "started",
            }
            if interaction_key is not None:
                self.interaction_call_counts[interaction_key] += 1
            self._active_tools[run_id] = {
                "tool_name": tool_name,
                "is_locator_interaction": tool_name in _LOCATOR_INTERACTION_TOOLS,
                "call_index": self.total_tool_calls,
            }

    def on_tool_end(self, output: Any, *, run_id=None, parent_run_id=None, **kwargs):
        with self._lock:
            active_tool = self._active_tools.pop(run_id, None)
            if active_tool is None:
                return
            tool_name = active_tool["tool_name"]
            failed = self._tool_output_failed(tool_name, output)
            if self._last_operation and self._last_operation["call_index"] == active_tool["call_index"]:
                self._last_operation["status"] = "failed" if failed else "succeeded"
            if failed:
                self.failed_tool_calls += 1
            if self._terminal_error is not None:
                return
            if active_tool.get("is_locator_interaction"):
                if failed:
                    self._record_interaction_failure(tool_name)
                else:
                    self.consecutive_interaction_failures = 0
            self._record_observed_state(tool_name, output, failed=failed)
            self._record_page_check(tool_name, output, failed=failed)

    def on_tool_error(self, error: BaseException, *, run_id=None, parent_run_id=None, **kwargs):
        with self._lock:
            active_tool = self._active_tools.pop(run_id, None)
            if active_tool is None:
                return
            if self._last_operation and self._last_operation["call_index"] == active_tool["call_index"]:
                self._last_operation["status"] = "failed"
            self.failed_tool_calls += 1
            if self._terminal_error is not None:
                return
            if active_tool["is_locator_interaction"]:
                self._record_interaction_failure(active_tool["tool_name"])

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_tool_calls": self.total_tool_calls,
                "tool_counts": dict(self.tool_call_counts),
                "failed_tool_calls": self.failed_tool_calls,
                "blocked_tool_calls": self.blocked_tool_calls,
                "termination_reason": self.termination_reason,
                "last_operation": dict(self._last_operation) if self._last_operation else None,
                "last_blocked_operation": (
                    dict(self._last_blocked_operation) if self._last_blocked_operation else None
                ),
            }


def _iter_exception_chain(error: BaseException):
    """遍历异常及其 cause/context，兼容被 RuntimeError 包装的底层错误。"""
    current: Optional[BaseException] = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _exception_text(error: BaseException) -> str:
    """获取异常链文本，仅用于错误分类。"""
    return " | ".join(
        str(candidate) for candidate in _iter_exception_chain(error) if str(candidate)
    ).lower()


def _classify_mcp_error(error: BaseException) -> str:
    """将 MCP 执行异常归类为可测试、可重试的稳定类别。"""
    for candidate in _iter_exception_chain(error):
        if isinstance(candidate, MCPToolGuardError):
            return candidate.error_kind
    if any(
        isinstance(candidate, (GraphRecursionError, ModelCallLimitExceededError))
        for candidate in _iter_exception_chain(error)
    ):
        return MCP_ERROR_GRAPH_RECURSION

    error_text = _exception_text(error)
    if "upstream_rate_limited" in error_text or "too many requests" in error_text:
        return MCP_ERROR_RATE_LIMIT
    status_match = _HTTP_STATUS_PATTERN.search(error_text)
    if status_match and status_match.group(1) == "429":
        return MCP_ERROR_RATE_LIMIT
    if any(marker in error_text for marker in _PLAYWRIGHT_TOOL_PARAMETER_ERROR_MARKERS):
        return MCP_ERROR_TOOL_PARAMETER
    if any(marker in error_text for marker in _NON_RETRYABLE_MCP_ERROR_MARKERS):
        return MCP_ERROR_BROWSER
    if (
        (status_match and status_match.group(1) in {"503", "504"})
        or any(
            isinstance(candidate, (ConnectionError, TimeoutError))
            for candidate in _iter_exception_chain(error)
        )
        or any(marker in error_text for marker in _TRANSIENT_MCP_ERROR_MARKERS)
    ):
        return MCP_ERROR_TRANSIENT
    return MCP_ERROR_OTHER


def _is_non_retryable_mcp_error(error: BaseException) -> bool:
    """识别浏览器、递归超限和上游限流等不适合重试的错误。"""
    return _classify_mcp_error(error) in {
        MCP_ERROR_GRAPH_RECURSION,
        MCP_ERROR_TOOL_PARAMETER,
        MCP_ERROR_BROWSER,
        MCP_ERROR_RATE_LIMIT,
    } | _MCP_TOOL_GUARD_ERROR_KINDS


def _get_mcp_error_message(error: BaseException) -> str:
    """将底层 MCP 错误转换为固定的前端提示。"""
    error_kind = _classify_mcp_error(error)
    for candidate in _iter_exception_chain(error):
        if isinstance(candidate, MCPToolGuardError):
            return str(candidate)
    if error_kind == MCP_ERROR_GRAPH_RECURSION:
        return (
            "MCP智能体超过最大执行步数，页面探索未在限制内完成。"
            "请缩短测试描述或减少需要探索的操作后重试。"
        )
    if error_kind == MCP_ERROR_TOOL_PARAMETER:
        return (
            "Playwright MCP工具参数错误：调用 playwright_navigate 时必须传入 JSON boolean "
            "headless: true，不能省略或传字符串 \"true\"。请修正工具参数后重试，"
            "本次任务不会自动重试。"
        )
    if error_kind == MCP_ERROR_BROWSER:
        return (
            "Playwright浏览器启动失败或找不到可执行文件。请确认运行Celery的机器已安装"
            "与当前MCP版本匹配的Chromium，并检查MCP_PLAYWRIGHT_BROWSERS_PATH后重启Celery。"
        )
    if error_kind == MCP_ERROR_RATE_LIMIT:
        return (
            "上游模型服务触发限流（429/UPSTREAM_RATE_LIMITED），本次任务不会自动重试。"
            "请等待限流窗口结束后重试，或切换可用模型、提高服务配额。"
        )
    if error_kind == MCP_ERROR_TRANSIENT:
        return "MCP服务暂时不可用（503/504或临时连接错误），有限重试后仍未恢复，请稍后重试。"
    return "MCP智能体运行失败，请查看Celery日志。"


def _get_non_retryable_mcp_error_message(error: BaseException) -> str:
    """兼容旧调用方：返回已分类的 MCP 错误提示。"""
    return _get_mcp_error_message(error)


def _enforce_script_guarantees(script: str, description: str = '') -> str:
    """Normalize generated code to the shared async ``run(page)`` contract."""
    if not script or not script.strip():
        return script
    from web_testing.script_contract import normalize_for_storage
    return normalize_for_storage(script)


# 定义WebUI Playwright Agent状态数据结构
class WebUIPlaywrightAgentState(TypedDict):
    """WebUI Playwright测试脚本生成Agent的状态数据"""
    description: str                    # 用户需求描述
    url: str                           # 目标URL
    user_id: int                       # 用户ID
    project_id: Optional[int]          # 项目ID
    script_name: Optional[str]         # 脚本名称
    mcp_config: Dict[str, Any]         # MCP服务器配置
    test_case_id: Optional[int]        # 测试用例ID
    steps_info: Optional[str]          # 测试步骤（JSON 或结构化文本）
    expected_result: Optional[str]      # 预期结果
    yaml_test_script: Optional[str]     # 生成的yaml测试脚本
    test_script: Optional[str]          # 转换后的Python测试脚本
    script_id: Optional[int]            # 保存的脚本ID
    current_step: str                   # 当前执行步骤


class WebUIPlaywrightAgent:
    """WebUI Playwright测试脚本生成智能体"""
    
    def __init__(self, user_id: int, user=None, enable_streaming: bool = True):
        self.user = user
        self.user_id = user_id
        self.enable_streaming = enable_streaming
        self.project_id = None
        self.script_name = None
        self.mcp_config = {}
        self.test_case_id = None
        # 外部注入：当前Celery任务ID（用于协作式取消）
        self.celery_task_id: Optional[str] = None
        
        # 修复LoggingProxy问题
        try:
            import sys
            import subprocess
            
            # 保存原始的subprocess.Popen
            original_popen = subprocess.Popen
            
            def patched_popen(*args, **kwargs):
                # 如果stderr是LoggingProxy，替换为subprocess.PIPE
                if 'stderr' in kwargs and hasattr(kwargs['stderr'], '__class__') and 'LoggingProxy' in str(kwargs['stderr'].__class__):
                    kwargs['stderr'] = subprocess.PIPE
                return original_popen(*args, **kwargs)
            
            # 应用补丁
            subprocess.Popen = patched_popen
            logger.info("已应用LoggingProxy修复补丁")
            
        except Exception as e:
            logger.warning(f"应用LoggingProxy修复补丁失败: {e}")
        
        # 启用MCP调试模式
        try:
            import mcp_use
            mcp_use.set_debug(1)  # 设置INFO级别调试
            logger.info("MCP调试模式已启用")
        except Exception as e:
            logger.warning(f"启用MCP调试模式失败: {e}")
        
        # 初始化LLM管理器
        try:
            self.llm_manager = get_llm_manager()
            logger.info(f"LLM管理器初始化成功: {self.llm_manager.get_model_info()}")
        except Exception as e:
            logger.error(f"LLM管理器初始化失败: {e}")
            raise RuntimeError(f"LLM管理器初始化失败: {e}") from e
        
        # 初始化MCP客户端
        self.mcp_client = None
        self.mcp_agent = None
        self._mcp_tool_guard = MCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
        # MCP日志handler（避免重复挂载导致日志重复）
        self._mcp_log_handler = None
        # 最近输出去重：只在短窗口内去重，避免刷屏
        self._recent_log_hashes = deque(maxlen=200)
        
        # 构建LangGraph工作流
        self.workflow = self._build_workflow()

    def _log_mcp_tool_guard_stats(self) -> None:
        """记录不包含工具参数和凭据的任务级守卫统计。"""
        tool_guard = getattr(self, "_mcp_tool_guard", None)
        if not tool_guard:
            return
        stats = tool_guard.get_stats()
        logger.info(
            "MCP浏览器工具守卫统计: total=%s, counts=%s, termination_reason=%s",
            stats["total_tool_calls"],
            stats["tool_counts"],
            stats["termination_reason"] or "none",
        )

    def _is_cancelled(self) -> bool:
        """通过cache标记判断是否已请求取消（由停止接口写入）。"""
        if not self.celery_task_id:
            return False
        return bool(cache.get(f"celery:cancel:{self.celery_task_id}"))

    async def _wait_cancel_signal(self, poll_interval: float = 0.5):
        """异步等待取消信号（用于并发取消 mcp_agent.run）。"""
        while True:
            if self._is_cancelled():
                return
            await asyncio.sleep(poll_interval)
    
    
    def _initialize_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """初始化MCP客户端"""
        try:
            # 临时修改sys.stderr以避免LoggingProxy问题
            import sys
            import subprocess
            
            # 保存原始stderr
            original_stderr = sys.stderr
            
            try:
                # 临时将stderr设置为subprocess.PIPE
                sys.stderr = subprocess.PIPE
                
                # 验证MCP配置
                self._validate_mcp_config(config)
                
                # 创建MCP客户端
                client = MCPClient.from_dict(config)
                
                logger.info("MCP客户端创建成功")
                return client
                
            finally:
                # 恢复原始stderr
                sys.stderr = original_stderr
                
        except Exception as e:
            logger.error(f"MCP客户端初始化失败: {e}")
            raise RuntimeError(f"MCP客户端初始化失败: {e}") from e
    
    def _validate_mcp_config(self, config: Dict[str, Any]) -> None:
        """验证MCP配置（增强Windows平台支持）"""
        mcp_servers = config.get('mcpServers', {})
        if not mcp_servers:
            raise ValueError("MCP配置中没有找到mcpServers")
        
        is_windows = platform.system() == 'Windows'
        
        for server_name, server_config in mcp_servers.items():
            if 'command' not in server_config:
                raise ValueError(f"MCP服务器 {server_name} 缺少command字段")
            
            command = server_config['command']
            args = server_config.get('args', [])
            
            logger.info(f"验证MCP服务器 {server_name}: command={command}, args={args}")
            
            # 检查命令是否存在
            command_path = shutil.which(command)
            if not command_path:
                error_msg = f"MCP服务器命令 '{command}' 在PATH中未找到"
                logger.error(error_msg)
                
                # Windows平台特殊提示
                if is_windows:
                    if command == 'npx':
                        error_msg += "。请确保已安装Node.js，并且npx在PATH中可用。"
                    elif command.endswith('.sh') or command.endswith('.bash'):
                        error_msg += "。Windows系统不支持直接执行.sh脚本，请使用对应的Windows可执行文件。"
                
                raise ValueError(error_msg)
            
            # Windows平台额外验证
            if is_windows:
                # 检查文件是否存在且可执行
                if os.path.exists(command_path):
                    # 检查是否是有效的可执行文件
                    if command_path.endswith('.sh') or command_path.endswith('.bash'):
                        raise ValueError(f"Windows系统不支持执行Shell脚本: {command_path}。请使用Windows可执行文件或npx。")
                    
                    # 对于npx，检查Node.js是否可用
                    if command == 'npx':
                        try:
                            result = subprocess.run(
                                ['node', '--version'],
                                capture_output=True,
                                text=True,
                                timeout=5
                            )
                            if result.returncode != 0:
                                raise ValueError("Node.js未正确安装或不可用")
                            logger.info(f"Node.js版本: {result.stdout.strip()}")
                        except FileNotFoundError:
                            raise ValueError("Node.js未安装。请先安装Node.js才能使用npx命令。")
                        except Exception as e:
                            logger.warning(f"检查Node.js时出错: {e}")
                else:
                    logger.warning(f"命令路径不存在: {command_path}")
            
            logger.info(f"MCP服务器 {server_name} 验证通过: {command_path}")
    
    def _initialize_mcp_agent(self, client: MCPClient) -> MCPAgent:
        """初始化MCP智能体"""
        try:
            # 临时修改sys.stderr以避免LoggingProxy问题
            import sys
            import subprocess
            
            # 保存原始stderr
            original_stderr = sys.stderr
            
            try:
                # 临时将stderr设置为subprocess.PIPE
                sys.stderr = subprocess.PIPE
                
                # 获取LLM模型实例
                llm_model = self.llm_manager.current_llm
                
                if not llm_model:
                    raise RuntimeError("LLM模型未初始化")
                
                # 记录使用的LLM模型信息
                model_info = self.llm_manager.get_model_info()
                logger.info(f"使用LLM模型: {model_info}")

                tool_guard = getattr(self, "_mcp_tool_guard", None)
                if tool_guard is None:
                    tool_guard = MCPBrowserToolGuard(MCP_BROWSER_TOOL_CALL_LIMIT)
                    self._mcp_tool_guard = tool_guard
                
                # 创建MCP智能体
                agent = MCPAgent(
                    llm=llm_model,
                    client=client,
                    max_steps=MCP_MAX_STEPS,
                    additional_instructions=MCP_AGENT_ADDITIONAL_INSTRUCTIONS,
                    callbacks=[tool_guard],
                )
                
                logger.info(
                    "MCP智能体初始化成功：已启用 headless:true 约束，浏览器工具预算=%s 次",
                    MCP_BROWSER_TOOL_CALL_LIMIT,
                )
                return agent
                
            finally:
                # 恢复原始stderr
                sys.stderr = original_stderr
                
        except Exception as e:
            logger.error(f"MCP智能体初始化失败: {e}")
            raise RuntimeError(f"MCP智能体初始化失败: {e}") from e
    
    def _build_workflow(self) -> StateGraph:
        """构建LangGraph工作流"""
        # 创建状态图
        graph = StateGraph(WebUIPlaywrightAgentState)
        
        # 添加所有节点
        graph.add_node("load_mcp_config", self._load_mcp_config_node)
        graph.add_node("initialize_mcp", self._initialize_mcp_node)
        graph.add_node("call_mcp", self._call_mcp_node)
        graph.add_node("save_script", self._save_script_node)
        
        # 设置入口点
        graph.set_entry_point("load_mcp_config")
        
        # 添加条件边
        graph.add_conditional_edges(
            "load_mcp_config",
            self._decide_after_config_load,
            {
                "initialize_mcp": "initialize_mcp",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "initialize_mcp",
            self._decide_after_mcp_init,
            {
                "call_mcp": "call_mcp",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "call_mcp",
            self._decide_after_mcp_call,
            {
                "save_script": "save_script",
                "__end__": END
            }
        )

        graph.add_edge("save_script", END)
        
        return graph.compile()
    
    def _send_websocket_message(self, content: str, step: str = ""):
        """发送WebSocket流式消息"""
        if not self.enable_streaming or not self.user_id:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            
            # 检查WebSocket服务是否可用
            if not websocket_message_service.is_available():
                logger.error("WebSocket服务不可用，无法发送消息")
                return False
            
            success = websocket_message_service.send_streaming_output(
                user_id=self.user_id,
                step=step,
                content=content,
                timestamp=timestamp,
                room_type="webui_auto_test"
            )
            
            if not success:
                logger.warning(f"WebSocket流式消息发送失败: step={step}")
            
            return success
        except Exception as e:
            logger.error(f"WebSocket消息发送异常: {e}")
            return False
    
    def _send_node_start_notification(self, node_name: str, node_display_name: str):
        """发送节点开始执行通知（使用统一的辅助函数）"""
        return send_node_start_notification_helper(
            user_id=self.user_id,
            node_name=node_name,
            node_display_name=node_display_name,
            enable_streaming=self.enable_streaming,
            room_type="webui_auto_test"
        )
    
    def _send_task_completed_notification(self, state: WebUIPlaywrightAgentState):
        """发送任务完成通知"""
        if not self.enable_streaming or not self.user_id:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            
            # 检查WebSocket服务是否可用
            if not websocket_message_service.is_available():
                logger.error("WebSocket服务不可用，无法发送任务完成通知")
                return False
            
            # 构建任务结果
            result = {
                "test_script": state.get("test_script"),
                "script_id": state.get("script_id"),
                "test_case_id": state.get("test_case_id"),
                "current_step": state.get("current_step", "completed")
            }
            
            success = websocket_message_service.send_task_completed(
                user_id=self.user_id,
                task_id="webui_auto_test",
                result=result,
                message="任务完成",
                timestamp=timestamp,
                room_type="webui_auto_test"
            )
            
            if success:
                logger.info("任务完成通知发送成功")
            else:
                logger.warning("WebSocket任务完成通知发送失败")
            
            return success
        except Exception as e:
            logger.error(f"WebSocket任务完成通知发送异常: {e}")
            return False
    
    def _process_and_send_mcp_output(self, message: str, levelno: int = logging.INFO):
        """处理并发送MCP智能体输出到前端（带去重/过滤）"""
        if not message:
            return
        
        message = message.strip()
        if not message:
            return
        
        # 过滤掉过长/噪音内容
        if "📄 Tool result:" in message:
            return
        if "Anonymized telemetry enabled" in message or "MCP_USE_ANONYMIZED_TELEMETRY" in message:
            return

        # 去重：短窗口内相同消息只发一次（解决重复日志）
        msg_hash = hash(message)
        if msg_hash in self._recent_log_hashes:
            return
        self._recent_log_hashes.append(msg_hash)
        
        self._send_websocket_message(f"{message}\n", "MCP智能体运行")
        
    
    def _load_mcp_config_node(self, state: WebUIPlaywrightAgentState) -> Dict[str, Any]:
        """1. 加载MCP配置节点"""
        self._send_node_start_notification("load_mcp_config", "加载MCP配置")
        
        try:
            # 获取用户ID
            user_id = state.get("user_id")
            
            # 发送开始加载的消息
            self._send_websocket_message("开始加载MCP配置...\n", "加载MCP配置")
            
            # 构建查询条件
            query_filter = {'is_active': True}
            if user_id:
                query_filter['created_by_id'] = user_id
            
            # 查询启用的MCP配置
            mcp_configs = MCPConfiguration.objects.filter(**query_filter)
            
            if not mcp_configs.exists():
                logger.info(f"用户 {user_id} 没有找到启用的MCP配置")
                mcp_config = {"mcpServers": {}}
                self._send_websocket_message(f"⚠️ 用户 {user_id} 没有找到启用的MCP配置\n", "加载MCP配置")
            else:
                logger.info(f"找到 {mcp_configs.count()} 个启用的MCP配置")
                
                # 发送找到配置的消息
                self._send_websocket_message(f"📋 找到 {mcp_configs.count()} 个启用的MCP配置\n", "加载MCP配置")
                
                # 查找包含mcp-playwright且激活的配置
                playwright_config = None
                for config in mcp_configs:
                    try:
                        config_dict = config.get_config_dict()
                        mcp_servers = config_dict.get('mcpServers', {})
                        
                        # 检查是否包含mcp-playwright
                        if 'playwright' not in mcp_servers:
                            continue
                        
                        # 检查mcp-playwright是否激活
                        playwright_config_data = mcp_servers['playwright']
                        is_active = playwright_config_data.get('is_active', True)  # 默认为激活状态
                        
                        if not is_active:
                            logger.warning(f"MCP配置 {config.id} 中的playwright未激活")
                            continue
                        
                        playwright_config = config
                        logger.info(f"找到mcp-playwright配置: {config.id}")
                        break
                        
                    except Exception as e:
                        logger.warning(f"解析MCP配置 {config.id} 失败: {e}")
                        continue
                
                # 构建MCP配置
                if playwright_config:
                    try:
                        config_dict = playwright_config.get_config_dict()
                        mcp_config = {"mcpServers": config_dict['mcpServers']}
                        
                        # 修改playwright配置以避免LoggingProxy问题
                        if 'playwright' in mcp_config['mcpServers']:
                            playwright_server_config = mcp_config['mcpServers']['playwright']
                            
                            # 设置环境变量
                            if 'env' not in playwright_server_config:
                                playwright_server_config['env'] = {}
                            
                            playwright_server_config['env']['PYTHONUNBUFFERED'] = '1'
                            playwright_server_config['env']['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'

                            # 支持将浏览器缓存放在项目目录，便于部署时复用。
                            # 相对路径统一按 backend 根目录解析，避免受 Celery 启动目录影响。
                            browser_path = (
                                os.getenv('MCP_PLAYWRIGHT_BROWSERS_PATH')
                                or os.getenv('PLAYWRIGHT_BROWSERS_PATH')
                            )
                            if browser_path:
                                if not os.path.isabs(browser_path):
                                    browser_path = os.path.join(str(settings.BASE_DIR), browser_path)
                                playwright_server_config['env']['PLAYWRIGHT_BROWSERS_PATH'] = os.path.abspath(browser_path)
                                logger.info(
                                    "MCP Playwright浏览器缓存目录: %s",
                                    playwright_server_config['env']['PLAYWRIGHT_BROWSERS_PATH'],
                                )
                            
                            # 添加超时设置
                            if 'timeout' not in playwright_server_config:
                                playwright_server_config['timeout'] = 30
                            
                            # 确保命令和参数正确
                            if 'command' not in playwright_server_config:
                                logger.error("MCP playwright配置缺少command字段")
                                raise ValueError("MCP playwright配置缺少command字段")
                            
                            logger.info(f"MCP playwright配置: command={playwright_server_config.get('command')}, args={playwright_server_config.get('args', [])}")
                        
                        logger.info(f"成功加载mcp-playwright配置: {list(config_dict['mcpServers'].keys())}")
                        
                        # 发送成功消息
                        self._send_websocket_message(f"✅ 成功加载mcp-playwright配置: {list(config_dict['mcpServers'].keys())}\n", "加载MCP配置")
                    except Exception as e:
                        logger.error(f"构建MCP配置失败: {e}")
                        mcp_config = {"mcpServers": {}}
                        # 发送错误消息
                        self._send_websocket_message(f"❌ 构建MCP配置失败: {str(e)}\n", "加载MCP配置")
                else:
                    logger.warning("没有找到mcp-playwright配置")
                    # 发送错误消息并返回失败状态
                    self._send_websocket_message("❌ 没有找到mcp-playwright配置，请先配置MCP服务器\n", "加载MCP配置")
                    return {
                        "mcp_config": {"mcpServers": {}},
                        "current_step": "config_load_failed"
                    }
                
        except Exception as e:
            logger.error(f"加载MCP配置失败: {e}")
            # 发送错误消息并返回失败状态
            self._send_websocket_message(f"❌ 加载MCP配置失败: {str(e)}\n", "加载MCP配置")
            return {
                "mcp_config": {"mcpServers": {}},
                "current_step": "config_load_failed"
            }
        
        return {
            "mcp_config": mcp_config,
            "current_step": "config_loaded"
        }
    
    def _initialize_mcp_node(self, state: WebUIPlaywrightAgentState) -> Dict[str, Any]:
        """2. 初始化MCP客户端和智能体节点"""
        self._send_node_start_notification("initialize_mcp", "初始化MCP客户端")
        
        try:
            # 验证MCP配置
            mcp_config = state.get("mcp_config", {})
            if not mcp_config:
                raise RuntimeError("MCP配置为空")
            
            # 初始化MCP客户端
            self.mcp_client = self._initialize_mcp_client(mcp_config)
            self._send_websocket_message("MCP客户端初始化完成\n", "初始化MCP客户端")
            
            # 初始化MCP智能体（会话将在调用时按需创建）
            self.mcp_agent = self._initialize_mcp_agent(self.mcp_client)
            
            return {
                "current_step": "mcp_initialized"
            }
        except Exception as e:
            logger.error(f"初始化MCP失败: {e}")
            self._send_websocket_message(f"❌ 初始化MCP失败: {str(e)}\n", "初始化MCP客户端")
            return {
                "current_step": "mcp_init_failed"
            }
    
    async def _ensure_mcp_sessions(self) -> bool:
        """确保MCP会话已创建（异步版本，增强错误处理）"""
        if not self.mcp_client:
            raise RuntimeError("MCP客户端未初始化")
        
        try:
            # 创建或重新创建MCP会话
            await self.mcp_client.create_all_sessions()
            logger.debug("MCP会话已确保创建")
            return True
        except Exception as e:
            error_msg = str(e)
            logger.error(f"创建MCP会话失败: {error_msg}")
            
            # Windows平台特殊错误处理
            import platform
            if platform.system() == 'Windows':
                if 'WinError 193' in error_msg or '不是有效的 Win32 应用程序' in error_msg:
                    detailed_error = (
                        "MCP命令执行失败：不是有效的Win32应用程序。\n"
                        "可能的原因：\n"
                        "1. 命令路径不正确或文件不存在\n"
                        "2. 尝试执行了非Windows可执行文件（如.sh脚本）\n"
                        "3. 架构不匹配（32位/64位）\n"
                        "4. 如果使用npx，请确保Node.js已正确安装\n"
                        f"错误详情: {error_msg}"
                    )
                    logger.error(detailed_error)
                    self._send_websocket_message(f"❌ {detailed_error}\n", "MCP会话创建")
            
            return False
    
    async def _cleanup_mcp_resources(self):
        """清理MCP资源（会话和客户端）"""
        try:
            if self.mcp_client:
                await self.mcp_client.close_all_sessions()
                logger.debug("MCP会话已关闭")
        except Exception as e:
            logger.warning(f"清理MCP资源时出错: {e}")
        finally:
            self.mcp_client = None
            self.mcp_agent = None
    
    async def _call_mcp_node(self, state: WebUIPlaywrightAgentState) -> Dict[str, Any]:
        """4. 调用MCP节点生成Playwright Python测试脚本"""
        self._send_node_start_notification("call_mcp", "调用MCP执行并生成Playwright Python脚本")
        
        try:
            if not self.mcp_agent:
                raise RuntimeError("MCP智能体未初始化")
            
            # 构建用户需求描述
            description = state['description']
            target_url = state['url']

            # 构建MCP调用提示词 - 直接生成playwright Python脚本
            mcp_prompt = f"""
你是一个 Playwright 自动化测试专家，请根据以下信息生成测试脚本：

【目标 URL】: {target_url}
【用户需求】: {description}

【MCP探索约束】
以下约束必须在本次任务中执行，并与系统级指令保持一致：
{MCP_EXPLORATION_CONSTRAINTS}

【代码编写规范】
1. 必须使用 Python Playwright 异步 API；文件头部导入 `expect`（如需）。
2. 只能输出一个业务入口：`async def run(page)`，不要输出 `def test_xxx`、pytest fixture 或其他测试入口。
3. 不得在代码内部创建或关闭 browser/context/page，不得调用 `async_playwright()`、`launch()` 或 `new_page()`。
4. 请遵循 Playwright 最佳实践，优先使用稳定选择器（`get_by_role`, `get_by_label`, `get_by_placeholder` 等）。
5. 测试脚本必须包含至少一个断言，使用 `expect(...)` 进行验证。
6. 必须使用相对路径访问页面，例如 `page.goto("/")`，以便支持外部传入的 base_url。
7. 脚本必须为包含 `async def run(page)` 的 Python 代码，严禁包含任何解释、说明文字或 Markdown 格式标记。
8. 每个关键业务动作前添加简短中文注释，帮助用户理解脚本流程；不要保留未使用的 import。

【操作类型严格区分 - 必须遵守】
- 输入类操作（fill/输入）：需要传入文本参数，如 `page.get_by_placeholder("手机号").fill("13800138000")`。
- 点击/无参类操作（click、访问网站、导航等）：绝对不允许传递或定义任何参数。调用时仅 `page.get_by_role("button").click()`，禁止写成 `click(text="")` 或给无参方法定义 text 参数。
- 若定义 Fallback 类或降级处理方法，点击按钮、访问链接等无参操作的方法签名为 `def method(self):`，严禁 `def method(self, text):`。

【起步导航规范 - 显式化 - 必须遵守】
- 在 `run(page)` 函数内部的第一行，必须显式生成 `await page.goto("/")`。
- 目的：即便底层有 BaseURL 注入，也必须在脚本中让用户看到起步动作，增强可读性与完整性。
- 绝对禁止在脚本中硬编码完整域名（如 http://...）。基地址(Base URL) 由 BrowserContext 统一管理。

【智能断言规范 - 死命令 - 必须遵守】
- 你生成的 run(page) 必须以 `await expect(...)` 结尾。
- 断言必须直接写在 `run(page)` 中，并清楚说明正在验证什么。
- 动态提取关键词：分析 expected_result/预期结果，提取 4-10 个核心业务字符，忽略引导词（系统显示、用户看到、应该、弹出等）。
- 优先使用 `await expect(page.get_by_text("关键词")).to_be_visible()`，默认不开启 exact=True。
- 若预期涉及 URL 跳转，使用 `await expect(page).to_have_url(re.compile(r"..."))`。

【代码示例（参考形态）】
```python
async def run(page):
    # 第一步：显式起步
    await page.goto("/")
    
    # ... 业务操作 ...
    
    # 最终步：智能断言 (假设预期结果为: 系统提示手机号格式不正确)
    await expect(page.get_by_text("手机号格式不正确")).to_be_visible()
```
"""
            
            # 发送开始生成的消息
            self._send_websocket_message(f"用户需求: {description}\n", "MCP智能体生成")
            self._send_websocket_message(f"目标URL: {target_url}\n", "MCP智能体生成")
            logger.info(
                "MCP脚本生成请求: target_url=%s, description_length=%s",
                target_url,
                len(description),
            )
            logger.info(
                "MCP完整请求 Prompt 开始\n%s\nMCP完整请求 Prompt 结束",
                mcp_prompt,
            )
            
            # 调用MCP智能体生成脚本（异步调用）
            try:
                raw_output = await self._call_mcp_agent_async(mcp_prompt)
            except RuntimeError as e:
                # 如果是取消异常，返回明确的取消状态
                if "任务已被取消" in str(e):
                    self._send_websocket_message("⛔ 任务已被取消，已终止脚本生成\n", "MCP智能体生成")
                    return {
                        "current_step": "cancelled",
                        "test_script": None,
                        "cancelled": True
                    }
                # 其他RuntimeError继续抛出
                raise
            finally:
                self._log_mcp_tool_guard_stats()
            
            if not raw_output:
                logger.error("MCP生成脚本失败: 返回空内容")
                # 发送失败消息
                self._send_websocket_message("❌ MCP生成脚本失败: 返回空内容\n", "MCP智能体生成")
                return {
                    "current_step": "script_generation_failed",
                    "test_script": None
                }
            
            # 从MCP输出中提取Python脚本
            script = extract_python_from_output(raw_output)

            if script:
                try:
                    script = _enforce_script_guarantees(script)
                except ValueError as exc:
                    logger.warning("生成脚本不符合统一契约: %s", exc)
                    self._send_websocket_message(f"❌ 生成脚本格式不符合规范: {exc}\n", "MCP智能体生成")
                    return {"current_step": "script_generation_failed", "test_script": None}
            
            if not script:
                logger.warning("从MCP输出中提取Playwright Python脚本失败")
                # 发送失败消息
                self._send_websocket_message("❌ 从MCP输出中提取Playwright Python脚本失败\n", "MCP智能体生成")
                return {
                    "current_step": "script_generation_failed",
                    "test_script": None
                }
            
            
            # 发送脚本生成完成的消息
            self._send_websocket_message("🎉 脚本生成完成！\n", "MCP智能体生成")
            
            return {
                "test_script": script,
                "current_step": "script_generated"
            }
        except RuntimeError as e:
            if "任务已被取消" in str(e):
                self._send_websocket_message("⛔ 任务已被取消，已终止脚本生成\n", "MCP智能体生成")
                return {"current_step": "cancelled", "test_script": None, "cancelled": True}
            raise
        except Exception as e:
            error_message = _get_mcp_error_message(e)
            logger.error(
                f"MCP生成Playwright Python测试脚本失败: {error_message}",
                exc_info=True,
            )
            self._send_websocket_message(
                f"❌ MCP生成Playwright Python测试脚本失败: {error_message}\n",
                "MCP智能体生成",
            )
            return {"current_step": "script_generation_failed", "test_script": None}
    
    async def _call_mcp_agent_async(self, prompt: str) -> str:
        """异步调用MCP智能体生成脚本"""
        # 在开始前检查是否已取消
        if self._is_cancelled():
            self._send_websocket_message("已收到停止指令，终止MCP智能体执行\n", "MCP智能体运行")
            raise RuntimeError("任务已被取消")
        
        max_retries = 3
        base_retry_delay = 2  # 基础重试延迟（秒）
        
        for attempt in range(max_retries):
            # 每次重试前检查是否已取消
            if self._is_cancelled():
                self._send_websocket_message("已收到停止指令，终止MCP智能体执行\n", "MCP智能体运行")
                raise RuntimeError("任务已被取消")
            
            try:
                # 确保MCP会话已创建
                if not await self._ensure_mcp_sessions():
                    raise RuntimeError("MCP会话创建失败")
                
                # 设置日志处理器捕获MCP输出
                mcp_handler = self._setup_mcp_output_handler()
                
                try:
                    self._send_websocket_message("📝 MCP智能体终端输出:\n", "MCP智能体运行")
                    # 使用MCP智能体的run方法（并发监听取消）
                    run_task = asyncio.create_task(self.mcp_agent.run(prompt))
                    cancel_task = asyncio.create_task(self._wait_cancel_signal())

                    done, pending = await asyncio.wait(
                        {run_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    # 取消信号先到：尽力取消run_task
                    if cancel_task in done and self._is_cancelled():
                        self._send_websocket_message("已收到停止指令，正在尝试终止当前MCP执行...\n", "MCP智能体运行")
                        run_task.cancel()
                        try:
                            await run_task
                        except asyncio.CancelledError:
                            pass
                        raise RuntimeError("任务已被取消")

                    # MCP执行先结束：取消cancel_task
                    cancel_task.cancel()
                    try:
                        await cancel_task
                    except asyncio.CancelledError:
                        pass

                    result = await run_task
                    
                    self._send_websocket_message("✅ MCP智能体运行完成\n", "MCP智能体运行")
                    return result
                
                finally:
                    # 清理日志处理器
                    self._cleanup_mcp_output_handler(mcp_handler)
                    
            except Exception as e:
                # 如果是取消异常，直接抛出，不重试
                if "任务已被取消" in str(e):
                    raise

                error_kind = _classify_mcp_error(e)
                error_message = _get_mcp_error_message(e)
                if _is_non_retryable_mcp_error(e):
                    logger.error(
                        f"MCP智能体遇到不可重试错误: type={error_kind}",
                        exc_info=True,
                    )
                    self._send_websocket_message(
                        f"❌ {error_message}\n",
                        "MCP智能体运行",
                    )
                    raise RuntimeError(error_message) from e

                # 503/504、临时 MCP 连接错误及其他未知错误保持有限重试。
                logger.error(
                    f"运行MCP智能体失败 (尝试 {attempt + 1}/{max_retries}): type={error_kind}",
                    exc_info=True,
                )

                if attempt < max_retries - 1:
                    # 指数退避重试
                    retry_delay = base_retry_delay * (2 ** attempt)
                    self._send_websocket_message(
                        f"⚠️ 连接失败，{retry_delay}秒后重试...\n",
                        "MCP智能体运行"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    # 最后一次尝试失败
                    self._send_websocket_message(
                        f"❌ MCP智能体运行失败: {error_message}\n",
                        "MCP智能体运行"
                    )
                    raise
        
        raise Exception("MCP智能体运行失败，已达到最大重试次数")
    
    def _setup_mcp_output_handler(self):
        """设置MCP输出日志处理器（INFO/WARN/ERROR均捕获，避免重复挂载）"""
        import logging
        
        class MCPOutputHandler(logging.Handler):
            def __init__(self, agent_instance):
                super().__init__()
                self.agent = agent_instance
            
            def emit(self, record):
                try:
                    message = self.format(record)
                    self.agent._process_and_send_mcp_output(message, record.levelno)
                except Exception as e:
                    # 避免handler异常导致日志系统递归
                    logger.warning(f"MCP日志处理器异常: {e}")
        
        mcp_logger = logging.getLogger('mcp_use')

        # 如果已经挂了handler，直接复用，避免重复输出
        if self._mcp_log_handler and self._mcp_log_handler in mcp_logger.handlers:
            return self._mcp_log_handler

        handler = MCPOutputHandler(self)
        handler.setLevel(logging.INFO)  # 过滤DEBUG，减少噪音（需要DEBUG可改为DEBUG）
        handler.setFormatter(logging.Formatter("%(message)s"))
        mcp_logger.addHandler(handler)
        self._mcp_log_handler = handler
        return handler
    
    def _cleanup_mcp_output_handler(self, handler):
        """清理MCP输出日志处理器"""
        import logging
        mcp_logger = logging.getLogger('mcp_use')
        try:
            if handler and handler in mcp_logger.handlers:
                mcp_logger.removeHandler(handler)
        finally:
            if handler == self._mcp_log_handler:
                self._mcp_log_handler = None
    

    
    def _decide_after_config_load(self, state: WebUIPlaywrightAgentState) -> str:
        """配置加载后的决策"""
        if state.get("current_step") == "config_loaded":
            return "initialize_mcp"
        elif state.get("current_step") == "config_load_failed":
            return "__end__"
        else:
            return "__end__"
    
    def _decide_after_mcp_init(self, state: WebUIPlaywrightAgentState) -> str:
        """MCP初始化后的决策：当前Agent仅用于AI实验室，直接走自由探索节点"""
        if state.get("current_step") != "mcp_initialized":
            return "__end__"
        return "call_mcp"
    
    def _decide_after_mcp_call(self, state: WebUIPlaywrightAgentState) -> str:
        """MCP调用后的决策"""
        # 如果已取消，直接结束
        if state.get("current_step") == "cancelled" or state.get("cancelled"):
            return "__end__"
        if state.get("current_step") == "script_generated" and state.get("test_script"):
            return "save_script"
        else:
            return "__end__"
    
    
    
    def _save_script_node(self, state: WebUIPlaywrightAgentState) -> WebUIPlaywrightAgentState:
        """保存脚本到数据库"""
        # 发送节点开始通知
        self._send_node_start_notification("save_script", "保存脚本到数据库")
        
        try:
            python_script = state.get("test_script")
            user_id = state.get("user_id")
            project_id = state.get("project_id")
            script_name = state.get("script_name", "WebUI Playwright测试脚本")
            description = state.get("description")
            url = state.get("url")
            test_case_id = state.get("test_case_id")  # 获取测试用例ID
            
            if not python_script:
                logger.warning("没有Python脚本内容需要保存")
                return {
                    **state,
                    "current_step": "save_failed"
                }

            from web_testing.script_contract import ScriptContractError, normalize_for_storage, store_script_content
            try:
                python_script = normalize_for_storage(python_script)
            except ScriptContractError as exc:
                self._send_websocket_message(f"❌ 脚本不符合统一契约，未保存: {exc}\n", "脚本保存")
                return {**state, "current_step": "save_failed", "error": str(exc)}
            
            # 判断保存方式：如果有test_case_id则是选择测试用例方式，否则是手动填写方式
            if test_case_id:
                # 选择测试用例方式：保存到WebUITestCase模型的test_script_content字段
                self._send_websocket_message("💾 开始保存Python脚本到测试用例...\n", "脚本保存")
                
                # 导入模型
                from web_testing.models import WebUITestCase
                from django.contrib.auth import get_user_model
                
                # 获取自定义用户模型
                User = get_user_model()
                user = User.objects.get(id=user_id)
                from web_testing.project_access import EDIT, get_project_for_user
                get_project_for_user(project_id, user, EDIT)
                
                # 获取测试用例并更新test_script_content字段
                test_case = WebUITestCase.objects.get(
                    id=test_case_id,
                    project_id=project_id,
                )
                store_script_content(test_case, python_script, source='mcp_exploration')
                self._send_websocket_message(f"✅ Python脚本已保存到测试用例: {test_case.title}\n", "脚本保存")
                
                # 发送任务完成通知
                self._send_task_completed_notification(state)
                
                return {
                    **state,
                    "test_case_id": test_case_id,
                    "current_step": "saved"
                }
            else:
                # 手动填写方式：不保存到数据库，只返回成功状态
                # 发送任务完成通知
                self._send_task_completed_notification(state)
                
                return {
                    **state,
                    "current_step": "saved"
                }
            
        except Exception as e:
            logger.error(f"保存Python脚本失败: {e}")
            self._send_websocket_message(f"❌ 保存Python脚本失败: {str(e)}\n", "脚本保存")
            return {
                **state,
                "current_step": "save_failed"
            }
    
    

    async def run(self, description: str, url: str = "") -> Dict[str, Any]:
        """运行WebUI测试脚本生成智能体"""
        try:
            if not self.workflow:
                raise RuntimeError("LangGraph工作流未初始化，无法运行WebUI测试脚本生成智能体")
            return await self._run_with_langgraph(description, url)
                
        except RuntimeError as e:
            # 如果是取消异常，返回明确的取消状态
            if "任务已被取消" in str(e):
                return {
                    "success": False,
                    "cancelled": True,
                    "error": "任务已被取消",
                    "current_step": "cancelled"
                }
            # 其他RuntimeError继续抛出
            raise
        except Exception as e:
            error_msg = f"运行WebUI测试脚本生成智能体失败: {_get_mcp_error_message(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "current_step": "failed"
            }
        finally:
            # 清理MCP资源
            await self._cleanup_mcp_resources()
    
    async def _run_with_langgraph(self, description: str, url: str) -> Dict[str, Any]:
        """使用LangGraph工作流运行"""
        initial_state = {
            "description": description,
            "url": url,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "script_name": self.script_name,
            "mcp_config": self.mcp_config,
            "test_script": None,
            "script_id": None,
            "current_step": "initialized"
        }
        result = await self.workflow.ainvoke(initial_state)
        
        # 检查是否是因为取消
        if result.get("current_step") == "cancelled" or result.get("cancelled"):
            return {
                "success": False,
                "cancelled": True,
                "error": "任务已被取消",
                "current_step": "cancelled"
            }
        
        # 检查是否有错误
        if not result.get("test_script"):
            return {
                "success": False,
                "error": "测试脚本生成失败",
                "current_step": result.get("current_step", "unknown")
            }
        
        # 返回成功结果
        return {
            "success": True,
            "test_script": result.get("test_script"),
            "script_id": result.get("script_id"),
            "model_info": self.llm_manager.get_model_info(),
            "model_type": "llm",
            "current_step": result.get("current_step", "completed")
        }




def create_webui_playwright_agent(user, user_id: int = None, enable_streaming: bool = True) -> WebUIPlaywrightAgent:
    """创建WebUI Playwright智能体实例"""
    return WebUIPlaywrightAgent(user, user_id, enable_streaming)
