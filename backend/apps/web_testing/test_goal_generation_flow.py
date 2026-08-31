"""Offline integration checks for side-effect-aware generation orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from .generation_contracts import ExplorationSnapshot, ScenarioSpec
from .generation_orchestrator import run_v2_generation, _map_explorer_error, _model_failure
from .generation_repository import claim_generation_worker
from .mcp_page_explorer import MCPPageExplorerError
from .models import WebUIScriptGeneration
from .test_generation_phase45 import Phase45Base, VALID_SCRIPT, scenario_payload, snapshot_payload
from .test_model_service_errors import openai_status_error


class GoalGenerationFlowTests(Phase45Base):
    def run_pipeline(self, generation, snapshot=None, *, normalize_hook=None, explore_error=None):
        snapshot = snapshot or ExplorationSnapshot.model_validate(snapshot_payload())
        explorer = SimpleNamespace(explore_until_complete=AsyncMock(return_value=snapshot, side_effect=explore_error))
        generator = SimpleNamespace(generate=Mock(return_value=VALID_SCRIPT), repair=Mock(return_value=VALID_SCRIPT))
        with patch('web_testing.generation_orchestrator.normalize_requirement', side_effect=normalize_hook,
                   return_value=ScenarioSpec.model_validate(scenario_payload())), patch(
            'web_testing.generation_orchestrator.get_llm_manager', return_value=SimpleNamespace(current_llm=object()),
        ), patch('web_testing.generation_orchestrator.MCPPageExplorer', return_value=explorer) as constructor, patch(
            'web_testing.generation_orchestrator.ScriptGenerator', return_value=generator,
        ), patch('web_testing.generation_orchestrator.publish_stage_changed'), patch('web_testing.generation_orchestrator.publish_terminal'):
            result = run_v2_generation(str(generation.pk), celery_task_id='goal-flow-task')
        return result, constructor, explorer, generator

    def test_duplicate_delivery_does_not_reenter_exploration_or_change_active_state(self):
        generation = self.generation()
        duplicate_results = []

        def normalize(*args):
            duplicate_results.append(run_v2_generation(str(generation.pk), celery_task_id='goal-flow-task'))
            duplicate_results.append(run_v2_generation(str(generation.pk), celery_task_id='stale-other-task'))
            return ScenarioSpec.model_validate(scenario_payload())

        result, _, explorer, _ = self.run_pipeline(generation, normalize_hook=normalize)
        self.assertTrue(all(item['skipped'] for item in duplicate_results))
        self.assertNotEqual(result['status'], WebUIScriptGeneration.Status.FAILED)
        explorer.explore_until_complete.assert_awaited_once()

    def test_previous_revision_task_cannot_claim_a_resumed_generation(self):
        generation = self.generation()
        self.assertIsNotNone(claim_generation_worker(generation.pk, 'old-task'))
        generation.refresh_from_db()
        generation.revision += 1
        generation.celery_task_id = None
        generation.status = WebUIScriptGeneration.Status.NORMALIZING
        generation.save()
        self.assertIsNone(claim_generation_worker(generation.pk, 'old-task'))
        self.assertIsNotNone(claim_generation_worker(generation.pk, 'new-task'))

    def test_original_user_constraints_reach_explorer(self):
        generation = self.generation(description_safe='探索阶段只查看页面；后续脚本生成测试数据。')
        _, constructor, _, _ = self.run_pipeline(generation)
        self.assertEqual(constructor.call_args.kwargs['user_constraints'], generation.description_safe)

    def test_cleanup_residual_keeps_script_and_requires_review(self):
        generation = self.generation()
        payload = snapshot_payload()
        payload.update({
            'exploration_namespace': 'aits-explore-fixture',
            'cleanup_report': {'status': 'residual', 'attempted': True, 'residuals': ['aits-explore-fixture-user']},
            'warnings': ['本轮测试数据清理未完成。'],
        })
        result, _, _, generator = self.run_pipeline(generation, ExplorationSnapshot.model_validate(payload))
        generation.refresh_from_db()
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.error_code, 'EXPLORATION_CLEANUP_UNCONFIRMED')
        self.assertTrue(generation.script_draft)
        self.assertIn('本轮测试数据清理未完成。', generation.warnings)
        generator.generate.assert_called_once()

    def test_provider_504_after_exploration_preserves_failure_evidence_without_replay(self):
        generation = self.generation()
        payload = snapshot_payload()
        payload['warnings'] = ['模型服务中断，需人工检查本轮数据。']
        failure = MCPPageExplorerError('other', 'wrapped model failure', snapshot=ExplorationSnapshot.model_validate(payload))
        failure.__cause__ = openai_status_error(504)
        result, _, explorer, generator = self.run_pipeline(generation, explore_error=failure)
        generation.refresh_from_db()
        self.assertEqual(result['error_code'], 'MODEL_GATEWAY_TIMEOUT')
        self.assertIn('504', generation.error_message)
        self.assertEqual(generation.exploration_snapshot['visited_paths'], ['/'])
        self.assertIn(payload['warnings'][0], generation.warnings)
        explorer.explore_until_complete.assert_awaited_once()
        generator.generate.assert_not_called()

    def test_model_error_mapping_does_not_override_cancellation(self):
        failure = MCPPageExplorerError('TASK_CANCELLED', 'cancelled')
        failure.__context__ = openai_status_error(500)
        self.assertEqual(_map_explorer_error(failure)[0], 'TASK_CANCELLED')
        self.assertEqual(_model_failure(openai_status_error(503))[0], 'MODEL_SERVICE_ERROR')
