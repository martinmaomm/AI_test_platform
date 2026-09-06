"""Offline contracts for bounded AI-assisted generated-script debugging."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project

from .generation_workspace import (
    WorkspaceConflict, apply_repair_candidate, attach_repair_task, finish_repair_failure,
    discard_repair_candidate, prepare_repair, script_hash, update_repair_state,
    workspace_for_generation,
)
from .generation_repository import GenerationResolutionConflict, prepare_trace_generation_retry
from .models import WebUIScriptGeneration, WebUITestCaseExecutionDetail, WebUITestExecution
from .execution_variables import store_repair_runtime_variables
from .ai_assisted_debugging import (
    MAX_CANDIDATE_DIFF_CHARS, bounded_candidate_diff, candidate_leaks_runtime_values,
    failure_evidence, is_non_code_failure, redact_runtime_values,
    requires_directed_mcp,
)
from .serializers import WebUIScriptGenerationRepairApplySerializer
from .tasks import repair_webui_script_generation_task
from .views import (
    WebUIScriptGenerationRepairApplyView, WebUIScriptGenerationRepairDiscardView,
    WebUIScriptGenerationRepairView, WebUIScriptGenerationDraftView,
    WebUIScriptGenerationDebugView, WebUIScriptGenerationSaveView,
)


SCRIPT = '''from playwright.async_api import expect
async def run(page):
    await page.goto("https://web.example.test/users")
    # 验证：主页面可见
    await expect(page.locator("main")).to_be_visible()
'''


class AssistedDebuggingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='repair-owner', password='pw')
        self.project = Project.objects.create(name='Repair', project_type='web', owner=self.user, created_by=self.user)
        self.factory = APIRequestFactory()

    def generation(self):
        return WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user, status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
            target_url='https://web.example.test/users', description_safe='修复用户页。',
            script_draft=SCRIPT, model_info={'config_id': 1}, scenario_spec={'schema_version': 5}, exploration_snapshot={'schema_version': 5},
            workspace={'revision': 0, 'verification': {'status': 'failed', 'script_hash': script_hash(SCRIPT),
                       'locked_revision': 0, 'execution_id': 7, 'diagnostics': [{'code': 'RUNTIME_FAILURE', 'message': 'locator failed'}]}},
        )

    def request(self, payload):
        request = self.factory.post('/repair/', payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def apply_request(self, payload):
        request = self.factory.post('/repair/apply/', payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def discard_request(self, payload, user=None):
        request = self.factory.post('/repair/discard/', payload, format='json')
        force_authenticate(request, user=user or self.user)
        return request

    def api_request(self, method, path, payload):
        request = getattr(self.factory, method.lower())(path, payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def set_candidate_status(self, generation, repair_status='candidate_ready'):
        _, digest = prepare_repair(generation.id, expected_revision=0)
        candidate = SCRIPT + '\n# review candidate\n'
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            candidate_script=candidate, candidate_diff='diff',
            candidate_quality_report={'status': 'ready', 'blockers': []},
            final_status=repair_status, task_id=None,
        ))
        return candidate

    def test_runtime_variables_are_cache_only_and_not_dispatched(self):
        generation = self.generation()
        secret = 'repair-temporary-value'
        with patch('web_testing.views.repair_webui_script_generation_task.delay', return_value=SimpleNamespace(id='repair-task')) as delay:
            response = WebUIScriptGenerationRepairView.as_view()(
                self.request({'expected_revision': 0, 'confirm_execution': True,
                              'runtime_variables': [{'name': 'UI_TEST_PASSWORD', 'value': secret, 'is_secret': True}]}),
                project_id=self.project.id, generation_id=generation.id,
            )
        self.assertEqual(response.status_code, 202, response.data)
        self.assertNotIn(secret, str(response.data))
        self.assertNotIn(secret, str(delay.call_args))
        generation.refresh_from_db()
        self.assertNotIn(secret, str(generation.workspace))
        self.assertTrue(workspace_for_generation(generation)['repair']['runtime_variables_present'])

    def test_attempts_are_bounded_and_workspace_drops_raw_fields(self):
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        attempts = [
            {'round': item, 'candidate_hash': str(item) * 64, 'execution_status': 'failed',
             'summary': 'x' * 4000, 'raw_log': 'must-not-persist'}
            for item in range(1, 4)
        ]
        self.assertTrue(update_repair_state(generation.id, locked_revision=0, locked_hash=digest,
                                            attempts=attempts, task_id=None))
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['attempt_count'], 2)
        self.assertNotIn('raw_log', str(repair))
        self.assertLessEqual(len(repair['attempts'][0]['summary']), 2000)

    def test_apply_only_inherits_complete_passed_evidence(self):
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        candidate = SCRIPT + '\n# repaired\n'
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            attempts=[{'round': 1, 'candidate_hash': script_hash(candidate), 'execution_id': 99,
                       'execution_status': 'passed', 'runtime_assertion_count': 1}],
            candidate_script=candidate, candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_passed', task_id=None,
        ))
        applied = apply_repair_candidate(generation.id, expected_revision=0, candidate_hash=script_hash(candidate))
        self.assertEqual(applied.script_draft, candidate)
        self.assertEqual(workspace_for_generation(applied)['verification']['status'], 'passed')
        repair = workspace_for_generation(applied)['repair']
        self.assertEqual(repair['status'], 'idle')
        self.assertEqual(repair['attempts'], [])
        self.assertEqual(repair['candidate_script'], '')

    def test_each_failed_debug_can_start_a_new_two_round_repair(self):
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        self.assertTrue(finish_repair_failure(
            generation.id, locked_revision=0, locked_hash=digest, message='候选不可用', task_id=None,
        ))
        generation.refresh_from_db()
        prepared, new_digest = prepare_repair(generation.id, expected_revision=0)
        self.assertEqual(prepared.pk, generation.pk)
        self.assertEqual(new_digest, digest)
        self.assertEqual(workspace_for_generation(prepared)['repair']['attempt_count'], 0)

    def test_unresolved_candidate_blocks_draft_debug_save_and_repair_apis(self):
        for repair_status in ('candidate_ready', 'candidate_passed'):
            with self.subTest(repair_status=repair_status):
                generation = self.generation()
                candidate = self.set_candidate_status(generation, repair_status)
                requests = (
                    (WebUIScriptGenerationDraftView, self.api_request('PATCH', '/draft/', {
                        'expected_revision': 0, 'script_draft': SCRIPT + '\n# edit\n', 'variables': [],
                    })),
                    (WebUIScriptGenerationDebugView, self.api_request('POST', '/debug/', {
                        'expected_revision': 0, 'confirm_execution': True, 'runtime_variables': [],
                    })),
                    (WebUIScriptGenerationSaveView, self.api_request('POST', '/save/', {
                        'expected_revision': 0, 'mode': 'draft',
                    })),
                    (WebUIScriptGenerationRepairView, self.request({
                        'expected_revision': 0, 'confirm_execution': True, 'runtime_variables': [],
                    })),
                )
                for view, request in requests:
                    response = view.as_view()(request, project_id=self.project.id, generation_id=generation.id)
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertEqual(response.data['message'], '请先采用或放弃当前候选。')
                generation.refresh_from_db()
                self.assertEqual(generation.script_draft, SCRIPT)
                self.assertEqual(workspace_for_generation(generation)['repair']['candidate_script'], candidate)

    def test_unresolved_candidate_blocks_trace_generation_retry(self):
        for repair_status in ('candidate_ready', 'candidate_passed'):
            with self.subTest(repair_status=repair_status):
                generation = self.generation()
                self.set_candidate_status(generation, repair_status)
                with self.assertRaisesRegex(GenerationResolutionConflict, '请先采用或放弃当前候选'):
                    prepare_trace_generation_retry(generation.id, expected_revision=0)
                generation.refresh_from_db()
                self.assertEqual(generation.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)

    def test_apply_hash_requires_lowercase_sha256(self):
        serializer = WebUIScriptGenerationRepairApplySerializer(
            data={'expected_revision': 0, 'candidate_hash': 'G' * 64},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('candidate_hash', serializer.errors)
        generation = self.generation()
        response = WebUIScriptGenerationRepairApplyView.as_view()(
            self.apply_request({'expected_revision': 0, 'candidate_hash': 'G' * 64}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 400)

    def _queue_repair(self, generation, *, task_id='repair-worker', runtime_variables_present=False):
        _, digest = prepare_repair(
            generation.id, expected_revision=0,
            runtime_variables_present=runtime_variables_present,
        )
        attach_repair_task(
            generation.id, locked_revision=0, locked_hash=digest, task_id=task_id,
        )
        return digest

    @staticmethod
    def _runner_result(*, passed, stdout='', runtime_assertion_count=1):
        return {
            'success': passed, 'operation_success': passed,
            'runtime_assertion_count': runtime_assertion_count,
            'error': '' if passed else 'runner failed',
            'result': {
                'stdout': stdout, 'stderr': '', 'test_file': '',
                'screenshot_path': None,
            },
        }

    def test_second_round_uses_first_candidate_snapshot_and_redecides_mcp(self):
        generation = self.generation()
        generation.workspace = {'revision': 0, 'variables': [
            {'name': 'DEFAULT_LABEL', 'value': 'saved-default', 'required': False, 'is_secret': False},
        ], 'verification': generation.workspace['verification']}
        generation.save(update_fields=['workspace', 'updated_at'])
        digest = self._queue_repair(generation)
        store_repair_runtime_variables(
            generation.id, 0, digest,
            [{'name': 'UI_TEST_OVERRIDE', 'value': 'one-time-value', 'required': False, 'is_secret': True}],
        )
        candidates = [SCRIPT + '\n# first\n', SCRIPT + '\n# second\n']
        calls = []
        agent_ids = []

        class Agent:
            def __init__(self, **kwargs):
                agent_ids.append(kwargs['generation_id'])

            async def generate(self, **kwargs):
                calls.append(kwargs)
                index = len(calls) - 1
                return SimpleNamespace(script_draft=candidates[index], snapshot={'schema_version': 5, 'round': index + 1})

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script', side_effect=[
                self._runner_result(passed=False, stdout='Locator.click: Timeout 1000ms exceeded.'),
                self._runner_result(passed=True),
            ],
        ), patch('web_testing.generation_preflight.run_safety_preflight', return_value=SimpleNamespace(outcome='continue', mcp_config={'mcpServers': {}})):
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()
        self.assertEqual(result['status'], 'candidate_passed')
        self.assertTrue(calls[0]['code_only'])
        self.assertFalse(calls[1]['code_only'])
        self.assertEqual(calls[1]['script_draft'], candidates[0])
        self.assertEqual(calls[1]['saved_snapshot']['round'], 1)
        self.assertEqual(calls[0]['brief']['runtime_input_values']['DEFAULT_LABEL'], 'saved-default')
        self.assertEqual(calls[0]['brief']['runtime_input_values']['UI_TEST_OVERRIDE'], 'one-time-value')
        self.assertEqual(len(agent_ids), 2)
        self.assertNotEqual(agent_ids[0], agent_ids[1])
        self.assertNotIn(str(generation.pk), agent_ids)

    def test_second_round_preflight_error_preserves_first_candidate(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        first_candidate = SCRIPT + '\n# first candidate\n'

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft=first_candidate, snapshot={'schema_version': 5, 'round': 1})

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script', return_value=self._runner_result(
                passed=False, stdout='Locator.click: Timeout 1000ms exceeded.',
            ),
        ), patch('web_testing.generation_preflight.run_safety_preflight', side_effect=RuntimeError('第二轮预检异常')):
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()
        self.assertEqual(result['status'], 'candidate_ready')
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(repair['status'], 'candidate_ready')
        self.assertEqual(repair['candidate_script'], first_candidate)
        self.assertEqual(len(repair['attempts']), 2)
        self.assertEqual(repair['attempts'][1]['execution_status'], 'not_run')
        self.assertIn('第二轮预检异常', repair['candidate_error_message'])

    def test_execution_save_error_closes_created_execution_and_records_attempt(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# candidate\n'
        original_save = WebUITestExecution.save
        final_save_failed = False

        def fail_first_final_save(instance, *args, **kwargs):
            nonlocal final_save_failed
            if instance.status == 'failed' and not final_save_failed:
                final_save_failed = True
                raise RuntimeError('execution final save failed')
            return original_save(instance, *args, **kwargs)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft=candidate, snapshot={'schema_version': 5})

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script', return_value=self._runner_result(passed=False),
        ), patch.object(WebUITestExecution, 'save', new=fail_first_final_save):
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()
        self.assertEqual(result['status'], 'candidate_ready')
        execution = WebUITestExecution.objects.get()
        detail = WebUITestCaseExecutionDetail.objects.get()
        self.assertEqual(execution.status, 'error')
        self.assertIsNotNone(execution.end_time)
        self.assertEqual(detail.status, 'error')
        self.assertIsNotNone(detail.end_time)
        generation.refresh_from_db()
        attempt = workspace_for_generation(generation)['repair']['attempts'][0]
        self.assertEqual(attempt['execution_id'], execution.id)
        self.assertFalse(attempt['has_screenshot'])

    def test_assertion_regression_is_blocked_before_runner(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        deleted_assertion = '''async def run(page):
    await page.goto("https://web.example.test/users")
'''
        agent_calls = 0

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                nonlocal agent_calls
                agent_calls += 1
                if agent_calls == 1:
                    return SimpleNamespace(script_draft=deleted_assertion, snapshot={'schema_version': 5})
                raise RuntimeError('second round stopped')

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script',
        ) as runner:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()
        self.assertEqual(result['status'], 'candidate_ready')
        runner.assert_not_called()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertTrue(any(item.get('code') == 'ASSERTION_REGRESSION' for item in repair['candidate_quality_report']['blockers']))

    def test_second_round_assertions_are_always_compared_with_original_draft(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidates = [
            '''async def run(page):
    await page.goto("https://web.example.test/users")
    await expect(page.locator("main")).to_be_hidden()
''',
            '''async def run(page):
    await page.goto("https://web.example.test/users")
    return
    await expect(page.locator("main")).to_be_visible()
''',
        ]
        agent_calls = 0

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                nonlocal agent_calls
                candidate = candidates[agent_calls]
                agent_calls += 1
                return SimpleNamespace(
                    script_draft=candidate,
                    snapshot={'schema_version': 5, 'round': agent_calls},
                )

        with patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', Agent,
        ), patch(
            'ai_core.model_manager.get_llm_manager',
            return_value=SimpleNamespace(current_llm=object()),
        ), patch(
            'web_testing.generation_workspace.evaluate_workspace_draft',
            side_effect=lambda *_args, **_kwargs: {'status': 'ready', 'blockers': []},
        ), patch('web_testing.tasks._run_test_script') as runner:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()

        self.assertEqual(result['status'], 'candidate_ready')
        self.assertEqual(agent_calls, 2)
        runner.assert_not_called()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertTrue(any(
            item.get('code') == 'ASSERTION_REGRESSION'
            for item in repair['candidate_quality_report']['blockers']
        ))

    def test_agent_error_semantic_regression_is_review_only_and_not_executed(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT.replace('.to_be_visible()', '.to_be_hidden()')

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(
                    script_draft=candidate, snapshot={'schema_version': 5},
                    error_code='MODEL_TIMEOUT', error_message='模型超时',
                )

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script',
        ) as runner:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()

        self.assertEqual(result['status'], 'candidate_ready')
        runner.assert_not_called()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(repair['candidate_script'], candidate)
        self.assertEqual(repair['candidate_quality_report']['status'], 'needs_review')
        self.assertTrue(any(
            item.get('code') == 'ASSERTION_REGRESSION'
            for item in repair['candidate_quality_report']['blockers']
        ))
        self.assertEqual(repair['attempts'][0]['execution_status'], 'not_run')
        self.assertEqual(repair['attempts'][0]['runtime_assertion_count'], 0)

    def test_unchanged_agent_output_is_not_executed_as_a_repair_candidate(self):
        generation = self.generation()
        digest = self._queue_repair(generation)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(
                    script_draft=SCRIPT,
                    snapshot={'schema_version': 5},
                )

        with patch(
            'web_testing.script_exploration_agent.ScriptExplorationAgent', Agent,
        ), patch(
            'ai_core.model_manager.get_llm_manager',
            return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.tasks._run_test_script') as runner:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='repair-worker',
            ).get()

        self.assertFalse(result['success'])
        self.assertIn('未生成有变化的候选脚本', result['message'])
        runner.assert_not_called()
        generation.refresh_from_db()
        self.assertEqual(workspace_for_generation(generation)['repair']['status'], 'failed')

    def test_non_code_classification_keeps_script_error_repairable_and_avoids_credential_false_positive(self):
        self.assertTrue(is_non_code_failure({'category': 'navigation_error'}))
        self.assertTrue(is_non_code_failure({'category': 'target_closed'}))
        self.assertTrue(is_non_code_failure({'stdout': "BrowserType.launch: Executable doesn't exist"}))
        self.assertFalse(is_non_code_failure({'category': 'script_error'}))
        locator_evidence = failure_evidence(
            stdout='Locator.click: Timeout 1000ms exceeded; 页面提示账号或密码。',
        )
        self.assertFalse(is_non_code_failure(locator_evidence))
        self.assertTrue(requires_directed_mcp(locator_evidence))

    def test_candidate_diff_is_display_truncated_but_direct_oversize_write_is_rejected(self):
        bounded = bounded_candidate_diff('x' * (MAX_CANDIDATE_DIFF_CHARS + 1))
        self.assertLessEqual(len(bounded), MAX_CANDIDATE_DIFF_CHARS)
        self.assertIn('差异过长，已省略；请查看完整候选脚本', bounded)
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        with self.assertRaisesRegex(ValueError, '差异超过允许长度'):
            update_repair_state(
                generation.id, locked_revision=0, locked_hash=digest,
                candidate_diff='x' * (MAX_CANDIDATE_DIFF_CHARS + 1), task_id=None,
            )

    def test_runner_exception_closes_execution_and_skips_unchanged_retry(self):
        generation = self.generation()
        digest = self._queue_repair(generation)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft=SCRIPT + '\n# candidate\n', snapshot={'schema_version': 5})

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_workspace.evaluate_workspace_draft', return_value={'status': 'ready', 'blockers': []}), patch(
            'web_testing.tasks._run_test_script', side_effect=RuntimeError('runner exploded'),
        ):
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        self.assertEqual(
            list(WebUITestExecution.objects.values_list('status', flat=True)),
            ['error'],
        )
        self.assertEqual(
            list(WebUITestCaseExecutionDetail.objects.values_list('status', flat=True)),
            ['error'],
        )
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, SCRIPT)
        attempts = workspace_for_generation(generation)['repair']['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertIsNotNone(attempts[0]['execution_id'])
        self.assertIsNone(attempts[1]['execution_id'])
        self.assertEqual(attempts[1]['execution_status'], 'not_run')
        self.assertIn('未生成有变化的候选脚本', attempts[1]['summary'])

    def test_oversized_candidate_is_rejected_without_truncation(self):
        generation = self.generation()
        digest = self._queue_repair(generation)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft='x' * 200001, snapshot={'schema_version': 5})

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ):
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['status'], 'failed')
        self.assertEqual(repair['candidate_script'], '')
        self.assertEqual(generation.script_draft, SCRIPT)

    def test_missing_cached_runtime_variables_stops_before_model(self):
        generation = self.generation()
        digest = self._queue_repair(generation, runtime_variables_present=True)
        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent') as agent:
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        agent.assert_not_called()
        generation.refresh_from_db()
        self.assertEqual(workspace_for_generation(generation)['repair']['status'], 'failed')
        self.assertEqual(workspace_for_generation(generation)['repair']['phase'], 'completed')

    def test_agent_error_without_new_candidate_terminates_with_safe_message(self):
        generation = self.generation()
        digest = self._queue_repair(generation)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft=SCRIPT, error_code='MODEL_TIMEOUT', error_message='模型超时')

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ):
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['status'], 'failed')
        self.assertEqual(repair['phase'], 'completed')
        self.assertEqual(generation.script_draft, SCRIPT)

    def test_runtime_value_leak_check_does_not_reject_short_substrings(self):
        candidate = SCRIPT + '\nasync def helper():\n    return "contest"\n'
        self.assertFalse(candidate_leaks_runtime_values(candidate, [{'name': 'USERNAME', 'value': 'test'}]))
        self.assertTrue(candidate_leaks_runtime_values(
            SCRIPT + '\nasync def helper():\n    return "test"\n',
            [{'name': 'USERNAME', 'value': 'test'}],
        ))
        self.assertTrue(candidate_leaks_runtime_values(
            SCRIPT + '\nasync def helper():\n    return "prefix-super-secret-runtime-value-suffix"\n',
            [{'name': 'PASSWORD', 'value': 'super-secret-runtime-value'}],
        ))

    def test_runtime_value_leak_check_allows_existing_literal_but_rejects_new_one(self):
        original = SCRIPT + '\nasync def existing():\n    return "test-password"\n'
        unchanged_candidate = original + '\n# locator repaired\n'
        added_literal_candidate = original + '\nasync def copied():\n    return "test-password"\n'
        runtime_variables = [{
            'name': 'UI_TEST_PASSWORD', 'value': 'test-password',
            'is_secret': True,
        }]
        self.assertFalse(candidate_leaks_runtime_values(
            unchanged_candidate, runtime_variables, baseline_script=original,
        ))
        self.assertTrue(candidate_leaks_runtime_values(
            added_literal_candidate, runtime_variables, baseline_script=original,
        ))
        repeated_inside_literal = original.replace(
            '"test-password"', '"test-passwordtest-password"',
        )
        self.assertTrue(candidate_leaks_runtime_values(
            repeated_inside_literal, runtime_variables, baseline_script=original,
        ))

    def test_diagnostic_redaction_uses_boundaries_for_short_runtime_values(self):
        runtime_variables = [
            {'name': 'UI_TEST_USERNAME', 'value': 'test', 'is_secret': False},
            {'name': 'UI_TEST_PASSWORD', 'value': 'secret-password', 'is_secret': True},
        ]
        redacted = redact_runtime_values(
            'pytest test secret-password', runtime_variables,
        )
        self.assertEqual(
            redacted,
            'pytest [运行变量已隐藏] [运行变量已隐藏]',
        )

    def test_agent_error_whitespace_only_candidate_is_not_new(self):
        generation = self.generation()
        digest = self._queue_repair(generation)

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(script_draft=f'\n{SCRIPT}\n', error_code='MODEL_TIMEOUT', error_message='模型超时')

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ):
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['status'], 'failed')
        self.assertEqual(repair['candidate_script'], '')

    def test_repair_workspace_error_fields_are_bounded_before_normalize(self):
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            candidate_error_code='C' * 200, candidate_error_message='M' * 3000,
            summary='S' * 3000, task_id=None,
        ))
        generation.refresh_from_db()
        raw_repair = generation.workspace['repair']
        self.assertEqual(len(raw_repair['candidate_error_code']), 96)
        self.assertEqual(len(raw_repair['candidate_error_message']), 2000)
        self.assertEqual(len(raw_repair['summary']), 2000)

    def test_finish_repair_failure_sanitizes_before_workspace_write(self):
        generation = self.generation()
        _, digest = prepare_repair(generation.id, expected_revision=0)
        self.assertTrue(finish_repair_failure(
            generation.id, locked_revision=0, locked_hash=digest, message='M' * 3000,
            blockers=[{'severity': 'blocker', 'code': 'C' * 200, 'message': 'B' * 3000, 'raw_log': 'drop'}],
            task_id=None,
        ))
        generation.refresh_from_db()
        raw_repair = generation.workspace['repair']
        self.assertEqual(len(raw_repair['message']), 2000)
        self.assertEqual(len(raw_repair['blockers'][0]['code']), 96)
        self.assertEqual(len(raw_repair['blockers'][0]['message']), 2000)
        self.assertNotIn('raw_log', raw_repair['blockers'][0])

    def test_runtime_value_hardcoded_by_agent_is_rejected(self):
        generation = self.generation()
        digest = self._queue_repair(generation, runtime_variables_present=True)
        store_repair_runtime_variables(
            generation.id, 0, digest,
            [{'name': 'UI_TEST_PASSWORD', 'value': 'one-time-secret', 'required': True, 'is_secret': True}],
        )

        class Agent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(
                    script_draft=SCRIPT + '\nasync def helper():\n    return "one-time-secret"\n',
                    snapshot={'schema_version': 5},
                )

        with patch('web_testing.script_exploration_agent.ScriptExplorationAgent', Agent), patch(
            'ai_core.model_manager.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ):
            repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['status'], 'failed')
        self.assertEqual(repair['candidate_script'], '')
        self.assertNotIn('one-time-secret', str(generation.workspace))

    def test_model_exception_uses_classified_message(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        with patch('ai_core.model_manager.get_llm_manager', side_effect=RuntimeError('raw provider detail')), patch(
            'web_testing.model_service_errors.classify_model_service_error',
            return_value=('MODEL_SERVICE_ERROR', '模型服务友好错误'),
        ):
            result = repair_webui_script_generation_task.apply(args=(str(generation.id), 0, digest), task_id='repair-worker').get()
        self.assertEqual(result['error'], '模型服务友好错误')
        generation.refresh_from_db()
        repair = workspace_for_generation(generation)['repair']
        self.assertEqual(repair['message'], '模型服务友好错误')

    def test_stale_task_cannot_write_back(self):
        generation = self.generation()
        digest = self._queue_repair(generation, task_id='new-task')
        with patch('web_testing.tasks._run_test_script') as runner:
            result = repair_webui_script_generation_task.apply(
                args=(str(generation.id), 0, digest), task_id='old-task',
            ).get()
        self.assertFalse(result['success'])
        runner.assert_not_called()
        generation.refresh_from_db()
        self.assertEqual(workspace_for_generation(generation)['repair']['status'], 'pending')

    def test_failed_candidate_keeps_draft_and_apply_conflicts_are_rejected(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# candidate\n'
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            attempts=[{'round': 1, 'candidate_hash': script_hash(candidate), 'execution_status': 'failed'}],
            candidate_script=candidate, candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_ready', task_id='repair-worker',
        ))
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, SCRIPT)
        with self.assertRaises(WorkspaceConflict):
            apply_repair_candidate(generation.id, expected_revision=0, candidate_hash='0' * 64)

    def test_passed_candidate_without_runtime_assertion_proof_is_unverified(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# candidate\n'
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            attempts=[{'round': 1, 'candidate_hash': script_hash(candidate), 'execution_id': 8,
                       'execution_status': 'passed', 'runtime_assertion_count': 0}],
            candidate_script=candidate, candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_passed', task_id='repair-worker',
        ))
        applied = apply_repair_candidate(generation.id, expected_revision=0, candidate_hash=script_hash(candidate))
        self.assertEqual(workspace_for_generation(applied)['verification']['status'], 'unverified')

    def test_discard_candidate_keeps_draft_and_revision(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# discard me\n'
        self.assertTrue(update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest,
            candidate_script=candidate, candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_ready', task_id='repair-worker',
        ))
        response = WebUIScriptGenerationRepairDiscardView.as_view()(
            self.discard_request({'expected_revision': 0, 'candidate_hash': script_hash(candidate)}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        generation.refresh_from_db()
        workspace = workspace_for_generation(generation)
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(workspace['revision'], 0)
        self.assertEqual(workspace['repair']['status'], 'idle')
        self.assertEqual(workspace['repair']['candidate_script'], '')

    def test_discard_rejects_conflict_and_unauthorized_user(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# discard me\n'
        update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest, candidate_script=candidate,
            candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_ready', task_id='repair-worker',
        )
        conflict = WebUIScriptGenerationRepairDiscardView.as_view()(
            self.discard_request({'expected_revision': 0, 'candidate_hash': '0' * 64}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(conflict.status_code, 409)
        outsider = get_user_model().objects.create_user(
            username='repair-outsider', email='repair-outsider@example.test', password='pw',
        )
        denied = WebUIScriptGenerationRepairDiscardView.as_view()(
            self.discard_request({'expected_revision': 0, 'candidate_hash': script_hash(candidate)}, outsider),
            project_id=self.project.id, generation_id=generation.id,
        )
        # Project access intentionally hides an unrelated project from outsiders.
        self.assertEqual(denied.status_code, 404)

    def test_discard_rejects_tampered_candidate_content(self):
        generation = self.generation()
        digest = self._queue_repair(generation)
        candidate = SCRIPT + '\n# candidate\n'
        update_repair_state(
            generation.id, locked_revision=0, locked_hash=digest, candidate_script=candidate,
            candidate_diff='diff', candidate_quality_report={'status': 'ready'},
            final_status='candidate_ready', task_id='repair-worker',
        )
        generation.refresh_from_db()
        generation.workspace['repair']['candidate_script'] = candidate + '# tampered\n'
        generation.save(update_fields=['workspace', 'updated_at'])
        with self.assertRaises(WorkspaceConflict):
            discard_repair_candidate(generation.id, expected_revision=0, candidate_hash=script_hash(candidate))
