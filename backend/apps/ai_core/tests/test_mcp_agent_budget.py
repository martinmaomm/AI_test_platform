"""Real MCPAgent/LangChain graph regressions for the project budget adapter."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool, ToolException, tool
from mcp.types import Tool
from mcp_use import MCPClient
from mcp_use.client.connectors.base import BaseConnector

from ai_core.mcp_agent_budget import BudgetedMCPAgent, mcp_graph_recursion_limit
from ai_core.webui_playwright_agent import MCPBrowserToolGuard
from web_testing.generation_contracts import ScenarioPlan
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


class ScriptedToolBatchModel(BaseChatModel):
    """Return one configured multi-tool batch per model turn, then finish."""

    calls: int = 0
    tool_batches: list[list[str]] = []

    @property
    def _llm_type(self):
        return 'mcp_budget_multi_tool_regression'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        batch_index = self.calls
        self.calls += 1
        if batch_index < len(self.tool_batches):
            message = AIMessage(content='', tool_calls=[{
                'name': tool_name,
                'args': {},
                'id': f'batch-{batch_index}-{tool_index}',
                'type': 'tool_call',
            } for tool_index, tool_name in enumerate(self.tool_batches[batch_index])])
        else:
            message = AIMessage(content='{"complete": true}')
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


class AsyncToolTrace:
    """Observe actual async tool overlap without changing graph scheduling."""

    def __init__(self):
        self.started = []
        self.finished = []
        self.active = 0
        self.peak_active = 0

    async def execute(self, tool_name, error=None):
        self.started.append(tool_name)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if error is not None:
                raise error
            self.finished.append(tool_name)
            return f'{tool_name} complete'
        finally:
            self.active -= 1


class ToolOrderCallback(BaseCallbackHandler):
    """Record only the fixture tool lifecycle delivered through MCPAgent.run."""

    raise_error = True
    run_inline = True

    def __init__(self, fixture_tool_names):
        self.fixture_tool_names = set(fixture_tool_names)
        self.events = []
        self._tool_names_by_run = {}

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        tool_name = str((serialized or {}).get('name') or '')
        if tool_name in self.fixture_tool_names:
            self._tool_names_by_run[run_id] = tool_name
            self.events.append(('start', tool_name))

    def on_tool_end(self, output, *, run_id, **kwargs):
        if tool_name := self._tool_names_by_run.pop(run_id, None):
            self.events.append(('end', tool_name))

    def on_tool_error(self, error, *, run_id, **kwargs):
        if tool_name := self._tool_names_by_run.pop(run_id, None):
            self.events.append(('error', tool_name))


def async_fixture_tool(tool_name, trace, *, error=None):
    async def fixture_tool():
        """Run an in-memory asynchronous fixture operation."""
        return await trace.execute(tool_name, error=error)

    return StructuredTool.from_function(
        coroutine=fixture_tool,
        name=tool_name,
        description=f'Async fixture for {tool_name}.',
    )


class BudgetedMCPAgentGraphTests(SimpleTestCase):
    def make_agent(self, model, tools, callbacks=None, *, retry_on_error=True):
        agent = BudgetedMCPAgent(
            llm=model,
            client=MCPClient.from_dict({'mcpServers': {}}),
            max_steps=60,
            callbacks=callbacks or [],
            memory_enabled=False,
            retry_on_error=retry_on_error,
        )
        agent._tools = list(tools) if isinstance(tools, list) else [tools]
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

    def test_multi_tool_round_runs_async_browser_reads_and_checkpoint_in_order(self):
        tool_names = [
            'playwright_navigate',
            'playwright_get_visible_text',
            'aits_save_script',
        ]
        trace = AsyncToolTrace()
        callback = ToolOrderCallback(tool_names)
        agent = self.make_agent(
            ScriptedToolBatchModel(tool_batches=[tool_names]),
            [async_fixture_tool(name, trace) for name in tool_names],
            callbacks=[callback],
        )

        output = asyncio.run(agent.run('one multi-tool graph turn', manage_connector=False))

        self.assertEqual(output, '{"complete": true}')
        self.assertEqual(trace.started, tool_names)
        self.assertEqual(trace.finished, tool_names)
        self.assertEqual(trace.peak_active, 1)
        self.assertEqual(callback.events, [
            ('start', 'playwright_navigate'), ('end', 'playwright_navigate'),
            ('start', 'playwright_get_visible_text'), ('end', 'playwright_get_visible_text'),
            ('start', 'aits_save_script'), ('end', 'aits_save_script'),
        ])

    def test_initialized_agent_rebuilds_local_tools_without_leaking_to_new_agent(self):
        trace = AsyncToolTrace()
        read_tool = async_fixture_tool('playwright_get_visible_text', trace)
        checkpoint_tool = async_fixture_tool('aits_local_checkpoint_fixture', trace)
        agent = self.make_agent(
            ScriptedToolBatchModel(tool_batches=[[
                'playwright_get_visible_text', 'aits_local_checkpoint_fixture',
            ]]),
            read_tool,
        )
        original_executor = agent._agent_executor
        asyncio.run(agent.register_local_tools([checkpoint_tool]))
        output = asyncio.run(agent.run('execute rebuilt local tools', manage_connector=False))

        self.assertIsNot(agent._agent_executor, original_executor)
        self.assertEqual(output, '{"complete": true}')
        self.assertEqual(trace.started, [
            'playwright_get_visible_text', 'aits_local_checkpoint_fixture',
        ])

        fresh_trace = AsyncToolTrace()
        fresh_agent = self.make_agent(
            ScriptedToolBatchModel(tool_batches=[['playwright_get_visible_text']]),
            async_fixture_tool('playwright_get_visible_text', fresh_trace),
        )
        fresh_output = asyncio.run(fresh_agent.run('fresh agent', manage_connector=False))

        self.assertEqual(fresh_output, '{"complete": true}')
        self.assertEqual(fresh_trace.started, ['playwright_get_visible_text'])
        self.assertNotIn('aits_local_checkpoint_fixture', {tool.name for tool in fresh_agent._tools})

    def test_terminal_guard_blocks_later_same_round_browser_write(self):
        trace = AsyncToolTrace()
        agent = self.make_agent(
            ScriptedToolBatchModel(tool_batches=[[
                'playwright_get_visible_text', 'playwright_click',
            ]]),
            [
                async_fixture_tool('playwright_get_visible_text', trace),
                async_fixture_tool('playwright_click', trace),
            ],
            callbacks=[MCPBrowserToolGuard(max_tool_calls=0)],
        )

        output = asyncio.run(agent.run('terminal guard cancellation', manage_connector=False))

        self.assertEqual(output, '{"complete": true}')
        self.assertEqual(trace.started, [])

    def test_retryable_tool_error_keeps_existing_follow_up_behavior(self):
        trace = AsyncToolTrace()
        agent = self.make_agent(
            ScriptedToolBatchModel(tool_batches=[[
                'fixture_retryable_error',
            ], [
                'playwright_get_visible_text',
            ]]),
            [
                async_fixture_tool(
                    'fixture_retryable_error', trace,
                    error=ToolException('temporary fixture failure'),
                ),
                async_fixture_tool('playwright_get_visible_text', trace),
            ],
        )

        output = asyncio.run(agent.run('retryable tool error', manage_connector=False))

        self.assertEqual(output, '{"complete": true}')
        self.assertEqual(agent.llm.calls, 3)
        self.assertEqual(trace.started, [
            'fixture_retryable_error', 'playwright_get_visible_text',
        ])
        self.assertEqual(trace.finished, ['playwright_get_visible_text'])


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

        plan = ScenarioPlan.model_validate({
            'schema_version': 4, 'title': 'Read', 'objective': 'Read current page',
            'instructions': ['Read the current page', 'Verify visible evidence'],
            'success_criteria': ['Visible evidence is retained'],
            'assertion_requirements': [{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            'input_refs': [], 'preconditions': [], 'forbidden_actions': [],
            'credentials_required': False, 'allow_test_data_writes': False,
            'cleanup_expected': False, 'discovery_notes': [], 'risk_level': 'low',
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
