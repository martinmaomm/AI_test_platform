import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, Environment
from .models import APITestCase, APITestExecution, APITestSuite
from .execution_snapshots import capture_case_snapshot, capture_suite_snapshot
from .execution_service import run_execution


class APIExecutionSnapshotTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='api-snapshot', password='local-only')
        self.project = Project.objects.create(name='API', project_type='api', created_by=self.user)
        self.environment = Environment.objects.create(
            project=self.project, category='api', name='Local fixture',
            config={'base_url': 'https://example.test', 'variables': {'token': 'environment', 'env': True}},
        )
        self.doc = {'config': {'name': 'Snapshot', 'variables': {'token': 'case'}},
                    'teststeps': [{'name': 'Health', 'request': {'method': 'GET', 'url': '/health'},
                                   'validate': [{'eq': ['status_code', 200]}]}]}
        self.case = APITestCase.objects.create(project=self.project, test_case_type='scenario',
                                              title='Case', created_by=self.user, script_content=json.dumps(self.doc))
        self.execution = APITestExecution.objects.create(project=self.project, executor=self.user,
                                                         exec_type='case', name='Case')
        self.passed = {'success': True, 'status': 'passed', 'step_datas': [
            {'name': 'Health', 'success': True, 'status': 'passed'}], 'log': '验证状态码通过'}

    def test_case_uses_snapshot_even_after_case_and_environment_delete(self):
        capture_case_snapshot(self.execution, self.case, self.environment, {'token': 'override'})
        self.case.delete()
        self.environment.delete()
        with patch('api_testing.execution_service.requests_runner', return_value=self.passed) as runner:
            result = run_execution(self.execution.pk)
        self.assertEqual(result['execution_status'], 'passed')
        self.assertEqual(runner.call_args.kwargs['options']['variables'], {'token': 'override', 'env': True})
        self.assertEqual(runner.call_args.kwargs['base_url'], 'https://example.test')
        self.execution.refresh_from_db()
        self.assertIn('验证状态码通过', self.execution.execution_log)
        self.assertEqual(self.execution.success_steps, 1)

    def test_suite_order_and_variables_frozen_and_cases_do_not_share_mutation(self):
        second = APITestCase.objects.create(project=self.project, test_case_type='scenario', title='Second',
                                            created_by=self.user, script_content=json.dumps(self.doc))
        suite = APITestSuite.objects.create(project=self.project, user=self.user, name='Suite',
                                            variables={'token': 'suite'}, test_case_order=[second.pk, self.case.pk])
        suite.test_cases.add(self.case, second)
        self.execution.exec_type = 'suite'
        self.execution.save()
        capture_suite_snapshot(self.execution, suite, self.environment)
        suite.test_cases.clear()
        suite.variables = {'token': 'changed'}
        suite.save()
        seen = []
        def run(**kwargs):
            seen.append((kwargs['script_id'], kwargs['options']['variables']['token']))
            kwargs['options']['variables']['token'] = 'extracted-from-first'
            return self.passed
        with patch('api_testing.execution_service.requests_runner', side_effect=run):
            result = run_execution(self.execution.pk, suite=True)
        self.assertEqual(seen, [(str(second.pk), 'suite'), (str(self.case.pk), 'suite')])
        self.assertEqual(result['passed_cases'], 2)

    def test_manual_suite_report_uses_frozen_case_order_after_suite_changes(self):
        """The report must not consult the mutable suite ordering."""
        from .serializers import APITestSuiteExecutionDetailSerializer

        second = APITestCase.objects.create(
            project=self.project, test_case_type='scenario', title='Second',
            created_by=self.user, script_content=json.dumps(self.doc),
        )
        suite = APITestSuite.objects.create(
            project=self.project, user=self.user, name='Suite',
            test_case_order=[second.pk, self.case.pk],
        )
        suite.test_cases.add(self.case, second)
        self.execution.exec_type = 'suite'
        self.execution.save(update_fields=['exec_type'])

        capture_suite_snapshot(self.execution, suite, self.environment)
        suite.test_case_order = [self.case.pk, second.pk]
        suite.test_cases.remove(second)
        suite.save(update_fields=['test_case_order', 'updated_at'])

        report = APITestSuiteExecutionDetailSerializer(self.execution.suite_execution_detail).data
        self.assertEqual(
            [case['test_case_title'] for case in report['case_executions']],
            ['Second', 'Case'],
        )

    def test_suite_report_preserves_orphan_detail_after_frozen_rows(self):
        """A stale snapshot must not hide a persisted report detail."""
        from .models import APITestSuiteCaseExecution
        from .serializers import APITestSuiteExecutionDetailSerializer

        second = APITestCase.objects.create(
            project=self.project, test_case_type='scenario', title='Second',
            created_by=self.user, script_content=json.dumps(self.doc),
        )
        suite = APITestSuite.objects.create(
            project=self.project, user=self.user, name='Suite',
            test_case_order=[second.pk, self.case.pk],
        )
        suite.test_cases.add(self.case, second)
        self.execution.exec_type = 'suite'
        self.execution.save(update_fields=['exec_type'])
        capture_suite_snapshot(self.execution, suite, self.environment)

        detail = self.execution.suite_execution_detail
        by_case = {row.test_case_id: row for row in detail.case_executions.all()}
        APITestSuiteCaseExecution.objects.create(
            suite_execution=detail, test_case=None, name='Orphan detail', status='error',
        )
        self.execution.input_snapshot = {
            'version': 1,
            'kind': 'suite',
            'cases': [
                {'detail_id': True},
                {'detail_id': by_case[self.case.pk].pk},
                {'detail_id': by_case[self.case.pk].pk},
                {'detail_id': 999999},
                {'detail_id': by_case[second.pk].pk},
            ],
        }
        self.execution.save(update_fields=['input_snapshot'])

        report = APITestSuiteExecutionDetailSerializer(detail).data
        self.assertEqual(
            [case['test_case_title'] for case in report['case_executions']],
            ['Case', 'Second', 'Orphan detail'],
        )

    def test_duplicate_delivery_does_not_resend_http(self):
        capture_case_snapshot(self.execution, self.case, self.environment)
        with patch('api_testing.execution_service.requests_runner', return_value=self.passed) as runner:
            run_execution(self.execution.pk)
            run_execution(self.execution.pk)
        self.assertEqual(runner.call_count, 1)

    def test_completed_checkpoint_survives_cancellation_without_overwriting_stopped(self):
        capture_case_snapshot(self.execution, self.case, self.environment)

        def run(**kwargs):
            self.assertFalse(kwargs['should_cancel']())
            partial = {**self.passed, 'success': False, 'status': 'running'}
            kwargs['on_progress'](partial)
            self.execution.refresh_from_db()
            stored = json.loads(self.execution.case_execution_detail.httprunner_result)
            self.assertEqual(stored['step_datas'][0]['status'], 'passed')
            self.assertEqual(self.execution.success_steps, 1)
            APITestExecution.objects.filter(pk=self.execution.pk).update(status='stopped')
            self.assertTrue(kwargs['should_cancel']())
            return {**partial, 'status': 'stopped', 'error_type': 'Cancelled', 'error': '用户停止执行'}

        with patch('api_testing.execution_service.requests_runner', side_effect=run):
            result = run_execution(self.execution.pk)
        self.assertEqual(result['execution_status'], 'stopped')
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.success_steps, 1)
        self.assertEqual(self.execution.failure_steps, 0)
        self.assertEqual(self.execution.case_execution_detail.status, 'skipped')

    def test_report_target_uses_frozen_options_after_environment_changes(self):
        from .serializers import APITestCaseExecutionDetailSerializer
        capture_case_snapshot(self.execution, self.case, self.environment)
        self.environment.config = {'base_url': 'https://changed.invalid'}
        self.environment.save()
        detail = self.execution.case_execution_detail
        self.assertEqual(APITestCaseExecutionDetailSerializer(detail).data['environment_base_url'], 'https://example.test')
        self.environment.delete()
        detail.refresh_from_db()
        self.assertEqual(APITestCaseExecutionDetailSerializer(detail).data['environment_base_url'], 'https://example.test')

    def test_missing_snapshot_is_error_not_live_fallback(self):
        from .models import APITestCaseExecutionDetail
        APITestCaseExecutionDetail.objects.create(execution=self.execution, test_case=self.case, name='Case')
        with patch('api_testing.execution_service.requests_runner') as runner:
            result = run_execution(self.execution.pk)
        self.assertEqual(result['execution_status'], 'error')
        runner.assert_not_called()

    def test_saved_execution_records_skipped_steps_without_counting_as_assertion_failure(self):
        capture_case_snapshot(self.execution, self.case, self.environment)
        failed = {'success': False, 'status': 'failed', 'error': '断言失败', 'step_datas': [
            {'name': 'First', 'success': False, 'status': 'failed'},
            {'name': 'Next', 'success': False, 'status': 'skipped'}]}
        with patch('api_testing.execution_service.requests_runner', return_value=failed):
            run_execution(self.execution.pk)
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.total_steps, 2)
        self.assertEqual(self.execution.failure_steps, 1)
        self.assertEqual(self.execution.error_steps, 0)

    def test_cross_project_case_id_cannot_be_executed(self):
        other = Project.objects.create(name='Other', project_type='api', created_by=self.user)
        client = APIClient()
        client.force_authenticate(self.user)
        with patch('api_testing.tasks.execute_api_test_case_async.delay') as delay:
            response = client.post(f'/api/v1/projects/{other.pk}/api-testing/test-cases/{self.case.pk}/execute/', {}, format='json')
        self.assertEqual(response.status_code, 404)
        delay.assert_not_called()

    def test_dispatch_failure_is_terminal_and_not_pending_forever(self):
        client = APIClient()
        client.force_authenticate(self.user)
        with patch('api_testing.tasks.execute_api_test_case_async.delay', side_effect=RuntimeError('broker down')):
            response = client.post(f'/api/v1/projects/{self.project.pk}/api-testing/test-cases/{self.case.pk}/execute/',
                                   {'environment_id': self.environment.pk}, format='json')
        self.assertGreaterEqual(response.status_code, 400)
        latest = APITestExecution.objects.order_by('-id').first()
        self.assertEqual(latest.status, 'error')

    def test_duplicate_case_names_do_not_share_latest_result(self):
        from .serializers import APITestCaseSerializer, APITestCaseDetailSerializer
        from .models import APITestCaseExecutionDetail
        other = APITestCase.objects.create(project=self.project, test_case_type='scenario',
                                            title=self.case.title, created_by=self.user, script_content=json.dumps(self.doc))
        APITestCaseExecutionDetail.objects.create(execution=self.execution, test_case=other, name=other.title)
        for serializer in (APITestCaseSerializer, APITestCaseDetailSerializer):
            self.assertIsNone(serializer(self.case).data['last_result_info'])
