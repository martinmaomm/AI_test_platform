"""Offline contracts for browser-discovery persistence and source handoff."""
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project, ProjectMember
from .browser_discovery import (
    _selection,
    _response_scalar_values, expire_stale_discovery, handoff_to_workspace,
    ingest_trace, origin_control_file, origin_resolution, origin_state_file,
    read_origin_state, selectable_origins, sync_auto_origin, task_trace_file,
    serialize_task,
)
from .browser_discovery_views import BrowserDiscoveryCollectionView, BrowserDiscoveryDetailView, BrowserDiscoveryHandoffView, BrowserDiscoveryOriginsView
from .models import APIEndpoint, APISpecification, APIWorkspace, BrowserDiscoveryHandoff, BrowserDiscoveryRecord, BrowserDiscoveryTask, default_api_workspace_draft
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
        ProjectMember.objects.create(
            project=self.project, user=self.owner, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
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

    def delete_task(self, task, user=None):
        request = self.factory.delete('/')
        force_authenticate(request, user or self.owner)
        return BrowserDiscoveryDetailView.as_view()(
            request, project_id=self.project.id, task_id=task.id,
        )

    def test_delete_each_terminal_task_cascades_records(self):
        for status in (
            BrowserDiscoveryTask.Status.COMPLETED,
            BrowserDiscoveryTask.Status.PARTIAL,
            BrowserDiscoveryTask.Status.FAILED,
            BrowserDiscoveryTask.Status.CANCELLED,
        ):
            with self.subTest(status=status):
                task = self.task(status=status)
                record = BrowserDiscoveryRecord.objects.create(
                    task=task, sequence=1, request_id=f'{status}-request', origin=task.api_origin,
                    method='GET', path=f'/{status}', public_summary={'source_authorized': True},
                )
                reply = self.delete_task(task)
                self.assertEqual(reply.status_code, 200, reply.data)
                self.assertEqual(reply.data['data']['id'], str(task.id))
                self.assertFalse(BrowserDiscoveryTask.objects.filter(pk=task.id).exists())
                self.assertFalse(BrowserDiscoveryRecord.objects.filter(pk=record.id).exists())
                self.assertEqual(self.delete_task(task).status_code, 404)

    def test_delete_cancelled_queue_task_makes_late_claim_a_noop(self):
        task = self.task()
        task.status = BrowserDiscoveryTask.Status.CANCELLED
        task.cancellation_requested = True
        task.save(update_fields=['status', 'cancellation_requested', 'updated_at'])
        self.assertEqual(self.delete_task(task).status_code, 200)
        self.assertIsNone(_claim_browser_discovery(str(task.id), task.version, task.task_id))

    def test_delete_requires_task_owner_and_current_project_edit_permission(self):
        owner_task = self.task(owner=self.other, status=BrowserDiscoveryTask.Status.CANCELLED)
        self.assertEqual(self.delete_task(owner_task, self.owner).status_code, 404)
        ProjectMember.objects.filter(project=self.project, user=self.other).delete()
        self.assertEqual(self.delete_task(owner_task, self.other).status_code, 403)
        self.assertTrue(BrowserDiscoveryTask.objects.filter(pk=owner_task.id).exists())

    def test_delete_rejects_active_or_unknown_status_without_expiring_task(self):
        for status in (
            BrowserDiscoveryTask.Status.QUEUED,
            BrowserDiscoveryTask.Status.RUNNING,
            BrowserDiscoveryTask.Status.FINALIZING,
            'unknown',
        ):
            with self.subTest(status=status):
                task = self.task(
                    status=status,
                    heartbeat_at=timezone.now() - timedelta(hours=1),
                )
                reply = self.delete_task(task)
                self.assertEqual(reply.status_code, 409, reply.data)
                self.assertIn('先取消并等待停止', reply.data['message'])
                task.refresh_from_db()
                self.assertEqual(task.status, status)

    def test_delete_rejects_handoff_and_published_source_references(self):
        handoff_task = self.task(status=BrowserDiscoveryTask.Status.COMPLETED)
        handoff_spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE,
            status=APISpecification.TaskStatus.COMPLETED, source_task=handoff_task,
        )
        BrowserDiscoveryHandoff.objects.create(
            task=handoff_task, source_version=handoff_task.version,
            selection_hash='handoff-delete-protection', spec=handoff_spec,
        )
        handoff_reply = self.delete_task(handoff_task)
        self.assertEqual(handoff_reply.status_code, 409, handoff_reply.data)
        self.assertIn('已交接', handoff_reply.data['message'])

        published_task = self.task(status=BrowserDiscoveryTask.Status.PARTIAL)
        APISpecification.objects.create(
            project=self.project, created_by=self.owner,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE,
            status=APISpecification.TaskStatus.COMPLETED, source_task=published_task,
        )
        published_reply = self.delete_task(published_task)
        self.assertEqual(published_reply.status_code, 409, published_reply.data)
        self.assertIn('已发布', published_reply.data['message'])

    def test_delete_serializes_state_and_maps_late_protection_to_conflict(self):
        active = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
        request = self.factory.get('/')
        force_authenticate(request, self.owner)
        detail = BrowserDiscoveryDetailView.as_view()(
            request, project_id=self.project.id, task_id=active.id,
        )
        self.assertFalse(detail.data['data']['can_delete'])
        self.assertIn('先取消并等待停止', detail.data['data']['delete_block_reason'])

        terminal = self.task(status=BrowserDiscoveryTask.Status.CANCELLED)
        with patch(
            'api_testing.browser_discovery_views.BrowserDiscoveryTask.delete',
            side_effect=ProtectedError('late relation protection', []),
        ):
            reply = self.delete_task(terminal)
        self.assertEqual(reply.status_code, 409, reply.data)
        self.assertTrue(BrowserDiscoveryTask.objects.filter(pk=terminal.id).exists())

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

    def test_runner_stop_reason_is_persisted_and_visible_in_task_detail(self):
        for code, message in [
            ('TOOL_FAILURE', '连续三次页面操作失败，已停止探索；请检查当前页面和登录条件。'),
            ('TOOL_NOT_ALLOWED', '本轮仅允许网页操作，不允许直接请求接口、执行任意代码或文件操作。'),
            ('TOTAL_TIMEOUT', '网页探索达到本轮总时限，已保留已收到的请求证据。'),
        ]:
            with self.subTest(code=code):
                task = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
                with patch('api_testing.browser_discovery.ingest_trace', return_value={}):
                    _finish_browser_discovery(str(task.id), task.version, task.task_id, {
                        'completed': False, 'error_code': code, 'summary': message,
                    })
                task.refresh_from_db()
                self.assertEqual(task.status, BrowserDiscoveryTask.Status.FAILED)
                self.assertEqual(task.error_code, code)
                self.assertEqual(task.error_message, message)
                request = self.factory.get('/')
                force_authenticate(request, self.owner)
                reply = BrowserDiscoveryDetailView.as_view()(
                    request, project_id=self.project.id, task_id=task.id,
                )
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.data['data']['error_message'], message)

    def test_finish_exception_diagnostic_takes_precedence_over_runner_summary(self):
        task = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
        with patch('api_testing.browser_discovery.ingest_trace', return_value={}):
            _finish_browser_discovery(str(task.id), task.version, task.task_id, {
                'completed': False, 'error_code': 'TOOL_FAILURE', 'summary': '先前的中止原因',
            }, exception=RuntimeError('SECRET_EXCEPTION_CONTENT'))
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'runner_failed')
        self.assertIn('RuntimeError', task.error_message)
        self.assertNotIn('SECRET', task.error_message)
        self.assertNotIn('先前', task.error_message)

    def test_model_failure_is_public_safe_metadata_at_initial_and_exploring_stages(self):
        for tool_calls, stage in ((0, 'initial_model'), (2, 'exploring')):
            with self.subTest(tool_calls=tool_calls):
                task = self.task(status=BrowserDiscoveryTask.Status.RUNNING)
                with patch('api_testing.browser_discovery.ingest_trace', return_value={}):
                    _finish_browser_discovery(str(task.id), task.version, task.task_id, {
                        'completed': False,
                        'error_code': 'MODEL_OVERLOADED',
                        'summary': '模型服务当前负载较高，请稍后重试。',
                        'tool_calls': tool_calls,
                        'diagnostic': {
                            'code': 'MODEL_OVERLOADED', 'message': 'raw SECRET provider body',
                            'retryable': True, 'status_code': 200,
                        },
                    })
                task.refresh_from_db()
                self.assertEqual(task.evidence_summary['diagnostic'], {
                    'code': 'MODEL_OVERLOADED',
                    'message': '模型服务当前负载较高，请稍后重试。',
                    'retryable': True,
                    'stage': stage,
                })
                data = serialize_task(task)
                self.assertEqual(data['model_failure'], task.evidence_summary['diagnostic'])
                self.assertNotIn('SECRET', str(data['model_failure']))

    def test_published_browser_capture_is_shared_while_workspaces_stay_personal(self):
        task = self.task()
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner, status=APISpecification.TaskStatus.COMPLETED,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, source_task=task,
        )
        endpoint = APIEndpoint.objects.create(spec=spec, method='GET', path='/private-sample')
        create_request = self.factory.post('/', {
            'spec_id': spec.id, 'endpoint_ids': [endpoint.id], 'model_id': self.model.id,
        }, format='json')
        force_authenticate(create_request, user=self.other)
        create_reply = APIWorkspaceCollectionView.as_view()(create_request, project_id=self.project.id)
        self.assertEqual(create_reply.status_code, 201, create_reply.data)

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
        self.assertEqual(patch_reply.status_code, 200, patch_reply.data)
        self.assertEqual([item['id'] for item in generation_endpoint_specs(legacy_workspace)], [endpoint.id])

        list_request = self.factory.get('/')
        force_authenticate(list_request, user=self.other)
        list_reply = APIEndpointListView.as_view()(list_request, project_id=self.project.id, spec_id=spec.id)
        self.assertEqual(list_reply.status_code, 200, list_reply.data)
        detail_request = self.factory.get('/')
        force_authenticate(detail_request, user=self.other)
        detail_reply = APISpecificationRetrieveUpdateDestroyView.as_view()(detail_request, project_id=self.project.id, pk=spec.id)
        self.assertEqual(detail_reply.status_code, 200, detail_reply.data)

    @override_settings(API_BROWSER_DISCOVERY_ENABLED=True)
    def test_create_rejects_non_api_project_and_does_not_store_queue_exception_details(self):
        web_project = Project.objects.create(name='Not API', project_type='web', owner=self.owner, created_by=self.owner)
        ProjectMember.objects.create(
            project=web_project, user=self.owner, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
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

    def test_auto_origin_selection_survives_final_ingest_and_never_mixes_sources(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = self.task(
                api_origin='', status=BrowserDiscoveryTask.Status.PARTIAL,
                limits={'origin_mode': 'auto', 'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 20 * 1024 * 1024},
                evidence_summary={'usable': 0, 'invalid_lines': 2, 'over_limit': 1, 'truncated': 1, 'pending': 0},
            )
            origin_state_file(task).parent.mkdir(parents=True)
            origin_state_file(task).write_text(json.dumps({
                'version': 1,
                'resolved_origins': ['https://one.example.test', 'https://two.example.test'],
                'pending': [], 'rejected_origins': [],
            }), encoding='utf-8')
            for sequence, origin, complete in [
                (1, 'https://one.example.test', True),
                (2, 'https://two.example.test', False),
            ]:
                BrowserDiscoveryRecord.objects.create(
                    task=task, sequence=sequence, request_id=str(sequence), origin=origin, method='GET', path='/items',
                    resource_type='fetch', content_type='application/json', is_eligible=False,
                    exclusion_reason='origin_not_selected' if complete else 'capture_incomplete',
                    public_summary={'source_authorized': True, 'capture_complete': complete},
                )
            self.assertEqual(origin_resolution(task)['state'], 'awaiting_selection')
            self.assertEqual(selectable_origins(task), ['https://one.example.test'])
            # Only the fully captured collector source can be selected, even
            # though two origins were resolved by Node.
            request = self.factory.post('/', {
                'version': task.version, 'origin': 'https://one.example.test', 'decision': 'select',
            }, format='json')
            force_authenticate(request, self.owner)
            reply = BrowserDiscoveryOriginsView.as_view()(request, project_id=self.project.id, task_id=task.id)
            self.assertEqual(reply.status_code, 200, reply.data)
            task.refresh_from_db()
            self.assertEqual(task.api_origin, 'https://one.example.test')
            self.assertEqual(task.limits['selected_origin'], task.api_origin)
            self.assertEqual(task.evidence_summary['usable'], 1)
            self.assertEqual(task.evidence_summary['invalid_lines'], 2)
            self.assertEqual(task.evidence_summary['over_limit'], 1)
            self.assertEqual(task.evidence_summary['truncated'], 1)
            sync_auto_origin(task)
            task.refresh_from_db()
            self.assertEqual(task.api_origin, 'https://one.example.test')
            self.assertEqual(origin_resolution(task)['state'], 'resolved')
            self.assertTrue(task.records.get(sequence=1).is_eligible)
            self.assertFalse(task.records.get(sequence=2).is_eligible)

    def test_auto_origin_confirmation_writes_only_bounded_control_for_live_pending_candidate(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = self.task(
                api_origin='', status=BrowserDiscoveryTask.Status.RUNNING,
                limits={'origin_mode': 'auto', 'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 20 * 1024 * 1024},
            )
            origin_state_file(task).parent.mkdir(parents=True)
            origin_state_file(task).write_text(json.dumps({
                'version': 1, 'resolved_origins': [], 'rejected_origins': [],
                'pending': [{'origin': 'https://third.example.test', 'method': 'POST', 'path': '/items'}],
            }), encoding='utf-8')
            request = self.factory.post('/', {
                'version': task.version, 'origin': 'https://third.example.test', 'decision': 'approve',
            }, format='json')
            force_authenticate(request, self.owner)
            reply = BrowserDiscoveryOriginsView.as_view()(request, project_id=self.project.id, task_id=task.id)
            self.assertEqual(reply.status_code, 200, reply.data)
            control = json.loads(origin_control_file(task).read_text(encoding='utf-8'))
            self.assertEqual(control, {
                'version': 1, 'approved_origins': ['https://third.example.test'], 'rejected_origins': [], 'cancelled': False,
            })
            self.assertEqual(read_origin_state(task)['pending'][0]['path'], '/items')

    def test_auto_origin_management_events_are_not_invalid_and_complete_when_response_is_captured(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = self.task(
                api_origin='', status=BrowserDiscoveryTask.Status.RUNNING,
                limits={'origin_mode': 'auto', 'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 20 * 1024 * 1024},
            )
            origin_state_file(task).parent.mkdir(parents=True)
            origin_state_file(task).write_text(json.dumps({
                'version': 1, 'resolved_origins': ['https://shop.example.test'], 'pending': [], 'rejected_origins': [],
            }), encoding='utf-8')
            trace = task_trace_file(task)
            trace.write_text('\n'.join(json.dumps(item) for item in [
                self.event('origin_resolved', origin='https://shop.example.test'),
                self.event('origin_pending', origin='https://third.example.test', method='POST', path='/x'),
                self.event('origin_rejected', origin='https://third.example.test'),
                self.event('request', request_id='login', request_sequence=1, method='POST', origin='https://shop.example.test',
                           path='/login', url='https://shop.example.test/login', resource_type='fetch', capture_status='pending'),
                self.event('request_body', request_id='login', capture_status='captured', body={'kind': 'json', 'value': {'name': 'test'}, 'bytes': 15, 'truncated': False}),
                self.event('response', request_id='login', status=200, capture_status='captured', body={'kind': 'json', 'value': {'ok': True}, 'bytes': 11, 'truncated': False}),
            ]) + '\n', encoding='utf-8')
            result = ingest_trace(task)
            self.assertEqual(result['invalid'], 0)
            _finish_browser_discovery(str(task.id), task.version, task.task_id, {'completed': True, 'summary': '完成'})
            task.refresh_from_db()
            self.assertEqual(task.status, BrowserDiscoveryTask.Status.COMPLETED)
            self.assertEqual(task.api_origin, 'https://shop.example.test')

    def test_origin_decision_rejects_owner_and_state_failures_without_writing_control(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            def auto_task(**changes):
                values = {
                    'api_origin': '', 'status': BrowserDiscoveryTask.Status.RUNNING,
                    'limits': {'origin_mode': 'auto', 'max_requests': 500, 'max_body_bytes': 256 * 1024, 'max_total_bytes': 20 * 1024 * 1024},
                }
                values.update(changes)
                task = self.task(**values)
                origin_state_file(task).parent.mkdir(parents=True)
                origin_state_file(task).write_text(json.dumps({
                    'version': 1, 'resolved_origins': [], 'rejected_origins': [],
                    'pending': [{'origin': 'https://third.example.test', 'method': 'POST', 'path': '/items'}],
                }), encoding='utf-8')
                return task

            cases = [
                ('non_owner', auto_task(), self.other, 1, 'https://third.example.test'),
                ('wrong_version', auto_task(), self.owner, 99, 'https://third.example.test'),
                ('terminal', auto_task(status=BrowserDiscoveryTask.Status.PARTIAL), self.owner, 1, 'https://third.example.test'),
                ('cancelled', auto_task(cancellation_requested=True), self.owner, 1, 'https://third.example.test'),
                ('not_pending', auto_task(), self.owner, 1, 'https://other.example.test'),
                ('manual', self.task(api_origin='https://api.example.test', status=BrowserDiscoveryTask.Status.RUNNING), self.owner, 1, 'https://third.example.test'),
            ]
            manual = cases[-1][1]
            origin_state_file(manual).parent.mkdir(parents=True, exist_ok=True)
            origin_state_file(manual).write_text(json.dumps({
                'version': 1, 'resolved_origins': [], 'rejected_origins': [],
                'pending': [{'origin': 'https://third.example.test', 'method': 'POST', 'path': '/items'}],
            }), encoding='utf-8')
            no_execute = get_user_model().objects.create_user(username='browser-no-execute', email='browser-no-execute@example.test', password='pw')
            ProjectMember.objects.create(project=self.project, user=no_execute, role='editor', can_edit=True, can_execute_tests=False)
            no_execute_task = auto_task(owner=no_execute)
            cases.append(('no_execute', no_execute_task, no_execute, 1, 'https://third.example.test'))
            for name, task, user, version, origin in cases:
                with self.subTest(name=name):
                    request = self.factory.post('/', {
                        'version': task.version if version == 1 else version, 'origin': origin, 'decision': 'approve',
                    }, format='json')
                    force_authenticate(request, user)
                    reply = BrowserDiscoveryOriginsView.as_view()(request, project_id=self.project.id, task_id=task.id)
                    self.assertGreaterEqual(reply.status_code, 400, reply.data)
                    self.assertFalse(origin_control_file(task).exists())

    def test_cross_origin_same_path_or_dependency_cannot_be_handed_off_as_one_base_url(self):
        task = self.task(
            api_origin='https://one.example.test', status=BrowserDiscoveryTask.Status.PARTIAL,
            limits={'origin_mode': 'auto', 'selected_origin': 'https://one.example.test'},
        )
        auth = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, request_id='auth', origin='https://two.example.test', method='POST', path='/session',
            resource_type='fetch', is_eligible=True, public_summary={'source_authorized': True, 'capture_complete': True},
        )
        business = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, request_id='one', origin='https://one.example.test', method='GET', path='/items',
            resource_type='fetch', is_eligible=True, dependency_record_ids=[auth.id],
            public_summary={'source_authorized': True, 'capture_complete': True},
        )
        same_path_other = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=3, request_id='two', origin='https://two.example.test', method='GET', path='/items',
            resource_type='fetch', is_eligible=True, public_summary={'source_authorized': True, 'capture_complete': True},
        )
        with self.assertRaises(WorkspaceValidationError):
            _selection(task, [business.id])
        with self.assertRaises(WorkspaceValidationError):
            _selection(task, [business.id, same_path_other.id])
