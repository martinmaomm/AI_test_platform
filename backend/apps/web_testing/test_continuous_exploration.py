"""Focused regressions for v4 final-path callback selection."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .exploration_trace import ExplorationTrace, ExplorationTraceRecorder, FinalizedAction, FinalizedAssertion, effective_scenario_plan
from .generation_contracts import GenerationContractError, ScenarioPlan
from .generation_workspace import variable_definitions_for_scenario_plan
from .mcp_page_explorer import (
    MCP_FINALIZATION_BROWSER_CALL_LIMIT,
    FinalizationOnlyBrowserToolError,
    MCPPageExplorer,
    ReadOnlyMCPBrowserToolGuard,
    build_dynamic_input_tools,
    build_finalization_tools,
)


def plan_payload(*, cleanup=False, **overrides):
    criteria = ['生成值出现在结果区域']
    requirements = [{'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main', 'kind': 'contains_ref', 'input_ref': 'ITEM_NAME', 'literal': ''}]
    if cleanup:
        criteria.append('生成值在清理后不再出现')
        requirements.append({'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup', 'kind': 'not_contains_ref', 'input_ref': 'ITEM_NAME', 'literal': ''})
    payload = {'schema_version': 4, 'title': '连续场景', 'objective': '同一会话完成页面流程。', 'instructions': ['进入页面', '完成操作'], 'success_criteria': criteria, 'assertion_requirements': requirements, 'input_refs': [{'name': 'ITEM_NAME', 'source': 'generated'}], 'preconditions': [], 'forbidden_actions': [], 'credentials_required': False, 'allow_test_data_writes': True, 'cleanup_expected': cleanup, 'discovery_notes': [], 'risk_level': 'low'}
    payload.update(overrides)
    return payload


class Driver:
    def __init__(self, plan, start_path='/'):
        self.plan, self.index = plan, 0
        self.recorder = ExplorationTraceRecorder(start_path)
        self.recorder.configure_plan(plan)
        runtime_values = {'ITEM_NAME': 'runtime-item'}
        runtime_values.update({
            item.name: f'private-{item.credential_slot}'
            for item in plan.input_refs if item.source == 'credential'
        })
        self.recorder.configure_runtime(runtime_values, plan.input_sources())

    def event(self, tool, inputs, output, error=False):
        self.index += 1
        run_id = f'callback-{self.index}'
        self.recorder.on_tool_start({'name': tool}, '', run_id=run_id, inputs=inputs)
        (self.recorder.on_tool_error if error else self.recorder.on_tool_end)(RuntimeError(str(output)) if error else output, run_id=run_id)

    def complete_main(self):
        self.event('playwright_navigate', {'url': 'https://example.test/'}, {'page_url': 'https://example.test/'})
        self.event('playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled')
        self.event('playwright_click', {'selector': '#submit'}, 'clicked')
        self.event('playwright_get_visible_html', {'selector': '#results'}, '<main>runtime-item</main>')

    def finalize(self, main, assertions, cleanup=()):
        self.recorder.candidate_summary()
        return self.recorder.finalize_path(
            main_actions=[FinalizedAction(event_id=event_id, step_name=name) for event_id, name in main],
            assertions=[FinalizedAssertion(assertion_id=assertion_id, event_id=event_id) for assertion_id, event_id in assertions],
            cleanup_actions=[FinalizedAction(event_id=event_id, step_name=name) for event_id, name in cleanup],
        )

    def build(self):
        return self.recorder.build(tool_stats={'total_tool_calls': self.index})


class FinalizationTraceTests(SimpleTestCase):
    def test_no_checkpoint_callback_and_complete_path_is_selected(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.complete_main()
        driver.finalize([('E000002', '填写测试值'), ('E000003', '提交表单')], [('A1', 'E000004')])
        trace = driver.build()
        self.assertEqual(trace.replay_event_ids, ['E000001', 'E000002', 'E000003'])
        self.assertEqual(trace.finalization.status, 'valid')
        self.assertNotIn('checkpoint', trace.model_dump_json().lower())

    def test_bad_action_finalizations_are_rejected(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan); driver.complete_main()
        for actions in ([('E999999', '未知动作')], [('E000002', '填写'), ('E000002', '重复')], [('E000003', '提交'), ('E000002', '填写')]):
            with self.assertRaises(GenerationContractError):
                driver.finalize(actions, [('A1', 'E000004')])
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_click', {'selector': '.row:nth-child(2)'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>runtime-item</main>')
        with self.assertRaises(GenerationContractError):
            driver.finalize([('E000002', '选择记录')], [('A1', 'E000003')])
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_fill', {'selector': '#name', 'value': 'unmapped'}, 'filled')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>runtime-item</main>')
        with self.assertRaises(GenerationContractError):
            driver.finalize([('E000002', '填写')], [('A1', 'E000003')])

    def test_assertion_and_cleanup_semantics_are_strict(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>other</main>')
        with self.assertRaises(GenerationContractError):
            driver.finalize([], [('A1', 'E000002')])
        plan = ScenarioPlan.model_validate(plan_payload(cleanup=True))
        driver = Driver(plan); driver.complete_main()
        driver.event('playwright_click', {'selector': '#cleanup'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>empty</main>')
        driver.finalize([('E000002', '填写测试值'), ('E000003', '提交表单')], [('A1', 'E000004'), ('A2', 'E000006')], [('E000005', '清理测试数据')])
        trace = driver.build()
        self.assertEqual(trace.cleanup['status'], 'completed')

    def test_finalization_is_invalidated_by_later_callback_and_old_payload_rejected(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan); driver.complete_main()
        driver.finalize([('E000002', '填写测试值'), ('E000003', '提交表单')], [('A1', 'E000004')])
        driver.event('playwright_snapshot', {}, '<main>still here</main>')
        trace = driver.build()
        self.assertEqual(trace.finalization.error_code, 'FINALIZATION_STALE')
        self.assertFalse(trace.replay_event_ids)
        payload = trace.model_dump(mode='json'); payload['events'][0]['checkpoint_id'] = 'M000001'
        with self.assertRaises(Exception): ExplorationTrace.model_validate(payload)

    def test_candidate_summary_redacts_url_and_runtime_values(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/private'}, 'ok')
        driver.event('playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled runtime-item')
        summary = driver.recorder.candidate_summary()
        encoded = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn('https://example.test', encoded)
        self.assertNotIn('runtime-item', encoded)
        self.assertEqual(summary['events'][1]['input_refs'], ['ITEM_NAME'])
        self.assertEqual(summary['candidate_sequence'], 2)

    def test_dynamic_generated_input_is_redacted_and_added_to_effective_plan(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            success_criteria=['结果区域可见'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            input_refs=[],
        ))
        driver = Driver(plan)
        tool = build_dynamic_input_tools(
            driver.recorder, lambda value_kind: f'private-{value_kind}-value',
        )[0]
        declared = tool.invoke({'value_kind': 'email'})
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_fill', {'selector': '#new-field', 'value': declared['value']}, 'filled')
        driver.event('playwright_click', {'selector': '#submit'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#result'}, '<main>saved</main>')
        driver.finalize(
            [('E000002', '填写动态输入'), ('E000003', '提交')], [('A1', 'E000004')],
        )
        trace = driver.build()
        encoded = trace.model_dump_json()
        self.assertIn('DYNAMIC_INPUT_1', encoded)
        self.assertIn('"value_kind":"email"', encoded)
        self.assertNotIn(declared['value'], encoded)
        effective_plan = effective_scenario_plan(plan, trace)
        self.assertEqual(
            [(item.name, item.source, item.value_kind) for item in effective_plan.input_refs],
            [('DYNAMIC_INPUT_1', 'generated', 'email')],
        )
        self.assertEqual(
            len(effective_scenario_plan(effective_plan, trace).input_refs), 1,
        )
        conflicting_plan = ScenarioPlan.model_validate(plan_payload(
            success_criteria=['结果区域可见'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            input_refs=[{'name': 'DYNAMIC_INPUT_1', 'source': 'runtime'}],
        ))
        with self.assertRaisesRegex(GenerationContractError, 'DYNAMIC_INPUT_CONFLICT'):
            effective_scenario_plan(conflicting_plan, trace)

    def test_default_generated_values_are_short_and_email_is_valid_example_com(self):
        explorer = MCPPageExplorer(
            llm_model=Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4()),
        )
        long_ref = 'A' * 128
        text = explorer._generated_runtime_value('text', long_ref)
        email = explorer._generated_runtime_value('email', long_ref)
        self.assertLessEqual(len(text), 32)
        self.assertLessEqual(len(email), 64)
        self.assertRegex(email, r'^aits-[0-9a-f]{8}-[0-9a-f]{16}@example\.com$')

    def test_declared_but_unselected_dynamic_input_does_not_pollute_effective_plan(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            success_criteria=['结果区域可见'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            input_refs=[],
        ))
        driver = Driver(plan)
        declared = build_dynamic_input_tools(
            driver.recorder, lambda _value_kind: 'private-password-value',
        )[0].invoke({'value_kind': 'password'})
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_click', {'selector': '#submit'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#result'}, '<main>saved</main>')
        driver.finalize([('E000002', '提交')], [('A1', 'E000003')])
        trace = driver.build()
        effective_plan = effective_scenario_plan(plan, trace)
        self.assertFalse(effective_plan.input_refs)
        self.assertEqual(variable_definitions_for_scenario_plan(effective_plan), [])
        self.assertNotIn(declared['name'], {item.name for item in effective_plan.input_refs})

    def test_dynamic_password_is_secret_in_workspace(self):
        password_plan = ScenarioPlan.model_validate(plan_payload(input_refs=[{
            'name': 'DYNAMIC_INPUT_1', 'source': 'generated', 'value_kind': 'password',
        }], success_criteria=['结果区域可见'], assertion_requirements=[{
            'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
            'kind': 'visible', 'input_ref': '', 'literal': '',
        }]))
        definitions = variable_definitions_for_scenario_plan(password_plan)
        self.assertEqual(definitions[0]['value'], '')
        self.assertTrue(definitions[0]['is_secret'])

    def test_finalization_requires_current_candidate_summary(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.complete_main()
        payload = {
            'main_actions': [FinalizedAction(event_id='E000002', step_name='填写测试值'), FinalizedAction(event_id='E000003', step_name='提交表单')],
            'assertions': [FinalizedAssertion(assertion_id='A1', event_id='E000004')],
            'cleanup_actions': [],
        }
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_CANDIDATES_REQUIRED'):
            driver.recorder.finalize_path(**payload)
        driver.recorder.candidate_summary()
        self.assertEqual(driver.recorder.finalize_path(**payload)['status'], 'accepted')
        driver.event('playwright_snapshot', {}, '<main>runtime-item</main>')
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_CANDIDATES_STALE'):
            driver.recorder.finalize_path(**payload)
        driver.recorder.candidate_summary()
        self.assertEqual(driver.recorder.finalize_path(**payload)['status'], 'accepted')

    def test_finalization_tool_returns_rejection_without_stopping_agent(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.complete_main()
        finalize = next(tool for tool in build_finalization_tools(driver.recorder) if tool.name == 'aits_finalize_path')
        rejected = finalize.invoke({
            'main_actions': [{'event_id': 'E000002', 'step_name': '填写测试值'}],
            'assertions': [{'assertion_id': 'A1', 'event_id': 'E000004'}],
            'cleanup_actions': [],
        })
        self.assertEqual(rejected, {
            'status': 'rejected', 'error_code': 'FINALIZATION_CANDIDATES_REQUIRED',
        })
        self.assertEqual(driver.recorder.build(tool_stats={}).finalization.status, 'invalid')

    def test_finalization_rejects_missing_press_key_and_input_dependencies(self):
        plan = ScenarioPlan.model_validate(plan_payload())
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_press_key', {'selector': '#name'}, 'pressed')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>runtime-item</main>')
        self.assertFalse(driver.recorder.candidate_summary()['events'][1]['compilable'])
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_PRESS_KEY_MISSING'):
            driver.finalize([('E000002', '提交')], [('A1', 'E000003')])

        plan = ScenarioPlan.model_validate(plan_payload(input_refs=[
            {'name': 'ITEM_NAME', 'source': 'generated'},
            {'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'},
            {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'},
        ], credentials_required=True))
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled')
        driver.event('playwright_click', {'selector': '#submit'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>runtime-item</main>')
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_NON_RUNTIME_INPUT_MISSING'):
            driver.finalize([('E000002', '填写测试值'), ('E000003', '提交表单')], [('A1', 'E000004')])

    def test_assertion_ref_requires_prior_selected_input_action(self):
        plan = ScenarioPlan.model_validate(plan_payload(input_refs=[{'name': 'ITEM_NAME', 'source': 'runtime'}]))
        driver = Driver(plan)
        driver.complete_main()
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_ASSERTION_INPUT_DEPENDENCY_MISSING'):
            driver.finalize([('E000003', '提交表单')], [('A1', 'E000004')])

    def test_non_runtime_inputs_cannot_be_covered_only_by_cleanup_actions(self):
        plan = ScenarioPlan.model_validate(plan_payload(
            success_criteria=['结果区域可见'],
            assertion_requirements=[{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            input_refs=[
                {'name': 'ITEM_NAME', 'source': 'generated'},
                {'name': 'UI_TEST_USERNAME', 'source': 'credential', 'credential_slot': 'username'},
                {'name': 'UI_TEST_PASSWORD', 'source': 'credential', 'credential_slot': 'password'},
            ],
            credentials_required=True,
        ))
        driver = Driver(plan)
        driver.event('playwright_navigate', {'url': 'https://example.test/'}, 'ok')
        driver.event('playwright_click', {'selector': '#submit'}, 'clicked')
        driver.event('playwright_get_visible_html', {'selector': '#results'}, '<main>visible</main>')
        driver.event('playwright_fill', {'selector': '#name', 'value': 'runtime-item'}, 'filled')
        driver.event('playwright_fill', {'selector': '#username', 'value': 'private-username'}, 'filled')
        driver.event('playwright_fill', {'selector': '#password', 'value': 'private-password'}, 'filled')
        with self.assertRaisesRegex(GenerationContractError, 'FINALIZATION_NON_RUNTIME_INPUT_MISSING'):
            driver.finalize(
                [('E000002', '提交表单')], [('A1', 'E000003')],
                [('E000004', '清理输入'), ('E000005', '清理凭据'), ('E000006', '清理凭据')],
            )

class SingleAgentExplorerTests(SimpleTestCase):
    def test_one_client_one_agent_one_run_and_finalization_tools(self):
        class Client:
            opened = closed = 0
            async def create_all_sessions(self): self.opened += 1
            async def close_all_sessions(self): self.closed += 1
        class Agent:
            created = runs = registrations = 0
            prompt = None
            def __init__(self, **kwargs): type(self).created += 1; self.guard = kwargs['callbacks'][0]
            async def initialize(self): self.guard.on_chat_model_start({}, [])
            async def register_local_tools(self, tools): type(self).registrations += 1; self.tools = tools
            async def run(self, prompt, **_kwargs):
                type(self).runs += 1; type(self).prompt = json.loads(prompt)
                self.guard.on_tool_start({'name': 'playwright_navigate'}, '', run_id='goto', inputs={'url': 'https://example.test/'})
                self.guard.on_tool_end('ok', run_id='goto')
        client = Client(); plan = ScenarioPlan.model_validate(plan_payload())
        with override_settings(BASE_DIR='/tmp'), patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch('web_testing.mcp_page_explorer.MCPAgent', Agent):
            trace = asyncio.run(MCPPageExplorer(llm_model=Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4())).explore_until_complete(plan=plan, start_path='/', target_url_safe='/'))
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertEqual((Agent.created, Agent.registrations, Agent.runs), (1, 1, 1))
        self.assertIn('finalization_protocol', Agent.prompt)
        self.assertIn('aits_declare_generated_input', Agent.prompt['finalization_protocol']['input_rule'])
        self.assertFalse(trace.replay_event_ids)


class FinalizationOnlyBudgetTests(SimpleTestCase):
    def test_finalization_only_budget_is_recoverable_then_bounded(self):
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=63)
        for index in range(MCP_FINALIZATION_BROWSER_CALL_LIMIT):
            run_id = f'read-{index}'
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id=run_id, inputs={})
            guard.on_tool_end('page state', run_id=run_id)
        with self.assertRaises(FinalizationOnlyBrowserToolError):
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='soft-1', inputs={})
        self.assertIsNone(guard.terminal_error)
        self.assertTrue(guard.get_stats()['finalization_only_mode'])
        self.assertEqual(guard.get_stats()['total_tool_calls'], MCP_FINALIZATION_BROWSER_CALL_LIMIT)
        explorer = MCPPageExplorer(
            llm_model=Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4()),
        )
        explorer.guard = guard
        async def await_local_finalization_window():
            task = asyncio.create_task(asyncio.sleep(0))
            await explorer._await_task(task, time.monotonic() + 1, '/')
        asyncio.run(await_local_finalization_window())
        with self.assertRaises(FinalizationOnlyBrowserToolError):
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='soft-2', inputs={})
        with self.assertRaisesRegex(Exception, '连续忽略收尾提示'):
            guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='hard', inputs={})
        self.assertEqual(guard.terminal_error.error_kind, 'finalization_browser_budget_exhausted')
