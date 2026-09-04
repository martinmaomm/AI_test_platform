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
from .script_exploration_agent import ScriptExplorationAgent, ScriptExplorationToolGuard


PARTIAL_SCRIPT = '''\
async def run(page, variables):
    await page.goto('https://example.test/catalog')
    # AITS_PENDING_STEP: {"reason":"详情页操作尚未在当前 trace 中观察到。"}
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
    def login_form(*, wrapper_attrs: str = '') -> str:
        return f'''\
<html><body><form id="login-form" {wrapper_attrs}>
  <input name="username" /><input type="password" name="password" />
  <button type="submit">登录</button>
</form></body></html>'''

    def page_check(self, guard: ScriptExplorationToolGuard, output: str, run_id: str) -> None:
        guard.on_tool_start(
            {'name': 'playwright_get_visible_html'}, '', run_id=run_id,
            inputs={'selector': 'body'},
        )
        guard.on_tool_end(output, run_id=run_id)

    def login_attempt(self, guard: ScriptExplorationToolGuard) -> None:
        guard.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='login-submit',
            inputs={'selector': '#login-submit'},
        )
        guard.on_tool_end('clicked', run_id='login-submit')

    def test_hidden_login_form_repr_confirms_success_and_allows_later_click(self):
        guard = self.make_guard()
        self.page_check(guard, self.html_output(self.login_form()), 'visible-login')
        self.login_attempt(guard)
        # The real MCP wrapper is a string repr.  The hidden template must be
        # decoded by AST constants before style visibility is assessed.
        post_login = self.login_form(wrapper_attrs='style="display:none"') + (
            '<section id="records"><button id="next-action">继续</button></section>'
        )
        self.page_check(guard, self.html_output(post_login), 'hidden-login')
        self.assertTrue(guard.login_verified)
        self.assertEqual(guard.login_checks_since_attempt, 0)
        guard.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='next-action',
            inputs={'selector': '#next-action'},
        )
        guard.on_tool_end('clicked', run_id='next-action')
        self.assertEqual(guard.login_attempts, 1)

    def test_hidden_void_input_does_not_hide_visible_login_siblings(self):
        guard = self.make_guard()
        # ``input`` has no end tag.  Its hidden attribute must not leak to the
        # following visible username/password fields through the parser stack.
        html = '''\
<html><body><form class="login-form">
  <input type="hidden" hidden name="csrf" />
  <input name="username" /><input type="password" name="password" />
  <button>登录</button>
</form></body></html>'''
        self.page_check(guard, self.html_output(html), 'visible-after-hidden-input')
        self.assertTrue(guard.login_page_detected)
        self.assertTrue(guard.login_form_seen)

    def test_visible_login_form_after_explicit_submit_still_stops_on_second_html_check(self):
        guard = self.make_guard()
        output = self.html_output(self.login_form())
        self.page_check(guard, output, 'before-submit')
        self.login_attempt(guard)
        self.page_check(guard, output, 'still-login-once')
        with self.assertRaisesRegex(Exception, '连续两次可见 HTML'):
            self.page_check(guard, output, 'still-login-twice')
        self.assertEqual(guard.termination_reason, 'login_failed')

    def test_plain_text_or_empty_data_does_not_increment_login_failure(self):
        guard = self.make_guard()
        self.page_check(guard, self.html_output(self.login_form()), 'before-submit')
        self.login_attempt(guard)
        for run_id in ('empty-data-once', 'empty-data-twice'):
            guard.on_tool_start(
                {'name': 'playwright_get_visible_text'}, '', run_id=run_id,
                inputs={'selector': 'main'},
            )
            guard.on_tool_end('暂无数据，未找到匹配记录。', run_id=run_id)
        self.assertEqual(guard.login_checks_since_attempt, 0)
        self.assertIsNone(guard.terminal_error)

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
        self.assertEqual(Agent.tool_names, ['aits_save_script'])
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
        self.assertEqual(Agent.tools, ['aits_save_script'])
        self.assertNotIn('aits_finalize_path', Agent.instructions)
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
        self.assertIn('AITS_PENDING_STEP:', result.script_draft)
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
        self.assertIn('AITS_PENDING_STEP:', result.script_draft)
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

    def test_final_text_is_partial_fallback_instead_of_complete_claim(self):
        class Agent:
            def __init__(self, **kwargs): pass

            async def initialize(self): pass

            async def register_local_tools(self, tools): pass

            async def run(self, *_args, **_kwargs): return f'```python\n{COMPLETE_SCRIPT}\n```'

        result, _ = self.run_with(Agent)
        self.assertIn('AITS_PENDING_STEP:', result.script_draft)
        self.assertEqual(result.completion, 'partial')
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
        self.assertIn('AITS_PENDING_STEP:', result.script_draft)
        self.assertEqual(result.snapshot['events'], [{'event_id': 'saved'}])

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
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['补充断言'])
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
