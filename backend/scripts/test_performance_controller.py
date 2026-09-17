"""Controller fencing and recovery tests: no subprocesses and no load."""
from datetime import timedelta
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from performance_testing.controller import PerformanceController, child_environment
from performance_testing.models import PerformanceControllerState, PerformanceNode, PerformanceRun
from performance_testing.runtime_settings import RuntimeSettings
from projects.models import Project
from users.models import User


class ControllerIsolationTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='controller-contract-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.config = RuntimeSettings(True, '127.0.0.1', 9443, '127.0.0.1', '127.0.0.1', root / 'runs', '/usr/bin/false')
        self.controller = PerformanceController(self.config)
        self.user = User.objects.create_user(username='controller-fixture', is_staff=True)
        self.project = Project.objects.create(name='fixture', project_type='perf', created_by=self.user)
        self.node = PerformanceNode.objects.create(project=self.project, name='fixture', network_mode='lan')

    def run_record(self, status='running'):
        return PerformanceRun.objects.create(project=self.project, node=self.node, created_by=self.user,
            request_id=uuid.uuid4(), snapshot={}, snapshot_sha256='0' * 64, status=status)

    def attach(self, run):
        self.assertTrue(self.controller.claim())
        self.controller.current = run.pk
        self.controller.reaping_since = None
        PerformanceControllerState.objects.filter(pk=1).update(current_run=run)

    def test_lease_cannot_be_stolen_or_resurrected(self):
        self.assertTrue(self.controller.claim())
        self.assertFalse(PerformanceController(self.config).claim())
        PerformanceControllerState.objects.filter(pk=1).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertFalse(self.controller.renew())
        successor = PerformanceController(self.config)
        self.assertTrue(successor.claim())
        self.assertFalse(self.controller.renew())

    def test_takeover_does_not_replay_old_run(self):
        run = self.run_record()
        queued = self.run_record('queued')
        self.assertTrue(self.controller.claim())
        run.refresh_from_db()
        self.assertEqual(run.status, 'stopping')
        self.assertEqual(run.node_command['type'], 'stop')
        with patch.object(self.controller, 'prepare') as prepare:
            self.controller.tick()
            prepare.assert_not_called()
        queued.refresh_from_db()
        self.assertEqual(queued.status, 'queued')

    def test_old_controller_cannot_overwrite_new_owner(self):
        run = self.run_record()
        self.attach(run)
        PerformanceControllerState.objects.filter(pk=1).update(owner_id='successor')
        with self.assertRaises(RuntimeError):
            self.controller.record_metrics(run, {'requests': 10, 'started': True})
        with self.assertRaises(RuntimeError):
            self.controller.begin_reap('failed', 'test', 'test')
        run.refresh_from_db()
        self.assertEqual(run.latest_metrics, {})

    def test_terminal_and_stop_not_overwritten_by_late_metrics(self):
        run = self.run_record()
        self.attach(run)
        PerformanceRun.objects.filter(pk=run.pk).update(status='stopping', stop_requested_at=timezone.now())
        self.controller.record_metrics(run, {'requests': 10, 'started': True})
        run.refresh_from_db()
        self.assertEqual(run.status, 'stopping')
        PerformanceRun.objects.filter(pk=run.pk).update(status='cancelled')
        self.controller.record_metrics(run, {'requests': 11, 'started': True})
        run.refresh_from_db()
        self.assertEqual(run.latest_metrics['requests'], 10)

    def test_report_ack_does_not_release_live_master_supervisor(self):
        run = self.run_record()
        self.attach(run)
        run.node_report = {'state': 'stopped'}
        self.controller.pending_result = ('completed', '', '')
        self.controller.reaping_since = 0
        with patch.object(self.controller, 'stop_process'), patch.object(self.controller, 'process') as process:
            process.poll.return_value = None
            self.controller.reap(run)
        self.assertEqual(self.controller.current, run.pk)

    def test_child_environment_does_not_receive_platform_secrets(self):
        with patch.dict('os.environ', {'DB_PASSWORD': 'private', 'PERFORMANCE_NODE_TOKEN': 'private',
                                       'HTTPS_PROXY': 'private', 'OPENAI_API_KEY': 'private'}):
            environment = child_environment()
        for key in ('DB_PASSWORD', 'PERFORMANCE_NODE_TOKEN', 'HTTPS_PROXY', 'OPENAI_API_KEY'):
            self.assertNotIn(key, environment)

    def test_node_revocation_retains_specific_reason_during_recovery(self):
        run = self.run_record()
        self.attach(run)
        PerformanceRun.objects.filter(pk=run.pk).update(reason_code='node_revoked', reason='节点已吊销')
        self.controller.directory = Path(self.temporary.name)
        with patch('performance_testing.controller.read_json', return_value=None), patch.object(self.controller, 'begin_reap') as reap:
            self.controller.tick()
        reap.assert_called_once_with('failed', 'node_revoked', '节点已吊销')
