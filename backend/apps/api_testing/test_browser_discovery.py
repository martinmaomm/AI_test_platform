"""Offline contracts for browser-discovery persistence and source handoff."""
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project, ProjectMember
from .browser_discovery import _response_scalar_values, expire_stale_discovery, handoff_to_workspace, ingest_trace, task_trace_file
from .browser_discovery_views import BrowserDiscoveryCollectionView, BrowserDiscoveryHandoffView
from .models import APIEndpoint, APISpecification, APIWorkspace, BrowserDiscoveryRecord, BrowserDiscoveryTask, default_api_workspace_draft
from .tasks import _claim_browser_discovery, _finish_browser_discovery
from .workspace_service import endpoint_specs, generation_endpoint_specs
from .workspace_service import WorkspaceValidationError
from .workspace_views import APIWorkspaceCollectionView, APIWorkspaceDetailView, APIWorkspaceMessagesView
from .workspace_verification import prepare_candidate
from .views import APIEndpointListView, APISpecificationRetrieveUpdateDestroyView


class BrowserDiscoveryContractsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='browser-owner', email='browser-owner@example.test', password='pw')
        self.other = get_user_model().objects.create_user(username='browser-other', email='browser-other@example.test', password='pw')
        self.project = Project.objects.create(name='Browser capture', project_type='api', owner=self.owner, created_by=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.other, role='editor', can_edit=True, can_execute_tests=True)
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', model_name='offline-browser', created_by=self.owner, is_active=True,
        )
        self.factory = APIRequestFactory()

    def task(self, **changes):
        values = {
            'project': self.project, 'owner': self.owner, 'model_id': self.model.id,
            'target_url': 'https://shop.example.test/app?tenant=a#items', 'description': '登录后创建并修改本轮商品',
            'api_origin': 'https://api.example.test', 'allow_test_data_writes': True,
            'task_id': str(uuid.uuid4()),
            'limits': {'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 20 * 1024 * 1024},
        }
        values.update(changes)
        return BrowserDiscoveryTask.objects.create(**values)

    def event(self, event, **payload):
        return {'protocol_version': 1, 'event': event, **payload}

    def test_node_jsonl_preserves_pairs_redacts_unknown_and_builds_path_dependency(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = self.task()
            trace = task_trace_file(task)
            trace.parent.mkdir(parents=True)
            events = [
                self.event('capture_started', capture_status='enabled'),
                self.event('request', request_id='create', request_sequence=1, method='POST', origin='https://api.example.test', path='/items', url='https://api.example.test/items', query=[{'name': 'tag', 'value': 'first'}, {'name': 'tag', 'value': 'second'}], resource_type='fetch', capture_status='pending', authentication_headers=[{'name': 'Authorization', 'scheme': 'Bearer', 'value_sha256': 'abc'}]),
                self.event('request_body', request_id='create', capture_status='captured', body={'kind': 'json', 'value': {'name': 'run-item'}, 'bytes': 19, 'truncated': False}),
                self.event('response', request_id='create', status=201, capture_status='captured', body={'kind': 'json', 'value': {'id': 'item-123'}, 'bytes': 17, 'truncated': False}),
                self.event('request', request_id='update', request_sequence=2, method='PATCH', origin='https://api.example.test', path='/items/item-123', url='https://api.example.test/items/item-123', query=[], resource_type='fetch', capture_status='pending'),
                self.event('request_body', request_id='update', capture_status='captured', body={'kind': 'json', 'value': {'name': 'run-item-edited'}, 'bytes': 26, 'truncated': False}),
                self.event('response', request_id='update', status=200, capture_status='captured', body={'kind': 'json', 'value': {'id': 'item-123'}, 'bytes': 17, 'truncated': False}),
                self.event('request', request_id='third-party', request_sequence=3, method='GET', origin='https://third.example.test', path='/pixel', resource_type='fetch', capture_status='metadata_only', reason='origin_not_authorized'),
                self.event('failure', request_id='third-party', capture_status='metadata_only', reason='origin_not_authorized'),
                self.event('request', request_id='known-failure', request_sequence=4, method='GET', origin='https://api.example.test', path='/items', url='https://api.example.test/items', resource_type='fetch', capture_status='pending'),
                self.event('failure', request_id='known-failure', capture_status='network_failed', reason='browser_closed_before_terminal'),
            ]
            trace.write_text('\n'.join(json.dumps(item) for item in events) + '\n', encoding='utf-8')
            result = ingest_trace(task)
            self.assertEqual(result['ingested'], 4)
            self.assertEqual(result['invalid'], 0)
            records = list(task.records.order_by('sequence'))
            self.assertEqual(records[0].request_id, 'create')
            self.assertEqual(records[0].resource_type, 'fetch')
            self.assertEqual(records[0].public_summary['observed_request']['query'][1]['value'], 'second')
            self.assertEqual(records[0].public_summary['observed_request']['auth_hints'][0]['value_hash'], 'abc')
            self.assertNotIn('observed_request', records[2].public_summary)
            self.assertFalse(records[2].is_eligible)
            self.assertFalse(records[3].is_eligible)
            self.assertEqual(records[3].exclusion_reason, 'capture_incomplete')
            self.assertEqual(records[1].dependency_record_ids, [records[0].id])

            task.status = BrowserDiscoveryTask.Status.COMPLETED
            task.source_version = task.version
            task.save(update_fields=['status', 'source_version', 'updated_at'])
            handoff, created = handoff_to_workspace(task=task, owner=self.owner, version=task.version, record_ids=[records[0].id, records[1].id])
            self.assertTrue(created)
            self.assertEqual(handoff.workspace.messages[0]['content'], task.description)
            self.assertEqual(handoff.workspace.draft['config']['base_url'], task.api_origin)
            request = self.factory.post('/', {
                'version': task.version, 'record_ids': [records[0].id, records[1].id],
            }, format='json')
            force_authenticate(request, self.owner)
            repeated = BrowserDiscoveryHandoffView.as_view()(request, project_id=self.project.id, task_id=task.id)
            self.assertEqual(repeated.status_code, 200, repeated.data)
            workspace_data = repeated.data['data']['workspace']
            self.assertEqual(workspace_data['source_type'], 'browser_capture')
            self.assertEqual(workspace_data['source_name'], handoff.spec.spec_name)
            self.assertEqual(workspace_data['source_task_id'], str(task.id))
            endpoints = endpoint_specs(self.project.id, handoff.workspace.endpoint_ids, spec_id=handoff.spec_id, owner=self.owner)
            update = next(item for item in endpoints if item['path'] == '/items/item-123')
            browser = update['document_context']['browser_capture']
            template = browser['observed_samples'][0]['path_template']
            self.assertEqual(template['exact_path'], '/items/item-123')
            self.assertIn('${browser_dep_', template['template'])
            self.assertNotIn('required', update['request_body'])
            self.assertEqual(update['parameters'], [])

            create = next(item for item in endpoints if item['method'] == 'POST' and item['path'] == '/items')
            draft = {
                'version': 1, 'config': {'name': 'fresh item', 'base_url': '', 'variables': {}, 'verify': True},
                'teststeps': [
                    {'name': 'create', 'endpoint_id': create['id'], 'request': {'method': 'POST', 'url': '/items', 'json': {'name': 'fresh'}}, 'extract': {'item_id': 'body.id'}, 'validate': [{'eq': ['status_code', 201]}]},
                    {'name': 'update', 'endpoint_id': update['id'], 'request': {'method': 'PATCH', 'url': '/items/${item_id}', 'json': {'name': 'fresh2'}}, 'extract': {}, 'validate': [{'eq': ['status_code', 200]}]},
                ],
            }
            accepted = prepare_candidate(draft, endpoints=endpoints, target_url='https://api.example.test', variables={})
            self.assertEqual(accepted['teststeps'][1]['request']['url'], '/items/${item_id}')
            draft['teststeps'][1]['request']['url'] = '/items/item-123'
            with self.assertRaises(WorkspaceValidationError):
                prepare_candidate(draft, endpoints=endpoints, target_url='https://api.example.test', variables={})

    @override_settings(API_BROWSER_DISCOVERY_ENABLED=True)
    def test_atomic_claim_rejects_duplicate_delivery_and_revalidates_owner_permissions(self):
        task = self.task()
        self.assertIsNotNone(_claim_browser_discovery(str(task.id), task.version, task.task_id))
        self.assertIsNone(_claim_browser_discovery(str(task.id), task.version, task.task_id))

        project_owner = get_user_model().objects.create_user(username='project-owner', email='project-owner@example.test', password='pw')
        revoked_owner = get_user_model().objects.create_user(username='revoked-owner', email='revoked-owner@example.test', password='pw')
        project = Project.objects.create(name='Revocable', project_type='api', owner=project_owner, created_by=project_owner)
        ProjectMember.objects.create(project=project, user=revoked_owner, role='editor', can_edit=True, can_execute_tests=True)
        model = LLMConfiguration.objects.create(model_type='llm', provider='offline', model_name='revocable', created_by=revoked_owner, is_active=True)
        revoked = self.task(project=project, owner=revoked_owner, model_id=model.id)
        ProjectMember.objects.filter(project=project, user=revoked_owner).delete()
        self.assertIsNone(_claim_browser_discovery(str(revoked.id), revoked.version, revoked.task_id))
        revoked.refresh_from_db()
        self.assertEqual(revoked.status, BrowserDiscoveryTask.Status.FAILED)
        self.assertEqual(revoked.error_code, 'permission_revoked')

    def test_finish_uses_capture_completeness_and_cannot_overwrite_stale_or_cancelled(self):
        task = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
        BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, request_id='ok', method='GET', path='/items', is_eligible=True,
            public_summary={'capture_complete': True},
        )
        BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, request_id='missing', method='GET', path='/items', is_eligible=False,
            exclusion_reason='capture_incomplete', public_summary={'capture_complete': False},
        )
        _finish_browser_discovery(str(task.id), task.version, task.task_id, {'completed': True, 'summary': '已完成'})
        task.refresh_from_db()
        self.assertEqual(task.status, BrowserDiscoveryTask.Status.PARTIAL)
        self.assertEqual(task.error_code, 'capture_incomplete')
        self.assertEqual(task.evidence_summary['usable'], 1)
        self.assertEqual(task.evidence_summary['incomplete'], 1)

        stale = self.task(status=BrowserDiscoveryTask.Status.RUNNING, heartbeat_at=timezone.now() - timedelta(minutes=5))
        BrowserDiscoveryRecord.objects.create(task=stale, sequence=1, request_id='saved', method='GET', path='/saved', is_eligible=True)
        expire_stale_discovery(stale)
        _finish_browser_discovery(str(stale.id), stale.version, stale.task_id, {'completed': True, 'summary': 'late worker'})
        stale.refresh_from_db()
        self.assertEqual(stale.status, BrowserDiscoveryTask.Status.PARTIAL)
        self.assertEqual(stale.error_code, 'heartbeat_expired')

        cancelled = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
        _finish_browser_discovery(str(cancelled.id), cancelled.version, cancelled.task_id, {'completed': False, 'error_code': 'CANCELLED'})
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, BrowserDiscoveryTask.Status.CANCELLED)

    def test_browser_capture_workspace_source_is_owner_scoped_at_create_patch_and_messages(self):
        task = self.task()
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner, status=APISpecification.TaskStatus.COMPLETED,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, source_task=task,
        )
        endpoint = APIEndpoint.objects.create(spec=spec, method='GET', path='/private-sample')
        create_request = self.factory.post('/', {'spec_id': spec.id, 'endpoint_ids': [endpoint.id]}, format='json')
        force_authenticate(create_request, user=self.other)
        create_reply = APIWorkspaceCollectionView.as_view()(create_request, project_id=self.project.id)
        self.assertEqual(create_reply.status_code, 400)
        self.assertIn('发起者', create_reply.data['message'])

        other_model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', model_name='other-browser', created_by=self.other, is_active=True,
        )
        legacy_workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.other, spec=spec, endpoint_ids=[endpoint.id], model_id=other_model.id,
            draft=default_api_workspace_draft(),
        )
        patch_request = self.factory.patch('/', {'revision': legacy_workspace.revision, 'endpoint_ids': [endpoint.id]}, format='json')
        force_authenticate(patch_request, user=self.other)
        patch_reply = APIWorkspaceDetailView.as_view()(patch_request, project_id=self.project.id, workspace_id=legacy_workspace.id)
        self.assertEqual(patch_reply.status_code, 400)
        with self.assertRaises(WorkspaceValidationError):
            generation_endpoint_specs(legacy_workspace)

        message_request = self.factory.post('/', {
            'revision': legacy_workspace.revision, 'message': '生成私有样本', 'mode': 'generate',
            'execution_confirmed': True, 'base_url': 'https://api.example.test', 'variables': {},
        }, format='json')
        force_authenticate(message_request, user=self.other)
        message_reply = APIWorkspaceMessagesView.as_view()(message_request, project_id=self.project.id, workspace_id=legacy_workspace.id)
        self.assertEqual(message_reply.status_code, 400)
        self.assertIn('发起者', message_reply.data['message'])

        list_request = self.factory.get('/')
        force_authenticate(list_request, user=self.other)
        list_reply = APIEndpointListView.as_view()(list_request, project_id=self.project.id, spec_id=spec.id)
        self.assertEqual(list_reply.status_code, 404)
        detail_request = self.factory.get('/')
        force_authenticate(detail_request, user=self.other)
        detail_reply = APISpecificationRetrieveUpdateDestroyView.as_view()(detail_request, project_id=self.project.id, pk=spec.id)
        self.assertEqual(detail_reply.status_code, 404)

    @override_settings(API_BROWSER_DISCOVERY_ENABLED=True)
    def test_create_rejects_non_api_project_and_does_not_store_queue_exception_details(self):
        web_project = Project.objects.create(name='Not API', project_type='web', owner=self.owner, created_by=self.owner)
        payload = {
            'target_url': 'https://shop.example.test/app?tenant=a#items', 'description': '仅用于测试',
            'api_origin': 'https://api.example.test', 'model_id': self.model.id,
            'allow_test_data_writes': True, 'exploration_timeout_seconds': 60,
        }
        web_request = self.factory.post('/', payload, format='json')
        force_authenticate(web_request, user=self.owner)
        web_reply = BrowserDiscoveryCollectionView.as_view()(web_request, project_id=web_project.id)
        self.assertEqual(web_reply.status_code, 400)
        self.assertIn('仅 API 类型项目', web_reply.data['message'])

        with patch('api_testing.browser_discovery.resolve_browser_discovery_mcp_config', return_value={'mcpServers': {'playwright': {}}}), \
                patch('api_testing.tasks.run_browser_discovery_async.apply_async', side_effect=RuntimeError('redis://user:secret@broker')):
            queue_request = self.factory.post('/', payload, format='json')
            force_authenticate(queue_request, user=self.owner)
            queue_reply = BrowserDiscoveryCollectionView.as_view()(queue_request, project_id=self.project.id)
        self.assertEqual(queue_reply.status_code, 503)
        queued = BrowserDiscoveryTask.objects.latest('created_at')
        self.assertEqual(queued.error_code, 'queue_unavailable')
        self.assertNotIn('secret', queued.error_message)

    def test_other_editor_cannot_list_owner_tasks(self):
        task = self.task()
        request = self.factory.get('/')
        force_authenticate(request, user=self.other)
        reply = BrowserDiscoveryCollectionView.as_view()(request, project_id=self.project.id)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.data['data'], [])

    def test_short_identifier_is_a_candidate_but_short_status_code_is_not_and_trace_keeps_bounded_prefix(self):
        self.assertIn(('data.id', '1'), _response_scalar_values({'data': {'id': 1, 'code': 1, 'status': 12}}))
        self.assertNotIn(('data.code', '1'), _response_scalar_values({'data': {'id': 1, 'code': 1, 'status': 12}}))
        self.assertNotIn(('data.status', '12'), _response_scalar_values({'data': {'id': 1, 'code': 1, 'status': 12}}))
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = self.task(limits={'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 1024})
            trace = task_trace_file(task)
            trace.parent.mkdir(parents=True)
            prefix = [
                self.event('request', request_id='one', request_sequence=1, method='GET', origin=task.api_origin,
                           path='/items/1', url=task.api_origin + '/items/1', resource_type='fetch', capture_status='pending'),
                self.event('response', request_id='one', status=200, capture_status='captured',
                           body={'kind': 'json', 'value': {'id': 1}, 'bytes': 8, 'truncated': False}),
            ]
            trace.write_text('\n'.join(json.dumps(item) for item in prefix) + '\n' + ('x' * 2048), encoding='utf-8')
            result = ingest_trace(task)
            self.assertEqual(result['truncated'], 1)
            self.assertEqual(task.records.count(), 1)
