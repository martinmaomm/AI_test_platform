"""Durable orchestration for one scheduled-task run.

The execution log's ordered ``linked_executions`` is the run plan.  Snapshots
for every suite are persisted before any worker message is published, while
only one suite is dispatched at a time.  A suite executor calls
``finish_scheduled_suite`` after reaching a terminal state; that callback
queues the next item without waiting for it in the current worker.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .contracts import get_schedule_project, validate_suites
from .models import ScheduledTask, TaskExecutionLog
from .reporting import platform_report_url, refresh_execution_log_from_links

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {'passed', 'failed', 'error', 'stopped', 'incomplete', 'skipped'}
SERIAL_PLAN_KEY = 'serial_dispatch_state'


@dataclass(frozen=True)
class ScheduledRunReservation:
    """Result returned to views and Celery entrypoints after the task lock."""

    started: bool
    execution_log_id: int | None = None
    error: str | None = None
    already_running: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            'started': self.started,
            'execution_log_id': self.execution_log_id,
            'error': self.error,
            'already_running': self.already_running,
        }


def reserve_scheduled_run(task_id: int, *, manual: bool) -> ScheduledRunReservation:
    """Reserve exactly one run and enqueue its first suite after commit.

    This is the safe public entrypoint for both the manual-run view and Beat.
    The per-task row lock prevents an overlapping run from creating a second
    execution log.  Manual invocations are allowed while paused; Beat runs are
    discarded after a pause without creating a misleading successful log.
    """
    with transaction.atomic():
        task = ScheduledTask.objects.select_for_update().select_related('project', 'environment').filter(
            pk=task_id,
        ).first()
        if task is None:
            return ScheduledRunReservation(False, error=f'定时任务不存在: {task_id}')
        if not manual and task.status != 'active':
            return ScheduledRunReservation(False, error='定时任务已暂停，已忽略队列中的自动触发')

        active_log = task.execution_logs.select_for_update().filter(
            status__in={'pending', 'running'},
        ).order_by('start_time', 'pk').first()
        if active_log is not None:
            return ScheduledRunReservation(
                False,
                execution_log_id=active_log.id,
                error='同一定时任务已有执行尚未结束',
                already_running=True,
            )

        execution_log = TaskExecutionLog.objects.create(
            task=task,
            start_time=timezone.now(),
            status='running',
        )
        execution_log.report_url = platform_report_url(execution_log.id)
        execution_log.save(update_fields=['report_url'])

        try:
            # A failed snapshot must roll back all child executions and links,
            # while preserving the parent log as a terminal, inspectable error.
            with transaction.atomic():
                suites = _validate_execution_contract(task)
                _create_execution_plan(task, execution_log, suites)
                first_execution_id = _reserve_next_link(execution_log)
        except Exception as exc:
            error = f'定时任务执行预检失败: {exc}'
            logger.warning('%s task_id=%s', error, task.id, exc_info=True)
            execution_log.status = 'failed'
            execution_log.end_time = timezone.now()
            execution_log.error_message = error
            execution_log.result_log = _failure_result(error)
            execution_log.save(update_fields=['status', 'end_time', 'error_message', 'result_log'])
            transaction.on_commit(lambda log_id=execution_log.id: _notify_finished_run(log_id))
            return ScheduledRunReservation(False, execution_log.id, error=error)

        # Avoid ScheduledTask.save(): it fires Beat registration signals.  Keep
        # the display timestamp fresh with the same Cron helper used by save.
        from .cron import next_run_time
        ScheduledTask.objects.filter(pk=task.id).update(
            last_run_time=timezone.now(),
            next_run_time=next_run_time(task.cron_expression) if task.status == 'active' else None,
        )
        if first_execution_id is None:
            # ``validate_suites`` normally prevents this branch.  Keep the
            # failure terminal if an unexpected race leaves no runnable link.
            error = '定时任务执行预检失败: 没有可派发的测试套件'
            execution_log.status = 'failed'
            execution_log.end_time = timezone.now()
            execution_log.error_message = error
            execution_log.result_log = _failure_result(error)
            execution_log.save(update_fields=['status', 'end_time', 'error_message', 'result_log'])
            transaction.on_commit(lambda log_id=execution_log.id: _notify_finished_run(log_id))
            return ScheduledRunReservation(False, execution_log.id, error=error)

        transaction.on_commit(
            lambda log_id=execution_log.id, execution_id=first_execution_id: _enqueue_suite_dispatch(
                log_id, execution_id,
            )
        )
        return ScheduledRunReservation(True, execution_log.id)


def finish_scheduled_suite(scheduled_log_id: int | None, execution_id: int, *, append_log: str = '') -> None:
    """Refresh the parent report and asynchronously advance one serial plan."""
    if not scheduled_log_id:
        return
    with transaction.atomic():
        execution_log = TaskExecutionLog.objects.select_for_update().filter(pk=scheduled_log_id).first()
        if execution_log is None:
            logger.warning('定时任务执行日志不存在，无法推进: %s', scheduled_log_id)
            return
        link = _find_link(execution_log, execution_id)
        if link is None:
            logger.warning('执行记录不属于定时任务计划: log=%s execution=%s', scheduled_log_id, execution_id)
            return
        if link.get(SERIAL_PLAN_KEY) == 'finished':
            # A retrying executor must not append the same log, dispatch a
            # second successor, or resend the one-time completion notice.
            return
        if not _execution_is_terminal(link):
            logger.warning('非终态执行记录不能推进串行计划: log=%s execution=%s', scheduled_log_id, execution_id)
            return
        # A missing execution is terminal by definition and is reported by the
        # aggregate as an execution-level error.  It must not strand the plan.
        link[SERIAL_PLAN_KEY] = 'finished'
        _save_links(execution_log)

        summary = refresh_execution_log_from_links(execution_log, append_log=append_log)
        next_execution_id = _reserve_next_link(execution_log)
        if next_execution_id is not None:
            transaction.on_commit(
                lambda log_id=execution_log.id, execution_id=next_execution_id: _enqueue_suite_dispatch(
                    log_id, execution_id,
                )
            )
        elif not summary['is_running']:
            transaction.on_commit(lambda log_id=execution_log.id: _notify_finished_run(log_id))


@shared_task(name='scheduled_tasks.dispatch_scheduled_suite')
def dispatch_scheduled_suite(scheduled_log_id: int, execution_id: int) -> dict[str, Any]:
    """Publish exactly one prepared suite task after its plan transaction commits."""
    with transaction.atomic():
        execution_log = TaskExecutionLog.objects.select_for_update().select_related(
            'task', 'task__environment',
        ).filter(pk=scheduled_log_id).first()
        if execution_log is None or execution_log.status not in {'pending', 'running'}:
            return {'success': False, 'message': '执行日志不存在或已结束'}
        link = _find_link(execution_log, execution_id)
        if link is None or link.get(SERIAL_PLAN_KEY) != 'queued':
            return {'success': False, 'message': '套件未处于待派发状态'}
        link[SERIAL_PLAN_KEY] = 'dispatching'
        _save_links(execution_log)

    try:
        task_id = _dispatch_prepared_suite(execution_log.task, execution_log.id, link)
    except Exception as exc:
        error = f'派发测试套件失败: {exc}'
        logger.error('%s log=%s execution=%s', error, scheduled_log_id, execution_id, exc_info=True)
        _record_dispatch_failure(scheduled_log_id, execution_id, error)
        return {'success': False, 'error': error}

    with transaction.atomic():
        execution_log = TaskExecutionLog.objects.select_for_update().get(pk=scheduled_log_id)
        link = _find_link(execution_log, execution_id)
        if link is not None and link.get(SERIAL_PLAN_KEY) != 'finished':
            link[SERIAL_PLAN_KEY] = 'dispatched'
            _save_links(execution_log)
    _set_execution_task_id(link['kind'], execution_id, task_id)
    return {'success': True, 'execution_id': execution_id, 'task_id': task_id}


def _validate_execution_contract(task: ScheduledTask):
    # A task may have outlived a membership change.  Re-check its creator's
    # current execute capability before any snapshot or worker message exists.
    project = get_schedule_project(task.project_id, task.user, 'execute')
    if task.suite_type not in {'web', 'api'}:
        raise ValueError(f'不支持的测试类型: {task.suite_type}')
    if project.project_type != task.suite_type:
        raise ValueError('定时任务类型与所属项目类型不一致')
    suites = validate_suites(project, task.suite_ids)
    if task.suite_type == 'api':
        environment = task.environment
        from projects.models import Environment
        if environment is None:
            raise ValueError('API 定时任务必须选择环境')
        if environment.project_id != task.project_id:
            raise ValueError('执行环境不属于当前项目')
        if not environment.is_active:
            raise ValueError('执行环境未启用')
        if environment.category != Environment.EnvironmentCategory.API:
            raise ValueError('执行环境不是 API 环境')
    elif task.environment_id is not None:
        raise ValueError('Web 定时任务不能配置 API 执行环境')
    return suites


def _create_execution_plan(task: ScheduledTask, execution_log: TaskExecutionLog, suites) -> None:
    links: list[dict[str, Any]] = []
    for index, suite in enumerate(suites):
        if task.suite_type == 'web':
            execution = _create_web_snapshot(task, suite)
        else:
            execution = _create_api_snapshot(task, suite)
        links.append({
            'kind': task.suite_type,
            'project_id': task.project_id,
            'execution_id': execution.id,
            'name': suite.name,
            'suite_id': suite.id,
            'sequence': index,
            SERIAL_PLAN_KEY: 'ready',
        })
    execution_log.linked_executions = links
    execution_log.total_cases = sum(_snapshot_case_count(link['kind'], link['execution_id']) for link in links)
    execution_log.save(update_fields=['linked_executions', 'total_cases'])


def _create_web_snapshot(task: ScheduledTask, suite):
    from web_testing.execution_snapshots import capture_suite_snapshot
    from web_testing.models import WebUITestExecution

    execution = WebUITestExecution.objects.create(
        exec_type='suite', name=f'{suite.name} - 定时任务', description=f'定时任务执行: {task.name}',
        status='pending', trigger_type='schedule', executor=task.user,
        project=suite.project, browser='chromium',
    )
    capture_suite_snapshot(execution, suite)
    return execution


def _create_api_snapshot(task: ScheduledTask, suite):
    from api_testing.models import APITestExecution, APITestSuiteCaseExecution, APITestSuiteExecutionDetail

    execution = APITestExecution.objects.create(
        exec_type='suite', name=f'{suite.name} - 定时任务', description=f'定时任务执行: {task.name}',
        status='pending', trigger_type='schedule', executor=task.user,
        environment=task.environment, project=task.project,
    )
    test_cases = list(suite.test_cases.all())
    suite_detail = APITestSuiteExecutionDetail.objects.create(
        execution=execution, test_suite=suite, test_suite_name=suite.name, total_cases=len(test_cases),
    )
    APITestSuiteCaseExecution.objects.bulk_create([
        APITestSuiteCaseExecution(
            suite_execution=suite_detail, test_case=test_case, name=test_case.title, status='pending',
        )
        for test_case in test_cases
    ])
    from api_testing.execution_snapshots import capture_suite_snapshot
    capture_suite_snapshot(execution, suite, task.environment)
    return execution


def _snapshot_case_count(kind: str, execution_id: int) -> int:
    if kind == 'web':
        from web_testing.models import WebUITestSuiteExecutionDetail
        return WebUITestSuiteExecutionDetail.objects.filter(execution_id=execution_id).values_list('total_cases', flat=True).first() or 0
    from api_testing.models import APITestSuiteExecutionDetail
    return APITestSuiteExecutionDetail.objects.filter(execution_id=execution_id).values_list('total_cases', flat=True).first() or 0


def _reserve_next_link(execution_log: TaskExecutionLog) -> int | None:
    links = list(execution_log.linked_executions or [])
    for index, link in enumerate(links):
        state = link.get(SERIAL_PLAN_KEY)
        if state in {'queued', 'dispatching', 'dispatched'}:
            return None
        if state == 'finished':
            continue
        if state != 'ready':
            continue
        if not all(_execution_is_terminal(previous) for previous in links[:index]):
            return None
        link[SERIAL_PLAN_KEY] = 'queued'
        execution_log.linked_executions = links
        execution_log.save(update_fields=['linked_executions'])
        return int(link['execution_id'])
    return None


def _execution_is_terminal(link: dict[str, Any]) -> bool:
    status = _execution_status(link)
    return status is None or status in TERMINAL_STATUSES


def _execution_status(link: dict[str, Any]) -> str | None:
    if link.get('kind') == 'web':
        from web_testing.models import WebUITestExecution
        return WebUITestExecution.objects.filter(
            pk=link['execution_id'], project_id=link['project_id'],
        ).values_list('status', flat=True).first()
    if link.get('kind') == 'api':
        from api_testing.models import APITestExecution
        return APITestExecution.objects.filter(
            pk=link['execution_id'], project_id=link['project_id'],
        ).values_list('status', flat=True).first()
    return None


def _find_link(execution_log: TaskExecutionLog, execution_id: int) -> dict[str, Any] | None:
    return next((link for link in execution_log.linked_executions or [] if int(link.get('execution_id', 0)) == int(execution_id)), None)


def _save_links(execution_log: TaskExecutionLog) -> None:
    execution_log.save(update_fields=['linked_executions'])


def _enqueue_suite_dispatch(scheduled_log_id: int, execution_id: int) -> None:
    try:
        dispatch_scheduled_suite.delay(scheduled_log_id, execution_id)
    except Exception as exc:
        error = f'派发调度任务失败: {exc}'
        logger.error('%s log=%s execution=%s', error, scheduled_log_id, execution_id, exc_info=True)
        _record_dispatch_failure(scheduled_log_id, execution_id, error)


def _dispatch_prepared_suite(task: ScheduledTask, scheduled_log_id: int, link: dict[str, Any]) -> str:
    execution_id = int(link['execution_id'])
    if link['kind'] == 'web':
        from web_testing.tasks import execute_webui_test_suite_task
        result = execute_webui_test_suite_task.delay(execution_id, task.user_id, {}, scheduled_log_id)
    elif link['kind'] == 'api':
        if task.environment_id is None:
            raise ValueError('API 定时任务执行环境不存在')
        from api_testing.tasks import execute_api_test_suite_async
        result = execute_api_test_suite_async.delay(
            execution_id, int(link['suite_id']), task.environment_id, scheduled_log_id,
        )
    else:
        raise ValueError(f"不支持的测试类型: {link['kind']}")
    return result.id


def _set_execution_task_id(kind: str, execution_id: int, task_id: str) -> None:
    if kind == 'web':
        from web_testing.models import WebUITestExecution
        WebUITestExecution.objects.filter(pk=execution_id).update(task_id=task_id)
    else:
        from api_testing.models import APITestExecution
        APITestExecution.objects.filter(pk=execution_id).update(task_id=task_id)


def _record_dispatch_failure(scheduled_log_id: int, execution_id: int, error: str) -> None:
    with transaction.atomic():
        execution_log = TaskExecutionLog.objects.select_for_update().filter(pk=scheduled_log_id).first()
        if execution_log is None:
            return
        link = _find_link(execution_log, execution_id)
        if link is None:
            return
        link[SERIAL_PLAN_KEY] = 'failed'
        _save_links(execution_log)
        now = timezone.now()
        if link['kind'] == 'web':
            from web_testing.models import WebUITestExecution
            WebUITestExecution.objects.filter(pk=execution_id).update(
                status='error', error_message=error, end_time=now,
            )
        else:
            from api_testing.models import APITestExecution
            APITestExecution.objects.filter(pk=execution_id).update(
                status='error', error_message=error, end_time=now,
            )
    finish_scheduled_suite(scheduled_log_id, execution_id, append_log=error)


def _notify_finished_run(scheduled_log_id: int) -> None:
    """Send one completion notification; notification errors do not reopen a run."""
    with transaction.atomic():
        execution_log = TaskExecutionLog.objects.select_for_update().select_related('task').filter(pk=scheduled_log_id).first()
        if execution_log is None or execution_log.notification_sent_at is not None:
            return
        if execution_log.status in {'pending', 'running'}:
            return
        try:
            from notifications.services import trigger_notification
            sent = trigger_notification(
                scheduled_task_id=execution_log.task_id,
                execution_log=execution_log,
                result=None,
            )
        except Exception:
            logger.error('发送定时任务完成通知失败: log=%s', scheduled_log_id, exc_info=True)
            return
        if sent is True:
            execution_log.notification_sent_at = timezone.now()
            execution_log.save(update_fields=['notification_sent_at'])


def _failure_result(error: str) -> str:
    return json.dumps({'success': False, 'error': error}, ensure_ascii=False)
