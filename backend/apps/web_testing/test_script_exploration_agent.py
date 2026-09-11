"""Offline contract tests for the v5 incremental script exploration agent."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationTraceRecorder, _tool_failed
from .script_exploration_agent import (
    EXPLORATION_SCRIPT_CONSTRAINTS,
    ScriptExplorationAgent,
    ScriptExplorationToolGuard,
)


PARTIAL_SCRIPT = '''\
async def run(page, variables):
    await page.goto('https://example.test/catalog')
    # PENDING_STEP: {"reason":"详情页操作尚未在当前 trace 中观察到。"}
'''

COMPLETE_SCRIPT = '''\
from playwright.async_api import expect

async def run(page, variables):
    await page.goto('https://example.test/catalog')
    await expect(page.get_by_role("heading")).to_be_visible()
'''


def brief(**overrides):
    value = {
        'schema_version': 5,
        'title': '目录检查',
        'objective': '检查目录页面。',
        'original_user_target': '检查目录页面。',
        'instructions': ['打开目录'],
        'allow_test_data_writes': True,
        'explicit_read_only': False,
        'cleanup_expected': False,
        'forbidden_actions': [],
    }
    value.update(overrides)
    return value


class ScriptExplorationAgentTests(SimpleTestCase):
    def make_agent(self, callback=None, llm_model=None):
        return ScriptExplorationAgent(
            llm_model=llm_model or Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4()),
            cancel_check=lambda: False, exploration_timeout_seconds=10,
            checkpoint_callback=callback,
        )

    def run_with(self, agent_type, *, callback=None):
        class Client:
            opened = closed = 0

            async def create_all_sessions(self):
                type(self).opened += 1

            async def close_all_sessions(self):
                type(self).closed += 1

        client = Client()
        with tempfile.TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir), patch(
            'web_testing.script_exploration_agent.prepare_playwright_mcp_output_config',
            side_effect=lambda config, _generation_id: config,
        ), patch('web_testing.script_exploration_agent.MCPClient.from_dict', return_value=client), patch(
            'web_testing.script_exploration_agent.MCPAgent', agent_type,
        ):
            result = asyncio.run(self.make_agent(callback).generate(
                brief=brief(), target_url='https://example.test/catalog',
            ))
        return result, client

    def make_guard(self) -> ScriptExplorationToolGuard:
        return ScriptExplorationToolGuard(
            policy=ExplorationPolicy(
                namespace='test-script-guard', data_scope='scenario_namespace',
                explicit_read_only=False, allow_test_data_writes=True,
                cleanup_expected=False,
            ),
            trace_recorder=ExplorationTraceRecorder('/'),
            target_url='https://example.test/',
            progress_notifier=lambda: None,
            stop_check=lambda: '',
        )

    @staticmethod
    def html_output(html: str) -> str:
        # Match the actual MCP string representation, not an eval-able fixture.
        return "[TextContent(type='text', text=" + repr('HTML content:\n' + html) + ')]'

    @staticmethod
    def operation(guard, tool_name, inputs=None, output='ok'):
        run_id = uuid4()
        guard.on_tool_start({'name': tool_name}, '', run_id=run_id, inputs=inputs or {})
        guard.on_tool_end(output, run_id=run_id)

    def test_empty_submit_then_corrected_fields_can_retry_in_any_language(self):
        # Reproduce the incident, with opaque field names and unrelated labels.
        # The callback guard must not assign authentication semantics to either.
        for label in ('登录', 'Continue', 'Weiter', '進む', 'Enviar'):
            with self.subTest(label=label):
                guard = self.make_guard()
                page = self.html_output(
                    '<form><input name="f17"><input name="f23" type="password">'
                    f'<button>{label}</button></form>'
                )
                self.operation(guard, 'playwright_get_visible_html', output=page)
                self.operation(guard, 'playwright_click', {'selector': f'text={label}'})
                self.operation(guard, 'playwright_get_visible_text', output='×')
                self.operation(guard, 'playwright_get_visible_html', output=page)
                self.operation(guard, 'playwright_fill', {'selector': '[name=f17]', 'value': 'fixture-user'})
                self.operation(guard, 'playwright_get_visible_text', output='×')
                self.operation(guard, 'playwright_fill', {'selector': '[name=f23]', 'value': 'fixture-value'})
                self.operation(guard, 'playwright_get_visible_text', output='×')
                self.operation(guard, 'playwright_click', {'selector': f'text={label}'})
                self.assertIsNone(guard.terminal_error)
                self.assertEqual(guard.get_stats()['tool_counts']['playwright_click'], 2)
                self.assertEqual(guard.get_stats()['blocked_tool_calls'], 0)

    def test_repeated_form_observations_do_not_claim_authentication_failure(self):
        guard = self.make_guard()
        page = self.html_output(
            '<form class="login-form"><input name="username">'
            '<input name="password" type="password"><button>登录</button></form>'
        )
        self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_click', {'selector': 'button'})
        for _ in range(3):
            self.operation(guard, 'playwright_get_visible_html', output=page)
        self.assertIsNone(guard.terminal_error)

    def test_hidden_form_and_error_copy_do_not_create_authentication_state(self):
        guard = self.make_guard()
        page = self.html_output(
            '<div hidden><form class="login-form"><input name="username">'
            '<input type="password"><button>登录</button></form></div>'
            '<main><button id="next">登录记录</button><p>没有找到匹配记录</p></main>'
        )
        self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_click', {'selector': '#next'})
        self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_click', {'selector': '#next'})
        self.assertIsNone(guard.terminal_error)
        # An unchanged third operation still stops, for the generic loop reason.
        with self.assertRaisesRegex(Exception, '相同|重复'):
            self.operation(guard, 'playwright_click', {'selector': '#next'})
        self.assertEqual(guard.termination_reason, 'repeated_interaction')

    def test_successful_input_correction_allows_retry_when_visible_output_is_unchanged(self):
        guard = self.make_guard()
        page = self.html_output(
            '<section><input id="f7"><button id="go">↪</button></section>'
        )
        self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_fill', {'selector': '#f7', 'value': 'first'})
        for _ in range(2):
            self.operation(guard, 'playwright_click', {'selector': '#go'})
            self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_fill', {'selector': '#f7', 'value': 'corrected'})
        self.operation(guard, 'playwright_get_visible_html', output=page)
        self.operation(guard, 'playwright_click', {'selector': '#go'})
        self.assertIsNone(guard.terminal_error)
        # Further value changes must not turn the correction window into a loop.
        self.operation(guard, 'playwright_fill', {'selector': '#f7', 'value': 'another'})
        with self.assertRaisesRegex(Exception, '纠错|重复|相同'):
            self.operation(guard, 'playwright_click', {'selector': '#go'})
        self.assertEqual(guard.termination_reason, 'repeated_interaction')

    def test_fake_agent_incrementally_saves_full_draft_and_checkpoints(self):
        checkpoints = []

        class Agent:
            tool_names = []

            def __init__(self, **kwargs):
                self.guard = kwargs['callbacks'][0]

            async def initialize(self):
                return None

            async def register_local_tools(self, tools):
                self.tools = tools
                type(self).tool_names = [tool.name for tool in tools]

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start(
                    {'name': 'playwright_navigate'}, '', run_id='goto',
                    inputs={'url': 'https://example.test/catalog', 'headless': True},
                )
                self.guard.on_tool_end({'content': [{'type': 'text', 'text': 'catalog ready'}]}, run_id='goto')
                feedback = await self.tools[0].ainvoke({
                    'code': PARTIAL_SCRIPT, 'completed_steps': ['打开目录'],
                    'remaining_steps': ['确认详情页操作'],
                    'variables': [{'name': 'item_name', 'value': 'fixed item', 'required': True, 'is_secret': False, 'description': '固定测试值'}],
                    'completion': 'complete',
                })
                assert feedback['status'] == 'accepted'
                return '草稿已通过工具保存。'

        result, client = self.run_with(Agent, callback=checkpoints.append)
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertEqual(Agent.tool_names, ['save_script_draft', 'patch_script_draft', 'read_script_draft'])
        self.assertIn('await page.goto', result.script_draft)
        self.assertEqual(result.completion, 'partial')
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['确认详情页操作'])
        self.assertGreaterEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[-1]['script_draft'], result.script_draft)

    def test_bad_candidate_does_not_replace_last_valid_draft_after_exception(self):
        class Agent:
            def __init__(self, **kwargs):
                self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                accepted = await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT})
                rejected = await self.tools[0].ainvoke({
                    'code': 'async def run(page, variables):\n    return None\n',
                })
                assert accepted['status'] == 'accepted'
                assert rejected['status'] == 'rejected'
                raise ConnectionError('connection reset')

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, 'transient')
        self.assertEqual(result.script_draft, PARTIAL_SCRIPT.strip())
        self.assertEqual(result.snapshot['termination_reason'], 'transient')

    def test_no_finalization_tool_or_protocol_is_registered(self):
        class Agent:
            instructions = ''
            tools = []

            def __init__(self, **kwargs):
                type(self).instructions = kwargs['additional_instructions']

            async def initialize(self): pass

            async def register_local_tools(self, tools): type(self).tools = [tool.name for tool in tools]

            async def run(self, *_args, **_kwargs): return ''

        self.run_with(Agent)
        self.assertEqual(Agent.tools, ['save_script_draft', 'patch_script_draft', 'read_script_draft'])
        self.assertNotIn('finalize_exploration_path', Agent.instructions)
        self.assertNotIn('finalization_protocol', Agent.instructions)

    def test_observation_selector_failure_is_failed_but_page_copy_is_not(self):
        self.assertTrue(_tool_failed(
            'Element with selector "#missing" not found', tool_name='playwright_get_visible_html',
        ))
        self.assertFalse(_tool_failed(
            '<main>没有找到匹配记录 not found</main>', tool_name='playwright_get_visible_html',
        ))

    def test_tool_failure_is_preserved_in_v5_snapshot(self):
        class Agent:
            def __init__(self, **kwargs): self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='html', inputs={'selector': '#missing'})
                self.guard.on_tool_end('Element with selector "#missing" not found', run_id='html')
                await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT})
                return '保存 partial 草稿。'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.snapshot['schema_version'], 5)
        self.assertEqual(result.snapshot['events'][0]['status'], 'failed')
        self.assertIn('raw_output', result.snapshot['events'][0])

    def test_trace_keeps_bounded_raw_observation_not_only_ui_summary(self):
        long_html = '<main>' + ('保留原始观察内容\n' * 2500) + '</main>'

        class Agent:
            def __init__(self, **kwargs): self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='html', inputs={'selector': 'main'})
                self.guard.on_tool_end(long_html, run_id='html')
                await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT})
                return 'saved'

        result, _ = self.run_with(Agent)
        event = result.snapshot['events'][0]
        self.assertGreater(len(event['raw_output']), len(event['result_excerpt']))
        self.assertLessEqual(len(event['raw_output']), 20000)

    def test_repair_runtime_values_are_templated_in_trace_and_snapshot(self):
        secret = 'repair-runtime-secret'
        agent = self.make_agent()
        agent._brief = brief(repair_only=True, runtime_input_values={'UI_TEST_PASSWORD': secret})
        agent._target_url = 'https://example.test/catalog'
        with tempfile.TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            agent._configure_trace(output_generation_id=str(uuid4()))
            agent._guard.on_tool_start(
                {'name': 'playwright_fill'}, '', run_id='fill',
                inputs={'selector': 'input[type=password]', 'value': secret},
            )
            agent._guard.on_tool_end(f'filled {secret}', run_id='fill')
            snapshot = agent._snapshot()
            trace_files = list((Path(temp_dir) / 'logs' / 'playwright-mcp').glob('*.script-v5.trace.jsonl'))
            trace_text = trace_files[0].read_text(encoding='utf-8') if trace_files else ''
        self.assertNotIn(secret, str(snapshot))
        self.assertNotIn(secret, trace_text)
        self.assertIn('{{UI_TEST_PASSWORD}}', str(snapshot))
        self.assertIn('{{UI_TEST_PASSWORD}}', trace_text)

    def test_empty_exploration_starts_with_honest_entry_seed_not_a_false_pass(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return '没有可以保存的代码。'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, '')
        self.assertIn('场景：', result.script_draft)
        self.assertIn("await page.goto('https://example.test/catalog')", result.script_draft)
        self.assertIn('PENDING_STEP:', result.script_draft)
        self.assertNotIn('expect(', result.script_draft)
        self.assertEqual(result.completion, 'partial')

    def test_early_agent_failure_keeps_checkpointed_entry_seed(self):
        checkpoints = []

        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self):
                raise ConnectionError('connection reset')

        result, _ = self.run_with(Agent, callback=checkpoints.append)
        self.assertEqual(result.error_code, 'transient')
        self.assertIn("await page.goto('https://example.test/catalog')", result.script_draft)
        self.assertIn('PENDING_STEP:', result.script_draft)
        self.assertTrue(checkpoints)
        self.assertIn('仅生成入口', checkpoints[0]['snapshot']['artifact']['remaining_steps'][0])

    def test_entry_seed_escapes_user_description_as_python_text(self):
        agent = ScriptExplorationAgent(
            llm_model=None, mcp_config={}, generation_id=None,
            cancel_check=None, exploration_timeout_seconds=10,
        )
        agent._brief = {'title': '测试标题', 'objective': '检查 C:\\Users\\test 和 """引号"""'}
        agent._target_url = 'https://example.test/catalog?sort=name#/users'
        agent._install_entry_seed()
        compile(agent._last_valid_script, '<entry-seed>', 'exec')
        self.assertEqual(agent._quality_report(agent._last_valid_script)['blockers'], [])

    def test_partial_marker_text_in_string_gets_a_real_pending_comment(self):
        agent = self.make_agent()
        agent._target_url = 'https://example.test/catalog'
        script = COMPLETE_SCRIPT + "\n    example = '# PENDING_STEP: example text'\n"
        feedback = agent._consider_candidate(
            script, completed_steps=[], remaining_steps=[], variables=[],
            completion='partial', source='test',
        )
        state = agent._quality_report(agent._last_valid_script)['assertion_state']
        self.assertEqual(feedback['status'], 'accepted')
        self.assertTrue(feedback['pending_step_inserted'])
        self.assertEqual([item['kind'] for item in state['pending']], ['step'])
        self.assertEqual(agent._artifact['completion'], 'partial')

    def test_complete_script_keeps_marker_looking_string_as_complete(self):
        agent = self.make_agent()
        agent._target_url = 'https://example.test/catalog'
        script = COMPLETE_SCRIPT + "\n    example = '# PENDING_STEP: example text'\n"
        feedback = agent._consider_candidate(
            script, completed_steps=[], remaining_steps=[], variables=[],
            completion='complete', source='test',
        )
        self.assertEqual(feedback['status'], 'accepted')
        self.assertFalse(feedback['pending_step_inserted'])
        self.assertEqual(agent._artifact['completion'], 'complete')

    def test_partial_draft_without_assertion_gets_pending_assertion_reason(self):
        agent = self.make_agent()
        agent._target_url = 'https://example.test/catalog'
        script = COMPLETE_SCRIPT.replace('    await expect(page.get_by_role("heading")).to_be_visible()\n', '')
        agent._consider_candidate(
            script, completed_steps=[], remaining_steps=[], variables=[],
            completion='partial', source='test',
        )
        state = agent._quality_report(agent._last_valid_script)['assertion_state']
        self.assertEqual([item['kind'] for item in state['pending']], ['assertion'])
        self.assertEqual(agent._artifact['remaining_steps'], ['草稿尚无真实断言，需补充可验证结果。'])

    def test_real_pending_marker_is_not_automatically_removed(self):
        agent = self.make_agent()
        agent._target_url = 'https://example.test/catalog'
        agent._consider_candidate(
            PARTIAL_SCRIPT, completed_steps=[], remaining_steps=[], variables=[],
            completion='partial', source='test',
        )
        self.assertEqual(agent._last_valid_script, PARTIAL_SCRIPT.strip())
        self.assertEqual(agent._quality_report(agent._last_valid_script)['assertion_state']['pending_count'], 1)

    def test_invalid_entry_does_not_create_browser_or_slash_seed(self):
        with patch('web_testing.script_exploration_agent.MCPClient.from_dict') as client:
            result = asyncio.run(self.make_agent().generate(brief=brief(), target_url='/'))
        client.assert_not_called()
        self.assertEqual(result.error_code, 'INVALID_TARGET_URL')
        self.assertEqual(result.script_draft, '')

    def test_prompt_uses_only_description_for_login_and_preserves_full_url(self):
        agent = self.make_agent()
        goal = '目标网址：https://example.test/catalog?sort=name#/users\n登录账号 fixture-user 密码 fixture-pass'
        agent._brief = brief(original_user_target=goal)
        agent._target_url = 'https://example.test/catalog?sort=name#/users'
        import json
        prompt = json.loads(agent._prompt())
        self.assertEqual(prompt['target_url'], agent._target_url)
        self.assertEqual(prompt['brief']['original_user_target'], goal)
        self.assertNotIn('credentials', prompt)
        self.assertNotIn('start_path', prompt)
        agent._install_entry_seed()
        self.assertIn(repr(agent._target_url), agent._last_valid_script)

    def test_prompt_explains_evidence_based_bounded_form_recovery(self):
        from ai_core.webui_playwright_agent import (
            MCP_INTERACTION_REPEAT_LIMIT,
            MCP_INTERACTION_CORRECTION_LIMIT,
        )
        self.assertIn('先检查当前可见结构', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('不代表认证或业务成功', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('不得猜测凭据', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('不要依赖固定语言', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn(f'最多执行 {MCP_INTERACTION_REPEAT_LIMIT} 次', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn(f'最多执行 {MCP_INTERACTION_CORRECTION_LIMIT} 次', EXPLORATION_SCRIPT_CONSTRAINTS)

    def test_final_text_static_complete_fallback_stays_partial_without_generic_pending_step(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return f'```python\n{COMPLETE_SCRIPT}\n```'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.completion, 'partial')
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], [])
        self.assertNotIn('智能体未确认完成', result.script_draft)
        self.assertIn('partial 回退', result.snapshot['warnings'][0])

    def test_code_only_never_creates_mcp_client_or_browser(self):
        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{PARTIAL_SCRIPT}\n```'

        agent = self.make_agent(llm_model=LLM())
        with patch('web_testing.script_exploration_agent.MCPClient.from_dict') as client_factory:
            result = asyncio.run(agent.generate(
                brief=brief(), target_url='https://example.test/catalog',
                saved_snapshot={'schema_version': 5, 'events': [{'event_id': 'saved'}], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
                script_draft=PARTIAL_SCRIPT, code_only=True,
            ))
        client_factory.assert_not_called()
        self.assertEqual(result.completion, 'partial')
        self.assertIn('PENDING_STEP:', result.script_draft)
        self.assertEqual(result.snapshot['events'], [{'event_id': 'saved'}])

    def test_code_only_repair_uses_current_complete_candidate_not_old_pending_steps(self):
        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{COMPLETE_SCRIPT}\n```'

        snapshot = {
            'schema_version': 5,
            'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {},
            'artifact': {
                'completion': 'partial',
                'remaining_steps': ['旧草稿的详情页待办'],
            },
        }
        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(repair_only=True), target_url='https://example.test/catalog',
            saved_snapshot=snapshot, script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.script_draft, COMPLETE_SCRIPT.strip())
        self.assertEqual(result.completion, 'complete')
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], [])
        self.assertNotIn('PENDING_STEP:', result.script_draft)

    def test_code_only_keeps_current_pending_marker_not_old_snapshot_pending_step(self):
        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{PARTIAL_SCRIPT}\n```'

        snapshot = {
            'schema_version': 5,
            'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {},
            'artifact': {'completion': 'partial', 'remaining_steps': ['过期待办']},
        }
        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(repair_only=True), target_url='https://example.test/catalog',
            saved_snapshot=snapshot, script_draft=COMPLETE_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.completion, 'partial')
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['详情页操作尚未在当前 trace 中观察到。'])
        self.assertNotIn('过期待办', result.script_draft)

    def test_code_only_records_all_current_pending_marker_reasons_once(self):
        script = COMPLETE_SCRIPT + '''\
    # PENDING_STEP: {"reason":"补充筛选操作"}
    # PENDING_ASSERTION: {"reason":"补充筛选结果断言"}
    # PENDING_STEP: {"reason":"补充筛选操作"}
'''

        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{script}\n```'

        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(repair_only=True), target_url='https://example.test/catalog',
            saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
            script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['补充筛选操作', '补充筛选结果断言'])

    def test_code_only_without_assertion_stays_partial_with_pending_assertion(self):
        class LLM:
            async def ainvoke(self, _prompt):
                return "```python\nasync def run(page, variables):\n    await page.goto('https://example.test/catalog')\n```"

        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(repair_only=True), target_url='https://example.test/catalog',
            saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
            script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.completion, 'partial')
        self.assertIn('PENDING_ASSERTION:', result.script_draft)
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['草稿尚无真实断言，需补充可验证结果。'])

    def test_code_only_static_complete_candidate_remains_partial_without_generic_pending_step(self):
        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{COMPLETE_SCRIPT}\n```'

        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(), target_url='https://example.test/catalog',
            saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
            script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.completion, 'partial')
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], [])
        self.assertNotIn('智能体未确认完成', result.script_draft)

    def test_code_only_extracts_full_fenced_code_while_snapshot_stays_bounded(self):
        encoded = 'A' * 21_000
        script = COMPLETE_SCRIPT + f"\n    payload = {encoded!r}\n"

        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{script}\n```'

        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(), target_url='https://example.test/catalog',
            saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
            script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertIn(encoded, result.script_draft)
        self.assertLessEqual(len(result.snapshot['model_output_raw']), 20_000)
        self.assertIn('<base64-omitted>', result.snapshot['model_output_raw'])

    def test_code_only_rejects_oversized_fenced_candidate_and_keeps_original_draft(self):
        oversized = 'A' * 200_001
        script = COMPLETE_SCRIPT + f"\n    payload = {oversized!r}\n"

        class LLM:
            async def ainvoke(self, _prompt):
                return f'```python\n{script}\n```'

        result = asyncio.run(self.make_agent(llm_model=LLM()).generate(
            brief=brief(), target_url='https://example.test/catalog',
            saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
            script_draft=PARTIAL_SCRIPT, code_only=True,
        ))
        self.assertEqual(result.script_draft, PARTIAL_SCRIPT.strip())
        self.assertEqual(result.error_code, 'SCRIPT_TOO_LONG')
        self.assertIn('超过 200000 字符', result.error_message)
        feedback = result.snapshot['draft_state']['latest_candidate_feedback']
        self.assertEqual(feedback['error_code'], 'SCRIPT_TOO_LONG')
        self.assertIn('超过 200000 字符', feedback['message'])
        self.assertTrue(any('超过 200000 字符' in warning for warning in result.snapshot['warnings']))

    def test_final_text_fallback_extracts_full_fenced_code_while_snapshot_stays_bounded(self):
        encoded = 'A' * 21_000
        script = COMPLETE_SCRIPT + f"\n    payload = {encoded!r}\n"

        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return f'```python\n{script}\n```'

        result, _ = self.run_with(Agent)
        self.assertIn(encoded, result.script_draft)
        self.assertLessEqual(len(result.snapshot['model_output_raw']), 20_000)
        self.assertIn('<base64-omitted>', result.snapshot['model_output_raw'])

    def test_final_text_fallback_rejects_oversized_candidate_with_result_error(self):
        oversized = 'A' * 200_001
        script = COMPLETE_SCRIPT + f"\n    payload = {oversized!r}\n"

        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return f'```python\n{script}\n```'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, 'SCRIPT_TOO_LONG')
        self.assertIn('超过 200000 字符', result.error_message)
        self.assertIn('仅生成入口', result.script_draft)

    def test_prepares_task_scoped_mcp_output_and_keeps_trace_file(self):
        prepared_ids = []

        class Client:
            async def create_all_sessions(self): pass

            async def close_all_sessions(self): pass

        class Agent:
            def __init__(self, **kwargs): self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='page', inputs={'selector': 'main'})
                self.guard.on_tool_end('页面已加载', run_id='page')
                await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT})
                return 'saved'

        with tempfile.TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir), patch(
            'web_testing.script_exploration_agent.prepare_playwright_mcp_output_config',
            side_effect=lambda config, generation_id: prepared_ids.append(generation_id) or config,
        ), patch('web_testing.script_exploration_agent.MCPClient.from_dict', return_value=Client()), patch(
            'web_testing.script_exploration_agent.MCPAgent', Agent,
        ):
            result = asyncio.run(self.make_agent().generate(
                brief=brief(), target_url='https://example.test/catalog',
            ))
            trace_files = list((Path(temp_dir) / 'logs' / 'playwright-mcp').glob('*.script-v5.trace.jsonl'))
            trace_text = trace_files[0].read_text(encoding='utf-8') if trace_files else ''
        self.assertTrue(prepared_ids)
        self.assertEqual(len(trace_files), 1)
        self.assertIn('页面已加载', trace_text)
        self.assertEqual(result.error_code, '')

    def test_checkpoint_false_returns_unsaved_and_stops(self):
        checkpoint_calls = []

        def checkpoint(_payload):
            checkpoint_calls.append(_payload)
            # The entry seed must first be durable; fail the subsequent local
            # tool save so its response can be asserted as ``unsaved``.
            return len(checkpoint_calls) == 1

        class Agent:
            feedback = None

            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                feedback = await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT})
                type(self).feedback = feedback
                try:
                    self.guard.on_tool_start(
                        {'name': 'playwright_get_visible_text'}, '', run_id='after-save', inputs={'selector': 'main'},
                    )
                except Exception:
                    type(self).stopped_before_browser = True
                return '不应在 checkpoint 失败后继续。'

        result, _ = self.run_with(Agent, callback=checkpoint)
        self.assertEqual(result.error_code, 'CHECKPOINT_FAILED')
        self.assertTrue(checkpoint_calls)
        self.assertEqual(Agent.feedback['status'], 'unsaved')
        self.assertTrue(Agent.stopped_before_browser)
        self.assertEqual(result.snapshot['termination_reason'], 'CHECKPOINT_FAILED')

    def test_code_only_without_draft_calls_model_before_reporting_missing_draft(self):
        class LLM:
            calls = 0

            async def ainvoke(self, prompt):
                type(self).calls += 1
                self.assertIn('code_only', prompt)
                return '无法从空 trace 推断安全脚本。'

            def assertIn(self, value, text):
                if value not in text:
                    raise AssertionError(f'{value!r} not in prompt')

        agent = self.make_agent(llm_model=LLM())
        with patch('web_testing.script_exploration_agent.MCPClient.from_dict') as client_factory:
            result = asyncio.run(agent.generate(
                brief=brief(), target_url='https://example.test/catalog',
                saved_snapshot={'schema_version': 5, 'events': [], 'page_states': [], 'locator_evidence': [], 'tool_stats': {}},
                script_draft='', code_only=True,
            ))
        self.assertEqual(LLM.calls, 1)
        client_factory.assert_not_called()
        self.assertEqual(result.error_code, 'NO_SCRIPT_DRAFT')
        self.assertNotIn('await page.goto', result.script_draft)
        self.assertEqual(result.snapshot['events'], [])

    def test_code_only_restores_artifact_and_passes_repair_diagnostics_to_model(self):
        class LLM:
            prompt = ''

            async def ainvoke(self, prompt):
                type(self).prompt = prompt
                return f'```python\n{PARTIAL_SCRIPT}\n```'

        snapshot = {
            'schema_version': 5,
            'events': [{'event_id': 'saved'}], 'page_states': [], 'locator_evidence': [], 'tool_stats': {},
            'repair_diagnostics': {'error_code': 'SYNTAX_ERROR', 'line': 3},
            'artifact': {
                'revision': 7, 'completion': 'partial', 'completed_steps': ['打开目录'],
                'remaining_steps': ['补充断言'],
                'variables': [{'name': 'fixed_name', 'value': 'constant', 'is_secret': False, 'required': True, 'description': '固定值'}],
            },
            'draft_state': {'last_valid_script': PARTIAL_SCRIPT},
        }
        agent = self.make_agent(llm_model=LLM())
        result = asyncio.run(agent.generate(
            brief=brief(), target_url='https://example.test/catalog',
            saved_snapshot=snapshot, script_draft='', code_only=True,
        ))
        self.assertIn('SYNTAX_ERROR', LLM.prompt)
        self.assertEqual(result.snapshot['artifact']['revision'], 7)
        self.assertEqual(result.snapshot['artifact']['completed_steps'], ['打开目录'])
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['详情页操作尚未在当前 trace 中观察到。'])
        self.assertEqual(result.snapshot['artifact']['variables'][0]['name'], 'fixed_name')
        self.assertEqual(result.snapshot['repair_diagnostics']['line'], 3)

    def test_saved_complete_draft_is_not_downgraded_by_extra_final_reply(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                feedback = await self.tools[0].ainvoke({'code': COMPLETE_SCRIPT, 'completion': 'complete'})
                assert feedback['status'] == 'accepted'
                return f'```python\n{PARTIAL_SCRIPT}\n```'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.completion, 'complete')
        self.assertEqual(result.script_draft, COMPLETE_SCRIPT.strip())

    def test_saved_complete_draft_survives_later_model_error(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                feedback = await self.tools[0].ainvoke({'code': COMPLETE_SCRIPT, 'completion': 'complete'})
                assert feedback['status'] == 'accepted'
                raise ConnectionError('connection reset')

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, 'transient')
        self.assertEqual(result.completion, 'complete')
        self.assertEqual(result.script_draft, COMPLETE_SCRIPT.strip())

    def test_final_python_replaces_entry_seed_when_no_tool_save_arrived(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return f'```python\n{PARTIAL_SCRIPT}\n```'

        result, _ = self.run_with(Agent)
        self.assertEqual(result.script_draft, PARTIAL_SCRIPT.strip())
        self.assertNotIn('仅生成入口', result.script_draft)
        self.assertEqual(result.completion, 'partial')

    def test_generic_loop_stop_keeps_draft_trace_and_objective_reason(self):
        class Agent:
            def __init__(self, **kwargs): self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): self.tools = tools

            async def run(self, *_args, **_kwargs):
                await self.tools[0].ainvoke({'code': PARTIAL_SCRIPT, 'completion': 'partial'})
                for _ in range(3):
                    ScriptExplorationAgentTests.operation(
                        self.guard, 'playwright_click', {'selector': '#opaque-action'}, 'Clicked',
                    )

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, 'repeated_interaction')
        self.assertEqual(result.completion, 'partial')
        self.assertEqual(result.script_draft, PARTIAL_SCRIPT.strip())
        self.assertEqual(result.snapshot['tool_stats']['total_tool_calls'], 2)
        self.assertEqual(result.snapshot['tool_stats']['blocked_tool_calls'], 1)
        self.assertEqual([event['status'] for event in result.snapshot['events']], [
            'succeeded', 'succeeded', 'blocked',
        ])
        self.assertNotIn('登录失败', result.error_message)
        self.assertNotIn('账号', result.error_message)

    def test_original_guard_termination_reason_is_kept(self):
        class Agent:
            def __init__(self, **kwargs): self.guard = kwargs['callbacks'][0]

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start(
                    {'name': 'playwright_navigate'}, '', run_id='outside',
                    inputs={'url': 'https://outside.example/blocked', 'headless': True},
                )

        result, _ = self.run_with(Agent)
        self.assertEqual(result.error_code, 'external_domain_blocked')
        self.assertEqual(result.snapshot['termination_reason'], 'external_domain_blocked')
