"""API requests execution and browser-discovery task entry points."""
from __future__ import annotations

import asyncio
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from common.task import update_task_progress
from .workspace_tasks import debug_api_workspace, generate_and_verify_api_workspace  # noqa: F401

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='api_testing.tasks.execute_api_test_case_async')
def execute_api_test_case_async(self, execution_id, test_case_id=None, environment_id=None):
    return _execute_api_test_case(self, execution_id, test_case_id, environment_id)


def _execute_api_test_case(task_instance, execution_id, test_case_id=None, environment_id=None):
    from .execution_service import run_execution
    return run_execution(execution_id, progress=lambda value, message: update_task_progress(task_instance, value, message))


@shared_task(bind=True, name='api_testing.tasks.execute_api_test_suite_async')
def execute_api_test_suite_async(self, execution_id, test_suite_id=None, environment_id=None, task_execution_log_id=None):
    return _execute_api_test_suite(self, execution_id, test_suite_id, environment_id, task_execution_log_id)


def _execute_api_test_suite(task_instance, execution_id, test_suite_id=None, environment_id=None, task_execution_log_id=None):
    from .execution_service import run_execution
    return run_execution(
        execution_id, suite=True, scheduled_log_id=task_execution_log_id,
        progress=lambda value, message: update_task_progress(task_instance, value, message),
    )


def _claim_browser_discovery(discovery_id: str, version: int, task_id: str):
    from .browser_discovery import browser_discovery_enabled
    from .models import BrowserDiscoveryTask
    from .workspace_service import can_edit_project, can_execute_project

    with transaction.atomic():
        task = BrowserDiscoveryTask.objects.select_for_update().select_related('project', 'owner').filter(pk=discovery_id).first()
        if (
            not task or task.version != version or task.task_id != task_id
            or task.status != BrowserDiscoveryTask.Status.QUEUED or task.cancellation_requested
        ):
            return None
        failure = ''
        if not browser_discovery_enabled():
            failure = 'feature_disabled'
        elif task.project.project_type != 'api':
            failure = 'project_not_api'
        elif not can_edit_project(task.project, task.owner) or not can_execute_project(task.project, task.owner):
            failure = 'permission_revoked'
        if failure:
            task.status = BrowserDiscoveryTask.Status.FAILED
            task.error_code = failure
            task.error_message = '任务开始前的功能、项目类型或所有者权限校验未通过。'
            task.current_action = '启动前校验未通过'
            task.finished_at = task.heartbeat_at = timezone.now()
            task.save()
            return None
        task.status = BrowserDiscoveryTask.Status.RUNNING
        task.current_action = '正在启动浏览器探索'
        task.started_at = task.heartbeat_at = timezone.now()
        task.save(update_fields=['status', 'current_action', 'started_at', 'heartbeat_at', 'updated_at'])
        return task


def _browser_discovery_checkpoint(discovery_id: str, version: int, task_id: str, payload: dict) -> bool:
    """Persist a bounded heartbeat and safely tell the async runner to stop."""
    from .browser_discovery import ingest_trace, origin_resolution, sync_auto_origin
    from .models import BrowserDiscoveryTask

    with transaction.atomic():
        task = BrowserDiscoveryTask.objects.select_for_update().filter(pk=discovery_id).first()
        if not task or task.version != version or task.task_id != task_id:
            return False
        if task.status not in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING} or task.cancellation_requested:
            return False
        state = sync_auto_origin(task)
        resolution = origin_resolution(task, state=state)
        payload = payload if isinstance(payload, dict) else {}
        elapsed = payload.get('elapsed_seconds')
        if isinstance(elapsed, (int, float)) and elapsed > task.exploration_timeout_seconds:
            task.current_action = '探索总时限已到，正在停止'
            task.error_code = 'timeout'
            task.save(update_fields=['current_action', 'error_code', 'updated_at'])
            return False
        task.current_action = (
            '正在等待确认跨来源请求' if resolution['state'] == 'awaiting_confirmation'
            else str(payload.get('current_action') or payload.get('phase') or '正在探索')[:500]
        )
        task.tool_calls = min(int(payload.get('tool_calls') or 0), int(task.limits.get('max_tool_calls', 100)))
        task.model_calls = min(int(payload.get('model_calls') or 0), int(task.limits.get('max_model_steps', 100)))
        task.heartbeat_at = timezone.now()
        task.save(update_fields=['current_action', 'tool_calls', 'model_calls', 'heartbeat_at', 'updated_at'])
    try:
        ingest_trace(task)
    except Exception as exc:
        logger.warning('Browser discovery trace ingest failed for %s: %s', discovery_id, exc)
        return False
    return True


def _browser_discovery_cancelled(discovery_id: str, version: int, task_id: str) -> bool:
    from .models import BrowserDiscoveryTask
    return not BrowserDiscoveryTask.objects.filter(
        pk=discovery_id, version=version, task_id=task_id,
        status__in=[BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING],
        cancellation_requested=False,
    ).exists()


def _browser_discovery_origin_pending(discovery_id: str, version: int, task_id: str) -> bool:
    """The agent gate reads only task-scoped, credential-free Node state."""
    from .browser_discovery import read_origin_state
    from .models import BrowserDiscoveryTask

    task = BrowserDiscoveryTask.objects.filter(
        pk=discovery_id, version=version, task_id=task_id,
        status__in=[BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING],
        cancellation_requested=False,
    ).first()
    return bool(task and read_origin_state(task)['pending'])


def _finish_browser_discovery(discovery_id: str, version: int, task_id: str, result: dict, *, exception: Exception | None = None):
    from .browser_discovery import evidence_statistics, ingest_trace, sync_auto_origin
    from .models import BrowserDiscoveryTask

    with transaction.atomic():
        task = BrowserDiscoveryTask.objects.select_for_update().filter(pk=discovery_id).first()
        if not task or task.version != version or task.task_id != task_id:
            return None
        if task.status not in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}:
            return task
        task.status = BrowserDiscoveryTask.Status.FINALIZING
        task.current_action = '正在整理浏览器证据'
        task.heartbeat_at = timezone.now()
        task.save(update_fields=['status', 'current_action', 'heartbeat_at', 'updated_at'])
    ingest_error = ''
    ingest_result = {}
    try:
        ingest_result = ingest_trace(task)
    except Exception as exc:
        ingest_error = str(exc)[:2000]
    with transaction.atomic():
        task = BrowserDiscoveryTask.objects.select_for_update().filter(pk=discovery_id).first()
        if not task or task.version != version or task.task_id != task_id:
            return None
        if task.status not in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}:
            return task
        sync_auto_origin(task)
        evidence = evidence_statistics(task, ingest_result=ingest_result)
        records_count = evidence['records']
        completed = bool(result.get('completed')) if isinstance(result, dict) else False
        error_code = str(result.get('error_code') or '')[:80] if isinstance(result, dict) else ''
        summary = str(result.get('summary') or '')[:4000] if isinstance(result, dict) else ''
        if exception is not None:
            error_code = 'runner_failed'
            task.error_message = f'浏览器探索执行异常（{type(exception).__name__}），请检查模型和 MCP 配置。'
        elif ingest_error:
            error_code = 'capture_incomplete'
            task.error_message = ingest_error
        elif error_code and error_code.lower() != 'cancelled' and not completed:
            # Failed runner summaries are platform-generated diagnostics, not
            # the model's free-form completion. Preserve them in the field
            # displayed by the task detail instead of showing only a code.
            task.error_message = summary or '网页探索未完成，请查看任务诊断。'
        if task.cancellation_requested or error_code.lower() == 'cancelled':
            status = BrowserDiscoveryTask.Status.CANCELLED
        elif completed and evidence['usable'] and not any(
            evidence[key] for key in ('incomplete', 'pending', 'invalid_lines', 'over_limit', 'truncated')
        ):
            status = BrowserDiscoveryTask.Status.COMPLETED
        elif evidence['usable'] or evidence['resolved_records']:
            status = BrowserDiscoveryTask.Status.PARTIAL
            error_code = error_code or ('capture_incomplete' if evidence['usable'] else '')
        else:
            status = BrowserDiscoveryTask.Status.FAILED
            error_code = error_code or ('no_usable_records' if records_count else 'no_records')
        task.status = status
        task.current_action = '已结束'
        task.tool_calls = min(int(result.get('tool_calls') or task.tool_calls) if isinstance(result, dict) else task.tool_calls, int(task.limits.get('max_tool_calls', 100)))
        task.model_calls = min(int(result.get('model_calls') or task.model_calls) if isinstance(result, dict) else task.model_calls, int(task.limits.get('max_model_steps', 100)))
        task.summary = summary
        task.error_code = error_code
        task.evidence_summary = evidence
        task.source_version = task.version if records_count else 0
        task.finished_at = task.heartbeat_at = timezone.now()
        task.save()
        return task


@shared_task(bind=True, name='api_testing.tasks.run_browser_discovery_async')
def run_browser_discovery_async(self, discovery_id: str, version: int, task_id: str):
    """Load ORM/config synchronously, then pass primitives to the async agent only."""
    task = _claim_browser_discovery(discovery_id, version, task_id)
    if task is None:
        return {'status': 'ignored'}
    result = {}
    try:
        from ai_core.model_manager import get_llm_manager
        from .browser_discovery import resolve_browser_discovery_mcp_config, task_trace_dir, task_trace_file
        from .workspace_service import require_generation_model_id
        from .browser_discovery_agent import run_browser_discovery

        require_generation_model_id(task.model_id, owner=task.owner)
        if not task.allow_test_data_writes:
            raise ValueError('浏览器探索缺少测试数据写入确认。')
        manager = get_llm_manager(task.model_id)
        llm_model = manager.current_llm
        if llm_model is None:
            raise ValueError('所选 LLM 未初始化。')
        mcp_config = resolve_browser_discovery_mcp_config(task.owner_id)
        trace_dir = task_trace_dir(task)
        trace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        capture_limits = {
            'max_requests': task.limits['max_requests'],
            'max_body_bytes': task.limits['max_body_bytes'],
            'max_total_body_bytes': task.limits['max_total_bytes'],
        }
        result = asyncio.run(run_browser_discovery(
            llm_model=llm_model, mcp_config=mcp_config, task_id=task.task_id,
            target_url=task.target_url, description=task.description, api_origin=task.api_origin,
            trace_file=str(task_trace_file(task)), timeout_seconds=task.exploration_timeout_seconds,
            max_steps=task.limits['max_model_steps'], max_tool_calls=task.limits['max_tool_calls'],
            capture_limits=capture_limits,
            checkpoint=lambda payload: _browser_discovery_checkpoint(discovery_id, version, task_id, payload),
            is_cancelled=lambda: _browser_discovery_cancelled(discovery_id, version, task_id),
            origin_pending=lambda: _browser_discovery_origin_pending(discovery_id, version, task_id),
        ))
        if not isinstance(result, dict):
            raise ValueError('浏览器探索 runner 返回格式无效。')
        _finish_browser_discovery(discovery_id, version, task_id, result)
    except Exception as exc:
        logger.exception('Browser discovery failed for %s', discovery_id)
        _finish_browser_discovery(discovery_id, version, task_id, result, exception=exc)
    return {'status': 'finished'}
