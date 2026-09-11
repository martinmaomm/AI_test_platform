"""Incremental draft tools use the same quality gate and durable checkpoint."""

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase

from .script_exploration_agent import ScriptExplorationAgent, ScriptExplorationAgentError
from .test_script_exploration_agent import PARTIAL_SCRIPT
from .exploration_trace import ExplorationTraceRecorder


class IncrementalRuntimeTests(SimpleTestCase):
    def make_agent(self):
        agent = ScriptExplorationAgent(Mock(), {}, '', lambda: False, 5)
        agent._target_url = 'https://example.test/catalog'
        agent._consider_candidate(
            PARTIAL_SCRIPT, completed_steps=['打开目录'], remaining_steps=['确认详情页'],
            variables=[{'name': 'locale', 'value': 'zh'}], completion='partial', source='save_tool',
        )
        agent._persist_checkpoint = AsyncMock(return_value=True)
        return agent

    def test_patch_preserves_metadata_and_pending_and_persists(self):
        agent = self.make_agent()
        prior = copy.deepcopy(agent._artifact)
        result = asyncio.run(agent._patch_tool().ainvoke({
            'expected_revision': prior['revision'],
            'edits': [{'old': 'await page.goto', 'new': '# 打开目录\n    await page.goto'}],
        }))
        self.assertEqual(result['status'], 'accepted')
        for key in ('remaining_steps', 'completed_steps', 'variables', 'completion'):
            self.assertEqual(agent._artifact[key], prior[key])
        self.assertIn('# PENDING_STEP:', agent._last_valid_script)
        self.assertEqual(agent._artifact['revision'], prior['revision'] + 1)
        agent._persist_checkpoint.assert_awaited_once()
        self.assertEqual(agent._efficiency_stats['patch_accepted'], 1)

    def test_bad_or_stale_patch_never_replaces_draft(self):
        for edits, revision in (
            ([{'old': 'async def run', 'new': 'broken python'}], 1),
            ([{'old': 'await page.goto', 'new': 'await page.goto'}], 0),
        ):
            with self.subTest(edits=edits, revision=revision):
                agent = self.make_agent()
                old = agent._last_valid_script
                result = asyncio.run(agent._patch_tool().ainvoke({'expected_revision': revision, 'edits': edits}))
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(agent._last_valid_script, old)
                agent._persist_checkpoint.assert_not_awaited()

    def test_failed_checkpoint_is_not_reported_as_saved(self):
        agent = self.make_agent()
        agent._persist_checkpoint.return_value = False
        agent._checkpoint_failure = '数据库不可用'
        result = asyncio.run(agent._patch_tool().ainvoke({
            'expected_revision': agent._artifact['revision'],
            'edits': [{'old': 'await page.goto', 'new': '# 入口\n    await page.goto'}],
        }))
        self.assertEqual(result['status'], 'unsaved')
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED')
        self.assertEqual(agent._efficiency_stats['patch_accepted'], 0)

    def test_failed_checkpoint_result_keeps_last_durable_code_and_candidate_diagnostics(self):
        attempts = []
        def persist(payload):
            attempts.append(payload)
            return len(attempts) == 1
        agent = ScriptExplorationAgent(Mock(), {}, '', lambda: False, 5, checkpoint_callback=persist)
        agent._target_url = 'https://example.test/catalog'
        async def run():
            await agent._save_tool().ainvoke({'code': PARTIAL_SCRIPT, 'remaining_steps': ['待确认']})
            durable = agent._last_valid_script
            revision = agent._artifact['revision']
            response = await agent._patch_tool().ainvoke({
                'expected_revision': revision,
                'edits': [{'old': 'await page.goto', 'new': '# 未持久化修改\n    await page.goto'}],
            })
            return durable, revision, response, agent._result('CHECKPOINT_FAILED', agent._checkpoint_failure)
        durable, revision, response, result = asyncio.run(run())
        self.assertEqual(response['status'], 'unsaved')
        self.assertEqual(result.script_draft, durable)
        self.assertEqual(result.snapshot['artifact']['revision'], revision)
        self.assertIn('未持久化修改', result.snapshot['draft_state']['latest_candidate'])

    def test_read_returns_authoritative_revision_and_exact_lines(self):
        agent = self.make_agent()
        result = asyncio.run(agent._read_tool().ainvoke({'start_line': 2, 'line_count': 1}))
        self.assertEqual(result['code'], agent._last_valid_script.splitlines(keepends=True)[1])
        self.assertEqual(result['revision'], agent._artifact['revision'])
        agent._persist_checkpoint.assert_not_awaited()

    def test_prompt_does_not_repeat_nested_drafts_or_prior_attempts(self):
        agent = self.make_agent()
        agent._saved_trace_data = {'events': [], 'draft_state': {'code': 'x' * 100000}, 'prior_attempts': [{'raw': 'y' * 100000}]}
        original = copy.deepcopy(agent._saved_trace_data)
        prompt = agent._prompt()
        self.assertLess(len(prompt), 5000)
        self.assertEqual(json.loads(prompt)['existing_script_draft'], agent._last_valid_script)
        self.assertEqual(agent._saved_trace_data, original)

    def test_plain_evidence_no_longer_runs_v4_finalization(self):
        recorder = ExplorationTraceRecorder('/')
        recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', inputs={}, run_id='read')
        recorder.on_tool_end('目录', run_id='read')
        with patch.object(recorder, '_finalized_assertion_evidence', side_effect=AssertionError('v4 should not run')):
            data = recorder.evidence_snapshot()
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(set(data), {'events', 'page_states', 'locator_evidence'})

    def test_fast_model_final_does_not_hide_a_terminal_browser_guard(self):
        agent = self.make_agent()
        agent._guard = SimpleNamespace(terminal_error=ScriptExplorationAgentError('tool_budget', '预算已耗尽'))
        async def run():
            task = asyncio.create_task(asyncio.sleep(0, result='model claimed done'))
            await task
            return await agent._await_task(task, time.monotonic() + 5)
        with self.assertRaises(ScriptExplorationAgentError) as raised:
            asyncio.run(run())
        self.assertEqual(raised.exception.error_code, 'tool_budget')
