from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from api_testing.models import (
    APIEndpoint,
    APISpecification,
    APITestCase,
    APITestExecution,
    APITestSuite,
)
from api_testing.tasks import _execute_api_test_suite
from projects.models import Environment, Project

from .models import ScheduledTask, TaskExecutionLog
from .scheduling import (
    _notify_finished_run,
    dispatch_scheduled_suite,
    finish_scheduled_suite,
    reserve_scheduled_run,
)


class ScheduledRunReliabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='scheduled-reliability', password='test-password',
        )
        self.project = Project.objects.create(
            name='Scheduled reliability', project_type='api',
            owner=self.user, created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='API environment',
            category=Environment.EnvironmentCategory.API,
            config={'base_url': 'https://api.example.test'}, is_active=True,
        )
        specification = APISpecification.objects.create(
            project=self.project, spec_name='Reliability spec', created_by=self.user,
        )
        endpoint = APIEndpoint.objects.create(
            spec=specification, path='/health', method='GET', summary='health',
        )
        self.case = APITestCase.objects.create(
            project=self.project, endpoint=endpoint, title='Health case',
            created_by=self.user, script_content='{"config":{"name":"health"},"teststeps":[{"name":"health","request":{"method":"GET","url":"/health"}}]}',
        )
        self.first_suite = self.make_suite('First')
        self.second_suite = self.make_suite('Second')
        with patch('scheduled_tasks.signals.register_periodic_task'):
            self.task = ScheduledTask.objects.create(
                name='Reliability schedule', suite_type='api',
                suite_ids=[self.second_suite.id, self.first_suite.id],
                cron_expression='0 9 * * *', environment=self.environment,
                user=self.user, project=self.project,
            )

    def make_suite(self, name):
        suite = APITestSuite.objects.create(name=name, user=self.user, project=self.project)
        suite.test_cases.add(self.case)
        return suite

    def reserve(self):
        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch') as enqueue, self.captureOnCommitCallbacks(execute=True):
            reservation = reserve_scheduled_run(self.task.id, manual=True)
        self.assertTrue(reservation.started, reservation.error)
        log = TaskExecutionLog.objects.get(pk=reservation.execution_log_id)
        return log, enqueue

    def mark_terminal(self, link, status):
        execution = APITestExecution.objects.get(pk=link['execution_id'])
        execution.status = status
        execution.save(update_fields=['status'])
        execution.suite_execution_detail.case_executions.update(status='failed' if status == 'failed' else 'passed')

    def test_prebuilds_ordered_snapshots_and_only_queues_the_first_suite(self):
        log, enqueue = self.reserve()

        self.assertEqual([link['name'] for link in log.linked_executions], ['Second', 'First'])
        self.assertEqual([link['serial_dispatch_state'] for link in log.linked_executions], ['queued', 'ready'])
        self.assertEqual(APITestExecution.objects.filter(trigger_type='schedule').count(), 2)
        enqueue.assert_called_once_with(log.id, log.linked_executions[0]['execution_id'])

    def test_scheduled_api_report_uses_frozen_suite_order_after_suite_changes(self):
        """Scheduled snapshots must create their report rows in frozen order."""
        from api_testing.serializers import APITestSuiteExecutionDetailSerializer

        later_case = APITestCase.objects.create(
            project=self.project, endpoint=self.case.endpoint, title='Later case',
            created_by=self.user,
            script_content='{"config":{"name":"later"},"teststeps":[]}',
        )
        suite = APITestSuite.objects.create(
            name='Ordered report', user=self.user, project=self.project,
            # ``APITestCase`` defaults to newest first, so this deliberately
            # reverses the source query order used by the old scheduler.
            test_case_order=[self.case.pk, later_case.pk],
        )
        suite.test_cases.add(self.case, later_case)
        ScheduledTask.objects.filter(pk=self.task.pk).update(suite_ids=[suite.pk])

        log, _ = self.reserve()
        execution = APITestExecution.objects.get(pk=log.linked_executions[0]['execution_id'])
        suite.test_case_order = [later_case.pk, self.case.pk]
        suite.test_cases.remove(later_case)
        suite.save(update_fields=['test_case_order', 'updated_at'])

        report = APITestSuiteExecutionDetailSerializer(execution.suite_execution_detail).data
        self.assertEqual(
            [case['test_case_title'] for case in report['case_executions']],
            ['Health case', 'Later case'],
        )

    def test_failed_first_suite_is_reported_then_advances_to_second(self):
        log, _ = self.reserve()
        first, second = log.linked_executions
        self.mark_terminal(first, 'failed')

        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch') as enqueue, self.captureOnCommitCallbacks(execute=True):
            finish_scheduled_suite(log.id, first['execution_id'], append_log='first failed')

        log.refresh_from_db()
        self.assertEqual(log.status, 'running')
        self.assertEqual(log.failed_cases, 1)
        self.assertEqual(log.linked_executions[1]['serial_dispatch_state'], 'queued')
        enqueue.assert_called_once_with(log.id, second['execution_id'])

    def test_dispatch_failure_is_terminal_and_does_not_block_the_next_suite(self):
        log, _ = self.reserve()
        first, second = log.linked_executions

        with patch('scheduled_tasks.scheduling._dispatch_prepared_suite', side_effect=RuntimeError('broker unavailable')), patch(
            'scheduled_tasks.scheduling._enqueue_suite_dispatch',
        ) as enqueue, self.captureOnCommitCallbacks(execute=True):
            result = dispatch_scheduled_suite.run(log.id, first['execution_id'])

        self.assertFalse(result['success'])
        log.refresh_from_db()
        first_execution = APITestExecution.objects.get(pk=first['execution_id'])
        self.assertEqual(first_execution.status, 'error')
        self.assertEqual(log.status, 'running')
        self.assertEqual(log.linked_executions[1]['serial_dispatch_state'], 'queued')
        enqueue.assert_called_once_with(log.id, second['execution_id'])

    def test_auto_run_after_pause_is_ignored_and_manual_run_is_allowed(self):
        ScheduledTask.objects.filter(pk=self.task.id).update(status='paused')
        ignored = reserve_scheduled_run(self.task.id, manual=False)
        self.assertFalse(ignored.started)
        self.assertIn('暂停', ignored.error)
        self.assertFalse(TaskExecutionLog.objects.filter(task=self.task).exists())

        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch'), self.captureOnCommitCallbacks(execute=True):
            manual = reserve_scheduled_run(self.task.id, manual=True)
        self.assertTrue(manual.started)

    def test_existing_running_log_prevents_reentry(self):
        log, _ = self.reserve()

        second = reserve_scheduled_run(self.task.id, manual=True)

        self.assertFalse(second.started)
        self.assertTrue(second.already_running)
        self.assertEqual(second.execution_log_id, log.id)
        self.assertEqual(TaskExecutionLog.objects.filter(task=self.task).count(), 1)

    def test_invalid_or_empty_suite_configuration_records_a_terminal_failure(self):
        ScheduledTask.objects.filter(pk=self.task.id).update(suite_ids=[])

        with patch('scheduled_tasks.scheduling._notify_finished_run'), self.captureOnCommitCallbacks(execute=True):
            reservation = reserve_scheduled_run(self.task.id, manual=True)

        self.assertFalse(reservation.started)
        log = TaskExecutionLog.objects.get(pk=reservation.execution_log_id)
        self.assertEqual(log.status, 'failed')
        self.assertIsNotNone(log.end_time)
        self.assertEqual(log.linked_executions, [])

    def test_duplicate_terminal_callback_is_idempotent(self):
        log, _ = self.reserve()
        first, second = log.linked_executions
        self.mark_terminal(first, 'passed')

        with patch('scheduled_tasks.scheduling._enqueue_suite_dispatch') as enqueue, self.captureOnCommitCallbacks(execute=True):
            finish_scheduled_suite(log.id, first['execution_id'], append_log='once')
            finish_scheduled_suite(log.id, first['execution_id'], append_log='twice')

        log.refresh_from_db()
        self.assertEqual(log.linked_executions[0]['serial_dispatch_state'], 'finished')
        self.assertEqual(log.linked_executions[1]['serial_dispatch_state'], 'queued')
        self.assertEqual(log.step_log, 'once')
        enqueue.assert_called_once_with(log.id, second['execution_id'])

    def test_fast_completion_cannot_be_overwritten_by_late_dispatch_write(self):
        log, _ = self.reserve()
        first, second = log.linked_executions

        def complete_before_dispatch_write(*_args, **_kwargs):
            self.mark_terminal(first, 'passed')
            finish_scheduled_suite(log.id, first['execution_id'], append_log='fast worker')
            return 'suite-task-id'

        with patch('scheduled_tasks.scheduling._dispatch_prepared_suite', side_effect=complete_before_dispatch_write), patch(
            'scheduled_tasks.scheduling._enqueue_suite_dispatch',
        ) as enqueue, self.captureOnCommitCallbacks(execute=True):
            result = dispatch_scheduled_suite.run(log.id, first['execution_id'])

        self.assertTrue(result['success'])
        log.refresh_from_db()
        self.assertEqual(log.linked_executions[0]['serial_dispatch_state'], 'finished')
        self.assertEqual(log.linked_executions[1]['serial_dispatch_state'], 'queued')
        enqueue.assert_called_once_with(log.id, second['execution_id'])

    def test_api_environment_id_change_uses_snapshot_and_advances(self):
        log, _ = self.reserve()
        first, second = log.linked_executions
        task_instance = SimpleNamespace(update_state=lambda **kwargs: None)

        with patch('api_testing.execution_service.requests_runner', return_value={'success': True, 'status': 'passed'}), patch('scheduled_tasks.scheduling._enqueue_suite_dispatch') as enqueue, self.captureOnCommitCallbacks(execute=True):
            result = _execute_api_test_suite(
                task_instance,
                first['execution_id'],
                first['suite_id'],
                999999,
                log.id,
            )

        self.assertEqual(result['execution_status'], 'passed')
        log.refresh_from_db()
        self.assertEqual(APITestExecution.objects.get(pk=first['execution_id']).status, 'passed')
        self.assertEqual(log.linked_executions[0]['serial_dispatch_state'], 'finished')
        self.assertEqual(log.linked_executions[1]['serial_dispatch_state'], 'queued')
        enqueue.assert_called_once_with(log.id, second['execution_id'])

    def test_paused_beat_entry_returns_a_structured_error(self):
        from .tasks import run_scheduled_task
        ScheduledTask.objects.filter(pk=self.task.id).update(status='paused')
        result = run_scheduled_task.run(self.task.id)
        self.assertFalse(result['success'])
        self.assertIn('暂停', result['error'])
        self.assertFalse(TaskExecutionLog.objects.exists())

    def test_partial_snapshot_failure_rolls_back_all_child_executions(self):
        from .scheduling import _create_api_snapshot
        calls = []

        def fail_second(task, suite):
            calls.append(suite.pk)
            if len(calls) == 2:
                raise RuntimeError('fixture snapshot failed')
            return _create_api_snapshot(task, suite)

        with patch('scheduled_tasks.scheduling._create_api_snapshot', side_effect=fail_second), patch(
            'scheduled_tasks.scheduling._notify_finished_run',
        ), self.captureOnCommitCallbacks(execute=True):
            result = reserve_scheduled_run(self.task.id, manual=True)
        self.assertFalse(result.started)
        log = TaskExecutionLog.objects.get(pk=result.execution_log_id)
        self.assertEqual(log.status, 'failed')
        self.assertEqual(log.linked_executions, [])
        self.assertFalse(APITestExecution.objects.exists())

    def test_final_suite_notifies_once_and_closes_parent(self):
        log, _ = self.reserve()
        with patch('notifications.services.trigger_notification', return_value=True) as notify, patch(
            'scheduled_tasks.scheduling._enqueue_suite_dispatch',
        ), self.captureOnCommitCallbacks(execute=True):
            for link in log.linked_executions:
                self.mark_terminal(link, 'passed')
                finish_scheduled_suite(log.id, link['execution_id'])
                finish_scheduled_suite(log.id, link['execution_id'])
        log.refresh_from_db()
        self.assertEqual(log.status, 'success')
        self.assertIsNotNone(log.notification_sent_at)
        self.assertEqual(log.passed_cases, 2)
        self.assertIsNotNone(log.end_time)
        notify.assert_called_once()

    def test_notification_marker_requires_explicit_true_delivery_result(self):
        log, _ = self.reserve()
        TaskExecutionLog.objects.filter(pk=log.pk).update(status='failed')

        with patch('notifications.services.trigger_notification', return_value=1):
            _notify_finished_run(log.id)
        log.refresh_from_db()
        self.assertIsNone(log.notification_sent_at)

        with patch('notifications.services.trigger_notification', return_value=True):
            _notify_finished_run(log.id)
        log.refresh_from_db()
        self.assertIsNotNone(log.notification_sent_at)
