from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from .beat_health import (
    BeatHealthDatabaseScheduler,
    HEARTBEAT_TTL_SECONDS,
    get_beat_service_status,
    heartbeat_cache_key,
)


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'beat-health-test',
}})
class BeatSchedulerHeartbeatTests(SimpleTestCase):
    def make_scheduler(self):
        scheduler = object.__new__(BeatHealthDatabaseScheduler)
        scheduler._last_heartbeat_attempt_monotonic = None
        return scheduler

    def test_tick_writes_heartbeat_at_most_once_per_ten_seconds(self):
        scheduler = self.make_scheduler()
        with patch('scheduled_tasks.beat_health.DatabaseScheduler.tick', return_value=5), patch(
            'scheduled_tasks.beat_health.time.monotonic', side_effect=(0, 5, 10),
        ), patch('scheduled_tasks.beat_health.timezone.now') as now, patch(
            'scheduled_tasks.beat_health.cache.set', return_value=True,
        ) as set_heartbeat:
            now.return_value.timestamp.return_value = 1_700_000_000
            self.assertEqual(scheduler.tick(), 5)
            self.assertEqual(scheduler.tick(), 5)
            self.assertEqual(scheduler.tick(), 5)

        self.assertEqual(set_heartbeat.call_count, 2)
        self.assertEqual(set_heartbeat.call_args.kwargs['timeout'], HEARTBEAT_TTL_SECONDS)

    def test_scheduler_caps_an_explicit_loop_interval_at_heartbeat_interval(self):
        def initialize_parent(instance, *args, **kwargs):
            instance.max_interval = kwargs['max_interval']

        with patch.object(BeatHealthDatabaseScheduler.__mro__[1], '__init__', new=initialize_parent):
            scheduler = BeatHealthDatabaseScheduler(max_interval=300)

        self.assertEqual(scheduler.max_interval, 10)

    def test_tick_writes_a_timestamp_after_normal_scheduler_tick(self):
        scheduler = self.make_scheduler()
        with patch('scheduled_tasks.beat_health.DatabaseScheduler.tick', return_value=3) as parent_tick, patch(
            'scheduled_tasks.beat_health.time.monotonic', return_value=0,
        ), patch('scheduled_tasks.beat_health.timezone.now') as now, patch(
            'scheduled_tasks.beat_health.cache.set', return_value=True,
        ) as set_heartbeat:
            now.return_value.timestamp.return_value = 1_700_000_000
            self.assertEqual(scheduler.tick(), 3)

        parent_tick.assert_called_once_with()
        self.assertEqual(set_heartbeat.call_args.args[1], 1_700_000_000)

    def test_cache_write_failure_does_not_interrupt_tick(self):
        scheduler = self.make_scheduler()
        with patch('scheduled_tasks.beat_health.DatabaseScheduler.tick', return_value=5), patch(
            'scheduled_tasks.beat_health.time.monotonic', return_value=0,
        ), patch('scheduled_tasks.beat_health.cache.set', side_effect=RuntimeError('cache unavailable')), patch(
            'scheduled_tasks.beat_health.logger.warning',
        ):
            self.assertEqual(scheduler.tick(), 5)

    def test_tick_failure_does_not_publish_a_false_heartbeat(self):
        scheduler = self.make_scheduler()
        with patch('scheduled_tasks.beat_health.DatabaseScheduler.tick', side_effect=RuntimeError('database unavailable')), patch(
            'scheduled_tasks.beat_health.cache.set', return_value=True,
        ) as set_heartbeat:
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                scheduler.tick()

        set_heartbeat.assert_not_called()


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'beat-health-test',
}})
class BeatServiceStatusTests(SimpleTestCase):
    def setUp(self):
        self.key = heartbeat_cache_key()
        cache.delete(self.key)
        self.now = timezone.now()

    def tearDown(self):
        cache.delete(self.key)

    def test_unstarted_and_expired_heartbeats_are_offline(self):
        self.assertEqual(get_beat_service_status(now=self.now)['status'], 'offline')
        cache.set(self.key, (self.now - timedelta(seconds=61)).timestamp(), timeout=120)
        status = get_beat_service_status(now=self.now)
        self.assertEqual(status['status'], 'offline')
        self.assertIsNotNone(status['last_heartbeat_at'])

    def test_fresh_heartbeat_is_online(self):
        cache.set(self.key, self.now.timestamp(), timeout=120)
        status = get_beat_service_status(now=self.now)
        self.assertEqual(status['status'], 'online')
        self.assertEqual(status['heartbeat_timeout_seconds'], 60)
        self.assertEqual(status['check_interval_seconds'], 15)

    def test_cache_failures_and_invalid_values_are_unknown(self):
        cache.set(self.key, 'not-a-timestamp', timeout=120)
        self.assertEqual(get_beat_service_status(now=self.now)['status'], 'unknown')
        cache.set(self.key, (self.now + timedelta(minutes=2)).timestamp(), timeout=120)
        self.assertEqual(get_beat_service_status(now=self.now)['status'], 'unknown')
        with patch('scheduled_tasks.beat_health.cache.get', side_effect=RuntimeError('cache unavailable')):
            self.assertEqual(get_beat_service_status(now=self.now)['status'], 'unknown')

    def test_database_identity_changes_the_heartbeat_key_without_using_passwords(self):
        first_database = {
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'first', 'HOST': '', 'PORT': '',
            'PASSWORD': 'not-in-key',
        }
        second_database = {**first_database, 'NAME': 'second'}
        with override_settings(DATABASES={'default': first_database}):
            first_key = heartbeat_cache_key()
        with override_settings(DATABASES={'default': second_database}):
            second_key = heartbeat_cache_key()

        self.assertNotEqual(first_key, second_key)
        self.assertNotIn('not-in-key', first_key)


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'beat-health-test',
}})
class ServiceStatusApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='schedule-owner', email='schedule-owner@example.test',
        )
        self.project = Project.objects.create(
            name='Schedule status project', project_type='web', owner=self.user, created_by=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.path = f'/api/v1/projects/{self.project.pk}/scheduled-tasks/service-status/'
        cache.delete(heartbeat_cache_key())

    def tearDown(self):
        cache.delete(heartbeat_cache_key())

    def test_api_returns_the_common_envelope_and_online_status(self):
        cache.set(heartbeat_cache_key(), timezone.now().timestamp(), timeout=120)
        result = self.client.get(self.path)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        self.assertEqual(result.data['data']['status'], 'online')
        self.assertIn('last_heartbeat_at', result.data['data'])

    def test_api_requires_project_read_access(self):
        viewer = get_user_model().objects.create_user(
            username='schedule-viewer', email='schedule-viewer@example.test',
        )
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        ProjectMember.objects.create(project=self.project, user=viewer, can_edit=False)
        self.assertEqual(self.client.get(self.path).status_code, 200)

    def test_anonymous_api_does_not_return_service_status(self):
        result = APIClient().get(self.path)
        self.assertEqual(result.status_code, 401)
        self.assertNotIn('data', result.data)
