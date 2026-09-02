"""v4 ScenarioPlan -> continuous callback ledger -> deterministic replay."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.core.cache import cache

from ai_core.model_manager import get_llm_manager

from .exploration_timeout import exploration_total_timeout_seconds
from .exploration_trace import ExplorationTrace, coerce_trace, required_replay_evidence_gaps, trace_has_minimum_page_state
from .generation_contracts import GenerationContractError, ScenarioInputInsufficientError, ScenarioPlan, is_terminal_status
from .generation_events import publish_stage_changed, publish_terminal
from .generation_preflight import environment_credentials, run_safety_preflight
from .generation_repository import PAUSED_GENERATION_STATUSES, claim_generation_worker, claim_trace_generation_retry, get_generation_temporary_credentials, is_cancel_requested, transition_generation
from .generation_workspace import variable_definitions_for_scenario_plan, workspace_for_generation
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError
from .models import WebUIScriptGeneration
from .requirement_normalizer import normalize_requirement
from .script_generator import ScriptGenerator
from .script_quality import blocker_issues, evaluate_script

logger = logging.getLogger(__name__)


def _normalization_description(generation: WebUIScriptGeneration) -> str:
    additions = []
    for item in generation.clarifications or []:
        additions.extend(str(answer.get('answer') or '') for answer in item.get('answers') or [] if answer.get('answer'))
    return '\n\n'.join([generation.description_safe, *additions])


def _fail(generation_id: str, code: str, message: str) -> dict[str, Any]:
    logger.warning('WebUI v4 generation stopped: generation_id=%s error_code=%s', generation_id, code)
    try:
        generation = transition_generation(generation_id, WebUIScriptGeneration.Status.FAILED, progress=100, error_code=code, error_message=message)
        publish_terminal(generation)
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': code}
    except Exception:
        return {'generation_id': generation_id, 'status': 'failed', 'error_code': code}


def _terminal_cancel(generation_id: str, task_id: str | None) -> bool:
    return bool(task_id and cache.get(f'celery:cancel:{task_id}')) or is_cancel_requested(generation_id)


def _compile_persisted(generation: WebUIScriptGeneration, plan: ScenarioPlan, trace: ExplorationTrace) -> dict[str, Any]:
    generation = transition_generation(
        generation.pk, WebUIScriptGeneration.Status.VALIDATING, progress=85,
        updates={'exploration_snapshot': trace.model_dump(mode='json'), 'tool_stats': trace.tool_stats},
    )
    publish_stage_changed(generation, '检查脚本')
    evidence_gaps = required_replay_evidence_gaps(trace, plan)
    if evidence_gaps:
        workspace = workspace_for_generation(generation)
        workspace['variables'] = variable_definitions_for_scenario_plan(plan)
        code = trace.finalization.error_code or 'FINALIZATION_REQUIRED'
        completed = transition_generation(
            generation.pk, WebUIScriptGeneration.Status.NEEDS_REVIEW, progress=100,
            error_code=code,
            error_message='最终路径定稿缺失或无效，已保留探索轨迹但未生成脚本草稿。',
            updates={
                'exploration_snapshot': trace.model_dump(mode='json'), 'script_draft': '',
                'workspace': workspace,
                'quality_report': {'status': 'blocked', 'blockers': [{
                    'level': 'blocker', 'code': code,
                    'message': '最终路径定稿、入口或断言证据未满足，不能生成脚本。',
                }], 'warnings': [], 'replay_plan': {}},
                'tool_stats': trace.tool_stats,
                'warnings': [f"{item['event_id']}：{item['reason']}" for item in evidence_gaps],
            },
        )
        publish_terminal(completed)
        return {'generation_id': str(completed.pk), 'status': completed.status, 'quality_status': 'blocked', 'error_code': completed.error_code}
    script, replay_plan = ScriptGenerator().generate(plan=plan, trace=trace)
    report = evaluate_script(script, plan=plan, trace=trace, replay_plan=replay_plan)
    blockers = blocker_issues(report)
    incomplete = not replay_plan.actions
    if blockers:
        status, error_code, error_message = (
            WebUIScriptGeneration.Status.NEEDS_REVIEW, 'SCRIPT_QUALITY_BLOCKED', '回放计划或脚本质量门禁未通过。',
        )
    elif incomplete:
        status, error_code, error_message = (
            WebUIScriptGeneration.Status.NEEDS_REVIEW, 'EXPLORATION_EVIDENCE_INCOMPLETE',
            '探索证据不完整，未生成脚本草稿。',
        )
    else:
        status = WebUIScriptGeneration.Status.READY_WITH_WARNINGS if report['warnings'] else WebUIScriptGeneration.Status.READY
        error_code = error_message = ''
    workspace = workspace_for_generation(generation)
    workspace['variables'] = variable_definitions_for_scenario_plan(plan)
    warnings = [item['message'] for item in report['warnings']]
    warnings.extend(f"{item['event_id']}：{item['reason']}" for item in evidence_gaps)
    completed = transition_generation(
        generation.pk, status, progress=100, error_code=error_code, error_message=error_message,
        updates={
            'exploration_snapshot': trace.model_dump(mode='json'), 'script_draft': '' if incomplete else script, 'workspace': workspace,
            'quality_report': {**report, 'replay_plan': replay_plan.model_dump(mode='json')},
            'tool_stats': trace.tool_stats, 'warnings': list(dict.fromkeys(warnings)),
        },
    )
    publish_terminal(completed)
    return {'generation_id': str(completed.pk), 'status': completed.status, 'quality_status': report['status'], 'error_code': completed.error_code}


def run_generation(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    """Run a fresh v4 generation; old plans and traces are intentionally rejected."""
    try:
        generation = WebUIScriptGeneration.objects.select_related('environment', 'test_case', 'user').get(pk=generation_id)
    except Exception:
        return {'generation_id': str(generation_id), 'status': 'failed', 'error_code': 'TRANSIENT_SERVICE_ERROR'}
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled'}
    if generation.status in PAUSED_GENERATION_STATUSES or is_terminal_status(generation.status):
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': generation.error_code}
    generation = claim_generation_worker(generation.pk, celery_task_id)
    if generation is None:
        return {'generation_id': str(generation_id), 'status': 'skipped'}
    try:
        if generation.status == WebUIScriptGeneration.Status.CREATED:
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.NORMALIZING,
                progress=10,
            )
        if generation.status == WebUIScriptGeneration.Status.NORMALIZING:
            publish_stage_changed(generation, '理解测试目标')
            plan = normalize_requirement(_normalization_description(generation), model_config_id=generation.model_info['config_id'], test_case_context={'title': generation.test_case.title} if generation.test_case else None)
            generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.PREFLIGHTING, progress=25, updates={'scenario_spec': plan.model_dump(mode='json'), 'credentials_required': plan.credentials_required})
        elif generation.status == WebUIScriptGeneration.Status.PREFLIGHTING:
            plan = ScenarioPlan.model_validate(generation.scenario_spec or {})
        else:
            return _fail(str(generation.pk), 'TRANSIENT_SERVICE_ERROR', '当前生成阶段不能继续。')
    except ScenarioInputInsufficientError:
        return _fail(str(generation.pk), 'INPUT_AMBIGUOUS', '描述缺少明确测试对象，请补充目标。')
    except GenerationContractError as exc:
        logger.warning(
            'WebUI v4 ScenarioPlan rejected: generation_id=%s stage=normalizing diagnostics=%s',
            generation.pk, list(exc.diagnostics),
        )
        return _fail(str(generation.pk), 'MODEL_OUTPUT_INVALID', '模型未返回有效的 v4 ScenarioPlan。')
    except (KeyError, ValueError):
        return _fail(str(generation.pk), 'MODEL_OUTPUT_INVALID', '模型未返回有效的 v4 ScenarioPlan。')
    credentials = get_generation_temporary_credentials(generation.pk) or environment_credentials(generation.environment)
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
        publish_stage_changed(generation, '连续探索页面')
        explorer = MCPPageExplorer(llm_model=manager.current_llm, mcp_config=preflight.mcp_config or {}, generation_id=str(generation.pk), user_constraints=_normalization_description(generation), cancel_check=lambda: _terminal_cancel(str(generation.pk), celery_task_id), exploration_timeout_seconds=generation.exploration_timeout_seconds or exploration_total_timeout_seconds())
        trace = asyncio.run(explorer.explore_until_complete(plan=plan, start_path=generation.start_path, target_url_safe=generation.target_url_safe, temporary_credentials=credentials))
    except MCPPageExplorerError as exc:
        if exc.error_code == 'TASK_CANCELLED' or _terminal_cancel(
            str(generation.pk), celery_task_id,
        ):
            return {
                'generation_id': str(generation.pk),
                'status': WebUIScriptGeneration.Status.CANCELLED,
                'error_code': 'TASK_CANCELLED',
            }
        if exc.snapshot is None:
            return _fail(str(generation.pk), exc.error_code, str(exc))
        trace = exc.snapshot
    except Exception:
        logger.exception('页面探索执行发生未知异常: generation_id=%s, stage=exploring', generation.pk)
        return _fail(str(generation.pk), 'INTERNAL_EXPLORATION_ERROR', '页面探索服务发生内部错误，请稍后重试。')
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {
            'generation_id': str(generation.pk),
            'status': WebUIScriptGeneration.Status.CANCELLED,
            'error_code': 'TASK_CANCELLED',
        }
    if not trace_has_minimum_page_state(trace):
        return _fail(str(generation.pk), 'EXPLORATION_NO_PAGE_STATE', '未取得可用页面观察，未生成脚本。')
    generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.GENERATING, progress=70, updates={'exploration_snapshot': trace.model_dump(mode='json'), 'tool_stats': trace.tool_stats})
    publish_stage_changed(generation, '整理回放路径')
    return _compile_persisted(generation, plan, trace)


def run_generation_from_trace(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    generation = claim_trace_generation_retry(generation_id, celery_task_id)
    if generation is None:
        return {'generation_id': str(generation_id), 'status': 'skipped'}
    try:
        plan = ScenarioPlan.model_validate(generation.scenario_spec or {})
        trace = coerce_trace(generation.exploration_snapshot or {})
    except Exception:
        return _fail(str(generation_id), 'V4_TRACE_INVALID', '已保存的 v4 连续探索轨迹无法继续编译。')
    return _compile_persisted(generation, plan, trace)
