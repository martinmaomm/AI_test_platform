"""Durable platform execution evidence for API workspace trial runs.

Workspace JSON remains the orchestration state.  Every real requests run also
gets a normal ``APITestExecution`` so reports survive regeneration and remain
readable through the existing execution report endpoint.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Iterable

from django.db import transaction
from django.db.models import F, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from .models import APITestCaseExecutionDetail, APITestExecution, APIWorkspace


WORKSPACE_GENERATION = 'workspace_generation'
WORKSPACE_DEBUG = 'workspace_debug'
WORKSPACE_EXECUTION_SOURCES = {WORKSPACE_GENERATION, WORKSPACE_DEBUG}


class WorkspaceExecutionStale(RuntimeError):
    pass


def _report_steps(report: Any) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    steps = report.get('step_datas')
    if isinstance(steps, list):
        return [item for item in steps if isinstance(item, dict)]
    details = report.get('details')
    if isinstance(details, list):
        for detail in reversed(details):
            if isinstance(detail, dict) and isinstance(detail.get('step_datas'), list):
                return [item for item in detail['step_datas'] if isinstance(item, dict)]
    return []


def _statistics(report: Any, total_steps: int) -> dict[str, int]:
    steps = _report_steps(report)
    passed = sum(item.get('status') == 'passed' for item in steps)
    failed = sum(item.get('status') == 'failed' for item in steps)
    errors = sum(item.get('status') == 'error' for item in steps)
    checkpoint = report.get('checkpoint') if isinstance(report, dict) and isinstance(report.get('checkpoint'), dict) else {}
    completed = checkpoint.get('completed_steps', 0)
    checkpoint_total = checkpoint.get('total_steps', total_steps)
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        completed = 0
    if not isinstance(checkpoint_total, int) or isinstance(checkpoint_total, bool) or checkpoint_total <= 0:
        checkpoint_total = total_steps
    progress = min(99, int(completed * 100 / max(checkpoint_total, 1)))
    return {
        'progress': progress,
        'total_steps': total_steps,
        'success_steps': passed,
        'failure_steps': failed,
        'error_steps': errors,
    }


def _report_log(report: Any) -> str:
    if not isinstance(report, dict):
        return ''
    if isinstance(report.get('log'), str):
        return report['log']
    details = report.get('details')
    if isinstance(details, list):
        logs = [item.get('log') for item in details if isinstance(item, dict) and isinstance(item.get('log'), str)]
        return '\n'.join(item for item in logs if item)
    return ''


@transaction.atomic
def start_workspace_execution(
    workspace: APIWorkspace,
    *,
    revision: int,
    task_id: str,
    source: str,
    attempt: int,
    draft: dict[str, Any],
    options: dict[str, Any],
) -> int:
    """Create the normal execution/detail pair immediately before the runner."""
    if source not in WORKSPACE_EXECUTION_SOURCES:
        raise ValueError('未知的工作区执行来源。')
    expected_status = APIWorkspace.Status.GENERATING if source == WORKSPACE_GENERATION else APIWorkspace.Status.DEBUGGING
    current = APIWorkspace.objects.select_for_update().filter(
        pk=workspace.id, revision=revision, task_id=task_id, status=expected_status,
    ).select_related('owner', 'project', 'saved_case').first()
    if current is None:
        raise WorkspaceExecutionStale('工作区执行租约已失效。')
    workspace = current
    now = timezone.now()
    name = str(draft.get('config', {}).get('name') or workspace.title or f'API 工作区 {workspace.id}')[:200]
    execution = APITestExecution.objects.create(
        exec_type='scenario' if len(draft.get('teststeps') or []) != 1 else 'case',
        name=name,
        description=f'API 工作区真实试运行（{source}）',
        status='running', progress=0, total_steps=len(draft.get('teststeps') or []),
        trigger_type='llm' if source == WORKSPACE_GENERATION else 'manual',
        executor=workspace.owner, project=workspace.project, task_id=task_id,
        start_time=now,
    )
    detail = APITestCaseExecutionDetail.objects.create(
        execution=execution, test_case=workspace.saved_case, name=name,
        status='running', start_time=now, httprunner_result='{}',
    )
    frozen = {
        'case_id': workspace.saved_case_id,
        'name': name,
        'script': deepcopy(draft),
        'revision': f'workspace:{workspace.id}:r{revision}',
        'options': deepcopy(options),
        'detail_id': detail.id,
    }
    execution.input_snapshot = {
        'version': 1,
        'kind': 'case',
        'cases': [frozen],
        'workspace_id': workspace.id,
        'source_revision': revision,
        'attempt': attempt,
        'source': source,
        'task_id': task_id,
    }
    execution.save(update_fields=['input_snapshot', 'updated_at'])
    return execution.id


@transaction.atomic
def store_workspace_progress(execution_id: int, report: dict[str, Any]) -> bool:
    """Persist a complete partial checkpoint without claiming a final status."""
    execution = APITestExecution.objects.select_for_update().filter(pk=execution_id, status='running').first()
    if execution is None:
        return False
    stats = _statistics(report, execution.total_steps)
    for field, value in stats.items():
        setattr(execution, field, value)
    report_log = _report_log(report)
    if report_log:
        execution.execution_log = report_log
    execution.save(update_fields=[*stats, 'execution_log', 'updated_at'])
    APITestCaseExecutionDetail.objects.filter(execution=execution).update(
        httprunner_result=json.dumps(report, ensure_ascii=False),
        log=report_log,
    )
    return True


@transaction.atomic
def finish_workspace_execution(execution_id: int, report: dict[str, Any] | None, *, error: str = '') -> bool:
    """Finalize one execution while preserving the latest runner evidence."""
    execution = APITestExecution.objects.select_for_update().filter(pk=execution_id).first()
    if execution is None:
        return False
    detail = APITestCaseExecutionDetail.objects.select_for_update().filter(execution=execution).first()
    previous_report: dict[str, Any] = {}
    if detail and detail.httprunner_result:
        try:
            parsed = json.loads(detail.httprunner_result)
            previous_report = parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            previous_report = {}
    report = deepcopy(report) if isinstance(report, dict) else previous_report
    error_type = str(report.get('error_type') or '')
    if execution.status == 'stopped' and error_type != 'Cancelled':
        return False
    if execution.status not in {'pending', 'running', 'stopped'}:
        return False
    if error_type == 'Cancelled':
        status, detail_status = 'stopped', 'skipped'
    elif report.get('success') is True:
        status = detail_status = 'passed'
    elif error_type in {'WorkerProtocolError', 'WorkerExitError'} or (error and not report):
        status = detail_status = 'error'
    else:
        status = detail_status = 'failed'
    now = timezone.now()
    stats = _statistics(report, execution.total_steps)
    stats['progress'] = 100
    for field, value in stats.items():
        setattr(execution, field, value)
    execution.status = 'stopped' if execution.status == 'stopped' else status
    execution.end_time = now
    execution.duration = max(0.0, (now - (execution.start_time or now)).total_seconds())
    execution.error_message = error or str(report.get('error') or report.get('message') or '') or None
    report_log = _report_log(report)
    if report_log:
        execution.execution_log = report_log
    execution.save(update_fields=[
        *stats, 'status', 'end_time', 'duration', 'error_message', 'execution_log', 'updated_at',
    ])
    APITestCaseExecutionDetail.objects.filter(execution=execution).update(
        status=detail_status,
        end_time=now,
        duration=execution.duration,
        error_message=execution.error_message,
        log=report_log or (detail.log if detail else ''),
        httprunner_result=json.dumps(report, ensure_ascii=False),
    )
    return True


@transaction.atomic
def stop_workspace_executions(workspace_leases: dict[int, str], *, message: str) -> None:
    """Stop only currently active evidence rows belonging to the cancelled family."""
    leases = {int(workspace_id): str(task_id) for workspace_id, task_id in workspace_leases.items() if task_id}
    if not leases:
        return
    lease_query = Q(pk__in=[])
    for workspace_id, task_id in leases.items():
        lease_query |= Q(input_snapshot__workspace_id=workspace_id, input_snapshot__task_id=task_id)
    now = timezone.now()
    executions = list(APITestExecution.objects.select_for_update().filter(
        lease_query, status__in={'pending', 'running'},
    ))
    for execution in executions:
        cancellation_note = '工作区取消时保留已有执行证据。'
        existing_log = execution.execution_log or ''
        if existing_log and cancellation_note not in existing_log:
            execution_log = f'{existing_log}\n{cancellation_note}'
        else:
            execution_log = existing_log or cancellation_note
        execution.status = 'stopped'
        execution.end_time = now
        execution.duration = max(0.0, (now - (execution.start_time or now)).total_seconds())
        execution.error_message = message
        execution.execution_log = execution_log
        execution.save(update_fields=[
            'status', 'end_time', 'duration', 'error_message', 'execution_log', 'updated_at',
        ])
        APITestCaseExecutionDetail.objects.filter(execution=execution).update(
            status='skipped', end_time=now, duration=execution.duration,
            error_message=message,
        )


def attach_execution_histories(workspaces: Iterable[APIWorkspace]) -> None:
    """Attach at most twenty rows per workspace with one bounded SQL query."""
    values = list(workspaces)
    ids = list(dict.fromkeys(item.id for item in values if item.id is not None))
    grouped: dict[int, list[dict[str, Any]]] = {workspace_id: [] for workspace_id in ids}
    if ids:
        executions = APITestExecution.objects.filter(
            input_snapshot__workspace_id__in=ids,
            input_snapshot__source__in=WORKSPACE_EXECUTION_SOURCES,
        ).annotate(
            _history_workspace_id=F('input_snapshot__workspace_id'),
            _history_source=F('input_snapshot__source'),
            _history_attempt=F('input_snapshot__attempt'),
            _workspace_rank=Window(
                expression=RowNumber(),
                partition_by=[F('input_snapshot__workspace_id')],
                order_by=F('created_at').desc(),
            ),
        ).filter(_workspace_rank__lte=20).order_by('_history_workspace_id', '-created_at').values(
            'id', 'status', 'name', 'created_at',
            '_history_workspace_id', '_history_source', '_history_attempt',
        )
        for execution in executions:
            workspace_id = execution['_history_workspace_id']
            if workspace_id not in grouped:
                continue
            grouped[workspace_id].append({
                'id': execution['id'],
                'status': execution['status'],
                'name': execution['name'],
                'source': execution['_history_source'],
                'attempt': execution['_history_attempt'],
                'created_at': execution['created_at'].isoformat() if execution['created_at'] else None,
            })
    for workspace in values:
        workspace._execution_history = grouped.get(workspace.id, [])
