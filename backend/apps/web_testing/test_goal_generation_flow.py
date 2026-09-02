"""Migrated v4 contract, policy, trace and replay flow regressions.

The historical filename is retained so generic coverage is not silently lost;
there are no Goal execution boundaries in these tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationTraceRecorder, FinalizedAction, FinalizedAssertion
from .generation_contracts import (
    GenerationContractError,
    ScenarioInputInsufficientError,
    ScenarioPlan,
    parse_scenario_plan_json,
)
from .generation_workspace import variable_definitions_for_scenario_plan
from .mcp_page_explorer import ReadOnlyMCPBrowserToolGuard
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .requirement_normalizer import RequirementNormalizer
from .script_quality import evaluate_script


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
    recorder.on_tool_start(
        {'name': tool_name}, '', run_id=run_id, inputs=inputs,
    )
    recorder.on_tool_end(output, run_id=run_id)


class ScenarioContractRegressionTests(SimpleTestCase):
    def test_rejects_reserved_inputs_conflicting_credentials_and_absolute_urls(self):
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                input_refs=[{'name': 'PATH', 'source': 'runtime'}],
            ))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                credentials_required=True,
                input_refs=[{
                    'name': 'UI_TEST_USERNAME', 'source': 'credential',
                    'credential_slot': 'username',
                }],
            ))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                objective='访问 https://example.test 后验证',
            ))

    def test_assertion_ref_must_be_declared_and_literal_shape_is_strict(self):
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                assertion_requirements=[{
                    'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                    'kind': 'contains_ref', 'input_ref': 'UNKNOWN', 'literal': '',
                }],
            ))
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(plan_payload(
                assertion_requirements=[{
                    'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                    'kind': 'visible', 'input_ref': '', 'literal': 'invented',
                }],
            ))

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
        definitions = {
            item['name']: item for item in variable_definitions_for_scenario_plan(plan)
        }
        self.assertFalse(definitions['ITEM_NAME']['required'])
        self.assertTrue(definitions['FILTER_VALUE']['required'])
        self.assertTrue(definitions['UI_TEST_PASSWORD']['is_secret'])
        self.assertTrue(all(item['value'] == '' for item in definitions.values()))


class RequirementNormalizerRegressionTests(SimpleTestCase):
    def _normalize(self, description: str, payload: dict):
        manager = Mock()
        manager.current_llm = None
        manager.invoke.return_value = json.dumps(payload, ensure_ascii=False)
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            return RequirementNormalizer(8).normalize(description)

    def test_model_complete_semantics_owns_write_permission(self):
        description = '把一条测试数据改成新的状态，并验证页面结果。'
        writable = self._normalize(
            description, plan_payload(allow_test_data_writes=True),
        )
        read_plan = self._normalize(
            description, plan_payload(allow_test_data_writes=False),
        )
        self.assertTrue(writable.allow_test_data_writes)
        self.assertFalse(read_plan.allow_test_data_writes)
        source = Path(__file__).with_name('requirement_normalizer.py').read_text(encoding='utf-8')
        self.assertNotIn('_MUTATING_INTENT_PATTERN', source)

    def test_explicit_read_only_constraint_overrides_model_write_scope(self):
        payload = plan_payload(
            allow_test_data_writes=True,
            cleanup_expected=True,
            success_criteria=['主结果可见', '清理后目标值不存在'],
            assertion_requirements=[
                {
                    'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                    'kind': 'visible', 'input_ref': '', 'literal': '',
                },
                {
                    'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup',
                    'kind': 'not_contains_literal', 'input_ref': '', 'literal': '目标值',
                },
            ],
        )
        plan = self._normalize(
            '只读检查当前页面的目标值，不允许任何写入。', payload,
        )
        self.assertFalse(plan.allow_test_data_writes)
        self.assertFalse(plan.cleanup_expected)
        self.assertEqual(plan.success_criteria, ['主结果可见'])
        self.assertEqual([item.phase for item in plan.assertion_requirements], ['main'])

    def test_plain_target_is_not_rejected_but_target_free_prompt_is(self):
        plan = self._normalize('检查当前页面中的目标状态。', plan_payload())
        self.assertEqual(plan.schema_version, 4)
        manager = Mock()
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            with self.assertRaises(ScenarioInputInsufficientError):
                RequirementNormalizer(8).normalize('请帮我生成测试')
        manager.invoke.assert_not_called()

    def test_literal_assertion_must_come_from_user_input(self):
        literal_payload = plan_payload(
            success_criteria=['页面包含 READY'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'contains_literal', 'input_ref': '', 'literal': 'READY',
            }],
        )
        plan = self._normalize('确认页面显示 READY。', literal_payload)
        self.assertEqual(plan.assertion_requirements[0].literal, 'READY')
        with self.assertRaises(Exception):
            self._normalize('确认页面显示完成状态。', literal_payload)

    def test_native_structured_output_uses_low_randomness_without_json_fallback(self):
        structured_model = Mock()
        structured_model.invoke.return_value = plan_payload()
        llm = Mock()
        llm.with_structured_output.return_value = structured_model
        manager = Mock()
        manager.current_llm = llm
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            plan = RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        self.assertEqual(plan.schema_version, 4)
        llm.with_structured_output.assert_called_once_with(ScenarioPlan)
        self.assertEqual(structured_model.invoke.call_args.kwargs['temperature'], 0)
        manager.invoke.assert_not_called()

    def test_explicit_structured_output_capability_gap_falls_back_once_at_low_randomness(self):
        llm = Mock()
        llm.with_structured_output.side_effect = NotImplementedError('response_format unsupported')
        manager = Mock()
        manager.current_llm = llm
        manager.invoke.return_value = json.dumps(plan_payload(), ensure_ascii=False)
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            plan = RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        self.assertEqual(plan.schema_version, 4)
        manager.invoke.assert_called_once()
        self.assertEqual(manager.invoke.call_args.kwargs['temperature'], 0)

    def test_transport_failures_do_not_fall_back_or_repeat_request(self):
        for message in ('HTTP 429 rate limit', 'HTTP 503 upstream unavailable', 'request timeout'):
            with self.subTest(message=message):
                structured_model = Mock()
                structured_model.invoke.side_effect = RuntimeError(message)
                llm = Mock()
                llm.with_structured_output.return_value = structured_model
                manager = Mock()
                manager.current_llm = llm
                with patch(
                    'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
                structured_model.invoke.assert_called_once()
                manager.invoke.assert_not_called()

    def test_transport_failure_with_capability_text_does_not_fall_back(self):
        structured_model = Mock()
        structured_model.invoke.side_effect = RuntimeError('HTTP 503 response_format unsupported')
        llm = Mock()
        llm.with_structured_output.return_value = structured_model
        manager = Mock()
        manager.current_llm = llm
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
                RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        structured_model.invoke.assert_called_once()
        manager.invoke.assert_not_called()

    def test_plain_attribute_error_does_not_fall_back(self):
        structured_model = Mock()
        structured_model.invoke.side_effect = AttributeError('internal parser attribute missing')
        llm = Mock()
        llm.with_structured_output.return_value = structured_model
        manager = Mock()
        manager.current_llm = llm
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            with self.assertRaisesRegex(AttributeError, 'internal parser'):
                RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        structured_model.invoke.assert_called_once()
        manager.invoke.assert_not_called()

    def test_deterministic_json_extraction_accepts_one_fenced_or_wrapped_object(self):
        payload = json.dumps(plan_payload(), ensure_ascii=False)
        for raw_output in (f'```json\n{payload}\n```', f'模型结果如下：\n{payload}\n请继续'):
            with self.subTest(raw_output=raw_output[:12]):
                self.assertEqual(parse_scenario_plan_json(raw_output).schema_version, 4)

    def test_semantic_repair_receives_field_diagnostics_and_runs_once(self):
        invalid = plan_payload(schema_version=3, title='private title')
        manager = Mock()
        manager.current_llm = None
        manager.invoke.side_effect = [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(plan_payload(), ensure_ascii=False),
        ]
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            plan = RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        self.assertEqual(plan.schema_version, 4)
        self.assertEqual(manager.invoke.call_count, 2)
        repair_request = json.loads(manager.invoke.call_args_list[1].args[0][1].content)
        self.assertEqual(repair_request['validation_diagnostics'][0]['path'], 'schema_version')
        self.assertNotIn('validation_error', repair_request)
        self.assertEqual(manager.invoke.call_args_list[1].kwargs['temperature'], 0)

    def test_structured_parser_failure_with_raw_output_uses_one_targeted_repair(self):
        class ParserFailure(RuntimeError):
            def __init__(self):
                super().__init__('structured parser rejected response')
                self.llm_output = json.dumps(plan_payload(schema_version=3), ensure_ascii=False)

        structured_model = Mock()
        structured_model.invoke.side_effect = ParserFailure()
        llm = Mock()
        llm.with_structured_output.return_value = structured_model
        manager = Mock()
        manager.current_llm = llm
        manager.invoke.return_value = json.dumps(plan_payload(), ensure_ascii=False)
        with patch(
            'web_testing.requirement_normalizer.get_llm_manager', return_value=manager,
        ):
            plan = RequirementNormalizer(8).normalize('检查当前页面的目标状态。')
        self.assertEqual(plan.schema_version, 4)
        structured_model.invoke.assert_called_once()
        manager.invoke.assert_called_once()
        repair_request = json.loads(manager.invoke.call_args.args[0][1].content)
        self.assertEqual(repair_request['validation_diagnostics'][0]['path'], 'schema_version')

    def test_contract_diagnostics_exclude_input_values(self):
        with self.assertRaises(GenerationContractError) as captured:
            parse_scenario_plan_json(json.dumps(plan_payload(
                schema_version=3, title='private title',
            ), ensure_ascii=False))
        diagnostics = captured.exception.diagnostics
        self.assertEqual(diagnostics[0]['path'], 'schema_version')
        self.assertEqual(diagnostics[0]['stage'], 'contract_validation')
        self.assertNotIn('private title', json.dumps(diagnostics, ensure_ascii=False))

        with self.assertRaises(GenerationContractError) as extra_field:
            parse_scenario_plan_json(json.dumps({
                **plan_payload(), 'password': 'private-value',
            }, ensure_ascii=False))
        serialized = json.dumps(extra_field.exception.diagnostics, ensure_ascii=False)
        self.assertIn('<field>', serialized)
        self.assertNotIn('password', serialized)
        self.assertNotIn('private-value', serialized)


class TraceSecurityAndReplayRegressionTests(SimpleTestCase):
    def test_runtime_values_template_and_credentials_never_persist(self):
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
            {
                'UI_TEST_USERNAME': 'private-user',
                'UI_TEST_PASSWORD': 'private-pass',
            },
            plan.input_sources(),
        )
        event(
            recorder, 'fill-password', 'playwright_fill',
            {'selector': '[name=password]', 'value': 'private-pass'},
            'filled private-pass',
        )
        event(
            recorder, 'screenshot', 'playwright_screenshot', {},
            'saved screenshots/private-pass.png',
        )
        serialized = recorder.build(tool_stats={}).model_dump_json()
        self.assertNotIn('private-user', serialized)
        self.assertNotIn('private-pass', serialized)
        self.assertIn('[name=password]', serialized)

    def test_locator_boolean_exact_is_preserved_and_long_selector_is_rejected_whole(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        event(
            recorder, 'observe', 'playwright_get_visible_html',
            {'text': 'Target', 'exact': False}, '<div>Target</div>',
        )
        trace = recorder.build(tool_stats={})
        evidence = trace.locator_evidence[0]
        self.assertIs(evidence.kwargs['exact'], False)

        long_recorder = ExplorationTraceRecorder('/')
        long_recorder.configure_plan(plan)
        event(
            long_recorder, 'long', 'playwright_click',
            {'selector': '#' + ('x' * 301)}, 'clicked',
        )
        self.assertFalse(long_recorder.build(tool_stats={}).locator_evidence)

    def test_press_arguments_and_callback_selector_are_compiled_unchanged(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        event(
            recorder, 'navigate', 'playwright_navigate',
            {'url': 'https://example.test/'}, 'ok',
        )
        event(
            recorder, 'press', 'playwright_press_key',
            {'selector': '[aria-label="query"]', 'key': 'Enter'}, 'pressed',
        )
        event(
            recorder, 'assert', 'playwright_get_visible_html',
            {'selector': '#result'}, '<main>visible</main>',
        )
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='提交查询')],
            assertions=[FinalizedAssertion(assertion_id='A1', event_id='E000003')],
            cleanup_actions=[],
        )
        trace = recorder.build(tool_stats={})
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        self.assertIn('[aria-label="query"]', source)
        self.assertIn(".press('Enter')", source)
        self.assertFalse(evaluate_script(
            source, plan=plan, trace=trace, replay_plan=replay,
        )['blockers'])


class ScenarioPolicyRegressionTests(SimpleTestCase):
    def test_policy_uses_plan_scope_and_explicit_read_only_override(self):
        writable = ScenarioPlan.model_validate(plan_payload(allow_test_data_writes=True))
        policy = ExplorationPolicy.for_plan(
            writable, generation_id='run-1', user_constraints='',
        )
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
            policy=ExplorationPolicy.for_plan(
                plan, generation_id='run-2', user_constraints='',
            ),
            trace_recorder=recorder,
        )
        guard.on_tool_start(
            {'name': 'playwright_get_visible_text'}, '', run_id='read', inputs={},
        )
        guard.on_tool_end('visible', run_id='read')
        with self.assertRaises(Exception) as budget:
            guard.on_tool_start(
                {'name': 'playwright_get_visible_text'}, '', run_id='over', inputs={},
            )
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
            policy=ExplorationPolicy.for_plan(
                plan, generation_id='run-3', user_constraints='',
            ),
            trace_recorder=ExplorationTraceRecorder(),
        )
        unknown.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='write',
            inputs={'selector': '#target'},
        )
        with self.assertRaises(Exception) as result_unknown:
            unknown.on_tool_end('Error: connection closed after dispatch', run_id='write')
        self.assertEqual(result_unknown.exception.error_kind, 'write_result_unknown')
