"""
定时任务执行结果通知：根据任务的 notice_targets 向各通知对象推送 Markdown 消息
"""
import logging
from typing import Optional

import requests

from .models import NotificationReceiver, EmailConfig

logger = logging.getLogger(__name__)


def trigger_notification(
    scheduled_task_id: int,
    execution_log,
    result: Optional[dict] = None,
) -> None:
    """
    定时任务执行结束后触发通知：从任务关联的 notice_targets（通知对象）读取渠道，
    按 trigger_condition 判断是否发送，组装 Markdown 推送到各渠道 webhook_url。
    单渠道失败不影响其他渠道与主流程。
    """
    logger.info("开始为任务 %s 发送通知", scheduled_task_id)
    try:
        from scheduled_tasks.models import TaskExecutionLog

        if getattr(execution_log, 'pk', None):
            try:
                execution_log = TaskExecutionLog.objects.select_related('task').get(pk=execution_log.pk)
            except Exception as e:
                logger.debug('trigger_notification 重查 TaskExecutionLog: %s', e)

        task = getattr(execution_log, 'task', None)
        if task is None:
            logger.warning("任务不存在，跳过通知: task_id=%s", scheduled_task_id)
            return

        trigger = getattr(task, 'trigger_condition', None) or 'always'
        if trigger == 'fail':
            passed = int(getattr(execution_log, 'passed_cases', 0) or (result or {}).get('passed_cases', 0))
            total = int(getattr(execution_log, 'total_cases', 0) or (result or {}).get('total_cases', 0))
            failed = int(getattr(execution_log, 'failed_cases', 0) or (result or {}).get('failed_cases', 0))
            if total and failed == 0 and passed == total:
                logger.info('任务配置为仅失败时通知，本次执行通过，跳过推送: task_id=%s', scheduled_task_id)
                return

        receivers = list(task.notice_targets.filter(is_active=True).select_related('channel'))
        if not receivers:
            logger.info('任务未配置通知对象或均为禁用: task_id=%s', scheduled_task_id)
            return

        total = int(getattr(execution_log, 'total_cases', 0) or (result or {}).get('total_cases', 0))
        passed = int(getattr(execution_log, 'passed_cases', 0) or (result or {}).get('passed_cases', 0))
        failed = int(getattr(execution_log, 'failed_cases', 0) or (result or {}).get('failed_cases', 0))
        skipped = int(getattr(execution_log, 'skipped_cases', 0) or (result or {}).get('skipped_cases', 0))
        start_time = getattr(execution_log, 'start_time', None)
        task_name = getattr(task, 'name', None) or f'任务#{scheduled_task_id}'
        start_str = start_time.strftime('%Y-%m-%d %H:%M:%S') if start_time and hasattr(start_time, 'strftime') else (str(start_time) if start_time else '-')
        success_rate = round((float(passed) / total * 100), 2) if total else 100.0
        conclusion_text = '测试通过' if failed == 0 else '测试未通过'

        markdown_lines = [
            '## 定时任务执行结果',
            f'**任务名称**: {task_name}',
            '**执行结果**: 已完成',
            f'**通过/失败/跳过/总计**: {passed} / {failed} / {skipped} / {total}',
            f'**成功率**: {success_rate}%',
            f'**测试结论**: {conclusion_text}',
            f'**开始时间**: {start_str}',
        ]
        execution_id = getattr(execution_log, 'pk', None) or getattr(execution_log, 'id', None)
        from django.conf import settings
        base_url = (getattr(settings, 'FRONTEND_BASE_URL', None) or getattr(settings, 'SITE_URL', 'http://127.0.0.1:5173')).rstrip('/')
        report_url = f"{base_url}/reports/detail/{execution_id}" if execution_id else ''
        if report_url:
            markdown_lines.append(f'[点击查看完整报告]({report_url})')
        markdown_text = '\n\n'.join(markdown_lines)

        for receiver in receivers:
            channel_type = getattr(receiver, 'channel_type', None) or (receiver.channel.channel_code if receiver.channel else 'dingtalk')
            if channel_type == 'email':
                try:
                    _send_email_to_channel(
                        channel=receiver,
                        task_name=task_name,
                        success_rate=success_rate,
                        passed=passed,
                        failed=failed,
                        skipped=skipped,
                        total=total,
                        start_str=start_str,
                        report_url=report_url,
                        execution_id=execution_id,
                    )
                except Exception as e:
                    logger.warning('单渠道发送异常（继续其他渠道）: channel=%s, error=%s', receiver.name, e, exc_info=True)
                continue
            url = (getattr(receiver, 'webhook_url', None) or '').strip()
            if not url:
                continue
            try:
                logger.info('Sending notification to "%s", webhook_url=%s', receiver.name, url)
                _send_to_channel(receiver, markdown_text, task_name)
            except Exception as e:
                logger.warning('单渠道发送异常（继续其他渠道）: channel=%s, error=%s', receiver.name, e, exc_info=True)

    except Exception as e:
        logger.warning('trigger_notification 执行异常（不影响主流程）: %s', e, exc_info=True)


def _send_to_channel(channel: NotificationReceiver, markdown_text: str, title: str) -> None:
    """向单个渠道发送 Markdown，失败仅打日志。"""
    url = (channel.webhook_url or '').strip()
    if not url:
        return
    try:
        channel_type = getattr(channel, 'channel_type', None) or 'dingtalk'
        if channel_type == 'wechat_work':
            payload = {
                'msgtype': 'markdown',
                'markdown': {'content': markdown_text},
            }
        else:
            payload = {
                'msgtype': 'markdown',
                'markdown': {'title': title or '定时任务执行结果', 'text': markdown_text},
            }
        logger.info("正在推送到 Webhook: %s", url)
        resp = requests.post(
            url,
            json=payload,
            timeout=10,
            headers={'Content-Type': 'application/json'},
        )
        logger.info(
            'Notification response: channel=%s, status_code=%s, body=%s',
            channel.name,
            resp.status_code,
            (resp.text or '')[:300],
        )
        if resp.status_code != 200:
            logger.warning(
                '通知渠道 %s 推送失败 status=%s body=%s',
                channel.name,
                resp.status_code,
                resp.text[:200],
            )
    except requests.RequestException as e:
        logger.warning('通知渠道 %s 请求异常: %s', channel.name, e)
    except Exception as e:
        logger.warning('通知渠道 %s 发送异常: %s', channel.name, e, exc_info=True)


def _send_email_to_channel(
    channel: NotificationReceiver,
    task_name: str,
    success_rate: float,
    passed: int,
    failed: int,
    skipped: int,
    total: int,
    start_str: str,
    report_url: str,
    execution_id,
) -> None:
    """向邮件渠道发送 HTML 报告摘要；从 EmailConfig 动态读取 SMTP 配置。"""
    from django.core.mail import get_connection, EmailMultiAlternatives

    addr_str = (getattr(channel, 'target_address', None) or '').strip()
    if not addr_str:
        logger.warning('邮件渠道 %s 未配置收件人，跳过', channel.name)
        return
    recipients = [s.strip() for s in addr_str.split(',') if s.strip()]
    if not recipients:
        logger.warning('邮件渠道 %s 收件人为空，跳过', channel.name)
        return

    config = EmailConfig.objects.filter(is_active=True).order_by('-updated_at').first()
    if not config:
        logger.warning('未配置邮件服务（系统设置-邮件服务配置），跳过邮件通知')
        return

    conn = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=config.smtp_server,
        port=config.port,
        username=config.sender_email,
        password=config.smtp_password,
        use_ssl=config.use_ssl,
        use_tls=not config.use_ssl,
        fail_silently=False,
    )

    subject = f'【AITS 自动化测试报告】任务: {task_name} 执行完毕 (成功率 {success_rate}%)'
    conclusion = '测试通过' if failed == 0 else '测试未通过'
    html_body = f'''
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 24px; color: #333; }}
  .card {{ background: #f8f9fa; border-radius: 8px; padding: 16px; max-width: 560px; }}
  .row {{ margin: 8px 0; }}
  .label {{ font-weight: 600; color: #666; }}
  .link {{ color: #1890ff; text-decoration: none; }}
  .link:hover {{ text-decoration: underline; }}
</style></head>
<body>
  <div class="card">
    <div class="row"><span class="label">任务名称：</span>{task_name}</div>
    <div class="row"><span class="label">通过 / 失败 / 跳过 / 总计：</span>{passed} / {failed} / {skipped} / {total}</div>
    <div class="row"><span class="label">成功率：</span>{success_rate}%</div>
    <div class="row"><span class="label">测试结论：</span>{conclusion}</div>
    <div class="row"><span class="label">开始时间：</span>{start_str}</div>
    <p style="margin-top: 16px;">请点击以下链接登录系统查看包含 Allure 饼图的详细报告：</p>
    <p><a class="link" href="{report_url}">{report_url}</a></p>
  </div>
</body>
</html>
'''
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=subject,
            from_email=config.sender_email,
            to=recipients,
            connection=conn,
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        logger.info('邮件已发送: channel=%s, recipients=%s', channel.name, len(recipients))
    except Exception as e:
        logger.warning('邮件渠道 %s 发送失败: %s', channel.name, e, exc_info=True)
        raise
