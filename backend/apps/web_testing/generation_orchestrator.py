"""Deterministic phase-2/3 orchestration for V2 WebUI script generation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.core.cache import cache

from ai_core.model_manager import get_llm_manager
from ai_core.webui_playwright_agent import _load_project_pom_context

from .generation_contracts import (
    GenerationContractError,
    ScenarioSpec,
    is_terminal_status,
    merge_exploration_snapshots,
    validate_snapshot_against_scenario,
)
from .generation_events import publish_stage_changed, publish_terminal
from .generation_preflight import run_safety_preflight
from .generation_repository import (
    MAX_GENERATION_RESUME_COUNT,
    PAUSED_GENERATION_STATUSES,
    attach_celery_task,
    cancel_generation,
    get_generation_temporary_credentials,
    is_cancel_requested,
    transition_generation,
)
from .mcp_page_explorer import MCPPageExplorer, MCPPageExplorerError
from .models import WebUIScriptGeneration
from .requirement_normalizer import normalize_requirement
from .script_generator import ScriptGenerator
from .script_quality import blocker_issues, evaluate_script, has_missing_evidence

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
) -> WebUIScriptGeneration:
    """Pause once, or stop an exhausted clarification loop for review."""
    if target_status in PAUSED_GENERATION_STATUSES and generation.resume_count >= MAX_GENERATION_RESUME_COUNT:
        reviewed = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.NEEDS_REVIEW,
            progress=progress,
            error_code='RESUME_LIMIT_REACHED',
            error_message='多次补充后仍无法安全确定场景，请人工检查描述后重新发起。',
            updates={'warnings': warnings or []},
        )
        publish_terminal(reviewed)
        return reviewed
    return transition_generation(
        generation.pk,
        target_status,
        progress=progress,
        error_code=error_code,
        error_message=error_message,
        updates={'warnings': warnings or []},
    )


def _is_rate_limited_error(error: BaseException) -> bool:
    error_text = str(error).lower()
    return any(marker in error_text for marker in (
        '429', 'too many requests', 'rate limit', 'rate_limited', 'upstream_rate_limited',
    ))


def _model_failure(error: BaseException) -> tuple[str, str]:
    if _is_rate_limited_error(error):
        return 'MODEL_RATE_LIMITED', '本次锁定的模型触发限流，请稍后重试或更换模型。'
    return 'MODEL_UNAVAILABLE', '本次锁定的模型暂时不可用，请检查模型配置或稍后重试。'


def _safe_fail_generation(generation_id: str, error_code: str, message: str) -> dict[str, Any]:
    """Persist a safe failure if possible; never expose a raw backend exception."""
    try:
        generation = transition_generation(
            generation_id,
            WebUIScriptGeneration.Status.FAILED,
            error_code=error_code,
            error_message=message,
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
    return {
        'title': test_case.title,
        'description': test_case.description,
        'steps': test_case.steps_list,
        'expected_result': test_case.expected_result,
    }


def _terminal_cancel_if_requested(generation_id: str, task_id: str | None):
    if _is_task_cancelled(generation_id, task_id):
        generation = cancel_generation(generation_id)
        publish_terminal(generation)
        return generation
    return None


def _map_explorer_error(error: MCPPageExplorerError) -> tuple[str, str]:
    mappings = {
        'browser': 'BROWSER_UNAVAILABLE',
        'tool_budget': 'EXPLORATION_LIMIT_REACHED',
        'repeated_interaction': 'REPEATED_INTERACTION',
        'interaction_failure': 'LOCATOR_FAILURE_LIMIT',
        'login_failed': 'LOGIN_FAILED',
        'graph_recursion': 'EXPLORATION_LIMIT_REACHED',
        'rate_limit': 'MODEL_RATE_LIMITED',
        'transient': 'TRANSIENT_SERVICE_ERROR',
        'TASK_CANCELLED': 'TASK_CANCELLED',
        'EVIDENCE_INSUFFICIENT': 'EVIDENCE_INSUFFICIENT',
        'read_only_violation': 'EXPLORATION_WRITE_BLOCKED',
    }
    return mappings.get(error.error_code, 'TRANSIENT_SERVICE_ERROR'), str(error)


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
    if celery_task_id:
        try:
            generation = attach_celery_task(generation.pk, celery_task_id)
        except Exception:
            return _safe_fail_generation(
                str(generation.pk),
                'TRANSIENT_SERVICE_ERROR',
                '生成任务状态暂时无法更新，请稍后重试。',
            )
    if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
        return {'generation_id': str(generation.pk), 'status': 'cancelled'}

    if generation.status in PAUSED_GENERATION_STATUSES or is_terminal_status(generation.status):
        return {
            'generation_id': str(generation.pk),
            'status': generation.status,
            'error_code': generation.error_code,
        }

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

        # ORM/POM/MCP configuration work stays here, before entering asyncio.
        try:
            model_manager = get_llm_manager(config_id=generation.model_info['config_id'])
        except Exception as exc:
            error_code, message = _model_failure(exc)
            return _safe_fail_generation(str(generation.pk), error_code, message)
        try:
            pom_context = _load_project_pom_context(generation.project_id)
        except Exception:
            return _safe_fail_generation(
                str(generation.pk),
                'TRANSIENT_SERVICE_ERROR',
                '页面元素信息暂时无法读取，请稍后重试。',
            )
        environment_variables = (generation.environment.config or {}).get('variables') or {}
        explorer_credentials = credentials or _environment_credentials(environment_variables)
        generation = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.EXPLORING,
            progress=40,
        )
        publish_stage_changed(generation, '只读探索页面')
        explorer = MCPPageExplorer(
            llm_model=model_manager.current_llm,
            mcp_config=preflight.mcp_config or {},
            pom_context=pom_context,
            cancel_check=lambda: bool(celery_task_id and cache.get(f'celery:cancel:{celery_task_id}')),
        )
        snapshot = asyncio.run(explorer.explore(
            scenario=scenario,
            start_path=generation.start_path,
            target_url_safe=generation.target_url_safe,
            temporary_credentials=explorer_credentials,
        ))
        try:
            validate_snapshot_against_scenario(scenario, snapshot)
        except GenerationContractError:
            return _safe_fail_generation(
                str(generation.pk),
                'EVIDENCE_INSUFFICIENT',
                '页面探索证据与测试场景不一致，请缩短范围后重试。',
            )
        if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}

        generation = transition_generation(
            generation.pk,
            WebUIScriptGeneration.Status.GENERATING,
            progress=60,
            updates={
                'exploration_snapshot': snapshot.model_dump(mode='json'),
                'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                'warnings': list(generation.warnings),
            },
        )
        publish_stage_changed(generation, '根据页面证据生成脚本')
        generator = ScriptGenerator(model_manager.current_llm)
        try:
            script = generator.generate(scenario=scenario, snapshot=snapshot, pom_context=pom_context)
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

        # A supplement is allowed only for a deterministic MISSING_EVIDENCE gate
        # result.  It is intentionally one-shot and never used to retry MCP
        # login, repeated interaction, tool-budget or browser errors.
        supplement_attempted = False
        if has_missing_evidence(report):
            supplement_attempted = True
            if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
                return {'generation_id': str(generation.pk), 'status': 'cancelled'}
            try:
                supplemental_snapshot = asyncio.run(explorer.explore_missing_evidence(
                    scenario=scenario,
                    existing_snapshot=snapshot,
                    start_path=generation.start_path,
                    target_url_safe=generation.target_url_safe,
                    temporary_credentials=explorer_credentials,
                ))
                target_ids = set(snapshot.unresolved_steps) or {
                    step.id for step in scenario.steps if step.id not in snapshot.step_evidence
                }
                snapshot = merge_exploration_snapshots(
                    snapshot,
                    supplemental_snapshot,
                    scenario=scenario,
                    target_step_ids=target_ids,
                )
            except (MCPPageExplorerError, GenerationContractError) as exc:
                if isinstance(exc, MCPPageExplorerError):
                    code, message = _map_explorer_error(exc)
                else:
                    code, message = 'EVIDENCE_INSUFFICIENT', '定向补充页面证据未成功，请人工检查后再保存。'
                if code == 'TASK_CANCELLED':
                    generation = cancel_generation(generation.pk)
                    publish_terminal(generation)
                    return {'generation_id': str(generation.pk), 'status': 'cancelled'}
                generation = transition_generation(
                    generation.pk,
                    WebUIScriptGeneration.Status.NEEDS_REVIEW,
                    progress=85,
                    error_code=code,
                    error_message=message,
                    updates={
                        'script_draft': script,
                        'quality_report': report,
                        'warnings': [*list(generation.warnings), '定向补充探索未完成，未进行重复探索。'],
                    },
                )
                publish_terminal(generation)
                return {'generation_id': str(generation.pk), 'status': generation.status, 'error_code': code}
            if _terminal_cancel_if_requested(str(generation.pk), celery_task_id):
                return {'generation_id': str(generation.pk), 'status': 'cancelled'}
            try:
                script = generator.generate(scenario=scenario, snapshot=snapshot, pom_context=pom_context)
            except Exception as exc:
                error_code, message = _model_failure(exc)
                return _safe_fail_generation(str(generation.pk), error_code, message)
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.VALIDATING,
                progress=78,
                updates={
                    'exploration_snapshot': snapshot.model_dump(mode='json'),
                    'tool_stats': snapshot.tool_stats.model_dump(mode='json'),
                    'script_draft': script,
                },
            )
            report = evaluate_script(script, scenario=scenario, snapshot=snapshot)

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
                    pom_context=pom_context,
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

        warnings = [item['message'] for item in report.get('warnings', [])]
        target_status = (
            WebUIScriptGeneration.Status.NEEDS_REVIEW
            if blocker_issues(report)
            else (WebUIScriptGeneration.Status.READY_WITH_WARNINGS if warnings else WebUIScriptGeneration.Status.READY)
        )
        generation = transition_generation(
            generation.pk,
            target_status,
            progress=100 if target_status != WebUIScriptGeneration.Status.NEEDS_REVIEW else 95,
            error_code='QUALITY_GATE_BLOCKED' if target_status == WebUIScriptGeneration.Status.NEEDS_REVIEW else '',
            error_message='脚本未通过静态质量检查，请人工修改后再保存。' if target_status == WebUIScriptGeneration.Status.NEEDS_REVIEW else '',
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
            generation = cancel_generation(generation.pk)
            publish_terminal(generation)
            return {'generation_id': str(generation.pk), 'status': 'cancelled'}
        return _safe_fail_generation(str(generation.pk), code, message)
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
