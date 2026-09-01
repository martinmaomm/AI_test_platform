"""v3 GoalPlan -> callback ledger -> deterministic replay orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.core.cache import cache

from ai_core.model_manager import get_llm_manager

from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import ExplorationTrace, coerce_trace, required_goal_evidence_gaps, trace_has_minimum_page_state
from .generation_contracts import GenerationContractError, GoalPlan, ScenarioInputInsufficientError, is_terminal_status
from .generation_events import publish_stage_changed, publish_terminal
from .generation_preflight import environment_credentials, run_safety_preflight
from .generation_repository import PAUSED_GENERATION_STATUSES, claim_generation_worker, claim_trace_generation_retry, get_generation_temporary_credentials, is_cancel_requested, transition_generation
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError
from .models import WebUIScriptGeneration
from .requirement_normalizer import normalize_requirement
from .script_generator import ScriptGenerator
from .script_quality import blocker_issues, evaluate_script
from .generation_workspace import variable_definitions_for_goal_plan, workspace_for_generation

logger = logging.getLogger(__name__)


def _normalization_description(generation: WebUIScriptGeneration) -> str:
    additions = []
    for item in generation.clarifications or []:
        additions.extend(str(answer.get('answer') or '') for answer in item.get('answers') or [] if answer.get('answer'))
    return '\n\n'.join([generation.description_safe, *additions])


def _review_report(gaps: list[dict[str, str]]) -> dict[str, Any]:
    blockers = [{'level': 'blocker', 'code': 'GOAL_EVIDENCE_MISSING', 'message': f"Goal {item['goal_id']}：{item['reason']}", 'line': None} for item in gaps]
    return {'status': 'needs_review', 'blockers': blockers, 'warnings': [], 'checks': blockers, 'summary': {'passed': 0, 'warning': 0, 'blocker': len(blockers)}}


def _fail(generation_id: str, code: str, message: str) -> dict[str, Any]:
    logger.warning('WebUI v3 generation stopped: generation_id=%s error_code=%s', generation_id, code)
    try:
        generation = transition_generation(generation_id, WebUIScriptGeneration.Status.FAILED, progress=100, error_code=code, error_message=message)
        publish_terminal(generation)
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': code}
    except Exception:
        return {'generation_id': generation_id, 'status': 'failed', 'error_code': code}


def _terminal_cancel(generation_id: str, task_id: str | None) -> bool:
    return bool(task_id and cache.get(f'celery:cancel:{task_id}')) or is_cancel_requested(generation_id)


def _compile_persisted(generation: WebUIScriptGeneration, plan: GoalPlan, trace: ExplorationTrace) -> dict[str, Any]:
    gaps = required_goal_evidence_gaps(plan, trace)
    if gaps:
        reviewed = transition_generation(generation.pk, WebUIScriptGeneration.Status.NEEDS_REVIEW, progress=100, error_code='GOAL_EVIDENCE_MISSING', error_message='部分 Goal 缺少可回放证据，未生成伪完整脚本。', updates={'exploration_snapshot': trace.model_dump(mode='json'), 'quality_report': _review_report(gaps), 'tool_stats': trace.tool_stats, 'warnings': [f"Goal {item['goal_id']}：{item['reason']}" for item in gaps]})
        publish_terminal(reviewed)
        return {'generation_id': str(reviewed.pk), 'status': reviewed.status, 'error_code': reviewed.error_code}
    script, replay_plan = ScriptGenerator().generate(plan=plan, trace=trace)
    report = evaluate_script(script, plan=plan, trace=trace, replay_plan=replay_plan)
    blockers = blocker_issues(report)
    status = WebUIScriptGeneration.Status.NEEDS_REVIEW if blockers else (WebUIScriptGeneration.Status.READY_WITH_WARNINGS if report['warnings'] else WebUIScriptGeneration.Status.READY)
    workspace = workspace_for_generation(generation)
    workspace['variables'] = variable_definitions_for_goal_plan(plan)
    completed = transition_generation(generation.pk, status, progress=100, error_code='SCRIPT_QUALITY_BLOCKED' if blockers else '', error_message='回放计划或脚本质量门禁未通过。' if blockers else '', updates={'exploration_snapshot': trace.model_dump(mode='json'), 'script_draft': script, 'workspace': workspace, 'quality_report': {**report, 'replay_plan': replay_plan.model_dump(mode='json')}, 'tool_stats': trace.tool_stats, 'warnings': [item['message'] for item in report['warnings']]})
    publish_terminal(completed)
    return {'generation_id': str(completed.pk), 'status': completed.status, 'quality_status': report['status']}


def run_generation(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    """Run a fresh v3 generation. Persisted v2 JSON is intentionally rejected."""
    try:
        generation = WebUIScriptGeneration.objects.select_related('environment', 'test_case', 'user').get(pk=generation_id)
    except Exception:
        return {'generation_id': str(generation_id), 'status': 'failed', 'error_code': 'TRANSIENT_SERVICE_ERROR'}
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled'}
    if generation.status in PAUSED_GENERATION_STATUSES or is_terminal_status(generation.status):
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': generation.error_code}
    claimed = claim_generation_worker(generation.pk, celery_task_id)
    if claimed is None:
        generation.refresh_from_db()
        return {'generation_id': str(generation.pk), 'status': generation.status, 'skipped': True}
    generation = claimed
    try:
        if generation.status in {WebUIScriptGeneration.Status.CREATED, WebUIScriptGeneration.Status.NORMALIZING}:
            publish_stage_changed(generation, '理解测试目标')
            plan = normalize_requirement(_normalization_description(generation), model_config_id=generation.model_info['config_id'], test_case_context={'title': generation.test_case.title} if generation.test_case else None)
            generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.PREFLIGHTING, progress=25, updates={'scenario_spec': plan.model_dump(mode='json'), 'credentials_required': plan.credentials_required})
        elif generation.status == WebUIScriptGeneration.Status.PREFLIGHTING:
            plan = GoalPlan.model_validate(generation.scenario_spec or {})
        else:
            return _fail(str(generation.pk), 'TRANSIENT_SERVICE_ERROR', '当前生成阶段不能继续。')
    except ScenarioInputInsufficientError:
        return _fail(str(generation.pk), 'INPUT_AMBIGUOUS', '描述缺少明确测试对象，请补充目标。')
    except (GenerationContractError, KeyError, ValueError):
        return _fail(str(generation.pk), 'MODEL_OUTPUT_INVALID', '模型未返回有效的 v3 GoalPlan。')
    credentials = (
        get_generation_temporary_credentials(generation.pk)
        or environment_credentials(generation.environment)
    )
    preflight = run_safety_preflight(generation, plan, credentials_available=credentials is not None)
    if preflight.outcome != 'continue':
        status = {'needs_credentials': WebUIScriptGeneration.Status.NEEDS_CREDENTIALS, 'needs_confirmation': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION, 'failed': WebUIScriptGeneration.Status.FAILED}[preflight.outcome]
        paused = transition_generation(generation.pk, status, progress=25, error_code=preflight.error_code, error_message=preflight.message, updates={'warnings': preflight.warnings})
        if status == WebUIScriptGeneration.Status.FAILED:
            publish_terminal(paused)
        return {'generation_id': str(paused.pk), 'status': paused.status, 'error_code': paused.error_code}
    try:
        manager = get_llm_manager(config_id=generation.model_info['config_id'])
        generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.EXPLORING, progress=45, updates={'warnings': preflight.warnings})
        publish_stage_changed(generation, '按目标探索页面')
        explorer = MCPPageExplorer(llm_model=manager.current_llm, mcp_config=preflight.mcp_config or {}, generation_id=str(generation.pk), user_constraints=_normalization_description(generation), cancel_check=lambda: _terminal_cancel(str(generation.pk), celery_task_id), exploration_timeout_seconds=generation.exploration_timeout_seconds or exploration_total_timeout_seconds())
        trace = asyncio.run(explorer.explore_until_complete(plan=plan, start_path=generation.start_path, target_url_safe=generation.target_url_safe, temporary_credentials=credentials))
    except MCPPageExplorerError as exc:
        if exc.snapshot is None:
            return _fail(str(generation.pk), exc.error_code, str(exc))
        trace = exc.snapshot
    except Exception:
        return _fail(str(generation.pk), 'BROWSER_UNAVAILABLE', '页面探索服务暂时不可用。')
    if not trace_has_minimum_page_state(trace):
        return _fail(str(generation.pk), 'EXPLORATION_NO_PAGE_STATE', '未取得可用页面观察，未生成脚本。')
    generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.GENERATING, progress=70, updates={'exploration_snapshot': trace.model_dump(mode='json'), 'tool_stats': trace.tool_stats})
    publish_stage_changed(generation, '整理可回放路径')
    return _compile_persisted(generation, plan, trace)

def run_generation_from_trace(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    generation = claim_trace_generation_retry(generation_id, celery_task_id)
    if generation is None:
        return {'generation_id': str(generation_id), 'status': 'skipped'}
    try:
        plan = GoalPlan.model_validate(generation.scenario_spec or {})
        trace = coerce_trace(generation.exploration_snapshot or {})
    except Exception:
        return _fail(str(generation_id), 'V3_TRACE_INVALID', '已保存的 v3 轨迹无法继续编译。')
    return _compile_persisted(generation, plan, trace)
