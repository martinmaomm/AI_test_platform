"""Connection outage recovery with isolated DB records; no remote services/load."""
from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from performance_testing.controller import ControllerLeaseLost, PerformanceController
from performance_testing.management.commands import run_performance_controller as command
from performance_testing.models import PerformanceControllerState, PerformanceNode, PerformanceRun
from performance_testing.runtime_settings import RuntimeSettings
from projects.models import Project
from users.models import User


class StopEvent:
    def __init__(self, on_wait=None):
        self.stopped = False
        self.waits = []
        self.on_wait = on_wait

    def set(self):
        self.stopped = True

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        if len(self.waits) > 30:
            raise AssertionError('Recovery loop did not stop')
        if self.on_wait:
            self.on_wait(seconds)
        return self.stopped


class ControllerCommandRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.stdout, self.stderr = StringIO(), StringIO()
        self.command = command.Command(stdout=self.stdout, stderr=self.stderr)
        self.stopping = StopEvent()
        self.close = self.enterContext(patch.object(command.connections, 'close_all'))
        self.enterContext(patch.object(command, 'close_old_connections'))

    def controller(self, *, error=None, claim=True):
        controller = Mock(spec=PerformanceController)
        controller.process = None
        controller.claim.return_value = claim
        if error:
            controller.claim.side_effect = error
        controller.tick.side_effect = self.stopping.set
        return controller

    def test_startup_outage_retries_without_exiting_and_redacts_errors(self):
        controllers = [self.controller(error=OperationalError(2003, 'private-db-password')),
                       self.controller(error=InterfaceError(0, 'private-db-password')),
                       self.controller()]
        with patch.object(command, 'PerformanceController', side_effect=controllers):
            self.command.run(self.stopping)
        self.assertEqual(self.stopping.waits, [1, 2, .5])
        self.assertIn('恢复调度', self.stdout.getvalue())
        self.assertEqual(self.stderr.getvalue().count('数据库连接中断'), 1)
        self.assertNotIn('private-db-password', self.stdout.getvalue() + self.stderr.getvalue())
        controllers[-1].tick.assert_called_once()
        self.assertGreaterEqual(self.close.call_count, 3)

    def test_long_outage_keeps_retrying_with_capped_delay(self):
        failures = [self.controller(error=OperationalError(2003, 'offline')) for _ in range(8)]
        with patch.object(command, 'PerformanceController', side_effect=[*failures, self.controller()]):
            self.command.run(self.stopping)
        self.assertEqual(self.stopping.waits, [1, 2, 4, 8, 16, 30, 30, 30, .5])

    def test_recovery_waits_for_old_local_supervisor_before_new_claim(self):
        old = self.controller()
        old.tick.side_effect = OperationalError(2013, 'connection lost')
        state = {'stops': 0, 'alive': True}
        old.process = Mock()
        old.process.poll.side_effect = lambda: None if state['alive'] else 0

        def stop():
            state['stops'] += 1
            state['alive'] = state['stops'] < 3

        old.stop_process.side_effect = stop
        replacement = self.controller()
        instances = [old, replacement]

        def create():
            instance = instances.pop(0)
            if instance is replacement:
                self.assertFalse(state['alive'])
            return instance

        with patch.object(command, 'PerformanceController', side_effect=create):
            self.command.run(self.stopping)
        old.close.assert_not_called()
        replacement.tick.assert_called_once()
        replacement.close.assert_called_once()

    def test_lease_loss_and_claim_conflict_wait_without_overwriting_owner(self):
        old = self.controller()
        old.tick.side_effect = ControllerLeaseLost('expired')
        busy = self.controller(claim=False)
        replacement = self.controller()
        with patch.object(command, 'PerformanceController', side_effect=[old, busy, replacement]):
            self.command.run(self.stopping)
        busy.tick.assert_not_called()
        busy.close.assert_not_called()
        self.assertEqual(self.stopping.waits, [1, 2, .5])
        self.assertIn('调度租约失效', self.stderr.getvalue())

    def test_initial_duplicate_controller_still_fails(self):
        with patch.object(command, 'PerformanceController', return_value=self.controller(claim=False)):
            with self.assertRaisesMessage(CommandError, '已有性能执行控制器持有租约'):
                self.command.run(self.stopping)
        self.assertEqual(self.stopping.waits, [])

    def test_invalid_sql_and_credentials_do_not_loop_forever(self):
        for code in (1045, 1064, 1146):
            with self.subTest(code=code):
                controller = self.controller(error=OperationalError(code, 'invalid configuration'))
                with patch.object(command, 'PerformanceController', return_value=controller):
                    with self.assertRaises(OperationalError):
                        self.command.run(self.stopping)
        self.assertEqual(self.stopping.waits, [])

    def test_shutdown_during_outage_does_not_require_database_cleanup(self):
        self.stopping.on_wait = lambda _: self.stopping.set()
        controller = self.controller(error=OperationalError(2003, 'offline'))
        with patch.object(command, 'PerformanceController', return_value=controller):
            self.command.run(self.stopping)
        controller.close.assert_not_called()
        self.assertGreaterEqual(controller.stop_process.call_count, 1)

    def test_shutdown_connection_error_still_stops_local_process(self):
        controller = self.controller()
        controller.close.side_effect = OperationalError(2003, 'private-db-password')
        with patch.object(command, 'PerformanceController', return_value=controller):
            self.command.run(self.stopping)
        controller.stop_process.assert_called_once()
        self.assertNotIn('private-db-password', self.stderr.getvalue())

    def test_mysql_timeouts_are_bounded_only_in_controller_process(self):
        original = {'charset': 'utf8mb4', 'connect_timeout': 2, 'read_timeout': None,
                    'write_timeout': 90}
        database = SimpleNamespace(vendor='mysql', settings_dict={'OPTIONS': original}, close=Mock())
        with patch.object(command, 'connections', {'default': database}):
            command.configure_database_timeouts()
        self.assertEqual(database.settings_dict['OPTIONS'], {
            'charset': 'utf8mb4', 'connect_timeout': 2, 'read_timeout': 5, 'write_timeout': 5,
        })
        self.assertIsNone(original['read_timeout'])
        database.close.assert_called_once()


class ControllerRecordRecoveryTests(TestCase):
    def test_interrupted_run_becomes_incomplete_and_queued_run_is_not_replayed(self):
        user = User.objects.create_user(username='db-recovery-fixture', is_staff=True)
        project = Project.objects.create(name='recovery fixture', project_type='perf', created_by=user)
        node = PerformanceNode.objects.create(project=project, name='fixture', network_mode='lan')
        with tempfile.TemporaryDirectory(prefix='controller-recovery-') as temporary:
            config = RuntimeSettings(True, '127.0.0.1', 9443, '127.0.0.1', '127.0.0.1',
                                     Path(temporary) / 'runs', '/usr/bin/false')
            original = PerformanceController(config)
            self.assertTrue(original.claim())

            def run_record(status):
                run = PerformanceRun.objects.create(
                    project=project, created_by=user, request_id=uuid.uuid4(),
                    snapshot={}, snapshot_sha256='0' * 64, status=status,
                    started_at=timezone.now() if status == 'running' else None)
                run.participants.create(node=node, node_name=node.name, assigned_users=1)
                return run

            active, queued = run_record('running'), run_record('queued')
            active.participants.update(node_report={'state': 'stopped'})
            original.current = active.pk
            PerformanceControllerState.objects.filter(pk=1).update(current_run=active)
            created = []
            stopping = StopEvent()

            def factory():
                instance = original if not created else PerformanceController(config)
                created.append(instance)
                return instance

            def wait(seconds):
                if seconds == 2:
                    # The first reconnect must wait for the old valid lease.
                    self.assertEqual(PerformanceControllerState.objects.get(pk=1).owner_id, original.owner)
                    PerformanceControllerState.objects.filter(pk=1).update(
                        lease_until=timezone.now() - timedelta(seconds=1))
                if seconds == .5:
                    stopping.set()

            stopping.on_wait = wait
            runner = command.Command(stdout=StringIO(), stderr=StringIO())
            with patch.object(command, 'PerformanceController', side_effect=factory), \
                 patch.object(command, 'close_old_connections'), \
                 patch.object(command.connections, 'close_all'), \
                 patch.object(original, 'claim', return_value=True), \
                 patch.object(original, 'tick', side_effect=OperationalError(2013, 'offline')), \
                 patch.object(PerformanceController, 'prepare') as prepare, \
                 patch('performance_testing.controller.LEASE_RECLAIM_SECONDS', 0):
                runner.run(stopping)
            active.refresh_from_db()
            queued.refresh_from_db()
            self.assertEqual(active.status, 'incomplete')
            self.assertEqual(active.reason_code, 'controller_restarted')
            self.assertEqual(active.participants.get().node_command, {})
            self.assertEqual(queued.status, 'queued')
            prepare.assert_not_called()
            self.assertEqual(stopping.waits, [1, 2, .5])

    def test_prepare_database_loss_is_not_misreported_as_generic_prepare_failure(self):
        user = User.objects.create_user(username='prepare-recovery-fixture', is_staff=True)
        project = Project.objects.create(name='fixture', project_type='perf', created_by=user)
        node = PerformanceNode.objects.create(project=project, name='fixture', network_mode='lan')
        with tempfile.TemporaryDirectory(prefix='controller-prepare-') as temporary:
            config = RuntimeSettings(True, '127.0.0.1', 9443, '127.0.0.1', '127.0.0.1',
                                     Path(temporary) / 'runs', '/usr/bin/false')
            controller = PerformanceController(config)
            self.assertTrue(controller.claim())
            run = PerformanceRun.objects.create(
                project=project, created_by=user, request_id=uuid.uuid4(),
                snapshot={}, snapshot_sha256='0' * 64, status='queued')
            with patch.object(controller, 'prepare', side_effect=OperationalError(2013, 'offline')), \
                 patch.object(controller, 'begin_reap') as reap:
                with self.assertRaises(OperationalError):
                    controller.tick()
            reap.assert_not_called()
            run.refresh_from_db()
            self.assertEqual(run.status, 'preparing')
