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
    PerformanceRunNode, PerformanceTarget,
)
from performance_testing.services import consume_enrollment, issue_enrollment
from projects.models import Project, ProjectMember
from projects.views import ProjectViewSet
from users.models import User


AVAILABLE = {'enabled': True, 'available': True, 'reason': ''}


class PerformanceRunContractTests(TestCase):
    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_validation_details_require_report_permission_and_are_absent_from_lists(self, _):
        created = self._create()
        self.assertEqual(created.status_code, 201, created.data)
        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        run.latest_metrics = {'requests': 1, 'validation_steps': [{
            'step_index': 1, 'step_name': 'fixture', 'phase': 'main', 'method': 'GET',
            'status': 'failed', 'response': {'status_code': 200,
                'body': {'content': '{"data":[],"token":"fixture-detail-token"}', 'truncated': False}},
        }]}
        run.save(update_fields=['latest_metrics'])
        participant = run.participants.get()
        participant.latest_metrics = {
            'requests': 1,
            'validation_steps': [{'response': {'body': 'participant-private-token'}}],
        }
        participant.save(update_fields=['latest_metrics'])
        detail_path = self._path(f'runs/{run.pk}/')
        self.client.force_authenticate(self.reader)
        listing = self.client.get(self._path('runs/'))
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn('validation_steps', listing.data['data']['items'][0]['latest_metrics'])
        self.assertNotIn('fixture-detail-token', str(listing.data))
        self.assertNotIn('participant-private-token', str(listing.data))
        self.assertEqual(self.client.get(detail_path).status_code, 403)
        self.client.force_authenticate(self.executor)
        detail = self.client.get(detail_path)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['data']['validation_steps'][0]['status'], 'failed')
        self.assertIn('fixture-detail-token', str(detail.data['data']['validation_steps']))
        run.latest_metrics = {'requests': 1}
        run.save(update_fields=['latest_metrics'])
        self.assertEqual(self.client.get(detail_path).data['data']['validation_steps'], [])

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
            connect_timeout_seconds=14, read_timeout_seconds=47,
            variables={}, unique_variables=[],
            steps=[{
                'name': 'list', 'phase': 'main', 'method': 'GET', 'path': '/items',
                'query': {'limit': 1}, 'headers': {'Accept': 'application/json'},
                'body_type': 'none', 'body': None, 'extract': [],
                'assertions': [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}],
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

    def _create(self, request_id=None, node=None, mode='validation'):
        self.client.force_authenticate(self.executor)
        return self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'),
            {'node_ids': [str((node or self.node).pk)],
             'request_id': str(request_id or uuid.uuid4()), 'mode': mode},
            format='json',
        )

    def _direct_run(self, *, node=None, **kwargs):
        selected = node or self.node
        defaults = {
            'project': self.project,
            'plan': self.plan,
            'created_by': self.admin,
            'request_id': uuid.uuid4(),
            'snapshot': {},
            'snapshot_sha256': '0' * 64,
        }
        defaults.update(kwargs)
        run = PerformanceRun.objects.create(**defaults)
        PerformanceRunNode.objects.create(
            run=run, node=selected, node_name=selected.name,
            node_agent_version=selected.agent_version,
            node_protocol_version=selected.protocol_version,
            node_engine_version=selected.engine_version,
        )
        return run

    def _complete_validation(self, node):
        response = self._create(node=node, mode='validation')
        self.assertEqual(response.status_code, 201, response.data)
        run = PerformanceRun.objects.get(pk=response.data['data']['id'])
        run.status = PerformanceRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.latest_metrics = {
            'complete': True, 'requests': 1, 'failures': 0,
            'validation_complete': True, 'validation_passed': True,
            'main_steps_completed': 1, 'main_steps_total': 1,
        }
        run.save(update_fields=('status', 'finished_at', 'latest_metrics'))
        return run

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_new_enrollment_generation_invalidates_old_same_version_validation(self, _):
        validation = self._complete_validation(self.node)
        self.client.force_authenticate(self.executor)
        path = self._path(f'plans/{self.plan.pk}/node-eligibility/')
        before = self.client.get(path)
        self.assertEqual(before.status_code, 200, before.data)
        self.assertTrue(before.data['data']['items'][0]['validation_valid'])
        validation_before = self.client.get(self._path(f'runs/{validation.pk}/'))
        self.assertEqual(validation_before.data['data']['validation_status'], 'passed')

        PerformanceNode.objects.filter(pk=self.node.pk).update(
            enrollment_consumed_at=timezone.now(),
        )
        self.node.refresh_from_db()
        after = self.client.get(path)
        self.assertEqual(after.status_code, 200, after.data)
        self.assertFalse(after.data['data']['items'][0]['validation_valid'])
        self.assertEqual(after.data['data']['items'][0]['reason_code'], 'validation_required')
        validation_after = self.client.get(self._path(f'runs/{validation.pk}/'))
        self.assertEqual(validation_after.data['data']['validation_status'], 'stale')
        listed = self.client.get(self._path('runs/'))
        listed_validation = next(
            item for item in listed.data['data']['items']
            if item['id'] == str(validation.pk)
        )
        self.assertEqual(listed_validation['validation_status'], 'stale')

        replacement = self._complete_validation(self.node)
        restored = self.client.get(path)
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertTrue(restored.data['data']['items'][0]['validation_valid'])
        self.assertEqual(
            restored.data['data']['items'][0]['validation_run_id'], str(replacement.pk),
        )

        # Reinstallation does not delete the historical validation report.
        historical = self.client.get(self._path(f'runs/{validation.pk}/'))
        self.assertEqual(historical.status_code, 200, historical.data)
        self.assertEqual(historical.data['data']['id'], str(validation.pk))
        self.assertEqual(historical.data['data']['validation_status'], 'stale')
        replacement_detail = self.client.get(self._path(f'runs/{replacement.pk}/'))
        self.assertEqual(replacement_detail.data['data']['validation_status'], 'passed')

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_multi_node_distribution_eligibility_idempotency_and_serialization(self, _):
        second, _ = self._node('node-two')
        third, _ = self._node('node-three')
        nodes = sorted((self.node, second, third), key=lambda item: str(item.pk))
        self.plan.users = 1000
        self.plan.save(update_fields=('users',))
        validations = {node.pk: self._complete_validation(node) for node in nodes}

        self.client.force_authenticate(self.executor)
        eligibility = self.client.get(
            self._path(f'plans/{self.plan.pk}/node-eligibility/'),
        )
        self.assertEqual(eligibility.status_code, 200, eligibility.data)
        by_node = {item['node_id']: item for item in eligibility.data['data']['items']}
        for node in nodes:
            item = by_node[str(node.pk)]
            self.assertTrue(item['compatible'])
            self.assertTrue(item['validation_valid'])
            self.assertEqual(item['validation_run_id'], str(validations[node.pk].pk))
            self.assertEqual((item['reason_code'], item['reason']), ('', ''))

        request_id = uuid.uuid4()
        payload = {
            'node_ids': [str(node.pk) for node in reversed(nodes)],
            'request_id': str(request_id), 'mode': 'load',
        }
        created = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), payload, format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        participants = list(run.participants.order_by('node_id'))
        self.assertEqual([item.assigned_users for item in participants], [334, 333, 333])
        self.assertEqual([item.node_id for item in participants], [node.pk for node in nodes])
        self.assertEqual([item.validation_run_id for item in participants], [
            validations[node.pk].pk for node in nodes
        ])
        self.assertEqual(run.snapshot['schema_version'], 4)
        self.assertNotIn('node_id', run.snapshot)
        self.assertNotIn('validation_key', run.snapshot)
        self.assertEqual(
            [set(item) for item in run.snapshot['nodes']],
            [{'node_id', 'users', 'validation_key'}] * 3,
        )
        self.assertEqual(created.data['data']['node_count'], 3)
        self.assertEqual([item['assigned_users'] for item in created.data['data']['nodes']], [334, 333, 333])

        repeated = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), {
                **payload, 'node_ids': [str(node.pk) for node in nodes],
            }, format='json',
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['data']['id'], str(run.pk))
        changed = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), {
                **payload, 'node_ids': [str(nodes[0].pk), str(nodes[1].pk)],
            }, format='json',
        )
        self.assertEqual(changed.status_code, 409, changed.data)

        duplicate = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'), {
                'node_ids': [str(nodes[0].pk), str(nodes[0].pk)],
                'request_id': str(uuid.uuid4()), 'mode': 'load',
            }, format='json',
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_config_and_create_freeze_exact_snapshot_and_never_serialize_private_command(self, _):
        self.client.force_authenticate(self.executor)
        config = self.client.get(self._path('config/'))
        self.assertTrue(config.data['data']['controller_online'])
        self.assertTrue(config.data['data']['execution_enabled'])
        self.assertEqual(config.data['data']['max_nodes_per_run'], 5)

        created = self._create()
        self.assertEqual(created.status_code, 201, created.data)
        run = PerformanceRun.objects.get(pk=created.data['data']['id'])
        self.assertEqual(set(run.snapshot), {
            'schema_version', 'run_id', 'nodes', 'engine_version', 'plan_name',
            'base_url', 'allowed_methods', 'mode', 'users',
            'spawn_rate', 'duration_seconds', 'wait_seconds',
            'connect_timeout_seconds', 'read_timeout_seconds', 'variables',
            'unique_variables', 'steps',
        })
        self.assertEqual(run.snapshot['schema_version'], 4)
        self.assertEqual(run.snapshot['nodes'][0]['node_id'], str(self.node.pk))
        self.assertEqual((run.snapshot['users'], run.snapshot['spawn_rate']), (1, 1))
        self.assertEqual(run.snapshot['duration_seconds'], 120)
        self.assertEqual(run.snapshot['connect_timeout_seconds'], 14)
        self.assertEqual(run.snapshot['read_timeout_seconds'], 47)
        self.assertEqual(created.data['data']['validation_status'], 'pending')
        self.assertEqual(run.snapshot['run_id'], str(run.pk))
        self.assertEqual(len(run.snapshot_sha256), 64)
        participant = run.participants.get()
        participant.node_command = {
            'type': 'prepare', 'run_id': str(run.pk), 'handshake_token': 'private',
            'tls': {'key_pem': 'private-key'},
        }
        participant.save(update_fields=('node_command',))
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

        mode_conflict = self._create(request_id, mode='load')
        self.assertEqual(mode_conflict.status_code, 409, mode_conflict.data)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_load_requires_matching_successful_real_validation_and_edits_make_it_stale(self, _):
        self.client.force_authenticate(self.executor)
        no_validation = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'),
            {'node_ids': [str(self.node.pk)], 'request_id': str(uuid.uuid4())},
            format='json',
        )
        self.assertEqual(no_validation.status_code, 400, no_validation.data)
        self.assertEqual(no_validation.data['error']['code'], 'run_validation_failed')

        invalid_mode = self.client.post(
            self._path(f'plans/{self.plan.pk}/runs/'),
            {'node_ids': [str(self.node.pk)], 'request_id': str(uuid.uuid4()), 'mode': 'smoke'},
            format='json',
        )
        self.assertEqual(invalid_mode.status_code, 400, invalid_mode.data)

        validation_response = self._create(mode='validation')
        validation = PerformanceRun.objects.get(pk=validation_response.data['data']['id'])
        validation.status = PerformanceRun.Status.COMPLETED
        validation.finished_at = timezone.now()
        validation.latest_metrics = {
            'complete': True, 'requests': 1, 'failures': 0,
            'validation_complete': True, 'validation_passed': True,
            'main_steps_completed': 1, 'main_steps_total': 1,
        }
        validation.save(update_fields=('status', 'finished_at', 'latest_metrics'))
        passed = self.client.get(self._path(f'runs/{validation.pk}/'))
        self.assertEqual(passed.data['data']['validation_status'], 'passed')

        allowed = self._create(mode='load')
        self.assertEqual(allowed.status_code, 201, allowed.data)
        load = PerformanceRun.objects.get(pk=allowed.data['data']['id'])
        self.assertEqual((load.snapshot['users'], load.snapshot['spawn_rate']), (2, 1.5))
        self.assertEqual(load.snapshot['duration_seconds'], 5)
        self.assertEqual(load.snapshot['connect_timeout_seconds'], 14)
        self.assertEqual(load.snapshot['read_timeout_seconds'], 47)
        self.assertEqual(allowed.data['data']['validation_status'], 'not_applicable')

        self.plan.variables = {'changed': True}
        self.plan.save(update_fields=('variables',))
        stale = self.client.get(self._path(f'runs/{validation.pk}/'))
        self.assertEqual(stale.data['data']['validation_status'], 'stale')
        rejected = self._create(mode='load')
        self.assertEqual(rejected.status_code, 400, rejected.data)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_changing_request_timeout_invalidates_previous_validation(self, _):
        validation = self._complete_validation(self.node)
        self.client.force_authenticate(self.executor)
        detail_path = self._path(f'runs/{validation.pk}/')
        self.assertEqual(self.client.get(detail_path).data['data']['validation_status'], 'passed')

        self.plan.connect_timeout_seconds = 15
        self.plan.save(update_fields=('connect_timeout_seconds',))
        self.assertEqual(self.client.get(detail_path).data['data']['validation_status'], 'stale')
        rejected = self._create(mode='load')
        self.assertEqual(rejected.status_code, 400, rejected.data)

    def test_historical_schema3_run_detail_remains_readable(self):
        historical = self._direct_run(
            status=PerformanceRun.Status.COMPLETED,
            finished_at=timezone.now(),
            snapshot={'schema_version': 3, 'users': 1, 'steps': []},
            latest_metrics={'complete': True, 'requests': 1, 'failures': 0},
        )
        self.client.force_authenticate(self.executor)
        detail = self.client.get(self._path(f'runs/{historical.pk}/'))
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['id'], str(historical.pk))
        self.assertEqual(detail.data['data']['status'], PerformanceRun.Status.COMPLETED)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_validation_must_include_real_requests_and_all_main_steps(self, _):
        validation = PerformanceRun.objects.get(pk=self._create().data['data']['id'])
        validation.status = PerformanceRun.Status.COMPLETED
        validation.latest_metrics = {
            'complete': True, 'requests': 0, 'validation_complete': True,
            'validation_passed': True, 'main_steps_completed': 1, 'main_steps_total': 1,
        }
        validation.save(update_fields=('status', 'latest_metrics'))
        self.assertEqual(self._create(mode='load').status_code, 400)
        validation.latest_metrics['requests'] = 1
        validation.latest_metrics['main_steps_completed'] = 0
        validation.save(update_fields=('latest_metrics',))
        self.assertEqual(self._create(mode='load').status_code, 400)

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
    def test_agent_020_heartbeat_compatibility_does_not_grant_schema2_execution(self, _):
        PerformanceNode.objects.filter(pk=self.node.pk).update(agent_version='0.2.0')
        created = self._create()
        self.assertEqual(created.status_code, 400, created.data)
        self.assertIn(AGENT_VERSION, created.data['error']['message'])

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_agent_031_must_upgrade_before_receiving_the_new_fixed_template(self, _):
        PerformanceNode.objects.filter(pk=self.node.pk).update(agent_version='0.3.1')
        created = self._create()
        self.assertEqual(created.status_code, 400, created.data)
        self.assertIn(AGENT_VERSION, created.data['error']['message'])

    @patch('performance_testing.agent_views.execution_configuration', return_value=AVAILABLE)
    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_legacy_heartbeat_remains_visible_but_cannot_keep_protocol3_command(self, _, __):
        run = PerformanceRun.objects.get(pk=self._create().data['data']['id'])
        run.status = PerformanceRun.Status.RUNNING
        run.save(update_fields=('status',))
        participant = run.participants.get()
        participant.node_command = {
            'type': 'prepare', 'run_id': str(run.pk), 'snapshot': run.snapshot,
            'snapshot_sha256': run.snapshot_sha256,
        }
        participant.save(update_fields=('node_command',))
        self._fresh_controller(run)

        self.client.force_authenticate(None)
        response = self.client.post('/api/v1/performance-agent/heartbeat/', {
            'protocol_version': 2,
            'agent_version': '0.3.2',
            'engine_version': ENGINE_VERSION,
            'resources': {'cpu_percent': 1.0, 'memory_percent': 2.0},
            'run_report': None,
        }, format='json', HTTP_AUTHORIZATION=f'Node {self.agent_token}')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['command']['type'], 'stop')
        self.node.refresh_from_db()
        run.refresh_from_db()
        participant.refresh_from_db()
        self.assertEqual(self.node.protocol_version, 2)
        self.assertEqual(self.node.agent_version, '0.3.2')
        self.assertEqual(run.status, PerformanceRun.Status.STOPPING)
        self.assertEqual(run.reason_code, 'node_identity_changed')
        self.assertEqual(participant.status, PerformanceRunNode.Status.LOST)
        self.assertEqual(participant.node_command['type'], 'stop')

        eligibility = self.client.get(
            self._path(f'plans/{self.plan.pk}/node-eligibility/'),
            HTTP_AUTHORIZATION='',
        )
        self.assertEqual(eligibility.status_code, 401)
        self.client.force_authenticate(self.executor)
        eligibility = self.client.get(self._path(f'plans/{self.plan.pk}/node-eligibility/'))
        item = eligibility.data['data']['items'][0]
        self.assertFalse(item['compatible'])
        self.assertEqual(item['reason_code'], 'version_mismatch')

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_new_run_rejects_deleted_node_even_with_valid_plan_and_version(self, _):
        PerformanceNode.objects.filter(pk=self.node.pk).update(deleted_at=timezone.now())
        rejected = self._create()
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('已删除', rejected.data['error']['message'])
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
            'node_ids': [str(self.node.pk)], 'request_id': str(uuid.uuid4()),
        }, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)

        run = self._direct_run()
        self.assertEqual(self.client.get(self._path('runs/')).status_code, 200)
        self.assertEqual(self.client.get(self._path(f'runs/{run.pk}/')).status_code, 403)
        self.assertEqual(self.client.post(self._path(f'runs/{run.pk}/stop/'), {}, format='json').status_code, 403)

    @patch('performance_testing.run_services.execution_configuration', return_value=AVAILABLE)
    def test_execute_without_report_never_receives_detail_fields(self, _):
        request_id = uuid.uuid4()
        payload = {'node_ids': [str(self.node.pk)], 'request_id': str(request_id),
                   'mode': 'validation'}
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
        participant = run.participants.get()
        participant.node_command = {'type': 'prepare', 'run_id': str(run.pk), 'secret': 'private'}
        run.metrics_samples = [{'timestamp': timezone.now().isoformat(), 'metrics': {'requests': 1}}]
        run.save(update_fields=('status', 'metrics_samples'))
        participant.save(update_fields=('node_command',))
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
        participant = run.participants.get()
        participant.node_command = prepare
        run.save(update_fields=('status', 'started_at'))
        participant.save(update_fields=('node_command',))
        self._fresh_controller(run)

        self.client.force_authenticate(self.executor)
        stopped = self.client.post(self._path(f'runs/{run.pk}/stop/'), {}, format='json')
        self.assertEqual(stopped.data['data']['status'], PerformanceRun.Status.STOPPING)
        run.refresh_from_db()
        participant.refresh_from_db()
        self.assertEqual(participant.node_command, prepare)

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
        participant.refresh_from_db()
        self.assertEqual(participant.node_report_seq, 1)

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
        participant = run.participants.get()
        participant.node_command = {
            'type': 'prepare', 'run_id': str(run.pk), 'snapshot': run.snapshot,
            'snapshot_sha256': run.snapshot_sha256, 'handshake_token': 'secret',
        }
        run.save(update_fields=('status',))
        participant.save(update_fields=('node_command',))
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
        self.assertEqual(run.participants.get().node_command, {})
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
        self.assertEqual(run.participants.get().node_command['type'], 'stop')
        self.assertIsNotNone(run.stop_requested_at)
        self.assertIsNone(run.finished_at)

        queued_node, _ = self._node('node-queued')
        queued = self._direct_run(node=queued_node)
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
        run = self._direct_run()
        with self.assertRaises(RestrictedError), transaction.atomic():
            self.project.delete()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        run.status = PerformanceRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=('status', 'finished_at'))
        self.project.delete()
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())

    def test_project_delete_api_rejects_active_run_before_cleanup(self):
        self._direct_run(status=PerformanceRun.Status.RUNNING)
        self.client.force_authenticate(self.admin)
        with patch.object(ProjectViewSet, 'perform_destroy') as cleanup:
            result = self.client.delete(f'/api/v1/projects/{self.project.pk}/')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(result.data['error']['code'], 'performance_run_active')
        cleanup.assert_not_called()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_project_delete_api_allows_terminal_performance_run(self):
        self._direct_run(
            status=PerformanceRun.Status.COMPLETED, finished_at=timezone.now(),
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
