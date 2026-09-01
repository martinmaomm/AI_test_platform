"""Real MCPAgent/LangChain graph regressions for the project budget adapter."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from mcp.types import Tool
from mcp_use import MCPClient
from mcp_use.client.connectors.base import BaseConnector

from ai_core.mcp_agent_budget import BudgetedMCPAgent, mcp_graph_recursion_limit
from ai_core.webui_playwright_agent import MCPBrowserToolGuard
from web_testing.generation_contracts import GoalPlan
from web_testing.mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError


@tool('playwright_get_visible_text')
def read_visible_text() -> str:
    """Return synthetic page evidence without connecting to a browser."""
    return 'synthetic evidence'


@tool('fixture_metadata_read')
def read_fixture_metadata() -> str:
    """Return synthetic non-browser evidence for model-budget verification."""
    return 'synthetic metadata'


class ScriptedLoopModel(BaseChatModel):
    calls: int = 0
    tool_rounds: int = 0
    tool_name: str = 'playwright_get_visible_text'

    @property
    def _llm_type(self):
        return 'mcp_budget_regression'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls <= self.tool_rounds:
            message = AIMessage(content='', tool_calls=[{
                'name': self.tool_name, 'args': {},
                'id': f'budget-{self.calls}', 'type': 'tool_call',
            }])
        else:
            message = AIMessage(content='{"complete": true}')
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


class BudgetedMCPAgentGraphTests(SimpleTestCase):
    def make_agent(self, model, tool, callbacks=None):
        agent = BudgetedMCPAgent(
            llm=model,
            client=MCPClient.from_dict({'mcpServers': {}}),
            max_steps=60,
            callbacks=callbacks or [],
            memory_enabled=False,
        )
        agent._tools = [tool]
        agent._agent_executor = agent._create_agent()
        agent._initialized = True
        return agent

    def test_31_browser_tools_finish_with_the_real_mcpagent_graph(self):
        model = ScriptedLoopModel(tool_rounds=31)
        guard = MCPBrowserToolGuard(max_tool_calls=50)
        agent = self.make_agent(model, read_visible_text, callbacks=[guard])

        output = asyncio.run(agent.run('offline budget regression', manage_connector=False))

        self.assertEqual(output, '{"complete": true}')
        self.assertEqual(model.calls, 32)
        self.assertEqual(agent.recursion_limit, mcp_graph_recursion_limit(60))
        self.assertEqual(agent.recursion_limit, 244)
        self.assertEqual(guard.get_stats()['total_tool_calls'], 31)

    def test_60_model_calls_raise_an_explicit_limit_error(self):
        model = ScriptedLoopModel(tool_rounds=70, tool_name='fixture_metadata_read')
        agent = self.make_agent(model, read_fixture_metadata)

        with self.assertRaises(ModelCallLimitExceededError):
            asyncio.run(agent.run('offline model-limit regression', manage_connector=False))

        self.assertEqual(model.calls, 60)


class MCPPageExplorerInitializationCancellationTests(SimpleTestCase):
    def test_cancellation_interrupts_real_agent_initialization_and_closes_sessions(self):
        class WaitingClient:
            def __init__(self):
                self.opened = 0
                self.closed = 0
                self.initialization_cancelled = False
                self.active_sessions = {}
                self.connector = Mock(spec=BaseConnector)
                self.connector.tools = [Tool(
                    name='playwright_get_visible_text', description='Synthetic browser tool',
                    inputSchema={'type': 'object', 'properties': {}},
                )]

                async def wait_for_tools():
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        self.initialization_cancelled = True
                        raise

                self.connector.list_tools = AsyncMock(side_effect=wait_for_tools)
                self.connector.list_resources = AsyncMock(return_value=[])
                self.connector.list_prompts = AsyncMock(return_value=[])

            def get_all_active_sessions(self):
                return self.active_sessions

            async def create_all_sessions(self):
                self.opened += 1
                self.active_sessions = {'local': SimpleNamespace(connector=self.connector)}
                return self.active_sessions

            async def close_all_sessions(self):
                self.closed += 1
                self.active_sessions = {}

        plan = GoalPlan.model_validate({
            'schema_version': 3, 'title': 'Read', 'objective': 'Read',
            'goals': [
                {'id': 'G1', 'kind': 'setup', 'objective': 'Read',
                 'completion_criteria': 'Visible', 'side_effect': 'none'},
                {'id': 'G2', 'kind': 'verify', 'objective': 'Verify',
                 'completion_criteria': 'Visible', 'side_effect': 'none',
                 'verification': {'mode': 'visible'}},
            ],
        })
        client = WaitingClient()
        checks = {'count': 0}

        def cancel_check():
            checks['count'] += 1
            return checks['count'] >= 3

        explorer = MCPPageExplorer(
            llm_model=ScriptedLoopModel(), mcp_config={'mcpServers': {}},
            cancel_check=cancel_check,
        )
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client):
            with self.assertRaises(MCPPageExplorerError) as raised:
                asyncio.run(asyncio.wait_for(explorer.explore_until_complete(
                    plan=plan, start_path='/', target_url_safe='https://example.invalid/',
                ), timeout=2))

        self.assertEqual(raised.exception.error_code, 'TASK_CANCELLED')
        self.assertTrue(client.initialization_cancelled)
        self.assertEqual(client.opened, 1)
        self.assertEqual(client.closed, 1)
        self.assertFalse(client.active_sessions)
