"""Offline integration checks for model text, repair gates, and verification handoff."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project
from web_testing.generation_workspace import prepare_repair, attach_repair_task, workspace_for_generation
from web_testing.models import WebUIScriptGeneration
from web_testing.tasks import repair_webui_script_generation_task

SCRIPT = '''# 场景：检查订单结果
from playwright.async_api import expect
async def run(page, variables):
    await page.goto('https://example.test/orders')
    await page.wait_for_load_state('domcontentloaded', timeout=1)
    # 验证：订单状态正确
    await expect(page.locator('#result')).to_have_text('paid')
'''


class WorkflowAcceptance(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='offline-review')
        self.project = Project.objects.create(name='Offline review', project_type='web', owner=user, created_by=user)
        self.user = user

    def run_repair(self, original, candidate, remaining=None):
        generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user,
            status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
            target_url='https://example.test/orders', description_safe='检查订单结果',
            script_draft=original, model_info={'config_id': 1},
            scenario_spec={'schema_version': 5, 'objective': '检查订单结果'},
            exploration_snapshot={'schema_version': 5, 'artifact': {
                'completion': 'partial' if remaining else 'complete', 'remaining_steps': remaining or [],
            }},
            workspace={'revision': 0, 'verification': {
                'status': 'failed', 'diagnostics': [{'code': 'RUNTIME_FAILURE', 'message': 'AttributeError: page operation unavailable'}],
            }},
        )
        _, digest = prepare_repair(generation.pk, expected_revision=0)
        attach_repair_task(generation.pk, locked_revision=0, locked_hash=digest, task_id='acceptance-task')
        llm = AsyncMock()
        llm.ainvoke.return_value = '```python\n' + candidate + '\n```'
        with patch('ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=llm)), patch(
            'web_testing.tasks._run_test_script', return_value={
                'success': True, 'operation_success': True, 'runtime_assertion_count': 1,
                'result': {'stdout': '', 'stderr': '', 'test_file': '', 'screenshot_path': None},
            },
        ) as runner, patch('web_testing.script_exploration_agent.MCPClient.from_dict') as mcp:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.pk), 0, digest), task_id='acceptance-task',
            ).get()
        mcp.assert_not_called()
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, original)
        return result, workspace_for_generation(generation)['repair'], runner.call_count

    def test_old_remaining_and_long_script_can_proceed_to_verification(self):
        original = ('# 业务流程注释用于完整输出验收\n' * 1400) + SCRIPT
        self.assertGreater(len(original), 20000)
        candidate = original.replace('timeout=1)', 'timeout=30000)')
        result, repair, runs = self.run_repair(original, candidate, remaining=['补充订单状态断言'])
        self.assertEqual(repair['status'], 'candidate_passed', (result, repair))
        self.assertEqual(repair['candidate_script'].strip(), candidate.strip())
        self.assertEqual(runs, 1)

    def test_same_count_weakened_assertion_is_not_executed(self):
        candidate = SCRIPT.replace("to_have_text('paid')", 'to_be_visible()')
        result, repair, runs = self.run_repair(SCRIPT, candidate)
        self.assertEqual(runs, 0, (result, repair))
        self.assertEqual(repair['status'], 'candidate_ready', (result, repair))
        self.assertTrue(repair['candidate_quality_report']['blockers'])
        for attempt in repair['attempts']:
            self.assertEqual(attempt['execution_status'], 'not_run')
            self.assertIsNone(attempt['execution_id'])
        self.assertIn('ASSERTION_REGRESSION', [item['code'] for item in repair['attempts'][0]['blockers']])
