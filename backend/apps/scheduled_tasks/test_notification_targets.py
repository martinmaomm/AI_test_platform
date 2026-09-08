"""Scheduled-task selection guards for email-only notification receivers."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from notifications.models import NotificationChannel, NotificationReceiver

from .serializers import ScheduledTaskCreateSerializer


class ScheduledTaskNotificationTargetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("schedule-email-owner")
        self.project = Project.objects.create(
            name="Schedule email project", project_type="web", owner=self.user, created_by=self.user
        )
        self.email = NotificationChannel.objects.create(channel_code="email", channel_name="邮件")
        self.legacy = NotificationChannel.objects.create(channel_code="dingtalk", channel_name="钉钉")

    def receiver(self, channel, *, active=True):
        return NotificationReceiver.objects.create(
            project=self.project,
            channel=channel,
            name=f"{channel.channel_code} receiver",
            target_address="one@example.test",
            is_active=active,
        )

    def serializer_for(self, receiver):
        return ScheduledTaskCreateSerializer(
            data={
                "name": "Email schedule",
                "suite_ids": [1],
                "cron_expression": "0 9 * * *",
                "notice_targets": [receiver.pk],
            },
            context={"project": self.project},
        )

    def test_only_active_email_receivers_can_be_selected(self):
        cases = (
            (self.receiver(self.email), True),
            (self.receiver(self.legacy), False),
            (self.receiver(self.email, active=False), False),
        )

        for receiver, expected in cases:
            with self.subTest(receiver=receiver.pk):
                serializer = self.serializer_for(receiver)
                with patch("scheduled_tasks.serializers.validate_suites"):
                    self.assertEqual(serializer.is_valid(), expected, serializer.errors)
                if not expected:
                    self.assertIn("notice_targets", serializer.errors)

    def test_disabled_email_channel_cannot_be_selected(self):
        receiver = self.receiver(self.email)
        self.email.is_active = False
        self.email.save(update_fields=["is_active"])

        serializer = self.serializer_for(receiver)
        with patch("scheduled_tasks.serializers.validate_suites"):
            self.assertFalse(serializer.is_valid())
        self.assertIn("notice_targets", serializer.errors)

    def test_receiver_with_invalid_address_cannot_be_selected(self):
        receiver = self.receiver(self.email)
        receiver.target_address = "not-an-email"
        receiver.save(update_fields=["target_address"])

        serializer = self.serializer_for(receiver)
        with patch("scheduled_tasks.serializers.validate_suites"):
            self.assertFalse(serializer.is_valid())
        self.assertIn("notice_targets", serializer.errors)
