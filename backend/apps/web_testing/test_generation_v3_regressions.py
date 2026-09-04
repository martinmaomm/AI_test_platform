"""Migrated v5 persistence, concurrency, API and quality regressions.

The historical filename remains to demonstrate that broadly useful coverage
was migrated rather than discarded. No legacy trace or Goal contract is accepted.
"""

from __future__ import annotations

import ast
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
from projects.models import Project

from .generation_contracts import GenerationTransitionError
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
    prepare_trace_generation_retry,
    transition_generation,
)
from .generation_save_state import generation_reference, is_generation_saved
from .models import WebUIScriptGeneration
from .script_exploration_agent import ScriptExplorationResult
from .serializers import (
    WebUIScriptGenerationCreateSerializer,
    WebUIScriptGenerationResolveSerializer,
)
from .target_urls import extract_target_url
from .views import WebUIScriptGenerationCreateView


class V4SafeOutputPathRegressionTests(SimpleTestCase):
    def test_explicit_target_url_and_high_risk_guards(self):
        with self.assertRaisesRegex(ValueError, '目标网址'):
            extract_target_url('检查 https://one.example.test 与 https://two.example.test。')
        self.assertEqual(
            extract_target_url('目标网址：https://web.example.test/items?tab=all#details\n检查详情。'),
            'https://web.example.test/items?tab=all#details',
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


class V5EntrypointBoundaryRegressionTests(SimpleTestCase):
    def test_generation_entrypoint_imports_exclude_retired_v4_modules(self):
        source = Path(__file__).with_name('generation_orchestrator.py').read_text(encoding='utf-8')
        imports = {
            node.module.rsplit('.', 1)[-1]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(imports & {
            'requirement_normalizer', 'script_generator', 'replay_plan', 'script_quality',
        })


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
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai',
            provider_name='Test provider', api_key='key',
            base_url='https://llm.example.test', model_name='v4-model',
            is_active=True, created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def make_generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user,
            'description_safe': '检查目标页面。',
            'target_url': 'https://web.example.test/items',
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
            'description': '目标网址：https://web.example.test/items\n检查目标页面。',
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
                'description': '目标网址：https://web.example.test/items\n检查目标页面。',
                'model_config_id': other.id,
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.model_info['config_id'], other.id)
        delay.assert_called_once_with(str(generation.pk))

        other.is_active = False
        other.save(update_fields=['is_active'])
        rejected = self.api_request(self.user, {
            'description': '目标网址：https://web.example.test/items\n检查目标页面。',
            'model_config_id': other.id,
        })
        self.assertEqual(rejected.status_code, 400)

    def test_create_and_resolution_preserve_raw_login_description(self):
        description = '目标网址：https://web.example.test/items\n使用用户名 test-user、密码 test-password 和 token=token-for-test 登录后检查首页。'
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='plaintext-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': description,
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

    def test_create_keeps_login_text_without_structured_credential_cache(self):
        description = '目标网址：https://web.example.test/items\n登录账号 inline-user inline-password 后检查首页。'
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='inline-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': description,
            })
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.description_safe, description)
        self.assertEqual(generation.target_url, 'https://web.example.test/items')
        self.assertFalse(hasattr(generation, 'credentials_required'))

    def test_create_rejects_removed_structured_credential_field(self):
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='structured-credential-task'),
        ):
            response = self.api_request(self.user, {
                'description': '目标网址：https://web.example.test/items\n登录账号 inline-user inline-password 后检查首页。',
                'temporary_credentials': {
                    'username': 'form-user',
                    'password': 'form-password',
                },
            })
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('temporary_credentials', response.data['error']['details'])

    def test_resolution_description_updates_target_url(self):
        generation = self.make_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
        )
        serializer = WebUIScriptGenerationResolveSerializer(
            data={
                'expected_status': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
                'expected_revision': generation.revision,
                'description': '目标网址：https://web.example.test/orders?tab=open#detail\n登录后继续。',
            },
            context={'generation': generation},
        )
        serializer.is_valid(raise_exception=True)
        self.assertEqual(
            serializer.validated_data['target_url'],
            'https://web.example.test/orders?tab=open#detail',
        )

    def test_dispatch_failure_marks_generation_failed_without_credential_cache(self):
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            side_effect=RuntimeError('broker unavailable'),
        ):
            response = self.api_request(self.user, {
                'description': '目标网址：https://web.example.test/items\n登录账号 test-user temporary-value 后检查。',
            })
        self.assertEqual(response.status_code, 503, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertIn('temporary-value', generation.description_safe)
        self.assertFalse(hasattr(generation, 'credentials_provided'))

    def test_removed_credential_field_does_not_create_generation(self):
        payload = {
            'description': '目标网址：https://web.example.test/items\n检查目标页面。',
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
        self.assertFalse(serializer.is_valid())
        self.assertIn('temporary_credentials', serializer.errors)
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

    def test_cancel_is_durable_and_idempotent(self):
        generation = self.make_generation()
        cancelled = cancel_generation(generation.pk)
        again = cancel_generation(generation.pk)
        self.assertEqual(cancelled.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertEqual(again.status, WebUIScriptGeneration.Status.CANCELLED)

    def test_terminal_transition_and_event_deduplication(self):
        generation = self.make_generation()
        transition_generation(
            generation.pk, WebUIScriptGeneration.Status.FAILED,
            error_code='MODEL_UNAVAILABLE',
        )

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

        generation = self.make_generation(target_url='https://web.example.test/items')
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

    def test_local_schema5_brief_exposes_normalizing_stage_without_calling_llm_manager(self):
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
            }

        with patch(
            'web_testing.generation_orchestrator._brief_for_generation', side_effect=inspect_state,
        ), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
        ) as llm_manager, patch(
            'web_testing.generation_orchestrator.run_safety_preflight',
            return_value=SimpleNamespace(
                outcome='needs_confirmation', error_code='INPUT_AMBIGUOUS',
                message='需要确认测试范围。', warnings=[],
            ),
        ), patch('web_testing.generation_orchestrator.publish_stage_changed') as publish_stage:
            result = run_generation(str(generation.pk), celery_task_id='task-1')
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_CONFIRMATION)
        llm_manager.assert_not_called()
        self.assertEqual(observed['status'], WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(observed['stage'], WebUIScriptGeneration.Stage.NORMALIZING)
        self.assertEqual(observed['progress'], 10)
        self.assertIsNotNone(observed['started_at'])
        published_generation = publish_stage.call_args.args[0]
        self.assertEqual(published_generation.status, WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(published_generation.progress, 10)

    def test_agent_model_authentication_failure_is_terminal(self):
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
            'web_testing.generation_orchestrator.publish_terminal',
        ) as publish_terminal:
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        generation.refresh_from_db()
        self.assertEqual(AuthenticationFailingAgent.calls, 1)
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

        generation = self.make_generation(target_url='https://web.example.test/items')
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
            project=self.project, user=self.user,
            description_safe='目标网址：https://web.example.test/items\n探索详情页并保存当前可用草稿。',
            target_url='https://web.example.test/items',
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
