"""Offline contract tests for API Workspace B; no model, broker, or HTTP service is used."""
import sys
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Environment, Project, ProjectMember
from ai_core.models import LLMConfiguration
from .models import APIEndpoint, APISpecification, APITestCase, APIWorkspace, default_api_workspace_draft
from .workspace_service import debug_timeout_seconds, normalize_draft
from .workspace_tasks import _path_matches, _step_assertions, debug_api_workspace, generate_api_workspace_candidate
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
        spec = APISpecification.objects.create(project=project, created_by=self.user, spec_name='Workspace API')
        return APIEndpoint.objects.create(spec=spec, method='GET', path='/items', summary='items')

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
        with patch('api_testing.workspace_views._queue_generation') as queue:
            denied_message = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(denied_message.status_code, 400)
        queue.assert_not_called()

        workspace.status = 'generating'
        workspace.task_id = 'other-owner-task'
        workspace.save(update_fields=['status', 'task_id', 'updated_at'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_api_workspace_candidate.apply(
                args=(workspace.id, 0, 'other-owner-task', 'generate', []),
            )
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()

    def test_generation_requires_an_explicit_owned_active_model(self):
        workspace = self.workspace()
        with patch('api_testing.workspace_views._queue_generation') as queue:
            denied_message = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(denied_message.status_code, 400)
        queue.assert_not_called()

        workspace.status = 'generating'
        workspace.task_id = 'no-model-task'
        workspace.save(update_fields=['status', 'task_id', 'updated_at'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_api_workspace_candidate.apply(
                args=(workspace.id, 0, 'no-model-task', 'generate', []),
            )
        self.assertEqual(result.result['status'], 'failed')
        manager.assert_not_called()

    def test_fake_llm_stores_candidate_without_replacing_draft(self):
        draft = default_api_workspace_draft()
        workspace = self.workspace(
            draft=draft, status='generating', task_id='candidate-task',
            messages=[{'role': 'user', 'content': '生成登录场景'}], model_id=self.model.id,
        )
        candidate = {
            'version': 1,
            'config': {'name': '登录', 'base_url': 'https://example.test', 'variables': {}, 'verify': True},
            'teststeps': [{'name': '登录', 'endpoint_id': 1, 'request': {'method': 'POST', 'url': '/login'}}],
        }
        manager = SimpleNamespace(stream_invoke=lambda messages: __import__('json').dumps(candidate))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager) as manager_factory:
            result = generate_api_workspace_candidate.apply(args=(workspace.id, 0, 'candidate-task', 'generate', [], None))
        self.assertEqual(result.result['status'], 'ready')
        manager_factory.assert_called_once_with(config_id=self.model.id)
        workspace.refresh_from_db()
        self.assertEqual(workspace.draft, draft)
        self.assertEqual(workspace.candidate['draft'], normalize_draft(candidate))
        self.assertEqual(workspace.candidate['source_revision'], 0)

    def test_fake_llm_repair_rejects_candidate_that_removes_assertion(self):
        draft = default_api_workspace_draft()
        draft['teststeps'] = [{'name': 'existing', 'request': {'method': 'GET', 'url': '/x'}, 'validate': [{'eq': ['status_code', 200]}]}]
        workspace = self.workspace(
            draft=draft, status='generating', task_id='repair-task', debug_revision=0,
            debug_result={'success': False, 'error': '500'}, model_id=self.model.id,
        )
        manager = SimpleNamespace(stream_invoke=lambda messages: __import__('json').dumps(default_api_workspace_draft()))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager):
            generate_api_workspace_candidate.apply(args=(workspace.id, 0, 'repair-task', 'repair', [], {'success': False, 'error': '500'}))
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'failed')
        self.assertIsNone(workspace.candidate)
        self.assertEqual(workspace.draft, draft)

    def test_debug_uses_frozen_snapshot_and_returns_matching_revision(self):
        workspace = self.workspace(
            status='debugging', task_id='debug-task', debug_revision=0,
            debug_snapshot={'revision': 0, 'draft': {
                **default_api_workspace_draft(),
                'teststeps': [{'name': 'health', 'request': {'method': 'GET', 'url': '/health'}}],
            }, 'environment': {'base_url': 'https://example.test'}, 'variables': {'token': 'frozen'}},
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
            debug_snapshot={'revision': 0, 'draft': default_api_workspace_draft(), 'environment': {}, 'variables': {}},
        )
        self.assertIsNotNone(_claim_debug(workspace_id=workspace.id, revision=0, task_id='claimed-task'))
        module = ModuleType('api_testing.requests_runner')
        module.requests_runner = lambda **kwargs: self.fail('duplicate delivery must not issue HTTP')
        with patch.dict(sys.modules, {'api_testing.requests_runner': module}):
            result = debug_api_workspace.apply(args=(workspace.id, 0, 'claimed-task'))
        self.assertEqual(result.result['status'], 'stale')

    def test_polling_expires_lost_debug_without_allowing_old_worker_writeback(self):
        workspace = self.workspace(
            status='debugging', task_id='lost-task', debug_revision=0,
            debug_snapshot={'revision': 0, 'draft': default_api_workspace_draft(), 'environment': {}, 'variables': {}},
        )
        APIWorkspace.objects.filter(pk=workspace.pk).update(
            updated_at=timezone.now() - timedelta(seconds=debug_timeout_seconds() + 1),
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

        with patch('api_testing.workspace_views._queue_generation') as queue:
            generate = APIWorkspaceMessagesView.as_view()(
                self.request(self.user, 'post', '/', {'revision': 0, 'message': '生成健康检查'}),
                project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(generate.status_code, 400)
        queue.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.messages, [])

    def test_model_disabled_after_enqueue_never_contacts_provider(self):
        workspace = self.workspace(model_id=self.model.id, status='generating', task_id='disabled-task')
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            generate_api_workspace_candidate.apply(args=(workspace.id, 0, 'disabled-task', 'generate', []))
        manager.assert_not_called()
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'failed')
        self.assertIn('禁用', workspace.error)
        self.assertIsNone(workspace.candidate)

    def test_model_owner_change_after_enqueue_never_contacts_provider(self):
        workspace = self.workspace(model_id=self.model.id, status='generating', task_id='owner-changed-task')
        self.model.created_by = self.viewer
        self.model.save(update_fields=['created_by'])
        with patch('api_testing.workspace_tasks.get_llm_manager') as manager:
            result = generate_api_workspace_candidate.apply(
                args=(workspace.id, 0, 'owner-changed-task', 'generate', []),
            )
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

    def test_feedback_retry_includes_previous_model_output(self):
        workspace = self.workspace(status='generating', task_id='retry-task', model_id=self.model.id)
        candidate = {
            'version': 1, 'config': {'name': 'health', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{'name': 'health', 'request': {'method': 'GET', 'url': '/health'}}],
        }
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=['not json', __import__('json').dumps(candidate)]))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager):
            result = generate_api_workspace_candidate.apply(args=(workspace.id, 0, 'retry-task', 'generate', [], None))
        self.assertEqual(result.result['status'], 'ready')
        self.assertEqual(manager.stream_invoke.call_count, 2)
        self.assertIn('not json', manager.stream_invoke.call_args_list[1].args[0][-1].content)

    def test_case_with_unparseable_legacy_script_is_not_silently_replaced(self):
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='legacy', test_case_type='scenario', script_content='not-json',
        )
        created = APIWorkspaceCollectionView.as_view()(
            self.request(self.user, 'post', '/', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(created.status_code, 400)
        self.assertEqual(APIWorkspace.objects.count(), 0)

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
