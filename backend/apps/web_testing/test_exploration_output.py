"""Offline regressions for exploration output recovery; no real MCP or model calls."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage

from .exploration_output import ExplorationOutputError, parse_exploration_output, semantic_tokens
from .generation_contracts import ScenarioSpec
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError


def evidence():
    return {
        'start_url_path': '/model-path',
        'visited_paths': ['/users'],
        'page_states': [{'name': '用户列表', 'path': '/users'}],
        'elements': [{
            'page_name': '用户列表', 'role': 'button', 'visible_name': '添加',
            'candidate_locators': ['get_by_role("button", name="添加")'],
        }],
        'navigation_paths': [],
        'step_evidence': {'S1': {'status': 'confirmed', 'paths': ['/users']}},
        'unresolved_steps': [], 'unresolved_questions': [], 'warnings': [],
        'tool_stats': {'total_tool_calls': 999, 'failed_tool_calls': 99, 'duration_seconds': 1},
    }


def scenario():
    return ScenarioSpec.model_validate({
        'title': '查询用户', 'objective': '检查用户列表',
        'steps': [{'id': 'S1', 'name': '查看用户', 'intent': 'read', 'target_hint': '用户列表', 'expected': '列表可见'}],
        'assertions': [{'id': 'A1', 'name': '列表可见', 'target_hint': '用户列表', 'expected': '可见', 'step_id': 'S1'}],
    })


def encoded(payload=None):
    return json.dumps(evidence() if payload is None else payload, ensure_ascii=False)


def trailing_comma(payload=None):
    return encoded(payload)[:-1] + ',}'


class OutputParserTests(SimpleTestCase):
    def test_single_objects_and_wrappers_preserve_nested_strings(self):
        payload = evidence()
        payload['warnings'] = ['字符 { [ ] } 和反斜杠 \\ 与引号 " 不是边界']
        raw = encoded(payload)
        for value in (
            raw, '\ufeff' + raw, f'```json\n{raw}\n```', f'```JSON\n{raw}\n```',
            f'```\n{raw}\n```', f'探索完成，证据如下：\n{raw}\n以上为探索结果。',
            f'探索完成。\n```JsOn\n{raw}\n```\n以上为探索结果。',
            AIMessage(content=raw), AIMessage(content=[{'type': 'text', 'text': raw[:40]}, {'type': 'text', 'text': raw[40:]}]),
        ):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(parse_exploration_output(value), payload)

    def test_english_contraction_wrapper_preserves_json_content(self):
        payload = evidence()
        payload['warnings'] = ["Here's a string with 'quoted' content."]
        self.assertEqual(parse_exploration_output("Here's the JSON:\n" + encoded(payload)), payload)

    def test_single_quoted_outer_key_is_not_a_prose_wrapper(self):
        with self.assertRaises(ExplorationOutputError) as raised:
            parse_exploration_output("'outer': " + encoded())
        self.assertEqual(raised.exception.kind, 'invalid_wrapper')
        self.assertIsNone(raised.exception.repair_payload)

    def test_repairable_separator_errors_keep_exact_tokens_and_payload(self):
        raw = encoded()
        for malformed in (
            trailing_comma(), raw.replace('"visited_paths":', '"visited_paths"', 1),
            raw.replace(', "visited_paths"', ' "visited_paths"', 1),
        ):
            with self.subTest(malformed=malformed[:30]):
                with self.assertRaises(ExplorationOutputError) as raised:
                    parse_exploration_output(malformed)
                self.assertEqual(raised.exception.repair_payload, evidence())
                self.assertEqual(semantic_tokens(encoded(raised.exception.repair_payload)), semantic_tokens(raw))
                self.assertIsInstance(raised.exception.offset, int)

    def test_no_inner_object_salvage_or_ambiguous_top_level(self):
        raw = encoded()
        for malformed in (
            f'[{raw}]', f'{raw}\n{raw}', f'{{"outer": {raw}',
            f'{{broken: {raw}}}', f'"outer": {raw}', f'{{"outer": {raw},',
            f'{raw}]', f'前言\n[{raw}]', f'{raw}\n```json\n{raw}\n```',
        ):
            with self.subTest(malformed=malformed[:20]):
                with self.assertRaises(ExplorationOutputError) as raised:
                    parse_exploration_output(malformed)
                self.assertIsNone(raised.exception.repair_payload)

    def test_unsafe_lexemes_duplicate_keys_and_missing_values_cannot_repair(self):
        for raw in (
            '{"x": "unterminated}', '{"x": "\\q"}', '{"x": }',
            '{"x": 01}', '{"x": truefalse}', '{"x": NaN}',
            '{"x": 1, "x": 2}', '{"x": 1 "x": 2}', "{'x': 'y'}",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ExplorationOutputError) as raised:
                    parse_exploration_output(raw)
                self.assertIsNone(raised.exception.repair_payload)

    def test_scalar_and_container_tokens_are_not_interchangeable(self):
        tokens = semantic_tokens('{"x": [1, true, false, null, "s"]}')
        for changed in (
            '{"x": [2, true, false, null, "s"]}', '{"x": [1, false, true, null, "s"]}',
            '{"x": [1, true, false, "null", "s"]}', '{"x": {"1": [true, false, null, "s"]}}',
        ):
            self.assertNotEqual(tokens, semantic_tokens(changed))


class ExplorerRecoveryTests(SimpleTestCase):
    def make_explorer(self, llm=None, cancel_check=None):
        return MCPPageExplorer(
            llm_model=llm or AsyncMock(), mcp_config={'mcpServers': {}}, cancel_check=cancel_check,
            generation_id='52ae9c6a-50a7-424d-9373-423750c6fd9f',
        )

    def fake_runtime(self, raw_output):
        client = AsyncMock()
        agent = AsyncMock()

        def factory(**kwargs):
            guard = kwargs['callbacks'][0]

            async def run(_prompt):
                guard.on_tool_start({'name': 'playwright_click'}, '', run_id='failed', inputs={'selector': '.test'})
                guard.on_tool_end({'isError': True}, run_id='failed', name='playwright_click')
                guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='read')
                guard.on_tool_end('read succeeded', run_id='read', name='playwright_get_visible_html')
                return raw_output

            agent.run.side_effect = run
            return agent

        return client, agent, patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch(
            'web_testing.mcp_page_explorer.MCPAgent', side_effect=factory,
        )

    async def explore(self, explorer, credentials=None):
        return await explorer.explore(
            scenario=scenario(), start_path='/local', target_url_safe='https://example.invalid/',
            temporary_credentials=credentials,
        )

    def test_standard_outputs_never_call_repair_and_override_model_stats(self):
        raw = encoded()
        for output in (raw, '\ufeff' + raw, f'```JSON\n{raw}\n```', f'```\n{raw}\n```', f'前言\n{raw}\n后记'):
            with self.subTest(output=output[:10]):
                explorer = self.make_explorer()
                client, agent, client_patch, agent_patch = self.fake_runtime(output)
                with client_patch, agent_patch:
                    snapshot = asyncio.run(self.explore(explorer))
                explorer.llm_model.ainvoke.assert_not_called()
                agent.run.assert_awaited_once()
                client.create_all_sessions.assert_awaited_once()
                client.close_all_sessions.assert_awaited_once()
                self.assertEqual(snapshot.start_url_path, '/local')
                self.assertEqual(snapshot.tool_stats.total_tool_calls, 2)
                self.assertEqual(snapshot.tool_stats.failed_tool_calls, 1)

    def test_separator_repair_is_local_and_preserves_the_normal_snapshot(self):
        raw = encoded()
        for malformed in (trailing_comma(), raw.replace('"visited_paths":', '"visited_paths"', 1), raw.replace(', "visited_paths"', ' "visited_paths"', 1)):
            with self.subTest(malformed=malformed[:20]):
                llm = AsyncMock()
                llm.ainvoke.side_effect = AssertionError('local recovery must never call a model')
                explorer = self.make_explorer(llm)
                client, agent, client_patch, agent_patch = self.fake_runtime(malformed)
                with client_patch, agent_patch:
                    snapshot = asyncio.run(self.explore(explorer, {'username': 'login-only-user', 'password': 'login-only-secret'}))
                llm.ainvoke.assert_not_called()
                agent.run.assert_awaited_once()
                client.create_all_sessions.assert_awaited_once()
                client.close_all_sessions.assert_awaited_once()
                self.assertEqual(snapshot.tool_stats.total_tool_calls, 2)
                self.assertEqual(snapshot.tool_stats.failed_tool_calls, 1)
                self.assertEqual(snapshot.elements[0].candidate_locators, evidence()['elements'][0]['candidate_locators'])
                normal = explorer._parse_snapshot(raw, '/local', snapshot.tool_stats.duration_seconds)
                self.assertEqual(snapshot, normal)

    def test_empty_array_script_truncation_and_no_evidence_never_call_repair(self):
        raw = encoded()
        for output in ('', None, 'No output generated', '探索完成但是没有证据', '[]', f'[{raw}]', f'{raw}{raw}', raw[:-1], 'async def run(page):\n    pass', f'```python\nresult = {raw}\n```', '{"warnings": ["未看到页面"],}'):
            with self.subTest(output=str(output)[:25]):
                explorer = self.make_explorer()
                client, agent, client_patch, agent_patch = self.fake_runtime(output)
                with client_patch, agent_patch:
                    with self.assertRaises(MCPPageExplorerError):
                        asyncio.run(self.explore(explorer))
                explorer.llm_model.ainvoke.assert_not_called()
                agent.run.assert_awaited_once()
                client.create_all_sessions.assert_awaited_once()
                client.close_all_sessions.assert_awaited_once()

    def test_schema_errors_are_not_offered_to_model_even_with_bad_punctuation(self):
        missing = evidence()
        del missing['page_states'][0]['path']
        absolute_url = evidence()
        absolute_url['visited_paths'] = ['https://example.invalid/users']
        sensitive = evidence()
        sensitive['warnings'] = ['password=do-not-forward-secret']
        for payload in (missing, absolute_url, sensitive):
            for output in (encoded(payload), trailing_comma(payload)):
                with self.subTest(output=output[:20]):
                    explorer = self.make_explorer()
                    with self.assertRaises(MCPPageExplorerError) as raised:
                        asyncio.run(explorer._parse_snapshot_with_repair(output, '/', 0))
                    self.assertEqual(raised.exception.error_code, 'EVIDENCE_INSUFFICIENT')
                    self.assertIn('结构', str(raised.exception))
                    explorer.llm_model.ainvoke.assert_not_called()

    def test_model_metadata_is_overridden_without_forwarding_credentials(self):
        payload = evidence()
        payload['tool_stats']['temporary_value'] = 'login-secret-literal'
        explorer = self.make_explorer()
        snapshot = asyncio.run(explorer._parse_snapshot_with_repair(trailing_comma(payload), '/', 0))
        self.assertNotIn('login-secret-literal', snapshot.model_dump_json())
        explorer.llm_model.ainvoke.assert_not_called()

    def test_admin_username_does_not_block_path_or_locator_substrings(self):
        payload = evidence()
        payload['visited_paths'] = ['/ums/admin']
        payload['elements'][0]['candidate_locators'] = ['locator("a[href=\\\"#/ums/admin\\\"]")']
        llm = AsyncMock()
        explorer = self.make_explorer(llm)
        client, agent, client_patch, agent_patch = self.fake_runtime(trailing_comma(payload))
        with client_patch, agent_patch:
            snapshot = asyncio.run(self.explore(explorer, {'username': 'admin', 'password': 'private-password'}))
        llm.ainvoke.assert_not_called()
        self.assertEqual(snapshot.visited_paths, ['/ums/admin'])
        self.assertNotIn('private-password', str(llm.ainvoke.call_args))

    def test_local_reconstruction_preserves_all_scalar_values_and_container_order(self):
        payload = {'facts': [1, -2, 3.5, True, False, None, {'path': '/users', 'label': '添加'}]}
        with self.assertRaises(ExplorationOutputError) as raised:
            parse_exploration_output(encoded(payload)[:-1] + ',}')
        self.assertEqual(raised.exception.repair_payload, payload)
        self.assertEqual(list(raised.exception.repair_payload['facts'][-1]), ['path', 'label'])

    def test_cancellation_before_and_after_local_recovery_closes_session(self):
        for after_validation in (False, True):
            with self.subTest(after_validation=after_validation):
                self._assert_interruption(guard=False, after_validation=after_validation)

    def test_guard_wins_over_cancel_after_local_recovery(self):
        self._assert_interruption(guard=True, after_validation=True)

    def _assert_interruption(self, *, guard, after_validation):
        cancelled = {'value': False}
        explorer = self.make_explorer(cancel_check=lambda: cancelled['value'])

        def interrupt():
            if guard:
                try:
                    explorer.guard.on_tool_start({'name': 'playwright_evaluate'}, '', inputs={})
                except Exception:
                    pass
            cancelled['value'] = True

        def parse_then_cancel(raw):
            try:
                return parse_exploration_output(raw)
            finally:
                interrupt()

        validate = explorer._snapshot_from_payload

        def validate_then_cancel(*args):
            snapshot = validate(*args)
            interrupt()
            return snapshot

        cancellation_patch = (
            patch.object(explorer, '_snapshot_from_payload', side_effect=validate_then_cancel)
            if after_validation else patch('web_testing.mcp_page_explorer.parse_exploration_output', side_effect=parse_then_cancel)
        )
        client, agent, client_patch, agent_patch = self.fake_runtime(trailing_comma())
        with client_patch, agent_patch, cancellation_patch:
            with self.assertRaises(MCPPageExplorerError) as raised:
                asyncio.run(self.explore(explorer))
        self.assertEqual(raised.exception.error_code, 'read_only_violation' if guard else 'TASK_CANCELLED')
        self.assertEqual(raised.exception.tool_stats['total_tool_calls'], 2)
        self.assertEqual(raised.exception.tool_stats['failed_tool_calls'], 1)
        client.create_all_sessions.assert_awaited_once()
        client.close_all_sessions.assert_awaited_once()
        agent.run.assert_awaited_once()
        explorer.llm_model.ainvoke.assert_not_called()

    def test_guard_prevents_any_repair(self):
        explorer = self.make_explorer()
        try:
            explorer.guard.on_tool_start({'name': 'playwright_evaluate'}, '', inputs={})
        except Exception:
            pass
        with self.assertRaises(MCPPageExplorerError) as raised:
            asyncio.run(explorer._parse_snapshot_with_repair(trailing_comma(), '/', 0))
        self.assertEqual(raised.exception.error_code, 'read_only_violation')
        explorer.llm_model.ainvoke.assert_not_called()

    def test_logs_only_safe_summary_and_report_both_phases(self):
        llm = AsyncMock()
        explorer = self.make_explorer(llm)
        with patch('web_testing.mcp_page_explorer.logger') as logger:
            asyncio.run(explorer._parse_snapshot_with_repair(trailing_comma(), '/', 0))
            with self.assertRaises(MCPPageExplorerError):
                explorer._parse_snapshot('password=secret-value\nnot-json', '/', 0)
        calls = str(logger.mock_calls)
        self.assertIn('original', calls)
        self.assertIn('repaired', calls)
        self.assertIn('52ae9c6a-50a7-424d-9373-423750c6fd9f', calls)
        for forbidden in ('secret-value', 'password=', 'visited_paths', 'candidate_locators', '/model-path', 'get_by_role'):
            self.assertNotIn(forbidden, calls)
        llm.ainvoke.assert_not_called()

    def test_sync_interface_recovers_locally_and_messages_distinguish_failures(self):
        explorer = self.make_explorer()
        self.assertEqual(explorer._parse_snapshot(trailing_comma(), '/', 0), explorer._parse_snapshot(encoded(), '/', 0))
        messages = []
        for raw in ('No output generated', '{invalid}', '[]'):
            with self.assertRaises(MCPPageExplorerError) as raised:
                explorer._parse_snapshot(raw, '/', 0)
            messages.append(str(raised.exception))
        self.assertEqual(len(set(messages)), 3)
        explorer.llm_model.ainvoke.assert_not_called()

    def test_prompt_includes_actual_nested_schema_in_both_modes(self):
        explorer = self.make_explorer()
        prompts = [
            explorer._build_prompt(scenario(), '/', 'https://example.invalid/', None),
            explorer._build_supplemental_prompt(scenario(), '/', 'https://example.invalid/', ['S1'], None),
        ]
        for prompt in prompts:
            schema = json.loads(prompt)['output_schema']
            self.assertIn('PageState', schema['$defs'])
            self.assertIn('path', schema['$defs']['PageState']['required'])
            self.assertFalse(schema['additionalProperties'])
