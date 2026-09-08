"""Offline completion-notification contracts for email-only delivery."""

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
        user = get_user_model().objects.create_user("notification-service-owner")
        self.project = Project.objects.create(
            name="Notification project", project_type="api", owner=user, created_by=user
        )
        self.other_project = Project.objects.create(
            name="Other notification project", project_type="api", owner=user, created_by=user
        )
        self.email = NotificationChannel.objects.create(channel_code="email", channel_name="邮件")
        self.legacy = NotificationChannel.objects.create(channel_code="dingtalk", channel_name="钉钉")
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

    def receiver(self, *, project=None, active=True, channel=None, address="one@example.test"):
        return NotificationReceiver.objects.create(
            project=project or self.project,
            channel=channel or self.email,
            name="mail receiver",
            target_address=address,
            is_active=active,
        )

    @patch("notifications.services.send_email")
    @override_settings(FRONTEND_BASE_URL="https://reports.example.test")
    def test_email_target_receives_report_content(self, send_email):
        receiver = self.receiver()
        self.task.notice_targets.add(receiver)

        self.assertTrue(trigger_notification(self.task.id, self.execution_log))

        subject, body, recipients = send_email.call_args.args
        self.assertIn("AITS 自动化测试报告", subject)
        self.assertIn("## AITS 定时任务执行结果", body)
        self.assertEqual(recipients, ["one@example.test"])
        self.assertIn("reports/detail", send_email.call_args.kwargs["html_body"])

    @patch("notifications.services.send_email")
    def test_old_non_email_targets_never_send(self, send_email):
        self.task.notice_targets.add(self.receiver(channel=self.legacy))

        self.assertFalse(trigger_notification(self.task.id, self.execution_log))

        send_email.assert_not_called()

    @patch("notifications.services.send_email")
    def test_disabled_or_cross_project_receivers_are_not_targets(self, send_email):
        active = self.receiver()
        disabled = self.receiver(active=False)
        cross_project = self.receiver(project=self.other_project)
        self.task.notice_targets.add(active, disabled, cross_project)

        self.assertTrue(trigger_notification(self.task.id, self.execution_log))

        send_email.assert_called_once()

    @patch("notifications.services.send_email")
    def test_disabled_email_channel_never_sends(self, send_email):
        receiver = self.receiver()
        self.task.notice_targets.add(receiver)
        self.email.is_active = False
        self.email.save(update_fields=["is_active"])

        self.assertFalse(trigger_notification(self.task.id, self.execution_log))

        send_email.assert_not_called()

    @patch("notifications.services.send_email")
    def test_failed_email_does_not_stop_later_receiver_and_returns_false(self, send_email):
        self.task.notice_targets.add(self.receiver(), self.receiver(address="two@example.test"))
        send_email.side_effect = [
            NotificationDeliveryError("SMTP 服务器拒绝收件人"),
            None,
        ]

        self.assertFalse(trigger_notification(self.task.id, self.execution_log))

        self.assertEqual(send_email.call_count, 2)

    @patch("notifications.services.send_email")
    def test_fail_trigger_does_not_send_a_passed_run(self, send_email):
        self.task.trigger_condition = "fail"
        self.task.save(update_fields=["trigger_condition"])
        self.execution_log.status = "success"
        self.execution_log.failed_cases = 0
        self.execution_log.passed_cases = 1
        self.execution_log.save(update_fields=["status", "failed_cases", "passed_cases"])
        self.task.notice_targets.add(self.receiver())

        self.assertFalse(trigger_notification(self.task.id, self.execution_log))

        send_email.assert_not_called()

    @patch("notifications.services.send_email")
    @override_settings(FRONTEND_BASE_URL="https://ui.example.test")
    def test_fail_trigger_sends_incomplete_report_with_ui_and_api_report_link(self, send_email):
        self.task.trigger_condition = "fail"
        self.task.save(update_fields=["trigger_condition"])
        self.execution_log.total_cases = 2
        self.execution_log.passed_cases = 1
        self.execution_log.failed_cases = 0
        self.execution_log.status = "failed"
        self.execution_log.save(update_fields=["total_cases", "passed_cases", "failed_cases", "status"])
        self.task.notice_targets.add(self.receiver())

        self.assertTrue(trigger_notification(self.task.id, self.execution_log))

        subject, body, recipients = send_email.call_args.args
        self.assertIn("AITS 自动化测试报告", subject)
        self.assertIn("验证未完成", body)
        self.assertEqual(recipients, ["one@example.test"])
        self.assertIn("https://ui.example.test/reports/detail/", send_email.call_args.kwargs["html_body"])

    @patch("notifications.services.send_email")
    def test_running_run_never_sends(self, send_email):
        self.task.notice_targets.add(self.receiver())
        self.execution_log.status = "running"
        self.execution_log.save(update_fields=["status"])

        self.assertFalse(trigger_notification(self.task.id, self.execution_log))

        send_email.assert_not_called()
