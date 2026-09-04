"""v5 artifact persistence regressions (run with the coordinated DB suite)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Environment, Project

from .generation_repository import (
    finalize_generation_artifact, persist_generation_checkpoint, claim_trace_generation_retry,
    prepare_trace_generation_retry,
)
from .models import WebUIScriptGeneration


SCRIPT = '''\
from playwright.async_api import expect

async def run(page, variables):
    await page.goto('/items')
    await expect(page.locator('#result')).to_be_visible()
'''
PARTIAL_SCRIPT = SCRIPT + '\n    # AITS_PENDING_STEP: {"reason":"后续详情页尚未观察"}\n'
COMPLETE_SCRIPT = '''\
"""检查列表页结果。"""
from playwright.async_api import expect

async def run(page, variables):
    # 打开列表页并验证结果区域。
    await page.goto('/')
    await expect(page.locator('#result')).to_be_visible()
'''


def snapshot(revision: int, *, completion: str = 'partial') -> dict:
    return {
        'schema_version': 5,
        'events': [{'event_id': f'E{revision}', 'status': 'observed'}],
        'page_states': [], 'locator_evidence': [], 'tool_stats': {'calls': revision},
        'termination_reason': '', 'final_message': '',
        'artifact': {
            'revision': revision, 'completion': completion,
            'completed_steps': ['打开列表'],
            'remaining_steps': ['详情页操作'] if completion == 'partial' else [],
            'variables': [],
        },
    }


class ScriptArtifactWorkflowTests(TestCase):
    """These tests use no model, MCP client, browser, or real task worker."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='v5-artifact-owner', email='v5-artifact@example.test', password='test-password',
        )
        self.project = Project.objects.create(
            name='v5 artifact', project_type='web', owner=self.user, created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='Web', category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'}, is_active=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='test', api_key='key',
            base_url='https://llm.example.test', model_name='test-model', is_active=True,
            created_by=self.user,
        )

    def generation(self, *, task_id: str = 'worker-a'):
        return WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user, environment=self.environment,
            status=WebUIScriptGeneration.Status.EXPLORING,
            current_stage=WebUIScriptGeneration.Stage.EXPLORING,
            description_safe='检查列表', target_url_safe='https://web.example.test/items',
            model_info={'config_id': self.model.id}, celery_task_id=task_id,
            workspace={'revision': 0, '_agent_run': {'generation_revision': 0, 'task_id': task_id}},
        )

    def test_partial_checkpoint_persists_draft_history_and_invalidates_verification(self):
        generation = self.generation()
        artifact_snapshot = snapshot(1)
        artifact_snapshot['artifact']['variables'] = [{
            'name': 'ITEM_NAME', 'value': 'temporary value', 'is_secret': False,
            'required': True, 'description': '本轮测试数据',
        }]
        self.assertTrue(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='worker-a',
            script_draft=PARTIAL_SCRIPT, snapshot=artifact_snapshot,
        ))
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, PARTIAL_SCRIPT)
        self.assertEqual(generation.workspace['revision'], 1)
        self.assertEqual(generation.workspace['verification']['status'], 'unverified')
        self.assertEqual(generation.workspace['variables'][0]['name'], 'ITEM_NAME')
        self.assertEqual(generation.workspace['variables'][0]['value'], 'temporary value')
        self.assertEqual(generation.workspace['artifact_history'][0]['script_draft'], PARTIAL_SCRIPT)
        self.assertEqual(generation.exploration_snapshot['artifact_history'][0]['artifact']['revision'], 1)

    def test_cancelled_or_different_worker_checkpoint_never_overwrites(self):
        generation = self.generation()
        self.assertFalse(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='worker-b',
            script_draft=PARTIAL_SCRIPT, snapshot=snapshot(1),
        ))
        generation.status = WebUIScriptGeneration.Status.CANCELLED
        generation.save(update_fields=['status', 'updated_at'])
        self.assertFalse(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='worker-a',
            script_draft=PARTIAL_SCRIPT, snapshot=snapshot(1),
        ))
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, '')
        self.assertEqual(generation.workspace['revision'], 0)

    def test_final_error_keeps_latest_checkpoint_source_and_reason(self):
        from .generation_orchestrator import _persist_agent_result

        generation = self.generation()
        self.assertTrue(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='worker-a',
            script_draft=PARTIAL_SCRIPT, snapshot=snapshot(2),
        ))
        result = _persist_agent_result(
            generation, task_id='worker-a', script_draft=SCRIPT, snapshot=snapshot(1),
            completion='complete', error_code='MODEL_SERVICE_ERROR',
            error_message='模型服务中断', final_message='',
        )
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.error_code, 'MODEL_SERVICE_ERROR')
        self.assertEqual(generation.current_stage, WebUIScriptGeneration.Stage.EXPLORING)
        self.assertEqual(generation.exploration_snapshot['actual_failure_stage'], WebUIScriptGeneration.Stage.EXPLORING)
        self.assertEqual(generation.script_draft, PARTIAL_SCRIPT)
        self.assertEqual(generation.exploration_snapshot['artifact']['revision'], 2)

    def test_stale_final_result_cannot_replace_active_worker_artifact(self):
        generation = self.generation()
        self.assertIsNone(finalize_generation_artifact(
            generation.pk, generation_revision=0, task_id='worker-b',
            target_status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
            script_draft=SCRIPT, snapshot=snapshot(1), quality_report={}, variables=[], warnings=[],
            error_code='MODEL_SERVICE_ERROR', error_message='stale',
        ))
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.EXPLORING)
        self.assertEqual(generation.script_draft, '')

    def test_cancelled_record_with_draft_can_enter_code_only_retry(self):
        generation = self.generation()
        generation.status = WebUIScriptGeneration.Status.CANCELLED
        generation.current_stage = WebUIScriptGeneration.Stage.COMPLETED
        generation.script_draft = PARTIAL_SCRIPT
        generation.scenario_spec = {'schema_version': 5}
        generation.exploration_snapshot = snapshot(1)
        generation.save(update_fields=[
            'status', 'current_stage', 'script_draft', 'scenario_spec',
            'exploration_snapshot', 'updated_at',
        ])
        retried = prepare_trace_generation_retry(generation.pk, expected_revision=0)
        self.assertEqual(retried.status, WebUIScriptGeneration.Status.GENERATING)
        self.assertEqual(retried.revision, 1)

    def test_retry_worker_claims_task_id_before_dispatcher_attaches_it(self):
        generation = self.generation()
        generation.status = WebUIScriptGeneration.Status.GENERATING
        generation.celery_task_id = None
        generation.save(update_fields=['status', 'celery_task_id'])
        claimed = claim_trace_generation_retry(generation.pk, 'fast-retry')
        self.assertEqual(claimed.celery_task_id, 'fast-retry')
        self.assertTrue(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='fast-retry',
            script_draft=PARTIAL_SCRIPT, snapshot=snapshot(1),
        ))
        self.assertIsNone(claim_trace_generation_retry(generation.pk, 'different-retry'))

    def test_fake_complete_agent_finishes_ready_from_exploring(self):
        from .generation_orchestrator import run_generation

        generation = self.generation()
        generation.status = WebUIScriptGeneration.Status.CREATED
        generation.current_stage = WebUIScriptGeneration.Stage.CREATED
        generation.celery_task_id = None
        generation.workspace = {}
        generation.save(update_fields=['status', 'current_stage', 'celery_task_id', 'workspace', 'updated_at'])

        class FakeAgent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **_kwargs):
                return SimpleNamespace(
                    script_draft=COMPLETE_SCRIPT,
                    snapshot={**snapshot(1, completion='complete'), 'events': [{'event_id': 'E1'}]},
                    error_code='', error_message='', final_message='草稿完成。', completion='complete',
                )

        preflight = SimpleNamespace(outcome='continue', warnings=[], mcp_config={})
        with patch('web_testing.generation_orchestrator.run_safety_preflight', return_value=preflight), patch(
            'web_testing.generation_orchestrator.get_llm_manager',
            return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.script_exploration_agent.ScriptExplorationAgent', FakeAgent), patch(
            'web_testing.generation_orchestrator.publish_stage_changed',
        ), patch('web_testing.generation_orchestrator.publish_terminal'):
            result = run_generation(str(generation.pk), celery_task_id='worker-a')
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY)
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.READY)
        self.assertEqual(generation.current_stage, WebUIScriptGeneration.Stage.COMPLETED)
        self.assertEqual(generation.script_draft, COMPLETE_SCRIPT)
