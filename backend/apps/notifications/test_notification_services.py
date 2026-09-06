"""Offline contract tests for scheduled-task notification orchestration."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from projects.models import Project
from scheduled_tasks.models import ScheduledTask, TaskExecutionLog

from .delivery import NotificationDeliveryError
from .models import NotificationChannel, NotificationReceiver
from .services import trigger_notification


class NotificationServiceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="notification-service-owner",
            password="test-password",
        )
        self.project = Project.objects.create(
            name="Notification project",
            project_type="api",
            owner=user,
            created_by=user,
        )
        self.other_project = Project.objects.create(
            name="Other notification project",
            project_type="api",
            owner=user,
            created_by=user,
        )
        with patch("scheduled_tasks.signals.register_periodic_task"):
            self.task = ScheduledTask.objects.create(
                name="AITS notification task",
                suite_type="api",
                suite_ids=[1],
                cron_expression="0 9 * * *",
                user=user,
                project=self.project,
            )
        self.execution_log = TaskExecutionLog.objects.create(
            task=self.task,
            start_time=timezone.now(),
            status="failed",
            total_cases=1,
            failed_cases=1,
        )

    def receiver(
        self,
        *,
        code="dingtalk",
        project=None,
        active=True,
        channel_active=True,
        **kwargs,
    ):
        channel, _ = NotificationChannel.objects.get_or_create(
            channel_code=code,
            defaults={"channel_name": code, "is_active": channel_active},
        )
        if channel.is_active != channel_active:
            channel.is_active = channel_active
            channel.save(update_fields=["is_active"])
        return NotificationReceiver.objects.create(
            project=project or self.project,
            channel=channel,
            name=f"{code} receiver",
            webhook_url="https://fixture.invalid/webhook",
            is_active=active,
            **kwargs,
        )

    def attach(self, *receivers):
        self.task.notice_targets.add(*receivers)

    @patch("notifications.services.send_webhook")
    def test_all_eligible_targets_succeed_and_markdown_has_aits_identifier(
        self, send_webhook
    ):
        receiver = self.receiver()
        self.attach(receiver)

        with self.assertLogs("notifications.services", level="INFO") as logs:
            sent = trigger_notification(self.task.id, self.execution_log)

        self.assertTrue(sent)
        self.assertTrue(any("通知发送成功" in line for line in logs.output))
        payload = send_webhook.call_args.args[2]
        self.assertIn("## AITS 定时任务执行结果", payload["markdown"]["text"])
        self.assertIn("通过/失败/错误/未完成/跳过/总计", payload["markdown"]["text"])

    @patch("notifications.services.send_webhook")
    def test_delivery_failure_does_not_stop_later_target_and_returns_false(
        self, send_webhook
    ):
        self.attach(self.receiver(), self.receiver(code="wechat_work"))
        send_webhook.side_effect = [
            NotificationDeliveryError("Webhook 网络请求失败"),
            None,
        ]

        with self.assertLogs("notifications.services", level="WARNING") as logs:
            sent = trigger_notification(self.task.id, self.execution_log)

        self.assertFalse(sent)
        self.assertEqual(send_webhook.call_count, 2)
        self.assertTrue(
            any("reason=Webhook 网络请求失败" in line for line in logs.output)
        )

    @patch(
        "notifications.services._notification_summary",
        side_effect=RuntimeError("private transport detail"),
    )
    def test_unexpected_root_error_logs_only_exception_type(self, _summary):
        with self.assertLogs("notifications.services", level="WARNING") as logs:
            sent = trigger_notification(self.task.id, self.execution_log)

        self.assertFalse(sent)
        self.assertTrue(any("error_type=RuntimeError" in line for line in logs.output))
        self.assertFalse(
            any("private transport detail" in line for line in logs.output)
        )

    @patch("notifications.services.send_webhook")
    def test_task_identifier_must_match_execution_log_task(self, send_webhook):
        self.attach(self.receiver())

        sent = trigger_notification(self.task.id + 1, self.execution_log)

        self.assertFalse(sent)
        send_webhook.assert_not_called()

    @patch("notifications.services.send_webhook")
    def test_running_summary_is_never_sent_even_for_always_trigger(self, send_webhook):
        self.attach(self.receiver())
        TaskExecutionLog.objects.filter(pk=self.execution_log.pk).update(
            status="running"
        )
        self.execution_log.refresh_from_db()

        sent = trigger_notification(self.task.id, self.execution_log)

        self.assertFalse(sent)
        send_webhook.assert_not_called()

    @patch(
        "notifications.services._notification_summary",
        return_value={
            "is_running": True,
            "report_status": "failed",
        },
    )
    @patch("notifications.services.send_webhook")
    def test_is_running_summary_is_never_sent_when_parent_status_is_stale(
        self, send_webhook, _summary
    ):
        self.attach(self.receiver())

        sent = trigger_notification(self.task.id, self.execution_log)

        self.assertFalse(sent)
        send_webhook.assert_not_called()

    @patch("notifications.services.send_webhook")
    def test_disabled_and_cross_project_targets_are_skipped_without_blocking_eligible_target(
        self, send_webhook
    ):
        eligible = self.receiver()
        disabled_channel = self.receiver(code="wechat_work", channel_active=False)
        cross_project = self.receiver(project=self.other_project)
        disabled_receiver = self.receiver(active=False)
        self.attach(eligible, disabled_channel, cross_project, disabled_receiver)

        sent = trigger_notification(self.task.id, self.execution_log)

        self.assertTrue(sent)
        send_webhook.assert_called_once()

    @patch("notifications.services.send_webhook")
    def test_fail_trigger_uses_unlinked_parent_status_not_counter_inference(
        self, send_webhook
    ):
        receiver = self.receiver()
        self.attach(receiver)
        self.task.trigger_condition = "fail"
        self.task.save(update_fields=["trigger_condition"])

        cases = (
            ("success", 1, 1, 0, "", None, False, "passed"),
            ("success", 2, 1, 1, "", None, True, "failed"),
            ("failed", 1, 0, 1, "", None, True, "failed"),
            ("failed", 1, 1, 0, "验证未完成：fixture", None, True, "incomplete"),
            (
                "failed",
                0,
                0,
                0,
                "",
                {"success": False, "error": "fixture"},
                True,
                "error",
            ),
            ("error", 0, 0, 0, "", None, True, "error"),
            ("running", 1, 0, 0, "", None, False, "running"),
        )
        for (
            status,
            total,
            passed,
            failed,
            error_message,
            result,
            expected_sent,
            expected_status,
        ) in cases:
            with self.subTest(status=expected_status):
                TaskExecutionLog.objects.filter(pk=self.execution_log.pk).update(
                    status=status,
                    total_cases=total,
                    passed_cases=passed,
                    failed_cases=failed,
                    error_message=error_message,
                    result_log=json.dumps(result) if result else "",
                    linked_executions=[],
                )
                self.execution_log.refresh_from_db()
                before = send_webhook.call_count

                sent = trigger_notification(self.task.id, self.execution_log)

                self.assertEqual(sent, expected_sent)
                self.assertEqual(send_webhook.call_count - before, int(expected_sent))
                if expected_sent:
                    markdown = send_webhook.call_args.args[2]["markdown"]["text"]
                    self.assertIn(
                        {
                            "failed": "测试未通过",
                            "incomplete": "验证未完成",
                            "error": "执行错误",
                        }[expected_status],
                        markdown,
                    )

    @override_settings(FRONTEND_BASE_URL='https://console.example.test/?next="<unsafe>')
    @patch("notifications.services.send_email")
    @patch(
        "notifications.services.parse_recipients",
        return_value=["first@example.test", "second@example.test"],
    )
    def test_email_uses_shared_recipient_parser_and_escapes_html(
        self, parse_recipients, send_email
    ):
        receiver = self.receiver(
            code="email",
            target_address="first@example.test;second@example.test",
        )
        self.attach(receiver)
        ScheduledTask.objects.filter(pk=self.task.pk).update(
            name="AITS <task> & report"
        )

        sent = trigger_notification(self.task.id, self.execution_log)

        self.assertTrue(sent)
        parse_recipients.assert_called_once_with(
            "first@example.test;second@example.test"
        )
        html_body = send_email.call_args.kwargs["html_body"]
        self.assertIn("AITS &lt;task&gt; &amp; report", html_body)
        self.assertNotIn("AITS <task> & report", html_body)
        self.assertIn("&quot;&lt;unsafe&gt;/reports/detail/", html_body)
