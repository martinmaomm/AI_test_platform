"""Offline acceptance: v5 agent draft -> human assertion -> verification."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Environment, Project

from .generation_orchestrator import _persist_agent_result
from .generation_workspace import finish_debug, prepare_debug
from .models import WebUIScriptGeneration, WebUITestCase
from .views import WebUIScriptGenerationDraftView, WebUIScriptGenerationSaveView


class DeferredAssertionIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='deferred-integration', password='test-only-password',
        )
        self.project = Project.objects.create(
            name='Deferred integration', project_type='web',
            owner=self.user, created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='Offline fixture',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://fixture.example.test'}, is_active=True,
        )
        self.factory = APIRequestFactory()

    def request(self, view, generation, payload, *, method='post'):
        request = getattr(self.factory, method)('/offline-fixture/', payload, format='json')
        force_authenticate(request, user=self.user)
        return view.as_view()(
            request, project_id=self.project.pk, generation_id=generation.pk,
        )

    def persist_pending_agent_draft(self):
        draft = '''"""场景：查看详细信息。目标：进入详情并验证显示结果。"""
async def run(page, variables):
    # 进入入口页面
    await page.goto('/items')
    # 打开详情
    await page.locator('#open-details').click()
    # AITS_PENDING_ASSERTION: {"reason":"观察结果不足以确定业务字段的正确预期"}
'''
        generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user, environment=self.environment,
            status=WebUIScriptGeneration.Status.EXPLORING,
            current_stage=WebUIScriptGeneration.Stage.EXPLORING,
            celery_task_id='deferred-agent-task',
            workspace={'_agent_run': {'generation_revision': 0, 'task_id': 'deferred-agent-task'}},
        )
        snapshot = {
            'schema_version': 5,
            'events': [
                {'event_id': 'E1', 'action': 'navigate'},
                {'event_id': 'E2', 'action': 'click'},
            ],
            'page_states': [], 'locator_evidence': [],
            'tool_stats': {'total_tool_calls': 2},
            'artifact': {
                'revision': 1, 'completion': 'partial',
                'completed_steps': ['进入入口页面', '打开详情'],
                'remaining_steps': ['补充详情内容断言'], 'variables': [],
            },
        }
        with patch('web_testing.generation_orchestrator.publish_terminal'):
            result = _persist_agent_result(
                generation, task_id='deferred-agent-task', script_draft=draft,
                snapshot=snapshot, completion='partial', error_code='', error_message='',
                final_message='已保存待人工补充断言的草稿。',
            )
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW, result)
        self.assertFalse(generation.quality_report.get('blockers'))
        self.assertIn('AITS_PENDING_ASSERTION:', generation.script_draft)
        self.assertIn('goto(', generation.script_draft)
        self.assertIn('click()', generation.script_draft)
        self.assertEqual(generation.exploration_snapshot['schema_version'], 5)
        return generation

    def test_generated_pending_draft_can_be_saved_but_not_verified(self):
        generation = self.persist_pending_agent_draft()
        revision = generation.workspace['revision']
        response = self.request(WebUIScriptGenerationSaveView, generation, {
            'mode': 'draft', 'expected_revision': revision,
        })
        self.assertEqual(response.status_code, 200, response.data)
        case = WebUITestCase.objects.get(pk=response.data['data']['test_case_id'])
        self.assertIn('AITS_PENDING_ASSERTION:', case.test_script_content)

        _, digest = prepare_debug(generation.pk, expected_revision=revision, execution_id=11)
        finish_debug(
            generation.pk, execution_id=11, locked_revision=revision,
            locked_hash=digest, status='passed', diagnostics=[], runtime_assertion_count=1,
        )
        generation.refresh_from_db()
        self.assertEqual(generation.workspace['verification']['status'], 'incomplete')
        rejected = self.request(WebUIScriptGenerationSaveView, generation, {
            'mode': 'verified', 'expected_revision': revision,
        })
        self.assertEqual(rejected.status_code, 409, rejected.data)

    def test_human_assertion_replaces_pending_without_reexploring(self):
        generation = self.persist_pending_agent_draft()
        revision = generation.workspace['revision']
        lines = []
        for line in generation.script_draft.splitlines():
            if line.lstrip().startswith('# AITS_PENDING_ASSERTION:'):
                indent = line[:len(line) - len(line.lstrip())]
                line = indent + "await expect(page.locator('#details')).to_be_visible()"
            lines.append(line)
        edited = '\n'.join(lines)
        if 'from playwright.async_api import expect' not in edited:
            edited = 'from playwright.async_api import expect\n' + edited
        response = self.request(WebUIScriptGenerationDraftView, generation, {
            'script_draft': edited, 'expected_revision': revision, 'variables': [],
        }, method='patch')
        self.assertEqual(response.status_code, 200, response.data)
        generation.refresh_from_db()
        self.assertFalse(generation.quality_report.get('blockers'))
        self.assertNotIn('AITS_PENDING_ASSERTION:', generation.script_draft)
        revision = generation.workspace['revision']
        _, digest = prepare_debug(generation.pk, expected_revision=revision, execution_id=12)
        finish_debug(
            generation.pk, execution_id=12, locked_revision=revision,
            locked_hash=digest, status='passed', diagnostics=[], runtime_assertion_count=1,
        )
        saved = self.request(WebUIScriptGenerationSaveView, generation, {
            'mode': 'verified', 'expected_revision': revision,
        })
        self.assertEqual(saved.status_code, 200, saved.data)
        case = WebUITestCase.objects.get(pk=saved.data['data']['test_case_id'])
        self.assertEqual(case.generation_metadata['verification']['status'], 'passed')
