import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.db.models.deletion import RestrictedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
from performance_testing.models import (
    PerformanceControllerState, PerformanceNode, PerformancePlan, PerformanceRun,
    PerformanceTarget,
)
from performance_testing.services import consume_enrollment, issue_enrollment
from projects.models import Project, ProjectMember
from projects.views import ProjectViewSet
from users.models import User


AVAILABLE = {'enabled': True, 'available': True, 'reason': ''}


class PerformanceRunContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='run-admin', email='run-admin@example.test', is_staff=True,
        )
        self.executor = User.objects.create_user(
            username='run-executor', email='run-executor@example.test',
        )
        self.reader = User.objects.create_user(
            username='run-reader', email='run-reader@example.test',
        )
        self.execute_only = User.objects.create_user(
            username='run-execute-only', email='run-execute-only@example.test',
        )
        self.project = Project.objects.create(
            name='runs', project_type='perf', created_by=self.admin,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.executor, role='editor', can_edit=True,
            can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.reader, role='viewer', can_edit=False,
            can_execute_tests=False, can_view_reports=False,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.execute_only, role='editor', can_edit=False,
            can_execute_tests=True, can_view_reports=False,
        )
        self.target = PerformanceTarget.objects.create(
            project=self.project, name='target', base_url='https://target.example.test',
            allowed_methods=['GET', 'POST'],
        )
        self.plan = PerformancePlan.objects.create(
            project=self.project, target=self.target, name='frozen plan', users=2,
            spawn_rate=1.5, duration_seconds=5, wait_seconds=.5,
            steps=[{
                'name': 'list', 'method': 'GET', 'path': '/items?limit=1',
                'expected_status': 200, 'headers': {'Accept': 'application/json'},
                'body': None,
            }],
        )
        self.node, self.agent_token = self._node('node-one')
        self._fresh_controller()

    def _node(self, name):
        node = PerformanceNode.objects.create(
            project=self.project, name=name, network_mode='lan',
        )
        node, enrollment = issue_enrollment(node)
        node, agent_token = consume_enrollment(enrollment, {
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
        })
        PerformanceNode.objects.filter(pk=node.pk).update(last_seen_at=timezone.now())
        node.refresh_from_db()
        return node, agent_token

    def _fresh_controller(self, current_run=None):
        now = timezone.now()
        return PerformanceControllerState.objects.update_or_create(pk=1, defaults={
            'owner_id': 'controller-test',
            'heartbeat_at': now,
            'lease_until': now + timedelta(seconds=30),
            'current_run': current_run,
        })[0]

    def _path(self, suffix):
        return f'/api/v1/projects/{self.project.pk}/performance/{suffix}'

    def _create(self, request_id=None, node=None):
        self.client.force_authenticate(self.executor)
        return self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'),
            {'node_id': str((node or self.node).pk), 'request_id': str(request_id or uuid.uuid4())},
            format='json',
        )

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_config_and_create_freeze_exact_snapshot_and_never_serialize_private_command(self, _):
        self.client.force_authenticate(self.executor)
        config = self.client.get(self._path('config/'))
        self.assertTrue(config.data['data']['controller_online'])
        self.assertTrue(config.data['data']['execution_enabled'])
        self.assertEqual(config.data['data']['max_nodes_per_run'], 1)

        created = self._create()
        self.assertEqual(created.status_code, 201, created.data)
        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        self.assertEqual(set(run.snapshot), {
            'schema_version', 'run_id', 'node_id', 'engine_version', 'plan_name',
            'base_url', 'allowed_methods', 'users', 'spawn_rate', 'duration_seconds',
            'wait_seconds', 'steps',
        })
        self.assertEqual(run.snapshot['run_id'], str(run.pk))
        self.assertEqual(len(run.snapshot_sha256), 64)
        run.node_command = {
            'type': 'prepare', 'run_id': str(run.pk), 'handshake_token': 'private',
            'tls': {'key_pem': 'private-key'},
        }
        run.save(update_fields=('node_command',))
        detail = self.client.get(self._path(f'runs/{run.pk}/'))
        encoded = str(detail.data)
        self.assertNotIn('node_command', encoded)
        self.assertNotIn('private-key', encoded)
        listed = self.client.get(self._path('runs/'))
        self.assertNotIn('snapshot', listed.data['data']['items'][0])
        self.assertNotIn('metrics_samples', listed.data['data']['items'][0])

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_create_is_idempotent_and_rejects_changed_parameters(self, _):
        request_id = uuid.uuid4()
        first = self._create(request_id)
        self.assertEqual(first.status_code, 201, first.data)
        PerformanceControllerState.objects.filter(pk=1).update(
            heartbeat_at=timezone.now() - timedelta(seconds=60),
        )
        repeated = self._create(request_id)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['data']['id'], first.data['data']['id'])
        second_node, _ = self._node('node-two')
        conflict = self._create(request_id, second_node)
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(PerformanceRun.objects.count(), 1)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_create_rechecks_template_security_and_node_version(self, _):
        self.plan.steps[0]['headers'] = {'Host': 'evil.example.test'}
        self.plan.save(update_fields=('steps',))
        rejected = self._create()
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertFalse(PerformanceRun.objects.exists())

        self.plan.steps[0]['headers'] = {}
        self.plan.save(update_fields=('steps',))
        PerformanceNode.objects.filter(pk=self.node.pk).update(agent_version='0.0.0')
        rejected = self._create()
        self.assertEqual(rejected.status_code, 400, rejected.data)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_new_run_rejects_deleted_node_even_with_valid_plan_and_version(self, _):
        PerformanceNode.objects.filter(pk=self.node.pk).update(deleted_at=timezone.now())
        rejected = self._create()
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('性能节点不存在', rejected.data['error']['message'])
        self.assertFalse(PerformanceRun.objects.exists())

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_new_run_requires_fresh_controller(self, _):
        PerformanceControllerState.objects.filter(pk=1).update(
            heartbeat_at=timezone.now() - timedelta(seconds=16),
        )
        rejected = self._create()
        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(rejected.data['error']['code'], 'execution_unavailable')
        self.assertFalse(PerformanceRun.objects.exists())

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_frozen_snapshot_survives_source_changes_and_deletion(self, _):
        run = PerformanceRun.objects.get(pk=self._create().data['data']['id'])
        frozen = run.snapshot
        PerformancePlan.objects.filter(pk=self.plan.pk).update(name='changed plan', users=99)
        PerformanceTarget.objects.filter(pk=self.target.pk).update(
            base_url='https://changed.example.test', allowed_methods=['POST'],
        )
        run.refresh_from_db()
        self.assertEqual(run.snapshot, frozen)

        self.client.force_authenticate(self.admin)
        deleted = self.client.delete(self._path(f'plans/{self.plan.pk}/'))
        self.assertEqual(deleted.status_code, 200, deleted.data)
        deleted = self.client.delete(self._path(f'targets/{self.target.pk}/'))
        self.assertEqual(deleted.status_code, 200, deleted.data)
        run.refresh_from_db()
        self.assertIsNone(run.plan_id)
        self.assertEqual(run.snapshot, frozen)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_execute_and_report_permissions_are_independent(self, _):
        self.client.force_authenticate(self.reader)
        denied = self.client.post(self._path(f'plans/{self.plan.pk}/runs/'), {
            'node_id': str(self.node.pk), 'request_id': str(uuid.uuid4()),
        }, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)

        run = PerformanceRun.objects.create(
            project=self.project, plan=self.plan, node=self.node, created_by=self.admin,
            request_id=uuid.uuid4(), snapshot={}, snapshot_sha256='0' * 64,
        )
        self.assertEqual(self.client.get(self._path('runs/')).status_code, 200)
        self.assertEqual(self.client.get(self._path(f'runs/{run.pk}/')).status_code, 403)
        self.assertEqual(self.client.post(self._path(f'runs/{run.pk}/stop/'), {}, format='json').status_code, 403)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_execute_without_report_never_receives_detail_fields(self, _):
        request_id = uuid.uuid4()
        payload = {'node_id': str(self.node.pk), 'request_id': str(request_id)}
        self.client.force_authenticate(self.execute_only)
        created = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), payload, format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        for private_field in ('snapshot', 'metrics_samples', 'node_command'):
            self.assertNotIn(private_field, created.data['data'])

        repeated = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), payload, format='json',
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        for private_field in ('snapshot', 'metrics_samples', 'node_command'):
            self.assertNotIn(private_field, repeated.data['data'])

        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        run.status = PerformanceRun.Status.RUNNING
        run.node_command = {'type': 'prepare', 'run_id': str(run.pk), 'secret': 'private'}
        run.metrics_samples = [{'timestamp': timezone.now().isoformat(), 'metrics': {'requests': 1}}]
        run.save(update_fields=('status', 'node_command', 'metrics_samples'))
        stopped = self.client.post(self._path(f'runs/{run.pk}/stop/'), {}, format='json')
        self.assertEqual(stopped.status_code, 200, stopped.data)
        self.assertEqual(stopped.data['data']['status'], PerformanceRun.Status.STOPPING)
        for private_field in ('snapshot', 'metrics_samples', 'node_command'):
            self.assertNotIn(private_field, stopped.data['data'])
        self.assertEqual(self.client.get(self._path(f'runs/{run.pk}/')).status_code, 403)

    @patch('performance_testing.agent_views.execution_configuration', return_value=AVAILABLE)
    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_stop_keeps_prepare_until_controller_replaces_it_and_reports_are_monotonic(self, _, __):
        created = self._create()
        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        prepare = {
            'type': 'prepare', 'run_id': str(run.pk), 'snapshot': run.snapshot,
            'snapshot_sha256': run.snapshot_sha256, 'handshake_token': 'x' * 32,
        }
        run.status = PerformanceRun.Status.RUNNING
        run.started_at = timezone.now()
        run.node_command = prepare
        run.save(update_fields=('status', 'started_at', 'node_command'))
        self._fresh_controller(run)

        self.client.force_authenticate(self.executor)
        stopped = self.client.post(self._path(f'runs/{run.pk}/stop/'), {}, format='json')
        self.assertEqual(stopped.data['data']['status'], PerformanceRun.Status.STOPPING)
        run.refresh_from_db()
        self.assertEqual(run.node_command, prepare)

        self.client.force_authenticate(None)
        report = {
            'run_id': str(run.pk), 'sequence': 1, 'state': 'running',
            'reason_code': '', 'reason': '',
        }
        heartbeat = self._heartbeat(report)
        self.assertEqual(heartbeat.status_code, 200, heartbeat.data)
        self.assertEqual(heartbeat.data['data']['command'], prepare)
        run.refresh_from_db()
        self.assertEqual(run.status, PerformanceRun.Status.STOPPING)
        self.assertEqual(run.node_report_seq, 1)

        duplicate = self._heartbeat(report)
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        PerformanceControllerState.objects.filter(pk=1).update(
            heartbeat_at=timezone.now() - timedelta(seconds=60),
        )
        report['sequence'] = 2
        stale = self._heartbeat(report)
        self.assertEqual(stale.data['data']['command']['type'], 'stop')

        self._fresh_controller(run)
        PerformanceRun.objects.filter(pk=run.pk).update(
            status=PerformanceRun.Status.COMPLETED, finished_at=timezone.now(),
        )
        report['sequence'] = 3
        terminal = self._heartbeat(report)
        self.assertEqual(terminal.data['data']['command']['type'], 'stop')
        run.refresh_from_db()
        self.assertEqual(run.status, PerformanceRun.Status.COMPLETED)

    def _heartbeat(self, report):
        return self.client.post('/api/v1/performance-agent/heartbeat/', {
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
            'resources': {'cpu_percent': 1.0, 'memory_percent': 2.0},
            'run_report': report,
        }, format='json', HTTP_AUTHORIZATION=f'Node {self.agent_token}')

    @patch('performance_testing.agent_views.execution_configuration', return_value=AVAILABLE)
    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_node_never_receives_another_nodes_private_command(self, _, __):
        run = PerformanceRun.objects.get(pk=self._create().data['data']['id'])
        run.status = PerformanceRun.Status.PREPARING
        run.node_command = {
            'type': 'prepare', 'run_id': str(run.pk), 'snapshot': run.snapshot,
            'snapshot_sha256': run.snapshot_sha256, 'handshake_token': 'secret',
        }
        run.save(update_fields=('status', 'node_command'))
        other_node, other_token = self._node('isolated-node')
        self.client.force_authenticate(None)
        response = self.client.post('/api/v1/performance-agent/heartbeat/', {
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
            'resources': {'cpu_percent': 1.0, 'memory_percent': 2.0},
            'run_report': None,
        }, format='json', HTTP_AUTHORIZATION=f'Node {other_token}')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['command'], {'type': 'idle'})
        self.assertNotIn('secret', str(response.data))

    @patch('performance_testing.agent_views.execution_configuration', return_value=AVAILABLE)
    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_unknown_or_other_node_report_is_rejected_without_heartbeat_write(self, _, __):
        before = self.node.last_seen_at
        unknown = self._heartbeat({
            'run_id': str(uuid.uuid4()), 'sequence': 1, 'state': 'failed',
            'reason_code': 'unknown', 'reason': 'unknown',
        })
        self.assertEqual(unknown.status_code, 409, unknown.data)
        self.node.refresh_from_db()
        self.assertEqual(self.node.last_seen_at, before)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_revoke_requires_strict_confirmation_and_has_no_preconfirmation_mutation(self, _):
        run = PerformanceRun.objects.get(pk=self._create().data['data']['id'])
        run.status = PerformanceRun.Status.RUNNING
        run.save(update_fields=('status',))
        original_agent_digest = self.node.agent_token_digest
        original_last_seen_at = self.node.last_seen_at
        self.client.force_authenticate(self.admin)
        rejected = self.client.post(
            self._path(f'nodes/{self.node.pk}/revoke/'), {}, format='json',
        )
        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(rejected.data['error']['code'], 'node_has_active_runs')
        self.assertEqual(rejected.data['error']['count'], 1)
        self.node.refresh_from_db()
        run.refresh_from_db()
        self.assertIsNone(self.node.revoked_at)
        self.assertEqual(self.node.agent_token_digest, original_agent_digest)
        self.assertEqual(self.node.last_seen_at, original_last_seen_at)
        self.assertEqual(run.status, PerformanceRun.Status.RUNNING)
        self.assertEqual(run.reason_code, '')
        self.assertEqual(run.node_command, {})
        self.assertIsNone(run.stop_requested_at)

        for payload in (
            {'confirm_stop': 'true'},
            {'confirm_stop': 1},
            {'confirm_stop': True, 'unexpected': False},
        ):
            with self.subTest(payload=payload):
                invalid = self.client.post(
                    self._path(f'nodes/{self.node.pk}/revoke/'), payload, format='json',
                )
                self.assertEqual(invalid.status_code, 400, invalid.data)
        self.node.refresh_from_db()
        run.refresh_from_db()
        self.assertIsNone(self.node.revoked_at)
        self.assertEqual(run.status, PerformanceRun.Status.RUNNING)

        revoked = self.client.post(
            self._path(f'nodes/{self.node.pk}/revoke/'),
            {'confirm_stop': True}, format='json',
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(revoked.data['data']['active_run_count'], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, PerformanceRun.Status.STOPPING)
        self.assertEqual(run.reason_code, 'node_revoked')
        self.assertEqual(run.node_command['type'], 'stop')
        self.assertIsNotNone(run.stop_requested_at)
        self.assertIsNone(run.finished_at)

        queued_node, _ = self._node('node-queued')
        queued = PerformanceRun.objects.create(
            project=self.project, plan=self.plan, node=queued_node, created_by=self.admin,
            request_id=uuid.uuid4(), snapshot={}, snapshot_sha256='0' * 64,
        )
        revoked = self.client.post(
            self._path(f'nodes/{queued_node.pk}/revoke/'),
            {'confirm_stop': True}, format='json',
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(revoked.data['data']['active_run_count'], 0)
        queued.refresh_from_db()
        self.assertEqual(queued.status, PerformanceRun.Status.INCOMPLETE)
        self.assertIsNotNone(queued.finished_at)

    def test_active_run_restricts_project_delete_but_terminal_run_does_not(self):
        run = PerformanceRun.objects.create(
            project=self.project, plan=self.plan, node=self.node, created_by=self.admin,
            request_id=uuid.uuid4(), snapshot={}, snapshot_sha256='0' * 64,
        )
        with self.assertRaises(RestrictedError), transaction.atomic():
            self.project.delete()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        run.status = PerformanceRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=('status', 'finished_at'))
        self.project.delete()
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())

    def test_project_delete_api_rejects_active_run_before_cleanup(self):
        PerformanceRun.objects.create(
            project=self.project, plan=self.plan, node=self.node, created_by=self.admin,
            request_id=uuid.uuid4(), status=PerformanceRun.Status.RUNNING,
            snapshot={}, snapshot_sha256='0' * 64,
        )
        self.client.force_authenticate(self.admin)
        with patch.object(ProjectViewSet, 'perform_destroy') as cleanup:
            result = self.client.delete(f'/api/v1/projects/{self.project.pk}/')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(result.data['error']['code'], 'performance_run_active')
        cleanup.assert_not_called()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_project_delete_api_allows_terminal_performance_run(self):
        PerformanceRun.objects.create(
            project=self.project, plan=self.plan, node=self.node, created_by=self.admin,
            request_id=uuid.uuid4(), status=PerformanceRun.Status.COMPLETED,
            snapshot={}, snapshot_sha256='0' * 64, finished_at=timezone.now(),
        )
        project_id = self.project.pk
        self.client.force_authenticate(self.admin)
        result = self.client.delete(f'/api/v1/projects/{project_id}/')
        self.assertEqual(result.status_code, 204, result.data)
        self.assertFalse(Project.objects.filter(pk=project_id).exists())

    def test_project_delete_api_keeps_non_performance_behavior(self):
        ordinary = Project.objects.create(
            name='ordinary-api-project', project_type='api', created_by=self.admin,
        )
        project_id = ordinary.pk
        self.client.force_authenticate(self.admin)
        result = self.client.delete(f'/api/v1/projects/{project_id}/')
        self.assertEqual(result.status_code, 204, result.data)
        self.assertFalse(Project.objects.filter(pk=project_id).exists())
