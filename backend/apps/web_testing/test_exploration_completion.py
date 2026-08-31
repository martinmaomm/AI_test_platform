"""Offline regression coverage for the pre-generation exploration gate."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from .exploration_completion import assess_exploration_completion, can_request_user_decision
from .generation_orchestrator import _test_case_context
from .generation_contracts import ExplorationSnapshot, ScenarioSpec, merge_exploration_snapshots
from .mcp_page_explorer import (
    MCP_BROWSER_TOOL_CALL_LIMIT,
    MCPPageExplorer,
    MCPPageExplorerError,
    ReadOnlyMCPBrowserToolGuard,
    exploration_total_timeout_seconds,
)
from .exploration_policy import ExplorationPolicy


def scenario(*, ambiguities=None):
    return ScenarioSpec.model_validate({
        'title': '检查用户列表', 'objective': '确认用户列表页面可用。',
        'steps': [{
            'id': 'S1', 'name': '进入用户列表', 'intent': 'navigate',
            'target_hint': '用户列表', 'expected': '用户列表出现。',
        }],
        'assertions': [{
            'id': 'A1', 'name': '列表可见', 'target_hint': '用户列表',
            'expected': '列表出现。', 'step_id': 'S1',
        }],
        'ambiguities': ambiguities or [],
    })


def snapshot(*, observed='用户列表', unresolved=False, calls=1, questions=None):
    return ExplorationSnapshot.model_validate({
        'start_url_path': '/', 'visited_paths': ['/'],
        'page_states': [{'name': observed, 'path': '/', 'key_regions': [observed]}],
        'elements': [{'page_name': observed, 'visible_name': observed, 'candidate_locators': ['page.get_by_role("heading")']}],
        'step_evidence': {'S1': {
            'status': 'unresolved' if unresolved else 'confirmed',
            'paths': ['/'], 'element_names': [observed], 'reason': 'mock',
        }},
        'unresolved_steps': ['S1'] if unresolved else [],
        'unresolved_questions': questions or [],
        'tool_stats': {
            'total_tool_calls': calls,
            'tool_counts': {'playwright_get_visible_text': calls},
            'failed_tool_calls': 0, 'duration_seconds': 0.1,
        },
        'checkpoints': [{
            'tool_name': 'playwright_get_visible_text', 'call_index': 1, 'status': 'succeeded',
        }] if calls else [],
    })


class ExplorationCompletionTests(SimpleTestCase):
    def test_existing_independent_script_case_context_never_reads_removed_fields(self):
        case = type('Case', (), {
            'title': '旧用例', 'description': '独立脚本描述', 'script_version': 2,
            'test_script_content': 'async def run(page): pass',
        })()
        generation = type('Generation', (), {'test_case': case})()
        self.assertEqual(_test_case_context(generation), {
            'title': '旧用例', 'description': '独立脚本描述',
            'script_version': 2, 'has_script': True,
        })

    def test_login_only_eight_calls_cannot_confirm_the_target_page(self):
        assessed = assess_exploration_completion(scenario(), snapshot(observed='登录', calls=8))
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')
        self.assertEqual(assessed.unresolved_steps, ['S1'])
        self.assertEqual(assessed.completion.missing_targets[0].target, '用户列表')

    def test_observable_page_question_never_becomes_a_user_questionnaire(self):
        assessed = assess_exploration_completion(
            scenario(), snapshot(questions=['用户列表菜单入口在哪里？']),
        )
        self.assertEqual(assessed.completion.status, 'complete')
        self.assertFalse(can_request_user_decision(assessed))

    def test_explicit_business_ambiguity_can_request_user_decision(self):
        question = '删除范围需要用户确认。'
        assessed = assess_exploration_completion(
            scenario(ambiguities=[question]), snapshot(questions=[question]),
        )
        self.assertEqual(assessed.completion.status, 'needs_user_decision')
        self.assertTrue(can_request_user_decision(assessed))

    def test_model_claim_without_actual_tool_call_is_not_completion(self):
        assessed = assess_exploration_completion(scenario(), snapshot(calls=0))
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')

    def test_sidebar_menu_does_not_confirm_the_target_page_step(self):
        landing = snapshot(observed='首页')
        payload = landing.model_dump(mode='json')
        payload['elements'] = [{
            'page_name': '首页', 'visible_name': '用户列表',
            'candidate_locators': ['page.get_by_role("link", name="用户列表")'],
        }]
        payload['step_evidence']['S1']['element_names'] = ['用户列表']
        assessed = assess_exploration_completion(scenario(), ExplorationSnapshot.model_validate(payload))
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')

    def test_dom_shaped_business_question_stays_in_read_only_exploration(self):
        question = '用户列表按钮的 locator 需要确认。'
        raw = snapshot(questions=[question]).model_dump(mode='json')
        raw['completion']['missing_targets'] = [{
            'target': question, 'kind': 'business_decision', 'user_question': question,
        }]
        assessed = assess_exploration_completion(scenario(ambiguities=[question]), ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')
        self.assertFalse(can_request_user_decision(assessed))

    def test_navigation_controls_need_not_repeat_destination_page_name(self):
        raw = snapshot().model_dump(mode='json')
        raw['elements'][0]['visible_name'] = '用户名搜索框'
        raw['step_evidence']['S1']['element_names'] = ['用户名搜索框']
        assessed = assess_exploration_completion(scenario(), ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'complete')

    def test_action_target_need_not_equal_page_name(self):
        spec = scenario()
        spec = spec.model_copy(update={'steps': [spec.steps[0].model_copy(update={
            'intent': 'read', 'target_hint': '新增用户按钮',
        })]})
        raw = snapshot().model_dump(mode='json')
        raw['elements'][0]['visible_name'] = '新增用户'
        raw['step_evidence']['S1']['element_names'] = ['新增用户']
        assessed = assess_exploration_completion(spec, ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'complete')

    def test_only_failed_calls_are_not_page_evidence(self):
        raw = snapshot().model_dump(mode='json')
        raw['tool_stats']['failed_tool_calls'] = 1
        assessed = assess_exploration_completion(scenario(), ExplorationSnapshot.model_validate(raw))
        self.assertNotEqual(assessed.completion.status, 'complete')

    def test_legacy_dom_question_cannot_leak_through_ambiguities(self):
        question = '用户列表的 DOM 定位器是什么？'
        assessed = assess_exploration_completion(scenario(ambiguities=[question]), snapshot(questions=[question]))
        self.assertFalse(can_request_user_decision(assessed))
        self.assertNotIn(question, assessed.completion.user_questions)

    def test_crud_step_without_observed_submission_requires_targeted_exploration(self):
        crud = scenario()
        crud = crud.model_copy(update={'steps': [crud.steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [
            {'id': 'C1', 'name': '清理', 'target_hint': '用户列表', 'condition': '测试数据存在时删除', 'step_id': 'S1'},
        ]})
        assessed = assess_exploration_completion(crud, snapshot())
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')
        self.assertTrue(any('CRUD 目标尚未获得提交后' in item.reason for item in assessed.completion.missing_targets))

    def test_unknown_submission_blocks_without_a_retry(self):
        crud = scenario().model_copy(update={'steps': [scenario().steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [{
            'id': 'C1', 'name': '清理', 'target_hint': '用户列表',
            'condition': '测试数据存在时删除', 'step_id': 'S1',
        }]})
        raw = snapshot().model_dump(mode='json')
        raw['exploration_actions'] = [{
            'step_id': 'S1', 'operation': 'create', 'scope': 'namespace',
            'status': 'unknown', 'target': '本轮测试用户', 'evidence_path': '/',
        }]
        assessed = assess_exploration_completion(crud, ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'blocked')
        self.assertTrue(any('提交结果未知' in item.reason for item in assessed.completion.missing_targets))

    def test_cleanup_unknown_before_attempt_blocks_and_warns(self):
        crud = scenario().model_copy(update={'steps': [scenario().steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [{
            'id': 'C1', 'name': '清理', 'target_hint': '用户列表',
            'condition': '测试数据存在时删除', 'step_id': 'S1',
        }]})
        raw = snapshot().model_dump(mode='json')
        raw['exploration_actions'] = [{
            'step_id': 'S1', 'operation': 'create', 'scope': 'namespace',
            'status': 'observed', 'target': '本轮测试用户', 'evidence_path': '/',
        }]
        raw['cleanup_report'] = {'status': 'unknown', 'attempted': False}
        assessed = assess_exploration_completion(crud, ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'blocked')
        self.assertIn('本轮 cleanup 状态未知；不得将其视为已完成。', assessed.warnings)

    def test_observed_write_without_cleanup_report_requires_cleanup(self):
        crud = scenario().model_copy(update={'steps': [scenario().steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [{
            'id': 'C1', 'name': '清理', 'target_hint': '用户列表',
            'condition': '测试数据存在时删除', 'step_id': 'S1',
        }]})
        raw = snapshot().model_dump(mode='json')
        raw['exploration_actions'] = [{
            'step_id': 'S1', 'operation': 'create', 'scope': 'namespace',
            'status': 'observed', 'target': '本轮测试用户', 'evidence_path': '/',
        }]
        assessed = assess_exploration_completion(crud, ExplorationSnapshot.model_validate(raw))
        self.assertEqual(assessed.completion.status, 'needs_targeted_exploration')
        self.assertEqual(assessed.cleanup_report.status, 'not_attempted')

    def test_explicit_read_only_policy_completes_ui_evidence_without_submit(self):
        crud = scenario().model_copy(update={'steps': [scenario().steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [{
            'id': 'C1', 'name': '清理', 'target_hint': '用户列表',
            'condition': '测试数据存在时删除', 'step_id': 'S1',
        }]})
        policy = ExplorationPolicy.for_scenario(
            crud, generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f', user_constraints='不要提交表单。',
        )
        raw = snapshot().model_dump(mode='json')
        raw.update({
            'exploration_policy_applied': True,
            'exploration_allowed_operations': [],
            'exploration_explicit_read_only': True,
        })
        assessed = assess_exploration_completion(crud, ExplorationSnapshot.model_validate(raw), policy=policy)
        self.assertEqual(assessed.completion.status, 'complete')
        self.assertIn('仅确认 UI 与定位证据', assessed.warnings[-1])
        self.assertEqual(
            assess_exploration_completion(crud, assessed).completion.status,
            'complete',
        )

    def test_merge_preserves_ledger_and_applies_successful_cleanup_delta(self):
        crud = scenario().model_copy(update={'steps': [scenario().steps[0].model_copy(update={
            'intent': 'create', 'mutates_data': True,
        })], 'cleanup': [{
            'id': 'C1', 'name': '清理', 'target_hint': '用户列表',
            'condition': '测试数据存在时删除', 'step_id': 'S1',
        }]})
        first = snapshot().model_dump(mode='json')
        first.update({
            'exploration_namespace': 'aits-explore-test-attempt',
            'exploration_policy_applied': True,
            'exploration_allowed_operations': ['create', 'delete'],
            'exploration_actions': [{
                'step_id': 'S1', 'operation': 'create', 'scope': 'namespace',
                'status': 'observed', 'target': '本轮测试用户', 'evidence_path': '/',
            }],
            'cleanup_report': {'status': 'not_attempted', 'attempted': False},
        })
        second = snapshot().model_dump(mode='json')
        second.update({
            'exploration_namespace': 'aits-explore-test-attempt',
            'exploration_policy_applied': True,
            'exploration_allowed_operations': ['create', 'delete'],
            'exploration_actions': [{
                'step_id': 'S1', 'operation': 'delete', 'scope': 'namespace',
                'status': 'observed', 'target': '本轮测试用户', 'evidence_path': '/',
            }],
            'cleanup_report': {'status': 'cleaned', 'attempted': True},
            'tool_stats': {'total_tool_calls': 1, 'potential_write_tool_calls': 1, 'blocked_write_tool_calls': 0},
        })
        merged = merge_exploration_snapshots(
            ExplorationSnapshot.model_validate(first), ExplorationSnapshot.model_validate(second),
            scenario=crud, target_step_ids={'S1'},
        )
        self.assertEqual([action.operation for action in merged.exploration_actions], ['create', 'delete'])
        self.assertEqual(merged.cleanup_report.status, 'cleaned')
        self.assertEqual(merged.tool_stats.potential_write_tool_calls, 1)

    def test_supplement_failure_keeps_primary_evidence_and_marks_cleanup_unknown(self):
        explorer = MCPPageExplorer(
            llm_model=AsyncMock(), mcp_config={'mcpServers': {}},
            generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
        )
        primary = snapshot(unresolved=True)
        failed_snapshot = ExplorationSnapshot.model_validate({
            'start_url_path': '/', 'tool_stats': {
                'total_tool_calls': 2, 'potential_write_tool_calls': 1,
                'termination_reason': 'mcp_error',
            },
        })
        client = AsyncMock()
        failure = MCPPageExplorerError('mcp_error', '补充失败', snapshot=failed_snapshot)
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch.object(
            explorer, '_explore_with_prompt', side_effect=[primary, failure],
        ):
            with self.assertRaises(MCPPageExplorerError) as raised:
                asyncio.run(explorer.explore_until_complete(
                    scenario=scenario(), start_path='/', target_url_safe='https://example.invalid/',
                ))
        preserved = raised.exception.snapshot
        self.assertEqual(preserved.page_states[0].name, '用户列表')
        self.assertTrue(preserved.exploration_namespace.startswith('aits-explore-'))
        self.assertEqual(preserved.cleanup_report.status, 'unknown')
        self.assertFalse(preserved.cleanup_report.attempted)

    def test_failure_delta_does_not_overwrite_prior_cleanup_without_new_write(self):
        explorer = MCPPageExplorer(
            llm_model=AsyncMock(), mcp_config={'mcpServers': {}},
            generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
        )
        prior_raw = snapshot().model_dump(mode='json')
        prior_raw.update({
            'cleanup_report': {'status': 'cleaned', 'attempted': True},
            'tool_stats': {'total_tool_calls': 2, 'potential_write_tool_calls': 1},
        })
        prior = ExplorationSnapshot.model_validate(prior_raw)
        failed = ExplorationSnapshot.model_validate({
            'start_url_path': '/',
            'tool_stats': {'total_tool_calls': 3, 'potential_write_tool_calls': 1},
        })
        preserved = explorer._failure_snapshot_with_prior_evidence(prior, failed)
        self.assertEqual(preserved.cleanup_report.status, 'cleaned')
        self.assertTrue(preserved.cleanup_report.attempted)

    def test_supplement_failure_keeps_known_residuals_and_handles_missing_snapshot(self):
        explorer = MCPPageExplorer(llm_model=AsyncMock(), mcp_config={'mcpServers': {}})
        prior = snapshot().model_copy(update={
            'cleanup_report': ExplorationSnapshot.model_validate({
                'start_url_path': '/', 'tool_stats': {'total_tool_calls': 0},
                'cleanup_report': {'status': 'residual', 'attempted': True, 'residuals': ['test-user-1']},
            }).cleanup_report,
        })
        failed = ExplorationSnapshot.model_validate({
            'start_url_path': '/', 'tool_stats': {'total_tool_calls': 2, 'potential_write_tool_calls': 1},
        })
        preserved = explorer._failure_snapshot_with_prior_evidence(prior, failed)
        self.assertEqual(preserved.cleanup_report.status, 'unknown')
        self.assertEqual(preserved.cleanup_report.residuals, ['test-user-1'])
        self.assertTrue(preserved.cleanup_report.attempted)
        self.assertEqual(explorer._failure_snapshot_with_prior_evidence(prior, None).cleanup_report.status, 'residual')

    def test_new_supplement_writes_cannot_reuse_previous_cleanup_success(self):
        first = snapshot().model_dump(mode='json')
        first['cleanup_report'] = {'status': 'cleaned', 'attempted': True}
        extra = snapshot().model_dump(mode='json')
        extra['tool_stats']['potential_write_tool_calls'] = 1
        merged = merge_exploration_snapshots(
            ExplorationSnapshot.model_validate(first), ExplorationSnapshot.model_validate(extra),
            scenario=scenario(), target_step_ids={'S1'},
        )
        self.assertEqual(merged.cleanup_report.status, 'unknown')


class ExplorerContinuationTests(SimpleTestCase):
    def explorer(self, *, cancel_check=None):
        return MCPPageExplorer(
            llm_model=object(), mcp_config={'mcpServers': {}}, cancel_check=cancel_check,
            generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
        )

    def test_directed_supplement_reuses_the_primary_mcp_session(self):
        explorer = self.explorer()
        client = AsyncMock()
        first = snapshot(observed='登录', unresolved=False)
        second = snapshot(observed='用户列表', unresolved=False)
        seen_clients = []

        async def run_prompt(*args, **kwargs):
            seen_clients.append(kwargs['client'])
            return first if len(seen_clients) == 1 else second

        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch.object(
            explorer, '_explore_with_prompt', side_effect=run_prompt,
        ):
            result = asyncio.run(explorer.explore_until_complete(
                scenario=scenario(), start_path='/', target_url_safe='https://example.invalid/',
            ))
        self.assertEqual(seen_clients, [client, client])
        self.assertEqual(result.completion.targeted_rounds, 1)
        self.assertEqual(result.completion.status, 'complete')
        self.assertFalse(result.completion.budget_exhausted)
        self.assertTrue(result.completion.supplement_round_limit_reached)
        client.create_all_sessions.assert_awaited_once()
        client.close_all_sessions.assert_awaited_once()

    def test_discovery_only_gap_reuses_all_steps_for_the_directed_round(self):
        explorer = self.explorer()
        client = AsyncMock()
        initial = snapshot()
        completed = snapshot()
        payload = completed.model_dump(mode='json')
        payload['page_states'][0]['key_regions'].append('新增用户表单字段')
        payload['elements'].append({
            'page_name': '用户列表', 'visible_name': '新增用户表单字段',
            'candidate_locators': ['page.get_by_role("dialog")'],
        })
        completed = ExplorationSnapshot.model_validate(payload)
        prompts = []

        async def run_prompt(prompt, *args, **kwargs):
            prompts.append(__import__('json').loads(prompt))
            return initial if len(prompts) == 1 else completed

        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch.object(
            explorer, '_explore_with_prompt', side_effect=run_prompt,
        ):
            result = asyncio.run(explorer.explore_until_complete(
                scenario=scenario().model_copy(update={'discovery_targets': ['新增用户表单字段']}),
                start_path='/', target_url_safe='https://example.invalid/',
            ))
        self.assertEqual(prompts[1]['requested_step_ids'], ['S1'])
        self.assertEqual(prompts[1]['missing_targets'][0]['target'], '新增用户表单字段')
        self.assertEqual(prompts[1]['already_observed_pages'][0]['name'], '用户列表')
        self.assertEqual(result.completion.status, 'complete')

    def test_supplement_prompt_reuses_namespace_and_includes_ledger(self):
        explorer = self.explorer()
        raw = snapshot(unresolved=True).model_dump(mode='json')
        raw.update({
            'exploration_namespace': 'aits-explore-existing-attempt',
            'exploration_actions': [{
                'step_id': 'S1', 'operation': 'create', 'scope': 'namespace',
                'status': 'observed', 'target': '本轮测试用户', 'evidence_path': '/',
            }],
            'cleanup_report': {'status': 'not_attempted', 'attempted': False},
        })
        existing = ExplorationSnapshot.model_validate(raw)
        prompts = []

        async def run_prompt(prompt, *args, **kwargs):
            prompts.append(__import__('json').loads(prompt))
            return existing

        with patch.object(explorer, '_explore_with_prompt', side_effect=run_prompt):
            asyncio.run(explorer.explore_missing_evidence(
                scenario=scenario(), existing_snapshot=existing,
                start_path='/', target_url_safe='https://example.invalid/',
            ))
        prompt = prompts[0]
        self.assertEqual(prompt['scope_policy']['exploration_namespace'], 'aits-explore-existing-attempt')
        self.assertEqual(prompt['existing_exploration_namespace'], 'aits-explore-existing-attempt')
        self.assertEqual(prompt['existing_exploration_actions'][0]['operation'], 'create')
        self.assertEqual(prompt['existing_cleanup_report']['status'], 'not_attempted')

    def test_supplement_timeout_keeps_primary_snapshot(self):
        explorer = self.explorer()
        client = AsyncMock()
        primary = snapshot(unresolved=True)
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch.object(
            explorer, '_explore_with_prompt', side_effect=[primary, TimeoutError()],
        ):
            with self.assertRaises(MCPPageExplorerError) as raised:
                asyncio.run(explorer.explore_until_complete(
                    scenario=scenario(), start_path='/', target_url_safe='https://example.invalid/',
                ))
        self.assertEqual(raised.exception.error_code, 'exploration_timeout')
        self.assertEqual(raised.exception.snapshot.page_states[0].name, '用户列表')

    def test_total_tool_budget_prevents_a_second_exploration_round(self):
        explorer = self.explorer()
        client = AsyncMock()

        async def first_prompt(*args, **kwargs):
            explorer.guard.total_tool_calls = MCP_BROWSER_TOOL_CALL_LIMIT
            return snapshot(observed='登录')

        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch.object(
            explorer, '_explore_with_prompt', side_effect=first_prompt,
        ) as run_prompt:
            result = asyncio.run(explorer.explore_until_complete(
                scenario=scenario(), start_path='/', target_url_safe='https://example.invalid/',
            ))
        self.assertEqual(result.completion.status, 'blocked')
        self.assertTrue(result.completion.budget_exhausted)
        self.assertEqual(run_prompt.await_count, 1)

    def test_cancellation_is_terminal_and_carries_no_page_content(self):
        explorer = self.explorer(cancel_check=lambda: True)
        with self.assertRaises(MCPPageExplorerError) as raised:
            asyncio.run(explorer.explore_until_complete(
                scenario=scenario(), start_path='/', target_url_safe='https://example.invalid/',
            ))
        self.assertEqual(raised.exception.error_code, 'TASK_CANCELLED')
        self.assertEqual(raised.exception.snapshot.page_states, [])
        self.assertNotIn('example.invalid', raised.exception.snapshot.model_dump_json())

    def test_runtime_read_only_policy_blocks_evaluate_without_a_retry(self):
        guard = ReadOnlyMCPBrowserToolGuard()
        with self.assertRaises(Exception):
            guard.on_tool_start({'name': 'playwright_evaluate'}, '', inputs={})
        self.assertEqual(guard.terminal_error.error_kind, 'read_only_violation')
        self.assertEqual(guard.get_stats()['total_tool_calls'], 0)

    def test_model_schema_does_not_request_callback_owned_counters(self):
        prompt = self.explorer()._build_prompt(scenario(), '/', 'https://example.invalid/', None)
        schema = __import__('json').loads(prompt)['output_schema']
        self.assertNotIn('tool_stats', schema['properties'])
        self.assertNotIn('checkpoints', schema['properties'])

    def test_exploration_timeout_defaults_to_existing_llm_timeout_and_accepts_override(self):
        with patch.dict('os.environ', {'AITS_LLM_TIMEOUT_SECONDS': '750'}, clear=False):
            self.assertEqual(exploration_total_timeout_seconds(), 750.0)
        with patch.dict('os.environ', {
            'AITS_LLM_TIMEOUT_SECONDS': '750', 'WEBUI_EXPLORATION_TOTAL_TIMEOUT_SECONDS': '900',
        }, clear=False):
            self.assertEqual(exploration_total_timeout_seconds(), 900.0)
