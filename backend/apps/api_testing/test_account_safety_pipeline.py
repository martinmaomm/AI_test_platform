"""Generation recovery must preserve drafts, without replaying unsafe writes."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from . import test_workspace as fixtures
from .workspace_tasks import generate_and_verify_api_workspace
from .workspace_verification import classify_result, account_safety_regressions


class AccountSafetyPipelineTests(TestCase):
    setUp = fixtures.APIWorkspaceTests.setUp
    workspace = fixtures.APIWorkspaceTests.workspace
    endpoint = fixtures.APIWorkspaceTests.endpoint
    pipeline_workspace = fixtures.APIWorkspaceTests.pipeline_workspace

    @staticmethod
    def draft(endpoint, name='普通查询'):
        return {
            'version': 1, 'config': {'name': name, 'variables': {}},
            'teststeps': [{
                'name': name, 'endpoint_id': endpoint.id,
                'request': {'method': 'GET', 'url': '/items'},
                'validate': [{'eq': ['status_code', 200]}],
            }],
        }

    @staticmethod
    def issue():
        return {'step': 1, 'code': 'AccountSafetyReview',
                'reason': '请补齐本轮临时用户的身份绑定。', 'repairable': True}

    def run_pipeline(self, workspace, candidates, issues, result=None):
        manager = SimpleNamespace(stream_invoke=Mock(side_effect=[json.dumps(d) for d in candidates]))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=manager), \
             patch('api_testing.requests_runtime.account_safety_issues', side_effect=issues), \
             patch('api_testing.requests_runner.requests_runner', return_value=result or fixtures.APIWorkspaceTests.passed_result()) as runner:
            outcome = generate_and_verify_api_workspace.apply(args=(workspace.id, 0, workspace.task_id))
        workspace.refresh_from_db()
        return outcome.result, manager, runner

    def test_ordinary_case_still_generates_and_runs_in_one_round(self):
        workspace, endpoint = self.pipeline_workspace()
        outcome, manager, runner = self.run_pipeline(workspace, [self.draft(endpoint)], [[]])
        self.assertEqual(outcome['status'], 'passed')
        self.assertEqual(manager.stream_invoke.call_count, 1)
        runner.assert_called_once()

    def test_review_is_repaired_before_any_target_execution(self):
        workspace, endpoint = self.pipeline_workspace()
        first = self.draft(endpoint, '待调整的草稿')
        fixed = self.draft(endpoint, '本轮临时账号方案')
        outcome, manager, runner = self.run_pipeline(workspace, [first, fixed], [[self.issue()], []])
        self.assertEqual(outcome['status'], 'passed')
        self.assertEqual(manager.stream_invoke.call_count, 2)
        runner.assert_called_once()
        self.assertEqual(workspace.generation['rounds'][0]['status'], 'needs_review')
        self.assertFalse(workspace.generation['rounds'][0]['runnable'])
        self.assertIsNone(workspace.generation['rounds'][0]['execution_id'])
        self.assertEqual(workspace.generation['rounds'][0]['draft']['config']['name'], '待调整的草稿')
        self.assertEqual(workspace.candidate['draft']['config']['name'], '本轮临时账号方案')
        repair_text = '\n'.join(message.content for message in manager.stream_invoke.call_args.args[0])
        self.assertIn('AccountSafetyReview', repair_text)
        self.assertIn('临时', repair_text)

    def test_three_reviews_keep_candidate_instead_of_generation_failure(self):
        workspace, endpoint = self.pipeline_workspace()
        candidate = self.draft(endpoint)
        outcome, manager, runner = self.run_pipeline(
            workspace, [candidate] * 3, [[self.issue()]] * 3,
        )
        self.assertEqual(outcome['status'], 'needs_review')
        self.assertEqual(manager.stream_invoke.call_count, 3)
        runner.assert_not_called()
        self.assertEqual(len(workspace.generation['rounds']), 3)
        self.assertEqual(workspace.candidate['verification_status'], 'needs_review')
        self.assertEqual(len(workspace.candidate['draft']['teststeps']), 1)
        self.assertIn('草稿已保留', workspace.generation['summary'])

    def test_unresolvable_review_does_not_repeat_model_calls(self):
        workspace, endpoint = self.pipeline_workspace()
        issue = {**self.issue(), 'repairable': False}
        outcome, manager, runner = self.run_pipeline(workspace, [self.draft(endpoint)], [[issue]])
        self.assertEqual(outcome['status'], 'needs_review')
        self.assertEqual(manager.stream_invoke.call_count, 1)
        runner.assert_not_called()
        self.assertTrue(workspace.candidate['draft'])

    def test_runtime_protection_keeps_evidence_without_automatic_replay(self):
        workspace, endpoint = self.pipeline_workspace()
        result = deepcopy(fixtures.APIWorkspaceTests.passed_result())
        result.update(success=False, error_type='AccountSafetyBlocked', error='已阻止修改登录账号。')
        result['step_datas'][0]['status'] = 'error'
        outcome, manager, runner = self.run_pipeline(workspace, [self.draft(endpoint)], [[]], result)
        self.assertEqual(outcome['status'], 'needs_review')
        self.assertEqual(manager.stream_invoke.call_count, 1)
        runner.assert_called_once()
        self.assertTrue(workspace.generation['rounds'][0]['execution_id'])
        self.assertEqual(workspace.generation['rounds'][0]['result']['error_type'], 'AccountSafetyBlocked')

    def test_review_explanation_precedes_generic_replay_warning(self):
        result = {'success': False, 'error_type': 'AccountSafetyReview',
                  'error': '步骤 4 无法确认角色属于本轮数据，未发送请求。',
                  'replay_safety': {'safe_to_retry': False}}
        status, summary, disposition = classify_result(result, {'teststeps': [{}]})
        self.assertEqual((status, disposition), ('needs_review', 'review'))
        self.assertIn('步骤 4', summary)

    def test_repairs_cannot_remove_known_endpoint_protection(self):
        old = {'teststeps': [{'endpoint_id': 11, 'name': '原步骤',
                             'request': {'method': 'POST', 'url': '/principals/${old}'},
                             'account_safety': {'effect': 'mutate', 'resource': 'account'}}]}
        new = deepcopy(old)
        new['teststeps'][0]['name'] = '改名不能绕过'
        new['teststeps'][0]['request']['url'] = '/principals/${temporary}'
        new['teststeps'][0].pop('account_safety')
        self.assertEqual(account_safety_regressions(old, new)[0]['step'], 1)
        new['teststeps'][0]['account_safety'] = deepcopy(old['teststeps'][0]['account_safety'])
        self.assertEqual(account_safety_regressions(old, new), [])
        self.assertEqual(account_safety_regressions({'teststeps': []}, new), [])

    def test_repair_cannot_relabel_a_known_mutation_as_creation(self):
        old = {'teststeps': [{'endpoint_id': 17, 'request': {'method': 'POST', 'url': '/rpc'},
                             'account_safety': [{'operation': 'mutate', 'entity': 'account'}]}]}
        new = deepcopy(old)
        new['teststeps'][0]['account_safety'][0]['operation'] = 'create'
        self.assertTrue(account_safety_regressions(old, new))
        new['teststeps'].append(deepcopy(old['teststeps'][0]))
        self.assertEqual(account_safety_regressions(old, new), [])
