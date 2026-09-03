"""Migrated v4 persistence, concurrency, API and quality regressions.

The historical filename remains to demonstrate that broadly useful coverage
was migrated rather than discarded. No v3 trace or Goal contract is accepted.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from openai import APIStatusError, OpenAIError
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Environment, Project

from .exploration_trace import ExplorationTraceRecorder, FinalizedAction, FinalizedAssertion
from .generation_contracts import GenerationContractError, GenerationTransitionError, ScenarioPlan
from .generation_events import publish_terminal
from .generation_preflight import (
    exploration_requires_write_confirmation,
    prepare_playwright_mcp_output_config,
    validate_generation_output_id,
)
from .generation_repository import (
    GenerationResolutionConflict,
    cancel_generation,
    claim_generation_worker,
    claim_trace_generation_retry,
    get_generation_temporary_credentials,
    prepare_trace_generation_retry,
    transition_generation,
)
from .generation_save_state import generation_reference, is_generation_saved
from .generation_security import (
    GenerationInputSecurityError,
    get_temporary_credentials,
    normalize_start_path,
    store_temporary_credentials,
)
from .mcp_page_explorer import EXPLORER_CONSTRAINTS, MCPPageExplorer, MCPPageExplorerError
from .models import WebUIScriptGeneration
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .script_quality import evaluate_script
from .serializers import (
    WebUIScriptGenerationCreateSerializer,
    WebUIScriptGenerationResolveSerializer,
)
from .views import WebUIScriptGenerationCreateView


def plan_payload(*, cleanup: bool = False):
    criteria = ['测试值可见']
    assertions = [{
        'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
        'kind': 'contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
    }]
    if cleanup:
        criteria.append('测试值清理后不可见')
        assertions.append({
            'assertion_id': 'A2', 'criterion_index': 1, 'phase': 'cleanup',
            'kind': 'not_contains_ref', 'input_ref': 'ITEM_NAME', 'literal': '',
        })
    return {
        'schema_version': 4,
        'title': '连续回归场景',
        'objective': '在同一会话中完成动作并验证 callback 证据。',
        'instructions': ['进入页面', '执行操作', '验证结果'],
        'success_criteria': criteria,
        'assertion_requirements': assertions,
        'input_refs': [{'name': 'ITEM_NAME', 'source': 'generated'}],
        'preconditions': [], 'forbidden_actions': [],
        'credentials_required': False, 'allow_test_data_writes': True,
        'cleanup_expected': cleanup, 'discovery_notes': [], 'risk_level': 'low',
    }


def record(recorder, run_id: str, tool_name: str, inputs: dict, output):
    recorder.on_tool_start(
        {'name': tool_name}, '', run_id=run_id, inputs=inputs,
    )
    recorder.on_tool_end(output, run_id=run_id)


def replay_fixture(*, verify_cleanup: bool = True):
    plan = ScenarioPlan.model_validate(plan_payload(cleanup=True))
    recorder = ExplorationTraceRecorder('/items')
    recorder.configure_plan(plan)
    recorder.configure_runtime(
        {'ITEM_NAME': 'runtime-item'}, plan.input_sources(),
    )
    record(
        recorder, 'navigate', 'playwright_navigate',
        {'url': 'https://example.test/items'}, 'URL: https://example.test/items',
    )
    record(
        recorder, 'fill', 'playwright_fill',
        {'selector': '[name=password][data-item="runtime-item"]', 'value': 'runtime-item'},
        'filled',
    )
    record(
        recorder, 'assert-main', 'playwright_get_visible_html',
        {'selector': '#result'}, '<main>runtime-item</main>',
    )
    record(
        recorder, 'cleanup', 'playwright_click', {'selector': '#cleanup'}, 'clicked',
    )
    if verify_cleanup:
        record(
            recorder, 'assert-cleanup', 'playwright_get_visible_html',
            {'selector': '#result'}, '<main>empty</main>',
        )
    if verify_cleanup:
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='填写测试值')],
            assertions=[
                FinalizedAssertion(assertion_id='A1', event_id='E000003'),
                FinalizedAssertion(assertion_id='A2', event_id='E000005'),
            ],
            cleanup_actions=[FinalizedAction(event_id='E000004', step_name='清理测试数据')],
        )
    return plan, recorder.build(tool_stats={'total_tool_calls': 5})


class V4ExplorerLifecycleRegressionTests(SimpleTestCase):
    def test_timeout_and_agent_failure_close_the_single_session(self):
        class Client:
            opened = closed = 0

            async def create_all_sessions(self):
                self.opened += 1

            async def close_all_sessions(self):
                self.closed += 1

        class BrokenAgent:
            def __init__(self, **_kwargs):
                pass

            async def initialize(self):
                raise RuntimeError('connector failed')

            async def register_local_tools(self, _tools):
                raise AssertionError('initialization did not finish')

            async def run(self, *_args, **_kwargs):
                raise AssertionError('must not run')

        plan = ScenarioPlan.model_validate(plan_payload())
        client = Client()
        with override_settings(BASE_DIR='/tmp'), patch(
            'web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client,
        ), patch('web_testing.mcp_page_explorer.MCPAgent', BrokenAgent):
            explorer = MCPPageExplorer(
                llm_model=Mock(), mcp_config={'mcpServers': {}},
                generation_id=str(uuid4()), exploration_timeout_seconds=10,
            )
            with self.assertRaises(MCPPageExplorerError):
                asyncio.run(explorer.explore_until_complete(
                    plan=plan, start_path='/', target_url_safe='/',
                ))
        self.assertEqual((client.opened, client.closed), (1, 1))

    def test_sync_database_cancel_check_runs_off_event_loop(self):
        class Client:
            opened = closed = 0

            async def create_all_sessions(self):
                self.opened += 1

            async def close_all_sessions(self):
                self.closed += 1

        class Agent:
            def __init__(self, **kwargs):
                self.guard = kwargs['callbacks'][0]

            async def initialize(self):
                self.guard.on_chat_model_start({}, [])

            async def register_local_tools(self, _tools):
                return None

            async def run(self, *_args, **_kwargs):
                self.guard.on_tool_start(
                    {'name': 'playwright_get_visible_html'}, '', run_id='observe',
                    inputs={'selector': '#main'},
                )
                self.guard.on_tool_end('<main>visible</main>', run_id='observe')

        calls = {'count': 0}

        def cancel_check():
            from django.db import connection

            calls['count'] += 1
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                return cursor.fetchone()[0] != 1

        plan = ScenarioPlan.model_validate(plan_payload())
        client = Client()
        with override_settings(BASE_DIR='/tmp'), patch(
            'web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client,
        ), patch('web_testing.mcp_page_explorer.MCPAgent', Agent):
            explorer = MCPPageExplorer(
                llm_model=Mock(), mcp_config={'mcpServers': {}},
                generation_id=str(uuid4()), cancel_check=cancel_check,
            )
            trace = asyncio.run(explorer.explore_until_complete(
                plan=plan, start_path='/', target_url_safe='/',
            ))
        self.assertGreaterEqual(calls['count'], 1)
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertGreater(trace.tool_stats['duration_seconds'], 0)


class V4ScriptQualityRegressionTests(SimpleTestCase):
    def test_compiled_script_has_callback_provenance_cleanup_verification_and_signature(self):
        plan, trace = replay_fixture()
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay)
        self.assertIn('async def run(page, variables):', source)
        self.assertIn('finally:', source)
        self.assertIn('[A2/E000005]', source)
        self.assertIn('[name=password]', source)
        self.assertNotIn('runtime-item', source)
        self.assertFalse(report['blockers'])

    def test_quality_restores_general_safety_executability_and_provenance_checks(self):
        plan, trace = replay_fixture()
        replay = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay)
        variants = {
            'ABSOLUTE_URL_FORBIDDEN': source + '# https://example.test/forbidden\n',
            'RUN_SIGNATURE_INVALID': source.replace(
                'async def run(page, variables):', 'async def run(page):',
            ),
            'ACTION_EVIDENCE_REFERENCE_MISSING': source.replace(
                '[E000001]', '(E000001)', 1,
            ),
            'CLEANUP_FINALLY_MISMATCH': source.replace(
                '    finally:', '    except Exception:',
            ),
            'UNRESOLVED_PLACEHOLDER': source.replace(
                '    variables = variables or {}', '    pass',
            ),
            'BROWSER_LIFECYCLE_FORBIDDEN': source.replace(
                '    variables = variables or {}', '    await page.close()',
            ),
            'FIXED_WAIT_FORBIDDEN': source.replace(
                '    variables = variables or {}', '    await page.wait_for_timeout(100)',
            ),
            'UNDEFINED_NAME': source.replace(
                '    variables = variables or {}', '    missing_name()',
            ),
        }
        for expected_code, invalid_source in variants.items():
            with self.subTest(expected_code=expected_code):
                report = evaluate_script(
                    invalid_source, plan=plan, trace=trace, replay_plan=replay,
                )
                self.assertIn(
                    expected_code, {item['code'] for item in report['blockers']},
                )
        credential_report = evaluate_script(
            source.replace('    variables = variables or {}', '    password = "test-only-password"'),
            plan=plan, trace=trace, replay_plan=replay,
        )
        self.assertEqual(
            {item['code'] for item in credential_report['blockers']},
            {'SCRIPT_NOT_DETERMINISTIC_REPLAY'},
        )


class V4SafeOutputPathRegressionTests(SimpleTestCase):
    def test_plaintext_credentials_do_not_weaken_target_or_high_risk_guards(self):
        with self.assertRaisesRegex(GenerationInputSecurityError, '同源'):
            normalize_start_path(
                'https://other.example.test/items',
                'https://web.example.test',
            )
        self.assertEqual(
            normalize_start_path(
                'https://web.example.test/items',
                'https://web.example.test',
            ),
            '/items',
        )
        self.assertTrue(exploration_requires_write_confirmation('提交付款并发布结果。'))
        self.assertFalse(exploration_requires_write_confirmation('不要付款或发布，只检查页面。'))

    def test_generation_output_id_and_mcp_artifact_paths_are_confined(self):
        with self.assertRaises(ValueError):
            validate_generation_output_id('../outside')
        generation_id = str(uuid4())
        with tempfile.TemporaryDirectory(prefix='aits-output-test-') as directory:
            base = Path(directory)
            scripts = base / 'scripts'
            scripts.mkdir()
            (scripts / 'playwright_mcp_output_bootstrap.mjs').write_text(
                '// fixture', encoding='utf-8',
            )
            config = {
                'mcpServers': {
                    'playwright': {
                        'command': 'npx',
                        'args': [
                            '@executeautomation/playwright-mcp-server@1.0.0',
                        ],
                    },
                },
            }
            prepared = prepare_playwright_mcp_output_config(
                config, generation_id, base_dir=directory,
            )
        environment = prepared['mcpServers']['playwright']['env']
        self.assertIn(generation_id, environment['AITS_MCP_LOG_FILE'])
        self.assertIn(generation_id, environment['AITS_MCP_SCREENSHOT_DIR'])
        self.assertNotIn('..', environment['AITS_MCP_SCREENSHOT_DIR'])
        self.assertEqual(environment['AITS_MCP_DISABLE_FILE_LOG'], '0')
        self.assertIn('测试环境模式允许凭据随 callback、日志和截图保留', EXPLORER_CONSTRAINTS)
        self.assertNotIn('不得输出用户名、密码、Token', EXPLORER_CONSTRAINTS)
        self.assertNotIn('不要调用截图工具', EXPLORER_CONSTRAINTS)


class V4GenerationPersistenceRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='v4-regression-owner', email='v4-owner@example.test',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='V4 regressions', project_type='web', owner=self.user,
            created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='Web',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'}, is_active=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai',
            provider_name='Test provider', api_key='key',
            base_url='https://llm.example.test', model_name='v4-model',
            is_active=True, created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def make_generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user, 'environment': self.environment,
            'description_safe': '检查目标页面。',
            'target_url_safe': 'https://web.example.test/items',
            'model_info': {'config_id': self.model.id},
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def api_request(self, user, payload):
        request = self.factory.post('/script-generations/', payload, format='json')
        force_authenticate(request, user=user)
        return WebUIScriptGenerationCreateView.as_view()(
            request, project_id=self.project.id,
        )

    def test_create_api_enforces_project_scope_and_locks_active_model(self):
        outsider = get_user_model().objects.create_user(
            username='v4-outsider', email='v4-outsider@example.test',
            password='test-password',
        )
        denied = self.api_request(outsider, {
            'description': '检查目标页面。', 'environment_id': self.environment.id,
        })
        self.assertEqual(denied.status_code, 404)

        other = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='Other',
            api_key='key', base_url='https://llm.example.test',
            model_name='other-model', is_active=True, created_by=self.user,
        )
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='v4-create-task'),
        ) as delay:
            response = self.api_request(self.user, {
                'description': '检查目标页面。',
                'environment_id': self.environment.id,
                'start_path': '/items', 'model_config_id': other.id,
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.model_info['config_id'], other.id)
        delay.assert_called_once_with(str(generation.pk))

        other.is_active = False
        other.save(update_fields=['is_active'])
        rejected = self.api_request(self.user, {
            'description': '检查目标页面。',
            'environment_id': self.environment.id,
            'start_path': '/items', 'model_config_id': other.id,
        })
        self.assertEqual(rejected.status_code, 400)

    def test_create_and_resolution_accept_plaintext_test_credentials(self):
        description = '使用用户名 test-user、密码 test-password 和 token=token-for-test 登录后检查首页。'
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='plaintext-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': description,
                'environment_id': self.environment.id,
                'start_path': '/items',
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.description_safe, description)

        generation.status = WebUIScriptGeneration.Status.NEEDS_CONFIRMATION
        generation.error_code = 'INPUT_AMBIGUOUS'
        generation.scenario_spec = {'ambiguities': ['请补充测试登录信息。']}
        generation.save(update_fields=['status', 'error_code', 'scenario_spec'])
        serializer = WebUIScriptGenerationResolveSerializer(
            data={
                'expected_status': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
                'expected_revision': generation.revision,
                'clarification_answers': [{
                    'question': '请补充测试登录信息。',
                    'answer': '用户名 test-user，密码 test-password，token=token-for-test。',
                }],
            },
            context={'generation': generation},
        )
        serializer.is_valid(raise_exception=True)
        self.assertEqual(
            serializer.validated_data['safe_answers'][0]['answer'],
            '用户名 test-user，密码 test-password，token=token-for-test。',
        )

    def test_create_uses_inline_login_pair_when_structured_credentials_are_absent(self):
        description = '登录账号 inline-user inline-password 后检查首页。'
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='inline-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': description,
                'environment_id': self.environment.id,
                'start_path': '/items',
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.description_safe, description)
        self.assertEqual(get_temporary_credentials(generation.pk), {
            'username': 'inline-user',
            'password': 'inline-password',
        })

    def test_structured_credentials_override_inline_login_pair_without_rejection(self):
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='structured-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': '登录账号 inline-user inline-password 后检查首页。',
                'environment_id': self.environment.id,
                'start_path': '/items',
                'temporary_credentials': {
                    'username': 'form-user',
                    'password': 'form-password',
                },
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(get_temporary_credentials(generation.pk), {
            'username': 'form-user',
            'password': 'form-password',
        })

    def test_needs_credentials_resolution_accepts_inline_login_pair(self):
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
        )
        serializer = WebUIScriptGenerationResolveSerializer(
            data={
                'expected_status': WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
                'expected_revision': generation.revision,
                'description': '登录账号 resume-user resume-password 后继续。',
            },
            context={'generation': generation},
        )
        serializer.is_valid(raise_exception=True)
        self.assertEqual(serializer.validated_data['temporary_credentials'], {
            'username': 'resume-user',
            'password': 'resume-password',
        })

    def test_dispatch_failure_clears_credentials_and_marks_generation_failed(self):
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            side_effect=RuntimeError('broker unavailable'),
        ):
            response = self.api_request(self.user, {
                'description': '检查目标页面。',
                'environment_id': self.environment.id,
                'start_path': '/items',
                'temporary_credentials': {
                    'username': 'test-user', 'password': 'temporary-value',
                },
            })
        self.assertEqual(response.status_code, 503, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertIsNone(get_temporary_credentials(generation.pk))
        self.assertNotIn('temporary-value', str(response.data))
        self.assertNotIn('temporary-value', str(generation.exploration_snapshot))

    def test_credential_cache_failure_rolls_back_generation(self):
        payload = {
            'description': '检查目标页面。',
            'environment_id': self.environment.id,
            'start_path': '/items',
            'temporary_credentials': {
                'username': 'test-user', 'password': 'temporary-value',
            },
        }
        request = self.factory.post('/script-generations/', payload, format='json')
        force_authenticate(request, user=self.user)
        request.user = self.user
        serializer = WebUIScriptGenerationCreateSerializer(
            data=payload, context={'request': request, 'project': self.project},
        )
        serializer.is_valid(raise_exception=True)
        with patch(
            'web_testing.serializers.store_temporary_credentials',
            side_effect=RuntimeError('cache unavailable'),
        ):
            with self.assertRaises(RuntimeError):
                serializer.save()
        self.assertFalse(WebUIScriptGeneration.objects.filter(project=self.project).exists())

    def test_transitions_worker_claims_and_v4_trace_retry_are_idempotent(self):
        generation = self.make_generation()
        with self.assertRaises(GenerationTransitionError):
            transition_generation(
                generation.pk, WebUIScriptGeneration.Status.GENERATING,
            )
        transition_generation(
            generation.pk, WebUIScriptGeneration.Status.NORMALIZING, progress=5,
        )
        transition_generation(
            generation.pk, WebUIScriptGeneration.Status.PREFLIGHTING, progress=25,
        )
        self.assertIsNotNone(claim_generation_worker(generation.pk, 'worker-1'))
        self.assertIsNone(claim_generation_worker(generation.pk, 'worker-1'))

        generation.refresh_from_db()
        generation.status = WebUIScriptGeneration.Status.FAILED
        generation.error_code = 'MODEL_UNAVAILABLE'
        generation.revision = 7
        generation.exploration_snapshot = {'schema_version': 3}
        generation.save(update_fields=[
            'status', 'error_code', 'revision', 'exploration_snapshot',
        ])
        with self.assertRaises(GenerationResolutionConflict):
            prepare_trace_generation_retry(generation.pk, expected_revision=7)

        generation.exploration_snapshot = {'schema_version': 4}
        generation.save(update_fields=['exploration_snapshot'])
        retried = prepare_trace_generation_retry(
            generation.pk, expected_revision=7,
        )
        self.assertEqual(
            (retried.status, retried.revision),
            (WebUIScriptGeneration.Status.GENERATING, 8),
        )
        self.assertIsNotNone(claim_trace_generation_retry(retried.pk, 'trace-worker'))
        self.assertIsNone(claim_trace_generation_retry(retried.pk, 'trace-worker'))

    def test_cancel_is_durable_idempotent_and_clears_credentials(self):
        generation = self.make_generation()
        store_temporary_credentials(generation.pk, {
            'username': 'test-user', 'password': 'temporary-value',
        })
        cancelled = cancel_generation(generation.pk)
        again = cancel_generation(generation.pk)
        self.assertEqual(cancelled.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(again.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertIsNone(get_generation_temporary_credentials(generation.pk))

    def test_terminal_transition_clears_credentials_and_event_dedupes_or_retries(self):
        generation = self.make_generation()
        store_temporary_credentials(generation.pk, {
            'username': 'test-user', 'password': 'temporary-value',
        })
        transition_generation(
            generation.pk, WebUIScriptGeneration.Status.FAILED,
            error_code='MODEL_UNAVAILABLE',
        )
        self.assertIsNone(get_generation_temporary_credentials(generation.pk))

        event = SimpleNamespace(
            pk=f'event-{uuid4()}', user_id=self.user.id, celery_task_id='',
            status=WebUIScriptGeneration.Status.READY,
            error_code='', error_message='',
        )
        key = f'webui:script-generation:terminal-event:{event.pk}'
        cache.delete(key)
        with patch(
            'web_testing.generation_events.websocket_message_service.send_task_completed',
            side_effect=[False, True],
        ) as send:
            publish_terminal(event)
            publish_terminal(event)
            publish_terminal(event)
        self.assertEqual(send.call_count, 2)
        cache.delete(key)

    def test_unknown_exploration_exception_is_logged_and_returns_internal_code(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation(
            start_path='/items', target_url_safe='https://web.example.test/items',
        )
        plan = ScenarioPlan.model_validate(plan_payload())
        preflight = SimpleNamespace(outcome='continue', warnings=[], mcp_config={})
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement', return_value=plan,
        ), patch(
            'web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight,
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.generation_orchestrator.MCPPageExplorer',
        ) as explorer_class, patch(
            'web_testing.generation_orchestrator.logger.exception',
        ) as logged_exception:
            explorer = Mock()
            explorer.explore_until_complete.side_effect = RuntimeError('mcp crashed')
            explorer_class.return_value = explorer
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        self.assertEqual(result['error_code'], 'INTERNAL_EXPLORATION_ERROR')
        self.assertEqual(result['status'], 'failed')
        logged_exception.assert_called_once()

    def test_normalization_contract_failure_logs_only_safe_diagnostics(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()
        error = GenerationContractError('scenario_plan_invalid', diagnostics=(
            {'path': 'assertion_requirements.[item].kind', 'type': 'value_error', 'stage': 'contract_validation'},
        ))
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement', side_effect=error,
        ) as normalize, patch('web_testing.generation_orchestrator.logger.warning') as logged_warning:
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        self.assertEqual(result['error_code'], 'MODEL_OUTPUT_INVALID')
        normalize.assert_called_once()
        diagnostic_calls = [
            call for call in logged_warning.call_args_list
            if call.args and call.args[0].startswith('WebUI v4 ScenarioPlan rejected:')
        ]
        self.assertEqual(len(diagnostic_calls), 1)
        self.assertEqual(diagnostic_calls[0].args[2], list(error.diagnostics))

    def test_generation_exposes_normalizing_state_before_model_call(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()
        observed = {}

        def inspect_persisted_state(*args, **kwargs):
            current = WebUIScriptGeneration.objects.get(pk=generation.pk)
            observed.update({
                'status': current.status,
                'stage': current.current_stage,
                'progress': current.progress,
                'started_at': current.started_at,
            })
            raise GenerationContractError('scenario_plan_invalid')

        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            side_effect=inspect_persisted_state,
        ), patch(
            'web_testing.generation_orchestrator.publish_stage_changed',
        ) as publish_stage:
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        self.assertEqual(result['error_code'], 'MODEL_OUTPUT_INVALID')
        self.assertEqual(observed['status'], WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(observed['stage'], WebUIScriptGeneration.Stage.NORMALIZING)
        self.assertEqual(observed['progress'], 10)
        self.assertIsNotNone(observed['started_at'])
        published_generation = publish_stage.call_args.args[0]
        self.assertEqual(published_generation.status, WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(published_generation.progress, 10)

    def test_normalizing_retries_one_stateless_model_error_then_continues(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()
        plan = ScenarioPlan.model_validate(plan_payload())
        transient = OpenAIError('模型服务暂时不可用，请稍后重试')
        preflight = SimpleNamespace(
            outcome='needs_confirmation', error_code='INPUT_AMBIGUOUS',
            message='需要确认测试范围。', warnings=[],
        )
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            side_effect=[transient, plan],
        ) as normalize, patch(
            'web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight,
        ):
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        self.assertEqual(normalize.call_count, 2)
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_CONFIRMATION)

    def test_normalizing_two_stateless_model_errors_finish_failed_and_publish_terminal(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()

        def stream_error():
            return OpenAIError('模型服务暂时不可用，请稍后重试')

        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            side_effect=[stream_error(), stream_error()],
        ) as normalize, patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal:
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        generation.refresh_from_db()
        self.assertEqual(normalize.call_count, 2)
        self.assertEqual(result['error_code'], 'MODEL_SERVICE_ERROR')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertEqual(generation.progress, 100)
        publish_terminal.assert_called_once()

    def test_normalizing_authentication_error_does_not_retry(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()
        response = httpx.Response(
            401,
            request=httpx.Request('POST', 'https://llm.example.test/v1/chat/completions'),
        )
        authentication_error = APIStatusError(
            'invalid credentials', response=response,
            body={'type': 'authentication_error'},
        )
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement',
            side_effect=authentication_error,
        ) as normalize:
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        generation.refresh_from_db()
        normalize.assert_called_once()
        self.assertEqual(result['error_code'], 'MODEL_AUTHENTICATION_FAILED')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertEqual(generation.progress, 100)

    def test_generation_task_guard_marks_unknown_exception_failed_and_publishes_terminal(self):
        from .tasks import generate_webui_script_generation_task

        generation = self.make_generation()
        with patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal, patch(
            'web_testing.generation_orchestrator.run_generation',
            side_effect=RuntimeError('unexpected failure'),
        ), patch(
            'web_testing.tasks.logger.exception',
        ) as logged_exception:
            result = generate_webui_script_generation_task.apply(
                args=(str(generation.pk),), task_id='task-unknown',
            ).get()

        generation.refresh_from_db()
        self.assertEqual(result['error_code'], 'INTERNAL_GENERATION_ERROR')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertEqual(generation.progress, 100)
        publish_terminal.assert_called_once()
        logged_exception.assert_called_once()

    def test_cancelled_exploration_never_compiles_a_partial_snapshot(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation(
            start_path='/items', target_url_safe='https://web.example.test/items',
        )
        plan, trace = replay_fixture(verify_cleanup=False)
        preflight = SimpleNamespace(outcome='continue', warnings=[], mcp_config={})
        with patch(
            'web_testing.generation_orchestrator.normalize_requirement', return_value=plan,
        ), patch(
            'web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight,
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.generation_orchestrator.MCPPageExplorer',
        ) as explorer_class, patch(
            'web_testing.generation_orchestrator._compile_persisted',
        ) as compile_persisted:
            explorer = Mock()
            explorer.explore_until_complete.side_effect = MCPPageExplorerError(
                'TASK_CANCELLED', '用户已取消任务。', snapshot=trace,
            )
            explorer_class.return_value = explorer
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(result['error_code'], 'TASK_CANCELLED')
        compile_persisted.assert_not_called()

    def test_saved_marker_requires_matching_generation_reference(self):
        test_case = SimpleNamespace(generation_metadata={})
        generation = SimpleNamespace(
            pk=uuid4(), test_case_id=1, test_case=test_case,
            workspace={}, revision=0, script_draft='',
        )
        test_case.generation_metadata = {
            'generation_ref': generation_reference(generation),
        }
        self.assertTrue(is_generation_saved(generation))
        test_case.generation_metadata['generation_ref'] = 'wrong'
        self.assertFalse(is_generation_saved(generation))

    def test_unverified_cleanup_finishes_as_needs_review_not_ready(self):
        from .generation_orchestrator import _compile_persisted

        plan, trace = replay_fixture(verify_cleanup=False)
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.GENERATING,
            current_stage=WebUIScriptGeneration.Stage.GENERATING,
            scenario_spec=plan.model_dump(mode='json'),
        )
        with patch(
            'web_testing.generation_orchestrator.publish_stage_changed',
        ), patch('web_testing.generation_orchestrator.publish_terminal'):
            result = _compile_persisted(generation, plan, trace)
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(result['quality_status'], 'blocked')
        self.assertEqual(generation.error_code, 'FINALIZATION_REQUIRED')
        self.assertEqual(generation.script_draft, '')

    def test_compile_persisted_generates_dynamic_input_script_and_workspace_without_value_leak(self):
        from .generation_orchestrator import _compile_persisted

        actual_value = 'dynamic-value-should-not-persist@example.com'
        plan = ScenarioPlan.model_validate({
            **plan_payload(),
            'success_criteria': ['结果区域可见'],
            'assertion_requirements': [{
                'assertion_id': 'A1', 'criterion_index': 0, 'phase': 'main',
                'kind': 'visible', 'input_ref': '', 'literal': '',
            }],
            'input_refs': [],
        })
        recorder = ExplorationTraceRecorder('/items')
        recorder.configure_plan(plan)
        recorder.configure_runtime({}, plan.input_sources())
        dynamic = recorder.declare_dynamic_input(
            value_kind='email', runtime_value=actual_value,
        )
        record(recorder, 'navigate', 'playwright_navigate', {
            'url': 'https://web.example.test/items',
        }, 'URL: https://web.example.test/items')
        record(recorder, 'fill', 'playwright_fill', {
            'selector': '#dynamic', 'value': actual_value,
        }, 'filled')
        record(recorder, 'assert', 'playwright_get_visible_html', {
            'selector': '#result',
        }, '<main>saved</main>')
        recorder.candidate_summary()
        recorder.finalize_path(
            main_actions=[FinalizedAction(event_id='E000002', step_name='填写动态输入')],
            assertions=[FinalizedAssertion(assertion_id='A1', event_id='E000003')],
            cleanup_actions=[],
        )
        trace = recorder.build(tool_stats={'total_tool_calls': 3})
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.GENERATING,
            current_stage=WebUIScriptGeneration.Stage.GENERATING,
            scenario_spec=plan.model_dump(mode='json'),
        )
        with patch('web_testing.generation_orchestrator.publish_stage_changed'), patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ):
            result = _compile_persisted(generation, plan, trace)
        generation.refresh_from_db()
        self.assertIn(result['status'], {
            WebUIScriptGeneration.Status.READY,
            WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
        })
        self.assertIn('DYNAMIC_INPUT_1', generation.script_draft)
        self.assertIn('@example.com', generation.script_draft)
        self.assertNotIn(actual_value, generation.script_draft)
        self.assertNotIn(actual_value, str(generation.exploration_snapshot))
        variables = {item['name']: item for item in generation.workspace['variables']}
        self.assertEqual(variables[dynamic.name]['value'], '')
        self.assertFalse(variables[dynamic.name]['is_secret'])
