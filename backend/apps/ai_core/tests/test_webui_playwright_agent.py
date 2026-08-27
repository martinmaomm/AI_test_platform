import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langgraph.errors import GraphRecursionError

from ai_core.webui_playwright_agent import (
    MCP_AGENT_ADDITIONAL_INSTRUCTIONS,
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_ERROR_BROWSER,
    MCP_ERROR_GRAPH_RECURSION,
    MCP_ERROR_TOOL_PARAMETER,
    MCP_ERROR_RATE_LIMIT,
    MCP_ERROR_TRANSIENT,
    MCP_EXPLORATION_CONSTRAINTS,
    MCP_MAX_STEPS,
    WebUIPlaywrightAgent,
    _classify_mcp_error,
    _get_mcp_error_message,
    _is_non_retryable_mcp_error,
)


class WebUIPlaywrightAgentStabilityTests(unittest.TestCase):
    def test_system_and_task_constraints_share_exploration_limits(self):
        self.assertIn("headless: true", MCP_AGENT_ADDITIONAL_INSTRUCTIONS)
        self.assertIn(str(MCP_BROWSER_TOOL_CALL_LIMIT), MCP_AGENT_ADDITIONAL_INSTRUCTIONS)
        self.assertIn(MCP_EXPLORATION_CONSTRAINTS, MCP_AGENT_ADDITIONAL_INSTRUCTIONS)
        self.assertIn("最终回复只能是完整 Python 脚本", MCP_EXPLORATION_CONSTRAINTS)

    def test_agent_keeps_max_steps_and_receives_system_constraints(self):
        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent.llm_manager = SimpleNamespace(
            current_llm=object(),
            get_model_info=lambda: {"provider": "test", "model": "test-model"},
        )

        with patch("ai_core.webui_playwright_agent.MCPAgent") as mcp_agent, patch(
            "ai_core.webui_playwright_agent.logger.info"
        ) as logger_info:
            result = agent._initialize_mcp_agent(object())

        self.assertIs(result, mcp_agent.return_value)
        kwargs = mcp_agent.call_args.kwargs
        self.assertEqual(MCP_MAX_STEPS, 30)
        self.assertEqual(kwargs["max_steps"], MCP_MAX_STEPS)
        self.assertEqual(
            kwargs["additional_instructions"],
            MCP_AGENT_ADDITIONAL_INSTRUCTIONS,
        )
        logger_info.assert_any_call(
            "MCP智能体初始化成功：已启用 headless:true 约束，浏览器工具预算=%s 次",
            MCP_BROWSER_TOOL_CALL_LIMIT,
        )

    def test_graph_recursion_is_not_reported_as_missing_chromium(self):
        error = GraphRecursionError("recursion limit reached")

        self.assertEqual(_classify_mcp_error(error), MCP_ERROR_GRAPH_RECURSION)
        self.assertTrue(_is_non_retryable_mcp_error(error))
        message = _get_mcp_error_message(error)
        self.assertNotIn("Chromium", message)
        self.assertIn("最大执行步数", message)

    def test_browser_startup_error_has_browser_message_and_does_not_retry(self):
        error = RuntimeError(
            "BrowserType.launch: Executable doesn't exist at /cache/chromium-1200/chrome"
        )

        self.assertEqual(_classify_mcp_error(error), MCP_ERROR_BROWSER)
        self.assertTrue(_is_non_retryable_mcp_error(error))
        message = _get_mcp_error_message(error)
        self.assertIn("Playwright浏览器", message)
        self.assertIn("Chromium", message)

    def test_headless_parameter_error_is_not_browser_missing(self):
        errors = (
            "BrowserType.launch: headless: expected boolean, got undefined",
            "BrowserType.launch: invalid headless option",
        )

        for error_text in errors:
            with self.subTest(error=error_text):
                error = RuntimeError(error_text)

                self.assertEqual(_classify_mcp_error(error), MCP_ERROR_TOOL_PARAMETER)
                self.assertTrue(_is_non_retryable_mcp_error(error))
                message = _get_mcp_error_message(error)
                self.assertIn("playwright_navigate", message)
                self.assertIn("JSON boolean", message)
                self.assertIn("headless: true", message)
                self.assertNotIn("Chromium", message)

    def test_rate_limit_is_non_retryable_and_has_clear_message(self):
        for error in (
            RuntimeError("HTTP 429 Too Many Requests"),
            RuntimeError("UPSTREAM_RATE_LIMITED"),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(_classify_mcp_error(error), MCP_ERROR_RATE_LIMIT)
                self.assertTrue(_is_non_retryable_mcp_error(error))
                message = _get_mcp_error_message(error)
                self.assertIn("限流", message)
                self.assertIn("不会自动重试", message)

    def test_temporary_upstream_and_connection_errors_remain_retryable(self):
        for error in (
            RuntimeError("HTTP 503 Service Unavailable"),
            RuntimeError("HTTP 504 Gateway Timeout"),
            ConnectionError("MCP server disconnected"),
            ConnectionError("BrowserType.connect: connection refused"),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(_classify_mcp_error(error), MCP_ERROR_TRANSIENT)
                self.assertFalse(_is_non_retryable_mcp_error(error))

    def test_unknown_error_uses_fixed_message(self):
        message = _get_mcp_error_message(RuntimeError("token=secret"))

        self.assertEqual(message, "MCP智能体运行失败，请查看Celery日志。")

    def test_task_prompt_repeats_exploration_constraints(self):
        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent.mcp_agent = object()
        agent._send_node_start_notification = Mock()
        agent._send_websocket_message = Mock()
        agent._call_mcp_agent_async = AsyncMock(return_value="python script")

        with patch(
            "ai_core.webui_playwright_agent.extract_python_from_output",
            return_value="python script",
        ):
            asyncio.run(
                agent._call_mcp_node(
                    {
                        "description": "登录并检查首页",
                        "url": "https://example.test",
                        "project_id": None,
                    }
                )
            )

        prompt = agent._call_mcp_agent_async.await_args.args[0]
        self.assertIn(MCP_EXPLORATION_CONSTRAINTS, prompt)
        self.assertIn("headless: true", prompt)
        self.assertIn(str(MCP_BROWSER_TOOL_CALL_LIMIT), prompt)


class WebUIPlaywrightAgentRetryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_agent(run_side_effect):
        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent.mcp_agent = SimpleNamespace(run=AsyncMock(side_effect=run_side_effect))
        agent._is_cancelled = Mock(return_value=False)
        agent._ensure_mcp_sessions = AsyncMock(return_value=True)
        agent._wait_cancel_signal = AsyncMock(return_value=None)
        agent._setup_mcp_output_handler = Mock(return_value=None)
        agent._cleanup_mcp_output_handler = Mock()
        agent._send_websocket_message = Mock()
        return agent

    async def test_browser_and_rate_limit_errors_do_not_retry_full_run(self):
        errors = (
            (RuntimeError("BrowserType.launch: Executable doesn't exist"), MCP_ERROR_BROWSER),
            (
                RuntimeError("BrowserType.launch: headless: expected boolean, got undefined"),
                MCP_ERROR_TOOL_PARAMETER,
            ),
            (RuntimeError("HTTP 429 Too Many Requests"), MCP_ERROR_RATE_LIMIT),
            (RuntimeError("UPSTREAM_RATE_LIMITED"), MCP_ERROR_RATE_LIMIT),
        )

        for error, expected_kind in errors:
            with self.subTest(error=str(error)):
                agent = self._build_agent(error)
                with patch(
                    "ai_core.webui_playwright_agent.asyncio.sleep",
                    new=AsyncMock(),
                ) as sleep:
                    with self.assertRaises(RuntimeError) as raised:
                        await agent._call_mcp_agent_async("prompt")

                self.assertEqual(_classify_mcp_error(error), expected_kind)
                self.assertEqual(agent.mcp_agent.run.await_count, 1)
                sleep.assert_not_awaited()
                if expected_kind == MCP_ERROR_RATE_LIMIT:
                    self.assertIn("不会自动重试", str(raised.exception))
                elif expected_kind == MCP_ERROR_BROWSER:
                    self.assertIn("浏览器", str(raised.exception))
                else:
                    self.assertIn("playwright_navigate", str(raised.exception))
                    self.assertIn("headless: true", str(raised.exception))
                    self.assertNotIn("Chromium", str(raised.exception))

    async def test_graph_recursion_does_not_retry_or_sleep_and_avoids_chromium(self):
        error = GraphRecursionError("recursion limit reached")
        agent = self._build_agent(error)

        with patch(
            "ai_core.webui_playwright_agent.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(RuntimeError) as raised:
                await agent._call_mcp_agent_async("prompt")

        self.assertEqual(agent.mcp_agent.run.await_count, 1)
        sleep.assert_not_awaited()
        self.assertIn("最大执行步数", str(raised.exception))
        self.assertNotIn("Chromium", str(raised.exception))

    async def test_503_504_and_connection_errors_are_retried(self):
        errors = (
            RuntimeError("HTTP 503 Service Unavailable"),
            RuntimeError("HTTP 504 Gateway Timeout"),
            ConnectionError("MCP server disconnected"),
        )

        for error in errors:
            with self.subTest(error=str(error)):
                agent = self._build_agent([error, "generated script"])
                with patch(
                    "ai_core.webui_playwright_agent.asyncio.sleep",
                    new=AsyncMock(),
                ) as sleep:
                    result = await agent._call_mcp_agent_async("prompt")

                self.assertEqual(result, "generated script")
                self.assertEqual(agent.mcp_agent.run.await_count, 2)
                sleep.assert_awaited_once_with(2)


if __name__ == "__main__":
    unittest.main()
