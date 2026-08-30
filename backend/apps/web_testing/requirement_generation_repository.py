"""Transactional state helpers for WebUI requirement-case generations."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import WebUITestCaseGeneration


RUNNING_STATUSES = {
    WebUITestCaseGeneration.Status.CREATED,
    WebUITestCaseGeneration.Status.CONTEXT_BUILDING,
    WebUITestCaseGeneration.Status.GENERATING,
    WebUITestCaseGeneration.Status.VALIDATING,
    WebUITestCaseGeneration.Status.REPAIRING,
}
TERMINAL_STATUSES = {
    WebUITestCaseGeneration.Status.IMPORTED,
    WebUITestCaseGeneration.Status.FAILED,
    WebUITestCaseGeneration.Status.CANCELLED,
}

ALLOWED_TRANSITIONS = {
    WebUITestCaseGeneration.Status.CREATED: {
        WebUITestCaseGeneration.Status.CONTEXT_BUILDING,
        WebUITestCaseGeneration.Status.CANCELLED,
        WebUITestCaseGeneration.Status.FAILED,
    },
    WebUITestCaseGeneration.Status.CONTEXT_BUILDING: {
        WebUITestCaseGeneration.Status.GENERATING,
        WebUITestCaseGeneration.Status.CANCELLED,
        WebUITestCaseGeneration.Status.FAILED,
    },
    WebUITestCaseGeneration.Status.GENERATING: {
        WebUITestCaseGeneration.Status.VALIDATING,
        WebUITestCaseGeneration.Status.CANCELLED,
        WebUITestCaseGeneration.Status.FAILED,
    },
    WebUITestCaseGeneration.Status.VALIDATING: {
        WebUITestCaseGeneration.Status.REPAIRING,
        WebUITestCaseGeneration.Status.NEEDS_REVIEW,
        WebUITestCaseGeneration.Status.CANCELLED,
        WebUITestCaseGeneration.Status.FAILED,
    },
    WebUITestCaseGeneration.Status.REPAIRING: {
        WebUITestCaseGeneration.Status.VALIDATING,
        WebUITestCaseGeneration.Status.NEEDS_REVIEW,
        WebUITestCaseGeneration.Status.CANCELLED,
        WebUITestCaseGeneration.Status.FAILED,
    },
    WebUITestCaseGeneration.Status.NEEDS_REVIEW: {
        WebUITestCaseGeneration.Status.NEEDS_REVIEW,
        WebUITestCaseGeneration.Status.IMPORTING,
        WebUITestCaseGeneration.Status.CANCELLED,
    },
    WebUITestCaseGeneration.Status.IMPORTING: {
        WebUITestCaseGeneration.Status.IMPORTED,
        WebUITestCaseGeneration.Status.NEEDS_REVIEW,
        WebUITestCaseGeneration.Status.FAILED,
    },
}


class RequirementGenerationStateError(RuntimeError):
    """Raised for stale or invalid lifecycle transitions."""


def requirement_generation_queryset():
    return WebUITestCaseGeneration.objects.select_related(
        'project', 'user', 'module', 'model_config',
    ).prefetch_related('imported_test_cases')


def get_requirement_generation(*, generation_id, project_id, user, for_update=False):
    queryset = requirement_generation_queryset()
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.get(id=generation_id, project_id=project_id, user=user)


def create_or_reuse_requirement_generation(*, project, user, values):
    """Create a task record and reuse exact active duplicates or a repeated client request."""

    client_request_id = values.get('client_request_id')
    with transaction.atomic():
        if client_request_id:
            existing = requirement_generation_queryset().filter(
                client_request_id=client_request_id,
                project=project,
                user=user,
            ).first()
            if existing:
                return existing, True

        existing = requirement_generation_queryset().filter(
            project=project,
            user=user,
            module=values['module'],
            model_config=values['model_config'],
            request_text=values['request_text'],
            generation_scope=values['generation_scope'],
            case_categories=values['case_categories'],
            target_case_count=values['target_case_count'],
            status__in=RUNNING_STATUSES,
        ).order_by('-created_at').first()
        if existing:
            return existing, True

        try:
            generation = WebUITestCaseGeneration.objects.create(
                project=project,
                user=user,
                **values,
            )
        except IntegrityError:
            if not client_request_id:
                raise
            generation = requirement_generation_queryset().get(
                client_request_id=client_request_id,
                project=project,
                user=user,
            )
            return generation, True
        return generation, False


def transition_requirement_generation(
    generation_id,
    *,
    target_status,
    expected_statuses=None,
    updates=None,
):
    """Lock and update a generation while enforcing its lifecycle."""

    with transaction.atomic():
        generation = WebUITestCaseGeneration.objects.select_for_update().get(id=generation_id)
        if expected_statuses is not None and generation.status not in set(expected_statuses):
            raise RequirementGenerationStateError(
                f'当前状态 {generation.status} 不能进入 {target_status}'
            )
        allowed = ALLOWED_TRANSITIONS.get(generation.status, set())
        if target_status != generation.status and target_status not in allowed:
            raise RequirementGenerationStateError(
                f'当前状态 {generation.status} 不能进入 {target_status}'
            )

        generation.status = target_status
        update_fields = {'status', 'updated_at'}
        for field, value in (updates or {}).items():
            setattr(generation, field, value)
            update_fields.add(field)
        if target_status == WebUITestCaseGeneration.Status.CONTEXT_BUILDING and not generation.started_at:
            generation.started_at = timezone.now()
            update_fields.add('started_at')
        if target_status in TERMINAL_STATUSES or target_status == WebUITestCaseGeneration.Status.NEEDS_REVIEW:
            generation.completed_at = timezone.now()
            update_fields.add('completed_at')
        generation.save(update_fields=sorted(update_fields))
        return generation


def generation_is_cancelled(generation_id):
    return WebUITestCaseGeneration.objects.filter(
        id=generation_id,
        status=WebUITestCaseGeneration.Status.CANCELLED,
    ).exists()
