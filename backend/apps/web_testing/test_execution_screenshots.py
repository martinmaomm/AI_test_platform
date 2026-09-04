"""Offline persistence and access regressions for execution-ending screenshots."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .generation_workspace import attach_debug_task, prepare_debug
from .models import (
    WebUIScriptGeneration,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuite,
    WebUITestSuiteCase,
    WebUITestSuiteCaseExecution,
    WebUITestSuiteExecutionDetail,
)
from .tasks import (
    _execute_webui_test_case_logic,
    _execute_webui_test_suite_logic,
    debug_webui_script_generation_task,
)
from .views import TestExecutionScreenshotView


VALID_SCRIPT = '''from playwright.async_api import expect

async def run(page):
    await page.goto('https://web.example.test/items')
    await expect(page.locator('#result')).to_be_visible()
'''


class _OfflineTask:
    request = SimpleNamespace(id='execution-screenshot-offline')

    def update_state(self, **_kwargs):
        return None


class ScreenshotMediaTestCase(TestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory(prefix='aits-screenshot-tests-')
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)


class ExecutionScreenshotPersistenceTests(ScreenshotMediaTestCase):
    """Task paths must persist the runner's controlled ending screenshot for every outcome."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username='execution-screenshot-owner', email='execution-screenshot-owner@example.test',
            password='test-only-password',
        )
        self.project = Project.objects.create(
            name='Execution screenshots', project_type='web', owner=self.user, created_by=self.user,
        )

    def make_case(self, title='Screenshot case'):
        return WebUITestCase.objects.create(
            title=title, description='Offline screenshot fixture', user=self.user,
            project=self.project, test_script_content=VALID_SCRIPT, script_status='ready',
        )

    def make_case_execution(self, test_case=None, *, name='Screenshot execution'):
        execution = WebUITestExecution.objects.create(
            exec_type='case', name=name, executor=self.user, project=self.project, status='pending',
        )
        detail = WebUITestCaseExecutionDetail.objects.create(
            execution=execution, test_case=test_case, status='pending',
        )
        return execution, detail

    @staticmethod
    def runner_result(success, screenshot_path):
        Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
        Path(screenshot_path).write_bytes(b'PNG ending screenshot')
        return {
            'success': success,
            'operation_success': success,
            'runtime_assertion_count': 1 if success else 0,
            'error': '' if success else 'fixture browser failure',
            'result': {
                'stdout': 'fixture stdout', 'stderr': '', 'test_file': '', 'allure_report': '',
                'screenshot_path': screenshot_path,
            },
        }

    def runner_with_outcomes(self, outcomes):
        iterator = iter(outcomes)

        def run(*_args, **kwargs):
            return self.runner_result(next(iterator), kwargs['failure_screenshot_path'])

        return run

    def assert_persisted_screenshot(self, execution, detail):
        detail.refresh_from_db()
        expected_prefix = f'webui_failure_screenshots/execution_{execution.id}/'
        self.assertTrue(detail.screenshot_path.startswith(expected_prefix))
        self.assertTrue(os.path.isfile(os.path.join(str(settings.MEDIA_ROOT), detail.screenshot_path)))

    def test_case_success_and_failure_persist_ending_screenshot(self):
        for success in (True, False):
            with self.subTest(success=success):
                test_case = self.make_case(f'Case {success}')
                execution, detail = self.make_case_execution(test_case)
                with patch(
                    'web_testing.tasks._run_test_script',
                    side_effect=self.runner_with_outcomes([success]),
                ):
                    result = _execute_webui_test_case_logic(_OfflineTask(), execution.id, {})

                execution.refresh_from_db()
                self.assertEqual(execution.status, 'passed' if success else 'failed')
                self.assertEqual(result['operation_success'], success)
                self.assert_persisted_screenshot(execution, detail)

    def test_debug_success_and_failure_persist_ending_screenshot(self):
        for success in (True, False):
            with self.subTest(success=success):
                generation = WebUIScriptGeneration.objects.create(
                    project=self.project, user=self.user,
                    status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
                    target_url='https://web.example.test/items', description_safe='Debug screenshot',
                    script_draft=VALID_SCRIPT, scenario_spec={}, exploration_snapshot={},
                    workspace={'revision': 0, 'variables': []},
                )
                execution, detail = self.make_case_execution(name=f'Debug {success}')
                _, digest = prepare_debug(
                    generation.id, expected_revision=0, execution_id=execution.id,
                )
                attach_debug_task(
                    generation.id, execution_id=execution.id, locked_revision=0,
                    locked_hash=digest, task_id=f'debug-screenshot-{execution.id}',
                )
                with patch(
                    'web_testing.tasks._run_test_script',
                    side_effect=self.runner_with_outcomes([success]),
                ):
                    result = debug_webui_script_generation_task.apply(
                        args=(str(generation.id), execution.id, 0, digest),
                        task_id=f'debug-screenshot-{execution.id}',
                    ).get()

                execution.refresh_from_db()
                self.assertEqual(execution.status, 'passed' if success else 'failed')
                self.assertEqual(result['operation_success'], success)
                self.assert_persisted_screenshot(execution, detail)

    def test_suite_passed_and_failed_cases_each_persist_ending_screenshot(self):
        passed_case = self.make_case('Suite passed')
        failed_case = self.make_case('Suite failed')
        suite = WebUITestSuite.objects.create(
            name='Screenshot suite', description='', user=self.user, project=self.project,
        )
        WebUITestSuiteCase.objects.create(suite=suite, test_case=passed_case, order=1)
        WebUITestSuiteCase.objects.create(suite=suite, test_case=failed_case, order=2)
        execution = WebUITestExecution.objects.create(
            exec_type='suite', name=suite.name, executor=self.user, project=self.project, status='pending',
        )
        WebUITestSuiteExecutionDetail.objects.create(
            execution=execution, test_suite=suite, total_cases=2,
        )

        with patch(
            'web_testing.tasks._run_test_script',
            side_effect=self.runner_with_outcomes([True, False]),
        ):
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        execution.refresh_from_db()
        self.assertFalse(result['operation_success'])
        self.assertEqual(execution.status, 'failed')
        rows = list(WebUITestSuiteCaseExecution.objects.filter(suite_execution__execution=execution).order_by('id'))
        self.assertEqual([row.status for row in rows], ['passed', 'failed'])
        self.assertEqual(len({row.screenshot_path for row in rows}), 2)
        for row in rows:
            expected_prefix = f'webui_failure_screenshots/execution_{execution.id}/'
            self.assertTrue(row.screenshot_path.startswith(expected_prefix))
            self.assertTrue(os.path.isfile(os.path.join(str(settings.MEDIA_ROOT), row.screenshot_path)))


class ExecutionScreenshotDownloadAccessTests(ScreenshotMediaTestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(
            username='screenshot-download-owner', email='screenshot-download-owner@example.test',
            password='test-only-password',
        )
        self.viewer = get_user_model().objects.create_user(
            username='screenshot-download-viewer', email='screenshot-download-viewer@example.test',
            password='test-only-password',
        )
        self.project = Project.objects.create(
            name='Screenshot download', project_type='web', owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.viewer, role='viewer', can_view_reports=False,
        )
        self.execution = WebUITestExecution.objects.create(
            exec_type='case', name='Screenshot artifact', executor=self.owner,
            project=self.project, status='passed',
        )
        self.detail = WebUITestCaseExecutionDetail.objects.create(
            execution=self.execution, test_case=None, status='passed',
        )
        relative_path = f'webui_failure_screenshots/execution_{self.execution.id}/single_case.png'
        screenshot_file = Path(settings.MEDIA_ROOT) / relative_path
        screenshot_file.parent.mkdir(parents=True, exist_ok=True)
        screenshot_file.write_bytes(b'PNG artifact')
        self.detail.screenshot_path = relative_path
        self.detail.save(update_fields=['screenshot_path'])
        self.factory = APIRequestFactory()

    def request(self, user):
        request = self.factory.get('/execution-screenshot/')
        force_authenticate(request, user=user)
        return TestExecutionScreenshotView.as_view()(request, project_id=self.project.id, pk=self.execution.id)

    def test_existing_screenshot_endpoint_returns_png_only_to_report_authorized_user(self):
        owner_response = self.request(self.owner)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response['Content-Type'], 'image/png')
        self.assertEqual(b''.join(owner_response.streaming_content), b'PNG artifact')
        owner_response.close()

        denied_response = self.request(self.viewer)
        self.assertEqual(denied_response.status_code, 403)
