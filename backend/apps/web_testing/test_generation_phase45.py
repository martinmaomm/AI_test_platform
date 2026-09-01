"""Mock-only contract tests for V2 script generation, quality and save stages."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from langchain_core.messages import AIMessage
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType
from projects.models import Environment, Project

from .generation_contracts import ExplorationSnapshot, ScenarioSpec
from .generation_orchestrator import _model_failure, run_v2_generation
from .generation_events import publish_terminal
from .generation_save_state import generation_reference
from .models import WebUIScriptGeneration, WebUITestCase
from .playwright_python_runner import ExecutionConfig, PlaywrightRunner
from .script_extraction import extract_playwright_metadata
from .script_generator import SCRIPT_GENERATION_MAX_TOKENS, ScriptGenerator, ScriptGeneratorOutputError
from .script_quality import evaluate_script
from .serializers import WebUIScriptGenerationSerializer
from .views import WebUIScriptGenerationSaveView


def scenario_payload(**overrides):
    payload = {
        'title': '查询用户列表', 'objective': '验证用户列表可见。', 'preconditions': [],
        'steps': [{'id': 'S1', 'name': '进入用户列表', 'intent': 'navigate', 'target_hint': '用户列表', 'input_refs': [], 'mutates_data': False, 'expected': '页面显示。'}],
        'assertions': [{'id': 'A1', 'name': '列表可见', 'target_hint': '用户列表', 'expected': '列表显示', 'step_id': 'S1'}],
        'cleanup': [], 'forbidden_actions': [], 'credentials_required': False, 'ambiguities': [], 'risk_level': 'low',
    }
    payload.update(overrides)
    return payload


def snapshot_payload(*, unresolved=False):
    return {
        'start_url_path': '/', 'visited_paths': ['/'],
        'page_states': [{'name': '用户列表', 'title': '用户列表', 'path': '/', 'key_regions': ['用户列表']}],
        'elements': [{'page_name': '用户列表', 'role': 'heading', 'visible_name': '用户列表', 'stable_attributes': {}, 'candidate_locators': ['page.get_by_role("heading")']}],
        'navigation_paths': [],
        'step_evidence': {'S1': {'status': 'unresolved' if unresolved else 'confirmed', 'paths': ['/'], 'element_names': ['用户列表'], 'reason': '待确认' if unresolved else '已确认'}},
        'unresolved_steps': ['S1'] if unresolved else [], 'warnings': [],
        'tool_stats': {'total_tool_calls': 1, 'tool_counts': {'playwright_navigate': 1}, 'failed_tool_calls': 0, 'termination_reason': None, 'duration_seconds': 0.1},
    }


VALID_SCRIPT = '''"""场景：查询用户列表。目标：验证列表可见。前置条件：已登录。清理策略：无写操作，无需清理。"""
from playwright.async_api import expect

async def run(page):
    # 步骤 1：进入用户列表
    await page.goto('/users')
    # 断言 1：确认用户列表可见
    await expect(page.get_by_role('heading')).to_be_visible()
'''


class Phase45Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='phase45-user', password='test-password')
        self.project = Project.objects.create(name='Phase45 project', project_type='web', owner=self.user, created_by=self.user)
        self.environment = Environment.objects.create(
            project=self.project, name='WebUI', category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test', 'variables': {'UI_TEST_USERNAME': 'admin', 'UI_TEST_PASSWORD': 'secret'}},
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', api_key='key', base_url='https://llm.example.test', model_name='model', is_active=True, created_by=self.user,
        )
        MCPConfiguration.objects.create(
            name='playwright', created_by=self.user, is_active=True,
            raw_config='{"mcpServers":{"playwright":{"command":"npx"}}}',
        )
        self.factory = APIRequestFactory()

    def generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user, 'environment': self.environment,
            'description_safe': '查询用户列表。', 'target_url_safe': 'https://web.example.test/',
            'model_info': {
                'config_id': self.model.id, 'provider': 'openai',
                'provider_name': 'OpenAI 企业网关', 'model_name': 'model',
            },
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)


class ScriptGeneratorAndQualityTests(Phase45Base):
    def test_generator_prompt_is_evidence_only_and_quality_requires_docstring_and_comments(self):
        llm = Mock()
        llm.stream = None
        llm.invoke.return_value = VALID_SCRIPT
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        script = ScriptGenerator(llm).generate(scenario=scenario, snapshot=snapshot)
        self.assertEqual(script, VALID_SCRIPT.strip())
        prompt = str(llm.invoke.call_args)
        self.assertIn('不得调用 MCP', prompt)
        self.assertNotIn('secret', prompt)
        report = evaluate_script(script, scenario=scenario, snapshot=snapshot)
        self.assertFalse(report['blockers'])
        invalid = 'async def run(page):\n    await page.goto("/")\n'
        invalid_report = evaluate_script(invalid, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'DOCSTRING_MISSING' for item in invalid_report['blockers']))
        self.assertTrue(any(item['code'] == 'ACTION_COMMENT_MISSING' for item in invalid_report['blockers']))

    def test_generator_extracts_langchain_message_content_for_generate_and_repair(self):
        llm = Mock()
        llm.stream = None
        llm.invoke.side_effect = [
            AIMessage(content=VALID_SCRIPT),
            AIMessage(content=[{'type': 'text', 'text': f'```python\n{VALID_SCRIPT}\n```'}]),
        ]
        generator = ScriptGenerator(llm)
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        self.assertEqual(generator.generate(scenario=scenario, snapshot=snapshot), VALID_SCRIPT.strip())
        self.assertEqual(
            generator.repair(script='bad', issues=[], scenario=scenario, snapshot=snapshot),
            VALID_SCRIPT.strip(),
        )

    def test_generator_and_repair_keep_validated_dom_author_attributes(self):
        llm = Mock(return_value=VALID_SCRIPT)
        llm.stream = None
        llm.invoke.return_value = VALID_SCRIPT
        data = snapshot_payload()
        data['elements'][0]['stable_attributes'] = {'data-author': 'ui-library'}
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(data)
        generator = ScriptGenerator(llm)
        generator.generate(scenario=scenario, snapshot=snapshot)
        generator.repair(script='bad', issues=[], scenario=scenario, snapshot=snapshot)
        for call in llm.invoke.call_args_list:
            prompt = call.args[0][1].content
            self.assertIn('exploration_trace', json.loads(prompt))

    def test_generator_uses_global_streaming_invoke_and_sets_a_local_output_limit(self):
        class StreamingInvokeLLM:
            def __init__(self):
                self.invoke = Mock(return_value=AIMessage(content=VALID_SCRIPT))

        llm = StreamingInvokeLLM()
        scenario = ScenarioSpec.model_validate(scenario_payload())
        script = ScriptGenerator(llm).generate(
            scenario=scenario,
            snapshot=ExplorationSnapshot.model_validate(snapshot_payload()),
        )
        repaired = ScriptGenerator(llm).repair(
            script='bad', issues=[], scenario=scenario,
            snapshot=ExplorationSnapshot.model_validate(snapshot_payload()),
        )

        self.assertEqual(script, VALID_SCRIPT.strip())
        self.assertEqual(repaired, VALID_SCRIPT.strip())
        self.assertEqual(
            [call.kwargs['max_tokens'] for call in llm.invoke.call_args_list],
            [SCRIPT_GENERATION_MAX_TOKENS, SCRIPT_GENERATION_MAX_TOKENS],
        )
        self.assertEqual(SCRIPT_GENERATION_MAX_TOKENS, 8192)

    def test_generator_uses_invoke_when_stream_is_unavailable(self):
        llm = SimpleNamespace(invoke=Mock(return_value=AIMessage(content=VALID_SCRIPT)))
        scenario = ScenarioSpec.model_validate(scenario_payload())

        self.assertEqual(
            ScriptGenerator(llm).repair(
                script='bad', issues=[], scenario=scenario,
                snapshot=ExplorationSnapshot.model_validate(snapshot_payload()),
            ),
            VALID_SCRIPT.strip(),
        )
        llm.invoke.assert_called_once()
        self.assertEqual(llm.invoke.call_args.kwargs['max_tokens'], SCRIPT_GENERATION_MAX_TOKENS)

    def test_generator_does_not_retry_a_failed_invoke(self):
        class InvokeFailure(RuntimeError):
            status_code = 504

        class FailingInvokeLLM:
            def __init__(self):
                self.invoke = Mock(side_effect=InvokeFailure('provider stream interrupted'))

        llm = FailingInvokeLLM()
        with self.assertRaises(InvokeFailure) as raised:
            ScriptGenerator(llm).generate(
                scenario=ScenarioSpec.model_validate(scenario_payload()),
                snapshot=ExplorationSnapshot.model_validate(snapshot_payload()),
            )
        llm.invoke.assert_called_once()
        self.assertEqual(_model_failure(raised.exception)[0], 'MODEL_GATEWAY_TIMEOUT')

    def test_generator_rejects_an_empty_invoke_response(self):
        class EmptyInvokeLLM:
            def invoke(self, _messages, **_kwargs):
                return AIMessage(content=[])

        with self.assertRaises(ScriptGeneratorOutputError) as raised:
            ScriptGenerator(EmptyInvokeLLM()).generate(
                scenario=ScenarioSpec.model_validate(scenario_payload()),
                snapshot=ExplorationSnapshot.model_validate(snapshot_payload()),
            )
        self.assertEqual(_model_failure(raised.exception)[0], 'MODEL_OUTPUT_INVALID')

    def test_quality_blocks_missing_required_trace_coverage_but_keeps_draft_reviewable(self):
        report = evaluate_script(
            VALID_SCRIPT,
            scenario=ScenarioSpec.model_validate(scenario_payload()),
            snapshot=ExplorationSnapshot.model_validate(snapshot_payload(unresolved=True)),
        )
        self.assertTrue(any(item['code'] == 'TRACE_STEP_COVERAGE_MISSING' for item in report['blockers']))

    def test_quality_blocks_sensitive_literals_and_placeholders_but_allows_environment_references(self):
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        leaked = VALID_SCRIPT.replace("async def run(page):", "async def run(page):\n    password = 'hunter2'\n    token = 'abc'")
        report = evaluate_script(leaked, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'SENSITIVE_LITERAL' for item in report['blockers']))
        self.assertNotIn('hunter2', str(report))
        safe = VALID_SCRIPT.replace('async def run(page):', "import os\n\nasync def run(page):\n    password = os.environ['UI_TEST_PASSWORD']")
        safe_report = evaluate_script(safe, scenario=scenario, snapshot=snapshot)
        self.assertFalse(any(item['code'] == 'SENSITIVE_LITERAL' for item in safe_report['blockers']))
        placeholder = VALID_SCRIPT.replace("    await page.goto('/users')", "    pass\n    await page.goto('/users')")
        placeholder_report = evaluate_script(placeholder, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'UNRESOLVED_PLACEHOLDER' for item in placeholder_report['blockers']))
        comment_placeholder = VALID_SCRIPT.replace(
            '# 步骤 1：进入用户列表',
            '# TODO: 待确认定位器',
        )
        comment_report = evaluate_script(comment_placeholder, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'UNRESOLVED_PLACEHOLDER' for item in comment_report['blockers']))
        self.assertFalse(any(item['code'] == 'UNRESOLVED_PLACEHOLDER' for item in safe_report['blockers']))

    def test_quality_uses_the_strict_storage_contract_before_ready(self):
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        invalid_cases = [
            VALID_SCRIPT + "\nasync def main():\n    return None\n",
            VALID_SCRIPT.replace('async def run(page):', 'async def run(page=None):'),
            VALID_SCRIPT + "\nif __name__ == '__main__':\n    value = None\n",
        ]
        for invalid_script in invalid_cases:
            with self.subTest(invalid_script=invalid_script):
                report = evaluate_script(invalid_script, scenario=scenario, snapshot=snapshot)
                self.assertEqual(report['status'], 'needs_review')
                self.assertTrue(any(item['code'] == 'SCRIPT_CONTRACT_INVALID' for item in report['blockers']))

    def test_quality_requires_a_distinct_comment_for_each_action_and_respects_exact_text(self):
        scenario = ScenarioSpec.model_validate(scenario_payload())
        snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        reused = VALID_SCRIPT.replace(
            "    await page.goto('/users')",
            "    await page.goto('/users')\n    await page.get_by_role('button').click()",
        )
        report = evaluate_script(reused, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'ACTION_COMMENT_MISSING' for item in report['blockers']))
        exact = VALID_SCRIPT.replace("page.get_by_role('heading')", "page.get_by_text('用户列表', exact=True)")
        exact_report = evaluate_script(exact, scenario=scenario, snapshot=snapshot)
        self.assertFalse(any(item['code'] == 'AMBIGUOUS_TEXT_LOCATOR' for item in exact_report['warnings']))
        fuzzy = exact.replace(', exact=True', '')
        fuzzy_report = evaluate_script(fuzzy, scenario=scenario, snapshot=snapshot)
        self.assertTrue(any(item['code'] == 'AMBIGUOUS_TEXT_LOCATOR' for item in fuzzy_report['warnings']))

    def test_quality_report_has_deduplicated_checks_and_summary_counts(self):
        report = evaluate_script(
            VALID_SCRIPT,
            scenario=ScenarioSpec.model_validate(scenario_payload()),
            snapshot=ExplorationSnapshot.model_validate(snapshot_payload(unresolved=True)),
        )
        missing = [item for item in report['checks'] if item['code'] == 'TRACE_STEP_COVERAGE_MISSING']
        self.assertEqual(len(missing), 1)
        self.assertEqual(report['summary']['warning'], len(report['warnings']))
        self.assertEqual(report['summary']['blocker'], len(report['blockers']))
        passed = [item for item in report['checks'] if item['level'] == 'pass']
        self.assertEqual(report['summary']['passed'], len(passed))
        self.assertTrue({
            'SYNTAX_VALID', 'SCRIPT_CONTRACT_VALID', 'DOCSTRING_VALID',
            'RELATIVE_URL_VALID', 'SENSITIVE_INFORMATION_VALID', 'EXPECT_VALID',
            'ACTION_COMMENT_VALID', 'ASSERTION_COMMENT_VALID', 'CLEANUP_VALID',
            'NAMES_IMPORTS_VALID',
        }.issubset({item['code'] for item in passed}))

    def test_extraction_uses_nearest_readable_comments(self):
        metadata = extract_playwright_metadata(VALID_SCRIPT)
        self.assertEqual(metadata['extracted_steps'][0]['readable_name'], '步骤 1：进入用户列表')
        self.assertEqual(metadata['assertion_candidates'][0]['readable_name'], '断言 1：确认用户列表可见')


class OrchestratorPhase45Tests(Phase45Base):
    def _run_with(self, generation, explorer_class, generator_class):
        with patch('web_testing.generation_orchestrator.normalize_requirement', return_value=ScenarioSpec.model_validate(scenario_payload())), patch(
            'web_testing.generation_orchestrator.get_llm_manager', return_value=SimpleNamespace(current_llm=object())
        ), patch(
            'web_testing.generation_orchestrator.MCPPageExplorer', explorer_class
        ), patch('web_testing.generation_orchestrator.ScriptGenerator', generator_class), patch(
            'web_testing.generation_orchestrator.publish_stage_changed'
        ), patch('web_testing.generation_orchestrator.publish_terminal'
        ):
            return run_v2_generation(str(generation.pk), celery_task_id='phase45-task')

    def test_pre_generation_targeted_exploration_returns_one_completed_snapshot(self):
        generation = self.generation()

        class Explorer:
            explore_calls = 0
            def __init__(self, **kwargs): pass
            async def explore_until_complete(self, **kwargs):
                type(self).explore_calls += 1
                payload = snapshot_payload(unresolved=False)
                payload['completion'] = {'status': 'complete', 'targeted_rounds': 1}
                return ExplorationSnapshot.model_validate(payload)

        class Generator:
            generate_calls = 0
            def __init__(self, _model): pass
            def generate(self, **kwargs):
                type(self).generate_calls += 1
                return VALID_SCRIPT
            def repair(self, **kwargs): raise AssertionError('quality-approved script must not repair')

        result = self._run_with(generation, Explorer, Generator)
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY_WITH_WARNINGS)
        self.assertEqual(result['trace_schema_version'], 2)
        self.assertEqual(Explorer.explore_calls, 1)
        self.assertEqual(Generator.generate_calls, 1)
        self.assertEqual(generation.exploration_snapshot['schema_version'], 2)

    def test_quality_repair_has_a_hard_two_attempt_limit(self):
        generation = self.generation()

        class Explorer:
            def __init__(self, **kwargs): pass
            async def explore(self, **kwargs): return ExplorationSnapshot.model_validate(snapshot_payload())

        class Generator:
            repairs = 0
            def __init__(self, _model): pass
            def generate(self, **kwargs): return 'async def run(page):\n    await page.goto("/")\n'
            def repair(self, **kwargs):
                type(self).repairs += 1
                return 'async def run(page):\n    await page.goto("/")\n'

        result = self._run_with(generation, Explorer, Generator)
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(Generator.repairs, 2)
        self.assertEqual(generation.repair_count, 2)


class SaveAndRunnerTests(Phase45Base):
    def test_save_is_idempotent_and_keeps_credentials_out_of_metadata(self):
        generation = self.generation(
            status=WebUIScriptGeneration.Status.READY,
            current_stage=WebUIScriptGeneration.Stage.COMPLETED,
            scenario_spec=scenario_payload(), script_draft=VALID_SCRIPT,
            quality_report={'status': 'ready'},
        )
        request = self.factory.post('/save/', {'title': '保存的用例'}, format='json')
        force_authenticate(request, user=self.user)
        first = WebUIScriptGenerationSaveView.as_view()(request, project_id=self.project.id, generation_id=generation.pk)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data['data']['generation']['is_saved'])
        case = WebUITestCase.objects.get(pk=first.data['data']['test_case_id'])
        self.assertEqual(case.script_version, 1)
        self.assertNotIn('secret', str(case.generation_metadata))
        self.assertEqual(case.generation_metadata['model']['provider_name'], 'OpenAI 企业网关')
        request = self.factory.post('/save/', {}, format='json')
        force_authenticate(request, user=self.user)
        second = WebUIScriptGenerationSaveView.as_view()(request, project_id=self.project.id, generation_id=generation.pk)
        self.assertEqual(second.status_code, 200, second.data)
        case.refresh_from_db()
        self.assertEqual(case.script_version, 1)

    def test_is_saved_requires_this_generation_marker_not_only_test_case_relation(self):
        existing = WebUITestCase.objects.create(
            title='原始用例', description='原始描述', user=self.user, project=self.project,
        )
        generation = self.generation(test_case=existing)
        self.assertFalse(WebUIScriptGenerationSerializer(generation).data['is_saved'])
        existing.generation_metadata = {'generation_ref': generation_reference(generation.pk)}
        existing.save(update_fields=['generation_metadata', 'updated_at'])
        self.assertTrue(WebUIScriptGenerationSerializer(generation).data['is_saved'])

    def test_save_contract_failure_rolls_back_new_case_and_generation_link(self):
        generation = self.generation(
            status=WebUIScriptGeneration.Status.READY,
            current_stage=WebUIScriptGeneration.Stage.COMPLETED,
            scenario_spec=scenario_payload(), script_draft='async def not_run(page):\n    return None\n',
            quality_report={'status': 'ready'},
        )
        request = self.factory.post('/save/', {}, format='json')
        force_authenticate(request, user=self.user)
        response = WebUIScriptGenerationSaveView.as_view()(request, project_id=self.project.id, generation_id=generation.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WebUITestCase.objects.filter(project=self.project).count(), 0)
        generation.refresh_from_db()
        self.assertIsNone(generation.test_case)

    def test_terminal_event_is_idempotent_for_one_generation(self):
        generation = self.generation(status=WebUIScriptGeneration.Status.READY)
        cache.delete(f'webui:script-generation:terminal-event:{generation.pk}')
        with patch('web_testing.generation_events.websocket_message_service.send_task_completed') as completed:
            publish_terminal(generation)
            publish_terminal(generation)
        completed.assert_called_once()

    def test_terminal_event_sends_when_deduplication_cache_is_unavailable(self):
        generation = self.generation(status=WebUIScriptGeneration.Status.READY)
        with patch(
            'web_testing.generation_events.cache.add',
            side_effect=RuntimeError('cache unavailable'),
        ), patch('web_testing.generation_events.websocket_message_service.send_task_completed') as completed:
            publish_terminal(generation)
        completed.assert_called_once()

    def test_runner_injects_only_safe_environment_variables_without_logging_values(self):
        runner = PlaywrightRunner()
        config = ExecutionConfig(base_url='https://web.example.test', environment_variables={
            'UI_TEST_USERNAME': 'admin', 'UI_TEST_PASSWORD': 'secret', 'PATH': 'unsafe', 'bad-name': 'unsafe',
            'HTTP_PROXY': 'unsafe', 'HTTPS_PROXY': 'unsafe', 'ALL_PROXY': 'unsafe', 'NO_PROXY': 'unsafe',
            'SSL_CERT_FILE': 'unsafe', 'REQUESTS_CA_BUNDLE': 'unsafe', 'NODE_OPTIONS': 'unsafe', 'BUSINESS_FLAG': 'ok',
        })
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch('web_testing.playwright_python_runner.subprocess.run', return_value=completed) as run_mock, patch(
            'web_testing.playwright_python_runner.logger'
        ) as log_mock:
            runner._run_pytest_command('/tmp', config)
        env = run_mock.call_args.kwargs['env']
        self.assertEqual(env['UI_TEST_USERNAME'], 'admin')
        self.assertEqual(env['UI_TEST_PASSWORD'], 'secret')
        self.assertNotEqual(env['PATH'], 'unsafe')
        self.assertNotIn('bad-name', env)
        self.assertNotEqual(env.get('HTTP_PROXY'), 'unsafe')
        self.assertNotEqual(env.get('HTTPS_PROXY'), 'unsafe')
        self.assertNotEqual(env.get('ALL_PROXY'), 'unsafe')
        self.assertNotEqual(env.get('NO_PROXY'), 'unsafe')
        self.assertNotEqual(env.get('SSL_CERT_FILE'), 'unsafe')
        self.assertNotEqual(env.get('REQUESTS_CA_BUNDLE'), 'unsafe')
        self.assertNotEqual(env.get('NODE_OPTIONS'), 'unsafe')
        self.assertEqual(env['BUSINESS_FLAG'], 'ok')
        self.assertNotIn('secret', str(log_mock.mock_calls))
