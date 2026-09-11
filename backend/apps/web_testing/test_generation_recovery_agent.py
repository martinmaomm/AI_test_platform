"""Recovery starts a fresh browser while retaining prior attempt evidence."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .script_exploration_agent import EXPLORATION_SCRIPT_CONSTRAINTS, ScriptExplorationAgent


class GenerationRecoveryAgentTests(SimpleTestCase):
    def agent(self):
        return ScriptExplorationAgent(Mock(), {}, str(uuid4()), lambda: False, 5)

    def test_each_attempt_keeps_an_independent_trace_file(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            agent = self.agent()
            agent._target_url = 'https://example.test/'
            agent._brief = {'allow_test_data_writes': True}
            agent._configure_trace(output_generation_id=agent.generation_id)
            first = Path(agent._trace_path)
            run_id = uuid4()
            agent._guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', inputs={}, run_id=run_id)
            agent._guard.on_tool_end('prior attempt evidence', run_id=run_id)
            previous_bytes = first.read_bytes()
            agent._configure_trace(output_generation_id=agent.generation_id)
            self.assertNotEqual(agent._trace_path, str(first))
            self.assertEqual(first.read_bytes(), previous_bytes)

    def test_recovery_keeps_old_evidence_separate_and_preserves_saved_code(self):
        snapshot = {
            'schema_version': 5,
            'events': [{'event_id': 'prior-only'}],
            'page_states': [], 'locator_evidence': [],
            'artifact': {'revision': 4, 'completion': 'partial', 'completed_steps': ['已创建'], 'remaining_steps': ['待查询确认']},
            'prior_attempts': [{'events': [{'event_id': 'even-older'}]}],
            'trace_path': '/prior-attempt.trace.jsonl',
        }
        code = "async def run(page, variables):\n    await page.goto('https://example.test/')\n"
        client = Mock()
        client.create_all_sessions = AsyncMock(side_effect=RuntimeError('fixture unavailable'))
        client.close_all_sessions = AsyncMock()
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory), patch(
            'web_testing.script_exploration_agent.prepare_playwright_mcp_output_config', side_effect=lambda config, _: config,
        ), patch('web_testing.script_exploration_agent.MCPClient.from_dict', return_value=client):
            agent = self.agent()
            result = asyncio.run(agent.generate(
                brief={'allow_test_data_writes': True, 'recovery_context': {'notes': '已创建数据，仅确认后继续未完成项'}},
                target_url='https://example.test/', saved_snapshot=snapshot, script_draft=code,
            ))
        self.assertEqual(result.script_draft, code.strip())
        self.assertEqual(result.snapshot['events'], [])
        self.assertEqual(len(result.snapshot['prior_attempts']), 2)
        self.assertEqual(result.snapshot['prior_attempts'][-1]['events'], snapshot['events'])
        self.assertEqual(result.snapshot['artifact']['completed_steps'], ['已创建'])
        self.assertEqual(result.snapshot['artifact']['remaining_steps'], ['待查询确认'])
        self.assertEqual(json.loads(agent._prompt())['brief']['recovery_context']['notes'], '已创建数据，仅确认后继续未完成项')
        self.assertIn('不得直接执行整个已保存脚本或重复新增、修改、删除', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('旧轨迹仅是历史证据', EXPLORATION_SCRIPT_CONSTRAINTS)
