"""Controller fencing and recovery tests: no subprocesses and no load."""
from datetime import timedelta
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from performance_testing.controller import PerformanceController, bounded_metrics, child_environment, LEASE_RECLAIM_SECONDS
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
        run = PerformanceRun.objects.create(project=self.project, created_by=self.user,
            request_id=uuid.uuid4(), snapshot={'nodes': [{'node_id': str(self.node.pk), 'users': 1}]},
            snapshot_sha256='0' * 64, status=status)
        run.participants.create(node=self.node, node_name=self.node.name, assigned_users=1)
        return run

    def attach(self, run):
        status = run.status
        self.assertTrue(self.controller.claim())
        self.controller.current = run.pk
        self.controller.reaping_since = None
        self.controller.orphan = False
        PerformanceRun.objects.filter(pk=run.pk).update(status=status, reason_code='', reason='',
                                                       latest_metrics=run.latest_metrics)
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
        self.assertEqual(run.participants.get().node_command['type'], 'stop')
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
        run.participants.update(node_report={'state': 'stopped'})
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

    def test_failure_evidence_is_bounded_and_malformed_samples_are_discarded(self):
        sample = {
            'step_index': 1, 'step_name': 'main', 'phase': 'main', 'check': 'body.data',
            'comparator': 'eq', 'expected': 'x' * 5000, 'actual': {'missing': True},
            'error_type': 'assertion_failed', 'message': 'm' * 1000,
        }
        metrics = bounded_metrics({
            'requests': 1,
            'failure_samples': [sample] * 30 + [{'unexpected': True}],
        })
        self.assertEqual(len(metrics['failure_samples']), 20)
        self.assertLessEqual(len(metrics['failure_samples'][0]['message']), 500)
        self.assertLessEqual(
            len(str(metrics['failure_samples'][0]['expected']).encode('utf-8')), 2051,
        )

    def test_validation_details_are_not_repeated_in_trend_history_or_load_runs(self):
        run = self.run_record()
        run.mode = 'validation'
        run.save(update_fields=['mode'])
        self.attach(run)
        step = {'step_index': 1, 'step_name': 'fixture', 'phase': 'main', 'method': 'GET',
                'status': 'passed', 'response': {'status_code': 200,
                    'body': {'content': '{"ok": true}', 'truncated': False}}}
        self.controller.record_metrics(run, {'requests': 1, 'started': True, 'complete': True,
                                            'validation_steps': [step]})
        run.refresh_from_db()
        self.assertEqual(run.latest_metrics['validation_steps'][0]['status'], 'passed')
        self.assertNotIn('validation_steps', run.metrics_samples[-1]['metrics'])
        run.mode = 'load'
        run.save(update_fields=['mode'])
        self.controller.record_metrics(run, {'requests': 2, 'validation_steps': [step]})
        run.refresh_from_db()
        self.assertNotIn('validation_steps', run.latest_metrics)

    def test_node_revocation_retains_specific_reason_during_recovery(self):
        run = self.run_record()
        self.attach(run)
        PerformanceRun.objects.filter(pk=run.pk).update(reason_code='node_revoked', reason='节点已吊销')
        self.controller.directory = Path(self.temporary.name)
        with patch('performance_testing.controller.read_json', return_value=None), patch.object(self.controller, 'begin_reap') as reap:
            self.controller.tick()
        reap.assert_called_once()
        # begin_reap retains the first persisted cause under the Run lock.
        self.controller.begin_reap('failed', 'node_unavailable', '离线')
        run.refresh_from_db()
        self.assertEqual(run.reason_code, 'node_revoked')

    def test_all_members_must_acknowledge_or_prove_their_last_lease_expired(self):
        run = self.run_record()
        other = PerformanceNode.objects.create(project=self.project, name='second', network_mode='lan')
        second = run.participants.create(node=other, node_name=other.name, assigned_users=1,
            last_command_at=timezone.now())
        self.attach(run)
        run.participants.filter(node=self.node).update(node_report={'state': 'stopped'})
        self.controller.begin_reap('incomplete', 'worker_lost', 'lost')
        self.controller.reaping_since = 0  # waiting alone never proves expiry
        self.controller.reap(run)
        self.assertEqual(self.controller.current, run.pk)
        second.last_command_at = timezone.now() - timedelta(seconds=LEASE_RECLAIM_SECONDS + 1)
        second.save(update_fields=['last_command_at'])
        self.controller.reap(run)
        run.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(run.status, 'incomplete')
        self.assertEqual(second.status, 'lost')
        self.assertEqual(second.reason_code, 'lease_expired')
        self.assertEqual(second.node_command, {})
        self.assertIsNone(self.controller.current)

    def test_exact_member_identity_set_is_required_before_start(self):
        from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
        run = self.run_record('preparing')
        self.attach(run)
        self.node.last_seen_at = timezone.now()
        self.node.agent_token_digest = '1' * 64
        self.node.agent_version, self.node.engine_version = AGENT_VERSION, ENGINE_VERSION
        self.node.protocol_version = PROTOCOL_VERSION
        self.node.save()
        run.participants.update(node_report={'state': 'ready'})
        self.controller.directory = Path(self.temporary.name)
        metrics = {'requests': 0, 'admitted_workers': 1, 'admitted_node_ids': [str(uuid.uuid4())]}

        def read(path):
            return metrics if path.name == 'metrics.json' else None

        with patch('performance_testing.controller.read_json', side_effect=read):
            self.controller.tick()
            self.assertFalse((self.controller.directory / 'start').exists())
            metrics['admitted_node_ids'] = [str(self.node.pk)]
            self.controller.tick()
            self.assertTrue((self.controller.directory / 'start').exists())

    def test_user_stop_signals_master_and_keeps_members_leased_for_final_metrics(self):
        run = self.run_record('running')
        self.attach(run)
        self.node.last_seen_at = timezone.now()
        self.node.agent_token_digest = '1' * 64
        self.node.save()
        run.participants.update(node_command={'type': 'prepare'}, node_report={'state': 'running'})
        PerformanceRun.objects.filter(pk=run.pk).update(
            status='stopping', stop_requested_at=timezone.now(), reason_code='cancelled')
        self.controller.directory = Path(self.temporary.name)
        with patch('performance_testing.controller.read_json', return_value=None), \
                patch.object(self.controller, 'stop_process') as stop:
            self.controller.tick()
            stop.assert_not_called()
        self.assertTrue((self.controller.directory / 'start.stop').exists())
        self.assertEqual(run.participants.get().node_command['type'], 'prepare')

    def test_final_file_appearing_between_read_and_process_exit_is_not_lost(self):
        run = self.run_record()
        self.attach(run)
        self.controller.directory = Path(self.temporary.name)
        final_reads = 0

        def read(path):
            nonlocal final_reads
            if path.name == 'complete.json':
                final_reads += 1
                return None if final_reads == 1 else {'complete': True, 'reason': 'completed',
                    'metrics': {'requests': 20, 'complete': True, 'started': True}}
            if path.name == 'metrics.json':
                return {'requests': 10, 'started': True}
            return {'reason': 'finished'}

        with patch('performance_testing.controller.read_json', side_effect=read), \
                patch.object(self.controller, 'begin_reap') as reap:
            self.controller.tick()
        run.refresh_from_db()
        self.assertEqual(run.latest_metrics['requests'], 20)
        reap.assert_called_once_with('completed', '', '压测执行完成')

    def test_ready_report_changing_before_parent_lock_cannot_start(self):
        from contextlib import contextmanager
        run = self.run_record('preparing')
        self.attach(run)
        self.node.last_seen_at = timezone.now()
        self.node.agent_token_digest = '1' * 64
        self.node.save()
        run.participants.update(node_report={'state': 'ready'})
        self.controller.directory = Path(self.temporary.name)
        original = self.controller.owned_run
        calls = 0

        @contextmanager
        def changing_lock(run_id):
            nonlocal calls
            calls += 1
            if calls == 2:  # first is metric write, second is start barrier
                run.participants.update(node_report={'state': 'failed'})
            with original(run_id) as value:
                yield value

        def read(path):
            return {'requests': 0, 'admitted_node_ids': [str(self.node.pk)]} if path.name == 'metrics.json' else None

        with patch('performance_testing.controller.read_json', side_effect=read), \
                patch.object(self.controller, 'owned_run', side_effect=changing_lock):
            self.controller.tick()
        self.assertEqual(calls, 2)
        self.assertFalse((self.controller.directory / 'start').exists())

    def test_member_metrics_cannot_update_foreign_nodes_or_repeat_validation_details(self):
        run = self.run_record('running')
        self.attach(run)
        self.controller.record_metrics(run, {'requests': 5, 'started': True, 'complete': True,
            'nodes': {str(self.node.pk): {'requests': 5, 'users': 0, 'complete': True,
                       'validation_steps': [{'private': 'detail'}]},
                      str(uuid.uuid4()): {'requests': 99}}})
        run.refresh_from_db()
        member = run.participants.get()
        self.assertNotIn('nodes', run.latest_metrics)
        self.assertEqual(member.latest_metrics['requests'], 5)
        self.assertNotIn('validation_steps', member.latest_metrics)
        self.assertEqual(len(member.metrics_samples), 1)

    def test_per_node_certificates_have_distinct_keys_and_shared_run_ca(self):
        from cryptography import x509
        from performance_testing.pki import create_run_certificates
        nodes = [str(self.node.pk), str(uuid.uuid4())]
        result = create_run_certificates(Path(self.temporary.name), 'localhost', uuid.uuid4(), nodes)
        self.assertEqual(set(result), set(nodes))
        first, second = (result[node] for node in nodes)
        self.assertEqual(first['ca_pem'], second['ca_pem'])
        self.assertNotEqual(first['key_pem'], second['key_pem'])
        for node_id in nodes:
            certificate = x509.load_pem_x509_certificate(result[node_id]['cert_pem'].encode())
            self.assertEqual(certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value, node_id)
            self.assertEqual((Path(self.temporary.name) / f'client-{node_id}.key').stat().st_mode & 0o777, 0o600)
