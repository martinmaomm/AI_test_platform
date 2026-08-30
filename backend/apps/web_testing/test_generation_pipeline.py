"""Mock-only regression tests for V2 requirement and read-only exploration stages."""

import asyncio
import inspect
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType
from ai_core.model_manager import ModelManager
from projects.models import Environment, Project

from .generation_contracts import (
    ExplorationSnapshot,
    GenerationContractError,
    ScenarioSpec,
    parse_exploration_snapshot_json,
    parse_scenario_spec_json,
    validate_snapshot_against_scenario,
)
from .generation_orchestrator import run_v2_generation
from .generation_preflight import (
    prepare_playwright_mcp_config,
    resolve_active_playwright_mcp_config,
    run_safety_preflight,
)
from .generation_security import get_temporary_credentials
from .mcp_page_explorer import (
    MCPPageExplorer,
    ReadOnlyMCPBrowserToolGuard,
    suppress_mcp_raw_query_logs,
)
from .models import WebUIScriptGeneration
from .requirement_normalizer import RequirementNormalizer
from .tasks import generate_webui_script_generation_v2_task
from .views import WebUIScriptGenerationCreateView


def scenario_payload(**overrides):
    payload = {
        'title': '查询用户列表',
        'objective': '验证用户列表可以查询。',
        'preconditions': [],
        'steps': [{
            'id': 'S1', 'name': '进入用户列表', 'intent': 'navigate',
            'target_hint': '权限菜单中的用户列表', 'input_refs': [],
            'mutates_data': False, 'expected': '用户列表页面显示。',
        }],
        'assertions': [{
            'id': 'A1', 'name': '确认用户列表可见', 'target_hint': '用户列表',
            'expected': '列表区域可见', 'step_id': 'S1',
        }],
        'cleanup': [], 'forbidden_actions': ['探索阶段不得提交写操作'],
        'credentials_required': False, 'ambiguities': [], 'risk_level': 'low',
    }
    payload.update(overrides)
    return payload


def snapshot_payload(**overrides):
    payload = {
        'start_url_path': '/',
        'visited_paths': ['/'],
        'page_states': [{'name': '首页', 'title': '首页', 'path': '/', 'key_regions': ['导航']}],
        'elements': [{'page_name': '首页', 'role': 'link', 'visible_name': '权限', 'stable_attributes': {}, 'candidate_locators': ['get_by_text("权限")']}],
        'navigation_paths': [],
        'step_evidence': {'S1': {'status': 'confirmed', 'paths': ['/'], 'element_names': ['权限'], 'reason': '导航可见'}},
        'unresolved_steps': [], 'warnings': [],
        'tool_stats': {'total_tool_calls': 1, 'tool_counts': {'playwright_navigate': 1}, 'failed_tool_calls': 0, 'termination_reason': None, 'duration_seconds': 0.1},
    }
    payload.update(overrides)
    return payload


class GenerationPipelineBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='generation-pipeline-user', email='generation-pipeline@example.com', password='test-password'
        )
        self.project = Project.objects.create(
            name='Generation pipeline project', project_type='web', owner=self.user, created_by=self.user
        )
        self.environment = Environment.objects.create(
            project=self.project,
            name='Pipeline WebUI',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test', 'variables': {}},
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', api_key='test-key',
            base_url='https://llm.example.test', model_name='locked-model',
            is_active=True, created_by=self.user,
        )
        self.mcp = MCPConfiguration.objects.create(
            name='playwright', created_by=self.user, is_active=True,
            raw_config=json.dumps({'mcpServers': {'playwright': {'command': 'npx', 'args': ['-y', 'playwright-mcp-server']}}}),
        )

    def make_generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user, 'environment': self.environment,
            'description_safe': '查询用户列表。', 'target_url_safe': 'https://web.example.test/',
            'model_info': {'config_id': self.model.id, 'provider': self.model.provider, 'model_name': self.model.model_name},
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)


class GenerationContractAndNormalizerTests(TestCase):
    def test_contract_repair_is_used_once_and_then_validates(self):
        repair = Mock(return_value=json.dumps(scenario_payload(), ensure_ascii=False))
        spec = parse_scenario_spec_json('{invalid json', format_repair=repair)
        self.assertEqual(spec.steps[0].id, 'S1')
        repair.assert_called_once()

    def test_contract_repair_failure_is_reported(self):
        with self.assertRaises(GenerationContractError):
            parse_scenario_spec_json('{invalid', format_repair=lambda *_: '{still invalid')

    def test_normalizer_calls_locked_manager_and_never_reinterprets_format_repair(self):
        manager = Mock()
        manager.invoke.side_effect = ['{invalid', json.dumps(scenario_payload(), ensure_ascii=False)]
        with patch('web_testing.requirement_normalizer.get_llm_manager', return_value=manager) as factory:
            spec = RequirementNormalizer(123).normalize('查询用户列表。', {'title': '列表用例'})
        self.assertEqual(spec.title, '查询用户列表')
        factory.assert_called_once_with(config_id=123)
        self.assertEqual(manager.invoke.call_count, 2)

    def test_scenario_contract_requires_assertion_known_references_and_cleanup_for_mutation(self):
        with self.assertRaises(GenerationContractError):
            parse_scenario_spec_json(json.dumps(scenario_payload(assertions=[]), ensure_ascii=False))
        with self.assertRaises(GenerationContractError):
            parse_scenario_spec_json(json.dumps(scenario_payload(assertions=[{
                **scenario_payload()['assertions'][0], 'step_id': 'S99',
            }]), ensure_ascii=False))
        with self.assertRaises(GenerationContractError):
            parse_scenario_spec_json(json.dumps(scenario_payload(steps=[{
                **scenario_payload()['steps'][0], 'intent': 'create', 'mutates_data': True,
            }]), ensure_ascii=False))

    def test_contract_rejects_sensitive_values_and_unsafe_snapshot_artifacts(self):
        with self.assertRaises(GenerationContractError):
            parse_scenario_spec_json(json.dumps(scenario_payload(title='账号 admin 123456'), ensure_ascii=False))
        with self.assertRaises(GenerationContractError):
            parse_exploration_snapshot_json(json.dumps(snapshot_payload(
                visited_paths=['https://web.example.test/users'],
            ), ensure_ascii=False))
        with self.assertRaises(GenerationContractError):
            parse_exploration_snapshot_json(json.dumps(snapshot_payload(
                elements=[{
                    **snapshot_payload()['elements'][0],
                    'candidate_locators': ['page.get_by_role("button").click()'],
                }],
            ), ensure_ascii=False))

    def test_snapshot_must_cover_exactly_the_scenario_steps(self):
        scenario = ScenarioSpec.model_validate(scenario_payload())
        valid_snapshot = ExplorationSnapshot.model_validate(snapshot_payload())
        validate_snapshot_against_scenario(scenario, valid_snapshot)
        missing = ExplorationSnapshot.model_validate(snapshot_payload(step_evidence={}))
        with self.assertRaises(GenerationContractError):
            validate_snapshot_against_scenario(scenario, missing)
        unknown = ExplorationSnapshot.model_validate(snapshot_payload(step_evidence={
            'S1': snapshot_payload()['step_evidence']['S1'],
            'S2': snapshot_payload()['step_evidence']['S1'],
        }))
        with self.assertRaises(GenerationContractError):
            validate_snapshot_against_scenario(scenario, unknown)


class GenerationPreflightTests(GenerationPipelineBase):
    def test_preflight_allows_normal_crud_goal_but_stops_exploration_write_request(self):
        generation = self.make_generation(description_safe='新增、编辑、删除本轮测试数据，探索阶段只查看页面。')
        crud_scenario = ScenarioSpec.model_validate(scenario_payload(
            steps=[{**scenario_payload()['steps'][0], 'intent': 'create', 'mutates_data': True}],
            cleanup=[{
                'id': 'C1', 'name': '清理本轮用户', 'target_hint': '本轮新增用户',
                'condition': '仅清理本轮创建的数据', 'step_id': 'S1',
            }],
        ))
        allowed = run_safety_preflight(generation, crud_scenario, credentials_available=False)
        self.assertEqual(allowed.outcome, 'continue')

        generation.description_safe = '探索阶段请提交新增用户后查看结果。'
        blocked = run_safety_preflight(generation, crud_scenario, credentials_available=False)
        self.assertEqual(blocked.outcome, 'needs_confirmation')
        self.assertEqual(blocked.error_code, 'EXPLORATION_WRITE_CONFIRMATION_REQUIRED')

    def test_preflight_does_not_misread_read_only_exploration_safety_constraints(self):
        generation = self.make_generation(description_safe=(
            '登录后进入“权限 > 用户列表”，只探索页面结构、按钮名称、表单字段和列表字段。'
            '探索阶段不要提交新增、编辑或删除操作，不要修改任何已有数据。'
            '后续脚本再执行新增、编辑、删除并清理本轮数据。'
        ))
        spec = ScenarioSpec.model_validate(scenario_payload())
        allowed = run_safety_preflight(generation, spec, credentials_available=False)
        self.assertEqual(allowed.outcome, 'continue')

        generation.description_safe = '禁止在探索阶段提交新增、编辑或删除；只允许查看和打开表单。'
        self.assertEqual(
            run_safety_preflight(generation, spec, credentials_available=False).outcome,
            'continue',
        )

    def test_preflight_stops_only_explicit_exploration_write_requests(self):
        generation = self.make_generation(description_safe='探索阶段可以提交新增用户，并验证保存结果。')
        result = run_safety_preflight(
            generation,
            ScenarioSpec.model_validate(scenario_payload()),
            credentials_available=False,
        )
        self.assertEqual(result.outcome, 'needs_confirmation')

    def test_preflight_still_blocks_only_execute_write_not_read_only_wording(self):
        spec = ScenarioSpec.model_validate(scenario_payload())
        generation = self.make_generation(description_safe='探索阶段只执行新增并保存，用于确认页面行为。')
        self.assertEqual(
            run_safety_preflight(generation, spec, credentials_available=False).outcome,
            'needs_confirmation',
        )
        generation.description_safe = '探索阶段只查看页面，只读且不提交新增或编辑。'
        self.assertEqual(
            run_safety_preflight(generation, spec, credentials_available=False).outcome,
            'continue',
        )

    def test_preflight_requires_credentials_without_cache_or_environment_variables(self):
        generation = self.make_generation()
        spec = ScenarioSpec.model_validate(scenario_payload(credentials_required=True))
        result = run_safety_preflight(generation, spec, credentials_available=False)
        self.assertEqual(result.outcome, 'needs_credentials')
        self.assertEqual(result.error_code, 'CREDENTIALS_REQUIRED')

    def test_preflight_rejects_missing_mcp_model_and_ambiguity(self):
        generation = self.make_generation()
        spec = ScenarioSpec.model_validate(scenario_payload(ambiguities=['无法确定保存按钮名称']))
        ambiguous = run_safety_preflight(generation, spec, credentials_available=False)
        self.assertEqual(ambiguous.outcome, 'needs_confirmation')

        self.mcp.is_active = False
        self.mcp.save(update_fields=['is_active'])
        no_mcp = run_safety_preflight(generation, ScenarioSpec.model_validate(scenario_payload()), credentials_available=False)
        self.assertEqual(no_mcp.error_code, 'MCP_CONFIG_MISSING')

        self.mcp.is_active = True
        self.mcp.save(update_fields=['is_active'])
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        no_model = run_safety_preflight(generation, ScenarioSpec.model_validate(scenario_payload()), credentials_available=False)
        self.assertEqual(no_model.error_code, 'MODEL_CONFIG_MISSING')

    def test_playwright_mcp_preparation_restores_browser_cache_timeout_and_command_checks(self):
        raw_config = {'mcpServers': {'playwright': {'command': 'npx', 'args': ['-y', 'server']}}}
        prepared = prepare_playwright_mcp_config(
            raw_config,
            browser_path='.playwright-browsers',
            base_dir='/tmp/backend',
        )
        server = prepared['mcpServers']['playwright']
        self.assertEqual(server['timeout'], 30)
        self.assertEqual(server['env']['PLAYWRIGHT_BROWSERS_PATH'], '/tmp/backend/.playwright-browsers')
        self.assertEqual(server['env']['PYTHONUNBUFFERED'], '1')
        self.assertEqual(server['env']['MCP_USE_ANONYMIZED_TELEMETRY'], 'false')
        self.assertNotIn('env', raw_config['mcpServers']['playwright'])
        with self.assertRaisesRegex(ValueError, 'command'):
            prepare_playwright_mcp_config({'mcpServers': {'playwright': {}}})

    def test_preflight_uses_browser_path_environment_without_logging_config(self):
        with patch.dict(os.environ, {'MCP_PLAYWRIGHT_BROWSERS_PATH': '.python-playwright-browsers'}, clear=False):
            selection = resolve_active_playwright_mcp_config(self.user.id)
        self.assertIsNotNone(selection)
        _, prepared = selection
        browser_path = prepared['mcpServers']['playwright']['env']['PLAYWRIGHT_BROWSERS_PATH']
        self.assertTrue(browser_path.endswith('/backend/.python-playwright-browsers'))


class MCPPageExplorerTests(TestCase):
    def test_read_only_explorer_returns_snapshot_without_orm_or_raw_query_logging(self):
        class FakeClient:
            async def create_all_sessions(self):
                return None

            async def close_all_sessions(self):
                return None

        class FakeAgent:
            received_prompt = ''

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def run(self, prompt):
                type(self).received_prompt = prompt
                return json.dumps(snapshot_payload(), ensure_ascii=False)

        explorer = MCPPageExplorer(
            llm_model=object(),
            mcp_config={'mcpServers': {'playwright': {'command': 'npx'}}},
            pom_context='无 POM',
        )
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=FakeClient()), patch(
            'web_testing.mcp_page_explorer.MCPAgent', FakeAgent
        ):
            snapshot = asyncio.run(explorer.explore(
                scenario=ScenarioSpec.model_validate(scenario_payload()),
                start_path='/',
                target_url_safe='https://web.example.test/',
            ))
        self.assertIsInstance(snapshot, ExplorationSnapshot)
        self.assertEqual(snapshot.tool_stats.total_tool_calls, 0)
        self.assertEqual(json.loads(FakeAgent.received_prompt)['navigation_target_url'], 'https://web.example.test/')
        self.assertNotIn('https://', json.dumps(snapshot.model_dump(mode='json')))

    def test_raw_mcp_query_logging_is_suppressed(self):
        mcp_loggers = [logging.getLogger(name) for name in ('mcp_use', 'mcpagent')]
        previous_levels = [mcp_logger.level for mcp_logger in mcp_loggers]
        captured = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = ListHandler()
        for mcp_logger in mcp_loggers:
            mcp_logger.addHandler(handler)
        try:
            for mcp_logger in mcp_loggers:
                mcp_logger.setLevel(logging.INFO)
            with suppress_mcp_raw_query_logs():
                for mcp_logger in mcp_loggers:
                    self.assertGreaterEqual(mcp_logger.level, logging.WARNING)
                    mcp_logger.info('Received query: username=admin password=super-secret')
            self.assertEqual([mcp_logger.level for mcp_logger in mcp_loggers], [logging.INFO, logging.INFO])
            self.assertEqual(captured, [])
        finally:
            for mcp_logger, previous_level in zip(mcp_loggers, previous_levels):
                mcp_logger.removeHandler(handler)
                mcp_logger.setLevel(previous_level)

    def test_explorer_classifies_browser_failure_without_leaking_prompt(self):
        class FakeClient:
            async def create_all_sessions(self):
                return None

            async def close_all_sessions(self):
                return None

        class FailingAgent:
            def __init__(self, **kwargs):
                pass

            async def run(self, prompt):
                raise RuntimeError("browser executable doesn't exist")

        explorer = MCPPageExplorer(
            llm_model=object(), mcp_config={'mcpServers': {'playwright': {'command': 'npx'}}}, pom_context='无 POM'
        )
        with patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=FakeClient()), patch(
            'web_testing.mcp_page_explorer.MCPAgent', FailingAgent
        ):
            with self.assertRaisesRegex(Exception, 'Chromium'):
                asyncio.run(explorer.explore(
                    scenario=ScenarioSpec.model_validate(scenario_payload()),
                    start_path='/',
                    target_url_safe='https://web.example.test/',
                ))

    def test_temporary_credentials_are_only_in_memory_prompt_not_platform_logs(self):
        explorer = MCPPageExplorer(
            llm_model=object(), mcp_config={'mcpServers': {'playwright': {'command': 'npx'}}}, pom_context='无 POM'
        )
        with patch('web_testing.mcp_page_explorer.logger') as explorer_logger:
            prompt = explorer._build_prompt(
                ScenarioSpec.model_validate(scenario_payload()), '/',
                'https://web.example.test/',
                {'username': 'admin', 'password': 'super-secret'},
            )
        self.assertIn('super-secret', prompt)
        explorer_logger.info.assert_not_called()
        explorer_logger.warning.assert_not_called()

    def test_read_only_guard_blocks_definite_write_actions(self):
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=50)
        with self.assertRaisesRegex(Exception, '只读探索'):
            guard.on_tool_start(
                {'name': 'playwright_click'},
                '',
                inputs={'selector': 'button', 'text': '确认删除'},
            )

    def test_read_only_guard_blocks_enter_but_allows_non_submission_key(self):
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=50)
        with self.assertRaisesRegex(Exception, 'Enter'):
            guard.on_tool_start(
                {'name': 'playwright_press_key'},
                '',
                inputs={'key': 'NumpadEnter'},
            )
        safe_guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=50)
        safe_guard.on_tool_start(
            {'name': 'playwright_press_key'},
            '',
            inputs={'key': 'Tab'},
        )
        self.assertEqual(safe_guard.get_stats()['total_tool_calls'], 1)

    def test_read_only_guard_allows_opening_a_create_form(self):
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=50)
        guard.on_tool_start(
            {'name': 'playwright_click'},
            '',
            inputs={'selector': 'button', 'text': '新增用户'},
        )
        self.assertEqual(guard.get_stats()['total_tool_calls'], 1)

    def test_async_explorer_contains_no_orm_access(self):
        self.assertNotIn('.objects', inspect.getsource(MCPPageExplorer))


class GenerationOrchestratorTests(GenerationPipelineBase):
    def test_normalization_receives_persisted_clarification_answers(self):
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.NORMALIZING,
            current_stage=WebUIScriptGeneration.Stage.NORMALIZING,
            clarifications=[{
                'answers': [{
                    'question': '编辑后的昵称如何生成？',
                    'answer': '使用唯一名称加 _edited 后缀。',
                }],
            }],
        )
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            return_value=ScenarioSpec.model_validate(scenario_payload()),
        ) as normalizer, patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(
                outcome='failed', error_code='MCP_CONFIG_MISSING',
                message='没有可用的 Playwright MCP 配置。', warnings=[],
            ),
        ):
            run_v2_generation(str(generation.pk), celery_task_id='resume-normalize-task')

        prompt = normalizer.call_args.args[0]
        self.assertIn('编辑后的昵称如何生成', prompt)
        self.assertIn('_edited', prompt)

    def test_preflight_resume_reuses_saved_scenario_without_normalizing_again(self):
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.PREFLIGHTING,
            current_stage=WebUIScriptGeneration.Stage.PREFLIGHTING,
            progress=25,
            scenario_spec=scenario_payload(),
        )
        preflight_result = SimpleNamespace(
            outcome='failed',
            error_code='MCP_CONFIG_MISSING',
            message='没有可用的 Playwright MCP 配置。',
            warnings=[],
        )
        with patch('web_testing.generation_orchestrator.normalize_requirement') as normalizer, patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=preflight_result,
        ):
            result = run_v2_generation(str(generation.pk), celery_task_id='resume-preflight-task')

        normalizer.assert_not_called()
        self.assertEqual(result['error_code'], 'MCP_CONFIG_MISSING')
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)

    def test_orchestrator_persists_scenario_snapshot_and_quality_approved_script(self):
        generation = self.make_generation()

        class FakeExplorer:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def explore(self, **kwargs):
                return ExplorationSnapshot.model_validate(snapshot_payload())

        class FakeGenerator:
            def __init__(self, _model):
                pass

            def generate(self, **kwargs):
                return '''"""场景：查询用户列表。目标：验证列表可见。前置条件：无。清理策略：无需清理。"""
from playwright.async_api import expect

async def run(page):
    # 步骤 1：进入用户列表
    await page.goto('/users')
    # 断言 1：确认用户列表可见
    await expect(page.get_by_role('heading')).to_be_visible()
'''

        fake_manager = SimpleNamespace(current_llm=object())
        with patch('web_testing.generation_orchestrator.normalize_requirement', return_value=ScenarioSpec.model_validate(scenario_payload())), patch(
            'web_testing.generation_orchestrator.get_llm_manager', return_value=fake_manager
        ), patch('web_testing.generation_orchestrator.MCPPageExplorer', FakeExplorer), patch(
            'web_testing.generation_orchestrator.ScriptGenerator', FakeGenerator
        ), patch(
            'web_testing.generation_orchestrator.publish_stage_changed'
        ):
            result = run_v2_generation(str(generation.pk), celery_task_id='v2-task')

        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY_WITH_WARNINGS)
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.READY_WITH_WARNINGS)
        self.assertTrue(generation.scenario_spec)
        self.assertTrue(generation.exploration_snapshot)
        self.assertIn('async def run(page)', generation.script_draft)
        self.assertNotIn('https://', json.dumps(generation.exploration_snapshot))

    def test_orchestrator_honours_cancel_before_model_or_mcp_work(self):
        generation = self.make_generation()
        cache.set('celery:cancel:v2-cancelled-task', True, timeout=60)
        try:
            with patch('web_testing.generation_orchestrator.normalize_requirement') as normalizer:
                result = run_v2_generation(str(generation.pk), celery_task_id='v2-cancelled-task')
            normalizer.assert_not_called()
            self.assertEqual(result['status'], 'cancelled')
        finally:
            cache.delete('celery:cancel:v2-cancelled-task')

    def test_task_signature_only_accepts_generation_id(self):
        parameters = list(inspect.signature(generate_webui_script_generation_v2_task.run).parameters)
        self.assertEqual(parameters, ['generation_id'])

    def test_orchestrator_classifies_normalizer_rate_limit_and_pom_failure(self):
        generation = self.make_generation()
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            side_effect=RuntimeError('HTTP 429 Too Many Requests'),
        ):
            result = run_v2_generation(str(generation.pk))
        self.assertEqual(result['error_code'], 'MODEL_RATE_LIMITED')

        generation = self.make_generation()
        fake_manager = SimpleNamespace(current_llm=object())
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            return_value=ScenarioSpec.model_validate(scenario_payload()),
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=fake_manager,
        ), patch(
            'web_testing.generation_orchestrator._load_project_pom_context',
            side_effect=RuntimeError('database unavailable'),
        ):
            result = run_v2_generation(str(generation.pk))
        self.assertEqual(result['error_code'], 'TRANSIENT_SERVICE_ERROR')


class GenerationModelLockAPITests(GenerationPipelineBase):
    def test_api_locks_default_active_model_and_dispatches_only_generation_id(self):
        request = APIRequestFactory().post('/script-generations/', {
            'description': '查询用户列表。', 'environment_id': self.environment.id, 'start_path': '/',
        }, format='json')
        force_authenticate(request, user=self.user)
        task_result = SimpleNamespace(id='locked-v2-task')
        with patch('web_testing.views.generate_webui_script_generation_v2_task.delay', return_value=task_result) as delay:
            response = WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)
        self.assertEqual(response.status_code, 201)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.model_info['config_id'], self.model.id)
        self.assertEqual(generation.model_info['model_name'], 'locked-model')
        delay.assert_called_once_with(str(generation.pk))

    def test_api_rejects_disabled_requested_model_without_fallback(self):
        disabled = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', api_key='disabled-key',
            base_url='https://llm.example.test', model_name='disabled', is_active=False, created_by=self.user,
        )
        request = APIRequestFactory().post('/script-generations/', {
            'description': '查询用户列表。', 'environment_id': self.environment.id,
            'start_path': '/', 'model_config_id': disabled.id,
        }, format='json')
        force_authenticate(request, user=self.user)
        response = WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn('model_config_id', response.data['error']['details'])

    def test_model_manager_initializes_only_the_requested_active_config(self):
        with patch('ai_core.model_manager.init_chat_model', return_value=object()) as init_model:
            manager = ModelManager(model_type='llm', config_id=self.model.id)
        self.assertEqual(manager.config['config_id'], self.model.id)
        self.assertEqual(init_model.call_count, 1)

        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        with self.assertRaisesRegex(ValueError, '已停用'):
            ModelManager(model_type='llm', config_id=self.model.id)

    def test_dispatch_failure_marks_record_failed_and_clears_temporary_credentials(self):
        request = APIRequestFactory().post('/script-generations/', {
            'description': '查询用户列表。', 'environment_id': self.environment.id, 'start_path': '/',
            'temporary_credentials': {'username': 'admin', 'password': 'super-secret'},
        }, format='json')
        force_authenticate(request, user=self.user)
        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            side_effect=RuntimeError('broker unavailable'),
        ):
            response = WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)
        self.assertEqual(response.status_code, 503)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertEqual(generation.error_code, 'TRANSIENT_SERVICE_ERROR')
        self.assertIsNone(get_temporary_credentials(generation.pk))
