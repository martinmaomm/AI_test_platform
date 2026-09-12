"""Single sequential requests execution path for cases, suites and schedules."""
import json
import logging

from django.utils import timezone

from .models import APITestExecution
from .requests_runner import requests_runner

logger = logging.getLogger(__name__)


def _persist_detail(detail, result):
    detail.status = result.get('status') or ('passed' if result.get('success') else 'failed')
    if result.get('error_type') == 'Cancelled':
        detail.status = 'skipped'
    elif detail.status not in {'passed', 'failed', 'error'}:
        detail.status = 'error'
    detail.end_time = timezone.now()
    detail.duration = max(0, (detail.end_time - detail.start_time).total_seconds())
    detail.error_message = result.get('error') or ''
    detail.log = result.get('log') or ''
    # Existing report storage column; contents are now requests-native structured results.
    detail.httprunner_result = json.dumps(result, ensure_ascii=False)
    detail.save()


def run_execution(execution_id, *, suite=False, scheduled_log_id=None, progress=None):
    """Consume only frozen inputs. Never re-read live scripts, suite order or environment."""
    execution = None
    claimed = False
    try:
        started = timezone.now()
        claimed = bool(APITestExecution.objects.filter(pk=execution_id, status='pending').update(
            status='running', start_time=started, progress=10,
        ))
        execution = APITestExecution.objects.get(pk=execution_id)
        if not claimed:
            return {'success': False, 'status': 'completed', 'execution_id': execution_id,
                    'execution_status': execution.status, 'message': '执行已被领取或已结束，未重复发送请求'}
        snapshot = execution.input_snapshot or {}
        cases = snapshot.get('cases') or []
        if snapshot.get('version') != 1 or not cases or snapshot.get('kind') != ('suite' if suite else 'case'):
            raise ValueError('执行输入快照缺失或不匹配，请重新发起执行')
        suite_detail = execution.suite_execution_detail if suite else None
        if suite_detail:
            suite_detail.start_time = started
            suite_detail.save(update_fields=['start_time'])
        logs, results = [], []
        for index, item in enumerate(cases):
            detail = (suite_detail.case_executions.get(pk=item['detail_id']) if suite
                      else execution.case_execution_detail)
            if detail.pk != item['detail_id']:
                raise ValueError('执行快照与记录不匹配')
            execution.refresh_from_db(fields=['status'])
            if execution.status == 'stopped':
                detail.status = 'skipped'
                detail.error_message = '用户已停止执行'
                detail.save(update_fields=['status', 'error_message'])
                continue
            detail.start_time, detail.status = timezone.now(), 'running'
            detail.save(update_fields=['start_time', 'status'])
            if progress:
                progress(10 + int(index / len(cases) * 80), f"正在执行 {index + 1}/{len(cases)}：{item['name']}")
            try:
                options = item.get('options') or {}

                def checkpoint(partial):
                    # Persist evidence before the next request. A cancel or hard
                    # deadline must not erase a previously completed operation.
                    steps = partial.get('step_datas') or []
                    detail.httprunner_result = json.dumps(partial, ensure_ascii=False)
                    detail.log = partial.get('log') or ''
                    detail.save(update_fields=['httprunner_result', 'log'])
                    if not suite:
                        checkpoint_data = partial.get('checkpoint') or {}
                        completed = checkpoint_data.get('completed_steps')
                        total = checkpoint_data.get('total_steps')
                        progress_fields = {}
                        if isinstance(completed, int) and isinstance(total, int) and total > 0:
                            progress_fields['progress'] = min(99, int(completed / total * 100))
                        APITestExecution.objects.filter(pk=execution.pk, status='running').update(
                            execution_log=detail.log, total_steps=len(steps),
                            success_steps=sum(step.get('status') == 'passed' for step in steps),
                            failure_steps=sum(step.get('status') == 'failed' for step in steps),
                            error_steps=sum(step.get('status') == 'error' for step in steps),
                            **progress_fields,
                        )

                result = requests_runner(
                    script_id=str(item['case_id']), script_content=item['script'],
                    base_url=options.get('base_url') or None, options=options,
                    on_progress=checkpoint,
                    should_cancel=lambda: not APITestExecution.objects.filter(
                        pk=execution.pk, status='running',
                    ).exists(),
                )
            except Exception as exc:
                result = {'success': False, 'error': str(exc), 'error_type': type(exc).__name__,
                          'log': f'执行错误：{exc}', 'step_datas': []}
            _persist_detail(detail, result)
            results.append(result)
            logs.append(f"用例：{item['name']}\n{detail.log}\n")
        execution.refresh_from_db(fields=['status'])
        stopped = execution.status == 'stopped'
        execution.end_time = timezone.now()
        execution.duration = max(0, (execution.end_time - started).total_seconds())
        execution.execution_log = '\n'.join(logs)
        execution.progress = 100
        if suite:
            details = list(suite_detail.case_executions.all())
            passed = sum(detail.status == 'passed' for detail in details)
            skipped = sum(detail.status == 'skipped' for detail in details)
            errors = sum(detail.status == 'error' for detail in details)
            failed = len(details) - passed - skipped
            suite_detail.total_cases, suite_detail.passed_cases = len(details), passed
            suite_detail.failed_cases, suite_detail.skipped_cases = failed, skipped
            suite_detail.end_time, suite_detail.duration = execution.end_time, execution.duration
            suite_detail.log = execution.execution_log
            suite_detail.save()
            execution.total_steps, execution.success_steps = len(details), passed
            execution.failure_steps, execution.error_steps = failed - errors, errors
            if not stopped:
                execution.status = 'error' if errors else ('failed' if failed or skipped else 'passed')
        else:
            result = results[0] if results else {}
            steps = result.get('step_datas') or []
            execution.total_steps = len(steps)
            execution.success_steps = sum(step.get('status') == 'passed' or step.get('success') is True for step in steps)
            execution.failure_steps = sum(step.get('status') == 'failed' for step in steps)
            execution.error_steps = sum(step.get('status') == 'error' for step in steps)
            if not stopped:
                execution.status = execution.case_execution_detail.status
        execution.error_message = next((result.get('error') for result in results if result.get('error')), '')
        execution.save()
        return {
            'success': execution.status == 'passed', 'status': 'completed',
            'execution_id': execution.pk, 'execution_status': execution.status,
            'message': '执行完成', 'duration': execution.duration, 'user_id': execution.executor_id,
            'total_cases': len(cases), 'passed_cases': sum(result.get('success') is True for result in results),
            'failed_cases': sum(not result.get('success') for result in results),
            'skipped_cases': len(cases) - len(results),
        }
    except Exception as exc:
        logger.exception('API 执行失败 execution=%s', execution_id)
        if execution and claimed:
            finished = timezone.now()
            execution.status, execution.end_time = 'error', finished
            execution.duration = max(0, (finished - (execution.start_time or finished)).total_seconds())
            execution.error_message, execution.execution_log = str(exc), f'执行错误：{exc}'
            execution.error_steps, execution.progress = 1, 100
            execution.save()
            if suite:
                detail = execution.suite_execution_detail
                detail.case_executions.filter(status__in=['pending', 'running']).update(
                    status='error', error_message=str(exc), end_time=finished,
                )
                detail.failed_cases = detail.case_executions.filter(status__in=['failed', 'error']).count()
                detail.end_time, detail.duration, detail.log = finished, execution.duration, execution.execution_log
                detail.save()
            else:
                detail = execution.case_execution_detail
                detail.start_time = detail.start_time or execution.start_time
                _persist_detail(detail, {'success': False, 'error': str(exc), 'error_type': type(exc).__name__, 'log': str(exc)})
        return {'success': False, 'status': 'completed', 'execution_id': execution_id,
                'execution_status': 'error', 'message': str(exc)}
    finally:
        if scheduled_log_id and (claimed or execution is None):
            try:
                from scheduled_tasks.scheduling import finish_scheduled_suite
                finish_scheduled_suite(scheduled_log_id, execution_id,
                                       append_log=(execution.execution_log or '') if execution else '执行记录不存在')
            except Exception:
                logger.exception('推进定时套件失败 log=%s execution=%s', scheduled_log_id, execution_id)
