"""Offline v3 regressions for GoalPlan generation invariants.

These tests deliberately exercise only the GoalPlan/callback-ledger/replay
contract.  They do not retain the removed v2 CRUD-step schema.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from ai_core.webui_playwright_agent import MCP_MAX_STEPS
from projects.models import Environment, Project

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationTraceRecorder, GoalRun, evaluate_goal_events
from .generation_contracts import GenerationTransitionError, GoalPlan
from .generation_events import publish_terminal
from .generation_preflight import environment_credentials, exploration_requires_write_confirmation
from .generation_repository import (
    GenerationResolutionConflict,
    claim_generation_worker,
    claim_trace_generation_retry,
    get_generation_temporary_credentials,
    prepare_trace_generation_retry,
    transition_generation,
)
from .generation_save_state import generation_reference, is_generation_saved
from .generation_security import get_temporary_credentials
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError, ReadOnlyMCPBrowserToolGuard
from .models import WebUIScriptGeneration
from .replay_plan import PythonReplayCompiler, ReplayPlanner
from .script_quality import evaluate_script
from .serializers import WebUIScriptGenerationCreateSerializer
from .views import WebUIScriptGenerationCreateView


def plan_payload(*, include_external: bool = True):
    goals = [
        {'id': 'G1', 'kind': 'setup', 'objective': '观察用户页面', 'completion_criteria': '页面已观察', 'side_effect': 'none'},
        {'id': 'G2', 'kind': 'exercise', 'objective': '操作本轮测试数据', 'completion_criteria': '已确认结果', 'side_effect': 'test_data'},
        {
            'id': 'G3', 'kind': 'cleanup', 'objective': '清理本轮测试数据',
            'completion_criteria': '已确认清理结果',
            'verification': {'mode': 'visible'},
            'side_effect': 'none', 'cleanup_for_goal_ids': ['G2'],
        },
    ]
    if include_external:
        goals.extend([
            {
                'id': 'G4', 'kind': 'verify', 'objective': '观察外部结果',
                'completion_criteria': '外部结果已观察',
                'verification': {'mode': 'visible'}, 'side_effect': 'external',
            },
            {
                'id': 'G5', 'kind': 'verify', 'objective': '观察未知结果',
                'completion_criteria': '未知结果已观察',
                'verification': {'mode': 'visible'}, 'side_effect': 'unknown',
            },
        ])
    return {
        'schema_version': 3,
        'title': '用户数据生命周期',
        'objective': '验证目标范围内的页面行为。',
        'goals': goals,
        'forbidden_actions': [],
        'credentials_required': False,
        'discovery_notes': [],
        'ambiguities': [],
        'risk_level': 'medium',
    }


def replay_fixture():
    plan = GoalPlan.model_validate(plan_payload(include_external=False))
    recorder = ExplorationTraceRecorder('/users')
    recorder.set_active_goal('G1')
    recorder.on_tool_start({'name': 'playwright_navigate'}, '', run_id='nav', inputs={'url': '/users'})
    recorder.on_tool_end('navigated /users', run_id='nav')
    recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='observe-1', inputs={'path': '/users'})
    recorder.on_tool_end('用户列表', run_id='observe-1')
    recorder.record_goal_run(GoalRun(goal_id='G1', status='completed', selected_event_ids=['E000001', 'E000002']))
    recorder.set_active_goal('G2')
    recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='write', inputs={'testid': 'create-test-user'})
    recorder.on_tool_end('created', run_id='write')
    recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='observe-2', inputs={'path': '/users'})
    recorder.on_tool_end('已创建本轮数据', run_id='observe-2')
    recorder.record_goal_run(GoalRun(goal_id='G2', status='completed', selected_event_ids=['E000003', 'E000004']))
    recorder.set_active_goal(
        'G3', verification=plan.goals[2].verification.model_dump(mode='json'),
    )
    recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='cleanup', inputs={'testid': 'remove-test-user'})
    recorder.on_tool_end('removed', run_id='cleanup')
    recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='observe-3', inputs={'path': '/users'})
    recorder.on_tool_end('已清理本轮数据', run_id='observe-3')
    recorder.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='verify-cleanup', inputs={'selector': '#result'})
    recorder.on_tool_end('<section id="result">empty</section>', run_id='verify-cleanup')
    recorder.record_goal_run(GoalRun(goal_id='G3', status='completed', selected_event_ids=['E000005', 'E000006'], assertion_event_ids=['E000007']))
    return plan, recorder.build(tool_stats={'total_tool_calls': 7})


class V3ExplorationPolicyRegressionTests(SimpleTestCase):
    def test_environment_credentials_and_high_risk_negation_are_generic(self):
        environment = SimpleNamespace(config={'variables': {
            'UI_TEST_USERNAME': 'env-user', 'UI_TEST_PASSWORD': 'env-password',
        }})
        self.assertEqual(environment_credentials(environment), {
            'username': 'env-user', 'password': 'env-password',
        })
        self.assertFalse(exploration_requires_write_confirmation('不要执行付款、发布或上传操作。'))
        self.assertTrue(exploration_requires_write_confirmation('完成付款操作并确认结果。'))

    def test_namespace_goal_scope_and_read_only_override(self):
        plan = GoalPlan.model_validate(plan_payload())
        first = ExplorationPolicy.for_plan(plan, generation_id='run-1', user_constraints='')
        second = ExplorationPolicy.for_plan(plan, generation_id='run-1', user_constraints='')
        self.assertTrue(first.namespace.startswith('aits-explore-run-1-'))
        self.assertNotEqual(first.namespace, second.namespace)
        self.assertEqual(first.data_scope, 'goal_scoped')

        expected = {'G1': False, 'G2': True, 'G3': True, 'G4': False, 'G5': False}
        for goal_id, may_write in expected.items():
            first.set_active_goal(goal_id)
            self.assertEqual(first.current_goal_may_write(), may_write, goal_id)

        read_only = ExplorationPolicy.for_plan(plan, generation_id='run-1', user_constraints='探索阶段只读，禁止任何写入。')
        read_only.set_active_goal('G2')
        self.assertTrue(read_only.explicit_read_only)
        self.assertFalse(read_only.current_goal_may_write())

    def test_guard_blocks_high_risk_disabled_and_unknown_write_results(self):
        plan = GoalPlan.model_validate(plan_payload())
        policy = ExplorationPolicy.for_plan(plan, generation_id='run-2', user_constraints='')
        policy.set_active_goal('G2')
        recorder = ExplorationTraceRecorder()
        recorder.set_active_goal('G2')
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=4, policy=policy, trace_recorder=recorder)

        with self.assertRaises(Exception) as high_risk:
            guard.on_tool_start({'name': 'playwright_click'}, '', run_id='risk', inputs={'name': '确认付款'})
        self.assertEqual(high_risk.exception.error_kind, 'extra_risk_action')
        self.assertEqual(guard.get_stats()['potential_write_tool_calls'], 0)

        policy.set_active_goal('G2')
        recorder.set_active_goal('G2')
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=4, policy=policy, trace_recorder=recorder)
        guard.on_tool_start({'name': 'playwright_click'}, '', run_id='write', inputs={'testid': 'create-test-user'})
        with self.assertRaises(Exception) as unknown:
            guard.on_tool_end({'isError': True}, run_id='write')
        self.assertEqual(unknown.exception.error_kind, 'write_result_unknown')
        self.assertEqual(guard.get_stats()['potential_write_tool_calls'], 1)

        recorder.set_active_goal('G2')
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=1, policy=policy, trace_recorder=recorder)
        with self.assertRaises(Exception) as disabled:
            guard.on_tool_start({'name': 'playwright_upload_file'}, '', run_id='upload', inputs={})
        self.assertEqual(disabled.exception.error_kind, 'read_only_violation')


class V3TraceRegressionTests(SimpleTestCase):
    def test_trace_redacts_urls_and_secrets_and_bounds_events(self):
        recorder = ExplorationTraceRecorder('/users', sensitive_values=('trace-password',))
        recorder.set_active_goal('G1')
        recorder.on_tool_start({'name': 'playwright_fill'}, '', run_id='secret', inputs={'selector': '#password', 'value': 'trace-password'})
        recorder.on_tool_end('https://example.test/users password=trace-password', run_id='secret')
        for number in range(130):
            run_id = f'event-{number}'
            recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id=run_id, inputs={'path': '/users'})
            recorder.on_tool_end('用户列表', run_id=run_id)
        trace = recorder.build(tool_stats={})
        dumped = trace.model_dump_json()
        self.assertNotIn('trace-password', dumped)
        self.assertNotIn('https://example.test', dumped)
        self.assertIn('/users', dumped)
        self.assertLessEqual(len(trace.events), 120)
        self.assertEqual(trace.events[0].locator_input['input_value'], '<runtime_test_data>')

    def test_failed_and_blocked_events_never_become_goal_evidence(self):
        plan = GoalPlan.model_validate(plan_payload(include_external=False))
        recorder = ExplorationTraceRecorder('/users')
        recorder.set_active_goal('G2')
        recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='failed', inputs={'testid': 'create-test-user'})
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='failed')
        recorder.mark_blocked({'name': 'playwright_click'}, '', run_id='blocked', inputs={'testid': 'create-test-user'})
        run = evaluate_goal_events(plan.goals[1], recorder.events)
        recorder.record_goal_run(run)
        trace = recorder.build(tool_stats={})
        self.assertEqual(run.status, 'uncertain')
        self.assertEqual([item.status for item in trace.events], ['failed', 'blocked'])
        self.assertFalse(trace.locator_evidence)


class V3MCPExplorerRegressionTests(SimpleTestCase):
    def _plan(self):
        return GoalPlan.model_validate({
            **plan_payload(include_external=False),
            'goals': [
                {'id': 'G1', 'kind': 'setup', 'objective': '观察第一页', 'completion_criteria': '第一页已观察', 'side_effect': 'none'},
                {
                    'id': 'G2', 'kind': 'verify', 'objective': '观察第二页',
                    'completion_criteria': '第二页已观察',
                    'verification': {'mode': 'visible'}, 'side_effect': 'none',
                },
            ],
        })

    def test_one_session_closes_after_success_and_model_budget_is_global(self):
        class Client:
            opened = closed = 0
            async def create_all_sessions(self):
                self.opened += 1
            async def close_all_sessions(self):
                self.closed += 1

        class Agent:
            limits = []
            index = 0
            def __init__(self, **kwargs):
                self.guard = kwargs['callbacks'][0]
                type(self).limits.append(kwargs['max_steps'])
                type(self).index += 1
                self.index = type(self).index
            async def initialize(self):
                self.guard.on_chat_model_start({}, [])
            async def run(self, *_args, **_kwargs):
                run_id = f'observe-{self.index}'
                self.guard.on_tool_start(
                    {'name': 'playwright_get_visible_html'}, '', run_id=run_id,
                    inputs={'selector': f'#page-{self.index}'},
                )
                self.guard.on_tool_end(f'<main id="page-{self.index}">页面已观察</main>', run_id=run_id)

        client = Client()
        with override_settings(BASE_DIR='/tmp'), patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch('web_testing.mcp_page_explorer.MCPAgent', Agent):
            explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4()))
            trace = asyncio.run(explorer.explore_until_complete(plan=self._plan(), start_path='/', target_url_safe='/'))
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertEqual(trace.tool_stats['total_tool_calls'], 2)
        self.assertEqual(Agent.limits[1], Agent.limits[0] - 1)

    def test_timeout_and_agent_failure_close_the_same_session(self):
        class Client:
            opened = closed = 0
            async def create_all_sessions(self): self.opened += 1
            async def close_all_sessions(self): self.closed += 1

        class BrokenAgent:
            def __init__(self, **_kwargs): pass
            async def initialize(self): raise RuntimeError('connector failed')
            async def run(self, *_args, **_kwargs): raise AssertionError('must not run')

        for agent, timeout in ((BrokenAgent, 10), (BrokenAgent, 0)):
            with self.subTest(timeout=timeout):
                client = Client()
                with override_settings(BASE_DIR='/tmp'), patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch('web_testing.mcp_page_explorer.MCPAgent', agent):
                    explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={'mcpServers': {}}, generation_id=str(uuid4()), exploration_timeout_seconds=timeout)
                    with self.assertRaises(MCPPageExplorerError):
                        asyncio.run(explorer.explore_until_complete(plan=self._plan(), start_path='/', target_url_safe='/'))
                self.assertEqual((client.opened, client.closed), (1, 1))

    def test_global_tool_budget_and_explicit_read_only_submit_are_blocked(self):
        plan = GoalPlan.model_validate(plan_payload(include_external=False))
        policy = ExplorationPolicy.for_plan(plan, generation_id='run-3', user_constraints='只读')
        policy.set_active_goal('G2')
        recorder = ExplorationTraceRecorder()
        recorder.set_active_goal('G2')
        guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=2, policy=policy, trace_recorder=recorder)
        for run_id in ('first', 'second'):
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id=run_id, inputs={})
            guard.on_tool_end('ok', run_id=run_id)
        with self.assertRaises(Exception) as budget:
            guard.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='third', inputs={})
        self.assertEqual(budget.exception.error_kind, 'tool_budget')

        recorder.set_active_goal('G2')
        submit_guard = ReadOnlyMCPBrowserToolGuard(max_tool_calls=2, policy=policy, trace_recorder=recorder)
        with self.assertRaises(Exception) as submit:
            submit_guard.on_tool_start({'name': 'playwright_press_key'}, '', run_id='submit', inputs={'key': 'Enter'})
        self.assertEqual(submit.exception.error_kind, 'read_only_violation')

    def test_model_budget_never_grants_an_extra_step_after_exhaustion(self):
        plan = self._plan()
        explorer = MCPPageExplorer(llm_model=Mock(), mcp_config={}, generation_id=str(uuid4()))
        explorer._configure(plan, '/', None)
        explorer.guard.model_call_count = MCP_MAX_STEPS
        with self.assertRaises(MCPPageExplorerError) as exhausted:
            asyncio.run(explorer._run_goal(
                None, plan, plan.goals[0], '/', '/', None,
                time.monotonic() + 5,
                supplement=False,
            ))
        self.assertEqual(exhausted.exception.error_code, 'MODEL_STEP_BUDGET')


class V3MCPExplorerAsyncCancellationTests(TestCase):
    def _plan(self):
        return GoalPlan.model_validate(plan_payload(include_external=False))

    def test_cancel_check_with_sync_orm_query_runs_in_async_context_without_sync_error(self):
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
            async def run(self, *_args, **_kwargs):
                run_id = 'observe'
                self.guard.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id=run_id, inputs={'selector': '#main'})
                self.guard.on_tool_end('<main id="main">页面已观察</main>', run_id=run_id)

        calls = {'count': 0}
        def cancel_check():
            # A real synchronous Django DB access would raise
            # SynchronousOnlyOperation if it ran directly on the event-loop
            # thread. SELECT 1 also works with this suite's thread-local
            # in-memory SQLite connection.
            from django.db import connection

            calls['count'] += 1
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                return cursor.fetchone()[0] != 1

        client = Client()
        with override_settings(BASE_DIR='/tmp'), patch('web_testing.mcp_page_explorer.MCPClient.from_dict', return_value=client), patch('web_testing.mcp_page_explorer.MCPAgent', Agent):
            explorer = MCPPageExplorer(
                llm_model=Mock(),
                mcp_config={'mcpServers': {}},
                generation_id=str(uuid4()),
                cancel_check=cancel_check,
            )
            trace = asyncio.run(explorer.explore_until_complete(plan=self._plan(), start_path='/', target_url_safe='https://example.test'))
        self.assertGreaterEqual(calls['count'], 1)
        self.assertEqual((client.opened, client.closed), (1, 1))
        self.assertEqual(trace.tool_stats['total_tool_calls'], 3)


class V3ScriptQualityRegressionTests(SimpleTestCase):
    def test_compiled_script_has_provenance_cleanup_and_v3_run_signature(self):
        plan, trace = replay_fixture()
        replay_plan = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay_plan)
        report = evaluate_script(source, plan=plan, trace=trace, replay_plan=replay_plan)
        self.assertIn('async def run(page, variables):', source)
        self.assertIn('finally:', source)
        self.assertIn('[G2/E000003]', source)
        self.assertFalse(report['blockers'])

    def test_quality_rejects_sensitive_urls_missing_provenance_and_cleanup_finally(self):
        plan, trace = replay_fixture()
        replay_plan = ReplayPlanner.build(plan, trace)
        source = PythonReplayCompiler.compile(plan, trace, replay_plan)
        variants = {
            'SENSITIVE_LITERAL': source.replace('    variables = variables or {}', '    password = "literal"'),
            'ABSOLUTE_URL_FORBIDDEN': source + '# https://example.test/forbidden\n',
            'RUN_SIGNATURE_INVALID': source.replace('async def run(page, variables):', 'async def run(page):'),
            'ACTION_EVIDENCE_REFERENCE_MISSING': source.replace('[G1/E000001]', '(G1/E000001)', 1),
            'CLEANUP_FINALLY_MISSING': source.replace('    finally:', '    except Exception:'),
        }
        for expected_code, invalid_source in variants.items():
            with self.subTest(expected_code=expected_code):
                report = evaluate_script(invalid_source, plan=plan, trace=trace, replay_plan=replay_plan)
                self.assertIn(expected_code, {item['code'] for item in report['blockers']})


class V3GenerationPersistenceRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='v3-regression-owner', email='v3-regression-owner@example.test', password='test-password')
        self.project = Project.objects.create(name='V3 regressions', project_type='web', owner=self.user, created_by=self.user)
        self.environment = Environment.objects.create(
            project=self.project, name='Web', category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'}, is_active=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='Test provider', api_key='key',
            base_url='https://llm.example.test', model_name='v3-model', is_active=True, created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def make_generation(self, **overrides):
        values = {
            'project': self.project, 'user': self.user, 'environment': self.environment,
            'description_safe': '观察用户列表。', 'target_url_safe': 'https://web.example.test/users',
            'model_info': {'config_id': self.model.id},
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def api_request(self, user, payload):
        request = self.factory.post('/script-generations/', payload, format='json')
        force_authenticate(request, user=user)
        return WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)

    def test_create_api_enforces_project_scope_and_locks_requested_active_model(self):
        outsider = get_user_model().objects.create_user(username='v3-regression-outsider', email='v3-regression-outsider@example.test', password='test-password')
        denied = self.api_request(outsider, {'description': '观察用户列表。', 'environment_id': self.environment.id})
        self.assertEqual(denied.status_code, 404)

        other = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='Other', api_key='key',
            base_url='https://llm.example.test', model_name='other-model', is_active=True, created_by=self.user,
        )
        with patch('web_testing.views.generate_webui_script_generation_task.delay', return_value=SimpleNamespace(id='v3-create-task')) as delay:
            response = self.api_request(self.user, {'description': '观察用户列表。', 'environment_id': self.environment.id, 'start_path': '/users', 'model_config_id': other.id})
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.model_info['config_id'], other.id)
        delay.assert_called_once_with(str(generation.pk))

        other.is_active = False
        other.save(update_fields=['is_active'])
        rejected = self.api_request(self.user, {'description': '观察用户列表。', 'environment_id': self.environment.id, 'start_path': '/users', 'model_config_id': other.id})
        self.assertEqual(rejected.status_code, 400)

    def test_dispatch_failure_clears_temporary_credentials_and_marks_failed(self):
        with patch('web_testing.views.generate_webui_script_generation_task.delay', side_effect=RuntimeError('broker unavailable')):
            response = self.api_request(self.user, {
                'description': '观察用户列表。', 'environment_id': self.environment.id, 'start_path': '/users',
                'temporary_credentials': {'username': 'test-user', 'password': 'temporary-value'},
            })
        self.assertEqual(response.status_code, 503, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertIsNone(get_temporary_credentials(generation.pk))
        self.assertNotIn('temporary-value', str(response.data))
        self.assertNotIn('temporary-value', str(generation.exploration_snapshot))

    def test_credential_cache_failure_rolls_back_new_generation(self):
        payload = {
            'description': '观察用户列表。', 'environment_id': self.environment.id,
            'start_path': '/users',
            'temporary_credentials': {'username': 'test-user', 'password': 'temporary-value'},
        }
        request = self.factory.post('/script-generations/', payload, format='json')
        force_authenticate(request, user=self.user)
        request.user = self.user
        serializer = WebUIScriptGenerationCreateSerializer(data=payload, context={'request': request, 'project': self.project})
        serializer.is_valid(raise_exception=True)
        with patch('web_testing.serializers.store_temporary_credentials', side_effect=RuntimeError('cache unavailable')):
            with self.assertRaises(RuntimeError):
                serializer.save()
        self.assertFalse(WebUIScriptGeneration.objects.filter(project=self.project).exists())

    def test_transitions_claims_and_trace_only_retry_are_idempotent_and_v3_only(self):
        generation = self.make_generation()
        with self.assertRaises(GenerationTransitionError):
            transition_generation(generation.pk, WebUIScriptGeneration.Status.GENERATING)
        transition_generation(generation.pk, WebUIScriptGeneration.Status.NORMALIZING, progress=5)
        transition_generation(generation.pk, WebUIScriptGeneration.Status.PREFLIGHTING, progress=25)
        self.assertIsNotNone(claim_generation_worker(generation.pk, 'worker-1'))
        self.assertIsNone(claim_generation_worker(generation.pk, 'worker-1'))

        generation.status = WebUIScriptGeneration.Status.FAILED
        generation.error_code = 'MODEL_UNAVAILABLE'
        generation.revision = 7
        generation.exploration_snapshot = {'schema_version': 2}
        generation.save(update_fields=['status', 'error_code', 'revision', 'exploration_snapshot'])
        with self.assertRaises(GenerationResolutionConflict):
            prepare_trace_generation_retry(generation.pk, expected_revision=7)

        generation.exploration_snapshot = {'schema_version': 3}
        generation.save(update_fields=['exploration_snapshot'])
        retried = prepare_trace_generation_retry(generation.pk, expected_revision=7)
        self.assertEqual((retried.status, retried.revision), (WebUIScriptGeneration.Status.GENERATING, 8))
        self.assertIsNotNone(claim_trace_generation_retry(retried.pk, 'trace-worker'))
        self.assertIsNone(claim_trace_generation_retry(retried.pk, 'trace-worker'))

    def test_terminal_transition_clears_credentials_and_terminal_event_can_retry_after_failure(self):
        generation = self.make_generation()
        from .generation_security import store_temporary_credentials
        store_temporary_credentials(generation.pk, {'username': 'test-user', 'password': 'temporary-value'})
        transition_generation(generation.pk, WebUIScriptGeneration.Status.FAILED, error_code='MODEL_UNAVAILABLE')
        self.assertIsNone(get_generation_temporary_credentials(generation.pk))

        event = SimpleNamespace(
            pk=f'event-{uuid4()}', user_id=self.user.id, celery_task_id='',
            status=WebUIScriptGeneration.Status.READY, error_code='', error_message='',
        )
        key = f'webui:script-generation:terminal-event:{event.pk}'
        cache.delete(key)
        with patch('web_testing.generation_events.websocket_message_service.send_task_completed', side_effect=[False, True]) as send:
            publish_terminal(event)
            publish_terminal(event)
        self.assertEqual(send.call_count, 2)
        cache.delete(key)

    def test_unknown_exploration_exception_records_stack_and_returns_internal_error_code(self):
        from .generation_orchestrator import run_generation

        generation = self.make_generation(start_path='/users', target_url_safe='https://web.example.test/users')
        plan = GoalPlan.model_validate(plan_payload(include_external=False))
        preflight = SimpleNamespace(outcome='continue', warnings=[], mcp_config={})

        with patch('web_testing.generation_orchestrator.normalize_requirement', return_value=plan), \
             patch('web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight), \
             patch('web_testing.generation_orchestrator.get_llm_manager', return_value=SimpleNamespace(current_llm=Mock())), \
             patch('web_testing.generation_orchestrator.MCPPageExplorer') as explorer_cls, \
             patch('web_testing.generation_orchestrator.logger.exception') as logged_exception:
            explorer = Mock()
            explorer.explore_until_complete.side_effect = RuntimeError('mcp crashed')
            explorer_cls.return_value = explorer
            result = run_generation(str(generation.pk), celery_task_id='task-1')

        self.assertEqual(result['error_code'], 'INTERNAL_EXPLORATION_ERROR')
        self.assertEqual(result['status'], 'failed')
        logged_exception.assert_called_once()
        self.assertIn('页面探索执行发生未知异常', logged_exception.call_args[0][0])

    def test_saved_marker_requires_matching_generation_reference(self):
        test_case = SimpleNamespace(generation_metadata={})
        generation = SimpleNamespace(pk=uuid4(), test_case_id=1, test_case=test_case, workspace={}, revision=0, script_draft='')
        test_case.generation_metadata = {'generation_ref': generation_reference(generation)}
        self.assertTrue(is_generation_saved(generation))
        test_case.generation_metadata['generation_ref'] = 'wrong'
        self.assertFalse(is_generation_saved(generation))
