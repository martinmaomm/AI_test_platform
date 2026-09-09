"""No real provider, browser, broker or database is used by these regressions."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from langchain_core.callbacks import AsyncCallbackManager

from .browser_discovery_agent import (
    ALLOWED_BROWSER_TOOLS, DiscoveryStopped, DiscoveryToolGuard, PendingOriginGate,
    _safe_agent_summary, prepare_capture_config, run_browser_discovery,
)


class BrowserDiscoveryAgentTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='automation-capture-agent-')
        self.addCleanup(self.temp.cleanup)
        self.options = {
            'llm_model': Mock(),
            'mcp_config': {'mcpServers': {
                'playwright': {'command': 'npx', 'args': ['-y', '@executeautomation/playwright-mcp-server@1.0.12']},
                'unrelated': {'command': 'not-to-be-started'},
            }},
            'task_id': '11111111-1111-4111-8111-111111111111',
            'target_url': 'https://web.example.test/app?tenant=1#/users',
            'api_origin': 'https://api.example.test', 'description': '只探索本轮新增用户。',
            'trace_file': str(Path(self.temp.name) / 'network.jsonl'),
            'timeout_seconds': 30,
        }

    def clients(self):
        session = SimpleNamespace(
            list_tools=AsyncMock(return_value=[
                SimpleNamespace(name=name) for name in [*ALLOWED_BROWSER_TOOLS, 'playwright_evaluate', 'playwright_post']
            ]),
            call_tool=AsyncMock(),
        )
        client = SimpleNamespace(
            create_all_sessions=AsyncMock(), close_all_sessions=AsyncMock(),
            get_session=Mock(return_value=session),
        )
        return client, session

    def test_only_pinned_browser_is_started_with_task_specific_capture(self):
        original = self.options['mcp_config']
        config = prepare_capture_config(original, self.options['task_id'], self.options['trace_file'], 'https://api.example.test', {'max_requests': 30})
        self.assertEqual(set(config['mcpServers']), {'playwright'})
        env = config['mcpServers']['playwright']['env']
        self.assertEqual(env['MCP_NETWORK_CAPTURE'], '1')
        self.assertEqual(env['MCP_NETWORK_CAPTURE_MAX_REQUESTS'], '30')
        self.assertEqual(env['MCP_NETWORK_CAPTURE_DIR'], str(Path(self.temp.name).resolve()))
        self.assertEqual(env['MCP_NETWORK_CAPTURE_AUTO_ORIGIN'], '0')
        self.assertNotIn('env', original['mcpServers']['playwright'])
        self.assertNotIn('MCP_NETWORK_CAPTURE', str(original))

    def test_unsupported_package_fails_without_silently_using_another_browser(self):
        with self.assertRaisesRegex(ValueError, '固定'):
            prepare_capture_config({'mcpServers': {'playwright': {'command': 'npx', 'args': ['unmanaged-server']}}},
                                   self.options['task_id'], self.options['trace_file'], '', {})

    def test_empty_api_origin_enables_target_scoped_auto_collection(self):
        config = prepare_capture_config(
            self.options['mcp_config'], self.options['task_id'], self.options['trace_file'], '', {},
            target_url=self.options['target_url'], auto_origin=True,
        )
        self.assertEqual(config['mcpServers']['playwright']['env']['MCP_NETWORK_CAPTURE_ALLOWED_ORIGINS'], '')
        self.assertEqual(config['mcpServers']['playwright']['env']['MCP_NETWORK_CAPTURE_AUTO_ORIGIN'], '1')
        self.assertEqual(config['mcpServers']['playwright']['env']['MCP_NETWORK_CAPTURE_TARGET_URL'], self.options['target_url'])

    def test_plain_model_final_text_does_not_require_json_or_script(self):
        client, session = self.clients()
        agent = SimpleNamespace(initialize=AsyncMock(), run=AsyncMock(return_value='完成，无需 JSON'))
        checkpoint = Mock(return_value=True)
        with patch('api_testing.browser_discovery_agent.MCPClient.from_dict', return_value=client), patch(
            'api_testing.browser_discovery_agent.BudgetedMCPAgent', return_value=agent,
        ) as factory:
            result = asyncio.run(run_browser_discovery(**self.options, checkpoint=checkpoint))
        self.assertTrue(result['completed'])
        self.assertEqual(result['error_code'], '')
        self.assertIn('playwright_post', factory.call_args.kwargs['disallowed_tools'])
        self.assertIn(self.options['target_url'], agent.run.call_args.args[0])
        self.assertNotIn('save_script_draft', agent.run.call_args.args[0])
        callbacks = factory.call_args.kwargs['callbacks']
        self.assertIsInstance(callbacks[0], PendingOriginGate)
        self.assertIsInstance(callbacks[1], DiscoveryToolGuard)
        self.assertTrue(callbacks[0].run_inline)
        session.call_tool.assert_awaited_once_with('playwright_close', {})
        client.close_all_sessions.assert_awaited_once()
        self.assertEqual(checkpoint.call_args.args[0]['phase'], 'finalizing')

    def test_cancelled_task_does_not_start_sessions(self):
        client, _ = self.clients()
        with patch('api_testing.browser_discovery_agent.MCPClient.from_dict', return_value=client):
            result = asyncio.run(run_browser_discovery(**self.options, is_cancelled=lambda: True))
        self.assertEqual(result['error_code'], 'CANCELLED')
        self.assertFalse(result['completed'])
        client.create_all_sessions.assert_not_awaited()
        client.close_all_sessions.assert_awaited_once()

    def test_deadline_cancels_pending_work_and_keeps_evidence_file(self):
        client, _ = self.clients()

        async def never():
            await asyncio.Event().wait()

        agent = SimpleNamespace(initialize=AsyncMock(), run=never)
        async def stalled_run(*args, **kwargs):
            Path(self.options['trace_file']).write_text('{"event":"request","request_id":"kept"}\n')
            await never()
        agent.run = stalled_run
        options = {**self.options, 'timeout_seconds': 0.2}
        with patch('api_testing.browser_discovery_agent.MCPClient.from_dict', return_value=client), patch(
            'api_testing.browser_discovery_agent.BudgetedMCPAgent', return_value=agent,
        ):
            result = asyncio.run(run_browser_discovery(**options))
        self.assertEqual(result['error_code'], 'TOTAL_TIMEOUT')
        self.assertIn('kept', Path(self.options['trace_file']).read_text())
        client.close_all_sessions.assert_awaited_once()

    def test_model_error_never_returns_credentials_in_summary(self):
        client, _ = self.clients()
        agent = SimpleNamespace(initialize=AsyncMock(), run=AsyncMock(side_effect=RuntimeError('SECRET_PASSWORD API_KEY TOKEN')))
        with patch('api_testing.browser_discovery_agent.MCPClient.from_dict', return_value=client), patch(
            'api_testing.browser_discovery_agent.BudgetedMCPAgent', return_value=agent,
        ):
            result = asyncio.run(run_browser_discovery(**self.options))
        self.assertFalse(result['completed'])
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('API_KEY', str(result))

    def test_true_tool_count_includes_each_call_and_blocks_limit_plus_one(self):
        guard = DiscoveryToolGuard('https://web.example.test', '', 2)
        guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=1, inputs={'selector': '#a'})
        guard.on_tool_end('clicked', run_id=1)
        guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=2, inputs={'selector': '#b'})
        guard.on_tool_end('clicked', run_id=2)
        with self.assertRaises(DiscoveryStopped):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=3, inputs={'selector': '#c'})
        self.assertEqual(guard.tool_calls, 2)
        self.assertEqual(guard.error.code, 'TOOL_BUDGET')

    def test_direct_api_tools_and_out_of_scope_navigation_are_blocked(self):
        for name, values in [('playwright_post', {'url': 'https://web.example.test/'}),
                             ('playwright_navigate', {'url': 'https://unapproved.test/', 'headless': True})]:
            with self.subTest(name=name):
                guard = DiscoveryToolGuard('https://web.example.test', '', 20)
                with self.assertRaises(DiscoveryStopped):
                    guard.on_tool_start({'name': name}, '{}', inputs=values)
                self.assertEqual(guard.tool_calls, 0)

    def test_repeated_operation_stops_but_changed_observation_resets_it(self):
        guard = DiscoveryToolGuard('https://web.example.test', '', 20)
        for index in range(4):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=index, inputs={'selector': '#same'})
            guard.on_tool_end('clicked', run_id=index)
        with self.assertRaises(DiscoveryStopped):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', inputs={'selector': '#same'})
        self.assertEqual(guard.error.code, 'REPEATED_OPERATION')

    def test_three_explicit_tool_errors_stop_without_logging_inputs(self):
        guard = DiscoveryToolGuard('https://web.example.test', '', 20)
        for index in range(3):
            guard.on_tool_start({'name': 'playwright_fill'}, '{}', run_id=index, inputs={'selector': f'#field{index}', 'value': 'SECRET'})
            if index < 2:
                guard.on_tool_end({'isError': True}, run_id=index)
            else:
                with self.assertRaises(DiscoveryStopped):
                    guard.on_tool_end({'isError': True}, run_id=index)
        self.assertNotIn('SECRET', guard.current_action)
        self.assertEqual(guard.error.code, 'TOOL_FAILURE')

    def test_observation_kinds_do_not_clear_repeated_action_or_operation_failures(self):
        guard = DiscoveryToolGuard('https://web.example.test', '', 20)
        for index in range(2):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=index, inputs={'selector': '#same'})
            guard.on_tool_end({'isError': True}, run_id=index)
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '{}', run_id=f'text-{index}', inputs={'selector': '#app'})
            guard.on_tool_end('unchanged text', run_id=f'text-{index}')
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '{}', run_id=f'html-{index}', inputs={'selector': '#app'})
            guard.on_tool_end('<main>unchanged</main>', run_id=f'html-{index}')
        with self.assertRaises(DiscoveryStopped):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id='third', inputs={'selector': '#other'})
            guard.on_tool_end({'isError': True}, run_id='third')
        self.assertEqual(guard.error.code, 'TOOL_FAILURE')

    def test_successful_clicks_with_alternating_same_observations_still_stop_repetition(self):
        guard = DiscoveryToolGuard('https://web.example.test', '', 20)
        for index in range(4):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', run_id=f'click-{index}', inputs={'selector': '#same'})
            guard.on_tool_end('clicked', run_id=f'click-{index}')
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '{}', run_id=f'text-{index}', inputs={'selector': '#app'})
            guard.on_tool_end('same page', run_id=f'text-{index}')
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '{}', run_id=f'html-{index}', inputs={'selector': '#app'})
            guard.on_tool_end('<main>same page</main>', run_id=f'html-{index}')
        with self.assertRaises(DiscoveryStopped):
            guard.on_tool_start({'name': 'playwright_click'}, '{}', inputs={'selector': '#same'})
        self.assertEqual(guard.error.code, 'REPEATED_OPERATION')

    def test_summary_redacts_chinese_label_and_exact_description_secret(self):
        summary = _safe_agent_summary('已登录，密码：show-me，token=also-hide', '测试密码：show-me')
        self.assertNotIn('show-me', summary)
        self.assertNotIn('also-hide', summary)
        self.assertIn('<redacted>', summary)

    def test_pending_origin_gate_propagates_stop(self):
        async def stopped():
            raise DiscoveryStopped('CANCELLED', '已取消')
        gate = PendingOriginGate(stopped)
        self.assertTrue(gate.raise_error)
        with self.assertRaises(DiscoveryStopped):
            asyncio.run(gate.on_tool_start({}, '{}'))

    def test_inline_gate_stops_before_guard_counts_a_cancelled_tool(self):
        async def stopped():
            raise DiscoveryStopped('CANCELLED', '已取消')

        guard = DiscoveryToolGuard('https://web.example.test', '', 20)
        manager = AsyncCallbackManager(handlers=[PendingOriginGate(stopped), guard])
        with self.assertRaises(DiscoveryStopped):
            asyncio.run(manager.on_tool_start(
                {'name': 'playwright_click'}, '{}', inputs={'selector': '#submit'}, run_id='cancelled-call',
            ))
        self.assertEqual(guard.tool_calls, 0)
