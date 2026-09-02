"""Offline contracts for the editable generated-script workspace."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Environment, Project, ProjectMember

from .generation_workspace import (
    attach_debug_task,
    attach_repair_task,
    base_url_fingerprint,
    environment_fingerprint,
    infer_script_variables,
    finish_debug,
    mark_debug_running,
    mark_repair_running,
    prepare_repair,
    prepare_debug,
    script_hash,
    workspace_for_generation,
)
from .models import WebUIScriptGeneration, WebUITestCase, WebUITestExecution
from .models import WebUITestCaseExecutionDetail
from .tasks import debug_webui_script_generation_task
from .serializers import WebUIScriptGenerationSerializer, WebUITestCaseDetailSerializer
from .views import (
    WebUIScriptGenerationDebugView,
    WebUIScriptGenerationDraftView,
    WebUIScriptGenerationSaveView,
)


VALID_SCRIPT = '''"""Check the users page."""
async def run(page):
    await page.goto('/users')
'''


class GenerationWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='workspace-owner', email='workspace-owner@example.test', password='pw')
        self.member = get_user_model().objects.create_user(username='workspace-member', email='workspace-member@example.test', password='pw')
        self.project = Project.objects.create(name='Workspace', project_type='web', owner=self.user, created_by=self.user)
        self.environment = Environment.objects.create(
            project=self.project, name='web', category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'}, is_active=True,
        )
        ProjectMember.objects.create(project=self.project, user=self.member, role='viewer', can_edit=False)
        self.factory = APIRequestFactory()

    def generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user, 'environment': self.environment,
            'status': WebUIScriptGeneration.Status.NEEDS_REVIEW,
            'description_safe': 'Check users.', 'script_draft': VALID_SCRIPT,
            'scenario_spec': {}, 'exploration_snapshot': {'finalization': {'status': 'valid'}},
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def request(self, user, method, path, payload):
        request = getattr(self.factory, method.lower())(path, payload, format='json')
        force_authenticate(request, user=user)
        return request

    def test_edit_permission_is_required(self):
        generation = self.generation()
        response = WebUIScriptGenerationDraftView.as_view()(
            self.request(self.member, 'PATCH', '/draft/', {'script_draft': VALID_SCRIPT, 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 403)

    def test_inferred_variables_exclude_runner_settings_and_preserve_safe_defaults(self):
        script = '''import os
async def run(page):
    base = os.environ['PLAYWRIGHT_BASE_URL']
    label = os.getenv('TEST_LABEL', 'qa-user')
    token = os.getenv('TEST_TOKEN', 'do-not-copy')
'''
        variables = {item['name']: item for item in infer_script_variables(script)}
        self.assertNotIn('PLAYWRIGHT_BASE_URL', variables)
        self.assertEqual(variables['TEST_LABEL']['value'], 'qa-user')
        self.assertFalse(variables['TEST_LABEL']['required'])
        self.assertEqual(variables['TEST_TOKEN']['value'], '')
        self.assertTrue(variables['TEST_TOKEN']['is_secret'])

    def test_draft_edit_invalidates_verification_and_never_persists_secret_value(self):
        generation = self.generation(workspace={
            'revision': 0, 'variables': [],
            'verification': {'status': 'passed', 'script_hash': script_hash(VALID_SCRIPT), 'environment_id': self.environment.id, 'execution_id': 7, 'locked_revision': 0},
        })
        changed = 'async def broken(page):\n'
        response = WebUIScriptGenerationDraftView.as_view()(
            self.request(self.user, 'PATCH', '/draft/', {
                'script_draft': changed, 'expected_revision': 0,
                'variables': [{'name': 'PASSWORD', 'value': 'never-store-me', 'is_secret': True, 'required': True}],
            }), project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        generation.refresh_from_db()
        self.assertEqual(generation.workspace['revision'], 1)
        self.assertEqual(generation.workspace['verification']['status'], 'unverified')
        self.assertEqual(generation.workspace['variables'][0]['value'], '')
        self.assertNotIn('never-store-me', str(response.data))

    def test_duplicate_debug_is_conflict_and_execution_has_no_case(self):
        generation = self.generation()
        with patch('web_testing.views.debug_webui_script_generation_task.delay', return_value=SimpleNamespace(id='debug-1')):
            first = WebUIScriptGenerationDebugView.as_view()(
                self.request(self.user, 'POST', '/debug/', {'expected_revision': 0, 'confirm_execution': True, 'runtime_variables': []}),
                project_id=self.project.id, generation_id=generation.id,
            )
        self.assertEqual(first.status_code, 202, first.data)
        execution = WebUITestExecution.objects.get(pk=first.data['data']['workspace']['verification']['execution_id'])
        self.assertIsNone(execution.case_execution_detail.test_case_id)
        duplicate = WebUIScriptGenerationDebugView.as_view()(
            self.request(self.user, 'POST', '/debug/', {'expected_revision': 0, 'confirm_execution': True}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_stale_task_completion_cannot_mark_new_draft_passed(self):
        generation = self.generation(workspace={
            'revision': 0, 'variables': [],
            'verification': {'status': 'running', 'script_hash': script_hash(VALID_SCRIPT), 'environment_id': self.environment.id, 'execution_id': 91, 'locked_revision': 0},
        })
        generation.script_draft = VALID_SCRIPT + '\n# changed\n'
        generation.workspace = {'revision': 1, 'variables': [], 'verification': {'status': 'unverified'}}
        generation.save(update_fields=['script_draft', 'workspace', 'updated_at'])
        self.assertFalse(finish_debug(
            generation.id, execution_id=91, locked_revision=0, locked_hash=script_hash(VALID_SCRIPT),
            status='passed', diagnostics=[],
        ))
        generation.refresh_from_db()
        self.assertEqual(workspace_for_generation(generation)['verification']['status'], 'unverified')

    def test_debug_workspace_roundtrip_preserves_lock_and_terminal_message(self):
        generation = self.generation()
        prepared, digest = prepare_debug(generation.id, expected_revision=0, execution_id=101)
        attached = attach_debug_task(
            generation.id, execution_id=101, locked_revision=0, locked_hash=digest, task_id='debug-roundtrip',
        )
        self.assertEqual(workspace_for_generation(attached)['verification']['locked_revision'], 0)
        self.assertIsNotNone(mark_debug_running(
            generation.id, execution_id=101, locked_revision=0, locked_hash=digest,
        ))
        self.assertTrue(finish_debug(
            generation.id, execution_id=101, locked_revision=0, locked_hash=digest,
            status='failed', diagnostics=[{'code': 'RUNTIME_FAILURE', 'message': 'locator not found'}],
        ))
        generation.refresh_from_db()
        verification = workspace_for_generation(generation)['verification']
        self.assertEqual(verification['status'], 'failed')
        self.assertEqual(verification['locked_revision'], 0)
        self.assertEqual(verification['task_id'], 'debug-roundtrip')
        self.assertEqual(verification['message'], 'locator not found')

    def test_duplicate_worker_delivery_does_not_execute_draft_twice(self):
        generation = self.generation()
        execution = WebUITestExecution.objects.create(
            exec_type='case', name='debug', executor=self.user, project=self.project,
            environment=self.environment, status='pending', trigger_type='manual',
        )
        WebUITestCaseExecutionDetail.objects.create(execution=execution, test_case=None, status='pending')
        _, digest = prepare_debug(generation.id, expected_revision=0, execution_id=execution.id)
        attach_debug_task(generation.id, execution_id=execution.id, locked_revision=0, locked_hash=digest, task_id='worker-once')
        result = {'success': True, 'result': {'stdout': '', 'stderr': '', 'test_file': '', 'allure_report': '', 'screenshot_path': None}}
        with patch('web_testing.tasks._run_test_script', return_value=result) as runner:
            debug_webui_script_generation_task.apply(args=(str(generation.id), execution.id, 0, digest), task_id='worker-once')
            debug_webui_script_generation_task.apply(args=(str(generation.id), execution.id, 0, digest), task_id='worker-once')
        self.assertEqual(runner.call_count, 1)
        generation.refresh_from_db()
        self.assertEqual(workspace_for_generation(generation)['verification']['status'], 'passed')

    def test_repair_worker_transition_is_one_shot(self):
        generation = self.generation(workspace={
            'revision': 0, 'variables': [],
            'verification': {'status': 'failed', 'script_hash': script_hash(VALID_SCRIPT), 'environment_id': self.environment.id, 'execution_id': 1, 'locked_revision': 0, 'diagnostics': [{'code': 'RUNTIME_FAILURE', 'message': 'failed'}]},
        })
        _, digest = prepare_repair(generation.id, expected_revision=0)
        attach_repair_task(generation.id, locked_revision=0, locked_hash=digest, task_id='repair-once')
        self.assertIsNotNone(mark_repair_running(generation.id, locked_revision=0, locked_hash=digest, task_id='repair-once'))
        self.assertIsNone(mark_repair_running(generation.id, locked_revision=0, locked_hash=digest, task_id='repair-once'))

    def test_dispatch_failure_compensates_execution_and_workspace(self):
        generation = self.generation()
        with patch('web_testing.views.debug_webui_script_generation_task.delay', side_effect=RuntimeError('broker down')):
            response = WebUIScriptGenerationDebugView.as_view()(
                self.request(self.user, 'POST', '/debug/', {'expected_revision': 0, 'confirm_execution': True}),
                project_id=self.project.id, generation_id=generation.id,
            )
        self.assertEqual(response.status_code, 503)
        generation.refresh_from_db()
        self.assertEqual(generation.workspace['verification']['status'], 'error')
        self.assertEqual(WebUITestExecution.objects.get().status, 'error')

    def test_runtime_cache_failure_does_not_leave_an_active_debug(self):
        generation = self.generation()
        with patch('web_testing.views.store_runtime_variables', side_effect=RuntimeError('cache unavailable')), patch(
            'web_testing.views.debug_webui_script_generation_task.delay',
        ) as dispatch:
            response = WebUIScriptGenerationDebugView.as_view()(
                self.request(self.user, 'POST', '/debug/', {'expected_revision': 0, 'confirm_execution': True}),
                project_id=self.project.id, generation_id=generation.id,
            )
        self.assertEqual(response.status_code, 503)
        dispatch.assert_not_called()
        generation.refresh_from_db()
        self.assertEqual(generation.workspace['verification']['status'], 'error')
        self.assertEqual(WebUITestExecution.objects.get().status, 'error')

    def test_whitespace_cannot_erase_draft(self):
        generation = self.generation()
        response = WebUIScriptGenerationDraftView.as_view()(
            self.request(self.user, 'PATCH', '/draft/', {'script_draft': ' \n ', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 400)
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, VALID_SCRIPT)

    def test_saving_unchanged_draft_does_not_invalidate_passed_revision(self):
        generation = self.generation()
        _, digest = prepare_debug(generation.id, expected_revision=0, execution_id=4)
        finish_debug(generation.id, execution_id=4, locked_revision=0, locked_hash=digest, status='passed', diagnostics=[])
        response = WebUIScriptGenerationDraftView.as_view()(
            self.request(self.user, 'PATCH', '/draft/', {'script_draft': VALID_SCRIPT, 'expected_revision': 0, 'variables': []}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['workspace']['revision'], 0)
        self.assertEqual(response.data['data']['workspace']['verification']['status'], 'passed')

    def test_initial_save_includes_inferred_variables_in_saved_state(self):
        generation = self.generation(script_draft='import os\n' + VALID_SCRIPT + '\n    label = os.getenv("TEST_LABEL", "qa-user")\n')
        response = WebUIScriptGenerationSaveView.as_view()(
            self.request(self.user, 'POST', '/save/', {'mode': 'draft', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['data']['generation']['is_saved'])

    def test_legacy_save_cannot_bypass_active_debug(self):
        generation = self.generation(status=WebUIScriptGeneration.Status.READY)
        prepare_debug(generation.id, expected_revision=0, execution_id=4)
        response = WebUIScriptGenerationSaveView.as_view()(
            self.request(self.user, 'POST', '/save/', {}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(WebUITestCase.objects.exists())

    def test_environment_change_invalidates_badge_and_verified_save(self):
        for change in ({'base_url': 'https://changed.example.test'}, None):
            with self.subTest(change=change):
                self.environment.is_active = True
                self.environment.save()
                generation = self.generation()
                _, digest = prepare_debug(generation.id, expected_revision=0, execution_id=4)
                finish_debug(generation.id, execution_id=4, locked_revision=0, locked_hash=digest, status='passed', diagnostics=[])
                generation.refresh_from_db()
                self.assertEqual(WebUIScriptGenerationSerializer(generation).data['workspace']['verification']['status'], 'passed')
                if change is None:
                    self.environment.is_active = False
                else:
                    self.environment.config = change
                self.environment.save()
                generation.refresh_from_db()
                self.assertEqual(WebUIScriptGenerationSerializer(generation).data['workspace']['verification']['status'], 'unverified')
                response = WebUIScriptGenerationSaveView.as_view()(
                    self.request(self.user, 'POST', '/save/', {'mode': 'verified', 'expected_revision': 0}),
                    project_id=self.project.id, generation_id=generation.id,
                )
                self.assertEqual(response.status_code, 409)

    def test_case_script_or_variables_edit_invalidates_previous_execution(self):
        for changes in (
            {'test_script_content': VALID_SCRIPT + '\n# independent edit'},
            {'variables': [{'name': 'TEST_LABEL', 'value': 'changed'}]},
        ):
            with self.subTest(changes=changes):
                case = WebUITestCase.objects.create(
                    title='independent case', user=self.user, project=self.project,
                    test_script_content=VALID_SCRIPT, last_execute_status='passed',
                    generation_metadata={'verification': {'status': 'passed'}},
                )
                serializer = WebUITestCaseDetailSerializer(case, data=changes, partial=True)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                serializer.save()
                case.refresh_from_db()
                self.assertEqual(case.last_execute_status, 'untested')
                self.assertNotIn('verification', case.generation_metadata)

    def test_explicit_draft_and_verified_save_are_distinct_and_idempotent(self):
        generation = self.generation()
        first = WebUIScriptGenerationSaveView.as_view()(
            self.request(self.user, 'POST', '/save/', {'mode': 'draft', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(first.status_code, 200, first.data)
        case = WebUITestCase.objects.get(pk=first.data['data']['test_case_id'])
        self.assertEqual(case.script_version, 1)
        verified = WebUIScriptGenerationSaveView.as_view()(
            self.request(self.user, 'POST', '/save/', {'mode': 'verified', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(verified.status_code, 409)
        _, digest = prepare_debug(generation.id, expected_revision=0, execution_id=4)
        self.assertTrue(finish_debug(
            generation.id, execution_id=4, locked_revision=0, locked_hash=digest,
            status='passed', diagnostics=[],
        ))
        second = WebUIScriptGenerationSaveView.as_view()(
            self.request(self.user, 'POST', '/save/', {'mode': 'verified', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(second.status_code, 200, second.data)
        case.refresh_from_db()
        self.assertEqual(case.script_version, 1)
        self.assertEqual(case.generation_metadata['verification']['status'], 'passed')
