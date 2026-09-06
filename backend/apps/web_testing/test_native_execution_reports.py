"""Offline contracts for platform-native WebUI execution reports."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project

from .models import (
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuite,
    WebUITestSuiteCase,
    WebUITestSuiteCaseExecution,
    WebUITestSuiteExecutionDetail,
)
from .tasks import _execute_webui_test_suite_logic
from .views import (
    TestCaseExecutionDetailView,
    TestExecutionCasesView,
    TestExecutionReportView,
    TestSuiteExecutionDetailView,
)


COMPLETE_SCRIPT = '''from playwright.async_api import expect

async def run(page):
    await page.goto('https://web.example.test/items')
    await expect(page.locator('#result')).to_be_visible()
'''
PENDING_SCRIPT = COMPLETE_SCRIPT + '\n    # AITS_PENDING_ASSERTION: {"reason":"待补充"}\n'


class _OfflineTask:
    request = SimpleNamespace(id='native-report-offline')

    def update_state(self, **_kwargs):
        return None


class NativeExecutionReportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='native-report-owner', password='test-only')
        self.project = Project.objects.create(
            name='Native report project', project_type='web', owner=self.user, created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def create_suite_execution(self, snapshots):
        execution = WebUITestExecution.objects.create(
            exec_type='suite', name='Snapshot suite', description='Saved suite description',
            executor=self.user, project=self.project, status='pending',
        )
        detail = WebUITestSuiteExecutionDetail.objects.create(
            execution=execution, total_cases=len(snapshots), suite_variables=[],
        )
        rows = [
            WebUITestSuiteCaseExecution(
                suite_execution=detail,
                test_case=item.get('test_case'),
                name=item['name'], description=item.get('description', 'saved description'),
                module_name=item.get('module_name', 'saved module'), execution_order=index,
                script_content=item.get('script', COMPLETE_SCRIPT), variables=item.get('variables', []),
                status='pending',
            )
            for index, item in enumerate(snapshots, start=1)
        ]
        WebUITestSuiteCaseExecution.objects.bulk_create(rows)
        return execution, detail

    @staticmethod
    def runner_result(*, success=True, assertions=1):
        return {
            'success': success,
            'operation_success': success,
            'runtime_assertion_count': assertions,
            'error': '' if success else 'fixture runner failure',
            'result': {'stdout': 'fixture stdout', 'stderr': '', 'test_file': ''},
        }

    def request(self, view, execution_id):
        request = self.factory.get('/execution-report/')
        force_authenticate(request, user=self.user)
        return view.as_view()(request, project_id=self.project.id, pk=execution_id)

    def test_snapshot_report_survives_deleted_suite_and_case(self):
        source_case = WebUITestCase.objects.create(
            title='Deleted source', description='Source description', user=self.user,
            project=self.project, test_script_content=COMPLETE_SCRIPT, script_status='ready',
        )
        suite = WebUITestSuite.objects.create(
            name='Deleted suite', description='Source suite', user=self.user, project=self.project,
        )
        execution, detail = self.create_suite_execution([
            {'test_case': source_case, 'name': source_case.title, 'description': source_case.description},
        ])
        source_case.delete()
        suite.delete()
        with patch('web_testing.tasks._run_test_script', return_value=self.runner_result()):
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        self.assertTrue(result['success'])
        detail.refresh_from_db()
        self.assertIsNone(detail.test_suite_id)
        row = detail.case_executions.get()
        self.assertIsNone(row.test_case_id)
        self.assertEqual(row.name, 'Deleted source')
        self.assertEqual(row.description, 'Source description')

        report = self.request(TestExecutionReportView, execution.id)
        self.assertEqual(report.status_code, 200)
        data = report.data['data']
        self.assertEqual(data['id'], execution.id)
        self.assertEqual(data['execution'], execution.id)
        self.assertEqual(data['exec_type'], 'suite')
        self.assertEqual(data['name'], 'Snapshot suite')
        self.assertEqual(data['report_url'], f'/reports/web/{self.project.id}/{execution.id}')

        suite_detail = self.request(TestSuiteExecutionDetailView, execution.id)
        self.assertEqual(suite_detail.status_code, 200)
        self.assertEqual(suite_detail.data['data']['exec_type'], 'suite')

        cases = self.request(TestExecutionCasesView, execution.id)
        self.assertEqual(cases.status_code, 200)
        self.assertEqual(cases.data['data']['cases'][0]['test_case_title'], 'Deleted source')
        self.assertEqual(cases.data['data']['suite_summary']['passed_cases'], 1)

    def test_incomplete_and_skipped_snapshots_never_become_passed(self):
        execution, detail = self.create_suite_execution([
            {'name': 'Pending verification', 'script': PENDING_SCRIPT},
            {'name': 'No script', 'script': ''},
        ])
        with patch('web_testing.tasks._run_test_script', return_value=self.runner_result(assertions=1)):
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        execution.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual(execution.status, 'incomplete')
        self.assertTrue(result['operation_success'])
        self.assertEqual(detail.passed_cases, 0)
        self.assertEqual(detail.incomplete_cases, 1)
        self.assertEqual(detail.skipped_cases, 1)

    def test_missing_snapshot_fails_without_rebuilding_from_current_suite(self):
        source_case = WebUITestCase.objects.create(
            title='Source must not be reread', description='Source', user=self.user,
            project=self.project, test_script_content=COMPLETE_SCRIPT, script_status='ready',
        )
        suite = WebUITestSuite.objects.create(
            name='Incomplete dispatch', user=self.user, project=self.project,
        )
        WebUITestSuiteCase.objects.create(suite=suite, test_case=source_case, order=1)
        execution = WebUITestExecution.objects.create(
            exec_type='suite', name=suite.name, executor=self.user,
            project=self.project, status='pending',
        )
        WebUITestSuiteExecutionDetail.objects.create(execution=execution, test_suite=suite, total_cases=1)

        with patch('web_testing.tasks._run_test_script') as runner:
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        execution.refresh_from_db()
        self.assertFalse(result['success'])
        self.assertEqual(execution.status, 'failed')
        self.assertIn('执行快照中没有测试用例', execution.error_message)
        runner.assert_not_called()

    def test_suite_start_time_is_worker_start_not_queue_time(self):
        execution, detail = self.create_suite_execution([{'name': 'Worker timing'}])
        queued_at = timezone.now() - timedelta(minutes=5)
        WebUITestExecution.objects.filter(pk=execution.id).update(start_time=queued_at)
        worker_started = timezone.now()
        with patch('web_testing.tasks.timezone.now', return_value=worker_started), patch(
            'web_testing.tasks._run_test_script', return_value=self.runner_result(),
        ):
            _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        execution.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual(execution.start_time, worker_started)
        self.assertEqual(detail.start_time, worker_started)

    def test_mid_suite_exception_persists_actual_counts_without_marking_pending_passed(self):
        execution, detail = self.create_suite_execution([
            {'name': 'First pass'}, {'name': 'Second crashes'}, {'name': 'Never starts'},
        ])
        progress_calls = 0

        def interrupt_before_second_case(*_args, **_kwargs):
            nonlocal progress_calls
            progress_calls += 1
            if progress_calls == 2:
                raise RuntimeError('worker coordination broke')

        with patch(
            'web_testing.tasks._run_test_script', return_value=self.runner_result(),
        ), patch('web_testing.tasks.update_task_progress', side_effect=interrupt_before_second_case):
            result = _execute_webui_test_suite_logic(_OfflineTask(), execution.id, self.user.id, {})

        execution.refresh_from_db()
        detail.refresh_from_db()
        rows = list(detail.case_executions.order_by('execution_order'))
        self.assertFalse(result['success'])
        self.assertEqual(execution.status, 'failed')
        self.assertEqual([row.status for row in rows], ['passed', 'pending', 'pending'])
        self.assertEqual(detail.passed_cases, 1)
        self.assertEqual(detail.failed_cases, 0)
        self.assertEqual(detail.total_cases, 3)
        cases = self.request(TestExecutionCasesView, execution.id)
        self.assertEqual(cases.data['data']['suite_summary']['not_executed_cases'], 2)
        self.assertEqual(cases.data['data']['cases'][1]['status_display'], '未执行（套件已结束）')

    def test_case_report_is_accessible_after_source_case_is_deleted(self):
        source_case = WebUITestCase.objects.create(
            title='Single source', description='Single snapshot', user=self.user,
            project=self.project, test_script_content=COMPLETE_SCRIPT, script_status='ready',
        )
        execution = WebUITestExecution.objects.create(
            exec_type='case', name=source_case.title, description=source_case.description,
            executor=self.user, project=self.project, status='passed',
        )
        WebUITestCaseExecutionDetail.objects.create(execution=execution, test_case=source_case, status='passed')
        source_case.delete()

        report = self.request(TestExecutionReportView, execution.id)
        self.assertEqual(report.status_code, 200)
        data = report.data['data']
        self.assertEqual(data['id'], execution.id)
        self.assertEqual(data['execution'], execution.id)
        self.assertEqual(data['exec_type'], 'case')
        self.assertEqual(data['name'], 'Single source')

        case_detail = self.request(TestCaseExecutionDetailView, execution.id)
        self.assertEqual(case_detail.status_code, 200)
        self.assertEqual(case_detail.data['data']['exec_type'], 'case')
