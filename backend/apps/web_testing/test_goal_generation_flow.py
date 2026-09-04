"""Regression coverage for still-used generation contracts, traces and guards."""

from __future__ import annotations

from django.test import SimpleTestCase

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationTraceRecorder
from .generation_contracts import ScenarioPlan
from .generation_workspace import variable_definitions_for_scenario_plan
from .mcp_page_explorer import ReadOnlyMCPBrowserToolGuard


def plan_payload(**overrides):
    value = {
        'schema_version': 4,
        'title': '完整连续流程',
        'objective': '在一个连续浏览器会话中完成目标并验证。',
        'instructions': ['观察入口', '执行目标范围内操作', '验证页面状态'],
        'success_criteria': ['目标区域可见'],
        'assertion_requirements': [{
            'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
            'kind': 'visible', 'input_ref': '', 'literal': '',
        }],
        'input_refs': [], 'preconditions': [], 'forbidden_actions': [],
        'credentials_required': False, 'allow_test_data_writes': False,
        'cleanup_expected': False, 'discovery_notes': [], 'risk_level': 'low',
    }
    value.update(overrides)
    return value


def event(recorder, run_id: str, tool_name: str, inputs: dict, output):
    recorder.on_tool_start({'name': tool_name}, '', run_id=run_id, inputs=inputs)
    recorder.on_tool_end(output, run_id=run_id)


class ScenarioContractRegressionTests(SimpleTestCase):
    def test_rejects_reserved_inputs_conflicting_credentials_and_absolute_urls(self):
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(input_refs=[{'name': 'PATH', 'source': 'runtime'}]))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                credentials_required=True,
                input_refs=[{
                    'name': 'UI_TEST_USERNAME', 'source': 'credential',
                    'credential_slot': 'username',
                }],
            ))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(objective='访问 https://example.test 后验证'))

    def test_assertion_ref_must_be_declared_and_literal_shape_is_strict(self):
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'contains_ref', 'input_ref': 'UNKNOWN', 'literal': '',
            }]))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': 'invented',
            }]))

    def test_symbolic_credential_refs_are_not_treated_as_literal_secrets(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            objective='使用 UI_TEST_USERNAME 和 UI_TEST_PASSWORD 完成登录后验证。',
            credentials_required=True,
            input_refs=[
                {
                    'name': 'UI_TEST_USERNAME', 'source': 'credential',
                    'credential_slot': 'username',
                },
                {
                    'name': 'UI_TEST_PASSWORD', 'source': 'credential',
                    'credential_slot': 'password',
                },
            ],
        ))
        self.assertTrue(plan.credentials_required)

    def test_plaintext_credentials_are_allowed_in_scenario_plan(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            objective='使用用户名 test-user、密码 test-password 和 token-for-test 完成登录验证。',
            instructions=['输入用户名 test-user 和密码 test-password。'],
        ))
        self.assertIn('test-password', plan.objective)

    def test_workspace_variables_follow_explicit_sources_without_values(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            credentials_required=True,
            input_refs=[
                {'name': 'ITEM_NAME', 'source': 'generated'},
                {'name': 'FILTER_VALUE', 'source': 'runtime'},
                {
                    'name': 'UI_TEST_USERNAME', 'source': 'credential',
                    'credential_slot': 'username',
                },
                {
                    'name': 'UI_TEST_PASSWORD', 'source': 'credential',
                    'credential_slot': 'password',
                },
            ],
        ))
        definitions = {item['name']: item for item in variable_definitions_for_scenario_plan(plan)}
        self.assertFalse(definitions['ITEM_NAME']['required'])
        self.assertTrue(definitions['FILTER_VALUE']['required'])
        self.assertTrue(definitions['UI_TEST_PASSWORD']['is_secret'])
        self.assertTrue(all(item['value'] == '' for item in definitions.values()))


class TraceSecurityRegressionTests(SimpleTestCase):
    def test_runtime_values_and_credentials_are_retained_in_test_environment_trace(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            credentials_required=True,
            input_refs=[
                {
                    'name': 'UI_TEST_USERNAME', 'source': 'credential',
                    'credential_slot': 'username',
                },
                {
                    'name': 'UI_TEST_PASSWORD', 'source': 'credential',
                    'credential_slot': 'password',
                },
            ],
        ))
        recorder = ExplorationTraceRecorder('/login')
        recorder.configure_plan(plan)
        recorder.configure_runtime(
            {'UI_TEST_USERNAME': 'private-user', 'UI_TEST_PASSWORD': 'private-pass'},
            plan.input_sources(),
        )
        event(
            recorder, 'fill-password', 'playwright_fill',
            {'selector': '[name=password]', 'value': 'private-pass'}, 'filled private-pass',
        )
        event(
            recorder, 'screenshot', 'playwright_screenshot', {},
            'saved screenshots/private-pass.png',
        )
        serialized = recorder.build(tool_stats={}).model_dump_json()
        self.assertIn('private-pass', serialized)
        self.assertIn('[name=password]', serialized)

    def test_locator_boolean_exact_is_preserved_and_long_selector_is_rejected_whole(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        event(
            recorder, 'observe', 'playwright_get_visible_html',
            {'text': 'Target', 'exact': False}, '<div>Target</div>',
        )
        evidence = recorder.build(tool_stats={}).locator_evidence[0]
        self.assertIs(evidence.kwargs['exact'], False)

        long_recorder = ExplorationTraceRecorder('/')
        long_recorder.configure_plan(plan)
        event(
            long_recorder, 'long', 'playwright_click',
            {'selector': '#' + ('x' * 301)}, 'clicked',
        )
        self.assertFalse(long_recorder.build(tool_stats={}).locator_evidence)


class ScenarioPolicyRegressionTests(SimpleTestCase):
    def test_policy_uses_plan_scope_and_explicit_read_only_override(self):
        writable = ScenarioPlan.model_validate(plan_payload(allow_test_data_writes=True))
        policy = ExplorationPolicy.for_plan(writable, generation_id='run-1', user_constraints='')
        self.assertTrue(policy.may_write())
        self.assertTrue(policy.namespace.startswith('aits-explore-run-1-'))

        read_only = ExplorationPolicy.for_plan(
            writable, generation_id='run-1', user_constraints='仅查看',
        )
        self.assertFalse(read_only.may_write())
        self.assertTrue(read_only.explicit_read_only)

    def test_guard_budget_disabled_tool_and_unknown_write_result_are_enforced(self):
        plan = ScenarioPlan.model_validate(plan_payload(allow_test_data_writes=True))
        recorder = ExplorationTraceRecorder()
        recorder.configure_plan(plan)
        guard = ReadOnlyMCPBrowserToolGuard(
            max_tool_calls=1,
            policy=ExplorationPolicy.for_plan(plan, generation_id='run-2', user_constraints=''),
            trace_recorder=recorder,
        )
        guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='read', inputs={})
        guard.on_tool_end('visible', run_id='read')
        with self.assertRaises(Exception) as budget:
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='over', inputs={})
        self.assertEqual(budget.exception.error_kind, 'tool_budget')

        blocked = ReadOnlyMCPBrowserToolGuard(
            policy=ExplorationPolicy.read_only(), trace_recorder=ExplorationTraceRecorder(),
        )
        with self.assertRaises(Exception) as disabled:
            blocked.on_tool_start(
                {'name': 'playwright_evaluate'}, '', run_id='eval', inputs={'code': '1'},
            )
        self.assertEqual(disabled.exception.error_kind, 'read_only_violation')

        unknown = ReadOnlyMCPBrowserToolGuard(
            policy=ExplorationPolicy.for_plan(plan, generation_id='run-3', user_constraints=''),
            trace_recorder=ExplorationTraceRecorder(),
        )
        unknown.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='write', inputs={'selector': '#target'},
        )
        with self.assertRaises(Exception) as result_unknown:
            unknown.on_tool_end('Error: connection closed after dispatch', run_id='write')
        self.assertEqual(result_unknown.exception.error_kind, 'write_result_unknown')
