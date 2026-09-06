"""Platform-native scheduled-report aggregation.

Scheduled reports are projections of the executions dispatched for one task
run.  They never run a suite a second time and only read durable execution
details, so a report remains useful after a source suite or case is removed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from django.utils import timezone


PLATFORM_REPORT_PATH = '/reports/detail/{execution_id}'
RUNNING_STATUSES = {'pending', 'running'}
ERROR_STATUSES = {'error', 'stopped'}


@dataclass
class ExecutionSummary:
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    skipped_cases: int = 0
    incomplete_cases: int = 0
    error_cases: int = 0
    execution_errors: int = 0
    is_running: bool = False

    def add(self, other: 'ExecutionSummary') -> None:
        for key in (
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases',
            'incomplete_cases', 'error_cases',
            'execution_errors',
        ):
            setattr(self, key, getattr(self, key) + getattr(other, key))
        self.is_running = self.is_running or other.is_running

    @property
    def report_status(self) -> str:
        if self.is_running:
            return 'running'
        if self.execution_errors or self.error_cases:
            return 'error'
        if self.failed_cases:
            return 'failed'
        if self.incomplete_cases:
            return 'incomplete'
        if not self.total_cases or self.skipped_cases == self.total_cases:
            return 'skipped'
        if self.passed_cases == self.total_cases:
            return 'passed'
        return 'completed'

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['report_status'] = self.report_status
        data['success_rate'] = round(self.passed_cases / self.total_cases * 100, 2) if self.total_cases else 0.0
        return data


def platform_report_url(execution_id: int) -> str:
    return PLATFORM_REPORT_PATH.format(execution_id=execution_id)


def append_linked_execution(execution_log, *, kind: str, project_id: int, execution_id: int, name: str) -> None:
    """Append one dispatch snapshot without reordering earlier suite runs."""
    if kind not in {'web', 'api'}:
        raise ValueError(f'Unsupported execution kind: {kind}')
    links = list(execution_log.linked_executions or [])
    links.append({
        'kind': kind,
        'project_id': int(project_id),
        'execution_id': int(execution_id),
        'name': str(name or ''),
    })
    execution_log.linked_executions = links
    execution_log.save(update_fields=['linked_executions'])


def _summarize_statuses(statuses: list[str], *, fallback_total: int, execution_status: str) -> ExecutionSummary:
    summary = ExecutionSummary()
    expected_total = max(int(fallback_total or 0), 0)
    summary.total_cases = max(expected_total, len(statuses))
    for status in statuses:
        if status == 'passed':
            summary.passed_cases += 1
        elif status == 'failed':
            summary.failed_cases += 1
        elif status == 'skipped':
            summary.skipped_cases += 1
        elif status == 'incomplete':
            summary.incomplete_cases += 1
        elif status in ERROR_STATUSES:
            summary.error_cases += 1
        else:
            summary.incomplete_cases += 1
    # A parent total is an execution snapshot.  Missing child rows are not
    # passes: the report explicitly surfaces them as not executed/unfinished.
    summary.incomplete_cases += max(expected_total - len(statuses), 0)
    if execution_status == 'failed' and not (summary.failed_cases or summary.error_cases):
        # A suite can fail after its recorded child cases all passed (for
        # example, a finalizer exception).  Preserve that execution-level
        # failure without inventing a business test case.
        summary.execution_errors += 1
    elif execution_status in ERROR_STATUSES:
        summary.execution_errors += 1
    # A terminal parent wins over stale child rows.  Those rows remain counted
    # as incomplete above, but must not keep the whole report running forever.
    summary.is_running = execution_status in RUNNING_STATUSES
    return summary


def _summarize_api_execution(execution_id: int, project_id: int) -> ExecutionSummary:
    from api_testing.models import APITestExecution

    execution = APITestExecution.objects.filter(id=execution_id, project_id=project_id).first()
    if execution is None:
        return ExecutionSummary(execution_errors=1)
    if execution.exec_type == 'suite':
        detail = getattr(execution, 'suite_execution_detail', None)
        if detail is not None:
            return _summarize_statuses(
                list(detail.case_executions.values_list('status', flat=True)),
                fallback_total=detail.total_cases,
                execution_status=execution.status,
            )
    detail = getattr(execution, 'case_execution_detail', None)
    statuses = [detail.status] if detail is not None else []
    return _summarize_statuses(statuses, fallback_total=1, execution_status=execution.status)


def _summarize_web_execution(execution_id: int, project_id: int) -> ExecutionSummary:
    from web_testing.models import WebUITestExecution

    execution = WebUITestExecution.objects.filter(id=execution_id, project_id=project_id).first()
    if execution is None:
        return ExecutionSummary(execution_errors=1)
    if execution.exec_type == 'suite':
        detail = getattr(execution, 'suite_execution_detail', None)
        if detail is not None:
            return _summarize_statuses(
                list(detail.case_executions.values_list('status', flat=True)),
                fallback_total=detail.total_cases,
                execution_status=execution.status,
            )
    detail = getattr(execution, 'case_execution_detail', None)
    statuses = [detail.status] if detail is not None else []
    return _summarize_statuses(statuses, fallback_total=1, execution_status=execution.status)


def summarize_linked_executions(execution_log) -> dict[str, Any]:
    """Read real executions in stored dispatch order and return report totals."""
    summary = ExecutionSummary()
    for link in execution_log.linked_executions or []:
        try:
            kind = link['kind']
            project_id = int(link['project_id'])
            execution_id = int(link['execution_id'])
            part = (
                _summarize_api_execution(execution_id, project_id)
                if kind == 'api'
                else _summarize_web_execution(execution_id, project_id)
                if kind == 'web'
                # A malformed link is a dispatch/reporting fault, not a
                # synthetic business test case.
                else ExecutionSummary(execution_errors=1)
            )
        except Exception:
            # An unavailable execution row/database read is an execution-level
            # report error, not an additional business test case.
            part = ExecutionSummary(execution_errors=1)
        summary.add(part)
    return summary.as_dict()


def refresh_execution_log_from_links(execution_log, *, append_log: str = '') -> dict[str, Any]:
    """Persist aggregate counters after a linked execution changes state.

    The return value lets callers decide whether this was the final execution
    and therefore whether a single notification may be sent.
    """
    summary = summarize_linked_executions(execution_log)
    execution_log.total_cases = summary['total_cases']
    execution_log.passed_cases = summary['passed_cases']
    execution_log.failed_cases = summary['failed_cases'] + summary['error_cases']
    execution_log.skipped_cases = summary['skipped_cases']
    if append_log:
        execution_log.step_log = '\n\n'.join(filter(None, [execution_log.step_log or '', append_log]))
    if summary['is_running']:
        execution_log.status = 'running'
        execution_log.end_time = None
    else:
        execution_log.status = 'success' if summary['report_status'] == 'passed' else 'failed'
        execution_log.end_time = timezone.now()
    errors = []
    if summary['incomplete_cases']:
        errors.append(f"验证未完成：本次有 {summary['incomplete_cases']} 个用例尚未完成。")
    if summary['execution_errors']:
        errors.append(f"本次有 {summary['execution_errors']} 个执行级异常。")
    execution_log.error_message = ' '.join(errors)
    if not execution_log.report_url:
        execution_log.report_url = platform_report_url(execution_log.id)
    execution_log.save()
    return summary


def mark_notification_sent(execution_log) -> None:
    """Persist the one-time report notification marker after a successful send."""
    if execution_log.notification_sent_at is None:
        execution_log.notification_sent_at = timezone.now()
        execution_log.save(update_fields=['notification_sent_at'])
