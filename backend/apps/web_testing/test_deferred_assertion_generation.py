"""Offline regression coverage for deferred assertion generation only."""

from __future__ import annotations

import json
import ast
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .exploration_trace import (
    ExplorationTraceRecorder,
    FinalizedAction,
    FinalizedAssertion,
    FinalizedPendingAssertion,
    required_replay_evidence_gaps,
)
from .generation_contracts import GenerationContractError, ScenarioPlan, parse_scenario_plan_json
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .requirement_normalizer import RequirementNormalizer
from .script_quality import _has_unresolved_placeholder, evaluate_script


def _plan_payload(**overrides):
    value = {
        'schema_version': 4,
        'title': '探索后验证',
        'objective': '完成目标并验证结果。',
        'instructions': ['进入页面', '执行目标操作', '验证结果'],
        'success_criteria': ['目标结果符合预期'],
        'assertion_requirements': [{
            'assertion_id': 'A1', 'criterion_index': 0,
            'phase': 'main', 'kind': 'deferred',
        }],
    }
    value.update(overrides)
    return value


def _event(recorder, run_id, tool_name, inputs, output):
    recorder.on_tool_start({'name': tool_name}, '', run_id=run_id, inputs=inputs)
    recorder.on_tool_end(output, run_id=run_id)


class DeferredAssertionGenerationTests(SimpleTestCase):
    def assert_retired_relative_replay_rejected(self, report):
        # v4 compilation is no longer an executable generation path: it emits
        # relative goto URLs. Keep testing its evidence/marker semantics, but
        # never restore an environment fallback to make its output executable.
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(
            [item['code'] for item in report['blockers']],
            ['SCRIPT_CONTRACT_INVALID'],
        )

    def test_missing_assertion_requirements_are_completed_as_deferred(self):
        plan = parse_scenario_plan_json(json.dumps(_plan_payload(
            success_criteria=['第一目标', '第二目标'], assertion_requirements=[],
        ), ensure_ascii=False))
        self.assertEqual(
            [(item.assertion_id, item.criterion_index, item.kind) for item in plan.assertion_requirements],
            [('A1', 0, 'deferred'), ('A2', 1, 'deferred')],
        )

    def test_model_invented_literal_becomes_deferred_and_preserves_raw_user_target(self):
        payload = _plan_payload(
            success_criteria=['页面显示 READY'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'contains_literal', 'literal': 'READY',
            }],
        )
        manager = SimpleNamespace(current_llm=object(), invoke=Mock(
            return_value=json.dumps(payload, ensure_ascii=False),
        ))
        with patch('web_testing.requirement_normalizer.get_llm_manager', return_value=manager):
            plan = RequirementNormalizer(1).normalize('确认操作完成后的状态。')
        self.assertEqual(plan.original_user_target, '确认操作完成后的状态。')
        self.assertEqual(plan.assertion_requirements[0].kind, 'deferred')
        self.assertEqual(plan.success_criteria, ['页面显示 READY'])

    def test_original_user_target_accepts_description_longer_than_one_thousand_characters(self):
        description = '确认页面业务目标。' + ('长文本' * 800)
        manager = SimpleNamespace(current_llm=object(), invoke=Mock(
            return_value=json.dumps(_plan_payload(), ensure_ascii=False),
        ))
        with patch('web_testing.requirement_normalizer.get_llm_manager', return_value=manager):
            plan = RequirementNormalizer(1).normalize(description)
        self.assertEqual(plan.original_user_target, description)

    def test_deferred_assertion_can_compile_real_ref_evidence(self):
        plan = ScenarioPlan.model_validate(_plan_payload(input_refs=[{
            'name': 'ITEM_NAME', 'source': 'generated', 'value_kind': 'text',
        }]))
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({'ITEM_NAME': 'test-item'}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'fill', 'playwright_fill', {'selector': '#name', 'value': 'test-item'}, 'filled')
        _event(recorder, 'submit', 'playwright_click', {'selector': '#submit'}, 'clicked')
        _event(recorder, 'observe', 'playwright_get_visible_html', {'selector': '#result'}, '<main>test-item</main>')
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[
                FinalizedAction(event_id='E000002', step_name='填写名称'),
                FinalizedAction(event_id='E000003', step_name='提交'),
            ],
            assertions=[FinalizedAssertion(
                assertion_id='A1', event_id='E000004', kind='contains_ref', input_ref='ITEM_NAME',
            )],
            cleanup_actions=[], pending_assertions=[],
        )
        trace = recorder.build(tool_stats={})
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertIn('to_contain_text', source)
        self.assertEqual(report['assertion_state']['status'], 'complete')
        self.assert_retired_relative_replay_rejected(report)

    def test_pending_assertion_compiles_as_comment_and_quality_warning(self):
        plan = ScenarioPlan.model_validate(_plan_payload())
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'open', 'playwright_click', {'selector': '#details'}, 'clicked')
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='打开详情')],
            assertions=[], cleanup_actions=[],
            pending_assertions=[FinalizedPendingAssertion(
                assertion_id='A1', after_event_id='E000002', reason='未能确认页面业务结果。',
            )],
        )
        trace = recorder.build(tool_stats={})
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertIn(
            '# AITS_PENDING_ASSERTION: {"assertion_id":"A1","criterion":"目标结果符合预期","reason":"未能确认页面业务结果。"}',
            source,
        )
        self.assertEqual(report['assertion_state']['pending_count'], 1)
        self.assert_retired_relative_replay_rejected(report)

    def test_pure_navigation_with_only_pending_assertions_is_rejected(self):
        plan = ScenarioPlan.model_validate(_plan_payload())
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        recorder.candidate_summary()
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_MAIN_ACTION_MISSING'):
            recorder.finalize_path(
                main_actions=[], assertions=[], cleanup_actions=[],
                pending_assertions=[FinalizedPendingAssertion(
                    assertion_id='A1', after_event_id='E000001', reason='没有业务动作。',
                )],
            )

    def test_read_only_navigation_with_real_observation_is_allowed(self):
        plan = ScenarioPlan.model_validate(_plan_payload(assertion_requirements=[{
            'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main', 'kind': 'visible',
        }]))
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'observe', 'playwright_get_visible_html', {'selector': 'h1'}, '<h1>首页</h1>')
        recorder.candidate_summary()
        result = recorder.finalize_path(
            main_actions=[],
            assertions=[FinalizedAssertion(assertion_id='A1', event_id='E000002')],
            cleanup_actions=[], pending_assertions=[],
        )
        self.assertEqual(result['status'], 'accepted')

    def test_cleanup_pending_assertion_is_mapped_from_plan_and_remains_a_warning(self):
        plan = ScenarioPlan.model_validate(_plan_payload(
            allow_test_data_writes=True, cleanup_expected=True,
            success_criteria=['主体操作完成', '测试数据已清理'],
            assertion_requirements=[
                {'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main', 'kind': 'deferred'},
                {'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup', 'kind': 'deferred'},
            ],
        ))
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'create', 'playwright_click', {'selector': '#create'}, 'clicked')
        _event(recorder, 'cleanup', 'playwright_click', {'selector': '#cleanup'}, 'clicked')
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='创建测试数据')],
            assertions=[], cleanup_actions=[FinalizedAction(event_id='E000003', step_name='清理测试数据')],
            pending_assertions=[
                FinalizedPendingAssertion(assertion_id='A1', after_event_id='E000002', reason='主体结果待补充。'),
                FinalizedPendingAssertion(assertion_id='A2', after_event_id='E000003', reason='清理结果待补充。'),
            ],
        )
        trace = recorder.build(tool_stats={})
        self.assertEqual(required_replay_evidence_gaps(trace, plan), [])
        replay = ReplayPlanner.build(plan, trace)
        report = evaluate_script(
            PythonReplayCompiler.compile(plan, trace, replay),
            plan=plan, trace=trace, replay_plan=replay,
        )
        self.assert_retired_relative_replay_rejected(report)
        self.assertEqual(report['assertion_state']['pending_count'], 2)

    def test_deferred_negative_literal_needs_user_or_earlier_observation_evidence(self):
        plan = ScenarioPlan.model_validate(_plan_payload(
            allow_test_data_writes=True, cleanup_expected=True,
            success_criteria=['主体操作完成', '旧文本已不存在'],
            assertion_requirements=[
                {'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main', 'kind': 'deferred'},
                {'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup', 'kind': 'deferred'},
            ],
        ))
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'create', 'playwright_click', {'selector': '#create'}, 'clicked')
        _event(recorder, 'before', 'playwright_get_visible_html', {'selector': '#result'}, '<main>obsolete</main>')
        _event(recorder, 'cleanup', 'playwright_click', {'selector': '#cleanup'}, 'clicked')
        _event(recorder, 'after', 'playwright_get_visible_html', {'selector': '#result'}, '<main>removed</main>')
        recorder.candidate_summary()
        common = {
            'main_actions': [FinalizedAction(event_id='E000002', step_name='创建测试数据')],
            'cleanup_actions': [FinalizedAction(event_id='E000004', step_name='清理测试数据')],
            'pending_assertions': [FinalizedPendingAssertion(
                assertion_id='A1', after_event_id='E000002', reason='主体结果待补充。',
            )],
        }
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_DEFERRED_ASSERTION_LITERAL_UNPROVEN'):
            recorder.finalize_path(
                **common,
                assertions=[FinalizedAssertion(
                    assertion_id='A2', event_id='E000005', kind='not_contains_literal', literal='invented',
                )],
            )
        self.assertEqual(recorder.finalize_path(
            **common,
            assertions=[FinalizedAssertion(
                assertion_id='A2', event_id='E000005', kind='not_contains_literal', literal='obsolete',
            )],
        )['status'], 'accepted')

    def test_business_literals_and_step_titles_are_not_treated_as_placeholders(self):
        source = '''async def run(page, variables):
    value = "待补充"
    # 步骤 1：确认待确认状态
    await page.get_by_text(value).click()
'''
        self.assertFalse(_has_unresolved_placeholder(ast.parse(source), source.splitlines()))
        placeholder = '''async def run(page, variables):
    # TODO: replace with a real assertion
    value = 1
'''
        self.assertTrue(_has_unresolved_placeholder(ast.parse(placeholder), placeholder.splitlines()))

    def test_partial_real_assertion_and_pending_marker_is_warning_and_marker_is_required(self):
        plan = ScenarioPlan.model_validate(_plan_payload(
            success_criteria=['结果区域存在', '详情语义正确'],
            assertion_requirements=[
                {'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main', 'kind': 'visible'},
                {'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'main', 'kind': 'deferred'},
            ],
        ))
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        _event(recorder, 'nav', 'playwright_navigate', {'url': 'https://offline.test/'}, 'ok')
        _event(recorder, 'open', 'playwright_click', {'selector': '#details'}, 'clicked')
        _event(recorder, 'observe', 'playwright_get_visible_html', {'selector': '#result'}, '<main>详情</main>')
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='打开详情')],
            assertions=[FinalizedAssertion(assertion_id='A1', event_id='E000003')], cleanup_actions=[],
            pending_assertions=[FinalizedPendingAssertion(
                assertion_id='A2', after_event_id='E000002', reason='详情业务语义待人工补充。',
            )],
        )
        trace = recorder.build(tool_stats={})
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assert_retired_relative_replay_rejected(report)
        self.assertTrue(any(item['code'] == 'PENDING_ASSERTIONS' for item in report['warnings']))
        source_without_marker = '\n'.join(
            line for line in source.splitlines()
            if not line.lstrip().startswith('# AITS_PENDING_ASSERTION:')
        )
        missing = evaluate_script(
            source_without_marker, plan=plan, trace=trace, replay_plan=replay,
        )
        self.assertTrue(any(
            item['code'] == 'PENDING_ASSERTION_MARKER_MISMATCH'
            for item in missing['blockers']
        ))
