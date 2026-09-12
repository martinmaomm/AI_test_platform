"""Offline contract tests for API Workspace B; no model, broker, or HTTP service is used."""
import sys
from copy import deepcopy
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Environment, Project, ProjectMember
from ai_core.models import LLMConfiguration
from .models import (
    APIEndpoint, APISpecification, APITestCase, APIWorkspace,
    BrowserDiscoveryTask, default_api_workspace_draft,
)
from .workspace_service import (
    endpoint_specs, generation_budget, normalize_draft, serialize_workspace,
)
from .workspace_tasks import _path_matches, _step_assertions, debug_api_workspace, generate_and_verify_api_workspace
from .workspace_verification import draft_hash, prepare_candidate, protected_expected_values
from .workspace_views import (
    APIWorkspaceCollectionView, APIWorkspaceDebugView, APIWorkspaceDetailView,
    APIWorkspaceMessagesView, APIWorkspaceSaveView,
)


class APIWorkspaceTests(TestCase):
    def test_endpoint_context_keeps_server_and_auth_metadata_without_entire_spec(self):
        from .models import APISpecification, APIEndpoint
        from .workspace_service import endpoint_specs
        spec = APISpecification.objects.create(project=self.project, created_by=self.user, metadata={
            'host': 'api.example.test', 'basePath': '/v1', 'schemes': ['https'],
            'securityDefinitions': {'key': {'type': 'apiKey', 'in': 'header', 'name': 'X-Api-Key'}},
            'security': [{'key': []}], 'paths': {'/health': {'get': {'security': []}}},
        })
        endpoint = APIEndpoint.objects.create(spec=spec, method='GET', path='/health')
        value = endpoint_specs(self.project.pk, [endpoint.pk])[0]['document_context']
        self.assertEqual(value['host'], 'api.example.test')
        self.assertEqual(value['basePath'], '/v1')
        self.assertEqual(value['security'], [])
        self.assertEqual(value['security_schemes']['key']['name'], 'X-Api-Key')
        self.assertNotIn('paths', value)

    def test_endpoint_specs_expand_local_schema_refs_without_external_fetch(self):
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            metadata={'definitions': {'Login': {'type': 'object', 'properties': {'email': {'type': 'string'}}}}},
        )
        endpoint = APIEndpoint.objects.create(
            spec=spec, method='POST', path='/login', request_body={'required': True, 'schema': {'$ref': '#/definitions/Login'}},
        )
        value = endpoint_specs(self.project.id, [endpoint.id], spec_id=spec.id)[0]
        self.assertEqual(value['request_body']['schema']['properties']['email']['type'], 'string')

    def test_endpoint_specs_preserve_recursive_and_external_refs_as_marked_context(self):
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            metadata={'definitions': {'Node': {'type': 'object', 'properties': {'children': {
                'type': 'array', 'items': {'$ref': '#/definitions/Node'},
            }}}}},
        )
        endpoint = APIEndpoint.objects.create(
            spec=spec, method='GET', path='/tree', request_body={'schema': {'$ref': '#/definitions/Node'}},
            responses={'200': {'schema': {'$ref': 'https://example.invalid/common.yaml#/Result'}}},
        )
        value = endpoint_specs(self.project.id, [endpoint.id], spec_id=spec.id)[0]
        self.assertEqual(value['request_body']['schema']['properties']['children']['items']['x-platform-ref-status'], 'circular')
        self.assertEqual(value['responses']['200']['schema']['x-platform-ref-status'], 'external')

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='workspace-owner', email='workspace-owner@example.test', password='pw')
        self.viewer = get_user_model().objects.create_user(username='workspace-viewer', email='workspace-viewer@example.test', password='pw')
        self.project = Project.objects.create(name='Workspace', project_type='api', owner=self.user, created_by=self.user)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer', can_edit=False)
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-model', created_by=self.user, is_active=True,
        )
        self.factory = APIRequestFactory()

    def request(self, user, method, path, payload=None):
        request = getattr(self.factory, method.lower())(path, payload or {}, format='json')
        force_authenticate(request, user=user)
        return request

    def workspace(self, **kwargs):
        values = {'project': self.project, 'owner': self.user, 'draft': default_api_workspace_draft()}
        values.update(kwargs)
        return APIWorkspace.objects.create(**values)

    def endpoint(self, project=None):
        project = project or self.project
        spec = APISpecification.objects.create(
            project=project, created_by=self.user, spec_name='Workspace API',
            status=APISpecification.TaskStatus.COMPLETED,
        )
        return APIEndpoint.objects.create(spec=spec, method='GET', path='/items', summary='items')

    def pipeline_workspace(self, *, draft=None, model_id=..., task_id='pipeline-task'):
        endpoint = self.endpoint()
        workspace = self.workspace(
            draft=draft or default_api_workspace_draft(), model_id=self.model.id if model_id is ... else model_id,
            spec=endpoint.spec, endpoint_ids=[endpoint.id], status='generating', task_id=task_id,
        )
        budget = generation_budget(model_id=workspace.model_id, owner=workspace.owner)
        workspace.generation = {
            'status': 'queued', 'phase': 'queued', 'attempt': 0, 'max_attempts': 3,
            'source_revision': 0, 'target_url': 'https://example.test', 'rounds': [], 'adopted_revision': None,
            **deepcopy(budget),
            '_snapshot': {
                'revision': 0, 'task_id': task_id, 'mode': 'generate', 'draft': workspace.draft,
                'model_id': workspace.model_id, 'spec_id': endpoint.spec_id,
                'endpoints': endpoint_specs(self.project.id, [endpoint.id], spec_id=endpoint.spec_id),
                'target_url': 'https://example.test', 'variables': {}, 'messages': [],
                'user_draft': workspace.draft, **deepcopy(budget),
            },
        }
        workspace.save(update_fields=['generation', 'updated_at'])
        return workspace, endpoint

    @staticmethod
    def passed_result():
        return {
            'success': True, 'error_type': '', 'step_datas': [{
                'status': 'passed', 'validators': {'validate_extractor': [{'passed': True}]},
                'data': {'req_resps': [{'response': {'status_code': 200}}]},
            }],
        }

    def test_project_editor_and_workspace_owner_are_both_required(self):
        workspace = self.workspace()
        denied = APIWorkspaceDetailView.as_view()(
            self.request(self.viewer, 'get', '/'), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(denied.status_code, 403)
        other = get_user_model().objects.create_user(username='other-editor', email='other-editor@example.test', password='pw')
        ProjectMember.objects.create(project=self.project, user=other, role='editor', can_edit=True)
        hidden = APIWorkspaceDetailView.as_view()(
            self.request(other, 'get', '/'), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(hidden.status_code, 404)

    def test_workspace_source_fields_are_root_stable_and_list_filters_are_owner_scoped(self):
        document_spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='已处理文档',
            status=APISpecification.TaskStatus.COMPLETED,
        )
        task = BrowserDiscoveryTask.objects.create(
            project=self.project, owner=self.user, model_id=self.model.id,
            target_url='https://browser-source.example.test', description='已确认样本',
            task_id='workspace-source-browser-task',
        )
        browser_spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='浏览器样本',
            status=APISpecification.TaskStatus.COMPLETED,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, source_task=task,
        )
        no_spec = self.workspace(title='无规范工作区')
        document = self.workspace(title='文档工作区', spec=document_spec)
        browser = self.workspace(title='浏览器工作区', spec=browser_spec, generation={})
        child = self.workspace(parent=browser, spec=document_spec, title='浏览器子场景')

        all_workspaces = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'get', '/'), project_id=self.project.id,
        )
        self.assertEqual(all_workspaces.status_code, 200, all_workspaces.data)
        by_id = {item['id']: item for item in all_workspaces.data['data']}
        self.assertEqual(by_id[no_spec.id]['source_type'], 'document')
        self.assertEqual(by_id[no_spec.id]['source_name'], '')
        self.assertIsNone(by_id[no_spec.id]['source_task_id'])
        self.assertEqual(by_id[document.id]['source_type'], 'document')
        self.assertEqual(by_id[document.id]['source_name'], '已处理文档')
        self.assertIsNone(by_id[document.id]['source_task_id'])
        self.assertEqual(by_id[browser.id]['source_type'], 'browser_capture')
        self.assertEqual(by_id[browser.id]['source_name'], '浏览器样本')
        self.assertEqual(by_id[browser.id]['source_task_id'], str(task.id))
        self.assertEqual(by_id[browser.id]['scenarios'][0]['id'], child.id)
        self.assertEqual(by_id[browser.id]['scenarios'][0]['source_type'], 'browser_capture')
        self.assertEqual(by_id[browser.id]['scenarios'][0]['source_name'], '浏览器样本')

        detail = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'get', '/'), project_id=self.project.id, workspace_id=child.id,
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['source_type'], 'browser_capture')
        self.assertEqual(detail.data['data']['source_task_id'], str(task.id))

        spoofed = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {
                'revision': browser.revision, 'source_type': 'document',
                'source_name': '伪造来源', 'source_task_id': 'not-a-task',
            }), project_id=self.project.id, workspace_id=browser.id,
        )
        self.assertEqual(spoofed.status_code, 200, spoofed.data)
        self.assertEqual(spoofed.data['data']['source_type'], 'browser_capture')
        self.assertEqual(spoofed.data['data']['source_name'], '浏览器样本')
        self.assertEqual(spoofed.data['data']['source_task_id'], str(task.id))

        document_only = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'get', '/?source_type=document'), project_id=self.project.id,
        )
        self.assertEqual(document_only.status_code, 200, document_only.data)
        self.assertEqual({item['id'] for item in document_only.data['data']}, {no_spec.id, document.id})
        browser_only = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'get', '/?source_type=browser_capture'), project_id=self.project.id,
        )
        self.assertEqual(browser_only.status_code, 200, browser_only.data)
        self.assertEqual([item['id'] for item in browser_only.data['data']], [browser.id])
        invalid_filter = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'get', '/?source_type=unknown'), project_id=self.project.id,
        )
        self.assertEqual(invalid_filter.status_code, 400)

        editor = get_user_model().objects.create_user(
            username='workspace-source-editor', email='workspace-source-editor@example.test', password='pw',
        )
        ProjectMember.objects.create(project=self.project, user=editor, role='editor', can_edit=True)
        editor_workspace = APIWorkspace.objects.create(
            project=self.project, owner=editor, title='其他编辑者工作区', draft=default_api_workspace_draft(),
        )
        editor_list = APIWorkspaceCollectionView.as_view()(
            self.request(editor, 'get', '/'), project_id=self.project.id,
        )
        self.assertEqual(editor_list.status_code, 200, editor_list.data)
        self.assertEqual([item['id'] for item in editor_list.data['data']], [editor_workspace.id])

    def test_workspace_serialization_uses_prefetched_children_and_orders_fallback_queries(self):
        task = BrowserDiscoveryTask.objects.create(
            project=self.project, owner=self.user, model_id=self.model.id,
            target_url='https://serialization-source.example.test', description='已确认样本',
            task_id='workspace-serialization-browser-task',
        )
        browser_spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='浏览器样本',
            status=APISpecification.TaskStatus.COMPLETED,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, source_task=task,
        )
        document_spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='子场景旧文档',
            status=APISpecification.TaskStatus.COMPLETED,
        )
        root = self.workspace(spec=browser_spec)
        second = self.workspace(parent=root, spec=document_spec, scenario_order=2)
        first = self.workspace(parent=root, spec=document_spec, scenario_order=1)

        with self.assertNumQueries(1):
            fallback = serialize_workspace(root)
        self.assertEqual([item['id'] for item in fallback['scenarios']], [first.id, second.id])

        prefetched_root = APIWorkspace.objects.select_related('saved_case', 'spec').prefetch_related(
            Prefetch(
                'scenarios',
                queryset=APIWorkspace.objects.select_related('saved_case').order_by('scenario_order', 'id'),
            ),
        ).get(pk=root.id)
        with self.assertNumQueries(0):
            prefetched = serialize_workspace(prefetched_root)
        self.assertEqual([item['id'] for item in prefetched['scenarios']], [first.id, second.id])
        self.assertTrue(all(item['source_type'] == 'browser_capture' for item in prefetched['scenarios']))

    def test_patch_revision_conflict_does_not_overwrite_draft(self):
        workspace = self.workspace()
        first = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'draft': {
                'version': 1, 'config': {'name': 'v1', 'base_url': '', 'variables': {}, 'verify': True}, 'teststeps': [],
            }}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(first.status_code, 200, first.data)
        stale = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'draft': default_api_workspace_draft()}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(stale.status_code, 409)
        workspace.refresh_from_db()
        self.assertEqual(workspace.draft['config']['name'], 'v1')
        self.assertEqual(workspace.revision, 1)

    def test_create_and_patch_allow_null_model_for_an_empty_workspace(self):
        created = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'model_id': None}), project_id=self.project.id,
        )
        self.assertEqual(created.status_code, 201, created.data)
        workspace_id = created.data['data']['id']
        self.assertIsNone(created.data['data']['model_id'])

        updated = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': None}),
            project_id=self.project.id, workspace_id=workspace_id,
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertIsNone(updated.data['data']['model_id'])

        owned_created = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'model_id': self.model.id}), project_id=self.project.id,
        )
        self.assertEqual(owned_created.status_code, 201, owned_created.data)
        self.assertEqual(owned_created.data['data']['model_id'], self.model.id)
        owned_updated = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': self.model.id}),
            project_id=self.project.id, workspace_id=owned_created.data['data']['id'],
        )
        self.assertEqual(owned_updated.status_code, 200, owned_updated.data)
        self.assertEqual(owned_updated.data['data']['model_id'], self.model.id)

    def test_model_binding_and_generation_are_owner_scoped(self):
        other_model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='other-owner-model',
            created_by=self.viewer, is_active=True,
        )
        denied_create = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'model_id': other_model.id}), project_id=self.project.id,
        )
        self.assertEqual(denied_create.status_code, 400)

        workspace = self.workspace()
        denied_patch = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': other_model.id}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(denied_patch.status_code, 400)

        workspace.model_id = other_model.id
        workspace.save(update_fields=['model_id', 'updated_at'])
        with patch('api_testing.workspace_views.generate_and_verify_api_workspace.apply_async') as queue:
            denied_message = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查', 'execution_confirmed': True, 'base_url': 'https://example.test'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(denied_message.status_code, 400)
        queue.assert_not_called()

        workspace, _ = self.pipeline_workspace(model_id=other_model.id, task_id='other-owner-task')
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'other-owner-task'))
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()

    def test_generation_requires_an_explicit_owned_active_model(self):
        workspace = self.workspace()
        with patch('api_testing.workspace_views.generate_and_verify_api_workspace.apply_async') as queue:
            denied_message = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查', 'execution_confirmed': True, 'base_url': 'https://example.test'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(denied_message.status_code, 400)
        queue.assert_not_called()

        workspace, _ = self.pipeline_workspace(model_id=None, task_id='no-model-task')
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'no-model-task'))
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()

    def test_fake_llm_stores_candidate_without_replacing_draft(self):
        draft = default_api_workspace_draft()
        workspace, endpoint = self.pipeline_workspace(draft=draft, task_id='candidate-task')
        candidate = {
            'version': 1,
            'config': {'name': 'items', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': 'items', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'}, 'validate': [{'eq': ['status_code', 200]}]}],
        }
        manager = SimpleNamespace(stream_invoke=lambda messages, **kwargs: __import__('json').dumps(candidate))
        module = ModuleType('api_testing.requests_runner')
        module.requests_runner = lambda **kwargs: self.passed_result()
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager) as manager_factory:
            with patch.dict(sys.modules, {'api_testing.requests_runner': module}):
                result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'candidate-task'))
        self.assertEqual(result.result['status'], 'passed')
        manager_factory.assert_called_once_with(config_id=self.model.id)
        workspace.refresh_from_db()
        self.assertEqual(workspace.draft, draft)
        self.assertEqual(workspace.candidate['draft']['config']['base_url'], 'https://example.test')
        self.assertFalse(workspace.candidate['draft']['teststeps'][0]['request']['allow_redirects'])
        self.assertEqual(workspace.candidate['source_revision'], 0)
        adopted = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'draft': workspace.candidate['draft']}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(adopted.status_code, 200, adopted.data)
        workspace.refresh_from_db()
        self.assertIsNone(workspace.candidate)
        self.assertEqual(workspace.debug_revision, 1)

    def test_fake_llm_repair_rejects_candidate_that_removes_assertion(self):
        endpoint = self.endpoint()
        draft = {'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {}, 'verify': True}, 'teststeps': [
            {'name': 'existing', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'}, 'validate': [{'eq': ['status_code', 200]}]},
        ]}
        workspace, _ = self.pipeline_workspace(draft=draft, task_id='repair-task')
        manager = SimpleNamespace(stream_invoke=lambda messages, **kwargs: __import__('json').dumps(default_api_workspace_draft()))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager):
            generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'repair-task'))
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'failed')
        self.assertIsNone(workspace.candidate)
        self.assertEqual(workspace.draft, draft)

    def test_debug_uses_frozen_snapshot_and_returns_matching_revision(self):
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        workspace = self.workspace(
            status='debugging', task_id='debug-task', debug_revision=0,
            debug_snapshot={'revision': 0, 'draft': {
                **default_api_workspace_draft(),
                'teststeps': [{'name': 'health', 'request': {'method': 'GET', 'url': '/health'}}],
            }, 'environment': {'base_url': 'https://example.test'}, 'variables': {'token': 'frozen'},
                'budget': budget},
        )
        module = ModuleType('api_testing.requests_runner')
        called = {}

        def runner(**kwargs):
            called.update(kwargs)
            return {'success': False, 'steps': [{'status': 'failed'}]}

        module.requests_runner = runner
        with patch.dict(sys.modules, {'api_testing.requests_runner': module}):
            result = debug_api_workspace.apply(args=(workspace.id, 0, 'debug-task'))
        self.assertEqual(result.result['status'], 'ready')
        self.assertEqual(called['script_id'], f'workspace:{workspace.id}:r0')
        self.assertEqual(called['base_url'], 'https://example.test')
        self.assertEqual(called['options']['variables'], {'token': 'frozen'})
        workspace.refresh_from_db()
        self.assertEqual(workspace.debug_revision, 0)
        self.assertEqual(workspace.debug_result['success'], False)
        self.assertEqual(workspace.status, 'ready')
        self.assertEqual(workspace.debug_snapshot, {})

    def test_save_creates_case_then_detects_external_case_change(self):
        workspace = self.workspace(title='保存用例')
        workspace.draft['teststeps'] = [{'name': 'one', 'request': {'method': 'GET', 'url': '/health'}}]
        workspace.save(update_fields=['draft', 'updated_at'])
        created = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(created.status_code, 200, created.data)
        workspace.refresh_from_db()
        case = workspace.saved_case
        self.assertIsNotNone(case)
        case.description = 'another editor changed this'
        case.save(update_fields=['description', 'updated_at'])
        conflict = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(conflict.status_code, 409)

    def test_save_description_preserves_omitted_or_null_and_allows_explicit_clear(self):
        draft = default_api_workspace_draft()
        draft['teststeps'] = [{'name': 'one', 'request': {'method': 'GET', 'url': '/health'}}]
        workspace = self.workspace(draft=draft)

        created = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(created.data['data']['saved_case_description'], '')
        workspace.refresh_from_db()
        case = workspace.saved_case
        case.description = '保留的描述'
        case.save(update_fields=['description', 'updated_at'])
        workspace.saved_case_updated_at = case.updated_at
        workspace.save(update_fields=['saved_case_updated_at', 'updated_at'])

        omitted = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(omitted.status_code, 200, omitted.data)
        self.assertEqual(omitted.data['data']['saved_case_description'], '保留的描述')

        preserved_null = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0, 'description': None}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(preserved_null.status_code, 200, preserved_null.data)
        self.assertEqual(preserved_null.data['data']['saved_case_description'], '保留的描述')

        cleared = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0, 'description': ''}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertEqual(cleared.data['data']['saved_case_description'], '')

        invalid = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0, 'description': {'not': 'text'}}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_empty_draft_can_be_edited_but_not_saved_or_debugged(self):
        workspace = self.workspace()
        save = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        debug = APIWorkspaceDebugView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0, 'variables': {}}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(save.status_code, 400)
        self.assertEqual(debug.status_code, 400)

    def test_debug_requires_execute_permission_active_environment_and_frozen_variable_order(self):
        editor = get_user_model().objects.create_user(username='edit-only', email='edit-only@example.test', password='pw')
        ProjectMember.objects.create(project=self.project, user=editor, role='editor', can_edit=True, can_execute_tests=False)
        workspace = self.workspace(owner=editor)
        forbidden = APIWorkspaceDebugView.as_view()(
            self.request(editor, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(forbidden.status_code, 403)
        workspace = self.workspace()
        inactive = Environment.objects.create(
            project=self.project, name='inactive', category='api', is_active=False, config={'base_url': 'https://example.test'},
        )
        inactive_result = APIWorkspaceDebugView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0, 'environment_id': inactive.id}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(inactive_result.status_code, 404)
        workspace.draft['teststeps'] = [{'name': 'one', 'request': {'method': 'GET', 'url': '/health'}}]
        workspace.draft['config']['variables'] = {'shared': 'draft', 'draft_only': 'draft'}
        workspace.save(update_fields=['draft', 'updated_at'])
        active = Environment.objects.create(
            project=self.project, name='active', category='api', is_active=True,
            config={'base_url': 'https://example.test', 'variables': {'shared': 'environment', 'env_only': 'environment'}},
        )
        with patch('api_testing.workspace_views.debug_api_workspace.apply_async'):
            queued = APIWorkspaceDebugView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 0, 'environment_id': active.id,
                    'variables': {'shared': 'request', 'request_only': 'request'},
                }), project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(queued.status_code, 202, queued.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.debug_snapshot['variables'], {
            'shared': 'request', 'env_only': 'environment', 'draft_only': 'draft', 'request_only': 'request',
        })

    def test_duplicate_debug_delivery_is_claimed_before_requests_runner(self):
        from .workspace_tasks import _claim_debug
        workspace = self.workspace(
            status='debugging', task_id='claimed-task', debug_revision=0,
            debug_snapshot={'revision': 0, 'draft': default_api_workspace_draft(), 'environment': {}, 'variables': {},
                            'budget': generation_budget(model_id=self.model.id, owner=self.user)},
        )
        self.assertIsNotNone(_claim_debug(workspace_id=workspace.id, revision=0, task_id='claimed-task'))
        module = ModuleType('api_testing.requests_runner')
        module.requests_runner = lambda **kwargs: self.fail('duplicate delivery must not issue HTTP')
        with patch.dict(sys.modules, {'api_testing.requests_runner': module}):
            result = debug_api_workspace.apply(args=(workspace.id, 0, 'claimed-task'))
        self.assertEqual(result.result['status'], 'stale')

    def test_polling_expires_lost_debug_without_allowing_old_worker_writeback(self):
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        budget['deadlines']['queue_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        workspace = self.workspace(
            status='debugging', task_id='lost-task', debug_revision=0,
            debug_snapshot={'revision': 0, 'draft': default_api_workspace_draft(), 'environment': {}, 'variables': {},
                            'budget': budget},
        )
        detail = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'get', '/'), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['status'], 'failed')
        self.assertIn('未自动重试', detail.data['data']['error'])
        module = ModuleType('api_testing.requests_runner')
        module.requests_runner = lambda **kwargs: self.fail('expired task must not issue HTTP')
        with patch.dict(sys.modules, {'api_testing.requests_runner': module}):
            result = debug_api_workspace.apply(args=(workspace.id, 0, 'lost-task'))
        self.assertEqual(result.result['status'], 'stale')

    def test_disabled_model_cannot_be_bound_or_used_for_generation(self):
        workspace = self.workspace(model_id=self.model.id)
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        update = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': self.model.id}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(update.status_code, 400)

        with patch('api_testing.workspace_views.generate_and_verify_api_workspace.apply_async') as queue:
            generate = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查', 'execution_confirmed': True, 'base_url': 'https://example.test'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(generate.status_code, 400)
        queue.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.messages, [])

    def test_model_disabled_after_enqueue_never_contacts_provider(self):
        workspace, _ = self.pipeline_workspace(task_id='disabled-task')
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'disabled-task'))
        manager.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'failed')
        self.assertIn('禁用', workspace.error)
        self.assertIsNone(workspace.candidate)

    def test_model_owner_change_after_enqueue_never_contacts_provider(self):
        workspace, _ = self.pipeline_workspace(task_id='owner-changed-task')
        self.model.created_by = self.viewer
        self.model.save(update_fields=['created_by'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'owner-changed-task'))
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'failed')
        self.assertIn('不属于', workspace.error)

    def test_normalization_rejects_runtime_unsupported_fields_before_persisting(self):
        workspace = self.workspace()
        invalid = default_api_workspace_draft()
        invalid['functions'] = {'unsafe': 'python'}
        update = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'draft': invalid}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(update.status_code, 400)
        workspace.refresh_from_db()
        self.assertEqual(workspace.draft, default_api_workspace_draft())

    def test_busy_workspace_rejects_patch_and_save(self):
        draft = default_api_workspace_draft()
        draft['teststeps'] = [{'name': 'health', 'request': {'method': 'GET', 'url': '/health'}}]
        workspace = self.workspace(draft=draft, status='debugging', task_id='running')
        update = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'draft': draft}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        save = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(update.status_code, 409)
        self.assertEqual(save.status_code, 409)
        workspace.refresh_from_db()
        self.assertEqual(workspace.task_id, 'running')
        self.assertEqual(workspace.status, 'debugging')

    def test_repair_assertions_remain_attached_to_each_duplicate_endpoint_step(self):
        baseline = normalize_draft({
            'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [
                {'name': 'first', 'endpoint_id': 9, 'request': {'method': 'GET', 'url': '/items'}, 'validate': [{'eq': ['status_code', 200]}]},
                {'name': 'second', 'endpoint_id': 9, 'request': {'method': 'GET', 'url': '/items'}, 'validate': [{'contains': ['body.kind', 'next']}]},
            ],
        })
        checks = _step_assertions(baseline)
        self.assertEqual(len(checks), 2)
        self.assertNotEqual(*checks.values())
        self.assertTrue(_path_matches('/foo.json', '/foo.json'))
        self.assertFalse(_path_matches('/foo.json', '/fooXjson'))
        self.assertTrue(_path_matches('/users/{id}', '/users/42'))

    def test_repair_candidate_cannot_shadow_a_baseline_expected_variable(self):
        endpoint = self.endpoint()
        baseline = normalize_draft({
            'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {'expected': 200}, 'verify': True},
            'teststeps': [{'name': 'items', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'},
                           'validate': [{'eq': ['status_code', '${expected}']}]}],
        })
        unsafe = deepcopy(baseline)
        unsafe['teststeps'][0]['extract'] = {'expected': 'status_code'}
        with self.assertRaisesRegex(ValueError, '断言'):
            prepare_candidate(
                unsafe, endpoints=endpoint_specs(self.project.id, [endpoint.id], spec_id=endpoint.spec_id),
                target_url='https://example.test', variables={}, baseline=_step_assertions(baseline),
                protected=protected_expected_values(baseline),
            )

    def test_security_required_target_rejects_repair_that_deletes_authorization(self):
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            metadata={
                'security': [{'bearerAuth': []}],
                'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}},
            },
        )
        endpoint = APIEndpoint.objects.create(spec=spec, method='GET', path='/admin/info')
        baseline = normalize_draft({
            'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {'token': 'issued'}, 'verify': True},
            'teststeps': [{'name': 'info', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/admin/info', 'headers': {'Authorization': 'Bearer ${token}'}},
                'validate': [{'eq': ['body.code', 0]}]}],
        })
        unsafe = deepcopy(baseline)
        unsafe['teststeps'][0]['request']['headers'] = {}
        with self.assertRaisesRegex(ValueError, 'security'):
            prepare_candidate(
                unsafe, endpoints=endpoint_specs(self.project.id, [endpoint.id], spec_id=spec.id),
                target_url='https://example.test', variables={'token': 'issued'},
                baseline=_step_assertions(baseline), protected=protected_expected_values(baseline),
                authenticated_target_ids={endpoint.id},
            )

    def test_child_uses_frozen_root_scope_for_self_contained_login_without_widening_targets(self):
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            metadata={
                'security': [{'bearerAuth': []}],
                'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}},
            },
        )
        login = APIEndpoint.objects.create(spec=spec, method='POST', path='/session', request_body={'required': True})
        info = APIEndpoint.objects.create(spec=spec, method='GET', path='/admin/info')
        root = self.workspace(model_id=self.model.id, spec=spec, endpoint_ids=[login.id, info.id])
        plan = {'summary': '当前用户', 'scenarios': [{
            'title': '读取当前用户', 'description': '独立登录后读取', 'endpoint_ids': [info.id],
            'dependency_endpoint_ids': [login.id], 'dependency_evidence': '目标 security 要求 bearerAuth；登录响应提供 token。',
            'requires_authenticated_context': True,
        }]}
        candidate = {
            'version': 1, 'config': {'name': '当前用户', 'variables': {'username': 'u', 'password': 'p'}},
            'teststeps': [
                {'name': '建立会话', 'endpoint_id': login.id, 'request': {'method': 'POST', 'url': '/session', 'json': {'username': '${username}', 'password': '${password}'}},
                 'extract': {'token': 'body.data.token'}, 'validate': [{'eq': ['status_code', 200]}]},
                {'name': '读取当前用户', 'endpoint_id': info.id,
                 'request': {'method': 'GET', 'url': '/admin/info', 'headers': {'Authorization': 'Bearer ${token}'}},
                 'validate': [{'eq': ['body.code', 0]}]},
            ],
        }
        prompts = []

        def stream(messages, callback=None, **_kwargs):
            payload = next(__import__('json').loads(item.content) for item in messages if item.content.startswith('{'))
            prompts.append(payload)
            value = plan if payload.get('stage') == 'plan' else candidate
            output = __import__('json').dumps(value)
            if callback:
                callback(output)
            return output

        passed = {
            'success': True, 'error_type': '', 'step_datas': [
                {'status': 'passed', 'validators': {'validate_extractor': [{'passed': True}]}, 'data': {'req_resps': [{'response': {'status_code': 200}}]}},
                {'status': 'passed', 'validators': {'validate_extractor': [{'passed': True}]}, 'data': {'req_resps': [{'response': {'status_code': 200}}]}},
            ],
        }
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '读取当前登录用户',
                    'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {}}),
                project_id=self.project.id, workspace_id=root.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=stream)), \
             patch('api_testing.requests_runner.requests_runner', return_value=passed) as runner:
            result = generate_and_verify_api_workspace.apply(**queued.call_args.kwargs)
        self.assertEqual(result.result['status'], 'passed')
        child = root.scenarios.get()
        self.assertEqual(child.endpoint_ids, [info.id])
        self.assertEqual([item['id'] for item in child.generation['_snapshot']['endpoints']], [login.id, info.id])
        self.assertEqual(child.generation['scenario_context']['available_endpoint_ids'], [login.id, info.id])
        self.assertEqual([item['endpoint_id'] for item in child.candidate['draft']['teststeps']], [login.id, info.id])
        self.assertEqual(prompts[1]['current_scenario']['target_endpoint_ids'], [info.id])
        runner.assert_called_once()

    def mixed_authentication_child(self):
        from .workspace_tasks import _parse_plan, _scenario_children
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            metadata={'security': [{'bearer': []}], 'components': {'securitySchemes': {
                'bearer': {'type': 'http', 'scheme': 'bearer'},
            }}},
        )
        # Deliberately inherit global security, including the credential issuer;
        # neither URL matching nor a document override can make this test pass.
        endpoints = [APIEndpoint.objects.create(spec=spec, method=method, path=path) for method, path in (
            ('POST', '/flow/start'), ('GET', '/flow/current'), ('POST', '/flow/end'),
        )]
        ids = [endpoint.id for endpoint in endpoints]
        root = self.workspace(
            model_id=self.model.id, spec=spec, endpoint_ids=ids,
            status='generating', task_id='mixed-auth-root',
        )
        plan = _parse_plan({'scenarios': [{
            'title': 'Credential lifecycle', 'endpoint_ids': ids,
            'authenticated_endpoint_ids': ids[1:], 'requires_authenticated_context': True,
        }]}, endpoint_ids=set(ids))
        budget = generation_budget(model_id=root.model_id, owner=root.owner)
        budget['claimed_at'] = timezone.now().isoformat()
        budget['started_at'] = budget['claimed_at']
        budget['deadlines']['execution_at'] = None
        budget['deadlines']['batch_at'] = (
            timezone.now() + timedelta(seconds=budget['timeouts']['batch_seconds'])
        ).isoformat()
        _scenario_children(root_id=root.id, revision=0, task_id=root.task_id, plan=plan, snapshot={
            'target_url': 'https://example.test', 'variables': {}, 'queued_at': timezone.now().isoformat(),
            'scope_endpoint_ids': ids, 'endpoints': endpoint_specs(self.project.id, ids, spec_id=spec.id),
            **budget,
        })
        draft = {'version': 1, 'config': {'name': 'Credential lifecycle', 'variables': {}}, 'teststeps': [
            {'endpoint_id': endpoint.id, 'request': {'method': endpoint.method, 'url': endpoint.path},
             'extract': {}, 'validate': [{'eq': ['status_code', 200]}]}
            for endpoint in endpoints
        ]}
        draft['teststeps'][0]['extract'] = {'credential': 'body.credential'}
        for step in draft['teststeps'][1:]:
            step['request']['headers'] = {'Authorization': 'Bearer ${credential}'}
        return root, root.scenarios.get(), draft

    def test_mixed_authentication_targets_keep_issuer_and_enforce_only_frozen_subset(self):
        root, child, draft = self.mixed_authentication_child()
        manager = SimpleNamespace(stream_invoke=Mock(return_value=__import__('json').dumps(draft)))
        passed = self.passed_result()
        passed['step_datas'] *= 3
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
             patch('api_testing.requests_runner.requests_runner', return_value=passed) as runner:
            result = generate_and_verify_api_workspace.apply(args=(child.id, child.revision, child.task_id))
        self.assertEqual(result.result['status'], 'passed')
        child.refresh_from_db()
        self.assertEqual(child.endpoint_ids, root.endpoint_ids)
        self.assertEqual([step['endpoint_id'] for step in child.candidate['draft']['teststeps']], root.endpoint_ids)
        for context in (child.generation['_snapshot']['scenario'], child.generation['scenario_context']):
            self.assertEqual(context['authenticated_endpoint_ids'], root.endpoint_ids[1:])
            self.assertEqual(context['target_endpoint_ids'], root.endpoint_ids)
        manager.stream_invoke.assert_called_once()
        runner.assert_called_once()

    def test_automatic_repair_cannot_downgrade_frozen_authentication_subset(self):
        root, child, draft = self.mixed_authentication_child()
        downgrade = deepcopy(draft)
        downgrade.update(authenticated_endpoint_ids=[], requires_authenticated_context=False)
        for step in downgrade['teststeps'][1:]:
            step['request']['headers'] = {}
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=[
            __import__('json').dumps(value) for value in (draft, downgrade, downgrade)
        ]))
        failed = {'success': False, 'error_type': 'ExtractionFailure', 'step_datas': []}
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
             patch('api_testing.requests_runner.requests_runner', return_value=failed) as runner:
            result = generate_and_verify_api_workspace.apply(args=(child.id, child.revision, child.task_id))
        self.assertEqual(result.result['status'], 'needs_review')
        runner.assert_called_once()
        self.assertEqual(manager.stream_invoke.call_count, 3)
        for call in manager.stream_invoke.call_args_list:
            payload = __import__('json').loads(call.args[0][1].content)
            self.assertEqual(payload['current_scenario']['authenticated_endpoint_ids'], root.endpoint_ids[1:])
        child.refresh_from_db()
        self.assertEqual(child.generation['_snapshot']['scenario']['authenticated_endpoint_ids'], root.endpoint_ids[1:])
        for attempt in child.generation['rounds'][1:]:
            self.assertFalse(attempt['runnable'])
            self.assertIn('security', attempt['summary'])

    def test_manual_child_repair_preserves_snapshot_authentication_over_draft_or_request(self):
        root, child, draft = self.mixed_authentication_child()
        root.status = 'ready'
        root.save(update_fields=['status', 'updated_at'])
        # Editable draft metadata and request fields are not authentication authority.
        draft.update(authenticated_endpoint_ids=[], requires_authenticated_context=False)
        child.draft = draft
        child.status = 'ready'
        child.generation['status'] = 'needs_review'
        child.debug_revision = child.revision
        child.debug_result = {'success': False, 'error_type': 'ExtractionFailure', 'step_datas': []}
        child.save()
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': child.revision, 'mode': 'repair', 'message': 'Fix extraction only',
                    'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {},
                    'authenticated_endpoint_ids': [], 'requires_authenticated_context': False,
                }), project_id=self.project.id, workspace_id=child.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        queued.assert_called_once()
        child.refresh_from_db()
        for context in (child.generation['_snapshot']['scenario'], child.generation['scenario_context']):
            self.assertEqual(context['authenticated_endpoint_ids'], root.endpoint_ids[1:])
            self.assertEqual(context['target_endpoint_ids'], root.endpoint_ids)
            self.assertTrue(context['requires_authenticated_context'])

    def test_candidate_data_without_content_type_is_rejected_when_spec_only_accepts_json(self):
        endpoint = self.endpoint()
        endpoint.request_body = {'content': {'application/json': {'schema': {'type': 'object'}}}}
        endpoint.save(update_fields=['request_body'])
        draft = {
            'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': 'items', 'endpoint_id': endpoint.id,
                           'request': {'method': 'GET', 'url': '/items', 'data': {'x': '1'}},
                           'validate': [{'eq': ['status_code', 200]}]}],
        }
        with self.assertRaisesRegex(ValueError, '媒体类型'):
            prepare_candidate(
                draft, endpoints=endpoint_specs(self.project.id, [endpoint.id], spec_id=endpoint.spec_id),
                target_url='https://example.test', variables={},
            )

    def test_repair_freezes_current_candidate_and_failed_server_result(self):
        endpoint = self.endpoint()
        candidate = normalize_draft({
            'version': 1, 'config': {'name': 'candidate', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': 'items', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'},
                           'validate': [{'eq': ['status_code', 200]}]}],
        })
        result = {'success': False, 'error_type': 'ExtractionFailure', 'step_datas': []}
        candidate_hash = draft_hash(candidate)
        workspace = self.workspace(
            model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id], status='ready',
            candidate={'draft': candidate, 'source_revision': 0, 'draft_hash': candidate_hash, 'verification_status': 'needs_review'},
            generation={'status': 'needs_review', 'source_revision': 0,
                        'rounds': [{'draft_hash': candidate_hash, 'result': result}]},
        )
        with patch('api_testing.workspace_views.generate_and_verify_api_workspace.apply_async'):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 0, 'message': '修复提取路径', 'mode': 'repair', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(reply.status_code, 202, reply.data)
        workspace.refresh_from_db()
        snapshot = workspace.generation['_snapshot']
        self.assertEqual(snapshot['draft'], candidate)
        self.assertEqual(snapshot['user_draft'], workspace.draft)
        self.assertNotEqual(snapshot['user_draft'], snapshot['draft'])
        self.assertEqual(snapshot['failure_evidence'], result)

    def test_expired_pipeline_does_not_contact_model_or_target(self):
        workspace, _ = self.pipeline_workspace(task_id='expired-pipeline')
        workspace.generation['_snapshot']['deadlines']['queue_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        workspace.save(update_fields=['generation', 'updated_at'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'expired-pipeline'))
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.generation['status'], 'failed')

    def test_revoked_edit_permission_stops_queued_generation(self):
        workspace, _ = self.pipeline_workspace(task_id='edit-permission-revoked')
        with patch('api_testing.workspace_tasks.can_edit_project', return_value=False), \
             patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'edit-permission-revoked'))
        manager.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.generation['status'], 'failed')

    def test_polling_uses_total_deadline_despite_recent_heartbeat_and_cannot_revive(self):
        from .workspace_service import expire_stalled_workspace
        from .workspace_tasks import _finish_pipeline
        workspace, _ = self.pipeline_workspace(task_id='heartbeat-expired')
        workspace.generation['_snapshot']['deadlines']['queue_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        workspace.save(update_fields=['generation', 'updated_at'])
        expired = expire_stalled_workspace(workspace)
        self.assertEqual(expired.status, 'failed')
        self.assertEqual(expired.generation['status'], 'failed')
        _finish_pipeline(workspace.id, 0, 'heartbeat-expired', 'passed', 'late worker result')
        workspace.refresh_from_db()
        self.assertEqual(workspace.generation['status'], 'failed')

    def test_invalid_candidates_stop_after_three_bounded_attempts(self):
        workspace, _ = self.pipeline_workspace(task_id='retry-task')
        manager = SimpleNamespace(stream_invoke=Mock(return_value='not json'))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager):
            result = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'retry-task'))
        self.assertEqual(result.result['status'], 'failed')
        self.assertEqual(manager.stream_invoke.call_count, 3)

    def test_single_endpoint_root_generate_always_queues_scenario_planning(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id])
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': root.revision, 'message': '为这个接口覆盖正向和边界场景',
                    'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=root.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        root.refresh_from_db()
        self.assertEqual(root.generation['_snapshot']['workflow'], 'scenarios')
        self.assertEqual(root.generation['_snapshot']['scope_endpoint_ids'], [endpoint.id])
        self.assertEqual(queued.call_args.kwargs['args'][:2], (root.id, root.revision))

    def test_planner_keeps_invalid_raw_plan_then_repairs_once_before_children(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id])
        invalid_plan = {'summary': '坏计划', 'scenarios': []}
        valid_plan = {'summary': '修正后的计划', 'scenarios': [
            {'title': '读取项目', 'description': '正向读取', 'endpoint_ids': [endpoint.id], 'requires_authenticated_context': False},
        ]}
        candidate = {
            'version': 1, 'config': {'name': '读取项目', 'variables': {}},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}]}],
        }
        outputs, prompts = [invalid_plan, valid_plan, candidate], []

        def stream(messages, callback=None, **_kwargs):
            payload = next(__import__('json').loads(item.content) for item in messages if item.content.startswith('{'))
            prompts.append(payload)
            output = __import__('json').dumps(outputs.pop(0))
            if callback:
                callback(output)
            return output

        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 0, 'message': '规划一个读取场景', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=root.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=stream)), \
             patch('api_testing.requests_runner.requests_runner', return_value=self.passed_result()):
            result = generate_and_verify_api_workspace.apply(**queued.call_args.kwargs)
        self.assertEqual(result.result['status'], 'passed')
        root.refresh_from_db()
        self.assertEqual(root.generation['planning_attempts'][0]['raw'], invalid_plan)
        self.assertEqual(root.generation['plan'], {
            **valid_plan,
            'scenarios': [{
                **valid_plan['scenarios'][0], 'dependency_endpoint_ids': [],
                'dependency_evidence': '', 'requires_authenticated_context': False, 'authenticated_endpoint_ids': [],
            }],
        })
        self.assertEqual(prompts[0]['stage'], 'plan')
        self.assertEqual(prompts[1]['stage'], 'plan')
        self.assertEqual(prompts[1]['failure_evidence']['raw_plan'], invalid_plan)
        self.assertEqual(root.scenarios.count(), 1)

    def test_root_model_revocation_terminates_unstarted_children_without_http(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id])
        plan = {'summary': '两个独立场景', 'scenarios': [
            {'title': '第一场景', 'description': '会在执行前撤权', 'endpoint_ids': [endpoint.id]},
            {'title': '第二场景', 'description': '不得开始', 'endpoint_ids': [endpoint.id]},
        ]}
        candidate = {
            'version': 1, 'config': {'name': '候选', 'variables': {}},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}]}],
        }
        calls = 0

        def stream(_messages, callback=None, **_kwargs):
            nonlocal calls
            calls += 1
            value = plan if calls == 1 else candidate
            if callback:
                callback(__import__('json').dumps(value))
            if calls == 2:
                self.model.is_active = False
                self.model.save(update_fields=['is_active'])
            return __import__('json').dumps(value)

        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 0, 'message': '建立两个场景', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=root.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=stream)), \
             patch('api_testing.requests_runner.requests_runner') as runner:
            generate_and_verify_api_workspace.apply(**queued.call_args.kwargs)
        root.refresh_from_db()
        self.assertEqual(root.generation['status'], 'failed')
        self.assertEqual(calls, 2)
        self.assertFalse(runner.called)
        self.assertEqual(
            list(root.scenarios.order_by('scenario_order').values_list('generation__status', flat=True)),
            ['failed', 'failed'],
        )

    def test_parent_lease_change_during_child_callback_cleans_old_children_without_http(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id])
        plan = {'summary': '两个场景', 'scenarios': [
            {'title': '第一', 'description': '', 'endpoint_ids': [endpoint.id]},
            {'title': '第二', 'description': '', 'endpoint_ids': [endpoint.id]},
        ]}
        candidate = {
            'version': 1, 'config': {'name': '候选', 'variables': {}},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}]}],
        }
        calls = 0

        def stream(_messages, callback=None, **_kwargs):
            nonlocal calls
            calls += 1
            value = plan if calls == 1 else candidate
            if calls == 2:
                root.task_id = 'replacement-root-task'
                root.revision = 1
                root.save(update_fields=['task_id', 'revision', 'updated_at'])
            if callback:
                callback(__import__('json').dumps(value))
            return __import__('json').dumps(value)

        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 0, 'message': '建立两个场景', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=root.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=stream)), \
             patch('api_testing.requests_runner.requests_runner') as runner:
            result = generate_and_verify_api_workspace.apply(**queued.call_args.kwargs)
        self.assertEqual(result.result['status'], 'stale')
        root.refresh_from_db()
        self.assertEqual((root.task_id, root.revision), ('replacement-root-task', 1))
        self.assertFalse(runner.called)
        self.assertEqual(
            list(root.scenarios.order_by('scenario_order').values_list('status', flat=True)),
            ['failed', 'failed'],
        )

    def test_expired_root_terminates_generating_children(self):
        from .workspace_service import expire_stalled_workspace
        root, endpoint = self.pipeline_workspace(task_id='expired-root')
        child = self.workspace(
            parent=root, model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id],
            status='generating', task_id='expired-child',
            generation={'status': 'running', 'phase': 'generating', '_snapshot': {
                'revision': 0, 'task_id': 'expired-child', 'parent_task_id': 'expired-root',
                'parent_revision': 0, 'queue_managed_by_parent': True,
                'timeouts': deepcopy(root.generation['_snapshot']['timeouts']),
                'deadlines': {
                    'queue_at': None, 'execution_at': None,
                    'batch_at': (timezone.now() - timedelta(seconds=1)).isoformat(),
                },
            }},
        )
        root.generation['_snapshot']['deadlines']['queue_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        root.save(update_fields=['generation', 'updated_at'])
        expire_stalled_workspace(root)
        child.refresh_from_db()
        self.assertEqual(child.status, 'failed')
        self.assertEqual(child.generation['status'], 'failed')

    def test_root_or_sibling_busy_blocks_queue_and_delete(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id])
        self.workspace(parent=root, status='debugging', task_id='sibling-debug')
        queued = APIWorkspaceMessagesView.as_view()(
            self.request(self.user, 'post', '/', {
                'revision': 0, 'message': '不得并发', 'execution_confirmed': True,
                'base_url': 'https://example.test', 'variables': {},
            }), project_id=self.project.id, workspace_id=root.id,
        )
        deleted = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'delete', '/', {'revision': 0, 'confirmed': True}),
            project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(queued.status_code, 409, queued.data)
        self.assertEqual(deleted.status_code, 409, deleted.data)

    def test_root_rename_and_delete_are_owned_and_preserve_saved_cases(self):
        endpoint = self.endpoint()
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id], title='根标题')
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='已保存用例', test_case_type='endpoint', endpoint=endpoint,
            script_content=__import__('json').dumps(default_api_workspace_draft()),
        )
        self.workspace(parent=root, saved_case=case, saved_case_updated_at=case.updated_at)
        renamed = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'title': '用户根标题'}),
            project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(renamed.status_code, 200, renamed.data)
        root.refresh_from_db()
        self.assertEqual((root.title, root.revision), ('用户根标题', 0))
        stranger = get_user_model().objects.create_user(username='workspace-delete-stranger', email='workspace-delete-stranger@example.test')
        forbidden = APIWorkspaceDetailView.as_view()(
            self.request(stranger, 'delete', '/', {'revision': 0, 'confirmed': True}),
            project_id=self.project.id, workspace_id=root.id,
        )
        self.assertIn(forbidden.status_code, {403, 404})
        deleted = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'delete', '/', {'revision': 0, 'confirmed': True}),
            project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.assertTrue(APITestCase.objects.filter(pk=case.id).exists())

    def test_saving_existing_case_keeps_case_title_and_workspace_title_independent(self):
        endpoint = self.endpoint()
        draft = {
            'version': 1, 'config': {'name': '草稿名称', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'}}],
        }
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='正式用例名称', test_case_type='endpoint', endpoint=endpoint,
            script_content=__import__('json').dumps(draft),
        )
        workspace = self.workspace(
            title='独立工作区标题', saved_case=case, saved_case_updated_at=case.updated_at,
            spec=endpoint.spec, endpoint_ids=[endpoint.id], draft=draft,
        )
        saved = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(saved.status_code, 200, saved.data)
        workspace.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(workspace.title, '独立工作区标题')
        self.assertEqual(case.title, '正式用例名称')
        self.assertEqual(saved.data['data']['saved_case_title'], '正式用例名称')

    def test_child_model_only_change_rebinds_current_failed_evidence_for_repair(self):
        endpoint = self.endpoint()
        replacement = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='replacement-offline-model',
            created_by=self.user, is_active=True,
        )
        draft = {
            'version': 1, 'config': {'name': '失败场景', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}]}],
        }
        candidate_hash = draft_hash(draft)
        failed_result = {'success': False, 'error_type': 'ValidationFailure', 'step_datas': []}
        root = self.workspace(spec=endpoint.spec, endpoint_ids=[endpoint.id])
        root.generation = {'_snapshot': {'scope_endpoint_ids': [endpoint.id]}}
        root.save(update_fields=['generation', 'updated_at'])
        child = self.workspace(
            parent=root, title='冻结子场景', scenario_description='保留登录要求',
            model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id], draft=draft,
            messages=[{'role': 'user', 'content': '使用固定账号并验证失败原因'}],
            candidate={'draft': draft, 'summary': '失败', 'risks': [], 'source_revision': 0,
                'mode': 'generate', 'verification_status': 'needs_review', 'draft_hash': candidate_hash},
            generation={'status': 'needs_review', 'phase': 'finished', 'source_revision': 0,
                'rounds': [{'draft_hash': candidate_hash, 'draft': draft, 'result': failed_result}],
                '_snapshot': {
                    'model_id': self.model.id, 'historical': True, 'scope_endpoint_ids': [endpoint.id],
                    'scenario': {'target_endpoint_ids': [endpoint.id], 'available_endpoint_ids': [endpoint.id],
                                 'requires_authenticated_context': False},
                }},
            debug_result=failed_result, debug_revision=0,
        )
        updated = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': replacement.id, 'title': '改模型后的标题'}),
            project_id=self.project.id, workspace_id=child.id,
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        child.refresh_from_db()
        self.assertEqual(child.revision, 1)
        self.assertEqual(child.generation['status'], 'needs_review')
        self.assertEqual(child.generation['source_revision'], 1)
        self.assertEqual(child.generation['_snapshot']['model_id'], self.model.id)
        self.assertEqual(child.candidate['source_revision'], 1)
        self.assertEqual(child.debug_revision, 1)
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            repair = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {
                    'revision': 1, 'mode': 'repair', 'message': '仅修复失败原因',
                    'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=child.id,
            )
        self.assertEqual(repair.status_code, 202, repair.data)
        child.refresh_from_db()
        snapshot = child.generation['_snapshot']
        self.assertEqual(snapshot['model_id'], replacement.id)
        self.assertEqual(snapshot['scenario'], {
            'title': '改模型后的标题', 'description': '保留登录要求', 'endpoint_ids': [endpoint.id],
            'target_endpoint_ids': [endpoint.id], 'available_endpoint_ids': [endpoint.id],
            'dependency_endpoint_ids': [], 'dependency_evidence': '', 'requires_authenticated_context': False,
            'authenticated_endpoint_ids': [],
        })
        self.assertEqual(snapshot['messages'], [{'role': 'user', 'content': '使用固定账号并验证失败原因'},
            {'role': 'user', 'content': '仅修复失败原因', 'mode': 'repair'}])
        self.assertTrue(queued.called)

    def test_model_change_with_draft_or_root_scope_change_stales_evidence(self):
        endpoint = self.endpoint()
        replacement = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='replacement-stale-model',
            created_by=self.user, is_active=True,
        )
        draft = {
            'version': 1, 'config': {'name': '旧草稿', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': '读取', 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}]}],
        }
        evidence = {'status': 'needs_review', 'source_revision': 0, 'rounds': []}
        root = self.workspace(model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id], generation=evidence)
        child = self.workspace(
            parent=root, model_id=self.model.id, spec=endpoint.spec, endpoint_ids=[endpoint.id], draft=draft,
            candidate={'draft': draft, 'source_revision': 0}, generation=deepcopy(evidence),
            debug_result={'success': False}, debug_revision=0,
        )
        changed = deepcopy(draft)
        changed['config']['name'] = '脚本已编辑'
        child_reply = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': replacement.id, 'draft': changed}),
            project_id=self.project.id, workspace_id=child.id,
        )
        root_reply = APIWorkspaceDetailView.as_view()(
            self.request(self.user, 'patch', '/', {'revision': 0, 'model_id': replacement.id}),
            project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(child_reply.status_code, 200, child_reply.data)
        self.assertEqual(root_reply.status_code, 200, root_reply.data)
        child.refresh_from_db()
        root.refresh_from_db()
        self.assertEqual(child.generation['status'], 'stale')
        self.assertIsNone(child.candidate)
        self.assertIsNone(child.debug_revision)
        self.assertEqual(root.generation['status'], 'stale')

    def test_case_with_unparseable_legacy_script_is_not_silently_replaced(self):
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='legacy', test_case_type='scenario', script_content='not-json',
        )
        created = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(created.status_code, 400)
        self.assertEqual(APIWorkspace.objects.count(), 0)

    def test_reopen_multi_endpoint_browser_case_keeps_source_scope_and_owner_guard(self):
        task = BrowserDiscoveryTask.objects.create(
            project=self.project, owner=self.user, model_id=self.model.id,
            target_url='https://case-source.example.test', description='已确认样本',
            task_id='workspace-case-browser-task',
        )
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='浏览器多接口样本',
            status=APISpecification.TaskStatus.COMPLETED,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, source_task=task,
        )
        first = APIEndpoint.objects.create(spec=spec, method='GET', path='/items')
        second = APIEndpoint.objects.create(spec=spec, method='POST', path='/items')
        draft = default_api_workspace_draft()
        draft['teststeps'] = [
            {'name': '列表', 'endpoint_id': first.id, 'request': {'method': 'GET', 'url': '/items'}},
            {'name': '创建', 'endpoint_id': second.id, 'request': {'method': 'POST', 'url': '/items'}},
            {'name': '再次列表', 'endpoint_id': first.id, 'request': {'method': 'GET', 'url': '/items'}},
        ]
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='浏览器多接口用例', test_case_type='scenario',
            script_content=__import__('json').dumps(draft),
        )

        reopened = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(reopened.status_code, 201, reopened.data)
        self.assertEqual(reopened.data['data']['spec_id'], spec.id, reopened.data)
        self.assertEqual(reopened.data['data']['endpoint_ids'], [first.id, second.id])
        self.assertEqual(reopened.data['data']['source_type'], 'browser_capture')
        self.assertEqual(reopened.data['data']['source_task_id'], str(task.id))

        explicit_scope = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'case_id': case.id, 'endpoint_ids': [second.id]}),
            project_id=self.project.id,
        )
        self.assertEqual(explicit_scope.status_code, 201, explicit_scope.data)
        self.assertEqual(explicit_scope.data['data']['spec_id'], spec.id)
        self.assertEqual(explicit_scope.data['data']['endpoint_ids'], [second.id])

        editor = get_user_model().objects.create_user(
            username='workspace-case-editor', email='workspace-case-editor@example.test', password='pw',
        )
        ProjectMember.objects.create(project=self.project, user=editor, role='editor', can_edit=True)
        denied = APIWorkspaceCollectionView.as_view()(
            self.request(editor, 'post', '/', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(denied.status_code, 400)
        self.assertIn('发起者', denied.data['message'])
        self.assertFalse(APIWorkspace.objects.filter(project=self.project, owner=editor).exists())

    def test_reopen_case_falls_back_to_its_endpoint_when_draft_has_no_endpoint_reference(self):
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
        )
        endpoint = APIEndpoint.objects.create(spec=spec, method='GET', path='/health')
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='端点用例', test_case_type='endpoint', endpoint=endpoint,
        )

        reopened = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(reopened.status_code, 201, reopened.data)
        self.assertEqual(reopened.data['data']['spec_id'], spec.id)
        self.assertEqual(reopened.data['data']['endpoint_ids'], [endpoint.id])

    def test_save_classifies_single_endpoint_and_multi_step_scenario_without_losing_reference(self):
        endpoint = self.endpoint()
        draft = default_api_workspace_draft()
        draft['teststeps'] = [{'name': 'items', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'}}]
        workspace = self.workspace(draft=draft)
        first = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(first.status_code, 200, first.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.saved_case.test_case_type, 'endpoint')
        self.assertEqual(workspace.saved_case.endpoint_id, endpoint.id)
        self.assertEqual(first.data['data']['saved_case_id'], workspace.saved_case_id)
        workspace.draft['teststeps'].append({
            'name': 'items again', 'endpoint_id': endpoint.id, 'request': {'method': 'GET', 'url': '/items'},
        })
        workspace.revision = 1
        workspace.save(update_fields=['draft', 'revision', 'updated_at'])
        second = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 1}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(second.status_code, 200, second.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.saved_case.test_case_type, 'scenario')
        self.assertIsNone(workspace.saved_case.endpoint_id)

    def test_save_rejects_cross_project_endpoint_reference(self):
        other_project = Project.objects.create(name='Other', project_type='api', owner=self.user, created_by=self.user)
        foreign_endpoint = self.endpoint(project=other_project)
        draft = default_api_workspace_draft()
        draft['teststeps'] = [{'name': 'foreign', 'endpoint_id': foreign_endpoint.id, 'request': {'method': 'GET', 'url': '/items'}}]
        workspace = self.workspace(draft=draft)
        saved = APIWorkspaceSaveView.as_view()(
            self.request(self.user, 'post', '/', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(saved.status_code, 400)
        workspace.refresh_from_db()
        self.assertIsNone(workspace.saved_case_id)
