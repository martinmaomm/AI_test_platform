"""Focused offline contracts for explicit endpoint-generation workspaces."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project
from .models import APIEndpoint, APISpecification, APITestCase, APIWorkspace
from .workspace_service import (
    WorkspaceValidationError, endpoint_specs, normalize_draft, serialize_workspace,
    workspace_coverage,
)
from .workspace_tasks import (
    _generate_scenarios, _generation_messages, _parse_plan, _planner_messages, _scenario_children,
    generate_and_verify_api_workspace,
)
from .workspace_verification import draft_hash
from .workspace_views import (
    APIWorkspaceCollectionView, APIWorkspaceDetailView, APIWorkspaceMessagesView,
    APIWorkspaceSaveView,
)


class EndpointWorkspaceFixtures:
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='endpoint-workspace-owner', email='endpoint-workspace@example.test', password='pw',
        )
        self.project = Project.objects.create(
            name='Endpoint workspace', project_type='api', owner=self.user, created_by=self.user,
        )
        self.spec = self.make_spec(self.project, 'Primary spec')
        self.target = APIEndpoint.objects.create(spec=self.spec, method='GET', path='/items/{id}')
        self.dependency = APIEndpoint.objects.create(spec=self.spec, method='POST', path='/session')
        self.factory = APIRequestFactory()

    def make_spec(self, project, name):
        return APISpecification.objects.create(
            project=project, created_by=project.created_by, spec_name=name,
            status=APISpecification.TaskStatus.COMPLETED,
        )

    def request(self, method, data=None):
        request = getattr(self.factory, method)('/', data or {}, format='json')
        force_authenticate(request, user=self.user)
        return request

    @staticmethod
    def draft(*endpoint_ids):
        paths = {endpoint_ids[0]: '/items/7'} if endpoint_ids else {}
        return normalize_draft({
            'version': 1,
            'config': {'name': 'Explicit endpoint case', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [
                {
                    'name': f'step-{index}', 'endpoint_id': endpoint_id,
                    'request': {
                        'method': 'GET' if index == 0 else 'POST',
                        'url': paths.get(endpoint_id, '/session'),
                    },
                    'validate': [{'eq': ['status_code', 200]}],
                }
                for index, endpoint_id in enumerate(endpoint_ids)
            ],
        })

    def workspace(self, **overrides):
        values = {
            'project': self.project, 'owner': self.user, 'spec': self.spec,
            'target_endpoint_id': self.target.id,
            'endpoint_ids': [self.target.id, self.dependency.id],
            'draft': self.draft(self.target.id, self.dependency.id),
        }
        values.update(overrides)
        return APIWorkspace.objects.create(**values)


class EndpointWorkspaceApiTests(EndpointWorkspaceFixtures, TestCase):
    def test_create_validates_target_scope_spec_project_and_keeps_target_immutable(self):
        created = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'target_endpoint_id': self.target.id}), project_id=self.project.id,
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['target_endpoint_id'], self.target.id)
        self.assertEqual(created.data['data']['endpoint_ids'], [self.target.id])
        self.assertEqual(created.data['data']['spec_id'], self.spec.id)
        workspace_id = created.data['data']['id']

        immutable = APIWorkspaceDetailView.as_view()(
            self.request('patch', {'revision': 0, 'target_endpoint_id': self.target.id}),
            project_id=self.project.id, workspace_id=workspace_id,
        )
        self.assertEqual(immutable.status_code, 400)
        self.assertIn('不可通过 PATCH', immutable.data['message'])

        removed = APIWorkspaceDetailView.as_view()(
            self.request('patch', {'revision': 0, 'endpoint_ids': []}),
            project_id=self.project.id, workspace_id=workspace_id,
        )
        self.assertEqual(removed.status_code, 400)
        self.assertIn('不能移除', removed.data['message'])

        other_project = Project.objects.create(
            name='Other project', project_type='api', owner=self.user, created_by=self.user,
        )
        other_spec = self.make_spec(other_project, 'Other project spec')
        foreign_target = APIEndpoint.objects.create(spec=other_spec, method='GET', path='/foreign')
        same_project_other_spec = self.make_spec(self.project, 'Other local spec')
        local_other = APIEndpoint.objects.create(spec=same_project_other_spec, method='GET', path='/local-other')
        invalid_payloads = (
            {'target_endpoint_id': True},
            {'target_endpoint_id': 99999999},
            {'target_endpoint_id': foreign_target.id},
            {'target_endpoint_id': self.target.id, 'endpoint_ids': [self.dependency.id]},
            {'target_endpoint_id': self.target.id, 'endpoint_ids': [self.target.id], 'spec_id': local_other.spec_id},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                reply = APIWorkspaceCollectionView.as_view()(
                    self.request('post', payload), project_id=self.project.id,
                )
                self.assertEqual(reply.status_code, 400, reply.data)

    def test_open_endpoint_case_inherits_target_and_rejects_case_target_mismatches(self):
        draft = self.draft(self.target.id, self.dependency.id)
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='Existing negative endpoint case',
            test_case_type='endpoint', endpoint=self.target, test_type='negative',
            script_content=json.dumps(draft),
        )
        reopened = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'case_id': case.id}), project_id=self.project.id,
        )
        self.assertEqual(reopened.status_code, 201, reopened.data)
        payload = reopened.data['data']
        self.assertEqual(payload['target_endpoint_id'], self.target.id)
        self.assertEqual(payload['endpoint_ids'], [self.target.id, self.dependency.id])
        self.assertEqual(payload['suggested_test_type'], 'negative')

        mismatch = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'case_id': case.id, 'target_endpoint_id': self.dependency.id}),
            project_id=self.project.id,
        )
        self.assertEqual(mismatch.status_code, 400)

        scenario_case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='Scenario', test_case_type='scenario',
            script_content=json.dumps(draft),
        )
        scenario_target = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'case_id': scenario_case.id, 'target_endpoint_id': self.target.id}),
            project_id=self.project.id,
        )
        self.assertEqual(scenario_target.status_code, 400)

        other_project = Project.objects.create(
            name='Malformed case project', project_type='api', owner=self.user, created_by=self.user,
        )
        malformed_case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='Cross-project endpoint',
            test_case_type='endpoint', endpoint=APIEndpoint.objects.create(
                spec=self.make_spec(other_project, 'Foreign spec'), method='GET', path='/foreign-case',
            ),
        )
        malformed = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'case_id': malformed_case.id}), project_id=self.project.id,
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertIn('不属于当前项目', malformed.data['message'])

    def test_explicit_target_multi_step_save_stays_endpoint_and_preserves_or_accepts_test_type(self):
        workspace = self.workspace()
        saved = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0, 'test_type': 'negative'}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(saved.status_code, 200, saved.data)
        workspace.refresh_from_db()
        case = workspace.saved_case
        self.assertEqual(case.test_case_type, 'endpoint')
        self.assertEqual(case.endpoint_id, self.target.id)
        self.assertEqual(case.test_type, 'negative')
        self.assertEqual(len(json.loads(case.script_content)['teststeps']), 2)
        self.assertEqual(saved.data['data']['suggested_test_type'], 'negative')

        preserved = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(preserved.status_code, 200, preserved.data)
        case.refresh_from_db()
        self.assertEqual(case.test_type, 'negative')

        invalid = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0, 'test_type': 'regression'}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_reopened_single_endpoint_case_can_expand_to_multiple_steps_without_reclassification(self):
        initial = self.draft(self.target.id)
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='Boundary endpoint',
            test_case_type='endpoint', endpoint=self.target, test_type='boundary',
            script_content=json.dumps(initial),
        )
        created = APIWorkspaceCollectionView.as_view()(
            self.request('post', {'case_id': case.id}), project_id=self.project.id,
        )
        workspace_id = created.data['data']['id']
        expanded = self.draft(self.dependency.id, self.target.id)
        # Correct the helper's first-step method/path for the dependency-first flow.
        expanded['teststeps'][0]['request'] = {'method': 'POST', 'url': '/session', 'headers': {}, 'params': {}}
        expanded['teststeps'][1]['request'] = {'method': 'GET', 'url': '/items/7', 'headers': {}, 'params': {}}
        patched = APIWorkspaceDetailView.as_view()(
            self.request('patch', {
                'revision': 0, 'endpoint_ids': [self.target.id, self.dependency.id], 'draft': expanded,
            }), project_id=self.project.id, workspace_id=workspace_id,
        )
        self.assertEqual(patched.status_code, 200, patched.data)
        saved = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 1}), project_id=self.project.id, workspace_id=workspace_id,
        )
        self.assertEqual(saved.status_code, 200, saved.data)
        case.refresh_from_db()
        self.assertEqual((case.test_case_type, case.endpoint_id, case.test_type), ('endpoint', self.target.id, 'boundary'))
        self.assertEqual(len(json.loads(case.script_content)['teststeps']), 2)

    def test_target_must_be_referenced_and_dependencies_must_remain_in_scope(self):
        missing_target = self.workspace(draft=self.draft(self.dependency.id))
        reply = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=missing_target.id,
        )
        self.assertEqual(reply.status_code, 400)
        self.assertIn('必须实际引用目标端点', reply.data['message'])

        out_of_scope = self.workspace(
            endpoint_ids=[self.target.id], draft=self.draft(self.target.id, self.dependency.id),
        )
        reply = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=out_of_scope.id,
        )
        self.assertEqual(reply.status_code, 400)
        self.assertIn('可用范围之外', reply.data['message'])

    def test_deleted_target_id_is_readable_but_generation_and_save_fail_explicitly(self):
        model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-target-model',
            created_by=self.user, is_active=True,
        )
        target_id = self.target.id
        workspace = self.workspace(model_id=model.id, draft=self.draft(self.target.id))
        self.target.delete()
        workspace.refresh_from_db()
        self.assertEqual(workspace.target_endpoint_id, target_id)
        read = APIWorkspaceDetailView.as_view()(
            self.request('get'), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(read.status_code, 200, read.data)
        self.assertEqual(read.data['data']['target_endpoint_id'], target_id)

        save = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(save.status_code, 400)
        self.assertIn('目标端点不存在', save.data['message'])
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued:
            generated = APIWorkspaceMessagesView.as_view()(
                self.request('post', {
                    'revision': 0, 'message': '生成端点用例', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(generated.status_code, 400)
        self.assertIn('目标端点不存在', generated.data['message'])
        queued.assert_not_called()

    def test_target_generation_snapshot_and_child_keep_frozen_target_and_suggested_type(self):
        model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-snapshot-model',
            created_by=self.user, is_active=True,
        )
        workspace = self.workspace(model_id=model.id, draft=normalize_draft({'teststeps': []}))
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request('post', {
                    'revision': 0, 'message': '生成正反边界用例', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        workspace.refresh_from_db()
        snapshot = workspace.generation['_snapshot']
        self.assertEqual(snapshot['target_endpoint_id'], self.target.id)
        self.assertEqual(snapshot['workflow'], 'scenarios')
        queued.assert_called_once()

        plan = _parse_plan({'scenarios': [{
            'title': 'Negative item lookup', 'description': 'Missing item',
            'endpoint_ids': [self.target.id], 'test_type': 'negative',
            'dependency_endpoint_ids': [self.dependency.id],
            'dependency_evidence': 'Session response supplies the documented credential.',
            'authenticated_endpoint_ids': [], 'requires_authenticated_context': False,
        }]}, endpoint_ids={self.target.id, self.dependency.id}, target_endpoint_id=self.target.id)
        children = _scenario_children(
            root_id=workspace.id, revision=0, task_id=workspace.task_id, snapshot=snapshot, plan=plan,
        )
        self.assertIsNotNone(children)
        child = APIWorkspace.objects.get(pk=children[0][0])
        self.assertEqual(child.target_endpoint_id, self.target.id)
        self.assertEqual(child.generation['_snapshot']['target_endpoint_id'], self.target.id)
        self.assertEqual(child.generation['_snapshot']['scenario']['test_type'], 'negative')
        self.assertEqual(serialize_workspace(child)['suggested_test_type'], 'negative')

        three_step_draft = self.draft(self.dependency.id, self.target.id, self.dependency.id)
        three_step_draft['teststeps'][0]['request'] = {
            'method': 'POST', 'url': '/session', 'headers': {}, 'params': {},
        }
        three_step_draft['teststeps'][1]['request'] = {
            'method': 'GET', 'url': '/items/7', 'headers': {}, 'params': {},
        }
        candidate_hash = draft_hash(three_step_draft)
        workspace.status = APIWorkspace.Status.READY
        workspace.task_id = ''
        workspace.generation = {**workspace.generation, 'status': 'passed', 'phase': 'finished'}
        workspace.save(update_fields=['status', 'task_id', 'generation', 'updated_at'])
        child.status = APIWorkspace.Status.READY
        child.task_id = ''
        child.candidate = {
            'draft': three_step_draft, 'source_revision': 0, 'draft_hash': candidate_hash,
        }
        child.generation = {
            **child.generation, 'status': 'passed', 'phase': 'finished', 'source_revision': 0,
            'rounds': [{'draft_hash': candidate_hash, 'result': {'success': True}}],
        }
        child.save(update_fields=['status', 'task_id', 'candidate', 'generation', 'updated_at'])
        adopted = APIWorkspaceDetailView.as_view()(
            self.request('patch', {'revision': 0, 'draft': three_step_draft}),
            project_id=self.project.id, workspace_id=child.id,
        )
        self.assertEqual(adopted.status_code, 200, adopted.data)
        self.assertEqual(adopted.data['data']['target_endpoint_id'], self.target.id)
        saved_child = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 1, 'test_type': 'negative'}),
            project_id=self.project.id, workspace_id=child.id,
        )
        self.assertEqual(saved_child.status_code, 200, saved_child.data)
        child.refresh_from_db()
        self.assertEqual(child.endpoint_ids, [self.target.id])
        self.assertEqual(child.saved_case.endpoint_id, self.target.id)
        self.assertEqual(len(json.loads(child.saved_case.script_content)['teststeps']), 3)

    def test_existing_saved_case_generation_stays_single_pipeline_with_frozen_target(self):
        model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-existing-case-model',
            created_by=self.user, is_active=True,
        )
        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='Existing target case',
            test_case_type='endpoint', endpoint=self.target, test_type='security',
            script_content=json.dumps(self.draft(self.target.id)),
        )
        workspace = self.workspace(model_id=model.id, saved_case=case, saved_case_updated_at=case.updated_at)
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, self.captureOnCommitCallbacks(execute=True):
            reply = APIWorkspaceMessagesView.as_view()(
                self.request('post', {
                    'revision': 0, 'message': '重新生成当前用例', 'execution_confirmed': True,
                    'base_url': 'https://example.test', 'variables': {},
                }), project_id=self.project.id, workspace_id=workspace.id,
            )
        self.assertEqual(reply.status_code, 202, reply.data)
        workspace.refresh_from_db()
        self.assertNotIn('workflow', workspace.generation['_snapshot'])
        self.assertEqual(workspace.generation['_snapshot']['target_endpoint_id'], self.target.id)
        self.assertEqual(reply.data['data']['suggested_test_type'], 'security')
        queued.assert_called_once()

    def test_ordinary_multi_step_workspace_still_saves_as_scenario(self):
        workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec,
            endpoint_ids=[self.target.id, self.dependency.id],
            draft=self.draft(self.target.id, self.dependency.id),
        )
        reply = APIWorkspaceSaveView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(reply.status_code, 200, reply.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.saved_case.test_case_type, 'scenario')
        self.assertIsNone(workspace.saved_case.endpoint_id)

    def test_explicit_target_coverage_excludes_auxiliary_scope_in_current_and_stale_states(self):
        cleanup = APIEndpoint.objects.create(spec=self.spec, method='DELETE', path='/session')
        scope_ids = [self.target.id, self.dependency.id, cleanup.id]
        root = self.workspace(
            endpoint_ids=scope_ids,
            generation={'_snapshot': {'scope_endpoint_ids': scope_ids}, 'scenario_ids': []},
        )
        child = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=root, spec=self.spec,
            target_endpoint_id=self.target.id, endpoint_ids=[self.target.id],
        )
        root.generation['scenario_ids'] = [child.id]
        current = workspace_coverage(root, [child])
        self.assertEqual(current['total'], 1)
        self.assertEqual(current['planned'], 1)
        self.assertEqual(current['planned_endpoint_ids'], [self.target.id])
        self.assertEqual(current['uncovered_endpoint_ids'], [])

        root.generation = {**root.generation, 'status': 'stale'}
        stale = workspace_coverage(root, [child])
        self.assertEqual(stale['total'], 1)
        self.assertEqual(stale['planned'], 0)
        self.assertEqual(stale['uncovered_endpoint_ids'], [self.target.id])

        ordinary = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec, endpoint_ids=scope_ids,
            generation={'_snapshot': {'scope_endpoint_ids': scope_ids}, 'scenario_ids': [child.id]},
        )
        ordinary_child = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=ordinary, spec=self.spec,
            endpoint_ids=[self.target.id],
        )
        ordinary.generation['scenario_ids'] = [ordinary_child.id]
        ordinary_coverage = workspace_coverage(ordinary, [ordinary_child])
        self.assertEqual(ordinary_coverage['total'], 3)
        self.assertEqual(ordinary_coverage['planned'], 1)
        self.assertEqual(
            ordinary_coverage['uncovered_endpoint_ids'],
            sorted([self.dependency.id, cleanup.id]),
        )


class EndpointWorkspacePlanningTests(SimpleTestCase):
    def test_target_plan_requires_exact_target_and_valid_optional_test_type(self):
        valid = _parse_plan({'scenarios': [{
            'title': 'Boundary', 'endpoint_ids': [7], 'test_type': 'boundary',
            'dependency_endpoint_ids': [8], 'dependency_evidence': 'Documented setup response.',
            'authenticated_endpoint_ids': [], 'requires_authenticated_context': False,
        }]}, endpoint_ids={7, 8}, target_endpoint_id=7)
        self.assertEqual(valid['scenarios'][0]['endpoint_ids'], [7])
        self.assertEqual(valid['scenarios'][0]['test_type'], 'boundary')
        for scenario in (
            {'title': 'Missing target', 'endpoint_ids': [8]},
            {'title': 'Expanded target', 'endpoint_ids': [7, 8]},
            {'title': 'Duplicated target', 'endpoint_ids': [7, 7]},
            {'title': 'Invalid type', 'endpoint_ids': [7], 'test_type': 'regression'},
            {'title': 'Dependency outside', 'endpoint_ids': [7], 'dependency_endpoint_ids': [9],
             'dependency_evidence': 'unsupported'},
        ):
            with self.subTest(scenario=scenario), self.assertRaises(WorkspaceValidationError):
                _parse_plan({'scenarios': [scenario]}, endpoint_ids={7, 8}, target_endpoint_id=7)

        ordinary = _parse_plan(
            {'scenarios': [{'title': 'Ordinary lifecycle', 'endpoint_ids': [7, 8]}]},
            endpoint_ids={7, 8},
        )
        self.assertEqual(ordinary['scenarios'][0]['endpoint_ids'], [7, 8])

    def test_target_planner_prompt_freezes_target_and_classification_without_extra_calls(self):
        messages = _planner_messages(conversation=[], endpoints=[], target_endpoint_id=7)
        rules = messages[0].content
        payload = json.loads(messages[1].content)
        self.assertIn('endpoint_ids 必须严格等于 [7]', rules)
        self.assertIn('正向、反向和边界用例', rules)
        self.assertIn('dependency_endpoint_ids', rules)
        self.assertEqual(payload['target_endpoint_id'], 7)
        direct = _generation_messages(
            conversation=[], draft={}, endpoints=[], mode='generate', failure_evidence=None,
            target_endpoint_id=7,
        )
        self.assertIn('唯一业务目标 target_endpoint_id=7', direct[0].content)
        self.assertEqual(json.loads(direct[1].content)['target_endpoint_id'], 7)


class EndpointWorkspacePlanningRepairTests(EndpointWorkspaceFixtures, TestCase):
    def test_invalid_target_plan_gets_the_existing_single_structure_correction(self):
        model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-plan-repair-model',
            created_by=self.user, is_active=True,
        )
        task_id = 'target-plan-repair-task'
        snapshot = {
            'revision': 0, 'task_id': task_id, 'workflow': 'scenarios', 'model_id': model.id,
            'spec_id': self.spec.id, 'target_endpoint_id': self.target.id,
            'scope_endpoint_ids': [self.target.id, self.dependency.id],
            'endpoints': endpoint_specs(
                self.project.id, [self.target.id, self.dependency.id], spec_id=self.spec.id,
            ),
            'messages': [], 'target_url': 'https://example.test', 'variables': {},
            'timeouts': {'llm_seconds': 60}, 'deadlines': {},
        }
        workspace = self.workspace(
            model_id=model.id, status=APIWorkspace.Status.GENERATING, task_id=task_id,
            generation={'_snapshot': deepcopy(snapshot)},
        )
        invalid = json.dumps({'scenarios': [{
            'title': 'Dependency replaced target', 'endpoint_ids': [self.dependency.id],
        }]})
        corrected = json.dumps({'scenarios': [{
            'title': 'Negative target', 'endpoint_ids': [self.target.id], 'test_type': 'negative',
            'authenticated_endpoint_ids': [], 'requires_authenticated_context': False,
        }]})
        manager = SimpleNamespace(stream_invoke=SimpleNamespace())
        calls = []

        def answer(messages, **_kwargs):
            calls.append(messages)
            return invalid if len(calls) == 1 else corrected

        manager.stream_invoke = answer
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), patch(
            'api_testing.workspace_tasks._llm_call_budget', return_value=(60.0, time.monotonic() + 60),
        ), patch('api_testing.workspace_tasks._require_pipeline_time', return_value=60.0), patch(
            'api_testing.workspace_tasks._scenario_children', return_value=[],
        ):
            result = _generate_scenarios(workspace.id, 0, task_id, snapshot)

        self.assertEqual(len(calls), 2)
        correction_payload = json.loads(calls[1][1].content)
        self.assertEqual(correction_payload['target_endpoint_id'], self.target.id)
        self.assertIn('必须严格等于显式目标', correction_payload['failure_evidence']['error'])
        self.assertEqual(result['status'], 'failed')
