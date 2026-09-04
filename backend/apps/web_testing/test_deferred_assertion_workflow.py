"""Dedicated offline coverage for deferred WebUI assertions and evaluation state."""

import asyncio
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Environment, Project
from scheduled_tasks.models import ScheduledTask, TaskExecutionLog

from .assertion_state import (
    analyze_assertion_state,
    evaluation_status,
    read_runtime_assertion_count,
)
from .models import (
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuite,
    WebUITestSuiteCase,
    WebUITestSuiteExecutionDetail,
)
from .script_contract import materialize_script
from .tasks import (
    _execute_webui_test_case_logic,
    _execute_webui_test_suite_logic,
    _finalize_scheduled_execution,
)


PENDING_SCRIPT = '''async def run(page):
    await page.goto('https://fixture.example.test/details')
    # AITS_PENDING_ASSERTION: {"assertion_id":"A1","criterion":"详情内容正确","reason":"探索未能确认详情字段"}
'''

CONFIRMED_SCRIPT = '''from playwright.async_api import expect

async def run(page):
    await page.goto('https://fixture.example.test/details')
    await expect(page.locator('#details')).to_be_visible()
'''


class _OfflineTask:
    request = SimpleNamespace(id='deferred-assertion-offline')

    def update_state(self, **_kwargs):
        return None


class _FakeExpectation:
    async def to_be_visible(self):
        return None


class _FakePage:
    async def screenshot(self, **_kwargs):
        return None


class _FakeContext:
    async def new_page(self):
        return _FakePage()

    async def close(self):
        return None


class _FakeBrowser:
    async def new_context(self, **_kwargs):
        return _FakeContext()

    async def close(self):
        return None


class _FakeBrowserType:
    async def launch(self, **_kwargs):
        return _FakeBrowser()


class _FakePlaywright:
    chromium = _FakeBrowserType()


class _FakeAsyncPlaywright:
    async def __aenter__(self):
        return _FakePlaywright()

    async def __aexit__(self, *_args):
        return False


def _fake_playwright_modules(browser_factory=_FakeBrowser):
    package = types.ModuleType('playwright')
    async_api = types.ModuleType('playwright.async_api')
    class _ConfiguredBrowserType:
        async def launch(self, **_kwargs):
            return browser_factory()

    class _ConfiguredPlaywright:
        chromium = _ConfiguredBrowserType()

    class _ConfiguredAsyncPlaywright:
        async def __aenter__(self):
            return _ConfiguredPlaywright()

        async def __aexit__(self, *_args):
            return False

    async_api.expect = lambda _value: _FakeExpectation()
    async_api.async_playwright = lambda: _ConfiguredAsyncPlaywright()
    package.async_api = async_api
    return {'playwright': package, 'playwright.async_api': async_api}


class DeferredAssertionStateTests(TestCase):
    def test_only_tokenized_pending_comments_and_real_runtime_nodes_count(self):
        script = '''from playwright.async_api import expect

async def helper(page):
    await expect(page.locator('#helper')).to_be_visible()

async def run(page):
    marker = '# AITS_PENDING_ASSERTION: {"assertion_id":"not-a-comment"}'
    assert True
    assert 1 == 1
    assert True and False
    expect(page.locator('#not-awaited')).to_be_visible()
    await expect(page.locator('#real')).to_be_visible()
    # AITS_PENDING_ASSERTION: {"assertion_id":"A1","criterion":"真实目标","reason":"探索不足"}
'''
        state = analyze_assertion_state(script)
        self.assertEqual(state['confirmed_count'], 1)
        self.assertEqual(state['pending_count'], 1)
        self.assertEqual(state['pending'][0]['assertion_id'], 'A1')
        self.assertEqual(state['status'], 'incomplete')

    def test_invalid_indentation_and_literal_assertions_are_safe_and_incomplete(self):
        invalid = 'async def run(page):\n    if True:\n  # AITS_PENDING_ASSERTION: {"assertion_id":"A1"}\n'
        self.assertEqual(analyze_assertion_state(invalid)['status'], 'incomplete')

        constants_only = '''async def run(page):
    assert True
    assert 1 == 1
    assert (True and False) or (3 < 4)
'''
        state = analyze_assertion_state(constants_only)
        self.assertEqual(state['confirmed_count'], 0)
        self.assertEqual(state['status'], 'incomplete')

    def test_runtime_expect_wrapper_counts_only_an_awaited_successful_matcher(self):
        source = '''from playwright.async_api import expect

async def run(page):
    await expect(page).to_be_visible()
'''
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, _fake_playwright_modules(), clear=False,
        ):
            count_path = str(Path(directory) / 'runtime_assertions.json')
            namespace = {'__name__': 'runtime_expect_fixture'}
            exec(materialize_script(
                source,
                'test_runtime_expect',
                headed=False,
                runtime_assertion_count_path=count_path,
            ), namespace)
            namespace['test_runtime_expect']()
            self.assertEqual(read_runtime_assertion_count(count_path), 1)

    def test_static_expect_in_unexecuted_branch_needs_runtime_proof(self):
        source = '''from playwright.async_api import expect

async def run(page):
    if False:
        await expect(page).to_be_visible()
'''
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, _fake_playwright_modules(), clear=False,
        ):
            count_path = str(Path(directory) / 'runtime_assertions.json')
            namespace = {'__name__': 'runtime_branch_fixture'}
            exec(materialize_script(
                source,
                'test_runtime_branch',
                headed=False,
                runtime_assertion_count_path=count_path,
            ), namespace)
            namespace['test_runtime_branch']()
            self.assertEqual(read_runtime_assertion_count(count_path), 0)
        status, _state, runtime_count = evaluation_status(
            source, operation_success=True, runtime_assertion_count=0,
        )
        self.assertEqual(runtime_count, 0)
        self.assertEqual(status, 'incomplete')

    def test_runtime_ignores_pure_constant_assertions(self):
        source = '''async def run(page):
    assert True
    assert 1 == 1
'''
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, _fake_playwright_modules(), clear=False,
        ):
            count_path = str(Path(directory) / 'runtime_assertions.json')
            namespace = {'__name__': 'runtime_literal_assert_fixture'}
            exec(materialize_script(
                source,
                'test_runtime_literal_assert',
                headed=False,
                runtime_assertion_count_path=count_path,
            ), namespace)
            namespace['test_runtime_literal_assert']()
            self.assertEqual(read_runtime_assertion_count(count_path), 0)

    def test_close_failures_do_not_replace_the_original_run_failure(self):
        class FailingContext(_FakeContext):
            async def close(self):
                raise RuntimeError('context close failure')

        class FailingBrowser(_FakeBrowser):
            async def new_context(self, **_kwargs):
                return FailingContext()

            async def close(self):
                raise RuntimeError('browser close failure')

        source = '''async def run(page):
    raise RuntimeError('original run failure')
'''
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, _fake_playwright_modules(FailingBrowser), clear=False,
        ):
            count_path = str(Path(directory) / 'runtime_assertions.json')
            namespace = {'__name__': 'runtime_close_failure_fixture'}
            exec(materialize_script(
                source,
                'test_runtime_close_failure',
                headed=False,
                runtime_assertion_count_path=count_path,
            ), namespace)
            with self.assertRaisesRegex(RuntimeError, 'original run failure'):
                namespace['test_runtime_close_failure']()
            self.assertEqual(read_runtime_assertion_count(count_path), 0)

    def test_non_mapping_runtime_sidecar_is_safe_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            count_path = Path(directory) / 'runtime_assertions.json'
            count_path.write_text('[]', encoding='utf-8')
            self.assertEqual(read_runtime_assertion_count(count_path), 0)


class DeferredAssertionExecutionWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='deferred-workflow', password='test-only-password',
        )
        self.project = Project.objects.create(
            name='Deferred workflow', project_type='web', owner=self.user, created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project,
            name='Offline fixture',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://fixture.example.test'},
            is_active=True,
        )

    def make_case(self, title, script):
        return WebUITestCase.objects.create(
            title=title,
            description='Dedicated deferred assertion fixture',
            test_script_content=script,
            script_status='ready',
            user=self.user,
            project=self.project,
        )

    def make_case_execution(self, test_case):
        execution = WebUITestExecution.objects.create(
            exec_type='case', name=test_case.title, executor=self.user,
            project=self.project, status='pending',
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=execution, test_case=test_case, status='pending',
        )
        return execution

    @staticmethod
    def runner_result(*, operation_success, runtime_assertion_count):
        return {
            'success': operation_success,
            'operation_success': operation_success,
            'runtime_assertion_count': runtime_assertion_count,
            'result': {'stdout': '', 'stderr': '', 'test_file': '', 'allure_report': ''},
            'error': '' if operation_success else 'fixture browser timeout',
        }

    def test_case_operation_success_with_pending_marker_is_incomplete(self):
        test_case = self.make_case('Pending case', PENDING_SCRIPT)
        execution = self.make_case_execution(test_case)
        with patch('web_testing.tasks._run_test_script', return_value=self.runner_result(
            operation_success=True, runtime_assertion_count=1,
        )), patch('web_testing.tasks._failure_screenshot_paths', return_value=('/tmp/unused.png', None)):
            result = _execute_webui_test_case_logic(
                _OfflineTask(), execution.id, {},
            )
        execution.refresh_from_db()
        test_case.refresh_from_db()
        self.assertTrue(result['operation_success'])
        self.assertEqual(result['evaluation_status'], 'incomplete')
        self.assertEqual(execution.status, 'incomplete')
        self.assertEqual(test_case.last_execute_status, 'incomplete')

    def test_runtime_operation_failure_remains_failed_even_when_pending(self):
        test_case = self.make_case('Failed pending case', PENDING_SCRIPT)
        execution = self.make_case_execution(test_case)
        with patch('web_testing.tasks._run_test_script', return_value=self.runner_result(
            operation_success=False, runtime_assertion_count=0,
        )), patch('web_testing.tasks._failure_screenshot_paths', return_value=('/tmp/unused.png', None)):
            result = _execute_webui_test_case_logic(
                _OfflineTask(), execution.id, {},
            )
        execution.refresh_from_db()
        self.assertFalse(result['operation_success'])
        self.assertEqual(result['evaluation_status'], 'failed')
        self.assertEqual(execution.status, 'failed')

    def test_suite_keeps_operation_success_but_persists_incomplete_counts(self):
        pending_case = self.make_case('Pending suite case', PENDING_SCRIPT)
        confirmed_case = self.make_case('Confirmed suite case', CONFIRMED_SCRIPT)
        suite = WebUITestSuite.objects.create(
            name='Deferred suite', description='', user=self.user, project=self.project,
        )
        WebUITestSuiteCase.objects.create(suite=suite, test_case=pending_case, order=1)
        WebUITestSuiteCase.objects.create(suite=suite, test_case=confirmed_case, order=2)
        execution = WebUITestExecution.objects.create(
            exec_type='suite', name=suite.name, executor=self.user,
            project=self.project, status='pending',
        )
        detail = WebUITestSuiteExecutionDetail.objects.create(
            execution=execution, test_suite=suite, total_cases=2,
        )
        results = [
            self.runner_result(operation_success=True, runtime_assertion_count=1),
            self.runner_result(operation_success=True, runtime_assertion_count=1),
        ]
        with patch('web_testing.tasks._run_test_script', side_effect=results), patch(
            'web_testing.tasks._failure_screenshot_paths', return_value=('/tmp/unused.png', None),
        ):
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})
        execution.refresh_from_db()
        detail.refresh_from_db()
        self.assertTrue(result['operation_success'])
        self.assertEqual(result['evaluation_status'], 'incomplete')
        self.assertEqual(execution.status, 'incomplete')
        self.assertEqual(detail.passed_cases, 1)
        self.assertEqual(detail.incomplete_cases, 1)
        self.assertEqual(detail.failed_cases, 0)

    def test_scheduled_log_never_reports_pending_assertion_as_success(self):
        scheduled = ScheduledTask.objects.create(
            name='Deferred scheduled fixture', suite_type='web', suite_ids=[1, 2],
            cron_expression='0 9 * * *', environment=self.environment,
            user=self.user, project=self.project,
        )
        execution_log = TaskExecutionLog.objects.create(
            task=scheduled, start_time=scheduled.created_at, status='running', total_cases=3,
        )
        with patch('notifications.services.trigger_notification') as notify:
            _finalize_scheduled_execution(
                execution_log.id,
                total_cases=3,
                passed_cases=1,
                failed_cases=0,
                incomplete_cases=1,
                skipped_cases=0,
                log='fixture incomplete assertion',
            )
        execution_log.refresh_from_db()
        self.assertEqual(execution_log.status, 'failed')
        self.assertEqual(execution_log.passed_cases, 1)
        self.assertEqual(execution_log.failed_cases, 0)
        self.assertIn('验证未完成', execution_log.error_message)
        notify.assert_not_called()

        # A later genuine failure must still notify, even if an earlier suite
        # left verification incomplete. Pending assertions do not mute errors.
        with patch('notifications.services.trigger_notification') as notify_failure:
            _finalize_scheduled_execution(
                execution_log.id, total_cases=3, passed_cases=0,
                failed_cases=1, incomplete_cases=0, skipped_cases=0,
                log='fixture actual execution failure',
            )
        execution_log.refresh_from_db()
        self.assertEqual(execution_log.status, 'failed')
        self.assertEqual(execution_log.failed_cases, 1)
        notify_failure.assert_called_once()
