"""Offline policy regressions for read-only MCP exploration."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

from django.test import SimpleTestCase
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp.types import CallToolResult, TextContent, Tool
from mcp_use.client.connectors.base import BaseConnector
from pydantic import Field

from .generation_contracts import ScenarioSpec
from .mcp_page_explorer import (
    MCPPageExplorer,
    MCPPageExplorerError,
    READ_ONLY_DISABLED_TOOL_MESSAGES,
    ReadOnlyMCPBrowserToolGuard,
)


ACTUAL_GRAPH_OUTPUT = json.dumps({
    'start_url_path': '/', 'visited_paths': ['/users'],
    'page_states': [{'name': 'User list', 'path': '/users'}],
    'elements': [], 'navigation_paths': [],
    'step_evidence': {'S1': {'status': 'confirmed', 'paths': ['/users']}},
    'unresolved_steps': [], 'unresolved_questions': [], 'warnings': [],
    'tool_stats': {'total_tool_calls': 0},
})


class ActualGraphScriptedModel(BaseChatModel):
    calls: int = 0
    target: int = 31
    tool_name: str = 'playwright_get_visible_text'
    exposed: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return 'local_explorer_policy_regression'

    def bind_tools(self, tools, **kwargs):
        self.exposed = [tool.name if hasattr(tool, 'name') else tool['name'] for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls > self.target:
            message = AIMessage(content=ACTUAL_GRAPH_OUTPUT)
        else:
            message = AIMessage(content='', tool_calls=[{
                'name': self.tool_name, 'args': {},
                'id': f'explorer-policy-{self.calls}', 'type': 'tool_call',
            }])
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


class ActualGraphClient:
    """MCP transport boundary with real MCPAgent initialization and graph execution."""

    def __init__(self, *, hang=False):
        self.actual_calls = []
        self.opened = 0
        self.closed = 0
        self.cancelled = False
        self.active_sessions = {}
        self.connector = Mock(spec=BaseConnector)
        names = [
            'playwright_get_visible_text', 'playwright_get_visible_html',
            'playwright_click', 'playwright_fill', 'playwright_close',
            'playwright_evaluate', 'playwright_upload_file',
        ]
        self.connector.tools = [Tool(
            name=name, description='Synthetic explorer policy tool',
            inputSchema={'type': 'object', 'properties': {}},
        ) for name in names]
        self.connector.list_tools = AsyncMock(return_value=self.connector.tools)
        self.connector.list_resources = AsyncMock(return_value=[])
        self.connector.list_prompts = AsyncMock(return_value=[])

        async def invoke(name, args):
            self.actual_calls.append(name)
            if hang and len(self.actual_calls) == 2:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return CallToolResult(content=[TextContent(type='text', text='Synthetic page state')])

        self.connector.call_tool = AsyncMock(side_effect=invoke)

    def get_all_active_sessions(self):
        return self.active_sessions

    async def create_all_sessions(self):
        self.opened += 1
        self.active_sessions = {'local': SimpleNamespace(connector=self.connector)}
        return self.active_sessions

    async def close_all_sessions(self):
        self.closed += 1
        self.active_sessions = {}


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

    async def explore_with_actual_graph(self, model, client, *, supplemental=False, cancel_check=None):
        explorer = MCPPageExplorer(
            llm_model=model, mcp_config={'mcpServers': {}}, cancel_check=cancel_check,
        )
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client):
            kwargs = {
                'scenario': scenario(), 'start_path': '/',
                'target_url_safe': 'https://example.invalid/',
            }
            if supplemental:
                return await explorer.explore_missing_evidence(
                    **kwargs,
                    existing_snapshot=SimpleNamespace(unresolved_steps=['S1'], step_evidence={}),
                )
            return await explorer.explore(**kwargs)

    def test_actual_graph_main_and_supplemental_filter_close_and_finish_at_50_tools(self):
        for supplemental in (False, True):
            with self.subTest(supplemental=supplemental):
                model = ActualGraphScriptedModel(target=50)
                client = ActualGraphClient()
                snapshot = asyncio.run(self.explore_with_actual_graph(
                    model, client, supplemental=supplemental,
                ))
                self.assertEqual(snapshot.tool_stats.total_tool_calls, 50)
                self.assertEqual(len(client.actual_calls), 50)
                self.assertEqual(model.calls, 51)
                self.assertEqual(client.opened, 1)
                self.assertEqual(client.closed, 1)
                self.assertFalse(client.active_sessions)
                self.assertTrue({
                    'playwright_close', 'playwright_evaluate', 'playwright_upload_file',
                }.isdisjoint(model.exposed))
                self.assertTrue({
                    'playwright_click', 'playwright_fill', 'playwright_get_visible_text',
                }.issubset(model.exposed))

    def test_actual_graph_blocks_the_51st_browser_call_before_transport(self):
        model = ActualGraphScriptedModel(target=51)
        client = ActualGraphClient()
        with self.assertRaises(MCPPageExplorerError) as raised:
            asyncio.run(self.explore_with_actual_graph(model, client))
        self.assertEqual(raised.exception.error_code, 'tool_budget')
        self.assertEqual(raised.exception.tool_stats['total_tool_calls'], 50)
        self.assertEqual(len(client.actual_calls), 50)
        self.assertEqual(client.closed, 1)
        self.assertFalse(client.active_sessions)

    def test_actual_graph_running_cancellation_closes_sessions(self):
        model = ActualGraphScriptedModel(target=40)
        client = ActualGraphClient(hang=True)
        with self.assertRaises(MCPPageExplorerError) as raised:
            asyncio.run(asyncio.wait_for(self.explore_with_actual_graph(
                model, client, cancel_check=lambda: len(client.actual_calls) >= 2,
            ), timeout=3))
        self.assertEqual(raised.exception.error_code, 'TASK_CANCELLED')
        self.assertTrue(client.cancelled)
        self.assertEqual(client.closed, 1)
        self.assertFalse(client.active_sessions)

    def test_only_expected_tools_are_disabled_for_main_and_supplemental_exploration(self):
        self.assertEqual(
            set(READ_ONLY_DISABLED_TOOL_MESSAGES),
            {'playwright_close', 'playwright_evaluate', 'playwright_upload_file'},
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
                agent.initialize.assert_awaited_once()
                agent.run.assert_awaited_once_with(
                    ANY, manage_connector=False,
                )

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
