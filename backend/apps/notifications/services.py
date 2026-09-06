"""Completion notification orchestration for scheduled task runs.

Transport validation and I/O belong to :mod:`notifications.delivery`.  This
module decides which task-scoped receivers should be notified and produces a
safe, report-contract-compatible message for them.
"""

from __future__ import annotations

import json
import logging
from html import escape
from typing import Any, Optional

from django.conf import settings

from .delivery import (
    NotificationDeliveryError,
    parse_recipients,
    send_email,
    send_webhook,
)

logger = logging.getLogger(__name__)

_FAILURE_REPORT_STATUSES = {"failed", "error", "incomplete"}
_RUNNING_STATUSES = {"pending", "running"}


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _result_payload(execution_log, result: Optional[dict]) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    raw_result = getattr(execution_log, "result_log", None)
    if not isinstance(raw_result, str):
        return {}
    try:
        parsed = json.loads(raw_result)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fallback_summary(execution_log, result: Optional[dict]) -> dict[str, Any]:
    """Classify an unlinked terminal log without treating it as a pass."""
    total = _as_non_negative_int(getattr(execution_log, "total_cases", 0))
    passed = _as_non_negative_int(getattr(execution_log, "passed_cases", 0))
    failed = _as_non_negative_int(getattr(execution_log, "failed_cases", 0))
    skipped = _as_non_negative_int(getattr(execution_log, "skipped_cases", 0))
    incomplete = max(total - passed - failed - skipped, 0)
    payload = _result_payload(execution_log, result)
    status = str(getattr(execution_log, "status", "") or "").lower()
    error_message = str(getattr(execution_log, "error_message", "") or "")
    explicit_error = payload.get("success") is False and bool(payload.get("error"))

    if status in _RUNNING_STATUSES:
        report_status = "running"
    elif status == "error":
        report_status = "error"
    elif status == "success":
        if failed:
            report_status = "failed"
        elif total and passed == total:
            report_status = "passed"
        else:
            report_status = "skipped" if not total else "incomplete"
    elif status == "failed":
        if (
            explicit_error
            or "执行级异常" in error_message
            or "预检失败" in error_message
        ):
            report_status = "error"
        elif failed:
            report_status = "failed"
        elif incomplete or "验证未完成" in error_message:
            report_status = "incomplete"
        else:
            # A failed parent with no recorded business-case failure is an
            # execution-level error, never an inferred successful run.
            report_status = "error"
    elif status == "cancelled":
        report_status = "incomplete"
    else:
        report_status = "incomplete"

    return {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "skipped_cases": skipped,
        "incomplete_cases": incomplete,
        "error_cases": 0,
        "execution_errors": int(report_status == "error"),
        "is_running": report_status == "running",
        "report_status": report_status,
        "success_rate": round(passed / total * 100, 2) if total else 0.0,
    }


def _notification_summary(execution_log, result: Optional[dict]) -> dict[str, Any]:
    if getattr(execution_log, "linked_executions", None):
        from scheduled_tasks.reporting import summarize_linked_executions

        return summarize_linked_executions(execution_log)
    return _fallback_summary(execution_log, result)


def _report_url(execution_log) -> str:
    execution_id = getattr(execution_log, "pk", None) or getattr(
        execution_log, "id", None
    )
    if not execution_id:
        return ""
    base_url = (
        getattr(settings, "FRONTEND_BASE_URL", None)
        or getattr(settings, "SITE_URL", "http://127.0.0.1:5173")
    ).rstrip("/")
    return f"{base_url}/reports/detail/{execution_id}"


def _webhook_payload(
    channel_code: str, markdown_text: str, title: str
) -> dict[str, Any]:
    if channel_code == "wechat_work":
        return {"msgtype": "markdown", "markdown": {"content": markdown_text}}
    return {
        "msgtype": "markdown",
        "markdown": {"title": title or "定时任务执行结果", "text": markdown_text},
    }


def _message_content(
    execution_log, scheduled_task_id: int, summary: dict[str, Any]
) -> tuple[str, str, str]:
    task = execution_log.task
    task_name = str(getattr(task, "name", "") or f"任务#{scheduled_task_id}")
    start_time = getattr(execution_log, "start_time", None)
    start_text = (
        start_time.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(start_time, "strftime")
        else "-"
    )
    report_url = _report_url(execution_log)
    conclusion = {
        "passed": "测试通过",
        "failed": "测试未通过",
        "error": "执行错误",
        "incomplete": "验证未完成",
        "skipped": "未执行或已跳过",
        "running": "执行中",
    }.get(summary["report_status"], "验证未完成")

    lines = [
        "## AITS 定时任务执行结果",
        f"**任务名称**: {task_name}",
        f"**执行结果**: {conclusion}",
        "**通过/失败/错误/未完成/跳过/总计**: "
        f"{summary['passed_cases']} / {summary['failed_cases']} / {summary['error_cases']} / "
        f"{summary['incomplete_cases']} / {summary['skipped_cases']} / {summary['total_cases']}",
        f"**成功率**: {summary['success_rate']}%",
        f"**开始时间**: {start_text}",
    ]
    if report_url:
        lines.append(f"[点击查看完整报告]({report_url})")
    markdown_text = "\n\n".join(lines)

    escaped_task_name = escape(task_name)
    escaped_conclusion = escape(conclusion)
    escaped_start = escape(start_text)
    escaped_report_url = escape(report_url, quote=True)
    report_link = (
        f'<p><a href="{escaped_report_url}">查看完整报告</a></p>' if report_url else ""
    )
    html_body = f"""<!DOCTYPE html>
<html><body>
  <div>
    <p><strong>任务名称：</strong>{escaped_task_name}</p>
    <p><strong>执行结果：</strong>{escaped_conclusion}</p>
    <p><strong>通过 / 失败 / 错误 / 未完成 / 跳过 / 总计：</strong>
      {summary['passed_cases']} / {summary['failed_cases']} / {summary['error_cases']} / {summary['incomplete_cases']} / {summary['skipped_cases']} / {summary['total_cases']}</p>
    <p><strong>成功率：</strong>{summary['success_rate']}%</p>
    <p><strong>开始时间：</strong>{escaped_start}</p>
    {report_link}
  </div>
</body></html>"""
    return task_name, markdown_text, html_body


def trigger_notification(
    scheduled_task_id: int,
    execution_log,
    result: Optional[dict] = None,
) -> bool:
    """Send all applicable completion notifications.

    ``True`` means every receiver that should be notified was delivered
    successfully. Skipped, inactive, and cross-project receivers are not
    targets. A single failed target never prevents later target deliveries.
    """
    try:
        from scheduled_tasks.models import TaskExecutionLog

        if getattr(execution_log, "pk", None):
            execution_log = TaskExecutionLog.objects.select_related("task").get(
                pk=execution_log.pk
            )
        task = getattr(execution_log, "task", None)
        if task is None:
            logger.warning("任务不存在，跳过通知: task_id=%s", scheduled_task_id)
            return False
        if task.pk != scheduled_task_id:
            logger.warning(
                "任务标识不匹配，跳过通知: task_id=%s log_id=%s",
                scheduled_task_id,
                execution_log.pk,
            )
            return False

        summary = _notification_summary(execution_log, result)
        if summary["is_running"] or summary["report_status"] == "running":
            logger.info("任务执行尚未结束，跳过通知: task_id=%s", scheduled_task_id)
            return False
        trigger = getattr(task, "trigger_condition", None) or "always"
        if (
            trigger == "fail"
            and summary["report_status"] not in _FAILURE_REPORT_STATUSES
        ):
            logger.info("本次执行不满足失败通知条件: task_id=%s", scheduled_task_id)
            return False

        task_name, markdown_text, html_body = _message_content(
            execution_log, scheduled_task_id, summary
        )
        receivers = task.notice_targets.filter(is_active=True).select_related("channel")
        attempted = False
        all_succeeded = True
        for receiver in receivers:
            channel = getattr(receiver, "channel", None)
            if (
                receiver.project_id != task.project_id
                or channel is None
                or not channel.is_active
            ):
                continue
            channel_code = str(channel.channel_code or "").strip()
            if not channel_code:
                continue
            attempted = True
            try:
                if channel_code == "email":
                    recipients = parse_recipients(receiver.target_address)
                    subject = f"【AITS 自动化测试报告】任务: {task_name} 执行完毕"
                    send_email(subject, markdown_text, recipients, html_body=html_body)
                else:
                    send_webhook(
                        channel_code,
                        receiver.webhook_url,
                        _webhook_payload(channel_code, markdown_text, task_name),
                    )
                logger.info(
                    "通知发送成功: task_id=%s log_id=%s receiver_id=%s channel=%s",
                    scheduled_task_id,
                    execution_log.pk,
                    receiver.pk,
                    channel_code,
                )
            except NotificationDeliveryError as exc:
                all_succeeded = False
                logger.warning(
                    "通知发送失败: task_id=%s log_id=%s receiver_id=%s channel=%s reason=%s",
                    scheduled_task_id,
                    execution_log.pk,
                    receiver.pk,
                    channel_code,
                    str(exc),
                )
            except Exception as exc:
                all_succeeded = False
                logger.warning(
                    "通知发送发生未预期错误: task_id=%s log_id=%s receiver_id=%s channel=%s error_type=%s",
                    scheduled_task_id,
                    execution_log.pk,
                    receiver.pk,
                    channel_code,
                    type(exc).__name__,
                )

        if not attempted:
            logger.info("没有可发送的通知目标: task_id=%s", scheduled_task_id)
            return False
        return all_succeeded
    except Exception as exc:
        logger.warning(
            "通知触发处理失败: task_id=%s error_type=%s",
            scheduled_task_id,
            type(exc).__name__,
        )
        return False
