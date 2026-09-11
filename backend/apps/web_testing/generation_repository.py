"""Transactional persistence helpers for WebUI script-generation records."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from .generation_contracts import (
    GenerationTransitionError,
    is_terminal_status,
    stage_for_status,
    validate_transition,
)
from .generation_lifecycle import (
    GENERATION_ACTIVE_STATUSES,
    checkpoint_lifecycle_metadata,
    claim_lifecycle_metadata,
    run_matches,
)
from .models import WebUIScriptGeneration
from .generation_workspace import (
    BUSY_REPAIR_STATUSES,
    BUSY_VERIFICATION_STATUSES,
    REPAIR_CANDIDATE_STATUSES,
    workspace_for_generation,
)

logger = logging.getLogger(__name__)

MAX_GENERATION_RESUME_COUNT = 3
MAX_ARTIFACT_HISTORY = 8
PAUSED_GENERATION_STATUSES = frozenset({
    WebUIScriptGeneration.Status.NEEDS_INPUT,
    WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
})


def _invalidate_previous_run(workspace: dict[str, Any], previous_task_id: str | None) -> dict[str, Any]:
    result = dict(workspace)
    obsolete = result.get('_obsolete_generation_task_ids')
    obsolete = list(obsolete) if isinstance(obsolete, list) else []
    if previous_task_id:
        obsolete.append(str(previous_task_id))
    # Code-only retries have no product-level attempt cap.  Dropping an older
    # delivery id would eventually let that obsolete Celery message claim a
    # later revision before its new task id is attached.
    result['_obsolete_generation_task_ids'] = list(dict.fromkeys(obsolete))
    result.pop('_agent_run', None)
    result.pop('_generation_dispatch', None)
    result.pop('_trace_generation_dispatch', None)
    return result


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
        'project', 'user', 'test_case', 'module'
    ).get(pk=generation_id, project_id=project_id)


def transition_generation(
    generation_id: Any,
    target_status: str,
    *,
    progress: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    updates: dict[str, Any] | None = None,
    generation_revision: int | None = None,
    task_id: str | None = None,
) -> WebUIScriptGeneration | None:
    """Apply an idempotent, validated status update under a row lock."""
    updates = updates or {}
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation_revision is not None or task_id is not None:
            if generation_revision is None or task_id is None:
                raise ValueError('generation_revision 和 task_id 必须同时提供')
            if not run_matches(
                generation,
                generation_revision=generation_revision,
                task_id=task_id,
            ):
                return None
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

    return generation


def attach_celery_task(
    generation_id: Any,
    celery_task_id: str,
    *,
    expected_revision: int | None = None,
) -> WebUIScriptGeneration:
    """Attach the id-only task payload once; duplicate retries are harmless."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if expected_revision is not None and generation.revision != expected_revision:
            raise GenerationTransitionError('生成记录版本已变化，不能关联旧任务')
        if generation.celery_task_id and generation.celery_task_id != celery_task_id:
            raise GenerationTransitionError('生成记录已关联到另一个 Celery 任务')
        if generation.celery_task_id != celery_task_id:
            generation.celery_task_id = celery_task_id
            generation.save(update_fields=['celery_task_id', 'updated_at'])
        return generation


def claim_generation_worker(generation_id: Any, celery_task_id: str | None) -> WebUIScriptGeneration | None:
    """Claim one durable generation attempt without replaying browser writes.

    A crashed worker is deliberately not auto-restarted from the beginning.
    The user must inspect the preserved outcome and explicitly resume it.
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
        obsolete_task_ids = workspace.get('_obsolete_generation_task_ids')
        obsolete_task_ids = obsolete_task_ids if isinstance(obsolete_task_ids, list) else []
        previous = workspace.get('_generation_dispatch') or {}
        if previous.get('revision') == generation.revision:
            return None
        if celery_task_id and (
            previous.get('task_id') == celery_task_id
            or celery_task_id in obsolete_task_ids
            or any(item.get('previous_task_id') == celery_task_id for item in generation.clarifications or [])
        ):
            return None
        workspace['_generation_dispatch'] = {
            'revision': generation.revision, 'task_id': celery_task_id or '<direct>',
            'claimed_at': timezone.now().isoformat(),
        }
        workspace = claim_lifecycle_metadata(
            workspace,
            generation_revision=generation.revision,
            task_id=celery_task_id,
        )
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
    target_url: str | None = None,
    clarification_answers: list[dict[str, str]] | None = None,
) -> tuple[WebUIScriptGeneration, bool]:
    """Atomically record one user resolution and select the safe resume stage.

    The returned boolean tells the API whether a new Celery task should be
    scheduled.  A generation that already exhausted its resume budget moves to
    ``needs_review`` without another model or browser call.
    """
    clarification_answers = clarification_answers or []
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
                if auto_explore_resume else WebUIScriptGeneration.Status.NORMALIZING
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
            })
            generation.workspace = _invalidate_previous_run(
                dict(generation.workspace or {}), previous_task_id,
            )
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
            if target_url is not None:
                generation.target_url = target_url
            reset_fields = [
                'clarifications', 'revision', 'resume_count', 'status',
                'current_stage', 'progress', 'celery_task_id', 'error_code',
                'error_message', 'warnings', 'completed_at', 'description_safe', 'target_url',
                'workspace',
            ]
            if target_status == WebUIScriptGeneration.Status.NORMALIZING:
                generation.scenario_spec = {}
                generation.exploration_snapshot = {}
                generation.script_draft = ''
                generation.quality_report = {}
                generation.tool_stats = {}
                generation.repair_count = 0
                reset_fields.extend([
                    'scenario_spec', 'exploration_snapshot', 'script_draft',
                    'quality_report', 'tool_stats', 'repair_count',
                ])
            generation.save(update_fields=sorted(set([*reset_fields, 'updated_at'])))
            should_schedule = True

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
    return generation


def is_cancel_requested(generation_id: Any) -> bool:
    return WebUIScriptGeneration.objects.filter(
        pk=generation_id,
        status=WebUIScriptGeneration.Status.CANCELLED,
    ).exists()


def prepare_trace_generation_retry(generation_id: Any, *, expected_revision: int) -> WebUIScriptGeneration:
    """Retry code-only drafting from a v5 artifact; never reopen the browser."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        if workspace['verification'].get('status') in BUSY_VERIFICATION_STATUSES:
            raise GenerationResolutionConflict('草稿调试正在运行，请等待完成后再整理代码。', generation)
        if workspace['repair'].get('status') in BUSY_REPAIR_STATUSES:
            raise GenerationResolutionConflict('草稿修复正在运行，请等待完成后再整理代码。', generation)
        if workspace['repair'].get('status') in REPAIR_CANDIDATE_STATUSES:
            raise GenerationResolutionConflict('请先采用或放弃当前候选。', generation)
        snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        retryable_statuses = {
            WebUIScriptGeneration.Status.FAILED,
            WebUIScriptGeneration.Status.NEEDS_REVIEW,
            WebUIScriptGeneration.Status.CANCELLED,
        }
        has_trace_or_draft = bool(snapshot.get('events') or generation.script_draft.strip())
        if generation.status not in retryable_statuses or not has_trace_or_draft:
            raise GenerationResolutionConflict('当前记录没有可用于代码整理的真实轨迹或草稿，不能重试。', generation)
        if generation.revision != expected_revision or snapshot.get('schema_version') != 5:
            raise GenerationResolutionConflict('仅支持当前 v5 产物的代码整理；旧版记录请手动处理源码。', generation)
        previous_task_id = generation.celery_task_id
        next_revision = generation.revision + 1
        raw_workspace = _invalidate_previous_run(
            dict(generation.workspace or {}), previous_task_id,
        )
        previous_lifecycle = (
            generation.workspace.get('_generation_lifecycle')
            if isinstance(generation.workspace, dict) else {}
        )
        previous_lifecycle = previous_lifecycle if isinstance(previous_lifecycle, dict) else {}
        raw_workspace['_generation_lifecycle'] = {
            'revision': next_revision,
            'task_id': '',
            'claimed_at': None,
            'heartbeat_at': None,
            'last_checkpoint_at': previous_lifecycle.get('last_checkpoint_at'),
            'interrupted_at': None,
        }
        generation.workspace = raw_workspace
        generation.status = WebUIScriptGeneration.Status.GENERATING
        generation.current_stage = WebUIScriptGeneration.Stage.GENERATING
        generation.progress = 60
        generation.revision = next_revision
        generation.error_code = ''
        generation.error_message = ''
        generation.completed_at = None
        generation.celery_task_id = None
        generation.save(update_fields=['workspace', 'status', 'current_stage', 'progress', 'revision', 'error_code', 'error_message', 'completed_at', 'celery_task_id', 'updated_at'])
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
        obsolete_task_ids = workspace.get('_obsolete_generation_task_ids')
        obsolete_task_ids = obsolete_task_ids if isinstance(obsolete_task_ids, list) else []
        dispatch = workspace.get('_trace_generation_dispatch') or {}
        task_marker = celery_task_id or '<direct>'
        if task_marker in obsolete_task_ids:
            return None
        if dispatch.get('revision') == generation.revision and dispatch.get('task_id') == task_marker:
            return None
        workspace['_trace_generation_dispatch'] = {
            'revision': generation.revision,
            'task_id': task_marker,
            'claimed_at': timezone.now().isoformat(),
        }
        workspace = claim_lifecycle_metadata(
            workspace,
            generation_revision=generation.revision,
            task_id=task_marker,
        )
        workspace['_agent_run']['code_only'] = True
        generation.workspace = workspace
        fields = ['workspace', 'updated_at']
        # The worker can start before the HTTP dispatcher attaches its id.
        # Persist ownership here just as a fresh generation claim does.
        if celery_task_id and not generation.celery_task_id:
            generation.celery_task_id = celery_task_id
            fields.append('celery_task_id')
        generation.save(update_fields=fields)
        return generation


def persist_generation_checkpoint(
    generation_id: Any,
    *,
    generation_revision: int,
    task_id: str,
    script_draft: str,
    snapshot: dict[str, Any],
) -> bool:
    """Durably persist one incremental agent artifact under the current run lock.

    A late callback is intentionally a no-op after cancellation, retry, or any
    user-visible terminal transition.  Database failures are returned as false
    so callers never claim a checkpoint was saved merely because the agent
    emitted one.
    """
    if not isinstance(snapshot, dict):
        return False
    try:
        with transaction.atomic():
            generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
            workspace = dict(generation.workspace or {})
            run = workspace.get('_agent_run') if isinstance(workspace.get('_agent_run'), dict) else {}
            if (
                generation.revision != generation_revision
                or generation.status not in {
                    WebUIScriptGeneration.Status.EXPLORING,
                    WebUIScriptGeneration.Status.GENERATING,
                    WebUIScriptGeneration.Status.VALIDATING,
                }
                or int(run.get('generation_revision', -1)) != generation_revision
                or str(run.get('task_id') or '') != task_id
                or (task_id != '<direct>' and generation.celery_task_id != task_id)
            ):
                return False
            artifact = snapshot.get('artifact') if isinstance(snapshot.get('artifact'), dict) else {}
            history = workspace.get('artifact_history') if isinstance(workspace.get('artifact_history'), list) else []
            effective_script = script_draft if script_draft.strip() else generation.script_draft
            normalized_variables = None
            if isinstance(artifact.get('variables'), list):
                try:
                    from .execution_variables import normalize_variable_definitions
                    from .generation_workspace import _without_persisted_secret_values
                    normalized_variables = _without_persisted_secret_values(
                        normalize_variable_definitions(artifact['variables']),
                    )
                except ValueError:
                    # Invalid agent metadata remains in the artifact for
                    # review, but must not replace the editable variable set.
                    normalized_variables = None
            previous_snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
            previous_artifact = previous_snapshot.get('artifact') if isinstance(previous_snapshot.get('artifact'), dict) else {}
            changed = (
                effective_script != generation.script_draft
                or (
                    normalized_variables is not None
                    and normalized_variables != workspace.get('variables', [])
                )
            )
            if artifact:
                revision = artifact.get('revision')
                entry = {'revision': revision, 'artifact': artifact, 'script_draft': effective_script}
                if not history or history[-1].get('revision') != revision:
                    history.append(entry)
                else:
                    history[-1] = entry
            workspace['artifact_history'] = history[-MAX_ARTIFACT_HISTORY:]
            workspace = checkpoint_lifecycle_metadata(workspace)
            if normalized_variables is not None:
                workspace['variables'] = normalized_variables
            if changed:
                try:
                    workspace['revision'] = max(0, int(workspace.get('revision') or 0)) + 1
                except (TypeError, ValueError):
                    workspace['revision'] = 1
                # A checkpoint changes the draft identity.  No prior runtime
                # verification may represent this version.
                from .generation_workspace import _verification
                workspace['verification'] = _verification(script=effective_script)
            snapshot = dict(snapshot)
            snapshot['artifact_history'] = workspace['artifact_history']
            generation.workspace = workspace
            generation.exploration_snapshot = snapshot
            generation.tool_stats = snapshot.get('tool_stats') if isinstance(snapshot.get('tool_stats'), dict) else {}
            fields = ['workspace', 'exploration_snapshot', 'tool_stats', 'updated_at']
            if effective_script != generation.script_draft:
                generation.script_draft = effective_script
                fields.append('script_draft')
            generation.save(update_fields=fields)
            return True
    except Exception:
        # Do not re-raise from the callback: the agent can still return its
        # final artifact, and final persistence will separately fail closed.
        logger.exception(
            '增量草稿 checkpoint 持久化失败: generation_id=%s task_id=%s generation_revision=%s',
            generation_id, task_id, generation_revision,
        )
        return False


def finalize_generation_artifact(
    generation_id: Any,
    *,
    generation_revision: int,
    task_id: str,
    target_status: str,
    script_draft: str,
    snapshot: dict[str, Any],
    quality_report: dict[str, Any],
    variables: list[dict[str, Any]] | None,
    warnings: list[str],
    error_code: str,
    error_message: str,
    terminal_stage: str | None = None,
) -> WebUIScriptGeneration | None:
    """Atomically complete exactly the active agent attempt, or do nothing.

    The callback and final result use the same generation revision/task marker.
    This prevents a stale worker from restoring an old draft after cancellation,
    retry, or a user edit made after the prior run finished.
    """
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = dict(generation.workspace or {})
        run = workspace.get('_agent_run') if isinstance(workspace.get('_agent_run'), dict) else {}
        if (
            generation.revision != generation_revision
            or generation.status not in {
                WebUIScriptGeneration.Status.EXPLORING,
                WebUIScriptGeneration.Status.GENERATING,
                WebUIScriptGeneration.Status.VALIDATING,
            }
            or int(run.get('generation_revision', -1)) != generation_revision
            or str(run.get('task_id') or '') != task_id
            or (task_id != '<direct>' and generation.celery_task_id != task_id)
        ):
            return None
        target_stage = terminal_stage or stage_for_status(target_status)
        if target_stage not in set(WebUIScriptGeneration.Stage.values):
            raise GenerationTransitionError('未知生成阶段')
        validate_transition(generation.status, target_status)
        current_snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        current_artifact = current_snapshot.get('artifact') if isinstance(current_snapshot.get('artifact'), dict) else {}
        incoming_artifact = snapshot.get('artifact') if isinstance(snapshot.get('artifact'), dict) else {}
        try:
            current_artifact_revision = int(current_artifact.get('revision') or 0)
            incoming_artifact_revision = int(incoming_artifact.get('revision') or 0)
        except (TypeError, ValueError):
            current_artifact_revision = incoming_artifact_revision = 0
        # An exception path often has only the original snapshot.  Keep the
        # latest checkpoint rather than downgrading observable evidence.
        if current_artifact_revision > incoming_artifact_revision:
            snapshot = dict(current_snapshot)
            script_draft = generation.script_draft
        artifact = snapshot.get('artifact') if isinstance(snapshot.get('artifact'), dict) else {}
        history = workspace.get('artifact_history') if isinstance(workspace.get('artifact_history'), list) else []
        effective_script = script_draft if script_draft.strip() else generation.script_draft
        if artifact:
            entry = {
                'revision': artifact.get('revision'), 'artifact': artifact,
                'script_draft': effective_script,
            }
            if history and history[-1].get('revision') == entry['revision']:
                history[-1] = entry
            else:
                history.append(entry)
        workspace['artifact_history'] = history[-MAX_ARTIFACT_HISTORY:]
        if variables is not None:
            workspace['variables'] = variables
        normalized_snapshot = dict(snapshot)
        normalized_snapshot['artifact_history'] = workspace['artifact_history']
        if effective_script != generation.script_draft:
            try:
                workspace['revision'] = max(0, int(workspace.get('revision') or 0)) + 1
            except (TypeError, ValueError):
                workspace['revision'] = 1
            from .generation_workspace import _verification
            workspace['verification'] = _verification(script=effective_script)
        generation.status = target_status
        generation.current_stage = target_stage
        generation.progress = 100
        generation.error_code = error_code
        generation.error_message = error_message
        generation.exploration_snapshot = normalized_snapshot
        generation.script_draft = effective_script
        generation.workspace = workspace
        generation.quality_report = quality_report
        generation.tool_stats = normalized_snapshot.get('tool_stats') if isinstance(normalized_snapshot.get('tool_stats'), dict) else {}
        generation.warnings = warnings
        generation.completed_at = generation.completed_at or timezone.now()
        generation.save(update_fields=[
            'status', 'current_stage', 'progress', 'error_code', 'error_message',
            'exploration_snapshot', 'script_draft', 'workspace', 'quality_report',
            'tool_stats', 'warnings', 'completed_at', 'updated_at',
        ])
    return generation


def fail_generation_run(
    generation_id: Any,
    *,
    task_id: str,
    error_code: str,
    error_message: str,
    generation_revision: int | None = None,
) -> WebUIScriptGeneration | None:
    """Fail only the currently owned run and preserve every durable artifact."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        expected_revision = generation.revision if generation_revision is None else int(generation_revision)
        if (
            generation.status not in GENERATION_ACTIVE_STATUSES
            or not run_matches(
                generation,
                generation_revision=expected_revision,
                task_id=task_id,
            )
        ):
            return None
        actual_failure_stage = generation.current_stage
        snapshot = dict(generation.exploration_snapshot or {})
        snapshot['actual_failure_stage'] = actual_failure_stage
        snapshot['termination_reason'] = error_code
        generation.exploration_snapshot = snapshot
        generation.status = WebUIScriptGeneration.Status.FAILED
        generation.current_stage = WebUIScriptGeneration.Stage.COMPLETED
        generation.progress = 100
        generation.error_code = error_code
        generation.error_message = error_message
        generation.completed_at = generation.completed_at or timezone.now()
        generation.save(update_fields=[
            'exploration_snapshot', 'status', 'current_stage', 'progress',
            'error_code', 'error_message', 'completed_at', 'updated_at',
        ])
        return generation


def fail_queued_generation_dispatch(
    generation_id: Any,
    *,
    expected_revision: int,
    error_code: str,
    error_message: str,
) -> WebUIScriptGeneration:
    """Fail a broker dispatch only if no worker has claimed that revision."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = dict(generation.workspace or {})
        run = workspace.get('_agent_run') if isinstance(workspace.get('_agent_run'), dict) else {}
        if generation.revision != expected_revision or run:
            return generation
        if generation.status not in GENERATION_ACTIVE_STATUSES:
            return generation
        snapshot = dict(generation.exploration_snapshot or {})
        snapshot['actual_failure_stage'] = generation.current_stage
        snapshot['termination_reason'] = error_code
        generation.exploration_snapshot = snapshot
        generation.status = WebUIScriptGeneration.Status.FAILED
        generation.current_stage = WebUIScriptGeneration.Stage.COMPLETED
        generation.progress = 100
        generation.error_code = error_code
        generation.error_message = error_message
        generation.completed_at = generation.completed_at or timezone.now()
        generation.save(update_fields=[
            'exploration_snapshot', 'status', 'current_stage', 'progress',
            'error_code', 'error_message', 'completed_at', 'updated_at',
        ])
        return generation


def prepare_exploration_resume(
    generation_id: Any,
    *,
    expected_revision: int,
    recovery_notes: str,
    user_id: int,
) -> WebUIScriptGeneration:
    """Prepare one explicit browser exploration resume without clearing evidence."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        artifact = snapshot.get('artifact') if isinstance(snapshot.get('artifact'), dict) else {}
        if generation.revision != expected_revision:
            raise GenerationResolutionConflict('生成状态已变化，请刷新后重试。', generation)
        if generation.status not in {
            WebUIScriptGeneration.Status.FAILED,
            WebUIScriptGeneration.Status.NEEDS_REVIEW,
            WebUIScriptGeneration.Status.CANCELLED,
        }:
            raise GenerationResolutionConflict('当前生成记录不能恢复页面探索。', generation)
        if snapshot.get('schema_version') != 5 or not (
            snapshot.get('events') or (generation.script_draft or '').strip()
        ):
            raise GenerationResolutionConflict('当前记录没有可恢复的 v5 轨迹或草稿。', generation)
        if generation.resume_count >= MAX_GENERATION_RESUME_COUNT:
            raise GenerationResolutionConflict('恢复次数已达到上限，请新建生成任务。', generation)
        if workspace['verification'].get('status') in BUSY_VERIFICATION_STATUSES:
            raise GenerationResolutionConflict('当前草稿仍在调试，不能恢复页面探索。', generation)
        repair_status = workspace['repair'].get('status')
        if repair_status in BUSY_REPAIR_STATUSES:
            raise GenerationResolutionConflict('当前草稿仍在修复，不能恢复页面探索。', generation)
        if repair_status in REPAIR_CANDIDATE_STATUSES:
            raise GenerationResolutionConflict('请先采用或放弃当前修复候选。', generation)

        source_status = generation.status
        previous_lifecycle = (
            generation.workspace.get('_generation_lifecycle')
            if isinstance(generation.workspace, dict) else {}
        )
        previous_lifecycle = previous_lifecycle if isinstance(previous_lifecycle, dict) else {}
        history = list(generation.clarifications or [])
        next_revision = generation.revision + 1
        now = timezone.now()
        history.append({
            'revision': next_revision,
            'source_status': source_status,
            'error_code': generation.error_code,
            'recovery_notes': recovery_notes,
            'resumed_by': int(user_id),
            'resumed_at': now.isoformat(),
        })

        raw_workspace = _invalidate_previous_run(
            dict(generation.workspace or {}), generation.celery_task_id,
        )
        raw_workspace['_generation_lifecycle'] = {
            'revision': next_revision,
            'task_id': '',
            'claimed_at': None,
            'heartbeat_at': None,
            'last_checkpoint_at': previous_lifecycle.get('last_checkpoint_at'),
            'interrupted_at': None,
        }
        raw_workspace['_generation_recovery'] = {
            'revision': next_revision,
            'notes': recovery_notes,
            'completed_steps': list(artifact.get('completed_steps') or []),
            'remaining_steps': list(artifact.get('remaining_steps') or []),
            'previous_status': source_status,
            'last_checkpoint_at': previous_lifecycle.get('last_checkpoint_at'),
        }
        generation.workspace = raw_workspace
        generation.clarifications = history
        generation.revision = next_revision
        generation.resume_count += 1
        generation.status = WebUIScriptGeneration.Status.PREFLIGHTING
        generation.current_stage = WebUIScriptGeneration.Stage.PREFLIGHTING
        generation.progress = 25
        generation.celery_task_id = None
        generation.cancel_requested_at = None
        generation.error_code = ''
        generation.error_message = ''
        generation.completed_at = None
        generation.save(update_fields=[
            'workspace', 'clarifications', 'revision', 'resume_count', 'status',
            'current_stage', 'progress', 'celery_task_id', 'cancel_requested_at',
            'error_code', 'error_message', 'completed_at', 'updated_at',
        ])
        return generation
