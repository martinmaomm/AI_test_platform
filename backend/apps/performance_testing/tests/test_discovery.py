"""Offline SQLite contracts for performance-project browser discovery reuse."""
from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from ai_core.models import LLMConfiguration
from api_testing.models import (
    APISpecification,
    APIWorkspace,
    BrowserDiscoveryRecord,
    BrowserDiscoveryTask,
)
from performance_testing.discovery import (
    _claim_performance_discovery,
    run_performance_browser_discovery_async,
)
from performance_testing.models import PerformanceTarget
from performance_testing.serializers import PerformancePlanSerializer
from projects.models import Project, ProjectMember
from users.models import User


@override_settings(API_BROWSER_DISCOVERY_ENABLED=True)
class PerformanceDiscoveryAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='perf-discovery-owner', email='perf-discovery-owner@example.test',
        )
        self.viewer = User.objects.create_user(
            username='perf-discovery-viewer', email='perf-discovery-viewer@example.test',
        )
        self.outsider = User.objects.create_user(
            username='perf-discovery-outsider', email='perf-discovery-outsider@example.test',
        )
        self.admin = User.objects.create_user(
            username='perf-discovery-admin', email='perf-discovery-admin@example.test', is_staff=True,
        )
        self.project = Project.objects.create(
            name='performance discovery', project_type='perf',
            owner=self.owner, created_by=self.owner,
        )
        self.api_project = Project.objects.create(
            name='api discovery', project_type='api',
            owner=self.owner, created_by=self.owner,
        )
        for project in (self.project, self.api_project):
            ProjectMember.objects.create(
                project=project, user=self.owner, role='owner',
                can_edit=True, can_delete=True, can_execute_tests=True,
            )
        ProjectMember.objects.create(
            project=self.project, user=self.viewer, role='viewer',
            can_edit=False, can_delete=False, can_execute_tests=False,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', model_name='offline-discovery',
            created_by=self.owner, is_active=True,
        )
        self.target = PerformanceTarget.objects.create(
            project=self.project, name='approved',
            base_url='https://api.example.test/', allowed_methods=['POST', 'PATCH', 'GET'],
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.owner)

    def path(self, suffix=''):
        return f'/api/v1/projects/{self.project.id}/performance/discovery/{suffix}'

    def task(self, **overrides):
        values = {
            'project': self.project,
            'owner': self.owner,
            'model_id': self.model.id,
            'target_url': 'https://shop.example.test/page',
            'description': '创建并更新测试数据',
            'api_origin': 'https://api.example.test',
            'allow_test_data_writes': True,
            'exploration_timeout_seconds': 600,
            'limits': {
                'origin_mode': 'manual', 'max_requests': 50,
                'max_body_bytes': 4096, 'max_total_bytes': 65536,
                'max_model_steps': 10, 'max_tool_calls': 10,
            },
            'task_id': str(uuid.uuid4()),
        }
        values.update(overrides)
        return BrowserDiscoveryTask.objects.create(**values)

    @staticmethod
    def summary(*, method, path, request=None, response=None):
        return {
            'method': method,
            'path': path,
            'origin': 'https://api.example.test',
            'source_authorized': True,
            'capture_complete': True,
            'observed_request': request or {
                'query': [], 'headers': {}, 'auth_hints': [], 'json': None, 'form': [],
            },
            'observed_response': response or {'headers': {}, 'auth_hints': [], 'body': {}},
        }

    def test_config_create_list_and_records_wrapper_are_performance_specific(self):
        self.auth()
        config = self.client.get(self.path('config/'))
        self.assertEqual(config.status_code, 200, config.data)
        self.assertTrue(config.data['data']['enabled'])
        self.assertEqual(config.data['data']['models'][0]['id'], self.model.id)

        payload = {
            'target_url': 'https://shop.example.test/page#spa',
            'description': '只操作测试数据',
            'model_id': self.model.id,
            'api_origin': 'https://api.example.test',
            'allow_test_data_writes': True,
            'exploration_timeout_seconds': 120,
        }
        with patch(
            'performance_testing.discovery_views.resolve_browser_discovery_mcp_config',
            return_value={'mcpServers': {'playwright': {}}},
        ), patch(
            'performance_testing.discovery_views.dispatch_performance_discovery',
            return_value=True,
        ) as dispatch:
            created = self.client.post(self.path('tasks/'), payload, format='json')
        self.assertEqual(created.status_code, 202, created.data)
        task = BrowserDiscoveryTask.objects.get(pk=created.data['data']['id'])
        self.assertEqual((task.project_id, task.owner_id), (self.project.id, self.owner.id))
        self.assertEqual(task.target_url, payload['target_url'])
        dispatch.assert_called_once_with(task)

        invalid = self.client.post(self.path('tasks/'), {
            **payload, 'unknown_field': 'must-not-be-ignored',
        }, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)

        BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, origin=task.api_origin, method='GET', path='/health',
            status_code=200, is_eligible=True,
            public_summary=self.summary(method='GET', path='/health'),
        )
        listed = self.client.get(self.path('tasks/'))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['data']['items'][0]['id'], str(task.id))
        records = self.client.get(self.path(f'tasks/{task.id}/records/?limit=20&after=0'))
        self.assertEqual(records.status_code, 200, records.data)
        self.assertEqual(set(records.data['data']), {'items', 'next_after', 'evidence_path'})
        self.assertEqual(records.data['data']['items'][0]['path'], '/health')

    def test_project_type_and_owner_isolation_work_in_both_directions(self):
        performance_task = self.task(status=BrowserDiscoveryTask.Status.FAILED)
        api_task = self.task(
            project=self.api_project, status=BrowserDiscoveryTask.Status.FAILED,
            task_id=str(uuid.uuid4()),
        )
        self.auth()
        # The old API route cannot use a performance project id to read the
        # shared-table task, even when the caller is its exact owner.
        api_path = (
            f'/api/v1/projects/{self.project.id}/api-testing/'
            f'browser-discoveries/{performance_task.id}/'
        )
        self.assertEqual(self.client.get(api_path).status_code, 404)

        wrong_perf_path = (
            f'/api/v1/projects/{self.api_project.id}/performance/discovery/'
            f'tasks/{api_task.id}/'
        )
        self.assertEqual(self.client.get(wrong_perf_path).status_code, 404)

        self.auth(self.viewer)
        listed = self.client.get(self.path('tasks/'))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['data']['items'], [])
        self.assertEqual(
            self.client.get(self.path(f'tasks/{performance_task.id}/')).status_code, 404,
        )

        self.auth(self.admin)
        self.assertEqual(
            self.client.get(self.path(f'tasks/{performance_task.id}/')).status_code, 404,
        )

    def test_draft_preserves_real_types_adds_exact_closure_and_never_saves_assets(self):
        task = self.task(
            status=BrowserDiscoveryTask.Status.PARTIAL,
            version=3,
            source_version=3,
        )
        setup = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, request_id='create', origin=task.api_origin,
            method='POST', path='/items', resource_type='fetch', status_code=201,
            content_type='application/json', is_eligible=True,
            public_summary=self.summary(
                method='POST', path='/items',
                request={
                    'query': [{'name': 'dryRun', 'value': False}],
                    'headers': {'Accept': 'application/json'},
                    'auth_hints': [],
                    'json': {
                        'name': 'seed', 'password': '<redacted>', 'count': 2,
                        'this_is_a_very_long_secret_field_name_that_exceeds_sixty_four_characters_token': '<redacted>',
                    },
                    'form': [],
                },
                response={'headers': {}, 'auth_hints': [], 'body': {'data': {'id': 'item-123'}}},
            ),
        )
        main = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, request_id='update', origin=task.api_origin,
            method='PATCH', path='/items/item-123', resource_type='fetch', status_code=200,
            content_type='application/json', is_eligible=True,
            dependency_record_ids=[setup.id],
            public_summary=self.summary(
                method='PATCH', path='/items/item-123',
                request={
                    'query': [
                        {'name': 'tag', 'value': 'one'},
                        {'name': 'tag', 'value': 'two'},
                    ],
                    'headers': {},
                    'auth_hints': [{'name': 'Authorization', 'value_hash': 'evidence-only'}],
                    'json': {'id': 'item-123', 'enabled': True},
                    'form': [],
                },
                response={'headers': {}, 'auth_hints': [], 'body': {'data': {'id': 'item-123'}}},
            ) | {
                'dependency_bindings': [{
                    'location': 'json.id',
                    'sources': [{
                        'record_id': setup.id,
                        'method': setup.method,
                        'path': setup.path,
                        'field': 'data.id',
                    }],
                    'reason': 'exact observed value match',
                }],
            },
        )
        self.auth()
        before = (APISpecification.objects.count(), APIWorkspace.objects.count())
        reply = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
            'version': 3,
            'record_ids': [main.id],
            'target_id': self.target.id,
        }, format='json')
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(before, (APISpecification.objects.count(), APIWorkspace.objects.count()))

        payload = reply.data['data']
        draft, source = payload['draft'], payload['source']
        self.assertEqual(source['included_record_ids'], [setup.id, main.id])
        self.assertTrue(source['partial'])
        self.assertEqual([step['phase'] for step in draft['steps']], ['setup', 'main'])
        setup_step, main_step = draft['steps']
        self.assertIs(setup_step['query']['dryRun'], False)
        self.assertEqual(setup_step['body']['count'], 2)
        self.assertEqual(main_step['query']['tag'], ['one', 'two'])
        self.assertRegex(main_step['path'], r'^/items/\$\{discovery_\d+_data_id\}$')
        self.assertRegex(main_step['body']['id'], r'^\$\{discovery_\d+_data_id\}$')
        self.assertRegex(main_step['headers']['Authorization'], r'^\$\{record_\d+_headers_authorization\}$')
        self.assertEqual(setup_step['extract'][0]['check'], 'body.data.id')
        self.assertEqual(draft['variables'], {})
        self.assertTrue(source['required_variables'])
        self.assertTrue(all(len(item['name']) <= 64 for item in source['required_variables']))
        self.assertTrue(all(
            len(item['name']) <= 64
            for step in draft['steps'] for item in step['extract']
        ))
        self.assertTrue(any('部分完成' in warning for warning in payload['warnings']))
        self.assertTrue(any('未赋值' in warning for warning in payload['warnings']))

        # After the UI fills every explicit placeholder, the generated draft
        # satisfies the current plan serializer without hidden transformations.
        editable = deepcopy(draft)
        editable['variables'] = {
            item['name']: 'fixture-user-supplied-value'
            for item in source['required_variables']
        }
        serializer = PerformancePlanSerializer(data=editable, context={'project': self.project})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_form_request_with_json_response_stays_form(self):
        task = self.task(status=BrowserDiscoveryTask.Status.COMPLETED)
        record = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, origin=task.api_origin, method='POST', path='/sessions',
            resource_type='fetch', status_code=200,
            # This field is request-side capture metadata. The response body is
            # deliberately JSON to guard against future request/response mixups.
            content_type='application/x-www-form-urlencoded', is_eligible=True,
            public_summary=self.summary(
                method='POST', path='/sessions',
                request={
                    'query': [],
                    'headers': {'Content-Type': 'application/x-www-form-urlencoded'},
                    'auth_hints': [], 'json': None,
                    'form': [
                        {'name': 'account', 'value': 'fixture-user'},
                        {'name': 'remember', 'value': True},
                    ],
                },
                response={
                    'headers': {'Content-Type': 'application/json'},
                    'auth_hints': [], 'body': {'data': {'token': '<redacted>'}},
                },
            ),
        )
        self.auth()
        reply = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
            'version': task.version, 'record_ids': [record.id], 'target_id': self.target.id,
        }, format='json')
        self.assertEqual(reply.status_code, 200, reply.data)
        step = reply.data['data']['draft']['steps'][0]
        self.assertEqual(step['body_type'], 'form')
        self.assertEqual(step['body'], {'account': 'fixture-user', 'remember': True})

    def test_target_path_prefix_is_removed_once_and_outside_paths_are_rejected(self):
        target = PerformanceTarget.objects.create(
            project=self.project, name='prefixed target',
            base_url='https://api.example.test/api/', allowed_methods=['GET'],
        )
        task = self.task(status=BrowserDiscoveryTask.Status.COMPLETED)
        inside = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, origin=task.api_origin, method='GET', path='/api/items',
            resource_type='fetch', status_code=200, content_type='', is_eligible=True,
            public_summary=self.summary(method='GET', path='/api/items'),
        )
        outside = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, origin=task.api_origin, method='GET', path='/other/items',
            resource_type='fetch', status_code=200, content_type='', is_eligible=True,
            public_summary=self.summary(method='GET', path='/other/items'),
        )
        self.auth()
        accepted = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
            'version': task.version, 'record_ids': [inside.id], 'target_id': target.id,
        }, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data['data']['draft']['steps'][0]['path'], '/items')
        self.assertEqual(accepted.data['data']['source']['target_path_prefix'], '/api')

        refused = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
            'version': task.version, 'record_ids': [outside.id], 'target_id': target.id,
        }, format='json')
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn('不在性能目标路径前缀', refused.data['error']['message'])

    def test_candidate_auth_dependency_is_warned_but_not_made_executable(self):
        task = self.task(status=BrowserDiscoveryTask.Status.COMPLETED)
        authentication = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, origin=task.api_origin, method='POST', path='/sessions',
            resource_type='fetch', status_code=200, content_type='application/json', is_eligible=True,
            public_summary=self.summary(
                method='POST', path='/sessions',
                response={
                    'headers': {}, 'auth_hints': [{'name': 'set-cookie'}],
                    'body': {'token': '<redacted>'},
                },
            ),
        )
        business = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, origin=task.api_origin, method='GET', path='/items',
            resource_type='fetch', status_code=200, content_type='', is_eligible=True,
            dependency_record_ids=[authentication.id],
            public_summary=self.summary(
                method='GET', path='/items',
                request={
                    'query': [], 'headers': {}, 'json': None, 'form': [],
                    'auth_hints': [{'name': 'Authorization'}],
                },
            ) | {
                'dependency_candidates': [{
                    'record_id': authentication.id,
                    'kind': 'authentication',
                    'confidence': 'candidate',
                }],
            },
        )
        self.auth()
        reply = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
            'version': task.version,
            'record_ids': [business.id],
            'target_id': self.target.id,
        }, format='json')
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data['data']['source']['included_record_ids'], [business.id])
        self.assertEqual(len(reply.data['data']['draft']['steps']), 1)
        self.assertTrue(any(
            '仅为候选' in warning for warning in reply.data['data']['warnings']
        ))

    def test_draft_rejects_target_origin_method_and_cross_project_target(self):
        task = self.task(status=BrowserDiscoveryTask.Status.COMPLETED)
        record = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, origin=task.api_origin, method='PATCH', path='/items/1',
            resource_type='fetch', status_code=200, content_type='', is_eligible=True,
            public_summary=self.summary(method='PATCH', path='/items/1'),
        )
        wrong_origin = PerformanceTarget.objects.create(
            project=self.project, name='wrong origin',
            base_url='https://other.example.test', allowed_methods=['PATCH'],
        )
        wrong_method = PerformanceTarget.objects.create(
            project=self.project, name='wrong method',
            base_url=task.api_origin, allowed_methods=['GET'],
        )
        other_project = Project.objects.create(
            name='other perf', project_type='perf', created_by=self.admin,
        )
        cross_target = PerformanceTarget.objects.create(
            project=other_project, name='cross', base_url=task.api_origin,
            allowed_methods=['PATCH'],
        )
        self.auth()
        for target, expected in (
            (wrong_origin, 400), (wrong_method, 400), (cross_target, 404),
        ):
            with self.subTest(target=target.name):
                response = self.client.post(self.path(f'tasks/{task.id}/draft/'), {
                    'version': task.version,
                    'record_ids': [record.id],
                    'target_id': target.id,
                }, format='json')
                self.assertEqual(response.status_code, expected, response.data)

    def test_retry_clones_safe_failure_but_refuses_possible_browser_replay(self):
        safe = self.task(
            status=BrowserDiscoveryTask.Status.FAILED,
            error_code='queue_unavailable',
            error_message='not queued',
        )
        self.auth()
        with patch(
            'performance_testing.discovery_views.resolve_browser_discovery_mcp_config',
            return_value={'mcpServers': {'playwright': {}}},
        ), patch(
            'performance_testing.discovery_views.dispatch_performance_discovery',
            return_value=True,
        ) as dispatch:
            reply = self.client.post(self.path(f'tasks/{safe.id}/retry/'), {
                'version': safe.version,
            }, format='json')
        self.assertEqual(reply.status_code, 202, reply.data)
        retried = BrowserDiscoveryTask.objects.get(pk=reply.data['data']['task']['id'])
        self.assertNotEqual(retried.id, safe.id)
        self.assertEqual(retried.status, BrowserDiscoveryTask.Status.QUEUED)
        dispatch.assert_called_once_with(retried)
        duplicate = self.client.post(self.path(f'tasks/{safe.id}/retry/'), {
            'version': safe.version,
        }, format='json')
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertIn('已经提交过重试', duplicate.data['error']['message'])

        unsafe = self.task(
            status=BrowserDiscoveryTask.Status.PARTIAL,
            tool_calls=2,
            request_count=1,
            evidence_summary={'diagnostic': {
                'code': 'MODEL_OVERLOADED', 'message': 'safe',
                'retryable': True, 'stage': 'exploring',
            }},
        )
        refused = self.client.post(self.path(f'tasks/{unsafe.id}/retry/'), {
            'version': unsafe.version,
        }, format='json')
        self.assertEqual(refused.status_code, 409, refused.data)
        self.assertIn('不能自动重放', refused.data['error']['message'])

    def test_cancel_and_origin_endpoints_use_versioned_shared_state_contract(self):
        queued = self.task(status=BrowserDiscoveryTask.Status.QUEUED)
        self.auth()
        with patch(
            'performance_testing.discovery_views._read_origin_control',
            return_value={'version': 1, 'approved_origins': [], 'rejected_origins': [], 'cancelled': False},
        ), patch('performance_testing.discovery_views.write_origin_control') as write_control:
            cancelled = self.client.post(self.path(f'tasks/{queued.id}/cancel/'), {
                'version': queued.version,
            }, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        queued.refresh_from_db()
        self.assertEqual(queued.status, BrowserDiscoveryTask.Status.CANCELLED)
        self.assertTrue(queued.cancellation_requested)
        self.assertTrue(write_control.call_args.args[1]['cancelled'])

        selected_origin = 'https://api.example.test'
        terminal = self.task(
            status=BrowserDiscoveryTask.Status.PARTIAL,
            api_origin='',
            limits={
                'origin_mode': 'auto', 'max_requests': 50,
                'max_body_bytes': 4096, 'max_total_bytes': 65536,
                'max_model_steps': 10, 'max_tool_calls': 10,
            },
        )
        BrowserDiscoveryRecord.objects.create(
            task=terminal, sequence=1, origin=selected_origin, method='GET', path='/items',
            resource_type='fetch', status_code=200, is_eligible=False,
            public_summary=self.summary(method='GET', path='/items'),
        )
        state = {
            'version': 1, 'resolved_origins': [selected_origin],
            'pending': [], 'rejected_origins': [],
        }
        with patch(
            'performance_testing.discovery_views.sync_auto_origin', return_value=state,
        ), patch(
            'performance_testing.discovery_views.selectable_origins', return_value=[selected_origin],
        ), patch(
            'performance_testing.discovery_views._refresh_selected_record_eligibility',
        ):
            selected = self.client.post(self.path(f'tasks/{terminal.id}/origin/'), {
                'version': terminal.version,
                'origin': selected_origin,
                'decision': 'select',
            }, format='json')
        self.assertEqual(selected.status_code, 200, selected.data)
        terminal.refresh_from_db()
        self.assertEqual(terminal.api_origin, selected_origin)
        self.assertEqual(terminal.limits['selected_origin'], selected_origin)

    def test_worker_claim_requires_perf_type_and_current_owner_execute_permission(self):
        queued = self.task(status=BrowserDiscoveryTask.Status.QUEUED)
        claimed = _claim_performance_discovery(str(queued.id), queued.version, queued.task_id)
        self.assertIsNotNone(claimed)
        queued.refresh_from_db()
        self.assertEqual(queued.status, BrowserDiscoveryTask.Status.RUNNING)

        api_task = self.task(
            project=self.api_project,
            status=BrowserDiscoveryTask.Status.QUEUED,
            task_id=str(uuid.uuid4()),
        )
        self.assertIsNone(_claim_performance_discovery(
            str(api_task.id), api_task.version, api_task.task_id,
        ))
        api_task.refresh_from_db()
        self.assertEqual(api_task.error_code, 'project_not_perf')

        revoked = self.task(status=BrowserDiscoveryTask.Status.QUEUED)
        ProjectMember.objects.filter(project=self.project, user=self.owner).update(
            can_execute_tests=False,
        )
        self.assertIsNone(_claim_performance_discovery(
            str(revoked.id), revoked.version, revoked.task_id,
        ))
        revoked.refresh_from_db()
        self.assertEqual(revoked.error_code, 'permission_revoked')

    def test_worker_reuses_shared_agent_and_lifecycle_with_mock_model_and_mcp(self):
        queued = self.task(status=BrowserDiscoveryTask.Status.QUEUED)
        agent = AsyncMock(return_value={
            'completed': False, 'error_code': 'NO_RECORDS',
            'summary': 'offline mock', 'tool_calls': 0, 'model_calls': 1,
        })
        with tempfile.TemporaryDirectory() as directory, patch(
            'ai_core.model_manager.get_llm_manager',
            return_value=SimpleNamespace(current_llm=object()),
        ), patch(
            'performance_testing.discovery.resolve_browser_discovery_mcp_config',
            return_value={'mcpServers': {'playwright': {'command': 'offline-mock'}}},
        ), patch(
            'performance_testing.discovery.task_trace_dir',
            return_value=Path(directory),
        ), patch(
            'performance_testing.discovery.task_trace_file',
            return_value=Path(directory) / 'network.jsonl',
        ), patch(
            'api_testing.browser_discovery_agent.run_browser_discovery', agent,
        ), patch(
            'api_testing.tasks._finish_browser_discovery',
        ) as finish:
            result = run_performance_browser_discovery_async.run(
                str(queued.id), queued.version, queued.task_id,
            )
        self.assertEqual(result, {'status': 'finished'})
        agent.assert_awaited_once()
        options = agent.await_args.kwargs
        self.assertEqual(options['target_url'], queued.target_url)
        self.assertEqual(options['api_origin'], queued.api_origin)
        self.assertTrue(callable(options['checkpoint']))
        self.assertTrue(callable(options['is_cancelled']))
        finish.assert_called_once()
