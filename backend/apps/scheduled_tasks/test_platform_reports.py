from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from api_testing.models import (
    APIEndpoint,
    APISpecification,
    APITestCase,
    APITestExecution,
    APITestSuite,
    APITestSuiteCaseExecution,
    APITestSuiteExecutionDetail,
)
from api_testing.serializers import APITestSuiteExecutionDetailSerializer
from api_testing.tasks import _execute_api_test_suite as _execute_api_suite_logic
from projects.models import Environment, Project, ProjectMember

from .models import ScheduledTask, TaskExecutionLog
from .reporting import (
    append_linked_execution,
    mark_notification_sent,
    refresh_execution_log_from_links,
    summarize_linked_executions,
)
from .scheduling import reserve_scheduled_run


class PlatformNativeReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='native-report-owner', email='native-report-owner@example.test', password='test-password')
        self.reviewer = User.objects.create_user(username='native-report-viewer', email='native-report-viewer@example.test', password='test-password')
        self.denied = User.objects.create_user(username='native-report-denied', email='native-report-denied@example.test', password='test-password')
        self.project = Project.objects.create(
            name='Native reports', project_type='api', owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.reviewer, can_view_reports=True,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='Native API',
            category=Environment.EnvironmentCategory.API,
            config={'base_url': 'https://api.example.test'}, is_active=True,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, spec_name='Native spec', created_by=self.owner,
        )
        self.endpoint = APIEndpoint.objects.create(
            spec=self.spec, path='/health', method='GET', summary='health',
        )
        self.case = APITestCase.objects.create(
            project=self.project, endpoint=self.endpoint, title='Native case',
            created_by=self.owner, script_content='{"config":{"name":"native"},"teststeps":[{"name":"health","request":{"method":"GET","url":"/health"}}]}',
        )
        self.scheduled_suite = APITestSuite.objects.create(
            name='Scheduled suite', user=self.owner, project=self.project,
        )
        self.scheduled_suite.test_cases.add(self.case)
        with patch('scheduled_tasks.signals.register_periodic_task'):
            self.task = ScheduledTask.objects.create(
                name='Native report schedule', suite_type='api', suite_ids=[self.scheduled_suite.id],
                cron_expression='0 9 * * *', environment=self.environment,
                user=self.owner, project=self.project,
            )

    def make_log(self):
        return TaskExecutionLog.objects.create(task=self.task, start_time=self.task.created_at)

    def make_suite_execution(self, *, status='passed', total_cases=2, child_statuses=()):
        suite = APITestSuite.objects.create(name='Native suite', user=self.owner, project=self.project)
        suite.test_cases.add(self.case)
        execution = APITestExecution.objects.create(
            exec_type='suite', name=suite.name, status=status, trigger_type='schedule',
            executor=self.owner, environment=self.environment, project=self.project,
        )
        detail = APITestSuiteExecutionDetail.objects.create(
            execution=execution, test_suite=suite, test_suite_name=suite.name,
            total_cases=total_cases,
        )
        for child_status in child_statuses:
            APITestSuiteCaseExecution.objects.create(
                suite_execution=detail, test_case=self.case, name=self.case.title,
                status=child_status,
            )
        return suite, execution, detail

    def link(self, log, execution, name='Native suite'):
        append_linked_execution(
            log, kind='api', project_id=self.project.id,
            execution_id=execution.id, name=name,
        )

    def test_partial_child_rows_are_incomplete_not_passed(self):
        log = self.make_log()
        _, execution, _ = self.make_suite_execution(total_cases=4, child_statuses=('passed', 'passed'))
        self.link(log, execution)

        summary = summarize_linked_executions(log)

        self.assertEqual(summary['total_cases'], 4)
        self.assertEqual(summary['passed_cases'], 2)
        self.assertEqual(summary['incomplete_cases'], 2)
        self.assertEqual(summary['report_status'], 'incomplete')
        self.assertFalse(summary['is_running'])

    def test_parent_failure_after_passing_children_is_execution_error(self):
        log = self.make_log()
        _, execution, _ = self.make_suite_execution(status='failed', child_statuses=('passed', 'passed'))
        self.link(log, execution)

        summary = summarize_linked_executions(log)

        self.assertEqual(summary['passed_cases'], 2)
        self.assertEqual(summary['execution_errors'], 1)
        self.assertEqual(summary['report_status'], 'error')

    def test_parent_terminal_with_pending_child_finishes_as_incomplete(self):
        log = self.make_log()
        _, execution, _ = self.make_suite_execution(status='passed', total_cases=2, child_statuses=('pending',))
        self.link(log, execution)

        summary = refresh_execution_log_from_links(log)
        log.refresh_from_db()

        self.assertFalse(summary['is_running'])
        self.assertEqual(summary['incomplete_cases'], 2)
        self.assertIsNotNone(log.end_time)
        self.assertEqual(log.status, 'failed')

    def test_missing_execution_is_execution_error_not_extra_case(self):
        log = self.make_log()
        append_linked_execution(
            log, kind='api', project_id=self.project.id,
            execution_id=999999, name='missing',
        )

        summary = summarize_linked_executions(log)

        self.assertEqual(summary['total_cases'], 0)
        self.assertEqual(summary['execution_errors'], 1)
        self.assertEqual(summary['report_status'], 'error')

    def test_history_survives_source_suite_and_case_deletion(self):
        _, execution, detail = self.make_suite_execution(child_statuses=('passed',))
        execution_id = execution.id
        detail.test_suite.delete()
        self.case.delete()

        detail.refresh_from_db()
        child = detail.case_executions.get()
        self.assertEqual(detail.execution_id, execution_id)
        self.assertEqual(detail.test_suite_name, 'Native suite')
        self.assertIsNone(detail.test_suite)
        self.assertIsNone(child.test_case)
        self.assertEqual(child.name, 'Native case')
        serialized = APITestSuiteExecutionDetailSerializer(detail).data
        self.assertEqual(serialized['test_suite_name'], 'Native suite')
        self.assertEqual(serialized['case_executions'][0]['test_case_title'], 'Native case')

    def test_serial_reservation_links_all_suites_before_first_dispatch(self):
        first = APITestSuite.objects.create(name='First', user=self.owner, project=self.project)
        second = APITestSuite.objects.create(name='Second', user=self.owner, project=self.project)
        first.test_cases.add(self.case)
        second.test_cases.add(self.case)
        self.task.suite_ids = [second.id, first.id]
        self.task.save(update_fields=['suite_ids'])
        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch') as enqueue, self.captureOnCommitCallbacks(execute=True):
            reservation = reserve_scheduled_run(self.task.id, manual=True)

        self.assertTrue(reservation.started)
        log = TaskExecutionLog.objects.get(pk=reservation.execution_log_id)
        self.assertEqual([item['name'] for item in log.linked_executions], ['Second', 'First'])
        self.assertEqual([item['serial_dispatch_state'] for item in log.linked_executions], ['queued', 'ready'])
        enqueue.assert_called_once_with(log.id, log.linked_executions[0]['execution_id'])

    def test_async_dispatch_persists_platform_report_url(self):
        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch'), self.captureOnCommitCallbacks(execute=True):
            reservation = reserve_scheduled_run(self.task.id, manual=True)

        self.assertTrue(reservation.started)
        execution_log = TaskExecutionLog.objects.get(pk=reservation.execution_log_id)
        self.assertEqual(execution_log.report_url, f'/reports/detail/{execution_log.id}')

    def test_api_suite_runner_executes_each_snapshot_case_once(self):
        second_case = APITestCase.objects.create(
            project=self.project, endpoint=self.endpoint, title='Second native case',
            created_by=self.owner, script_content=self.case.script_content,
        )
        suite = APITestSuite.objects.create(name='One-run suite', user=self.owner, project=self.project)
        suite.test_cases.add(self.case, second_case)
        execution = APITestExecution.objects.create(
            exec_type='suite', name=suite.name, status='pending', trigger_type='schedule',
            executor=self.owner, environment=self.environment, project=self.project,
        )
        detail = APITestSuiteExecutionDetail.objects.create(
            execution=execution, test_suite=suite, test_suite_name=suite.name, total_cases=2,
        )
        for test_case in (self.case, second_case):
            APITestSuiteCaseExecution.objects.create(
                suite_execution=detail, test_case=test_case, name=test_case.title, status='pending',
            )

        task_instance = SimpleNamespace(update_state=lambda **kwargs: None)
        runner_result = {'success': True, 'log': 'runner completed'}
        from api_testing.execution_snapshots import capture_suite_snapshot
        capture_suite_snapshot(execution, suite, self.environment)
        with patch('api_testing.execution_service.requests_runner', return_value=runner_result) as runner:
            result = _execute_api_suite_logic(
                task_instance, execution.id, suite.id, self.environment.id,
            )

        self.assertEqual(result['execution_status'], 'passed')
        self.assertEqual(runner.call_count, 2)
        self.assertCountEqual(
            [call.kwargs['script_id'] for call in runner.call_args_list],
            [str(self.case.id), str(second_case.id)],
        )
        self.assertEqual(
            list(detail.case_executions.order_by('id').values_list('status', flat=True)),
            ['passed', 'passed'],
        )

    def test_public_reports_do_not_open_management_case_endpoints(self):
        log = self.make_log()
        _, execution, _ = self.make_suite_execution(child_statuses=('passed', 'passed'))
        self.link(log, execution)
        refresh_execution_log_from_links(log)
        client = APIClient()

        client.force_authenticate(self.reviewer)
        report = client.get(f'/api/v1/reports/detail/{log.id}/')
        api_report = client.get(f'/api/v1/projects/{self.project.id}/api-testing/executions/{execution.id}/report/')
        cases = client.get(f'/api/v1/projects/{self.project.id}/api-testing/executions/{execution.id}/cases/')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(api_report.status_code, 200)
        self.assertEqual(cases.status_code, 200)
        report_data = api_report.json()['data']
        self.assertEqual(report_data['id'], execution.id)
        self.assertEqual(report_data['execution'], execution.id)
        self.assertEqual(report_data['project_id'], self.project.id)
        self.assertEqual(report_data['exec_type'], 'suite')
        self.assertIn('detail_id', report_data)
        self.assertEqual(len(report_data['case_executions']), 2)
        self.assertIn('httprunner_result', report_data['case_executions'][0])

        client.force_authenticate(self.denied)
        self.assertEqual(client.get(f'/api/v1/reports/detail/{log.id}/').status_code, 200)
        self.assertEqual(
            client.get(f'/api/v1/projects/{self.project.id}/api-testing/executions/{execution.id}/report/').status_code,
            200,
        )
        self.assertEqual(
            client.get(f'/api/v1/projects/{self.project.id}/api-testing/executions/{execution.id}/cases/').status_code,
            404,
        )

    def test_notification_marker_is_persistent_and_idempotent(self):
        log = self.make_log()
        mark_notification_sent(log)
        log.refresh_from_db()
        first_sent_at = log.notification_sent_at
        mark_notification_sent(log)
        log.refresh_from_db()
        self.assertEqual(log.notification_sent_at, first_sent_at)

    def test_terminal_transition_keeps_one_notification_marker(self):
        log = self.make_log()
        _, pending_execution, pending_detail = self.make_suite_execution(
            status='pending', total_cases=1, child_statuses=('pending',),
        )
        _, completed_execution, _ = self.make_suite_execution(
            status='passed', total_cases=1, child_statuses=('passed',),
        )
        self.link(log, pending_execution, 'pending suite')
        self.link(log, completed_execution, 'completed suite')

        first_summary = refresh_execution_log_from_links(log)
        log.refresh_from_db()
        self.assertTrue(first_summary['is_running'])
        self.assertIsNone(log.end_time)
        self.assertIsNone(log.notification_sent_at)

        pending_execution.status = 'passed'
        pending_execution.save(update_fields=['status'])
        pending_detail.case_executions.update(status='passed')
        final_summary = refresh_execution_log_from_links(log)
        self.assertFalse(final_summary['is_running'])
        mark_notification_sent(log)
        log.refresh_from_db()
        sent_at = log.notification_sent_at
        self.assertIsNotNone(sent_at)
        refresh_execution_log_from_links(log)
        mark_notification_sent(log)
        log.refresh_from_db()
        self.assertEqual(log.notification_sent_at, sent_at)
