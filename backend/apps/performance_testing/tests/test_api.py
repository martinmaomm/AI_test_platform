from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
from performance_testing.models import PerformanceNode, PerformancePlan, PerformanceTarget
from projects.models import Project, ProjectMember
from users.models import User


class PerformanceManagementAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='perf-admin', email='perf-admin@example.test', is_staff=True,
        )
        self.editor = User.objects.create_user(
            username='perf-editor', email='perf-editor@example.test',
        )
        self.viewer = User.objects.create_user(
            username='perf-viewer', email='perf-viewer@example.test',
        )
        self.outsider = User.objects.create_user(
            username='perf-outsider', email='perf-outsider@example.test',
        )
        self.project = Project.objects.create(
            name='performance', project_type='perf', created_by=self.admin,
        )
        self.other_project = Project.objects.create(
            name='other-performance', project_type='perf', created_by=self.admin,
        )
        self.api_project = Project.objects.create(
            name='api', project_type='api', created_by=self.admin,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.editor, role='editor',
            can_edit=True, can_delete=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.viewer, role='viewer',
            can_edit=False, can_delete=False,
        )

    def path(self, suffix=''):
        return f'/api/v1/projects/{self.project.pk}/performance/{suffix}'

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def create_target(self, project=None, methods=None):
        return PerformanceTarget.objects.create(
            project=project or self.project,
            name='approved',
            base_url='https://target.example.test',
            allowed_methods=methods or ['GET'],
        )

    def valid_plan(self, target):
        return {
            'name': 'smoke',
            'description': '',
            'target_id': target.pk,
            'users': 2,
            'spawn_rate': 1,
            'duration_seconds': 30,
            'wait_seconds': 1,
            'steps': [{
                'name': 'list', 'method': 'GET', 'path': '/items',
                'expected_status': 200, 'headers': {}, 'body': None,
            }],
        }

    def test_config_requires_membership_and_perf_project(self):
        self.auth(self.editor)
        response = self.client.get(self.path('config/'))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['phase'], 'execution')
        self.assertFalse(response.data['data']['execution_enabled'])

        self.auth(self.outsider)
        self.assertEqual(self.client.get(self.path('config/')).status_code, 404)
        self.auth(self.admin)
        wrong_type = f'/api/v1/projects/{self.api_project.pk}/performance/config/'
        self.assertEqual(self.client.get(wrong_type).status_code, 404)

    def test_only_admin_manages_targets_but_members_can_read(self):
        self.auth(self.editor)
        denied = self.client.post(self.path('targets/'), {
            'name': 'target', 'base_url': 'https://example.test',
        }, format='json')
        self.assertEqual(denied.status_code, 403)

        self.auth(self.admin)
        created = self.client.post(self.path('targets/'), {
            'name': 'target', 'base_url': 'https://example.test/',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['allowed_methods'], ['GET'])

        self.auth(self.viewer)
        listed = self.client.get(self.path('targets/'))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data['data']['items']), 1)

    def test_target_origin_validation_and_plan_protected_delete(self):
        self.auth(self.admin)
        for invalid in (
            'ftp://example.test', 'https://user@example.test',
            'https://example.test/api', 'https://example.test?x=1',
            'https://example.test/#fragment', 'https://example.test?',
            'https://example.test#', 'https://exa mple.test',
            'https://example.test:', 'https://example.test:99999',
        ):
            response = self.client.post(self.path('targets/'), {
                'name': 'bad', 'base_url': invalid,
            }, format='json')
            self.assertEqual(response.status_code, 400, (invalid, response.data))

        target = self.create_target()
        PerformancePlan.objects.create(
            project=self.project, target=target, name='linked',
            steps=[{'name': 'get', 'method': 'GET', 'path': '/',
                    'expected_status': 200, 'headers': {}, 'body': None}],
        )
        response = self.client.delete(self.path(f'targets/{target.pk}/'))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(PerformanceTarget.objects.filter(pk=target.pk).exists())

    def test_target_cannot_remove_a_method_still_used_by_a_plan(self):
        self.auth(self.admin)
        target = self.create_target(methods=['GET', 'POST'])
        PerformancePlan.objects.create(
            project=self.project, target=target, name='posting',
            steps=[{'name': 'post', 'method': 'POST', 'path': '/',
                    'expected_status': 200, 'headers': {}, 'body': None}],
        )
        response = self.client.patch(
            self.path(f'targets/{target.pk}/'), {'allowed_methods': ['GET']}, format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        target.refresh_from_db()
        self.assertEqual(target.allowed_methods, ['GET', 'POST'])

    def test_project_delete_cascades_its_targets_and_plans_together(self):
        target = self.create_target()
        plan = PerformancePlan.objects.create(
            project=self.project, target=target, name='project-owned',
            steps=[{'name': 'get', 'method': 'GET', 'path': '/',
                    'expected_status': 200, 'headers': {}, 'body': None}],
        )
        project_id, target_id, plan_id = self.project.pk, target.pk, plan.pk
        self.project.delete()
        self.assertFalse(Project.objects.filter(pk=project_id).exists())
        self.assertFalse(PerformanceTarget.objects.filter(pk=target_id).exists())
        self.assertFalse(PerformancePlan.objects.filter(pk=plan_id).exists())

    def test_plan_permissions_defaults_and_server_side_contract(self):
        target = self.create_target(methods=['GET', 'POST'])
        self.auth(self.editor)
        payload = self.valid_plan(target)
        payload.pop('users')
        payload.pop('spawn_rate')
        payload.pop('duration_seconds')
        payload.pop('wait_seconds')
        created = self.client.post(self.path('plans/'), payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['users'], 1)
        self.assertEqual(created.data['data']['duration_seconds'], 30)

        self.auth(self.viewer)
        self.assertEqual(self.client.get(self.path('plans/')).status_code, 200)
        self.assertEqual(
            self.client.post(self.path('plans/'), self.valid_plan(target), format='json').status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(self.path(f"plans/{created.data['data']['id']}/")).status_code,
            403,
        )

    def test_plan_rejects_cross_project_method_path_limits_and_unknown_keys(self):
        own_target = self.create_target(methods=['GET'])
        other_target = self.create_target(project=self.other_project)
        self.auth(self.editor)

        cross = self.valid_plan(other_target)
        self.assertEqual(self.client.post(self.path('plans/'), cross, format='json').status_code, 400)

        for mutation in (
            lambda item: item['steps'][0].update(method='POST'),
            lambda item: item['steps'][0].update(path='//evil.example/x'),
            lambda item: item['steps'][0].update(path='https://evil.example/x'),
            lambda item: item.update(users=101),
            lambda item: item.update(users=True),
            lambda item: item.update(users='2'),
            lambda item: item.update(target_id=True),
            lambda item: item['steps'][0].update(python='print(1)'),
        ):
            payload = self.valid_plan(own_target)
            mutation(payload)
            response = self.client.post(self.path('plans/'), payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_nodes_are_admin_managed_member_read_only_and_never_leak_digests(self):
        self.auth(self.editor)
        self.assertEqual(self.client.post(self.path('nodes/'), {
            'name': 'node', 'network_mode': 'lan', 'labels': {},
        }, format='json').status_code, 403)

        self.auth(self.admin)
        created = self.client.post(self.path('nodes/'), {
            'name': 'node', 'network_mode': 'lan', 'labels': {'region': 'local'},
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIn('enrollment_token', created.data['data'])
        node_payload = created.data['data']['node']
        self.assertEqual(node_payload['status'], 'pending')
        self.assertFalse(any('token' in key or 'digest' in key for key in node_payload))

        self.auth(self.viewer)
        listed = self.client.get(self.path('nodes/'))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertFalse(any(
            'token' in key or 'digest' in key for key in listed.data['data']['items'][0]
        ))

    def test_node_status_is_derived_from_server_time(self):
        node = PerformanceNode.objects.create(
            project=self.project, name='old', network_mode='public',
            last_seen_at=timezone.now() - timedelta(seconds=31),
        )
        self.auth(self.viewer)
        response = self.client.get(self.path(f'nodes/{node.pk}/'))
        self.assertEqual(response.data['data']['status'], 'offline')

    def test_request_size_limit_is_enforced(self):
        self.auth(self.admin)
        oversized = '{"name":"' + ('x' * (65 * 1024)) + '"}'
        response = self.client.generic(
            'POST', self.path('targets/'), oversized, content_type='application/json',
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_request_json_top_level_must_be_an_object(self):
        self.auth(self.editor)
        for raw in ('[]', 'null', '"text"'):
            response = self.client.generic(
                'POST', self.path('plans/'), raw, content_type='application/json',
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PerformancePlan.objects.exists())


class PerformanceAgentProtocolTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='agent-admin', email='agent-admin@example.test', is_staff=True,
        )
        self.project = Project.objects.create(
            name='performance', project_type='perf', created_by=self.admin,
        )
        self.management_root = f'/api/v1/projects/{self.project.pk}/performance/'
        self.client.force_authenticate(user=self.admin)
        created = self.client.post(self.management_root + 'nodes/', {
            'name': 'agent', 'network_mode': 'lan', 'labels': {},
        }, format='json')
        self.node_id = created.data['data']['node']['id']
        self.enrollment_token = created.data['data']['enrollment_token']
        self.client.force_authenticate(user=None)

    @staticmethod
    def versions(**overrides):
        result = {
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
        }
        result.update(overrides)
        return result

    def enroll(self, token=None, **overrides):
        payload = {'enrollment_token': token or self.enrollment_token, **self.versions(**overrides)}
        return self.client.post('/api/v1/performance-agent/enroll/', payload, format='json')

    def heartbeat(self, agent_token, **overrides):
        payload = {
            **self.versions(),
            'resources': {'cpu_percent': 12.5, 'memory_percent': 34.5},
        }
        payload.update(overrides)
        return self.client.post(
            '/api/v1/performance-agent/heartbeat/', payload, format='json',
            HTTP_AUTHORIZATION=f'Node {agent_token}',
        )

    def test_enrollment_is_one_time_and_only_hashes_are_stored(self):
        enrolled = self.enroll()
        self.assertEqual(enrolled.status_code, 200, enrolled.data)
        self.assertEqual(set(enrolled.data), {'success', 'data'})
        self.assertFalse(enrolled.data['data']['execution_enabled'])
        agent_token = enrolled.data['data']['agent_token']
        node = PerformanceNode.objects.get(pk=self.node_id)
        self.assertNotIn(agent_token, node.agent_token_digest)
        self.assertNotIn(self.enrollment_token, node.enrollment_token_digest)
        self.assertEqual(len(node.agent_token_digest), 64)
        self.assertEqual(self.enroll().status_code, 401)

    def test_expired_cross_node_and_version_mismatch_are_rejected_without_consumption(self):
        PerformanceNode.objects.filter(pk=self.node_id).update(
            enrollment_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(self.enroll().status_code, 401)

        self.client.force_authenticate(user=self.admin)
        second = self.client.post(self.management_root + 'nodes/', {
            'name': 'second', 'network_mode': 'public', 'labels': {},
        }, format='json')
        second_token = second.data['data']['enrollment_token']
        self.client.force_authenticate(user=None)
        malformed_cross = f"{self.node_id}.{second_token.split('.', 1)[1]}"
        self.assertEqual(self.enroll(malformed_cross).status_code, 401)

        second_id = second.data['data']['node']['id']
        mismatch = self.enroll(second_token, protocol_version=999)
        self.assertEqual(mismatch.status_code, 409, mismatch.data)
        second_node = PerformanceNode.objects.get(pk=second_id)
        self.assertIsNone(second_node.last_seen_at)
        self.assertTrue(second_node.enrollment_token_digest)

    def test_heartbeat_binds_identity_and_rejects_bad_versions_without_last_seen_write(self):
        agent_token = self.enroll().data['data']['agent_token']
        mismatch = self.heartbeat(agent_token, protocol_version=999)
        self.assertEqual(mismatch.status_code, 409, mismatch.data)
        self.assertIsNone(PerformanceNode.objects.get(pk=self.node_id).last_seen_at)

        success = self.heartbeat(agent_token)
        self.assertEqual(success.status_code, 200, success.data)
        self.assertEqual(set(success.data), {'success', 'data'})
        self.assertEqual(success.data['data']['node_id'], self.node_id)
        self.assertEqual(success.data['data']['protocol_version'], PROTOCOL_VERSION)
        self.assertEqual(success.data['data']['command'], {'type': 'idle'})
        node = PerformanceNode.objects.get(pk=self.node_id)
        self.assertEqual(node.resources, {'cpu_percent': 12.5, 'memory_percent': 34.5})
        self.assertEqual(node.status_at(), 'online')

    def test_resources_are_strict_bounded_and_finite(self):
        agent_token = self.enroll().data['data']['agent_token']
        for resources in (
            {'cpu_percent': -1, 'memory_percent': 20},
            {'cpu_percent': 1, 'memory_percent': 101},
            {'cpu_percent': 1, 'memory_percent': 20, 'disk_percent': 2},
        ):
            response = self.heartbeat(agent_token, resources=resources)
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.generic(
            'POST', '/api/v1/performance-agent/heartbeat/',
            '{"protocol_version":1,"agent_version":"0.1.0","engine_version":"2.43.3",'
            '"resources":{"cpu_percent":NaN,"memory_percent":2}}',
            content_type='application/json', HTTP_AUTHORIZATION=f'Node {agent_token}',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(PerformanceNode.objects.get(pk=self.node_id).last_seen_at)

    def test_rotation_and_revoke_immediately_invalidate_agent_identity(self):
        old_agent_token = self.enroll().data['data']['agent_token']
        self.client.force_authenticate(user=self.admin)
        rotated = self.client.post(
            self.management_root + f'nodes/{self.node_id}/enrollment/', {}, format='json',
        )
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.heartbeat(old_agent_token).status_code, 401)

        new_agent_token = self.enroll(rotated.data['data']['enrollment_token']).data['data']['agent_token']
        self.client.force_authenticate(user=self.admin)
        first = self.client.post(
            self.management_root + f'nodes/{self.node_id}/revoke/', {}, format='json',
        )
        second = self.client.post(
            self.management_root + f'nodes/{self.node_id}/revoke/', {}, format='json',
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['data']['status'], 'revoked')
        self.client.force_authenticate(user=None)
        self.assertEqual(self.heartbeat(new_agent_token).status_code, 401)

    def test_user_jwt_scheme_is_not_accepted_as_node_identity(self):
        response = self.client.post(
            '/api/v1/performance-agent/heartbeat/',
            {**self.versions(), 'resources': {'cpu_percent': 1, 'memory_percent': 2}},
            format='json', HTTP_AUTHORIZATION='Bearer platform-user-token',
        )
        self.assertEqual(response.status_code, 401)

    def test_boolean_and_string_protocol_versions_are_rejected(self):
        for version in (True, '1'):
            response = self.enroll(protocol_version=version)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(PerformanceNode.objects.get(pk=self.node_id).last_seen_at)

    def test_agent_request_json_top_level_must_be_an_object(self):
        for raw in ('[]', 'null', '"text"'):
            response = self.client.generic(
                'POST', '/api/v1/performance-agent/enroll/', raw,
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 400, response.data)
