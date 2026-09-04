"""Migrated v5 persistence, concurrency, API and quality regressions.

The historical filename remains to demonstrate that broadly useful coverage
was migrated rather than discarded. No legacy trace or Goal contract is accepted.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
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
from .script_exploration_agent import ScriptExplorationResult
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

    def test_transitions_worker_claims_and_v5_artifact_retry_are_idempotent(self):
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

        generation.exploration_snapshot = {
            'schema_version': 5,
            'events': [{'event_id': 'E1', 'action': 'navigate'}],
            'tool_stats': {'total_tool_calls': 1},
            'artifact': {
                'revision': 1, 'completion': 'partial',
                'completed_steps': ['打开页面'], 'remaining_steps': ['补充断言'],
                'variables': [],
            },
        }
        generation.script_draft = '''async def run(page, variables):
    await page.goto('/items')
    # AITS_PENDING_ASSERTION: {"reason":"尚待补充页面断言"}
'''
        generation.save(update_fields=['exploration_snapshot', 'script_draft'])
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
            status=WebUIScriptGeneration.Status.READY, revision=0,
            error_code='', error_message='',
        )
        key = f'webui:script-generation:terminal-event:{event.pk}:{event.revision}'
        cache.delete(key)
        with patch(
            'web_testing.generation_events.websocket_message_service.send_task_completed',
            side_effect=[False, True, True],
        ) as send:
            publish_terminal(event)
            publish_terminal(event)
            publish_terminal(event)
            self.assertEqual(send.call_count, 2)
            event.revision = 1
            publish_terminal(event)
        self.assertEqual(send.call_count, 3)
        cache.delete(key)

    def _v5_snapshot(self, *, revision=1, completion='complete', variables=None, events=None):
        return {
            'schema_version': 5,
            'events': events if events is not None else [{'event_id': 'E1', 'action': 'navigate'}],
            'page_states': [], 'locator_evidence': [],
            'tool_stats': {'total_tool_calls': 1},
            'artifact': {
                'revision': revision, 'completion': completion,
                'completed_steps': ['打开页面'],
                'remaining_steps': [] if completion == 'complete' else ['人工补充断言'],
                'variables': variables or [],
            },
        }

    def _active_agent_generation(self, **overrides):
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.EXPLORING,
            current_stage=WebUIScriptGeneration.Stage.EXPLORING,
            celery_task_id='agent-task',
            **overrides,
        )
        generation.workspace = {
            '_agent_run': {
                'generation_revision': generation.revision,
                'task_id': 'agent-task',
            },
        }
        generation.save(update_fields=['workspace'])
        return generation

    def _run_v5_agent(self, generation, agent_type, *, preflight=None):
        from .generation_orchestrator import run_generation

        preflight = preflight or SimpleNamespace(outcome='continue', warnings=[], mcp_config={})
        with patch(
            'web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight,
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', agent_type,
        ):
            return run_generation(str(generation.pk), celery_task_id='task-1')

    def test_agent_exception_persists_needs_review_and_publishes_terminal(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation(
            start_path='/items', target_url_safe='https://web.example.test/items',
        )
        class BrokenAgent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                raise RuntimeError('agent crashed')

        with patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(outcome='continue', warnings=[], mcp_config={}),
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', BrokenAgent,
        ), patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal:
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        generation.refresh_from_db()
        self.assertEqual(result['error_code'], 'INTERNAL_GENERATION_ERROR')
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)
        publish_terminal.assert_called_once()

    def test_local_schema5_brief_exposes_normalizing_stage_without_calling_normalizer(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation()
        observed = {}

        def inspect_state(_generation):
            current = WebUIScriptGeneration.objects.get(pk=generation.pk)
            observed.update({
                'status': current.status, 'stage': current.current_stage,
                'progress': current.progress, 'started_at': current.started_at,
            })
            return {
                'schema_version': 5, 'title': '本地 brief', 'objective': '检查目标页面。',
                'original_user_target': '检查目标页面。', 'instructions': ['检查目标页面。'],
                'credentials_required': False,
            }

        with patch(
            'web_testing.generation_orchestrator._brief_for_generation', side_effect=inspect_state,
        ), patch(
            'web_testing.requirement_normalizer.RequirementNormalizer.normalize',
        ) as normalizer, patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(
                outcome='needs_confirmation', error_code='INPUT_AMBIGUOUS',
                message='需要确认测试范围。', warnings=[],
            ),
        ), patch('web_testing.generation_orchestrator.publish_stage_changed') as publish_stage:
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_CONFIRMATION)
        normalizer.assert_not_called()
        self.assertEqual(observed['status'], WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(observed['stage'], WebUIScriptGeneration.Stage.NORMALIZING)
        self.assertEqual(observed['progress'], 10)
        self.assertIsNotNone(observed['started_at'])
        published_generation = publish_stage.call_args.args[0]
        self.assertEqual(published_generation.status, WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(published_generation.progress, 10)

    def test_agent_model_authentication_failure_is_terminal_without_normalizer_retry(self):
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
        class AuthenticationFailingAgent:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                type(self).calls += 1
                raise authentication_error

        with patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(outcome='continue', warnings=[], mcp_config={}),
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', AuthenticationFailingAgent,
        ), patch(
            'web_testing.requirement_normalizer.RequirementNormalizer.normalize',
        ) as normalizer, patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal:
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        generation.refresh_from_db()
        self.assertEqual(AuthenticationFailingAgent.calls, 1)
        normalizer.assert_not_called()
        self.assertEqual(result['error_code'], 'MODEL_AUTHENTICATION_FAILED')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.progress, 100)
        publish_terminal.assert_called_once()

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

    def test_real_cancelled_generation_returns_terminal_state_without_starting_agent(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation(
            start_path='/items', target_url_safe='https://web.example.test/items',
        )
        cancelled = cancel_generation(generation.pk)

        class NeverStartedAgent:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                type(self).calls += 1
                raise AssertionError('已取消的任务不应启动脚本 Agent')

        with patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', NeverStartedAgent,
        ):
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        generation.refresh_from_db()
        self.assertEqual(cancelled.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(NeverStartedAgent.calls, 0)
        self.assertEqual(
            result['status'], WebUIScriptGeneration.Status.CANCELLED,
            {'result': result, 'db_status': generation.status, 'error_code': generation.error_code},
        )
        self.assertEqual(result['error_code'], 'TASK_CANCELLED')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(generation.script_draft, '')

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

    def test_partial_agent_draft_persists_without_finalization_gate(self):
        from .generation_orchestrator import _persist_agent_result

        generation = self._active_agent_generation()
        draft = '''"""场景：查看详情。目标：先保留待补充的断言草稿。"""
async def run(page, variables):
    # 打开详情页
    await page.goto('/items')
    # AITS_PENDING_ASSERTION: {"reason":"当前证据不足以确认详情字段"}
'''
        with patch('web_testing.generation_orchestrator.publish_terminal'):
            result = _persist_agent_result(
                generation, task_id='agent-task', script_draft=draft,
                snapshot=self._v5_snapshot(completion='partial'), completion='partial',
                error_code='', error_message='', final_message='已保留待补充草稿。',
            )
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertIn('AITS_PENDING_ASSERTION:', generation.script_draft)
        self.assertEqual(generation.exploration_snapshot['schema_version'], 5)
        self.assertEqual(generation.exploration_snapshot['final_message'], '已保留待补充草稿。')
        self.assertNotEqual(generation.error_code, 'FINALIZATION_REQUIRED')

    def test_agent_artifact_preserves_fixed_and_dynamic_variables_without_finalization(self):
        from .generation_orchestrator import _persist_agent_result

        generation = self._active_agent_generation()
        draft = '''"""场景：填写联系人。目标：验证固定标题与动态邮箱。"""
from playwright.async_api import expect

async def run(page, variables):
    # 打开联系人页面
    await page.goto('/items')
    fixed_title = '新建联系人'
    email = variables.get('DYNAMIC_EMAIL', '')
    # 填写运行时邮箱
    await page.locator('#email').fill(email)
    await expect(page.locator('#title')).to_have_text(fixed_title)
'''
        variables = [
            {'name': 'FIXED_TITLE', 'value': '新建联系人', 'required': False, 'is_secret': False},
            {'name': 'DYNAMIC_EMAIL', 'value': '', 'required': True, 'is_secret': False},
        ]
        with patch('web_testing.generation_orchestrator.publish_terminal'):
            result = _persist_agent_result(
                generation, task_id='agent-task', script_draft=draft,
                snapshot=self._v5_snapshot(completion='partial', variables=variables), completion='partial',
                error_code='', error_message='', final_message='草稿保存完成。',
            )
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertIn("fixed_title = '新建联系人'", generation.script_draft)
        self.assertIn("variables.get('DYNAMIC_EMAIL', '')", generation.script_draft)
        variables = {item['name']: item for item in generation.workspace['variables']}
        self.assertEqual(variables['FIXED_TITLE']['value'], '新建联系人')
        self.assertTrue(variables['DYNAMIC_EMAIL']['required'])
        self.assertEqual(generation.exploration_snapshot['artifact']['revision'], 1)


class V5RunningCancellationRegressionTests(TransactionTestCase):
    """Exercise real checkpoint/cancel ordering outside TestCase's transaction."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='v5-running-cancel-owner', password='test-password',
        )
        self.project = Project.objects.create(
            name='V5 running cancellation', project_type='web', owner=self.user,
            created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='Web',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'}, is_active=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='Test provider',
            api_key='key', base_url='https://llm.example.test', model_name='v5-model',
            is_active=True, created_by=self.user,
        )

    def test_running_cancel_keeps_last_checkpoint_and_ignores_late_agent_result(self):
        from .generation_orchestrator import _persist_agent_result, run_generation

        def run_agent_coroutine_in_test_thread(awaitable):
            """Keep thread-sensitive ORM callbacks on SQLite's test connection."""
            async def wait_for_result():
                return await awaitable

            return async_to_sync(wait_for_result)()

        checkpoint_draft = '''"""场景：查看详情。目标：保存可恢复的部分草稿。"""
async def run(page, variables):
    # 打开详情页
    await page.goto('/items')
    # AITS_PENDING_ASSERTION: {"reason":"尚未确认详情字段"}
'''
        late_draft = '''"""过期草稿，不应覆盖 checkpoint。"""
async def run(page, variables):
    await page.goto('/stale-result')
'''
        checkpoint_payload = {
            'script_draft': checkpoint_draft,
            'snapshot': {
                'schema_version': 5,
                'events': [{'event_id': 'checkpoint', 'action': 'navigate'}],
                'page_states': [], 'locator_evidence': [],
                'tool_stats': {'total_tool_calls': 1},
                'artifact': {
                    'revision': 1, 'completion': 'partial',
                    'completed_steps': ['打开详情页'],
                    'remaining_steps': ['补充详情断言'], 'variables': [],
                },
            },
        }
        late_snapshot = {
            'schema_version': 5,
            'events': [{'event_id': 'late', 'action': 'navigate'}],
            'page_states': [], 'locator_evidence': [],
            'tool_stats': {'total_tool_calls': 2},
            'artifact': {
                'revision': 2, 'completion': 'complete',
                'completed_steps': ['错误的完成结果'], 'remaining_steps': [], 'variables': [],
            },
        }
        generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user, environment=self.environment,
            description_safe='探索详情页并保存当前可用草稿。',
            start_path='/items', target_url_safe='https://web.example.test/items',
            model_info={'config_id': self.model.id},
        )

        class CheckpointThenCancelAgent:
            checkpoint_saved = False

            def __init__(self, **kwargs):
                self.generation_id = kwargs['generation_id']
                self.checkpoint_callback = kwargs['checkpoint_callback']

            async def generate(self, **_kwargs):
                type(self).checkpoint_saved = await sync_to_async(
                    self.checkpoint_callback, thread_sensitive=True,
                )(checkpoint_payload)
                await sync_to_async(cancel_generation, thread_sensitive=True)(self.generation_id)
                return ScriptExplorationResult(
                    script_draft=late_draft, snapshot=late_snapshot,
                    completion='complete', final_message='过期结果不应落库。',
                )

        with patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(outcome='continue', warnings=[], mcp_config={}),
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=Mock()),
        ), patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', CheckpointThenCancelAgent,
        ), patch(
            'web_testing.generation_orchestrator.asyncio',
            SimpleNamespace(run=run_agent_coroutine_in_test_thread),
        ), patch(
            'web_testing.generation_orchestrator._persist_agent_result',
            wraps=_persist_agent_result,
        ) as persist_final, patch(
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal:
            result = run_generation(str(generation.pk), celery_task_id='running-cancel-task')

        generation.refresh_from_db()
        self.assertTrue(CheckpointThenCancelAgent.checkpoint_saved)
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(result['error_code'], 'TASK_CANCELLED')
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(generation.script_draft, checkpoint_draft)
        self.assertEqual(generation.exploration_snapshot['artifact']['revision'], 1)
        self.assertNotIn('stale-result', generation.script_draft)
        self.assertNotIn('过期结果不应落库。', generation.exploration_snapshot.get('final_message', ''))
        persist_final.assert_not_called()
        publish_terminal.assert_not_called()
