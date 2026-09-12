"""Offline reliability contracts for API workspace orchestration."""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project
from .models import APIEndpoint, APISpecification, APITestExecution, APIWorkspace
from .views import APITestExecutionReportView
from .workspace_evidence import (
    WORKSPACE_DEBUG, WORKSPACE_GENERATION, attach_execution_histories, finish_workspace_execution,
    start_workspace_execution, store_workspace_progress,
)
from .workspace_service import WorkspaceValidationError, generation_budget, normalize_draft, serialize_workspace
from .workspace_tasks import (
    _claim_pipeline, _generation_messages, _remaining_pipeline_seconds,
    _prompt_failure_evidence, _scenario_children, _supplement_candidate_endpoints,
    _finish_pipeline, debug_api_workspace,
)
from .workspace_verification import (
    assertion_provenance, assertion_review, classify_result, draft_hash, prepare_candidate,
)
from .workspace_views import APIWorkspaceCancelView, APIWorkspaceDetailView


class WorkspaceReliabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='workspace-reliability', email='workspace-reliability@example.test', password='pw',
        )
        self.project = Project.objects.create(
            name='Workspace reliability', project_type='api', owner=self.user, created_by=self.user,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline', created_by=self.user,
            is_active=True, extra_config={'timeout': 77},
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='Reliability API',
            status=APISpecification.TaskStatus.COMPLETED,
        )
        self.factory = APIRequestFactory()

    def request(self, method, payload=None):
        request = getattr(self.factory, method.lower())('/', payload or {}, format='json')
        force_authenticate(request, user=self.user)
        return request

    def endpoint(self, method, path, **kwargs):
        return APIEndpoint.objects.create(spec=self.spec, method=method, path=path, **kwargs)

    def draft(self, endpoint, *, status=200, body_assertion=False):
        assertions = [{'eq': ['status_code', status]}]
        if body_assertion:
            assertions.append({'eq': ['body.code', 0]})
        return normalize_draft({
            'version': 1,
            'config': {'name': 'reliability run', 'base_url': '', 'variables': {}, 'verify': True},
            'teststeps': [{
                'name': 'request', 'endpoint_id': endpoint.id,
                'request': {'method': endpoint.method, 'url': endpoint.path},
                'validate': assertions,
            }],
        })

    def workspace(self, **kwargs):
        values = {'project': self.project, 'owner': self.user, 'model_id': self.model.id}
        values.update(kwargs)
        return APIWorkspace.objects.create(**values)

    def pipeline(self, *, workflow=None, queued_at=None):
        endpoint = self.endpoint('GET', f'/items-{APIEndpoint.objects.count()}')
        workspace = self.workspace(
            spec=self.spec, endpoint_ids=[endpoint.id], status='generating', task_id='task',
        )
        budget = generation_budget(model_id=self.model.id, owner=self.user, queued_at=queued_at)
        snapshot = {
            'revision': 0, 'task_id': 'task', 'mode': 'generate', 'draft': workspace.draft,
            'model_id': self.model.id, 'spec_id': self.spec.id, 'endpoints': [],
            'target_url': 'https://example.test', 'variables': {}, 'messages': [],
            **deepcopy(budget),
        }
        if workflow:
            snapshot['workflow'] = workflow
        workspace.generation = {
            'status': 'queued', 'phase': 'queued', 'source_revision': 0, 'rounds': [],
            '_snapshot': snapshot, **deepcopy(budget),
        }
        workspace.save(update_fields=['generation', 'updated_at'])
        return workspace

    def test_execution_checkpoint_history_and_standard_report_survive_regeneration(self):
        endpoint = self.endpoint('GET', '/report', responses={'200': {'description': 'ok'}})
        draft = self.draft(endpoint)
        workspace = self.workspace(
            spec=self.spec, endpoint_ids=[endpoint.id], draft=draft,
            status='debugging', task_id='debug-task',
        )
        execution_id = start_workspace_execution(
            workspace, revision=0, task_id='debug-task', source=WORKSPACE_DEBUG,
            attempt=1, draft=draft, options={'base_url': 'https://example.test'},
        )
        partial = {'success': False, 'log': '真实 checkpoint 日志', 'step_datas': []}
        self.assertTrue(store_workspace_progress(execution_id, partial))
        self.assertTrue(finish_workspace_execution(execution_id, None, error='runner disconnected'))
        execution = APITestExecution.objects.get(pk=execution_id)
        self.assertEqual(execution.execution_log, '真实 checkpoint 日志')
        self.assertEqual(json.loads(execution.case_execution_detail.httprunner_result), partial)
        self.assertIsInstance(execution.input_snapshot['cases'][0]['script'], dict)

        workspace.generation = {'status': 'queued', 'rounds': []}
        workspace.candidate = None
        workspace.save(update_fields=['generation', 'candidate', 'updated_at'])
        attach_execution_histories([workspace])
        self.assertEqual(serialize_workspace(workspace)['execution_history'][0]['id'], execution_id)
        report = APITestExecutionReportView.as_view()(
            self.request('get'), project_id=self.project.id, pk=execution_id,
        )
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['data']['httprunner_result'], partial)

    def test_partial_progress_uses_checkpoint_not_future_step_placeholders(self):
        endpoint = self.endpoint('GET', '/progress')
        draft = self.draft(endpoint)
        draft['teststeps'] = [deepcopy(draft['teststeps'][0]) for _ in range(3)]
        workspace = self.workspace(spec=self.spec, endpoint_ids=[endpoint.id], draft=draft,
                                   status='debugging', task_id='progress-task')
        execution_id = start_workspace_execution(
            workspace, revision=0, task_id='progress-task', source=WORKSPACE_DEBUG,
            attempt=1, draft=draft, options={},
        )
        placeholders = [
            {'status': 'unknown'}, {'status': 'skipped'}, {'status': 'skipped'},
        ]
        store_workspace_progress(execution_id, {
            'success': False, 'checkpoint': {'completed_steps': 0, 'total_steps': 3, 'current_step': 0},
            'step_datas': placeholders,
        })
        execution = APITestExecution.objects.get(pk=execution_id)
        self.assertEqual((execution.progress, execution.error_steps), (0, 0))
        store_workspace_progress(execution_id, {
            'success': False, 'checkpoint': {'completed_steps': 1, 'total_steps': 3, 'current_step': 1},
            'step_datas': [{'status': 'passed'}, {'status': 'unknown'}, {'status': 'skipped'}],
        })
        execution.refresh_from_db()
        self.assertEqual((execution.progress, execution.success_steps, execution.error_steps), (33, 1, 0))

    def test_cancel_is_revision_guarded_family_wide_idempotent_and_late_finish_safe(self):
        endpoint = self.endpoint('GET', '/cancel-review')
        original = self.draft(endpoint, status=200)
        candidate = self.draft(endpoint, status=201)
        candidate_hash = draft_hash(candidate)
        root = self.workspace(draft=original, status='generating', task_id='root-task', candidate={
            'draft': candidate, 'draft_hash': candidate_hash, 'source_revision': 0,
            'review': assertion_review(candidate, previous=original, protected_changed=True),
        }, generation={
            'status': 'running', 'source_revision': 0, 'rounds': [{'attempt': 1}],
        })
        child = self.workspace(parent=root, status='generating', task_id='child-task', generation={
            'status': 'running', 'source_revision': 0, 'rounds': [{'attempt': 1}],
        })
        execution_id = start_workspace_execution(
            root, revision=0, task_id='root-task', source=WORKSPACE_GENERATION,
            attempt=1, draft=original, options={},
        )
        self.assertTrue(store_workspace_progress(execution_id, {
            'success': False, 'log': '取消前真实执行日志', 'step_datas': [],
        }))
        first = APIWorkspaceCancelView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(first.status_code, 200, first.data)
        root.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual((root.status, child.status), ('ready', 'ready'))
        self.assertEqual((root.generation['status'], child.generation['status']), ('cancelled', 'cancelled'))
        self.assertEqual(root.revision, 1)
        self.assertEqual(root.candidate['source_revision'], 1)
        self.assertEqual(root.generation['source_revision'], 1)
        execution = APITestExecution.objects.get(pk=execution_id)
        self.assertEqual(execution.status, 'stopped')
        self.assertIn('取消前真实执行日志', execution.execution_log)
        self.assertIn('工作区取消时保留已有执行证据', execution.execution_log)
        _finish_pipeline(root.id, 0, 'root-task', 'passed', 'late result')
        root.refresh_from_db()
        self.assertEqual(root.generation['status'], 'cancelled')
        repeated = APIWorkspaceCancelView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        adopted = APIWorkspaceDetailView.as_view()(
            self.request('patch', {
                'revision': 1, 'draft': candidate, 'assertion_review_ack': candidate_hash,
            }), project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(adopted.status_code, 200, adopted.data)
        self.assertIsNone(adopted.data['data']['candidate'])
        root.refresh_from_db()
        root.status, root.task_id = 'generating', 'new-task'
        root.generation = {'status': 'queued', 'source_revision': root.revision}
        root.save(update_fields=['status', 'task_id', 'generation', 'updated_at'])
        new_execution_id = start_workspace_execution(
            root, revision=root.revision, task_id='new-task', source=WORKSPACE_GENERATION,
            attempt=1, draft=candidate, options={},
        )
        stale = APIWorkspaceCancelView.as_view()(
            self.request('post', {'revision': 0}), project_id=self.project.id, workspace_id=root.id,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(APITestExecution.objects.get(pk=new_execution_id).status, 'running')

    def test_debug_progress_creates_execution_and_returns_execution_id(self):
        endpoint = self.endpoint('GET', '/debug')
        draft = self.draft(endpoint)
        workspace = self.workspace(
            spec=self.spec, endpoint_ids=[endpoint.id], draft=draft,
            status='debugging', task_id='debug-task', debug_revision=0,
        )
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        workspace.debug_snapshot = {
            'revision': 0, 'task_id': 'debug-task', 'draft': draft,
            'environment': {'base_url': 'https://example.test'}, 'variables': {}, 'budget': budget,
        }
        workspace.debug_result = {'status': 'queued', **budget}
        workspace.save(update_fields=['debug_snapshot', 'debug_result', 'updated_at'])
        checkpoint = {'success': False, 'log': 'step one', 'step_datas': []}
        final = {'success': True, 'log': 'done', 'step_datas': [{
            'status': 'passed', 'validators': {'validate_extractor': [{'passed': True}]},
        }]}

        def runner(**kwargs):
            self.assertFalse(kwargs['should_cancel']())
            kwargs['on_progress'](checkpoint)
            return final

        with patch('api_testing.requests_runner.requests_runner', side_effect=runner):
            result = debug_api_workspace.apply(args=(workspace.id, 0, 'debug-task')).result
        self.assertEqual(result['status'], 'ready')
        workspace.refresh_from_db()
        execution_id = workspace.debug_result['execution_id']
        execution = APITestExecution.objects.get(pk=execution_id)
        self.assertEqual(execution.status, 'passed')
        self.assertEqual(execution.execution_log, 'done')

    def test_queue_wait_does_not_consume_active_budget_and_root_batch_is_independent(self):
        queued_at = timezone.now() - timedelta(minutes=10)
        scene = self.pipeline(queued_at=queued_at)
        snapshot = _claim_pipeline(scene.id, 0, 'task')
        claimed = datetime.fromisoformat(snapshot['claimed_at'])
        execution_deadline = datetime.fromisoformat(snapshot['deadlines']['execution_at'])
        self.assertGreater((execution_deadline - claimed).total_seconds(), 1799)

        root = self.pipeline(workflow='scenarios', queued_at=queued_at)
        root_snapshot = _claim_pipeline(root.id, 0, 'task')
        self.assertIsNone(root_snapshot['deadlines']['execution_at'])
        self.assertGreater(_remaining_pipeline_seconds(root_snapshot), 7199)

        stale = self.pipeline()
        stale.generation['_snapshot']['deadlines']['queue_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        stale.save(update_fields=['generation', 'updated_at'])
        self.assertEqual(_claim_pipeline(stale.id, 0, 'task')['_terminal_status'], 'failed')
        stale.refresh_from_db()
        self.assertIn('未消耗执行预算', stale.error)

    def test_provenance_review_ack_and_manual_edit_are_distinct(self):
        documented = self.endpoint('POST', '/create', responses={'201': {'description': 'created'}})
        inferred = self.endpoint('GET', '/read')
        user_draft = self.draft(documented, status=200)
        candidate = normalize_draft({
            'version': 1, 'config': user_draft['config'],
            'teststeps': [
                {**deepcopy(user_draft['teststeps'][0]), 'validate': [{'eq': ['status_code', 200]}]},
                {'name': 'read', 'endpoint_id': inferred.id,
                 'request': {'method': 'GET', 'url': '/read'},
                 'validate': [{'eq': ['status_code', 201]}, {'eq': ['body.code', 0]}]},
            ],
        })
        provenance = assertion_provenance(
            candidate,
            endpoints=[
                {'id': documented.id, 'responses': documented.responses},
                {'id': inferred.id, 'responses': {'201': {'description': 'documented'}}},
            ],
            user_draft=user_draft,
        )
        self.assertEqual(provenance[0]['assertions'][0]['source'], 'user')
        self.assertEqual(provenance[1]['assertions'][0]['source'], 'grounded')
        self.assertEqual(provenance[1]['assertions'][1]['source'], 'ai_proposed')
        repaired_provenance = assertion_provenance(
            changed := self.draft(documented, status=201),
            endpoints=[{'id': documented.id, 'responses': {}}],
            user_draft=user_draft,
        )
        self.assertEqual(repaired_provenance[0]['assertions'][0]['source'], 'ai_proposed')
        adopted_provenance = assertion_provenance(
            changed, endpoints=[{'id': documented.id, 'responses': documented.responses}],
            user_draft=changed,
        )
        self.assertEqual(adopted_provenance[0]['assertions'][0]['source'], 'user')

        candidate_hash = draft_hash(changed)
        workspace = self.workspace(draft=user_draft, candidate={
            'draft': changed, 'draft_hash': candidate_hash, 'source_revision': 0,
            'review': assertion_review(changed, previous=user_draft, protected_changed=True),
        }, generation={'status': 'needs_review', 'source_revision': 0, 'rounds': []})
        missing = APIWorkspaceDetailView.as_view()(
            self.request('patch', {'revision': 0, 'draft': changed}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(missing.status_code, 409)
        manual = deepcopy(changed)
        manual['config']['name'] = 'manual choice'
        accepted_manual = APIWorkspaceDetailView.as_view()(
            self.request('patch', {'revision': 0, 'draft': manual}),
            project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(accepted_manual.status_code, 200, accepted_manual.data)

        workspace = self.workspace(draft=user_draft, candidate={
            'draft': changed, 'draft_hash': candidate_hash, 'source_revision': 0,
            'review': assertion_review(changed, previous=user_draft, protected_changed=True),
        }, generation={'status': 'needs_review', 'source_revision': 0, 'rounds': []})
        accepted = APIWorkspaceDetailView.as_view()(
            self.request('patch', {
                'revision': 0, 'draft': changed, 'assertion_review_ack': candidate_hash,
            }), project_id=self.project.id, workspace_id=workspace.id,
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertIsNone(accepted.data['data']['candidate'])

    def test_unsafe_replay_stops_automatic_repair(self):
        status, summary, disposition = classify_result({
            'success': False, 'error_type': 'ExtractionFailure',
            'replay_safety': {'safe_to_retry': False}, 'step_datas': [],
        }, {'teststeps': []})
        self.assertEqual((status, disposition), ('needs_review', 'stop'))
        self.assertIn('不可安全重放', summary)

    def test_prompt_failure_evidence_keeps_real_failure_and_counts_skipped_tail(self):
        evidence = _prompt_failure_evidence({
            'success': False,
            'error_type': 'RequestFailure',
            'step_datas': [
                {'name': 'failed request', 'status': 'failed', 'data': {'req_resps': [{
                    'request': {'method': 'POST', 'url': '/items'},
                    'response': {'status_code': 503, 'body': {'error': 'unavailable'}},
                }]}},
                *[{'name': f'skipped {index}', 'status': 'skipped'} for index in range(10)],
            ],
        })
        self.assertEqual(evidence['failed_steps'][0]['response']['status_code'], 503)
        self.assertEqual(evidence['skipped_step_count'], 10)
        self.assertNotIn('step_datas', evidence)

    def test_child_prompt_has_only_target_dependency_details_and_full_light_catalog(self):
        dependency = self.endpoint('POST', '/session', summary='session')
        target = self.endpoint('GET', '/profile', summary='profile')
        unrelated = self.endpoint('GET', '/metrics', summary='metrics')
        root = self.workspace(
            spec=self.spec, endpoint_ids=[dependency.id, target.id, unrelated.id],
            status='generating', task_id='root-task',
        )
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        budget['claimed_at'] = budget['started_at'] = timezone.now().isoformat()
        budget['deadlines']['batch_at'] = (timezone.now() + timedelta(hours=2)).isoformat()
        endpoints = [{
            'id': item.id, 'method': item.method, 'path': item.path, 'summary': item.summary,
            'description': 'large detail', 'parameters': [], 'request_body': {},
            'responses': {}, 'operation_id': '', 'document_context': {},
        } for item in (dependency, target, unrelated)]
        snapshot = {
            'target_url': 'https://example.test', 'variables': {}, 'messages': [],
            'scope_endpoint_ids': [item.id for item in (dependency, target, unrelated)],
            'endpoints': endpoints, **budget,
        }
        root.generation = {'_snapshot': snapshot}
        root.save(update_fields=['generation', 'updated_at'])
        plan = {'scenarios': [{
            'title': 'profile', 'description': '', 'endpoint_ids': [target.id],
            'dependency_endpoint_ids': [dependency.id], 'dependency_evidence': 'security',
            'authenticated_endpoint_ids': [target.id], 'requires_authenticated_context': True,
        }]}
        children = _scenario_children(
            root_id=root.id, revision=0, task_id='root-task', snapshot=snapshot, plan=plan,
        )
        child = APIWorkspace.objects.get(pk=children[0][0])
        child_snapshot = child.generation['_snapshot']
        self.assertEqual([item['id'] for item in child_snapshot['endpoints']], [dependency.id, target.id])
        self.assertEqual([item['id'] for item in child_snapshot['scope_catalog']], [
            dependency.id, target.id, unrelated.id,
        ])
        payload = json.loads(_generation_messages(
            conversation=[], draft=child.draft, endpoints=child_snapshot['endpoints'],
            mode='generate', failure_evidence=None, scenario=child_snapshot['scenario'],
            scope_catalog=child_snapshot['scope_catalog'],
        )[1].content)
        self.assertEqual([item['id'] for item in payload['selected_endpoints']], [dependency.id, target.id])
        self.assertEqual(set(payload['scope_catalog'][2]), {'id', 'method', 'path', 'name'})

        supplemented = _supplement_candidate_endpoints(
            {'teststeps': [{'endpoint_id': unrelated.id}]},
            current=child_snapshot['endpoints'], scope_catalog=child_snapshot['scope_catalog'],
            frozen_scope_specs=child_snapshot['frozen_scope_specs'],
        )
        self.assertEqual([item['id'] for item in supplemented], [dependency.id, target.id, unrelated.id])
        outside = _supplement_candidate_endpoints(
            {'teststeps': [{'endpoint_id': 999999}]},
            current=child_snapshot['endpoints'], scope_catalog=child_snapshot['scope_catalog'],
            frozen_scope_specs=child_snapshot['frozen_scope_specs'],
        )
        self.assertEqual(outside, child_snapshot['endpoints'])
        outside_draft = normalize_draft({
            'version': 1, 'config': {'name': 'outside', 'variables': {}},
            'teststeps': [{
                'name': 'outside', 'endpoint_id': 999999,
                'request': {'method': 'GET', 'url': '/outside'},
                'validate': [{'eq': ['status_code', 200]}],
            }],
        })
        with self.assertRaises(WorkspaceValidationError):
            prepare_candidate(
                outside_draft, endpoints=outside, target_url='https://example.test', variables={},
            )
