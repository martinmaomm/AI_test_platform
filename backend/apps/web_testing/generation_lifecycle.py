"""Worker ownership, heartbeat, and interruption lifecycle for generations.

Lifecycle data is stored inside the existing ``workspace`` JSON field.  The
private metadata is intentionally reduced to a fixed public projection by the
serializers; task identifiers never leave the backend lifecycle helpers.
"""

from __future__ import annotations

import os
import math
import logging
import threading
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from typing import Any

from django.db import close_old_connections, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import WebUIScriptGeneration


logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_ENV = 'WEBUI_GENERATION_HEARTBEAT_INTERVAL_SECONDS'
STALE_TIMEOUT_ENV = 'WEBUI_GENERATION_STALE_TIMEOUT_SECONDS'
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
DEFAULT_STALE_TIMEOUT_SECONDS = 120.0
MIN_STALE_INTERVALS = 3

GENERATION_ACTIVE_STATUSES = frozenset({
    WebUIScriptGeneration.Status.CREATED,
    WebUIScriptGeneration.Status.NORMALIZING,
    WebUIScriptGeneration.Status.PREFLIGHTING,
    WebUIScriptGeneration.Status.EXPLORING,
    WebUIScriptGeneration.Status.GENERATING,
    WebUIScriptGeneration.Status.VALIDATING,
})
RESUMABLE_GENERATION_STATUSES = frozenset({
    WebUIScriptGeneration.Status.FAILED,
    WebUIScriptGeneration.Status.NEEDS_REVIEW,
    WebUIScriptGeneration.Status.CANCELLED,
})


def _positive_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def heartbeat_interval_seconds() -> float:
    return _positive_seconds(HEARTBEAT_INTERVAL_ENV, DEFAULT_HEARTBEAT_INTERVAL_SECONDS)


def stale_timeout_seconds() -> float:
    interval = heartbeat_interval_seconds()
    configured = _positive_seconds(STALE_TIMEOUT_ENV, DEFAULT_STALE_TIMEOUT_SECONDS)
    return max(configured, interval * MIN_STALE_INTERVALS)


def _iso(value: datetime | None = None) -> str:
    return (value or timezone.now()).isoformat()


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = parse_datetime(value)
        except (OverflowError, TypeError, ValueError):
            return None
    else:
        return None
    if result is not None and timezone.is_naive(result):
        result = timezone.make_aware(result, timezone.get_current_timezone())
    return result


def _public_timestamp(value: Any) -> str | None:
    parsed = _datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _workspace(generation: WebUIScriptGeneration) -> dict[str, Any]:
    return dict(generation.workspace or {}) if isinstance(generation.workspace, dict) else {}


def _task_marker(task_id: str | None) -> str:
    return str(task_id or '<direct>')


def run_matches(
    generation: WebUIScriptGeneration,
    *,
    generation_revision: int,
    task_id: str,
) -> bool:
    workspace = _workspace(generation)
    run = workspace.get('_agent_run') if isinstance(workspace.get('_agent_run'), dict) else {}
    marker = _task_marker(task_id)
    try:
        run_revision = int(run.get('generation_revision', -1))
    except (TypeError, ValueError):
        return False
    return (
        generation.revision == int(generation_revision)
        and run_revision == int(generation_revision)
        and str(run.get('task_id') or '') == marker
        and (marker == '<direct>' or generation.celery_task_id == marker)
    )


def claim_lifecycle_metadata(
    workspace: dict[str, Any],
    *,
    generation_revision: int,
    task_id: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return workspace metadata for a newly claimed attempt."""
    result = dict(workspace)
    previous = result.get('_generation_lifecycle')
    previous = previous if isinstance(previous, dict) else {}
    marker = _task_marker(task_id)
    timestamp = _iso(now)
    result['_agent_run'] = {
        'generation_revision': int(generation_revision),
        'task_id': marker,
        'started_at': timestamp,
    }
    result['_generation_lifecycle'] = {
        'revision': int(generation_revision),
        'task_id': marker,
        'claimed_at': timestamp,
        'heartbeat_at': timestamp,
        'last_checkpoint_at': previous.get('last_checkpoint_at'),
        'interrupted_at': None,
    }
    return result


def checkpoint_lifecycle_metadata(
    workspace: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    result = dict(workspace)
    lifecycle = result.get('_generation_lifecycle')
    lifecycle = dict(lifecycle) if isinstance(lifecycle, dict) else {}
    timestamp = _iso(now)
    lifecycle['heartbeat_at'] = timestamp
    lifecycle['last_checkpoint_at'] = timestamp
    result['_generation_lifecycle'] = lifecycle
    return result


def touch_generation_heartbeat(
    generation_id: Any,
    *,
    generation_revision: int,
    task_id: str,
    now: datetime | None = None,
) -> bool:
    """Pulse only while the exact claimed generation attempt still owns the row."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if (
            generation.status not in GENERATION_ACTIVE_STATUSES
            or not run_matches(
                generation,
                generation_revision=generation_revision,
                task_id=task_id,
            )
        ):
            return False
        workspace = _workspace(generation)
        lifecycle = workspace.get('_generation_lifecycle')
        lifecycle = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        lifecycle.update({
            'revision': int(generation_revision),
            'task_id': _task_marker(task_id),
            'heartbeat_at': _iso(now),
        })
        workspace['_generation_lifecycle'] = lifecycle
        generation.workspace = workspace
        # A pulse is worker liveness, not an artifact edit.
        generation.save(update_fields=['workspace'])
        return True


def is_generation_run_active(
    generation_id: Any, *, generation_revision: int, task_id: str,
) -> bool:
    marker = _task_marker(task_id)
    queryset = WebUIScriptGeneration.objects.filter(
        pk=generation_id,
        status__in=GENERATION_ACTIVE_STATUSES,
        revision=generation_revision,
    )
    if marker != '<direct>':
        queryset = queryset.filter(celery_task_id=marker)
    run = queryset.annotate(
        run_metadata=models.F('workspace___agent_run'),
    ).values_list('run_metadata', flat=True).first()
    if not isinstance(run, dict):
        return False
    try:
        run_revision = int(run.get('generation_revision', -1))
    except (TypeError, ValueError):
        return False
    return run_revision == int(generation_revision) and str(run.get('task_id') or '') == marker


def _has_v5_resume_evidence(generation: WebUIScriptGeneration) -> bool:
    snapshot = generation.exploration_snapshot
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return (
        snapshot.get('schema_version') == 5
        and bool(snapshot.get('events') or (generation.script_draft or '').strip())
    )


def generation_can_resume(generation: WebUIScriptGeneration) -> bool:
    if (
        generation.status not in RESUMABLE_GENERATION_STATUSES
        or not _has_v5_resume_evidence(generation)
    ):
        return False
    from .generation_repository import MAX_GENERATION_RESUME_COUNT
    from .generation_workspace import (
        BUSY_REPAIR_STATUSES,
        BUSY_VERIFICATION_STATUSES,
        REPAIR_CANDIDATE_STATUSES,
        workspace_for_generation,
    )

    workspace = workspace_for_generation(generation)
    return (
        generation.resume_count < MAX_GENERATION_RESUME_COUNT
        and workspace['verification'].get('status') not in BUSY_VERIFICATION_STATUSES
        and workspace['repair'].get('status') not in BUSY_REPAIR_STATUSES
        and workspace['repair'].get('status') not in REPAIR_CANDIDATE_STATUSES
    )


def lifecycle_for_generation(generation: WebUIScriptGeneration) -> dict[str, Any]:
    workspace = _workspace(generation)
    metadata = workspace.get('_generation_lifecycle')
    metadata = metadata if isinstance(metadata, dict) else {}
    run = workspace.get('_agent_run') if isinstance(workspace.get('_agent_run'), dict) else {}
    try:
        metadata_revision = int(metadata.get('revision', -1))
        run_revision = int(run.get('generation_revision', -1))
    except (TypeError, ValueError):
        metadata_revision = run_revision = -1
    claimed = bool(metadata.get('claimed_at')) and run_matches(
        generation,
        generation_revision=generation.revision,
        task_id=str(metadata.get('task_id') or ''),
    ) and metadata_revision == generation.revision and run_revision == generation.revision
    if generation.error_code == 'GENERATION_INTERRUPTED':
        state = 'interrupted'
    elif generation.status in GENERATION_ACTIVE_STATUSES:
        state = 'running' if claimed else 'queued'
    else:
        state = 'idle'
    return {
        'state': state,
        'heartbeat_at': _public_timestamp(metadata.get('heartbeat_at')) if claimed or state == 'interrupted' else None,
        'last_checkpoint_at': _public_timestamp(metadata.get('last_checkpoint_at')),
        'interrupted_at': _public_timestamp(metadata.get('interrupted_at')) if state == 'interrupted' else None,
        'can_resume': generation_can_resume(generation),
    }


def _effective_timeout(timeout_seconds: float | None) -> float:
    minimum = heartbeat_interval_seconds() * MIN_STALE_INTERVALS
    if timeout_seconds is None:
        return stale_timeout_seconds()
    try:
        supplied = float(timeout_seconds)
    except (TypeError, ValueError):
        return stale_timeout_seconds()
    if not math.isfinite(supplied) or supplied <= 0:
        return stale_timeout_seconds()
    return max(supplied, minimum)


def _reconcile_locked_generation(
    generation: WebUIScriptGeneration,
    *,
    current_time: datetime,
    timeout: float,
) -> WebUIScriptGeneration:
    if generation.status not in GENERATION_ACTIVE_STATUSES:
        return generation
    workspace = _workspace(generation)
    metadata = workspace.get('_generation_lifecycle')
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    task_id = str(metadata.get('task_id') or '')
    try:
        metadata_revision = int(metadata.get('revision', -1))
    except (TypeError, ValueError):
        return generation
    # Pre-lifecycle records may have old dispatch metadata but no lease.
    # Compatibility rows remain observable and are never guessed stale.
    if not metadata.get('heartbeat_at'):
        return generation
    heartbeat_at = (
        _datetime(metadata.get('heartbeat_at'))
        or _datetime(metadata.get('claimed_at'))
        or generation.updated_at
    )
    if (
        not metadata.get('claimed_at')
        or metadata_revision != generation.revision
        or not run_matches(
            generation,
            generation_revision=generation.revision,
            task_id=task_id,
        )
        or current_time - heartbeat_at < timedelta(seconds=timeout)
    ):
        return generation

    actual_failure_stage = generation.current_stage
    snapshot = dict(generation.exploration_snapshot or {})
    snapshot['actual_failure_stage'] = actual_failure_stage
    snapshot['termination_reason'] = 'GENERATION_INTERRUPTED'
    interrupted_at = _iso(current_time)
    metadata['interrupted_at'] = interrupted_at
    workspace['_generation_lifecycle'] = metadata
    generation.workspace = workspace
    generation.exploration_snapshot = snapshot
    generation.status = WebUIScriptGeneration.Status.NEEDS_REVIEW
    # Keep current_stage as the exact failed stage; status carries reviewability.
    generation.error_code = 'GENERATION_INTERRUPTED'
    generation.error_message = '生成工作进程已中断，已保留现有轨迹和草稿，请检查后明确恢复。'
    generation.completed_at = generation.completed_at or current_time
    generation.save(update_fields=[
        'workspace', 'exploration_snapshot', 'status', 'error_code',
        'error_message', 'completed_at', 'updated_at',
    ])
    return generation


def reconcile_stale_generation(
    generation_id: Any,
    *,
    now: datetime | None = None,
    timeout_seconds: float | None = None,
) -> WebUIScriptGeneration:
    """Salvage a claimed stale attempt without replaying it or clearing artifacts."""
    current_time = now or timezone.now()
    timeout = _effective_timeout(timeout_seconds)
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        return _reconcile_locked_generation(
            generation, current_time=current_time, timeout=timeout,
        )


def reconcile_stale_generations(records, *, now: datetime | None = None) -> list[WebUIScriptGeneration]:
    """Reconcile claimed records from an already authorized, bounded result set."""
    records = list(records)
    current_time = now or timezone.now()
    timeout = _effective_timeout(None)
    candidate_ids = []
    for generation in records:
        metadata = getattr(generation, 'lifecycle_metadata', None)
        if metadata is None and 'workspace' in generation.__dict__:
            workspace = generation.__dict__.get('workspace')
            workspace = workspace if isinstance(workspace, dict) else {}
            metadata = workspace.get('_generation_lifecycle')
        metadata = metadata if isinstance(metadata, dict) else {}
        # The history query annotates only this small JSON object.  Fresh rows
        # never need a second query or row lock; stale candidates are fully
        # re-read and ownership-checked under the lock below.
        if not metadata.get('heartbeat_at'):
            continue
        heartbeat_at = (
            _datetime(metadata.get('heartbeat_at'))
            or _datetime(metadata.get('claimed_at'))
            or generation.updated_at
        )
        if (
            generation.status in GENERATION_ACTIVE_STATUSES
            and metadata.get('claimed_at')
            and heartbeat_at is not None
            and current_time - heartbeat_at >= timedelta(seconds=timeout)
        ):
            candidate_ids.append(generation.pk)
    if not candidate_ids:
        return records
    with transaction.atomic():
        locked = {
            generation.pk: generation
            for generation in WebUIScriptGeneration.objects.select_for_update().filter(
                pk__in=candidate_ids,
            ).order_by('pk')
        }
        reconciled = {
            generation_id: _reconcile_locked_generation(
                locked[generation_id], current_time=current_time, timeout=timeout,
            )
            for generation_id in candidate_ids if generation_id in locked
        }
    return [reconciled.get(generation.pk, generation) for generation in records]


def recovery_context_for_generation(generation: WebUIScriptGeneration) -> dict[str, Any] | None:
    workspace = _workspace(generation)
    recovery = workspace.get('_generation_recovery')
    if not isinstance(recovery, dict):
        return None
    try:
        revision = int(recovery.get('revision', -1))
    except (TypeError, ValueError):
        return None
    if revision != generation.revision:
        return None
    return {
        'notes': str(recovery.get('notes') or '')[:2000],
        'completed_steps': [str(item) for item in (recovery.get('completed_steps') or [])],
        'remaining_steps': [str(item) for item in (recovery.get('remaining_steps') or [])],
        'previous_status': str(recovery.get('previous_status') or ''),
        'last_checkpoint_at': _public_timestamp(recovery.get('last_checkpoint_at')),
    }


class GenerationHeartbeat(AbstractContextManager):
    """Independent pulse thread that keeps long synchronous model calls alive."""

    def __init__(self, generation_id: Any, generation_revision: int, task_id: str):
        self.generation_id = generation_id
        self.generation_revision = int(generation_revision)
        self.task_id = _task_marker(task_id)
        self.interval = heartbeat_interval_seconds()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=self._run,
            name=f'webui-generation-heartbeat-{self.generation_id}',
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            close_old_connections()
            try:
                try:
                    if not touch_generation_heartbeat(
                        self.generation_id,
                        generation_revision=self.generation_revision,
                        task_id=self.task_id,
                    ):
                        return
                except Exception:
                    logger.warning(
                        'WebUI 生成心跳写入失败，将在下个间隔重试: generation_id=%s revision=%s',
                        self.generation_id, self.generation_revision, exc_info=True,
                    )
            finally:
                close_old_connections()

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, min(self.interval, 5.0)))
        return False


def generation_heartbeat(
    generation_id: Any, generation_revision: int, task_id: str,
) -> GenerationHeartbeat:
    return GenerationHeartbeat(generation_id, generation_revision, task_id)
