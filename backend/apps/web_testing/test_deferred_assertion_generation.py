"""Offline regression coverage for deferred assertion generation only."""

from __future__ import annotations

import json

from django.test import SimpleTestCase

from .exploration_trace import (
    ExplorationTraceRecorder,
    FinalizedAction,
    FinalizedAssertion,
    FinalizedPendingAssertion,
    required_replay_evidence_gaps,
)
from .generation_contracts import GenerationContractError, ScenarioPlan, parse_scenario_plan_json


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
    def test_missing_assertion_requirements_are_completed_as_deferred(self):
        plan = parse_scenario_plan_json(json.dumps(_plan_payload(
            success_criteria=['第一目标', '第二目标'], assertion_requirements=[],
        ), ensure_ascii=False))
        self.assertEqual(
            [(item.assertion_id, item.criterion_index, item.kind) for item in plan.assertion_requirements],
            [('A1', 0, 'deferred'), ('A2', 1, 'deferred')],
        )

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
        self.assertEqual(len(trace.finalization.pending_assertions), 2)

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
