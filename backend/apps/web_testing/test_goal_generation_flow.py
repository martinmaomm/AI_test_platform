"""Offline v3 regressions for goal-scoped exploration and replay."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationEvent, ExplorationTraceRecorder, evaluate_goal_events, required_goal_evidence_gaps
from .generation_contracts import GenerationContractError, GoalPlan, ScenarioInputInsufficientError
from .mcp_page_explorer import MCPPageExplorer
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .requirement_normalizer import RequirementNormalizer
from .script_quality import evaluate_script
from .generation_workspace import variable_definitions_for_goal_plan


def plan_payload(**overrides):
    payload = {
        'schema_version': 3,
        'title': '用户数据生命周期',
        'objective': '验证本轮测试数据可安全操作、确认结果并清理。',
        'goals': [
            {'id': 'G1', 'kind': 'setup', 'objective': '进入用户页面', 'completion_criteria': '用户页面已观察', 'side_effect': 'none'},
            {
                'id': 'G2', 'kind': 'exercise', 'objective': '录入本轮测试数据', 'completion_criteria': '操作后页面已观察',
                'input_refs': [{'name': 'user_name', 'source': 'generated'}, {'name': 'display_name', 'source': 'runtime'}], 'side_effect': 'test_data',
            },
            {
                'id': 'G3', 'kind': 'verify', 'objective': '确认本轮数据可见', 'completion_criteria': '结果区域包含测试数据',
                'input_refs': [{'name': 'USER_NAME', 'source': 'generated'}],
                'verification': {'mode': 'contains_ref', 'input_ref': 'USER_NAME'}, 'side_effect': 'none',
            },
            {
                'id': 'G4', 'kind': 'cleanup', 'objective': '清理本轮测试数据', 'completion_criteria': '结果区域不再包含测试数据',
                'input_refs': [{'name': 'USER_NAME', 'source': 'generated'}],
                'verification': {'mode': 'not_contains_ref', 'input_ref': 'USER_NAME'}, 'side_effect': 'none', 'cleanup_for_goal_ids': ['G2'],
            },
        ],
        'forbidden_actions': [], 'credentials_required': False, 'discovery_notes': [], 'ambiguities': [], 'risk_level': 'medium',
    }
    payload.update(overrides)
    return payload


class GoalPlanTests(SimpleTestCase):
    def test_v3_rejects_old_step_contract_requires_cleanup_assertion_and_normalizes_refs(self):
        with self.assertRaises(Exception):
            GoalPlan.model_validate({'title': '旧场景', 'objective': '旧步骤', 'steps': []})
        invalid = plan_payload(goals=plan_payload()['goals'][:2])
        with self.assertRaises(Exception):
            GoalPlan.model_validate(invalid)
        invalid_cleanup = plan_payload()
        invalid_cleanup['goals'][3].pop('verification')
        with self.assertRaises(Exception):
            GoalPlan.model_validate(invalid_cleanup)
        plan = GoalPlan.model_validate(plan_payload())
        self.assertEqual(plan.goals[1].input_refs[0].name, 'USER_NAME')
        self.assertEqual(plan.input_sources()['USER_NAME'], 'generated')

    def test_v3_rejects_reserved_inputs_conflicting_credentials_urls_and_missing_verification(self):
        reserved = plan_payload()
        reserved['goals'][1]['input_refs'][0]['name'] = 'PATH'
        with self.assertRaises(Exception):
            GoalPlan.model_validate(reserved)

        conflicting_credentials = plan_payload(credentials_required=True, goals=[
            {
                'id': 'G1', 'kind': 'setup', 'objective': '进入登录页',
                'completion_criteria': '登录页已观察', 'side_effect': 'none',
                'input_refs': [
                    {'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'},
                    {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'},
                ],
            },
            {
                'id': 'G2', 'kind': 'verify', 'objective': '确认登录状态',
                'completion_criteria': '状态可见', 'side_effect': 'none',
                'input_refs': [
                    {'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'password'},
                ],
                'verification': {'mode': 'visible'},
            },
        ])
        with self.assertRaises(Exception):
            GoalPlan.model_validate(conflicting_credentials)

        absolute_url = plan_payload(objective='访问 https://example.test/users 并验证。')
        with self.assertRaises(Exception):
            GoalPlan.model_validate(absolute_url)

        with self.assertRaises(Exception):
            GoalPlan.model_validate(plan_payload(goals=[
                {
                    'id': 'G1', 'kind': 'setup', 'objective': '观察目标页面',
                    'completion_criteria': '页面已观察', 'side_effect': 'none',
                },
            ]))

    def test_normalizer_does_not_gate_plain_language_without_crud_words(self):
        manager = Mock()
        manager.invoke.return_value = json.dumps(plan_payload(), ensure_ascii=False)
        with patch('web_testing.requirement_normalizer.get_llm_manager', return_value=manager):
            result = RequirementNormalizer(8).normalize('请检查会员页的筛选体验是否符合预期。')
        self.assertEqual(result.schema_version, 3)
        manager.invoke.assert_called_once()
        with patch('web_testing.requirement_normalizer.get_llm_manager', return_value=manager):
            with self.assertRaises(ScenarioInputInsufficientError):
                RequirementNormalizer(8).normalize('请帮我生成测试')

    def test_workspace_variables_follow_explicit_input_sources(self):
        definitions = {
            item['name']: item
            for item in variable_definitions_for_goal_plan(
                GoalPlan.model_validate(plan_payload())
            )
        }
        self.assertFalse(definitions['USER_NAME']['required'])
        self.assertTrue(definitions['DISPLAY_NAME']['required'])
        self.assertFalse(definitions['DISPLAY_NAME']['is_secret'])

        credential_plan = GoalPlan.model_validate(plan_payload(credentials_required=True, goals=[
            {
                'id': 'G1', 'kind': 'setup', 'objective': '登录目标页面',
                'completion_criteria': '登录后页面已观察', 'side_effect': 'none',
                'input_refs': [
                    {'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'},
                    {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'},
                ],
            },
            {
                'id': 'G2', 'kind': 'verify', 'objective': '确认登录结果',
                'completion_criteria': '登录后区域可见', 'side_effect': 'none',
                'verification': {'mode': 'visible'},
            },
        ]))
        credential_definitions = {
            item['name']: item
            for item in variable_definitions_for_goal_plan(credential_plan)
        }
        self.assertTrue(credential_definitions['UI_TEST_USERNAME']['required'])
        self.assertFalse(credential_definitions['UI_TEST_USERNAME']['is_secret'])
        self.assertTrue(credential_definitions['UI_TEST_PASSWORD']['is_secret'])


class GoalRecorderTests(SimpleTestCase):
    @staticmethod
    def _event(recorder, name, run_id, inputs, output):
        recorder.on_tool_start({'name': name}, '', run_id=run_id, inputs=inputs)
        recorder.on_tool_end(output, run_id=run_id)

    def _trace(self):
        plan = GoalPlan.model_validate(plan_payload())
        user_value = 'aits-user-unique-001'
        display_value = 'aits-display-unique-002'
        recorder = ExplorationTraceRecorder('/users')
        recorder.set_active_goal('G1', {})
        self._event(recorder, 'playwright_navigate', 'nav', {'url': '/users'}, 'navigated /users')
        self._event(recorder, 'playwright_get_visible_text', 'observe1', {}, '用户列表')
        recorder.record_goal_run(evaluate_goal_events(plan.goals[0], recorder.events))

        recorder.set_active_goal('G2', {'USER_NAME': user_value, 'DISPLAY_NAME': display_value}, {'USER_NAME': 'generated', 'DISPLAY_NAME': 'runtime'})
        recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='failed', inputs={'selector': '#broken'})
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='failed')
        self._event(recorder, 'playwright_fill', 'fill-user', {'selector': '#user-input', 'value': user_value}, 'filled')
        self._event(recorder, 'playwright_fill', 'fill-display', {'selector': '#display-input', 'value': display_value}, 'filled')
        self._event(recorder, 'playwright_press_key', 'press', {'selector': '#display-input', 'key': 'Shift+Enter'}, 'pressed')
        self._event(recorder, 'playwright_click', 'click', {'selector': f'tr[data-user="{user_value}"] button'}, 'clicked')
        self._event(recorder, 'playwright_get_visible_text', 'observe2', {}, '操作已完成')
        selected = [item.event_id for item in recorder.events if item.goal_id == 'G2' and item.status == 'succeeded']
        recorder.record_goal_run(evaluate_goal_events(plan.goals[1], recorder.events, {'status': 'completed', 'selected_event_ids': list(reversed(selected)), 'assertion_event_ids': []}))

        recorder.set_active_goal('G3', {'USER_NAME': user_value}, {'USER_NAME': 'generated'}, plan.goals[2].verification.model_dump(mode='json'))
        self._event(recorder, 'playwright_get_visible_html', 'verify', {'selector': '#result'}, f'<section id="result">{user_value}</section>')
        recorder.record_goal_run(evaluate_goal_events(plan.goals[2], recorder.events))

        recorder.set_active_goal('G4', {'USER_NAME': user_value}, {'USER_NAME': 'generated'}, plan.goals[3].verification.model_dump(mode='json'))
        self._event(recorder, 'playwright_click', 'cleanup', {'selector': f'tr[data-user="{user_value}"] button'}, 'clicked')
        self._event(recorder, 'playwright_get_visible_text', 'cleanup-observe', {}, '清理已提交')
        self._event(recorder, 'playwright_get_visible_html', 'cleanup-verify', {'selector': '#result'}, '<section id="result">empty</section>')
        recorder.record_goal_run(evaluate_goal_events(plan.goals[3], recorder.events))
        return plan, recorder.build(tool_stats={'total_tool_calls': 13}), user_value, display_value

    def test_callback_binds_goal_and_precisely_maps_multiple_input_values(self):
        _, trace, _, _ = self._trace()
        failed = next(item for item in trace.events if item.status == 'failed')
        fills = [item for item in trace.events if item.action == 'fill']
        self.assertEqual(failed.goal_id, 'G2')
        self.assertEqual([item.input_refs for item in fills], [['USER_NAME'], ['DISPLAY_NAME']])
        self.assertEqual([item.input_source for item in fills], ['generated', 'runtime'])
        self.assertTrue(all(len(item.input_refs) <= 1 for item in trace.events))

    def test_runtime_values_are_templated_and_credentials_do_not_persist(self):
        plan, trace, user_value, display_value = self._trace()
        serialized = trace.model_dump_json()
        self.assertNotIn(user_value, serialized)
        self.assertNotIn(display_value, serialized)
        click_evidence = next(item for item in trace.locator_evidence if item.goal_id == 'G2' and item.action == 'click')
        self.assertIn('{{USER_NAME}}', click_evidence.value)

        credential_plan = GoalPlan.model_validate(plan_payload(credentials_required=True, goals=[
            {'id': 'G1', 'kind': 'setup', 'objective': '进入登录页', 'completion_criteria': '登录页已观察', 'input_refs': [{'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'}, {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'}], 'side_effect': 'none'},
            {'id': 'G2', 'kind': 'verify', 'objective': '确认登录状态', 'completion_criteria': '状态可见', 'verification': {'mode': 'visible'}, 'side_effect': 'none'},
        ]))
        explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={})
        explorer._configure(credential_plan, '/', {'username': 'private-user', 'password': 'private-pass'})
        values, sources = explorer._runtime_values_for_goal(credential_plan.goals[0], {'username': 'private-user', 'password': 'private-pass'})
        recorder = ExplorationTraceRecorder('/')
        recorder.set_active_goal('G1', values, sources)
        self._event(recorder, 'playwright_fill', 'credential', {'selector': '#login-user', 'value': 'private-user'}, 'filled private-user')
        self.assertNotIn('private-user', recorder.build(tool_stats={}).model_dump_json())
        self.assertNotIn('private-pass', recorder.build(tool_stats={}).model_dump_json())

    def test_verify_requires_locator_backed_html_callback_and_nonmatching_exercise_stays_uncertain(self):
        plan, trace, _, _ = self._trace()
        verify_run = next(item for item in trace.goal_runs if item.goal_id == 'G3')
        verify_event = next(item for item in trace.events if item.event_id == verify_run.assertion_event_ids[0])
        self.assertEqual(verify_event.tool_name, 'playwright_get_visible_html')
        self.assertEqual(verify_event.locator_input['selector'], '#result')
        self.assertTrue(verify_event.assertion_result.matched)

        observe_only = ExplorationEvent(event_id='E000001', sequence=1, goal_id='G2', tool_name='playwright_get_visible_text', action='observe', status='succeeded', result_excerpt='页面存在')
        self.assertEqual(evaluate_goal_events(plan.goals[1], [observe_only]).status, 'uncertain')
        with self.assertRaises(GenerationContractError):
            evaluate_goal_events(plan.goals[2], trace.events, {'status': 'completed', 'selected_event_ids': [], 'assertion_event_ids': []})

    def test_deterministic_fallback_keeps_all_successful_goal_actions(self):
        plan, trace, _, _ = self._trace()
        run = evaluate_goal_events(plan.goals[1], trace.events)
        selected = {
            event.event_id: event.action
            for event in trace.events
            if event.event_id in run.selected_event_ids
        }
        self.assertEqual(run.status, 'completed')
        self.assertEqual(
            [action for action in selected.values() if action != 'observe'],
            ['fill', 'fill', 'press', 'click'],
        )

    def test_locator_capture_preserves_boolean_exact_and_never_truncates_selectors(self):
        recorder = ExplorationTraceRecorder('/items')
        recorder.set_active_goal('G1')
        self._event(
            recorder, 'playwright_click', 'exact-false',
            {'text': 'Continue', 'exact': False}, 'clicked',
        )
        self._event(
            recorder, 'playwright_click', 'oversized',
            {'selector': '#' + ('x' * 400)}, 'clicked',
        )
        events = recorder.events
        self.assertIs(events[0].locator_input['exact'], False)
        self.assertNotIn('selector', events[1].locator_input)


class ReplayTests(SimpleTestCase):
    def test_replay_uses_event_sequence_templates_press_and_cleanup_assertion_order(self):
        plan, trace, user_value, _ = GoalRecorderTests()._trace()
        replay = ReplayPlanner.build(plan, trace)
        sequences = {event.event_id: event.sequence for event in trace.events}
        self.assertEqual([action.event_id for action in replay.actions], sorted((action.event_id for action in replay.actions), key=sequences.get))
        source = PythonReplayCompiler.compile(plan, trace, replay)
        self.assertTrue(source.splitlines()[0].startswith('"'))
        self.assertLess(source.index('import time'), source.index('async def run'))
        self.assertIn('async def run(page, variables):', source)
        self.assertIn('_value_for("USER_NAME", "generated")', source)
        self.assertIn('press("Shift+Enter")', source)
        self.assertNotIn(user_value, source)
        self.assertNotIn('page.locator("body")', source)
        self.assertIn('to_contain_text(_value_for("USER_NAME", "generated"))', source)
        finally_at = source.index('finally:')
        cleanup_at = source.index('[G4/', finally_at)
        cleanup_assertion_at = source.index('not_to_contain_text', finally_at)
        self.assertLess(cleanup_at, cleanup_assertion_at)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertFalse(report['blockers'])

    def test_unmapped_input_or_missing_verification_blocks_replay(self):
        plan, trace, _, _ = GoalRecorderTests()._trace()
        broken_events = [item.model_copy(update={'input_refs': [], 'input_source': ''}) if item.action == 'fill' and item.input_refs == ['USER_NAME'] else item for item in trace.events]
        broken = trace.model_copy(update={'events': broken_events})
        self.assertTrue(required_goal_evidence_gaps(plan, broken))
        with self.assertRaises(GenerationContractError):
            ReplayPlanner.build(plan, broken)
        no_assertions = trace.model_copy(update={'goal_runs': [item.model_copy(update={'assertion_event_ids': []}) if item.goal_id == 'G4' else item for item in trace.goal_runs]})
        self.assertTrue(required_goal_evidence_gaps(plan, no_assertions))
        with self.assertRaises(GenerationContractError):
            ReplayPlanner.build(plan, no_assertions)

    def test_plan_runtime_values_reuse_one_value_across_goals(self):
        plan = GoalPlan.model_validate(plan_payload())
        explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={})
        explorer._configure(plan, '/', None)
        exercise_values, _ = explorer._runtime_values_for_goal(plan.goals[1], None)
        verify_values, _ = explorer._runtime_values_for_goal(plan.goals[2], None)
        cleanup_values, _ = explorer._runtime_values_for_goal(plan.goals[3], None)
        self.assertEqual(exercise_values['USER_NAME'], verify_values['USER_NAME'])
        self.assertEqual(verify_values['USER_NAME'], cleanup_values['USER_NAME'])

    def test_goal_prompt_requires_html_probe_and_only_declared_credential_refs_receive_values(self):
        plan = GoalPlan.model_validate(plan_payload())
        prompts = []

        class CapturingAgent:
            def __init__(self, **kwargs):
                pass

            async def initialize(self):
                return None

            async def run(self, prompt, **kwargs):
                prompts.append(prompt)

        explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={})
        explorer._configure(plan, '/', {'username': 'private-user', 'password': 'private-pass'})
        with patch('web_testing.mcp_page_explorer.MCPAgent', CapturingAgent):
            asyncio.run(explorer._run_goal(None, plan, plan.goals[2], '/', '/', {'username': 'private-user', 'password': 'private-pass'}, time.monotonic() + 5, supplement=False))
        prompt = json.loads(prompts[0])
        self.assertEqual(prompt['verification_probe'], {'mode': 'contains_ref', 'input_ref': 'USER_NAME'})
        self.assertNotIn('private-user', prompt['runtime_input_values'].values())
        self.assertIn('playwright_get_visible_html', prompt['instruction'])

        credential_plan = GoalPlan.model_validate(plan_payload(credentials_required=True, goals=[
            {'id': 'G1', 'kind': 'setup', 'objective': '进入登录页', 'completion_criteria': '登录页已观察', 'input_refs': [{'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'}, {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'}], 'side_effect': 'none'},
            {'id': 'G2', 'kind': 'verify', 'objective': '确认登录状态', 'completion_criteria': '状态可见', 'verification': {'mode': 'visible'}, 'side_effect': 'none'},
        ]))
        credential_explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={})
        credential_explorer._configure(credential_plan, '/', {'username': 'private-user', 'password': 'private-pass'})
        with patch('web_testing.mcp_page_explorer.MCPAgent', CapturingAgent):
            asyncio.run(credential_explorer._run_goal(None, credential_plan, credential_plan.goals[0], '/', '/', {'username': 'private-user', 'password': 'private-pass'}, time.monotonic() + 5, supplement=False))
        credential_prompt = json.loads(prompts[-1])
        self.assertEqual(credential_prompt['runtime_input_values'], {'UI_TEST_USERNAME': 'private-user', 'UI_TEST_PASSWORD': 'private-pass'})


class GoalPolicyTests(SimpleTestCase):
    def test_policy_uses_goal_metadata_not_control_words(self):
        plan = GoalPlan.model_validate(plan_payload())
        policy = ExplorationPolicy.for_plan(plan, generation_id='run-1', user_constraints='')
        policy.set_active_goal('G1')
        self.assertFalse(policy.current_goal_may_write())
        policy.set_active_goal('G2')
        self.assertTrue(policy.current_goal_may_write())
        policy.set_active_goal('G4')
        self.assertTrue(policy.current_goal_may_write())
        self.assertNotIn('CRUD', open(__file__.replace('test_goal_generation_flow.py', 'exploration_policy.py'), encoding='utf-8').read())
