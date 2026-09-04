"""Offline acceptance: recorded actions -> draft -> human assertion -> verification."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Environment, Project

from .exploration_trace import (
    ExplorationTraceRecorder, FinalizedAction, FinalizedPendingAssertion,
)
from .generation_contracts import ScenarioPlan
from .generation_orchestrator import _compile_persisted
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

    def compile_pending_draft(self):
        plan = ScenarioPlan.model_validate({
            'title': '查看详细信息', 'objective': '进入详情并验证显示结果',
            'instructions': ['进入入口页面', '打开详情', '验证详情内容'],
            'success_criteria': ['详情内容符合预期'],
            'assertion_requirements': [{
                'assertion_id': 'A1', 'criterion_index': 0, 'kind': 'deferred',
            }],
        })
        recorder = ExplorationTraceRecorder('/items')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        for run_id, tool, inputs, output in (
            ('nav', 'playwright_navigate', {'url': 'https://fixture.example.test/items'},
             'URL: https://fixture.example.test/items'),
            ('open', 'playwright_click', {'selector': '#open-details'}, 'clicked'),
        ):
            recorder.on_tool_start({'name': tool}, '', run_id=run_id, inputs=inputs)
            recorder.on_tool_end(output, run_id=run_id)
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='打开详情')],
            assertions=[], cleanup_actions=[],
            pending_assertions=[FinalizedPendingAssertion(
                assertion_id='A1', after_event_id='E000002',
                reason='观察结果不足以确定业务字段的正确预期',
            )],
        )
        generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user, environment=self.environment,
            status=WebUIScriptGeneration.Status.GENERATING,
            current_stage=WebUIScriptGeneration.Stage.GENERATING,
            scenario_spec=plan.model_dump(mode='json'),
        )
        with patch('web_testing.generation_orchestrator.publish_stage_changed'), patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ):
            result = _compile_persisted(generation, plan, recorder.build(tool_stats={}))
        generation.refresh_from_db()
        self.assertEqual(result['status'], 'ready_with_warnings', result)
        self.assertFalse(generation.quality_report.get('blockers'))
        self.assertIn('AITS_PENDING_ASSERTION:', generation.script_draft)
        self.assertIn('goto(', generation.script_draft)
        self.assertIn('click()', generation.script_draft)
        return generation

    def test_generated_pending_draft_can_be_saved_but_not_verified(self):
        generation = self.compile_pending_draft()
        response = self.request(WebUIScriptGenerationSaveView, generation, {
            'mode': 'draft', 'expected_revision': 0,
        })
        self.assertEqual(response.status_code, 200, response.data)
        case = WebUITestCase.objects.get(pk=response.data['data']['test_case_id'])
        self.assertIn('AITS_PENDING_ASSERTION:', case.test_script_content)

        _, digest = prepare_debug(generation.pk, expected_revision=0, execution_id=11)
        finish_debug(
            generation.pk, execution_id=11, locked_revision=0,
            locked_hash=digest, status='passed', diagnostics=[], runtime_assertion_count=1,
        )
        generation.refresh_from_db()
        self.assertEqual(generation.workspace['verification']['status'], 'incomplete')
        rejected = self.request(WebUIScriptGenerationSaveView, generation, {
            'mode': 'verified', 'expected_revision': 0,
        })
        self.assertEqual(rejected.status_code, 409, rejected.data)

    def test_human_assertion_replaces_pending_without_reexploring(self):
        generation = self.compile_pending_draft()
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
            'script_draft': edited, 'expected_revision': 0, 'variables': [],
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
