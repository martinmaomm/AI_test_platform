"""Non-destructive data-migration contract for retiring webhook notifications."""

from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from .models import NotificationChannel, NotificationReceiver


class EmailOnlyMigrationTests(TestCase):
    def test_migration_ensures_email_and_disables_legacy_rows_without_deleting_them(self):
        user = get_user_model().objects.create_user("migration-owner")
        project = Project.objects.create(name="Migration project", project_type="web", owner=user, created_by=user)
        legacy_channel = NotificationChannel.objects.create(channel_code="wechat_work", channel_name="企业微信")
        legacy_receiver = NotificationReceiver.objects.create(
            project=project,
            channel=legacy_channel,
            name="Legacy receiver",
            webhook_url="https://legacy.invalid/keep-history",
        )

        migration = import_module("notifications.migrations.0012_email_only_notifications")
        migration.retain_email_only(apps, None)

        email = NotificationChannel.objects.get(channel_code="email")
        legacy_channel.refresh_from_db()
        legacy_receiver.refresh_from_db()
        self.assertTrue(email.is_active)
        self.assertFalse(legacy_channel.is_active)
        self.assertFalse(legacy_receiver.is_active)
        self.assertEqual(legacy_receiver.webhook_url, "https://legacy.invalid/keep-history")
        self.assertEqual(legacy_receiver.channel_id, legacy_channel.pk)
