"""Focused policy regressions for scoped CRUD exploration."""

from django.test import SimpleTestCase

from .exploration_policy import ExplorationPolicy
from .generation_contracts import ScenarioSpec


def spec(*, intent='create', forbidden_actions=None, cleanup=True):
    payload = {
        'title': '创建测试用户', 'objective': '验证测试用户 CRUD',
        'steps': [{
            'id': 'S1', 'name': '创建用户', 'intent': intent,
            'target_hint': '测试用户', 'mutates_data': True, 'expected': '保存成功',
        }],
        'assertions': [{
            'id': 'A1', 'name': '操作可见', 'target_hint': '测试用户',
            'expected': '可见', 'step_id': 'S1',
        }],
        'forbidden_actions': forbidden_actions or [],
    }
    if cleanup:
        payload['cleanup'] = [{
            'id': 'C1', 'name': '删除测试用户', 'target_hint': '测试用户',
            'condition': '本轮创建后删除', 'step_id': 'S1',
        }]
    return ScenarioSpec.model_validate(payload)


class ExplorationPolicyTests(SimpleTestCase):
    def test_existing_data_bans_do_not_block_namespace_cleanup(self):
        policy = ExplorationPolicy.for_scenario(
            spec(forbidden_actions=['不要修改任何已有数据', '禁止删除已有用户']),
            generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f', user_constraints='',
        )
        self.assertEqual(policy.data_scope, 'namespace')
        self.assertEqual(policy.allowed_operations, frozenset({'create', 'delete'}))
        self.assertFalse(policy.explicit_read_only)

    def test_create_target_allows_declared_cleanup_delete(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f', user_constraints='',
        )
        self.assertTrue(policy.allows('create'))
        self.assertTrue(policy.allows('delete'))
        self.assertFalse(policy.allows('update'))

    def test_negated_existing_identifier_never_opens_user_scope(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='不要修改已有用户 ID:1，只创建本轮测试数据。',
        )
        self.assertEqual(policy.data_scope, 'namespace')

    def test_affirmative_identifier_bounded_write_opens_user_scope(self):
        policy = ExplorationPolicy.for_scenario(
            spec(intent='update'), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='修改已有用户 ID:1 的昵称。',
        )
        self.assertEqual(policy.data_scope, 'user_specified')

    def test_raw_submission_prohibition_blocks_crud_without_normalizer_forbidden_actions(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='探索阶段不提交新增、编辑或删除；不要提交表单。',
        )
        self.assertTrue(policy.explicit_read_only)
        self.assertFalse(policy.allowed_operations)

    def test_existing_data_submission_prohibition_does_not_block_namespace_crud(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='不要提交已有数据，只操作本轮 namespace。',
        )
        self.assertFalse(policy.explicit_read_only)
        self.assertEqual(policy.allowed_operations, frozenset({'create', 'delete'}))

    def test_existing_data_modification_prohibition_is_not_global_read_only(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='禁止修改已有数据，只创建和清理本轮数据。',
        )
        self.assertFalse(policy.explicit_read_only)
        self.assertEqual(policy.allowed_operations, frozenset({'create', 'delete'}))

    def test_global_read_only_survives_a_separate_existing_data_caveat(self):
        policy = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
            user_constraints='探索阶段只读，不要操作已有数据。后续脚本新增本轮用户。',
        )
        self.assertTrue(policy.explicit_read_only)
        self.assertFalse(policy.allowed_operations)

    def test_each_attempt_gets_a_distinct_namespace(self):
        first = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f', user_constraints='',
        )
        second = ExplorationPolicy.for_scenario(
            spec(), generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f', user_constraints='',
        )
        self.assertNotEqual(first.namespace, second.namespace)
        self.assertTrue(first.namespace.startswith('aits-explore-52ae9c6a-'))
