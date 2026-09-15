"""Provider retry contracts; all provider/HTTP calls are faked by the isolated runner."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project, ProjectMember
from .models import APIEndpoint, APISpecification, APITestCase, APIWorkspace, default_api_workspace_draft
from .workspace_service import endpoint_specs, generation_budget, serialize_workspace
from .workspace_tasks import generate_and_verify_api_workspace
from .workspace_verification import draft_hash
from .workspace_views import APIWorkspaceRetryGenerationView


OVERLOAD = '流式LLM调用失败: Our servers are currently overloaded. Please try again later.'


class APIWorkspaceProviderRetryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='provider-retry', password='fixture-only')
        self.project = Project.objects.create(
            name='Provider retry', project_type='api', owner=self.user, created_by=self.user,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-provider',
            api_key='fixture-only', base_url='https://never-called.invalid',
            created_by=self.user, is_active=True,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='Retry API', status='completed',
        )
        self.endpoint = APIEndpoint.objects.create(
            spec=self.spec, method='GET', path='/items', summary='items',
            responses={'200': {'description': 'OK'}},
        )
        self.factory = APIRequestFactory()

    def request(self, workspace, payload, *, user=None):
        request = self.factory.post('/', payload, format='json')
        force_authenticate(request, user=user or self.user)
        return APIWorkspaceRetryGenerationView.as_view()(
            request, project_id=self.project.id, workspace_id=workspace.id,
        )

    def draft(self, endpoint=None):
        endpoint = endpoint or self.endpoint
        return {
            'version': 1, 'config': {'name': 'retry', 'base_url': '', 'variables': {}},
            'teststeps': [{
                'name': 'items', 'endpoint_id': endpoint.id,
                'request': {'method': endpoint.method, 'url': endpoint.path},
                'extract': {}, 'validate': [{'eq': ['status_code', 200]}],
            }],
        }

    @staticmethod
    def passed_result():
        return {
            'success': True, 'error_type': '',
            'step_datas': [{
                'status': 'passed', 'validators': {'validate_extractor': [{'passed': True}]},
                'data': {'req_resps': [{'response': {'status_code': 200}}]},
            }],
        }

    def snapshot(self, workspace, *, workflow=None, mode='generate', draft=None,
                 variables=None, endpoints=None):
        budget = generation_budget(model_id=workspace.model_id, owner=workspace.owner)
        selected = endpoints or endpoint_specs(
            self.project.id, workspace.endpoint_ids, spec_id=self.spec.id, owner=self.user,
        )
        value = {
            'revision': workspace.revision, 'task_id': workspace.task_id or 'old-provider-task',
            'mode': mode, 'draft': deepcopy(draft or workspace.draft),
            'user_draft': deepcopy(workspace.draft), 'model_id': workspace.model_id,
            'spec_id': workspace.spec_id, 'target_endpoint_id': workspace.target_endpoint_id,
            'endpoints': deepcopy(selected), 'target_url': 'https://example.test',
            'variables': deepcopy(variables or {'tenant': 'fixture'}), 'messages': [],
            'failure_evidence': None, **deepcopy(budget),
        }
        if workflow:
            value.update(workflow=workflow, scope_endpoint_ids=[item['id'] for item in selected])
        return value

    def failed_workspace(self, *, parent=None, workflow=None, model_failure=None,
                         summary=OVERLOAD, rounds=None, candidate=None, draft=None):
        workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=parent, spec=self.spec,
            model_id=self.model.id, endpoint_ids=[self.endpoint.id],
            draft=deepcopy(draft or default_api_workspace_draft()), status='failed',
            error=summary, task_id='old-provider-task',
        )
        snapshot = self.snapshot(workspace, workflow=workflow, draft=draft)
        workspace.generation = {
            'status': 'failed', 'phase': 'finished', 'attempt': 1,
            'source_revision': workspace.revision, 'summary': summary,
            'rounds': deepcopy(rounds or []), '_snapshot': snapshot,
        }
        if model_failure:
            workspace.generation['model_failure'] = deepcopy(model_failure)
        workspace.candidate = deepcopy(candidate)
        workspace.save()
        return workspace

    def queue_retry(self, workspace):
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.request(workspace, {
                'revision': workspace.revision, 'execution_confirmed': True,
            })
        return response, queued

    def run_successfully(self, workspace):
        workspace.refresh_from_db()
        manager = SimpleNamespace(stream_invoke=Mock(return_value=json.dumps(self.draft())))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
                patch('api_testing.requests_runner.requests_runner', return_value=self.passed_result()) as runner:
            result = generate_and_verify_api_workspace.apply(
                args=(workspace.id, workspace.revision, workspace.task_id), task_id=workspace.task_id,
            )
        return result.result, manager, runner

    def test_first_stream_overload_can_explicitly_retry_without_http(self):
        workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec, model_id=self.model.id,
            endpoint_ids=[self.endpoint.id], status='generating', task_id='first-task',
        )
        budget = generation_budget(model_id=workspace.model_id, owner=workspace.owner)
        workspace.generation = {
            'status': 'queued', 'phase': 'queued', 'attempt': 0, 'source_revision': 0,
            'rounds': [], '_snapshot': self.snapshot(workspace), **deepcopy(budget),
        }
        workspace.generation['_snapshot']['task_id'] = workspace.task_id
        workspace.save()
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=RuntimeError(OVERLOAD)))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
                patch('api_testing.requests_runner.requests_runner') as runner:
            first = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, 'first-task'))
        self.assertEqual(first.result['status'], 'failed')
        runner.assert_not_called()
        workspace.refresh_from_db()
        public = serialize_workspace(workspace)
        self.assertEqual(public['model_failure']['code'], 'MODEL_OVERLOADED')
        self.assertEqual(public['model_failure']['stage'], 'generating')
        self.assertEqual(public['retry']['mode'], 'generate')
        self.assertTrue(public['retry']['available'])

        response, queued = self.queue_retry(workspace)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data['data']['revision'], 1)
        queued.assert_called_once()
        result, _manager, retry_runner = self.run_successfully(workspace)
        self.assertEqual(result['status'], 'passed')
        retry_runner.assert_called_once()

    def test_retry_advances_revision_and_rejects_duplicate_even_after_finish(self):
        workspace = self.failed_workspace()
        old_revision = workspace.revision
        response, _queued = self.queue_retry(workspace)
        self.assertEqual(response.status_code, 202, response.data)
        result, _manager, _runner = self.run_successfully(workspace)
        self.assertEqual(result['status'], 'passed')
        duplicate = self.request(workspace, {'revision': old_revision, 'execution_confirmed': True})
        self.assertEqual(duplicate.status_code, 409)

    def test_http_evidence_uses_repair_seed_and_private_nonrecursive_archive(self):
        candidate_draft = self.draft()
        digest = draft_hash(candidate_draft)
        failed_http = {
            'success': False, 'error_type': 'ExtractionFailure',
            'step_datas': [{
                'status': 'failed',
                'data': {'req_resps': [{'response': {'status_code': 422}}]},
            }],
        }
        candidate = {
            'draft': candidate_draft, 'draft_hash': digest, 'source_revision': 0,
            'verification_status': 'failed', 'mode': 'generate',
        }
        workspace = self.failed_workspace(
            rounds=[{'attempt': 1, 'draft_hash': digest, 'draft': candidate_draft, 'result': failed_http}],
            candidate=candidate,
            model_failure={
                'code': 'MODEL_UNAVAILABLE', 'message': 'ignored persisted wording',
                'retryable': True, 'stage': 'repairing',
            },
        )
        public = serialize_workspace(workspace)
        self.assertEqual(public['retry']['mode'], 'repair')
        self.assertIn('可能再次向冻结目标发送请求', public['retry']['reason'])
        response, _queued = self.queue_retry(workspace)
        self.assertEqual(response.status_code, 202, response.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.candidate['source_revision'], 1)
        self.assertEqual(workspace.generation['_snapshot']['mode'], 'repair')
        self.assertEqual(workspace.generation['_snapshot']['failure_evidence'], failed_http)
        self.assertEqual(len(workspace.generation['_retry_previous']['rounds']), 1)
        self.assertNotIn('_retry_previous', serialize_workspace(workspace)['generation'])

        # A second provider failure keeps one flat archive and the original HTTP seed.
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=RuntimeError(OVERLOAD)))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager):
            generate_and_verify_api_workspace.apply(
                args=(workspace.id, workspace.revision, workspace.task_id), task_id=workspace.task_id,
            )
        workspace.refresh_from_db()
        again, _queued = self.queue_retry(workspace)
        self.assertEqual(again.status_code, 202, again.data)
        workspace.refresh_from_db()
        self.assertNotIn('_retry_previous', workspace.generation['_retry_previous'])
        self.assertEqual(len(workspace.generation['_retry_previous']['rounds']), 1)

    def test_failed_child_retry_does_not_touch_passed_sibling_and_root_refuses_bulk_retry(self):
        root = self.failed_workspace(workflow='scenarios', summary='场景生成或验证未完成。')
        root.generation['scenario_ids'] = []
        root.save(update_fields=['generation', 'updated_at'])
        failed = self.failed_workspace(parent=root)
        failed.generation['_snapshot'].update({
            'scope_endpoint_ids': [self.endpoint.id],
            'scope_catalog': [{'id': self.endpoint.id, 'method': 'GET', 'path': '/items', 'name': 'items'}],
            'frozen_scope_specs': deepcopy(failed.generation['_snapshot']['endpoints']),
            'scenario': {
                'title': 'failed', 'description': 'only failed',
                'endpoint_ids': [self.endpoint.id], 'target_endpoint_ids': [self.endpoint.id],
                'available_endpoint_ids': [self.endpoint.id], 'dependency_endpoint_ids': [],
                'authenticated_endpoint_ids': [], 'requires_authenticated_context': False,
            },
        })
        failed.generation['scenario_context'] = deepcopy(failed.generation['_snapshot']['scenario'])
        failed.save(update_fields=['generation', 'updated_at'])
        passed_candidate = {
            'draft': self.draft(), 'draft_hash': draft_hash(self.draft()),
            'source_revision': 0, 'verification_status': 'passed',
        }
        passed = self.failed_workspace(parent=root, summary='passed', candidate=passed_candidate)
        passed.status = 'ready'
        passed.generation.update(status='passed', summary='passed')
        passed.save()
        root.generation['scenario_ids'] = [failed.id, passed.id]
        root.save(update_fields=['generation', 'updated_at'])
        before = deepcopy(serialize_workspace(passed))

        root_public = serialize_workspace(root)
        self.assertFalse(root_public['retry']['available'])
        self.assertIn('选择具体失败子场景', root_public['retry']['reason'])
        root_reply = self.request(root, {'revision': root.revision, 'execution_confirmed': True})
        self.assertEqual(root_reply.status_code, 400)

        response, _queued = self.queue_retry(failed)
        self.assertEqual(response.status_code, 202, response.data)
        result, _manager, _runner = self.run_successfully(failed)
        self.assertEqual(result['status'], 'passed')
        passed.refresh_from_db()
        after = serialize_workspace(passed)
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(after['candidate'], before['candidate'])
        self.assertEqual(after['generation'], before['generation'])

    def test_root_planning_failure_retries_planning_without_rebuilding_existing_children(self):
        root = self.failed_workspace(workflow='scenarios')
        public = serialize_workspace(root)
        self.assertEqual(public['model_failure']['stage'], 'planning')
        self.assertEqual(public['retry']['scope'], 'planning')
        self.assertEqual(public['retry']['mode'], 'generate')
        response, _queued = self.queue_retry(root)
        self.assertEqual(response.status_code, 202, response.data)
        root.refresh_from_db()
        plan = {
            'shared_constraints': [], 'summary': 'one',
            'scenarios': [{
                'title': 'one', 'description': 'one endpoint',
                'endpoint_ids': [self.endpoint.id], 'authenticated_endpoint_ids': [],
                'requires_authenticated_context': False,
            }],
        }
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=[json.dumps(plan), json.dumps(self.draft())]))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
                patch('api_testing.requests_runner.requests_runner', return_value=self.passed_result()):
            result = generate_and_verify_api_workspace.apply(
                args=(root.id, root.revision, root.task_id), task_id=root.task_id,
            )
        self.assertEqual(result.result['status'], 'passed')
        self.assertEqual(root.scenarios.count(), 1)

    def test_permission_version_busy_disabled_scope_and_body_guards(self):
        workspace = self.failed_workspace()
        extra = self.request(workspace, {
            'revision': 0, 'execution_confirmed': True, 'base_url': 'https://attacker.invalid',
        })
        self.assertEqual(extra.status_code, 400)
        missing_confirmation = self.request(workspace, {'revision': 0, 'execution_confirmed': False})
        self.assertEqual(missing_confirmation.status_code, 400)
        stale = self.request(workspace, {'revision': 99, 'execution_confirmed': True})
        self.assertEqual(stale.status_code, 409)

        editor = get_user_model().objects.create_user(
            username='retry-editor', email='retry-editor@example.test', password='fixture-only',
        )
        ProjectMember.objects.create(
            project=self.project, user=editor, role='editor', can_edit=True, can_execute_tests=False,
        )
        editor_model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='editor-model', created_by=editor, is_active=True,
        )
        denied = self.failed_workspace()
        denied.owner = editor
        denied.model_id = editor_model.id
        denied.generation['_snapshot']['model_id'] = editor_model.id
        denied.save()
        denied_reply = self.request(denied, {'revision': 0, 'execution_confirmed': True}, user=editor)
        self.assertEqual(denied_reply.status_code, 403)

        busy = self.failed_workspace()
        sibling = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=busy, status='generating', task_id='busy-sibling',
        )
        busy_reply = self.request(busy, {'revision': 0, 'execution_confirmed': True})
        self.assertEqual(busy_reply.status_code, 409)
        sibling.delete()

        disabled = self.failed_workspace()
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        disabled_public = serialize_workspace(disabled)
        self.assertFalse(disabled_public['retry']['available'])
        disabled_reply = self.request(disabled, {'revision': 0, 'execution_confirmed': True})
        self.assertEqual(disabled_reply.status_code, 400)
        self.model.is_active = True
        self.model.save(update_fields=['is_active'])

        second = APIEndpoint.objects.create(spec=self.spec, method='GET', path='/other')
        scoped = self.failed_workspace()
        scoped.endpoint_ids = [second.id]
        scoped.save(update_fields=['endpoint_ids', 'updated_at'])
        scoped_reply = self.request(scoped, {'revision': 0, 'execution_confirmed': True})
        self.assertEqual(scoped_reply.status_code, 400)

    def test_non_provider_and_unsafe_candidate_failures_have_no_retry(self):
        ordinary = self.failed_workspace(summary='JSON 校验失败：缺少 teststeps。')
        public = serialize_workspace(ordinary)
        self.assertIsNone(public['model_failure'])
        self.assertFalse(public['retry']['available'])

        candidate_draft = self.draft()
        unsafe = self.failed_workspace(
            candidate={
                'draft': candidate_draft, 'draft_hash': draft_hash(candidate_draft),
                'source_revision': 0, 'verification_status': 'failed',
            },
            model_failure={
                'code': 'MODEL_OVERLOADED', 'message': 'ignored',
                'retryable': True, 'stage': 'repairing',
            },
        )
        unsafe_public = serialize_workspace(unsafe)
        self.assertFalse(unsafe_public['retry']['available'])
        self.assertIn('不足以安全修复', unsafe_public['retry']['reason'])

        quota = self.failed_workspace(model_failure={
            'code': 'MODEL_QUOTA_EXHAUSTED', 'message': 'ignored',
            'retryable': False, 'stage': 'generating',
        })
        quota_public = serialize_workspace(quota)
        self.assertEqual(quota_public['model_failure']['code'], 'MODEL_QUOTA_EXHAUSTED')
        self.assertFalse(quota_public['retry']['available'])

    def test_retry_rejects_unknown_remote_state_and_replaced_snapshot_lease(self):
        candidate_draft = self.draft()
        digest = draft_hash(candidate_draft)

        def retry_state(result):
            workspace = self.failed_workspace(
                rounds=[{'attempt': 1, 'draft_hash': digest, 'draft': candidate_draft, 'result': result}],
                candidate={
                    'draft': candidate_draft, 'draft_hash': digest, 'source_revision': 0,
                    'verification_status': 'failed',
                },
                model_failure={
                    'code': 'MODEL_OVERLOADED', 'message': 'ignored',
                    'retryable': True, 'stage': 'repairing',
                },
            )
            return workspace, serialize_workspace(workspace)['retry']

        timeout, timeout_retry = retry_state({
            'success': False, 'error_type': 'HardTimeout', 'step_datas': [],
        })
        self.assertFalse(timeout_retry['available'])
        self.assertIn('远端是否生效未知', timeout_retry['reason'])
        self.assertEqual(self.request(timeout, {
            'revision': 0, 'execution_confirmed': True,
        }).status_code, 400)

        unsafe, unsafe_retry = retry_state({
            'success': False, 'error_type': 'ExtractionFailure',
            'replay_safety': {'safe_to_retry': False}, 'step_datas': [],
        })
        self.assertFalse(unsafe_retry['available'])
        self.assertIn('不可安全重放', unsafe_retry['reason'])

        for code in (401, 403, 503):
            blocked, blocked_retry = retry_state({
                'success': False, 'error_type': 'ExtractionFailure',
                'replay_safety': {'safe_to_retry': True},
                'step_datas': [{
                    'status': 'failed',
                    'data': {'req_resps': [{'response': {'status_code': code}}]},
                }],
            })
            self.assertFalse(blocked_retry['available'], code)

        replaced = self.failed_workspace()
        replaced.task_id = 'later-debug-task'
        replaced.save(update_fields=['task_id', 'updated_at'])
        replaced_public = serialize_workspace(replaced)
        self.assertIsNotNone(replaced_public['model_failure'])
        self.assertFalse(replaced_public['retry']['available'])
        self.assertIn('租约', replaced_public['retry']['reason'])
        replaced_reply = self.request(replaced, {'revision': 0, 'execution_confirmed': True})
        self.assertEqual(replaced_reply.status_code, 400)

    def test_model_failure_is_only_public_for_matching_failed_generation_terminal(self):
        failure = {
            'code': 'MODEL_OVERLOADED', 'message': 'ignored',
            'retryable': True, 'stage': 'generating',
        }
        workspace = self.failed_workspace(model_failure=failure)
        self.assertIsNotNone(serialize_workspace(workspace)['model_failure'])
        workspace.status = 'generating'
        workspace.generation.update(status='queued', phase='queued')
        workspace.save()
        queued = serialize_workspace(workspace)
        self.assertIsNone(queued['model_failure'])
        self.assertFalse(queued['retry']['available'])
        workspace.status = 'ready'
        workspace.generation.update(status='passed', phase='finished')
        workspace.save()
        passed = serialize_workspace(workspace)
        self.assertIsNone(passed['model_failure'])
        self.assertFalse(passed['retry']['available'])
