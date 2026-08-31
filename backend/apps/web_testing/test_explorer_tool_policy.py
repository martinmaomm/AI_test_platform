"""Offline policy regressions for read-only MCP exploration."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from .generation_contracts import ScenarioSpec
from .mcp_page_explorer import (
    MCPPageExplorer,
    READ_ONLY_DISABLED_TOOL_MESSAGES,
    ReadOnlyMCPBrowserToolGuard,
)


def scenario():
    return ScenarioSpec.model_validate({
        'title': '查看用户',
        'objective': '读取用户列表页面',
        'steps': [{
            'id': 'S1', 'name': '打开列表', 'intent': 'read',
            'target_hint': '用户列表', 'expected': '列表可见',
        }],
        'assertions': [{
            'id': 'A1', 'name': '列表可见', 'target_hint': '用户列表',
            'expected': '列表可见', 'step_id': 'S1',
        }],
    })


def explorer_output():
    return json.dumps({
        'start_url_path': '/',
        'visited_paths': ['/users'],
        'page_states': [{'name': '用户列表', 'path': '/users'}],
        'elements': [],
        'navigation_paths': [],
        'step_evidence': {'S1': {'status': 'confirmed', 'paths': ['/users']}},
        'unresolved_steps': [],
        'unresolved_questions': [],
        'warnings': [],
        'tool_stats': {'total_tool_calls': 0},
    }, ensure_ascii=False)


class ExplorerToolPolicyTests(SimpleTestCase):
    generation_id = '52ae9c6a-50a7-424d-9373-423750c6fd9f'

    def make_explorer(self):
        return MCPPageExplorer(
            llm_model=AsyncMock(),
            mcp_config={'mcpServers': {}},
            generation_id=self.generation_id,
        )

    def fake_runtime(self):
        client = AsyncMock()
        agent = AsyncMock()
        agent.run.return_value = explorer_output()
        return (
            client,
            agent,
            patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client),
            patch('web_testing.mcp_page_explorer.MCPAgent', return_value=agent),
        )

    def test_only_expected_tools_are_disabled_for_main_and_supplemental_exploration(self):
        self.assertEqual(
            set(READ_ONLY_DISABLED_TOOL_MESSAGES),
            {'playwright_evaluate', 'playwright_upload_file'},
        )
        for mode in ('main', 'supplemental'):
            with self.subTest(mode=mode):
                explorer = self.make_explorer()
                client, agent, client_patch, agent_patch = self.fake_runtime()
                with client_patch, agent_patch as agent_factory:
                    if mode == 'main':
                        asyncio.run(explorer.explore(
                            scenario=scenario(), start_path='/',
                            target_url_safe='https://example.invalid/',
                        ))
                    else:
                        asyncio.run(explorer.explore_missing_evidence(
                            scenario=scenario(),
                            existing_snapshot=SimpleNamespace(
                                unresolved_steps=['S1'], step_evidence={},
                            ),
                            start_path='/',
                            target_url_safe='https://example.invalid/',
                        ))
                self.assertEqual(
                    agent_factory.call_args.kwargs['disallowed_tools'],
                    list(READ_ONLY_DISABLED_TOOL_MESSAGES),
                )
                client.create_all_sessions.assert_awaited_once()
                client.close_all_sessions.assert_awaited_once()
                agent.run.assert_awaited_once()

    def test_guard_blocks_each_disabled_tool_with_its_own_message(self):
        for tool_name, message in READ_ONLY_DISABLED_TOOL_MESSAGES.items():
            with self.subTest(tool_name=tool_name):
                guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=3)
                with self.assertRaises(Exception) as raised:
                    guard.on_tool_start({'name': tool_name}, '', inputs={})
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(guard.terminal_error.error_kind, 'read_only_violation')
                self.assertEqual(guard.get_stats()['blocked_tool_calls'], 1)
                self.assertEqual(guard.get_stats()['last_blocked_operation']['tool_name'], tool_name)

    def test_reading_clicking_and_budget_are_unchanged(self):
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=2)
        for run_id, tool_name, inputs in (
            ('read', 'playwright_get_visible_text', {}),
            ('click', 'playwright_click', {'selector': '.open-filter'}),
        ):
            guard.on_tool_start({'name': tool_name}, '', run_id=run_id, inputs=inputs)
            guard.on_tool_end('ok', run_id=run_id)
        stats = guard.get_stats()
        self.assertEqual(stats['total_tool_calls'], 2)
        self.assertEqual(stats['tool_counts'], {
            'playwright_get_visible_text': 1,
            'playwright_click': 1,
        })
        self.assertEqual(stats['failed_tool_calls'], 0)
        self.assertEqual(stats['blocked_tool_calls'], 0)
        with self.assertRaises(Exception) as raised:
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', inputs={})
        self.assertEqual(guard.terminal_error.error_kind, 'tool_budget')
        self.assertIn('上限（2 次）', str(raised.exception))
