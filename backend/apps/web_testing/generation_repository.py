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


def create_generation(**kwargs: Any) -> WebUIScriptGeneration:
    """Create the durable record before any asynchronous work is scheduled."""
    return WebUIScriptGeneration.objects.create(**kwargs)


def get_generation_for_project(generation_id: Any, project_id: int) -> WebUIScriptGeneration:
    return WebUIScriptGeneration.objects.select_related(
        'project', 'user', 'environment', 'test_case'
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
