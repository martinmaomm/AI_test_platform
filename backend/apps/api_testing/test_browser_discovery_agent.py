"""No real provider, browser, broker or database is used by these regressions."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase

from .browser_discovery_agent import (
    ALLOWED_BROWSER_TOOLS, DiscoveryStopped, DiscoveryToolGuard,
    prepare_capture_config, run_browser_discovery,
)


class BrowserDiscoveryAgentTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aits-capture-agent-')
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
        self.assertEqual(env['AITS_MCP_NETWORK_CAPTURE'], '1')
        self.assertEqual(env['AITS_MCP_NETWORK_CAPTURE_MAX_REQUESTS'], '30')
        self.assertEqual(env['AITS_MCP_NETWORK_CAPTURE_DIR'], str(Path(self.temp.name).resolve()))
        self.assertNotIn('env', original['mcpServers']['playwright'])
        self.assertNotIn('AITS_MCP_NETWORK_CAPTURE', str(original))

    def test_unsupported_package_fails_without_silently_using_another_browser(self):
        with self.assertRaisesRegex(ValueError, '固定'):
            prepare_capture_config({'mcpServers': {'playwright': {'command': 'npx', 'args': ['unmanaged-server']}}},
                                   self.options['task_id'], self.options['trace_file'], '', {})

    def test_empty_api_origin_collects_metadata_only(self):
        config = prepare_capture_config(self.options['mcp_config'], self.options['task_id'], self.options['trace_file'], '', {})
        self.assertEqual(config['mcpServers']['playwright']['env']['AITS_MCP_NETWORK_CAPTURE_ALLOWED_ORIGINS'], '')

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
        self.assertNotIn('aits_save_script', agent.run.call_args.args[0])
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
