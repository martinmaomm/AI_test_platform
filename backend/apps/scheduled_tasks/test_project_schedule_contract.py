"""Current-project schedule APIs, validated only in the isolated test runner."""
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from web_testing.models import WebUITestSuite
from .cron import next_run_time, parse_cron
from .models import ScheduledTask, TaskExecutionLog
from .serializers import TaskRunSerializer, TaskExecutionLogSerializer, TaskExecutionLogListSerializer
from . import test_environment_contract as environment_tests


class ScheduleCronTests(SimpleTestCase):
    def test_timezone_interval_and_sunday_alias(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(next_run_time('0 9 * * 1-5', now=now).isoformat(), '2026-09-07T09:00:00+08:00')
        self.assertEqual(next_run_time('*/30 * * * *', now=now).minute, 30)
        self.assertEqual(parse_cron('0 0 * * 7')['day_of_week'], '0')
        self.assertEqual(next_run_time('0 0 * * 7', now=now), next_run_time('0 0 * * 0', now=now))

    def test_invalid_ranges_dates_and_suffixes(self):
        for expression in ('61 * * * *', '0 24 * * *', '0 0 31 2 *', '*/0 * * * *',
                           '0 0 * 13 *', '0 0 * * 8', '0-5oops * * * *', '* * * *', '0 0 * * * *'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                next_run_time(expression)


class ProjectScheduleContractTests(TestCase):
    payload = environment_tests.ScheduledTaskEnvironmentContractTests.payload
    serializer = environment_tests.ScheduledTaskEnvironmentContractTests.serializer

    def setUp(self):
        environment_tests.ScheduledTaskEnvironmentContractTests.setUp(self)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.path = f'/api/v1/projects/{self.project.pk}/scheduled-tasks/'

    def test_derives_type_and_labels_unavailable_choices(self):
        empty = WebUITestSuite.objects.create(name='Empty', user=self.user, project=self.project)
        result = self.client.get(self.path + 'suite-choices/')
        self.assertEqual(result.status_code, 200)
        rows = {row['id']: row for row in result.data['data']}
        self.assertTrue(rows[self.web_suite.pk]['selectable'])
        self.assertFalse(rows[empty.pk]['selectable'])
        payload = self.payload('web', self.web_suite.pk)
        del payload['suite_type']
        result = self.client.post(self.path + 'tasks/', payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(ScheduledTask.objects.get().suite_type, 'web')

    def test_rejects_foreign_empty_inactive_and_duplicate_suites(self):
        empty = WebUITestSuite.objects.create(name='Empty', user=self.user, project=self.project)
        foreign_project = Project.objects.create(name='Other', project_type='web', created_by=self.user)
        foreign = WebUITestSuite.objects.create(name='Foreign', user=self.user, project=foreign_project)
        for ids in ([empty.pk], [foreign.pk], [999999], [self.web_suite.pk] * 2, ['1'], []):
            serializer = self.serializer('web', data={**self.payload('web', self.web_suite.pk), 'suite_ids': ids})
            self.assertFalse(serializer.is_valid(), ids)
            self.assertIn('suite_ids', serializer.errors)
        self.web_suite.status = 'inactive'
        self.web_suite.save()
        self.assertFalse(self.serializer('web', data=self.payload('web', self.web_suite.pk)).is_valid())

    def test_requires_project_access_and_write_capability(self):
        viewer = get_user_model().objects.create_user(username='viewer', email='viewer@schedule.test')
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get(self.path + 'suite-choices/').status_code, 404)
        ProjectMember.objects.create(project=self.project, user=viewer, can_edit=False)
        self.assertEqual(self.client.get(self.path + 'suite-choices/').status_code, 200)
        self.assertEqual(self.client.post(self.path + 'tasks/', self.payload('web', self.web_suite.pk), format='json').status_code, 403)

    def test_pause_clears_next_time_and_manual_request_is_allowed(self):
        serializer = self.serializer('web', data=self.payload('web', self.web_suite.pk, status='paused'))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        task = serializer.save(user=self.user, project=self.project)
        self.assertIsNone(task.next_run_time)
        run = TaskRunSerializer(data={}, context={'task': task})
        self.assertTrue(run.is_valid(), run.errors)

    def test_beat_and_preview_share_timezone(self):
        serializer = self.serializer('web', data=self.payload('web', self.web_suite.pk))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        task = serializer.save(user=self.user, project=self.project)
        from django_celery_beat.models import PeriodicTask
        beat = PeriodicTask.objects.get(name=f'scheduled_task_{task.pk}')
        self.assertEqual(str(beat.crontab.timezone), 'Asia/Shanghai')

    def test_failed_registration_rolls_back_task(self):
        serializer = self.serializer('web', data=self.payload('web', self.web_suite.pk))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch('scheduled_tasks.signals.register_periodic_task', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                serializer.save(user=self.user, project=self.project)
        self.assertFalse(ScheduledTask.objects.exists())

    def make_task(self):
        serializer = self.serializer('web', data=self.payload('web', self.web_suite.pk))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save(user=self.user, project=self.project)

    def test_run_rejects_overlap_and_running_records_cannot_be_deleted(self):
        task = self.make_task()
        log = TaskExecutionLog.objects.create(task=task, status='running', start_time=timezone.now())
        result = self.client.post(self.path + f'tasks/{task.pk}/run/', {}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(task.execution_logs.count(), 1)
        self.assertEqual(self.client.delete(self.path + f'tasks/{task.pk}/').status_code, 400)
        self.assertEqual(self.client.delete(self.path + f'execution-logs/{log.pk}/').status_code, 400)

    def test_can_pause_deleted_suite_but_cannot_reenable(self):
        task = self.make_task()
        self.web_suite.delete()
        path = self.path + f'tasks/{task.pk}/status/'
        self.assertEqual(self.client.patch(path, {'status': 'paused'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(path, {'status': 'active'}, format='json').status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.status, 'paused')

    def test_invalid_cron_request_does_not_create_a_task(self):
        result = self.client.post(self.path + 'tasks/', self.payload('web', self.web_suite.pk, cron_expression='70 * * * *'), format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(ScheduledTask.objects.exists())

    def test_preflight_errors_have_consistent_list_and_detail_statuses(self):
        task = self.make_task()
        log = TaskExecutionLog.objects.create(task=task, status='failed', start_time=timezone.now())
        self.assertEqual(TaskExecutionLogSerializer(log).data['report_status'], 'error')
        self.assertEqual(TaskExecutionLogSerializer(log).data['execution_errors'], 1)
        self.assertEqual(TaskExecutionLogListSerializer(log).data['report_status'], 'error')
        TaskExecutionLog.objects.create(task=task, status='running', start_time=timezone.now())
        response = self.client.get(self.path + 'execution-logs/', {'status': 'failed'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['id'] for row in response.data['data']['items']], [log.id])
