"""Focused regressions for the v4 single-agent callback contract."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .exploration_policy import ExplorationPolicy
from .exploration_trace import CHECKPOINT_TOOL_NAME, ExplorationTraceRecorder
from .generation_contracts import ScenarioPlan
from .mcp_page_explorer import (
    MCPPageExplorer,
    ReadOnlyMCPBrowserToolGuard,
    build_exploration_checkpoint_tool,
)
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .script_quality import evaluate_script


def scenario_payload(*, cleanup: bool = False, **overrides):
    success_criteria = ['生成值出现在结果区域']
    assertions = [{
        'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
        'kind': 'contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
    }]
    if cleanup:
        success_criteria.append('生成值在清理后不再出现')
        assertions.append({
            'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup',
            'kind': 'not_contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
        })
    value = {
        'schema_version': 4,
        'title': '连续场景',
        'objective': '在同一会话完成页面流程并验证结果。',
        'instructions': ['进入目标页面', '完成必要操作', '观察验证结果'],
        'success_criteria': success_criteria,
        'assertion_requirements': assertions,
        'input_refs': [{'name': 'ITEM_NAME', 'source': 'generated'}],
        'preconditions': [], 'forbidden_actions': [],
        'credentials_required': False, 'allow_test_data_writes': True,
        'cleanup_expected': cleanup, 'discovery_notes': [], 'risk_level': 'low',
    }
    value.update(overrides)
    return value


class TraceDriver:
    def __init__(self, plan: ScenarioPlan, start_path: str = '/'):
        self.recorder = ExplorationTraceRecorder(start_path)
        self.recorder.configure_plan(plan)
        self.recorder.configure_runtime(
            {'ITEM_NAME': 'runtime-item'}, plan.input_sources(),
        )
        self.sequence = 0

    def marker(self, phase: str, intent: str, assertion_id: str = ''):
        self.sequence += 1
        run_id = f'marker-{self.sequence}'
        self.recorder.on_tool_start(
            {'name': CHECKPOINT_TOOL_NAME}, '', run_id=run_id,
            inputs={'phase': phase, 'intent': intent, 'assertion_id': assertion_id},
        )
        self.recorder.on_tool_end('checkpoint accepted', run_id=run_id)

    def event(self, tool_name: str, inputs: dict, output, *, error: bool = False):
        self.sequence += 1
        run_id = f'event-{self.sequence}'
        self.recorder.on_tool_start(
            {'name': tool_name}, '', run_id=run_id, inputs=inputs,
        )
        if error:
            self.recorder.on_tool_error(RuntimeError(str(output)), run_id=run_id)
        else:
            self.recorder.on_tool_end(output, run_id=run_id)

    def main_fixture(self):
        self.marker('main', 'replay')
        self.event(
            'playwright_navigate', {'url': 'https://example.test/items'},
            {'structuredContent': {'page_url': 'https://example.test/items'}},
        )
        self.marker('main', 'replay')
        self.event(
            'playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled',
        )
        self.marker('assertion', 'evidence', 'A1')
        self.event(
            'playwright_get_visible_html', {'selector': '#results'},
            '<main>runtime-item</main>',
        )


class ScenarioPlanTests(SimpleTestCase):
    def test_v4_rejects_goal_contract_and_requires_criterion_assertion_coverage(self):
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate({
                'schema_version': 3, 'title': '旧记录', 'objective': '旧目标', 'goals': [],
            })
        plan = ScenarioPlan.model_validate(scenario_payload())
        self.assertEqual(plan.input_sources(), {'ITEM_NAME': 'generated'})
        missing = scenario_payload(assertion_requirements=[])
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(missing)

    def test_cleanup_contract_requires_dedicated_cleanup_assertion(self):
        value = scenario_payload()
        value.update({'cleanup_expected': True, 'allow_test_data_writes': True})
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(value)
        plan = ScenarioPlan.model_validate(scenario_payload(cleanup=True))
        self.assertEqual(plan.assertion_requirements[-1].phase, 'cleanup')

    def test_cleanup_contract_cannot_replace_main_result_or_confirm_residual_value(self):
        only_cleanup = scenario_payload(
            cleanup_expected=True,
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'cleanup',
                'kind': 'not_contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
            }],
        )
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(only_cleanup)
        positive_cleanup = scenario_payload(cleanup=True)
        positive_cleanup['assertion_requirements'][-1].update({
            'kind': 'contains_ref', 'input_ref': 'ITEM_NAME',
        })
        with self.assertRaises(Exception):
            ScenarioPlan.model_validate(positive_cleanup)


class ContinuousTraceTests(SimpleTestCase):
    def test_error_text_is_failed_and_nested_spa_location_updates_without_reset(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        driver = TraceDriver(plan, '/start')
        driver.event('playwright_click', {'selector': '#missing'}, 'Error: locator not found')
        driver.event(
            'playwright_snapshot', {},
            {'result': {'current_url': 'https://example.test/app#/records/42'}},
        )
        driver.event(
            'playwright_get_visible_html', {'selector': '#main'},
            '<main>No items found; failed to load archived data '
            '<a href="https://example.test/not-current">help</a></main>',
        )
        trace = driver.recorder.build(tool_stats={'duration_seconds': 1.25})
        self.assertEqual(trace.events[0].status, 'failed')
        self.assertEqual(trace.events[-1].status, 'succeeded')
        self.assertEqual(trace.last_location, '/app#/records/42')
        self.assertEqual(trace.events[-1].relative_path, '/app#/records/42')
        self.assertEqual(trace.tool_stats['duration_seconds'], 1.25)
        self.assertNotIn('https://example.test', trace.model_dump_json())

        driver.event(
            'playwright_click', {'selector': '#retry'}, 'failed to click target',
        )
        retried = driver.recorder.build(tool_stats={})
        self.assertEqual(retried.events[-1].status, 'failed')

    def test_only_explicit_success_path_replays_and_failed_marker_is_consumed(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        driver = TraceDriver(plan)
        driver.event('playwright_click', {'selector': '#detour'}, 'clicked')
        driver.marker('main', 'replay')
        driver.event(
            'playwright_click', {'selector': '#wrong'},
            'strict mode violation: locator resolved to 2 elements',
        )
        driver.event('playwright_click', {'selector': '#unmarked-retry'}, 'clicked')
        driver.marker('main', 'replay')
        driver.event('playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled')
        driver.marker('assertion', 'evidence', 'A1')
        driver.event(
            'playwright_get_visible_html', {'selector': '#results'},
            '<main>runtime-item</main>',
        )
        trace = driver.recorder.build(tool_stats={})
        self.assertEqual(trace.replay_event_ids, ['E000004'])
        source = PythonReplayCompiler.compile(plan, trace, ReplayPlanner.build(plan, trace))
        self.assertNotIn('#wrong', source)
        self.assertNotIn('#detour', source)
        self.assertNotIn('#unmarked-retry', source)

    def test_assertion_evidence_requires_callback_output_to_satisfy_semantics(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        driver = TraceDriver(plan)
        driver.marker('assertion', 'evidence', 'A1')
        driver.event(
            'playwright_get_visible_html', {'selector': '#results'}, '<main>other</main>',
        )
        trace = driver.recorder.build(tool_stats={})
        self.assertFalse(trace.assertion_evidence)
        self.assertEqual(trace.events[0].assertion_status, 'not_satisfied')
        replay = ReplayPlanner.build(plan, trace)
        report = evaluate_script(
            PythonReplayCompiler.compile(plan, trace, replay),
            plan=plan, trace=trace, replay_plan=replay,
        )
        self.assertIn(
            'SUCCESS_CRITERIA_UNCOVERED', {item['code'] for item in report['blockers']},
        )

    def test_interleaved_main_actions_and_assertions_keep_callback_order(self):
        plan = ScenarioPlan.model_validate(scenario_payload(
            success_criteria=[
                '生成值出现在结果区域',
                '后续阶段的目标区域可见',
            ],
            assertion_requirements=[
                {
                    'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                    'kind': 'contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
                },
                {
                    'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'main',
                    'kind': 'visible', 'input_ref': '', 'literal': '',
                },
            ],
        ))
        driver = TraceDriver(plan)
        driver.marker('main', 'replay')
        driver.event('playwright_click', {'selector': '#create'}, 'clicked')
        driver.marker('assertion', 'evidence', 'A1')
        driver.event(
            'playwright_get_visible_html', {'selector': '#results'},
            '<main>runtime-item</main>',
        )
        driver.marker('main', 'replay')
        driver.event('playwright_click', {'selector': '#edit'}, 'clicked')
        driver.marker('assertion', 'evidence', 'A2')
        driver.event(
            'playwright_get_visible_html', {'selector': '#editor'},
            '<section>editor</section>',
        )
        trace = driver.recorder.build(tool_stats={})
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertEqual(
            [(step.step_type, step.event_id) for step in replay.main_steps],
            [
                ('action', 'E000001'), ('assertion', 'E000002'),
                ('action', 'E000003'), ('assertion', 'E000004'),
            ],
        )
        self.assertLess(source.index('[A1/E000002]'), source.index('[E000003]'))
        self.assertEqual(
            {item.assertion_id for item in replay.assertions}, {'A1', 'A2'},
        )
        self.assertFalse(report['blockers'])

    def test_runtime_templates_are_recursive_and_legal_sensitive_named_selector_survives(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        driver = TraceDriver(plan)
        driver.marker('main', 'replay')
        driver.event(
            'playwright_fill',
            {
                'selector': '[name=password][data-owner="runtime-item"]',
                'kwargs': {'has_text': ['runtime-item', {'nested': 'runtime-item'}]},
                'value': 'runtime-item',
            },
            'filled',
        )
        driver.marker('assertion', 'evidence', 'A1')
        driver.event(
            'playwright_get_visible_html',
            {'selector': '[data-owner="runtime-item"]'},
            '<main>runtime-item</main>',
        )
        trace = driver.recorder.build(tool_stats={})
        evidence = {item.event_id: item for item in trace.locator_evidence}
        self.assertEqual(
            evidence['E000001'].value, '[name=password][data-owner="{{ITEM_NAME}}"]',
        )
        self.assertEqual(
            evidence['E000001'].kwargs['has_text'][1]['nested'], '{{ITEM_NAME}}',
        )
        source = PythonReplayCompiler.compile(plan, trace, ReplayPlanner.build(plan, trace))
        self.assertIn('_resolve_template', source)
        self.assertIn('[name=password]', source)
        self.assertNotIn('runtime-item', source)

    def test_nested_select_value_maps_exactly_one_runtime_ref(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        driver = TraceDriver(plan)
        driver.marker('main', 'replay')
        driver.event(
            'playwright_select_option',
            {'selector': '#choice', 'options': [{'value': 'runtime-item'}]},
            'selected',
        )
        trace = driver.recorder.build(tool_stats={})
        self.assertEqual(trace.events[0].input_refs, ['ITEM_NAME'])


class CleanupEvidenceTests(SimpleTestCase):
    def _trace(self, *, verify_cleanup: bool):
        plan = ScenarioPlan.model_validate(scenario_payload(cleanup=True))
        driver = TraceDriver(plan)
        driver.main_fixture()
        driver.marker('cleanup', 'cleanup')
        driver.event('playwright_click', {'selector': '#cleanup'}, 'clicked')
        if verify_cleanup:
            driver.marker('cleanup', 'evidence', 'A2')
            driver.event(
                'playwright_get_visible_html', {'selector': '#results'}, '<main>empty</main>',
            )
        return plan, driver.recorder.build(tool_stats={})

    def test_cleanup_action_without_later_verification_is_attempted_and_needs_review(self):
        plan, trace = self._trace(verify_cleanup=False)
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertEqual(trace.cleanup['status'], 'attempted')
        self.assertIn('finally:', source)
        self.assertNotIn('pass', source)
        self.assertEqual(report['status'], 'needs_review')
        self.assertIn(
            'CLEANUP_VERIFICATION_MISSING', {item['code'] for item in report['blockers']},
        )

    def test_cleanup_action_and_later_semantic_observation_compile_in_finally(self):
        plan, trace = self._trace(verify_cleanup=True)
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertEqual(trace.cleanup['status'], 'completed')
        self.assertIn('# 清理 1：[E000004]', source)
        self.assertIn('# 清理验证 1：[A2/E000005]', source)
        self.assertIn('not_to_contain_text', source)
        self.assertFalse(report['blockers'])
        self.assertIn(report['status'], {'ready', 'ready_with_warnings'})

    def test_cleanup_observation_before_action_does_not_confirm_completion(self):
        plan = ScenarioPlan.model_validate(scenario_payload(cleanup=True))
        driver = TraceDriver(plan)
        driver.main_fixture()
        driver.marker('cleanup', 'evidence', 'A2')
        driver.event(
            'playwright_get_visible_html', {'selector': '#results'}, '<main>empty</main>',
        )
        driver.marker('cleanup', 'cleanup')
        driver.event('playwright_click', {'selector': '#cleanup'}, 'clicked')
        trace = driver.recorder.build(tool_stats={})
        self.assertEqual(trace.cleanup['status'], 'attempted')
        self.assertFalse(trace.cleanup_verification_event_ids)


class SingleAgentExplorerTests(SimpleTestCase):
    def test_one_client_one_agent_one_run_and_local_checkpoint_tool(self):
        class Client:
            opened = closed = 0

            async def create_all_sessions(self):
                self.opened += 1

            async def close_all_sessions(self):
                self.closed += 1

        class Agent:
            created = runs = registrations = 0
            prompt = None
            disallowed_tools = []

            def __init__(self, **kwargs):
                type(self).created += 1
                self.guard = kwargs['callbacks'][0]
                type(self).disallowed_tools = kwargs['disallowed_tools']

            async def initialize(self):
                self.guard.on_chat_model_start({}, [])

            async def register_local_tools(self, tools):
                type(self).registrations += 1
                self.tools = tools

            async def run(self, prompt, **_kwargs):
                type(self).runs += 1
                type(self).prompt = json.loads(prompt)
                self.guard.on_tool_start(
                    {'name': CHECKPOINT_TOOL_NAME}, '', run_id='marker',
                    inputs={'phase': 'assertion', 'intent': 'evidence', 'assertion_id': 'A1'},
                )
                self.guard.on_tool_end('checkpoint accepted', run_id='marker')
                self.guard.on_tool_start(
                    {'name': 'playwright_get_visible_html'}, '', run_id='observe',
                    inputs={'selector': '#main'},
                )
                self.guard.on_tool_end('<main>runtime-item</main>', run_id='observe')

        client = Client()
        plan = ScenarioPlan.model_validate(scenario_payload())
        with override_settings(BASE_DIR='/tmp'), patch(
            'web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client,
        ), patch('web_testing.mcp_page_explorer.MCPAgent', Agent):
            explorer = MCPPageExplorer(
                llm_model=Mock(), mcp_config={'mcpServers': {}},
                generation_id=str(uuid4()),
            )
            trace = asyncio.run(explorer.explore_until_complete(
                plan=plan, start_path='/', target_url_safe='/',
            ))
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertEqual((Agent.created, Agent.registrations, Agent.runs), (1, 1, 1))
        self.assertEqual(Agent.prompt['scenario_plan']['schema_version'], 4)
        self.assertNotIn('goals', Agent.prompt['scenario_plan'])
        self.assertNotIn('playwright_screenshot', Agent.disallowed_tools)
        self.assertEqual(trace.schema_version, 4)

    def test_checkpoint_tool_rejects_unknown_assertion_id(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        tool = build_exploration_checkpoint_tool(plan)
        with self.assertRaises(Exception):
            tool.invoke({
                'phase': 'assertion', 'intent': 'evidence', 'assertion_id': 'A99',
            })
        with self.assertRaises(Exception):
            tool.invoke({'phase': 'cleanup', 'intent': 'cleanup', 'assertion_id': ''})

    def test_persisted_trace_rejects_cleanup_verification_before_action(self):
        plan = ScenarioPlan.model_validate(scenario_payload(cleanup=True))
        driver = TraceDriver(plan)
        driver.main_fixture()
        driver.marker('cleanup', 'cleanup')
        driver.event('playwright_click', {'selector': '#cleanup'}, 'clicked')
        driver.marker('cleanup', 'evidence', 'A2')
        driver.event(
            'playwright_get_visible_html', {'selector': '#results'}, '<main>empty</main>',
        )
        payload = driver.recorder.build(tool_stats={}).model_dump(mode='json')
        cleanup_event = next(
            item for item in payload['events']
            if item['event_id'] in payload['cleanup_event_ids']
        )
        cleanup_event['sequence'] = 99
        with self.assertRaises(Exception):
            type(driver.recorder.build(tool_stats={})).model_validate(payload)

        unbound = driver.recorder.build(tool_stats={}).model_dump(mode='json')
        unbound['events'][0]['checkpoint_id'] = ''
        with self.assertRaises(Exception):
            type(driver.recorder.build(tool_stats={})).model_validate(unbound)

    def test_recoverable_locator_failure_does_not_mark_write_result_unknown(self):
        plan = ScenarioPlan.model_validate(scenario_payload())
        recorder = ExplorationTraceRecorder()
        recorder.configure_plan(plan)
        guard = ReadOnlyMCPBrowserToolGuard(
            policy=ExplorationPolicy.for_plan(
                plan, generation_id='local', user_constraints='',
            ),
            trace_recorder=recorder,
        )
        guard.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='click',
            inputs={'selector': '#later'},
        )
        guard.on_tool_end('Error: element not visible', run_id='click')
        self.assertIsNone(guard.terminal_error)
        self.assertEqual(guard.get_stats()['failed_tool_calls'], 1)
        self.assertEqual(recorder.events[-1].status, 'failed')

        guard.on_tool_start(
            {'name': 'playwright_get_visible_html'}, '', run_id='observe',
            inputs={'selector': '#status'},
        )
        guard.on_tool_end(
            '<main>failed to load archived data</main>', run_id='observe',
        )
        self.assertEqual(guard.get_stats()['failed_tool_calls'], 1)
        self.assertEqual(recorder.events[-1].status, 'succeeded')
