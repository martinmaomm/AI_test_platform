"""Focused offline contracts for multi-scenario conversation isolation."""
import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project
from .models import APIEndpoint, APISpecification, APIWorkspace, default_api_workspace_draft
from .workspace_service import endpoint_specs, generation_budget, normalize_draft
from .workspace_tasks import (
    _generation_messages, _parse_plan, _planner_messages, _scenario_children,
    generate_and_verify_api_workspace,
)
from .workspace_verification import ScenarioScopeViolation, prepare_candidate
from .workspace_views import APIWorkspaceMessagesView


class WorkspaceScenarioScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='scenario-scope-owner', email='scenario-scope@example.test', password='pw',
        )
        self.project = Project.objects.create(
            name='Scenario scope', project_type='api', owner=self.user, created_by=self.user,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='openai', model_name='offline-scope',
            created_by=self.user, is_active=True,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='Scenario scope API',
            status=APISpecification.TaskStatus.COMPLETED,
            metadata={
                'security': [{'bearer': []}],
                'components': {'securitySchemes': {
                    'bearer': {'type': 'http', 'scheme': 'bearer'},
                }},
            },
        )
        self.login = APIEndpoint.objects.create(spec=self.spec, method='POST', path='/session')
        self.target = APIEndpoint.objects.create(spec=self.spec, method='GET', path='/records')
        self.endpoints = endpoint_specs(
            self.project.id, [self.login.id, self.target.id], spec_id=self.spec.id,
        )
        self.root_message = {
            'role': 'user',
            'content': '根目标同时包含已认证流程和独立未认证流程；每轮数据必须唯一并清理。',
            'mode': 'generate',
        }
        self.shared_constraints = ['每轮使用唯一数据，只清理本轮创建的数据。']
        self.factory = APIRequestFactory()

    def plan(self):
        return _parse_plan({
            'shared_constraints': self.shared_constraints,
            'summary': '两个认证语义独立的场景',
            'scenarios': [
                {
                    'title': 'Authenticated lifecycle',
                    'description': '建立本场景会话后查询，并保留成功业务断言。',
                    'endpoint_ids': [self.target.id],
                    'dependency_endpoint_ids': [self.login.id],
                    'dependency_evidence': 'OpenAPI security 及登录响应字段支持该依赖。',
                    'authenticated_endpoint_ids': [self.target.id],
                    'requires_authenticated_context': True,
                },
                {
                    'title': 'Unauthenticated query',
                    'description': '不建立会话且不携带认证，断言文档约定的负向结果。',
                    'endpoint_ids': [self.target.id],
                    'dependency_endpoint_ids': [],
                    'dependency_evidence': '',
                    'authenticated_endpoint_ids': [],
                    'requires_authenticated_context': False,
                },
            ],
        }, endpoint_ids={self.login.id, self.target.id})

    def root_and_children(self):
        root = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec, model_id=self.model.id,
            endpoint_ids=[self.login.id, self.target.id], messages=[self.root_message],
            status=APIWorkspace.Status.GENERATING, task_id='scope-root-task',
        )
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        budget['deadlines']['batch_at'] = (
            timezone.now() + timedelta(seconds=budget['timeouts']['batch_seconds'])
        ).isoformat()
        snapshot = {
            'target_url': 'https://example.test', 'variables': {},
            'messages': [self.root_message],
            'scope_endpoint_ids': [self.login.id, self.target.id],
            'endpoints': deepcopy(self.endpoints), **budget,
        }
        children = _scenario_children(
            root_id=root.id, revision=0, task_id=root.task_id,
            snapshot=snapshot, plan=self.plan(),
        )
        return root, [APIWorkspace.objects.get(pk=item[0]) for item in children]

    def draft(self, *, include_auth=True, include_unauthenticated_duplicate=False):
        steps = [{
            'name': 'establish local session', 'endpoint_id': self.login.id,
            'request': {'method': 'POST', 'url': '/session'},
            'extract': {'token': 'body.data.token'},
            'validate': [{'eq': ['status_code', 200]}],
        }, {
            'name': 'authenticated target', 'endpoint_id': self.target.id,
            'request': {
                'method': 'GET', 'url': '/records',
                'headers': {'Authorization': 'Bearer ${token}'} if include_auth else {},
            },
            'extract': {}, 'validate': [{'eq': ['body.result', 'expected-success']}],
        }]
        if include_unauthenticated_duplicate:
            steps.append({
                'name': 'other scenario negative target', 'endpoint_id': self.target.id,
                'request': {'method': 'GET', 'url': '/records'}, 'extract': {},
                'validate': [{'eq': ['body.result', 'expected-negative']}],
            })
        return normalize_draft({
            'version': 1, 'config': {'name': 'scoped scenario', 'variables': {}},
            'teststeps': steps,
        })

    def test_planner_and_children_persist_shared_constraints_without_root_messages(self):
        rules = _planner_messages(conversation=[self.root_message], endpoints=self.endpoints)[0].content
        self.assertIn('shared_constraints', rules)
        self.assertIn('不得把其他场景目标复制进来', rules)
        root, children = self.root_and_children()
        self.assertEqual(root.messages, [self.root_message])
        self.assertEqual([child.messages for child in children], [[], []])
        for child in children:
            self.assertEqual(child.generation['_snapshot']['messages'], [])
            self.assertEqual(child.generation['_snapshot']['shared_constraints'], self.shared_constraints)
            self.assertEqual(child.generation['shared_constraints'], self.shared_constraints)
        self.assertEqual(children[0].generation['_snapshot']['scenario']['authenticated_endpoint_ids'], [self.target.id])
        self.assertEqual(children[1].generation['_snapshot']['scenario']['authenticated_endpoint_ids'], [])

        local_message = {'role': 'user', 'content': '只调整当前场景的局部查询参数。', 'mode': 'repair'}
        prompt = json.loads(_generation_messages(
            conversation=[local_message], draft=default_api_workspace_draft(),
            endpoints=children[0].generation['_snapshot']['endpoints'], mode='repair',
            failure_evidence={}, scenario=children[0].generation['_snapshot']['scenario'],
            shared_constraints=self.shared_constraints,
        )[1].content)
        self.assertEqual(prompt['shared_constraints'], self.shared_constraints)
        self.assertEqual(prompt['conversation'], [local_message])
        self.assertNotIn(self.root_message, prompt['conversation'])

    def test_same_endpoint_positive_and_negative_semantics_remain_separate(self):
        positive, negative = self.plan()['scenarios']
        valid_positive = self.draft()
        prepare_candidate(
            valid_positive, endpoints=self.endpoints, target_url='https://example.test', variables={},
            required_endpoint_ids={self.target.id}, authenticated_target_ids={self.target.id},
            cookie_session_dependency_ids={self.login.id}, scenario=positive,
        )
        negative_draft = normalize_draft({
            'version': 1, 'config': {'name': 'negative', 'variables': {}},
            'teststeps': [{
                'name': 'unauthenticated target', 'endpoint_id': self.target.id,
                'request': {'method': 'GET', 'url': '/records'}, 'extract': {},
                'validate': [{'eq': ['body.result', 'expected-negative']}],
            }],
        })
        prepare_candidate(
            negative_draft, endpoints=[self.endpoints[1]], target_url='https://example.test', variables={},
            required_endpoint_ids={self.target.id}, authenticated_target_ids=set(), scenario=negative,
        )
        with self.assertRaisesRegex(ScenarioScopeViolation, 'current_scenario.*认证契约冲突'):
            prepare_candidate(
                self.draft(include_unauthenticated_duplicate=True), endpoints=self.endpoints,
                target_url='https://example.test', variables={},
                required_endpoint_ids={self.target.id}, authenticated_target_ids={self.target.id},
                cookie_session_dependency_ids={self.login.id}, scenario=positive,
            )

    def test_automatic_repair_receives_scope_conflict_and_removes_other_scenario_step(self):
        _root, children = self.root_and_children()
        child = children[0]
        polluted = self.draft(include_unauthenticated_duplicate=True)
        repaired = self.draft()
        prompts = []

        def stream(messages, callback=None, **_kwargs):
            prompts.append((messages[0].content, json.loads(messages[1].content)))
            output = json.dumps(polluted if len(prompts) == 1 else repaired)
            if callback:
                callback(output)
            return output

        passed = {
            'success': True, 'error_type': '',
            'step_datas': [{
                'status': 'passed',
                'validators': {'validate_extractor': [{'passed': True}]},
                'data': {'req_resps': [{'response': {'status_code': 200}}]},
            }, {
                'status': 'passed',
                'validators': {'validate_extractor': [{'passed': True}]},
                'data': {'req_resps': [{'response': {'status_code': 200}}]},
            }],
        }
        manager = SimpleNamespace(stream_invoke=stream)
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
             patch('api_testing.requests_runner.requests_runner', return_value=passed) as runner:
            result = generate_and_verify_api_workspace.apply(
                args=(child.id, child.revision, child.task_id),
            )
        self.assertEqual(result.result['status'], 'passed')
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0][1]['conversation'], [])
        self.assertEqual(prompts[0][1]['shared_constraints'], self.shared_constraints)
        self.assertEqual(prompts[1][1]['failure_evidence']['error_type'], 'ScenarioScopeViolation')
        self.assertIn('删除该完整越界步骤及其断言', prompts[1][0])
        self.assertNotIn(self.root_message, prompts[1][1]['conversation'])
        runner.assert_called_once()
        child.refresh_from_db()
        self.assertEqual(len(child.candidate['draft']['teststeps']), 2)

    def test_existing_child_draft_keeps_strict_assertion_protection(self):
        baseline = self.draft(include_unauthenticated_duplicate=True)
        candidate = self.draft()
        from .workspace_verification import assertions_preserved, protected_expected_values, step_assertions
        self.assertFalse(assertions_preserved(
            step_assertions(baseline), protected_expected_values(baseline), candidate,
        ))

    def test_manual_child_repair_persists_only_child_follow_up_messages(self):
        root, children = self.root_and_children()
        for workspace in [root, *children]:
            workspace.status = APIWorkspace.Status.READY
            workspace.task_id = ''
            workspace.save(update_fields=['status', 'task_id', 'updated_at'])
        child = children[0]
        prior_local = {'role': 'user', 'content': '保留当前场景的精确查询断言。', 'mode': 'repair'}
        child.messages = [prior_local]
        child.draft = self.draft()
        child.debug_revision = child.revision
        child.debug_result = {'success': False, 'error_type': 'ExtractionFailure', 'step_datas': []}
        child.generation = {**child.generation, 'status': 'needs_review', 'phase': 'finished'}
        child.save()
        request = self.factory.post('/', {
            'revision': child.revision, 'mode': 'repair',
            'message': '只修复当前场景的提取路径。',
            'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {},
        }, format='json')
        force_authenticate(request, user=self.user)
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queued, \
             self.captureOnCommitCallbacks(execute=True):
            response = APIWorkspaceMessagesView.as_view()(
                request, project_id=self.project.id, workspace_id=child.id,
            )
        self.assertEqual(response.status_code, 202, response.data)
        queued.assert_called_once()
        child.refresh_from_db()
        expected_messages = [prior_local, {
            'role': 'user', 'content': '只修复当前场景的提取路径。', 'mode': 'repair',
        }]
        self.assertEqual(child.messages, expected_messages)
        self.assertEqual(child.generation['_snapshot']['messages'], expected_messages)
        self.assertEqual(child.generation['_snapshot']['shared_constraints'], self.shared_constraints)
        self.assertNotIn(self.root_message, child.generation['_snapshot']['messages'])
