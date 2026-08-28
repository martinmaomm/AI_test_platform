import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from langgraph.errors import GraphRecursionError

from ai_core.webui_playwright_agent import (
    MCP_AGENT_ADDITIONAL_INSTRUCTIONS,
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCP_ERROR_BROWSER,
    MCP_ERROR_GRAPH_RECURSION,
    MCP_ERROR_INTERACTION_FAILURE,
    MCP_ERROR_LOGIN_FAILED,
    MCP_ERROR_REPEATED_INTERACTION,
    MCP_ERROR_TOOL_PARAMETER,
    MCP_ERROR_TOOL_BUDGET,
    MCP_ERROR_RATE_LIMIT,
    MCP_ERROR_TRANSIENT,
    MCP_EXPLORATION_CONSTRAINTS,
    MCP_MAX_STEPS,
    MCPBrowserToolGuard,
    MCPToolGuardError,
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
        self.assertEqual(MCP_MAX_STEPS, 60)
        self.assertEqual(kwargs["max_steps"], MCP_MAX_STEPS)
        self.assertEqual(
            kwargs["additional_instructions"],
            MCP_AGENT_ADDITIONAL_INSTRUCTIONS,
        )
        self.assertEqual(len(kwargs["callbacks"]), 1)
        self.assertIsInstance(kwargs["callbacks"][0], MCPBrowserToolGuard)
        self.assertNotIn("retry_on_error", kwargs)
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

    async def test_tool_guard_errors_do_not_retry(self):
        error = MCPToolGuardError(
            MCP_ERROR_TOOL_BUDGET,
            "浏览器工具调用已达到本次任务上限",
        )
        agent = self._build_agent(error)

        with patch(
            "ai_core.webui_playwright_agent.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(RuntimeError) as raised:
                await agent._call_mcp_agent_async("prompt")

        self.assertEqual(agent.mcp_agent.run.await_count, 1)
        sleep.assert_not_awaited()
        self.assertIn("工具调用", str(raised.exception))

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


class MCPBrowserToolGuardTests(unittest.TestCase):
    @staticmethod
    def _start(guard, tool_name, inputs=None):
        run_id = uuid4()
        guard.on_tool_start(
            {"name": tool_name},
            "",
            run_id=run_id,
            inputs=inputs or {},
        )
        return run_id

    @staticmethod
    def _end(guard, run_id, tool_name, output="ok"):
        guard.on_tool_end(output, run_id=run_id, name=tool_name)

    def test_hard_budget_stops_before_next_tool_execution(self):
        guard = MCPBrowserToolGuard(max_tool_calls=2)
        first = self._start(guard, "playwright_get_visible_text")
        self._end(guard, first, "playwright_get_visible_text", "首页")
        second = self._start(guard, "playwright_screenshot", {"name": "page"})
        self._end(guard, second, "playwright_screenshot", "saved")

        with self.assertRaises(MCPToolGuardError) as raised:
            self._start(guard, "playwright_get_visible_html")

        self.assertEqual(raised.exception.error_kind, MCP_ERROR_TOOL_BUDGET)
        self.assertEqual(guard.get_stats()["total_tool_calls"], 2)

    def test_non_browser_tools_do_not_consume_budget(self):
        guard = MCPBrowserToolGuard(max_tool_calls=1)

        run_id = self._start(guard, "start_codegen_session")
        self._end(guard, run_id, "start_codegen_session")

        self.assertEqual(guard.get_stats()["total_tool_calls"], 0)

    def test_third_identical_interaction_is_stopped_before_execution(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        inputs = {"selector": "button.save"}
        for _ in range(2):
            run_id = self._start(guard, "playwright_click", inputs)
            self._end(guard, run_id, "playwright_click", "Clicked")

        with self.assertRaises(MCPToolGuardError) as raised:
            self._start(guard, "playwright_click", inputs)

        self.assertEqual(
            raised.exception.error_kind,
            MCP_ERROR_REPEATED_INTERACTION,
        )
        self.assertEqual(guard.get_stats()["total_tool_calls"], 2)

    def test_read_only_page_checks_may_repeat(self):
        guard = MCPBrowserToolGuard(max_tool_calls=5)

        for _ in range(3):
            run_id = self._start(guard, "playwright_get_visible_text")
            self._end(guard, run_id, "playwright_get_visible_text", "首页")

        self.assertEqual(guard.get_stats()["total_tool_calls"], 3)
        self.assertIsNone(guard.get_stats()["termination_reason"])

    def test_three_consecutive_interaction_failures_stop_task(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)

        for index in range(2):
            run_id = self._start(
                guard,
                "playwright_click",
                {"selector": f"button.missing-{index}"},
            )
            self._end(
                guard,
                run_id,
                "playwright_click",
                "Operation failed: Timeout 30000ms exceeded",
            )

        third_run = self._start(
            guard,
            "playwright_fill",
            {"selector": "input.missing", "value": "x"},
        )
        with self.assertRaises(MCPToolGuardError) as raised:
            self._end(
                guard,
                third_run,
                "playwright_fill",
                "Operation failed: waiting for locator",
            )

        self.assertEqual(
            raised.exception.error_kind,
            MCP_ERROR_INTERACTION_FAILURE,
        )

    def test_successful_interaction_resets_failure_counter(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        failed = self._start(
            guard,
            "playwright_click",
            {"selector": "button.missing"},
        )
        self._end(
            guard,
            failed,
            "playwright_click",
            "Operation failed: Timeout 30000ms exceeded",
        )
        succeeded = self._start(
            guard,
            "playwright_click",
            {"selector": "button.available"},
        )
        self._end(guard, succeeded, "playwright_click", "Clicked")

        self.assertEqual(guard.consecutive_interaction_failures, 0)

    def test_non_locator_tool_failure_does_not_increment_locator_failures(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        run_id = self._start(
            guard,
            "playwright_evaluate",
            {"script": "throw new Error('probe')"},
        )
        self._end(
            guard,
            run_id,
            "playwright_evaluate",
            "Error executing tool: exception",
        )

        self.assertEqual(guard.consecutive_interaction_failures, 0)

    def _submit_login(self, guard):
        username = self._start(
            guard,
            "playwright_fill",
            {"selector": 'input[placeholder="请输入用户名"]', "value": "user"},
        )
        self._end(guard, username, "playwright_fill", "Filled")
        password = self._start(
            guard,
            "playwright_fill",
            {"selector": 'input[placeholder="请输入密码"]', "value": "secret"},
        )
        self._end(guard, password, "playwright_fill", "Filled")
        submit = self._start(
            guard,
            "playwright_click",
            {"selector": 'button:has-text("登录")'},
        )
        self._end(guard, submit, "playwright_click", "Clicked")

    def test_two_login_page_checks_stop_failed_login(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        self._submit_login(guard)
        visible_text = "mall-admin-web 登录 获取体验账号"

        first = self._start(guard, "playwright_get_visible_text")
        self._end(guard, first, "playwright_get_visible_text", visible_text)
        second = self._start(guard, "playwright_get_visible_html")
        with self.assertRaises(MCPToolGuardError) as raised:
            self._end(
                guard,
                second,
                "playwright_get_visible_html",
                '<input placeholder="请输入用户名"><input type="password">登录',
            )

        self.assertEqual(raised.exception.error_kind, MCP_ERROR_LOGIN_FAILED)

    def test_successful_post_login_page_check_marks_login_verified(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        self._submit_login(guard)

        check = self._start(guard, "playwright_get_visible_text")
        self._end(
            guard,
            check,
            "playwright_get_visible_text",
            "首页 权限 用户列表 角色列表",
        )

        self.assertTrue(guard.login_verified)
        self.assertEqual(guard.login_checks_since_attempt, 0)

    def test_second_login_submission_before_verification_is_stopped(self):
        guard = MCPBrowserToolGuard(max_tool_calls=10)
        self._submit_login(guard)

        with self.assertRaises(MCPToolGuardError) as raised:
            self._start(
                guard,
                "playwright_click",
                {"selector": 'button:has-text("登录")'},
            )

        self.assertEqual(raised.exception.error_kind, MCP_ERROR_LOGIN_FAILED)

    def test_guard_stats_log_contains_counts_without_inputs(self):
        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent._mcp_tool_guard = MCPBrowserToolGuard(max_tool_calls=2)
        run_id = self._start(
            agent._mcp_tool_guard,
            "playwright_fill",
            {"selector": 'input[type="password"]', "value": "secret"},
        )
        self._end(agent._mcp_tool_guard, run_id, "playwright_fill", "Filled")

        with patch("ai_core.webui_playwright_agent.logger.info") as logger_info:
            agent._log_mcp_tool_guard_stats()

        logged_args = logger_info.call_args.args
        self.assertEqual(logged_args[1], 1)
        self.assertEqual(logged_args[2], {"playwright_fill": 1})
        self.assertNotIn("secret", str(logged_args))


if __name__ == "__main__":
    unittest.main()
