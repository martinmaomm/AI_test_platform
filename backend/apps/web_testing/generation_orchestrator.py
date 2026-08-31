"""Deterministic phase-2/3 orchestration for V2 WebUI script generation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.core.cache import cache

from ai_core.model_manager import get_llm_manager

from .generation_contracts import (
    GenerationContractError,
    ScenarioSpec,
    is_terminal_status,
    validate_snapshot_against_scenario,
)
from .exploration_completion import assess_exploration_completion, can_request_user_decision
from .generation_events import publish_stage_changed, publish_terminal
from .generation_preflight import run_safety_preflight
from .generation_repository import (
    MAX_GENERATION_RESUME_COUNT,
    PAUSED_GENERATION_STATUSES,
    claim_generation_worker,
    cancel_generation,
    get_generation_temporary_credentials,
    is_cancel_requested,
    transition_generation,
)
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError
from .models import WebUIScriptGeneration
from .model_service_errors import classify_model_service_error
from .requirement_normalizer import normalize_requirement
from .script_generator import ScriptGenerator
from .script_quality import blocker_issues, evaluate_script

logger = logging.getLogger(__name__)


def _normalization_description(generation: WebUIScriptGeneration) -> str:
    """Build model input from the safe description and persisted clarifications."""
    sections = [generation.description_safe]
    for item in generation.clarifications or []:
        answers = item.get('answers') or []
        if not answers:
            continue
        lines = ['用户补充确认：']
        for answer in answers:
            question = str(answer.get('question') or '').strip()
            value = str(answer.get('answer') or '').strip()
            if question and value:
                lines.append(f'- {question}：{value}')
        if len(lines) > 1:
            sections.append('\n'.join(lines))
    return '\n\n'.join(section for section in sections if section)


def _pause_or_require_review(
    generation: WebUIScriptGeneration,
    target_status: str,
    *,
    progress: int,
    error_code: str,
    error_message: str,
    warnings: list[str] | None = None,
    updates: dict[str, Any] | None = None,
) -> WebUIScriptGeneration:
    """Pause once, or stop an exhausted clarification loop for review."""
    persisted_updates = {**(updates or {}), 'warnings': warnings or []}
    if target_status in PAUSED_GENERATION_STATUSES and generation.resume_count >= MAX_GENERATION_RESUME_COUNT:
        review_updates = {
            key: value for key, value in persisted_updates.items()
            if key != 'current_stage'
        }
        reviewed = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.NEEDS_REVIEW,
            progress=progress,
            error_code='RESUME_LIMIT_REACHED',
            error_message='多次补充后仍无法安全确定场景，请人工检查描述后重新发起。',
            updates=review_updates,
        )
        publish_terminal(reviewed)
        return reviewed
    return transition_generation(
        generation.pk,
        target_status,
        progress=progress,
        error_code=error_code,
        error_message=error_message,
        updates=persisted_updates,
    )


def _is_rate_limited_error(error: BaseException) -> bool:
    error_text = str(error).lower()
    return any(marker in error_text for marker in (
        '429', 'too many requests', 'rate limit', 'rate_limited', 'upstream_rate_limited',
    ))


def _model_failure(error: BaseException) -> tuple[str, str]:
    service_error = classify_model_service_error(error, stage='generation')
    if service_error:
        return service_error
    if _is_rate_limited_error(error):
        return 'MODEL_RATE_LIMITED', '本次锁定的模型触发限流，请稍后重试或更换模型。'
    return 'MODEL_UNAVAILABLE', '本次锁定的模型暂时不可用，请检查模型配置或稍后重试。'


def _cleanup_requires_attention(snapshot) -> bool:
    return snapshot.cleanup_report.status in {'residual', 'unknown', 'not_attempted'}


def _safe_fail_generation(
    generation_id: str,
    error_code: str,
    message: str,
    *,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a safe failure if possible; never expose a raw backend exception."""
    try:
        logger.warning('WebUI generation stopped: generation_id=%s error_code=%s', generation_id, error_code)
        generation = transition_generation(
            generation_id,
            WebUIScriptGeneration.Status.FAILED,
            error_code=error_code,
            error_message=message,
            updates=updates,
        )
        publish_terminal(generation)
        return {
            'generation_id': str(generation.pk),
            'status': generation.status,
            'error_code': generation.error_code,
        }
    except Exception:
        logger.error(
            'WebUI V2 generation failure could not be persisted: generation_id=%s code=%s',
            generation_id,
            error_code,
        )
        return {
            'generation_id': str(generation_id),
            'status': WebUIScriptGeneration.Status.FAILED,
            'error_code': 'TRANSIENT_SERVICE_ERROR',
        }


def _is_task_cancelled(generation_id: str, task_id: str | None) -> bool:
    return is_cancel_requested(generation_id) or bool(task_id and cache.get(f'celery:cancel:{task_id}'))


def _test_case_context(generation) -> dict[str, Any] | None:
    test_case = generation.test_case
    if test_case is None:
        return None
    # WebUITestCase is now an independent-script model. Do not read the
    # removed structured-step fields when regenerating from an existing case.
    return {
        'title': test_case.title,
        'description': test_case.description,
        'script_version': int(getattr(test_case, 'script_version', 0) or 0),
        'has_script': bool(getattr(test_case, 'test_script_content', '') or ''),
    }


def _terminal_cancel_if_requested(generation_id: str, task_id: str | None):
    if _is_task_cancelled(generation_id, task_id):
        generation = cancel_generation(generation_id)
        publish_terminal(generation)
        return generation
    return None


def _map_explorer_error(error: MCPPageExplorerError) -> tuple[str, str]:
    if error.error_code in {'other', 'transient', 'rate_limit'}:
        service_error = classify_model_service_error(error)
        if service_error:
            return service_error
    mappings = {
        'browser': 'BROWSER_UNAVAILABLE',
        'tool_parameter': 'MCP_CONFIGURATION_INVALID',
        'tool_budget': 'EXPLORATION_LIMIT_REACHED',
        'repeated_interaction': 'REPEATED_INTERACTION',
        'interaction_failure': 'LOCATOR_FAILURE_LIMIT',
        'login_failed': 'LOGIN_FAILED',
        'graph_recursion': 'EXPLORATION_LIMIT_REACHED',
        'rate_limit': 'MODEL_RATE_LIMITED',
        'transient': 'TRANSIENT_SERVICE_ERROR',
        'TASK_CANCELLED': 'TASK_CANCELLED',
        'EVIDENCE_INSUFFICIENT': 'EVIDENCE_INSUFFICIENT',
        'SCRIPT_FORMAT_INVALID': 'EVIDENCE_INSUFFICIENT',
        'read_only_violation': 'EXPLORATION_WRITE_BLOCKED',
        'write_scope_violation': 'EXPLORATION_SCOPE_BLOCKED',
        'extra_risk_action': 'EXPLORATION_EXTRA_RISK_BLOCKED',
        'write_result_unknown': 'EXPLORATION_WRITE_RESULT_UNKNOWN',
        'exploration_timeout': 'EXPLORATION_TIMEOUT',
        'permission': 'EXPLORATION_PERMISSION_DENIED',
    }
    return mappings.get(error.error_code, 'TRANSIENT_SERVICE_ERROR'), str(error)


def _merge_explorer_failure_stats(
    previous: dict[str, Any] | None,
    failure: MCPPageExplorerError,
) -> dict[str, Any]:
    """Merge only the allowlisted, already-sanitized Explorer counters."""
    extra = failure.tool_stats or {}
    if not extra:
        return dict(previous or {})
    previous = previous or {}
    counts = dict(previous.get('tool_counts') or {})
    for name, count in (extra.get('tool_counts') or {}).items():
        counts[str(name)] = counts.get(str(name), 0) + int(count)
    merged = {
        'total_tool_calls': int(previous.get('total_tool_calls', 0)) + int(extra.get('total_tool_calls', 0)),
        'tool_counts': counts,
        'failed_tool_calls': int(previous.get('failed_tool_calls', 0)) + int(extra.get('failed_tool_calls', 0)),
        'termination_reason': extra.get('termination_reason') or previous.get('termination_reason'),
        'duration_seconds': round(
            float(previous.get('duration_seconds', 0)) + float(extra.get('duration_seconds', 0)), 3,
        ),
        'model_calls': int(previous.get('model_calls', 0)) + int(extra.get('model_calls', 0)),
    }
    for field in ('potential_write_tool_calls', 'blocked_write_tool_calls'):
        if field in previous or field in extra:
            merged[field] = int(previous.get(field, 0)) + int(extra.get(field, 0))
    # These are failure-only summaries from MCPPageExplorerError; never copy
    # tool input, selector, output text, URL, or credential-bearing metadata.
    if 'blocked_tool_calls' in extra:
        merged['blocked_tool_calls'] = max(0, int(extra['blocked_tool_calls']))
    for name in ('last_operation', 'last_blocked_operation'):
        operation = extra.get(name)
        if not isinstance(operation, dict):
            continue
        tool_name = str(operation.get('tool_name') or 'browser_tool')
        if not (tool_name.startswith('playwright_') or tool_name == 'browser_console_logs'):
            tool_name = 'browser_tool'
        try:
            call_index = max(0, int(operation.get('call_index', 0)))
        except (TypeError, ValueError):
            call_index = 0
        status = str(operation.get('status') or 'unknown')
        if status not in {'started', 'succeeded', 'failed', 'blocked'}:
            status = 'unknown'
        merged[name] = {
            'tool_name': tool_name,
            'call_index': call_index,
            'status': status,
        }
    return merged


def _cancel_with_explorer_stats(
    generation_id: str,
    failure: MCPPageExplorerError,
    *,
    previous_stats: dict[str, Any] | None = None,
) -> WebUIScriptGeneration:
    generation = cancel_generation(generation_id)
    stats = _merge_explorer_failure_stats(previous_stats or generation.tool_stats, failure)
    updates = []
    if stats and generation.tool_stats != stats:
        generation.tool_stats = stats
        updates.append('tool_stats')
    if failure.snapshot is not None:
        generation.exploration_snapshot = failure.snapshot.model_dump(mode='json')
        generation.warnings = list(dict.fromkeys([*generation.warnings, *failure.snapshot.warnings]))
        updates.extend(['exploration_snapshot', 'warnings'])
    if updates:
        generation.save(update_fields=[*updates, 'updated_at'])
    return generation


def _cancel_with_snapshot_stats(
    generation_id: str,
    tool_stats: dict[str, Any],
    snapshot=None,
) -> WebUIScriptGeneration:
    """Keep an already completed exploration's counters on a late cancellation."""
    generation = cancel_generation(generation_id)
    generation.tool_stats = tool_stats
    fields = ['tool_stats', 'updated_at']
    if snapshot is not None:
        generation.exploration_snapshot = snapshot.model_dump(mode='json')
        generation.warnings = list(dict.fromkeys([*generation.warnings, *snapshot.warnings]))
        fields.extend(['exploration_snapshot', 'warnings'])
    generation.save(update_fields=fields)
    return generation


def run_v2_generation(generation_id: str, *, celery_task_id: str | None = None) -> dict[str, Any]:
    """Run the V2 pipeline with bounded repair and at most one evidence supplement."""
    try:
        generation = WebUIScriptGeneration.objects.select_related(
            'environment', 'test_case', 'user'
        ).get(pk=generation_id)
    except Exception:
        logger.error('WebUI V2 generation record cannot be loaded: generation_id=%s', generation_id)
        return {
            'generation_id': str(generation_id),
            'status': WebUIScriptGeneration.Status.FAILED,
            'error_code': 'TRANSIENT_SERVICE_ERROR',
        }
    if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled'}

    if generation.status in PAUSED_GENERATION_STATUSES or is_terminal_status(generation.status):
        return {
            'generation_id': str(generation.pk),
            'status': generation.status,
            'error_code': generation.error_code,
        }

    try:
        claimed = claim_generation_worker(generation.pk, celery_task_id)
    except Exception:
        return _safe_fail_generation(str(generation.pk), 'TRANSIENT_SERVICE_ERROR', '无法确认任务执行权，尚未启动页面探索。')
    if claimed is None:
        generation.refresh_from_db()
        return {
            'generation_id': str(generation.pk), 'status': generation.status,
            'skipped': True, 'reason': '生成任务重复或已过期，未重新执行页面操作。',
        }
    generation = claimed

    try:
        if generation.status in {
            WebUIScriptGeneration.Status.CREATED,
            WebUIScriptGeneration.Status.NORMALIZING,
        }:
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.NORMALIZING,
                progress=10,
            )
            publish_stage_changed(generation, '理解测试场景')
            try:
                scenario = normalize_requirement(
                    _normalization_description(generation),
                    generation.model_info['config_id'],
                    _test_case_context(generation),
                )
            except GenerationContractError:
                generation = _pause_or_require_review(
                    generation,
                    WebUIScriptGeneration.Status.NEEDS_INPUT,
                    progress=10,
                    error_code='SCENARIO_CONTRACT_INVALID',
                    error_message='场景描述缺少可验证的步骤、断言或清理信息，请补充后继续。',
                )
                return {
                    'generation_id': str(generation.pk),
                    'status': generation.status,
                    'error_code': generation.error_code,
                }
            except Exception as exc:
                error_code, message = _model_failure(exc)
                return _safe_fail_generation(str(generation.pk), error_code, message)
            if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
                return {'generation_id': str(generation.pk), 'status': 'cancelled'}

            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.PREFLIGHTING,
                progress=25,
                updates={
                    'scenario_spec': scenario.model_dump(mode='json'),
                    'credentials_required': scenario.credentials_required,
                },
            )
        elif generation.status == WebUIScriptGeneration.Status.PREFLIGHTING:
            try:
                scenario = ScenarioSpec.model_validate(generation.scenario_spec or {})
            except Exception:
                return _safe_fail_generation(
                    str(generation.pk),
                    'SCENARIO_CONTRACT_INVALID',
                    '已保存的场景信息无法继续，请重新发起生成。',
                )
        else:
            return _safe_fail_generation(
                str(generation.pk),
                'TRANSIENT_SERVICE_ERROR',
                '当前生成阶段不能从暂停处理继续，请刷新后重试。',
            )

        publish_stage_changed(generation, '检查风险与登录条件')
        credentials = get_generation_temporary_credentials(generation.pk)
        preflight = run_safety_preflight(
            generation,
            scenario,
            credentials_available=credentials is not None,
        )
        if preflight.outcome != 'continue':
            target_status = {
                'needs_confirmation': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
                'needs_credentials': WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
                'failed': WebUIScriptGeneration.Status.FAILED,
            }[preflight.outcome]
            generation = _pause_or_require_review(
                generation,
                target_status,
                progress=25,
                error_code=preflight.error_code,
                error_message=preflight.message,
                warnings=preflight.warnings,
            )
            if generation.status == WebUIScriptGeneration.Status.FAILED:
                publish_terminal(generation)
            return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': generation.error_code}

        if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}

        # Resolve the locked model before entering the browser exploration loop.
        try:
            model_manager = get_llm_manager(config_id=generation.model_info['config_id'])
        except Exception as exc:
            error_code, message = _model_failure(exc)
            return _safe_fail_generation(str(generation.pk), error_code, message)
        environment_variables = (generation.environment.config or {}).get('variables') or {}
        explorer_credentials = credentials or _environment_credentials(environment_variables)
        generation = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.EXPLORING,
            progress=40,
            updates={'warnings': list(preflight.warnings)},
        )
        publish_stage_changed(generation, '探索并验证测试流程')
        explorer = MCPPageExplorer(
            llm_model=model_manager.current_llm,
            mcp_config=preflight.mcp_config or {},
            cancel_check=lambda: bool(celery_task_id and cache.get(f'celery:cancel:{celery_task_id}')),
            generation_id=str(generation.pk),
            user_constraints=_normalization_description(generation),
        )
        explore_until_complete = getattr(explorer, 'explore_until_complete', None)
        if explore_until_complete is None:
            # Keep the historical explorer interface compatible with callers
            # that have not yet implemented the same-session method.
            explore_until_complete = explorer.explore
        snapshot = asyncio.run(explore_until_complete(
            scenario=scenario,
            start_path=generation.start_path,
            target_url_safe=generation.target_url_safe,
            temporary_credentials=explorer_credentials,
        ))
        snapshot = assess_exploration_completion(scenario, snapshot)
        try:
            validate_snapshot_against_scenario(scenario, snapshot)
        except GenerationContractError:
            return _safe_fail_generation(
                str(generation.pk),
                'EVIDENCE_INSUFFICIENT',
                '页面探索证据与测试场景不一致，请缩短范围后重试。',
                updates={
                    'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                    'exploration_snapshot': snapshot.model_dump(mode='json'),
                    'warnings': list(snapshot.warnings),
                },
            )
        if _is_task_cancelled(str(generation.pk), celery_task_id):
            generation = _cancel_with_snapshot_stats(
                generation.pk, snapshot.tool_stats.model_dump(mode='json'), snapshot=snapshot,
            )
            publish_terminal(generation)
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}

        if can_request_user_decision(snapshot):
            unresolved_questions = snapshot.completion.user_questions
            scenario = scenario.model_copy(update={'ambiguities': unresolved_questions})
            generation = _pause_or_require_review(
                generation,
                WebUIScriptGeneration.Status.NEEDS_REVIEW if _cleanup_requires_attention(snapshot) else WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
                progress=50,
                error_code='EXPLORATION_CLEANUP_UNCONFIRMED' if _cleanup_requires_attention(snapshot) else 'INPUT_AMBIGUOUS',
                error_message='本次探索存在未确认的清理结果，请先检查残留数据；不会自动重新执行页面操作。' if _cleanup_requires_attention(snapshot) else '页面已完成目标范围内的探索，但仍有业务信息需要确认。',
                warnings=list(dict.fromkeys([*unresolved_questions, *snapshot.warnings])),
                updates={
                    'current_stage': WebUIScriptGeneration.Stage.EXPLORING,
                    'scenario_spec': scenario.model_dump(mode='json'),
                    'exploration_snapshot': snapshot.model_dump(mode='json'),
                    'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                },
            )
            if generation.status == WebUIScriptGeneration.Status.NEEDS_REVIEW:
                publish_terminal(generation)
            return {
                'generation_id': str(generation.pk),
                'status': generation.status,
                'error_code': generation.error_code,
            }

        if snapshot.completion.status != 'complete':
            error_code = (
                'EXPLORATION_LIMIT_REACHED'
                if snapshot.completion.budget_exhausted
                else 'EVIDENCE_INSUFFICIENT'
            )
            return _safe_fail_generation(
                str(generation.pk),
                error_code,
                '页面可观察目标未完成，未生成脚本；请缩短范围或检查页面访问权限。',
                updates={
                    'exploration_snapshot': snapshot.model_dump(mode='json'),
                    'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                    'warnings': list(dict.fromkeys([
                        *snapshot.warnings,
                        *[item.target for item in snapshot.completion.missing_targets if item.kind == 'observable'],
                    ])),
                },
            )

        # Exploration has resolved every pre-exploration question.  Keep the
        # discovery targets as evidence context, but do not leak stale prompts
        # into script generation or the user-action panel.
        scenario = scenario.model_copy(update={'ambiguities': []})

        generation = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.GENERATING,
            progress=60,
            updates={
                'scenario_spec': scenario.model_dump(mode='json'),
                'exploration_snapshot': snapshot.model_dump(mode='json'),
                'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                'warnings': list(dict.fromkeys([*generation.warnings, *snapshot.warnings])),
            },
        )
        publish_stage_changed(generation, '根据页面证据生成脚本')
        generator = ScriptGenerator(model_manager.current_llm)
        try:
            script = generator.generate(scenario=scenario, snapshot=snapshot)
        except Exception as exc:
            error_code, message = _model_failure(exc)
            return _safe_fail_generation(str(generation.pk), error_code, message)
        if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}

        generation = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.VALIDATING,
            progress=75,
            updates={'script_draft': script},
        )
        publish_stage_changed(generation, '检查脚本质量')
        report = evaluate_script(script, scenario=scenario, snapshot=snapshot)

        # Exploration completion is a pre-generation gate. Quality validation
        # must never reopen a browser session or compensate for missing evidence.
        supplement_attempted = snapshot.completion.targeted_rounds > 0

        repair_count = 0
        while blocker_issues(report) and repair_count < 2:
            if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
                return {'generation_id': str(generation.pk), 'status': 'cancelled'}
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.REPAIRING,
                progress=82 + repair_count * 4,
                updates={'quality_report': report, 'repair_count': repair_count},
            )
            publish_stage_changed(generation, '按质量检查结果修复脚本')
            try:
                script = generator.repair(
                    script=script,
                    issues=blocker_issues(report),
                    scenario=scenario,
                    snapshot=snapshot,
                )
            except Exception as exc:
                error_code, message = _model_failure(exc)
                return _safe_fail_generation(str(generation.pk), error_code, message)
            repair_count += 1
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.VALIDATING,
                progress=88 + repair_count * 2,
                updates={'script_draft': script, 'repair_count': repair_count},
            )
            report = evaluate_script(script, scenario=scenario, snapshot=snapshot)

        warnings = list(dict.fromkeys([*snapshot.warnings, *[item['message'] for item in report.get('warnings', [])]]))
        cleanup_attention = _cleanup_requires_attention(snapshot)
        target_status = (
            WebUIScriptGeneration.Status.NEEDS_REVIEW
            if blocker_issues(report) or cleanup_attention
            else (WebUIScriptGeneration.Status.READY_WITH_WARNINGS if warnings else WebUIScriptGeneration.Status.READY)
        )
        generation = transition_generation(
            generation.pk,
            target_status,
            progress=100 if target_status != WebUIScriptGeneration.Status.NEEDS_REVIEW else 95,
            error_code='EXPLORATION_CLEANUP_UNCONFIRMED' if cleanup_attention else ('QUALITY_GATE_BLOCKED' if blocker_issues(report) else ''),
            error_message='脚本草稿已保留，但探索数据清理未确认，请先检查残留数据再调试。' if cleanup_attention else ('脚本未通过静态质量检查，请人工修改后再保存。' if blocker_issues(report) else ''),
            updates={
                'exploration_snapshot': snapshot.model_dump(mode='json'),
                'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                'script_draft': script,
                'quality_report': report,
                'warnings': warnings,
                'repair_count': repair_count,
            },
        )
        publish_terminal(generation)
        return {
            'generation_id': str(generation.pk),
            'status': generation.status,
            'progress': generation.progress,
            'supplement_attempted': supplement_attempted,
        }
    except MCPPageExplorerError as exc:
        code, message = _map_explorer_error(exc)
        if code == 'TASK_CANCELLED':
            generation = _cancel_with_explorer_stats(generation.pk, exc)
            publish_terminal(generation)
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}
        updates = {'tool_stats': _merge_explorer_failure_stats(generation.tool_stats, exc)}
        if exc.snapshot is not None:
            updates['exploration_snapshot'] = exc.snapshot.model_dump(mode='json')
            updates['warnings'] = list(dict.fromkeys([*generation.warnings, *exc.snapshot.warnings]))
        return _safe_fail_generation(str(generation.pk), code, message, updates=updates)
    except Exception:
        logger.error(
            'WebUI V2 orchestration failed without exposing raw details: generation_id=%s',
            generation.pk,
        )
        return _safe_fail_generation(
            str(generation.pk),
            'TRANSIENT_SERVICE_ERROR',
            '生成流程状态暂时无法继续，请稍后重试。',
        )


def _environment_credentials(variables: dict[str, Any]) -> dict[str, str] | None:
    username = variables.get('UI_TEST_USERNAME') or variables.get('ui_test_username')
    password = variables.get('UI_TEST_PASSWORD') or variables.get('ui_test_password')
    if username and password:
        return {'username': str(username), 'password': str(password)}
    return None
