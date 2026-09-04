"""v5 brief -> preflight -> one script exploration agent -> static draft quality."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.core.cache import cache

from ai_core.model_manager import get_llm_manager

from .exploration_timeout import exploration_total_timeout_seconds
from .generation_events import publish_stage_changed, publish_terminal
from .generation_preflight import run_safety_preflight
from .generation_repository import (
    PAUSED_GENERATION_STATUSES, claim_generation_worker, claim_trace_generation_retry,
    finalize_generation_artifact, is_cancel_requested,
    persist_generation_checkpoint, transition_generation,
)
from .generation_workspace import evaluate_workspace_draft, workspace_for_generation
from .model_service_errors import classify_model_service_error
from .models import WebUIScriptGeneration

logger = logging.getLogger(__name__)


def _terminal_cancel(generation_id: str, task_id: str | None) -> bool:
    return bool(task_id and cache.get(f'celery:cancel:{task_id}')) or is_cancel_requested(generation_id)


def _fail(generation_id: str, code: str, message: str) -> dict[str, Any]:
    logger.warning('WebUI v5 generation stopped: generation_id=%s error_code=%s', generation_id, code)
    try:
        generation = transition_generation(
            generation_id, WebUIScriptGeneration.Status.FAILED, progress=100,
            error_code=code, error_message=message,
        )
        publish_terminal(generation)
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': code}
    except Exception:
        logger.exception('无法持久化生成失败状态: generation_id=%s', generation_id)
        return {'generation_id': str(generation_id), 'status': 'persistence_failed', 'error_code': 'PERSISTENCE_FAILED'}


def fail_unexpected_generation(generation_id: str) -> dict[str, Any]:
    try:
        generation = WebUIScriptGeneration.objects.only('pk', 'status', 'error_code').get(pk=generation_id)
    except Exception:
        return {'generation_id': str(generation_id), 'status': 'persistence_failed', 'error_code': 'INTERNAL_GENERATION_ERROR'}
    if generation.status in {
        WebUIScriptGeneration.Status.READY, WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
        WebUIScriptGeneration.Status.NEEDS_REVIEW, WebUIScriptGeneration.Status.FAILED,
        WebUIScriptGeneration.Status.CANCELLED,
    }:
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': generation.error_code}
    return _fail(str(generation.pk), 'INTERNAL_GENERATION_ERROR', '生成任务发生内部错误，请稍后重试。')


def _brief_for_generation(generation: WebUIScriptGeneration) -> dict[str, Any]:
    """Build only the deterministic v5 brief; no preflight model normalizer."""
    from .generation_brief import build_generation_brief

    title = generation.test_case.title if generation.test_case_id and generation.test_case else ''
    return build_generation_brief(generation.description_safe, title=title).model_dump(mode='json')


def _snapshot(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    artifact = dict(raw.get('artifact') or {}) if isinstance(raw.get('artifact'), dict) else {}
    try:
        artifact_revision = max(0, int(artifact.get('revision') or 0))
    except (TypeError, ValueError):
        artifact_revision = 0
    artifact.update({
        'revision': artifact_revision,
        'completion': str(artifact.get('completion') or 'unknown'),
        'completed_steps': artifact.get('completed_steps') if isinstance(artifact.get('completed_steps'), list) else [],
        'remaining_steps': artifact.get('remaining_steps') if isinstance(artifact.get('remaining_steps'), list) else [],
        'variables': artifact.get('variables') if isinstance(artifact.get('variables'), list) else [],
    })
    raw.update({
        'schema_version': 5,
        'events': raw.get('events') if isinstance(raw.get('events'), list) else [],
        'page_states': raw.get('page_states') if isinstance(raw.get('page_states'), list) else [],
        'locator_evidence': raw.get('locator_evidence') if isinstance(raw.get('locator_evidence'), list) else [],
        'tool_stats': raw.get('tool_stats') if isinstance(raw.get('tool_stats'), dict) else {},
        'termination_reason': str(raw.get('termination_reason') or ''),
        'final_message': str(raw.get('final_message') or ''),
        'artifact': artifact,
        'artifact_history': raw.get('artifact_history') if isinstance(raw.get('artifact_history'), list) else [],
    })
    return raw


def _workspace_variables(snapshot: dict[str, Any], generation: WebUIScriptGeneration) -> list[dict[str, Any]]:
    from .execution_variables import normalize_variable_definitions

    try:
        return normalize_variable_definitions((snapshot.get('artifact') or {}).get('variables') or [])
    except (TypeError, ValueError):
        return workspace_for_generation(generation)['variables']


def _checkpoint_callback(generation: WebUIScriptGeneration):
    generation_id = str(generation.pk)
    run_revision = generation.revision
    task_marker = generation.celery_task_id or '<direct>'

    def persist(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return persist_generation_checkpoint(
            generation_id, generation_revision=run_revision, task_id=task_marker,
            script_draft=str(payload.get('script_draft') or ''),
            snapshot=_snapshot(payload.get('snapshot')),
        )

    return persist


def _persist_agent_result(
    generation: WebUIScriptGeneration, *, task_id: str, script_draft: str, snapshot: dict[str, Any],
    completion: str, error_code: str, error_message: str, final_message: str,
) -> dict[str, Any]:
    """Persist a v5 artifact and code-quality status without finalization gates."""
    snapshot = _snapshot(snapshot)
    # Checkpoints may have completed while the agent was returning.  Use their
    # latest durable artifact for quality evaluation; the repository repeats
    # ownership checks under its final row lock before it writes anything.
    try:
        current = WebUIScriptGeneration.objects.only('script_draft', 'exploration_snapshot').get(pk=generation.pk)
    except WebUIScriptGeneration.DoesNotExist:
        return {'generation_id': str(generation.pk), 'status': 'stale', 'error_code': 'STALE_AGENT_RESULT'}
    current_snapshot = _snapshot(current.exploration_snapshot)
    try:
        current_revision = int((current_snapshot.get('artifact') or {}).get('revision') or 0)
        incoming_revision = int((snapshot.get('artifact') or {}).get('revision') or 0)
    except (TypeError, ValueError):
        current_revision = incoming_revision = 0
    if current_revision > incoming_revision:
        snapshot = current_snapshot
        script_draft = current.script_draft
    elif not script_draft.strip():
        script_draft = current.script_draft
    if final_message:
        snapshot['final_message'] = final_message
    if error_code:
        snapshot['termination_reason'] = error_code
    if script_draft.strip():
        try:
            report = evaluate_workspace_draft(
                script_draft, target_url=generation.target_url, snapshot=snapshot,
            )
        except Exception as exc:
            logger.exception('脚本静态检查不可用: generation_id=%s', generation.pk)
            report = {
                'status': 'needs_review', 'completion': completion,
                'blockers': [{'level': 'blocker', 'code': 'DRAFT_QUALITY_UNAVAILABLE', 'message': str(exc)}],
                'warnings': [],
            }
    else:
        report = {
            'status': 'needs_review', 'completion': completion,
            'blockers': [{'level': 'blocker', 'code': error_code or 'EMPTY_DRAFT', 'message': error_message or '未生成可保存的脚本草稿。'}],
            'warnings': [],
        }
    static_completion = str(report.get('completion') or 'unknown')
    report['completion'] = (
        'complete' if completion == 'complete' and static_completion == 'complete'
        else 'partial' if completion == 'partial' or static_completion == 'partial'
        else 'unknown'
    )
    blockers = list(report.get('blockers') or [])
    warnings = [str(item.get('message') or '') for item in report.get('warnings') or [] if isinstance(item, dict)]
    if error_message:
        warnings.append(error_message)
    if not script_draft.strip() or blockers or error_code or report['completion'] != 'complete':
        status = WebUIScriptGeneration.Status.NEEDS_REVIEW
    elif report.get('status') == 'ready':
        status = WebUIScriptGeneration.Status.READY
    else:
        status = WebUIScriptGeneration.Status.READY_WITH_WARNINGS
    terminal_code = error_code if status == WebUIScriptGeneration.Status.NEEDS_REVIEW else ''
    terminal_message = error_message if status == WebUIScriptGeneration.Status.NEEDS_REVIEW else ''
    if error_code:
        terminal_stage = generation.current_stage
        snapshot['actual_failure_stage'] = terminal_stage
    elif status == WebUIScriptGeneration.Status.NEEDS_REVIEW:
        terminal_stage = WebUIScriptGeneration.Stage.VALIDATING
    else:
        terminal_stage = WebUIScriptGeneration.Stage.COMPLETED
    try:
        completed = finalize_generation_artifact(
            generation.pk, generation_revision=generation.revision, task_id=task_id,
            target_status=status, script_draft=script_draft, snapshot=snapshot,
            quality_report=report, variables=_workspace_variables(snapshot, generation),
            warnings=list(dict.fromkeys(item for item in warnings if item)),
            error_code=terminal_code, error_message=terminal_message,
            terminal_stage=terminal_stage,
        )
    except Exception:
        logger.exception('最终产物持久化失败: generation_id=%s', generation.pk)
        return {'generation_id': str(generation.pk), 'status': 'persistence_failed', 'error_code': 'PERSISTENCE_FAILED'}
    if completed is None:
        return {'generation_id': str(generation.pk), 'status': 'stale', 'error_code': 'STALE_AGENT_RESULT'}
    publish_terminal(completed)
    return {
        'generation_id': str(completed.pk), 'status': completed.status,
        'quality_status': report.get('status'), 'completion': report['completion'],
        'error_code': completed.error_code,
    }


def _run_agent(
    generation: WebUIScriptGeneration, *, celery_task_id: str | None, brief: dict[str, Any],
    mcp_config: dict[str, Any], code_only: bool,
) -> dict[str, Any]:
    from .script_exploration_agent import ScriptExplorationAgent

    task_marker = generation.celery_task_id or celery_task_id or '<direct>'
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled', 'error_code': 'TASK_CANCELLED'}
    stage = WebUIScriptGeneration.Status.GENERATING if code_only else WebUIScriptGeneration.Status.EXPLORING
    generation = transition_generation(generation.pk, stage, progress=55 if code_only else 45)
    publish_stage_changed(generation, '整理现有草稿' if code_only else '探索页面并编写脚本')
    try:
        manager = get_llm_manager(config_id=generation.model_info['config_id'])
        agent = ScriptExplorationAgent(
            llm_model=manager.current_llm, mcp_config=mcp_config, generation_id=str(generation.pk),
            cancel_check=lambda: _terminal_cancel(str(generation.pk), celery_task_id),
            exploration_timeout_seconds=generation.exploration_timeout_seconds or exploration_total_timeout_seconds(),
            checkpoint_callback=_checkpoint_callback(generation),
        )
        result = asyncio.run(agent.generate(
            brief=brief, target_url=generation.target_url,
            saved_snapshot=generation.exploration_snapshot if code_only else None,
            script_draft=generation.script_draft if code_only else '', code_only=code_only,
        ))
    except Exception as exc:
        logger.exception(
            '脚本探索代理发生未处理异常: generation_id=%s code_only=%s',
            generation.pk, code_only,
        )
        model_error = classify_model_service_error(exc, stage='generating')
        code, message = model_error or ('INTERNAL_GENERATION_ERROR', '脚本生成服务发生内部错误，请稍后重试。')
        return _persist_agent_result(
            generation, task_id=task_marker, script_draft='', snapshot=generation.exploration_snapshot,
            completion='unknown', error_code=code, error_message=message, final_message='',
        )
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled', 'error_code': 'TASK_CANCELLED'}
    return _persist_agent_result(
        generation, task_id=task_marker, script_draft=str(getattr(result, 'script_draft', '') or ''),
        snapshot=getattr(result, 'snapshot', None) or generation.exploration_snapshot,
        completion=str(getattr(result, 'completion', 'unknown') or 'unknown'),
        error_code=str(getattr(result, 'error_code', '') or ''),
        error_message=str(getattr(result, 'error_message', '') or ''),
        final_message=str(getattr(result, 'final_message', '') or ''),
    )


def run_generation(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    """Run a fresh v5 generation without ScenarioPlan normalization or finalization."""
    try:
        generation = WebUIScriptGeneration.objects.select_related('test_case', 'user').get(pk=generation_id)
    except Exception:
        return {'generation_id': str(generation_id), 'status': 'failed', 'error_code': 'TRANSIENT_SERVICE_ERROR'}
    if _terminal_cancel(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled', 'error_code': 'TASK_CANCELLED'}
    if generation.status in PAUSED_GENERATION_STATUSES or generation.status in {
        WebUIScriptGeneration.Status.NEEDS_REVIEW, WebUIScriptGeneration.Status.READY,
        WebUIScriptGeneration.Status.READY_WITH_WARNINGS, WebUIScriptGeneration.Status.FAILED,
        WebUIScriptGeneration.Status.CANCELLED,
    }:
        return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': generation.error_code}
    generation = claim_generation_worker(generation.pk, celery_task_id)
    if generation is None:
        return {'generation_id': str(generation_id), 'status': 'skipped'}
    if generation.status == WebUIScriptGeneration.Status.CREATED:
        generation = transition_generation(generation.pk, WebUIScriptGeneration.Status.NORMALIZING, progress=10)
    if generation.status == WebUIScriptGeneration.Status.NORMALIZING:
        publish_stage_changed(generation, '整理测试目标')
        try:
            brief = _brief_for_generation(generation)
        except Exception:
            logger.exception('生成 brief 失败: generation_id=%s', generation.pk)
            return _fail(str(generation.pk), 'INPUT_INVALID', '无法整理测试目标，请检查描述后重试。')
        generation = transition_generation(
            generation.pk, WebUIScriptGeneration.Status.PREFLIGHTING, progress=25,
            updates={'scenario_spec': brief},
        )
    elif generation.status == WebUIScriptGeneration.Status.PREFLIGHTING:
        brief = generation.scenario_spec if isinstance(generation.scenario_spec, dict) else {}
        if brief.get('schema_version') != 5:
            return _fail(str(generation.pk), 'LEGACY_GENERATION_UNSUPPORTED', '旧版生成记录不能自动恢复，请手动处理已有源码。')
    else:
        return _fail(str(generation.pk), 'TRANSIENT_SERVICE_ERROR', '当前生成阶段不能继续。')
    preflight = run_safety_preflight(generation, brief)
    if preflight.outcome != 'continue':
        target = {
            'needs_confirmation': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            'failed': WebUIScriptGeneration.Status.FAILED,
        }[preflight.outcome]
        paused = transition_generation(
            generation.pk, target, progress=25, error_code=preflight.error_code,
            error_message=preflight.message, updates={'warnings': preflight.warnings},
        )
        if target == WebUIScriptGeneration.Status.FAILED:
            publish_terminal(paused)
        return {'generation_id': str(paused.pk), 'status': paused.status, 'error_code': paused.error_code}
    return _run_agent(
        generation, celery_task_id=celery_task_id, brief=brief,
        mcp_config=preflight.mcp_config or {}, code_only=False,
    )


def run_generation_from_trace(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    """Retry by code-only drafting from a v5 checkpoint; it never starts MCP."""
    generation = claim_trace_generation_retry(generation_id, celery_task_id)
    if generation is None:
        return {'generation_id': str(generation_id), 'status': 'skipped'}
    brief = generation.scenario_spec if isinstance(generation.scenario_spec, dict) else {}
    snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
    if brief.get('schema_version') != 5 or snapshot.get('schema_version') != 5:
        return _fail(str(generation.pk), 'LEGACY_GENERATION_UNSUPPORTED', '旧版记录不能自动恢复，请手动处理已有源码。')
    return _run_agent(
        generation, celery_task_id=celery_task_id, brief=brief,
        mcp_config={}, code_only=True,
    )
