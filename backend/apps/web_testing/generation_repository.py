"""Transactional persistence helpers for WebUI script-generation records."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from .generation_contracts import (
    GenerationTransitionError,
    is_terminal_status,
    stage_for_status,
    validate_transition,
)
from .generation_security import clear_temporary_credentials, get_temporary_credentials
from .models import WebUIScriptGeneration


MAX_GENERATION_RESUME_COUNT = 3
PAUSED_GENERATION_STATUSES = frozenset({
    WebUIScriptGeneration.Status.NEEDS_INPUT,
    WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
    WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
})


class GenerationResolutionConflict(ValueError):
    """The browser submitted an obsolete or non-resumable paused state."""

    def __init__(self, message: str, generation: WebUIScriptGeneration):
        super().__init__(message)
        self.generation = generation


def create_generation(**kwargs: Any) -> WebUIScriptGeneration:
    """Create the durable record before any asynchronous work is scheduled."""
    return WebUIScriptGeneration.objects.create(**kwargs)


def get_generation_for_project(generation_id: Any, project_id: int) -> WebUIScriptGeneration:
    return WebUIScriptGeneration.objects.select_related(
        'project', 'user', 'environment', 'test_case', 'module'
    ).get(pk=generation_id, project_id=project_id)


def transition_generation(
    generation_id: Any,
    target_status: str,
    *,
    progress: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    updates: dict[str, Any] | None = None,
) -> WebUIScriptGeneration:
    """Apply an idempotent, validated status update under a row lock."""
    updates = updates or {}
    should_clear_credentials = False
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        validate_transition(generation.status, target_status)

        changed_fields: set[str] = set()
        if generation.status != target_status:
            generation.status = target_status
            changed_fields.add('status')

        next_stage = stage_for_status(target_status)
        if generation.current_stage != next_stage:
            generation.current_stage = next_stage
            changed_fields.add('current_stage')

        if progress is not None:
            normalized_progress = max(0, min(100, int(progress)))
            if generation.progress != normalized_progress:
                generation.progress = normalized_progress
                changed_fields.add('progress')

        if error_code is not None and generation.error_code != error_code:
            generation.error_code = error_code
            changed_fields.add('error_code')
        if error_message is not None and generation.error_message != error_message:
            generation.error_message = error_message
            changed_fields.add('error_message')

        for field, value in updates.items():
            if not hasattr(generation, field):
                raise ValueError(f'不支持更新生成记录字段: {field}')
            if getattr(generation, field) != value:
                setattr(generation, field, value)
                changed_fields.add(field)

        now = timezone.now()
        if target_status not in {WebUIScriptGeneration.Status.CREATED} and generation.started_at is None:
            generation.started_at = now
            changed_fields.add('started_at')
        if is_terminal_status(target_status) and generation.completed_at is None:
            generation.completed_at = now
            changed_fields.add('completed_at')

        if changed_fields:
            changed_fields.add('updated_at')
            generation.save(update_fields=sorted(changed_fields))

        should_clear_credentials = is_terminal_status(target_status)
    if should_clear_credentials:
        clear_temporary_credentials(generation.pk)
    return generation


def attach_celery_task(generation_id: Any, celery_task_id: str) -> WebUIScriptGeneration:
    """Attach the id-only task payload once; duplicate retries are harmless."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.celery_task_id and generation.celery_task_id != celery_task_id:
            raise GenerationTransitionError('生成记录已关联到另一个 Celery 任务')
        if generation.celery_task_id != celery_task_id:
            generation.celery_task_id = celery_task_id
            generation.save(update_fields=['celery_task_id', 'updated_at'])
        return generation


def claim_generation_worker(generation_id: Any, celery_task_id: str | None) -> WebUIScriptGeneration | None:
    """Claim one durable generation attempt without replaying browser writes.

    A crashed worker is deliberately not auto-restarted from the beginning.
    The user must inspect the outcome and explicitly start a new generation.
    """
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status not in {
            WebUIScriptGeneration.Status.CREATED, WebUIScriptGeneration.Status.NORMALIZING,
            WebUIScriptGeneration.Status.PREFLIGHTING,
        }:
            return None
        if celery_task_id and generation.celery_task_id and generation.celery_task_id != celery_task_id:
            return None
        workspace = dict(generation.workspace or {})
        previous = workspace.get('_generation_dispatch') or {}
        if previous.get('revision') == generation.revision:
            return None
        if celery_task_id and (
            previous.get('task_id') == celery_task_id
            or any(item.get('previous_task_id') == celery_task_id for item in generation.clarifications or [])
        ):
            return None
        workspace['_generation_dispatch'] = {
            'revision': generation.revision, 'task_id': celery_task_id or '<direct>',
            'claimed_at': timezone.now().isoformat(),
        }
        generation.workspace = workspace
        fields = ['workspace', 'updated_at']
        if celery_task_id and not generation.celery_task_id:
            generation.celery_task_id = celery_task_id
            fields.append('celery_task_id')
        generation.save(update_fields=fields)
        return generation


def prepare_generation_resolution(
    generation_id: Any,
    *,
    expected_status: str,
    expected_revision: int,
    user_id: int,
    description_safe: str | None = None,
    clarification_answers: list[dict[str, str]] | None = None,
    credentials_provided: bool = False,
) -> tuple[WebUIScriptGeneration, bool]:
    """Atomically record one user resolution and select the safe resume stage.

    The returned boolean tells the API whether a new Celery task should be
    scheduled.  A generation that already exhausted its resume budget moves to
    ``needs_review`` without another model or browser call.
    """
    clarification_answers = clarification_answers or []
    should_clear_credentials = False
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status != expected_status or generation.revision != expected_revision:
            raise GenerationResolutionConflict('生成状态已变化，请刷新后重试。', generation)
        if generation.status not in PAUSED_GENERATION_STATUSES:
            raise GenerationResolutionConflict('当前生成记录不需要人工处理。', generation)

        if generation.resume_count >= MAX_GENERATION_RESUME_COUNT:
            validate_transition(generation.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)
            generation.status = WebUIScriptGeneration.Status.NEEDS_REVIEW
            generation.current_stage = stage_for_status(generation.status)
            generation.error_code = 'RESUME_LIMIT_REACHED'
            generation.error_message = '补充处理次数已达到上限，请人工检查场景描述后重新发起。'
            generation.completed_at = generation.completed_at or timezone.now()
            generation.save(update_fields=[
                'status', 'current_stage', 'error_code', 'error_message',
                'completed_at', 'updated_at',
            ])
            should_clear_credentials = True
            should_schedule = False
        else:
            source_status = generation.status
            source_error_code = generation.error_code
            previous_task_id = generation.celery_task_id
            auto_explore_resume = (
                source_status == WebUIScriptGeneration.Status.NEEDS_CONFIRMATION
                and source_error_code == 'INPUT_AMBIGUOUS'
                and generation.current_stage == WebUIScriptGeneration.Stage.PREFLIGHTING
                and not clarification_answers
                and description_safe is None
            )
            target_status = (
                WebUIScriptGeneration.Status.PREFLIGHTING
                if source_status == WebUIScriptGeneration.Status.NEEDS_CREDENTIALS or auto_explore_resume
                else WebUIScriptGeneration.Status.NORMALIZING
            )
            validate_transition(source_status, target_status)

            history = list(generation.clarifications or [])
            history.append({
                'revision': generation.revision + 1,
                'source_status': source_status,
                'error_code': source_error_code,
                'answers': clarification_answers,
                'description_revised': description_safe is not None,
                'resolved_by': int(user_id),
                'resolved_at': timezone.now().isoformat(),
                'previous_task_id': previous_task_id or '',
            })
            generation.clarifications = history
            generation.revision += 1
            generation.resume_count += 1
            generation.status = target_status
            generation.current_stage = stage_for_status(target_status)
            generation.progress = 25 if target_status == WebUIScriptGeneration.Status.PREFLIGHTING else 10
            generation.celery_task_id = None
            generation.error_code = ''
            generation.error_message = ''
            generation.warnings = []
            generation.completed_at = None
            if description_safe is not None:
                generation.description_safe = description_safe
            if credentials_provided:
                generation.credentials_provided = True
                generation.credentials_expired = False

            reset_fields = [
                'clarifications', 'revision', 'resume_count', 'status',
                'current_stage', 'progress', 'celery_task_id', 'error_code',
                'error_message', 'warnings', 'completed_at', 'description_safe',
                'credentials_provided', 'credentials_expired',
            ]
            if target_status == WebUIScriptGeneration.Status.NORMALIZING:
                generation.scenario_spec = {}
                generation.exploration_snapshot = {}
                generation.script_draft = ''
                generation.quality_report = {}
                generation.tool_stats = {}
                generation.repair_count = 0
                generation.credentials_required = False
                reset_fields.extend([
                    'scenario_spec', 'exploration_snapshot', 'script_draft',
                    'quality_report', 'tool_stats', 'repair_count',
                    'credentials_required',
                ])
            generation.save(update_fields=sorted(set([*reset_fields, 'updated_at'])))
            should_schedule = True

    if should_clear_credentials:
        clear_temporary_credentials(generation.pk)
    return generation, should_schedule


def cancel_generation(generation_id: Any) -> WebUIScriptGeneration:
    """Mark cancellation durably before attempting to revoke a worker task."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if is_terminal_status(generation.status):
            return generation
        generation.cancel_requested_at = timezone.now()
        generation.status = WebUIScriptGeneration.Status.CANCELLED
        generation.current_stage = WebUIScriptGeneration.Stage.COMPLETED
        generation.progress = min(generation.progress, 99)
        generation.completed_at = generation.completed_at or timezone.now()
        generation.save(update_fields=[
            'cancel_requested_at', 'status', 'current_stage', 'progress',
            'completed_at', 'updated_at',
        ])
    clear_temporary_credentials(generation.pk)
    return generation


def is_cancel_requested(generation_id: Any) -> bool:
    return WebUIScriptGeneration.objects.filter(
        pk=generation_id,
        status=WebUIScriptGeneration.Status.CANCELLED,
    ).exists()


def get_generation_temporary_credentials(generation_id: Any) -> dict[str, str] | None:
    """Read short-lived credentials and durably mark their expiry when absent."""
    credentials = get_temporary_credentials(generation_id)
    if credentials is not None:
        return credentials
    WebUIScriptGeneration.objects.filter(
        pk=generation_id,
        credentials_provided=True,
        credentials_expired=False,
    ).update(credentials_expired=True)
    return None


def prepare_trace_generation_retry(generation_id: Any, *, expected_revision: int) -> WebUIScriptGeneration:
    """Retry only v3 compilation from a persisted callback ledger; never reopen MCP."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        retryable = {
            'MODEL_UNAVAILABLE', 'MODEL_RATE_LIMITED', 'MODEL_SERVICE_ERROR',
            'MODEL_GATEWAY_TIMEOUT', 'TRANSIENT_SERVICE_ERROR',
        }
        if generation.status != WebUIScriptGeneration.Status.FAILED or generation.error_code not in retryable:
            raise GenerationResolutionConflict('当前失败状态不能只重试脚本生成。', generation)
        if generation.revision != expected_revision or snapshot.get('schema_version') != 3:
            raise GenerationResolutionConflict('探索轨迹或版本已变化，无法安全仅重试脚本生成。', generation)
        generation.status = WebUIScriptGeneration.Status.GENERATING
        generation.current_stage = WebUIScriptGeneration.Stage.GENERATING
        generation.progress = 60
        generation.revision += 1
        generation.error_code = ''
        generation.error_message = ''
        generation.completed_at = None
        generation.celery_task_id = None
        generation.save(update_fields=['status', 'current_stage', 'progress', 'revision', 'error_code', 'error_message', 'completed_at', 'celery_task_id', 'updated_at'])
        return generation


def claim_trace_generation_retry(
    generation_id: Any,
    celery_task_id: str | None,
) -> WebUIScriptGeneration | None:
    """Claim one model-only retry delivery without reopening the browser."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status != WebUIScriptGeneration.Status.GENERATING:
            return None
        if celery_task_id and generation.celery_task_id and generation.celery_task_id != celery_task_id:
            return None
        workspace = dict(generation.workspace or {})
        dispatch = workspace.get('_trace_generation_dispatch') or {}
        task_marker = celery_task_id or '<direct>'
        if dispatch.get('revision') == generation.revision and dispatch.get('task_id') == task_marker:
            return None
        workspace['_trace_generation_dispatch'] = {
            'revision': generation.revision,
            'task_id': task_marker,
            'claimed_at': timezone.now().isoformat(),
        }
        generation.workspace = workspace
        generation.save(update_fields=['workspace', 'updated_at'])
        return generation
