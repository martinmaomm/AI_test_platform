"""Exercise the actual notifications 0011 -> 0012 migration on disposable SQLite."""

import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / "apps")]
    os.environ["DJANGO_SETTINGS_MODULE"] = "aits_backend.settings"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"

    def denied(_socket, _address):
        raise RuntimeError("External connections are forbidden during migration verification")

    with tempfile.TemporaryDirectory(prefix="aits-notification-migration-") as temp, patch.object(
        socket.socket, "connect", denied
    ), patch.object(socket.socket, "connect_ex", denied):
        from aits_backend import settings as config

        config.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(Path(temp) / "migration.sqlite3"),
            }
        }
        config.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
        config.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        config.CELERY_BROKER_URL = "memory://"
        config.CELERY_RESULT_BACKEND = "cache+memory://"
        config.LOGGING = {"version": 1, "disable_existing_loggers": True}

        import django

        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        previous = ("notifications", "0011_alter_notificationreceiver_options_and_more")
        target = ("notifications", "0012_email_only_notifications")
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        historical_apps = executor.loader.project_state([previous]).apps

        User = historical_apps.get_model("users", "User")
        Project = historical_apps.get_model("projects", "Project")
        Environment = historical_apps.get_model("projects", "Environment")
        ScheduledTask = historical_apps.get_model("scheduled_tasks", "ScheduledTask")
        Channel = historical_apps.get_model("notifications", "NotificationChannel")
        Receiver = historical_apps.get_model("notifications", "NotificationReceiver")

        user = User.objects.create(username="migration-user", email="migration@example.test")
        project = Project.objects.create(name="Notification migration project")
        environment = Environment.objects.create(
            name="Migration environment",
            category="api",
            config={},
        )
        email = Channel.objects.get(channel_code="email")
        dingtalk = Channel.objects.get(channel_code="dingtalk")
        wechat = Channel.objects.get(channel_code="wechat_work")
        email_receiver = Receiver.objects.create(
            project_id=project.pk,
            channel_id=email.pk,
            name="邮件接收组",
            target_address="mail@example.test",
            is_active=True,
        )
        dingtalk_receiver = Receiver.objects.create(
            project_id=project.pk,
            channel_id=dingtalk.pk,
            name="钉钉历史接收组",
            webhook_url="https://legacy.invalid/dingtalk-token",
            is_active=True,
        )
        wechat_receiver = Receiver.objects.create(
            project_id=project.pk,
            channel_id=wechat.pk,
            name="企微历史接收组",
            webhook_url="https://legacy.invalid/wechat-key",
            is_active=True,
        )
        task = ScheduledTask.objects.create(
            name="历史通知任务",
            suite_type="api",
            suite_ids=[1],
            cron_expression="0 9 * * *",
            environment_id=environment.pk,
            user_id=user.pk,
            project_id=project.pk,
        )
        task.notice_targets.add(dingtalk_receiver)

        executor = MigrationExecutor(connection)
        executor.migrate([target])
        migrated_apps = executor.loader.project_state([target]).apps
        Channel = migrated_apps.get_model("notifications", "NotificationChannel")
        Receiver = migrated_apps.get_model("notifications", "NotificationReceiver")
        ScheduledTask = migrated_apps.get_model("scheduled_tasks", "ScheduledTask")

        assert Channel.objects.get(pk=email.pk).is_active is True
        assert Channel.objects.get(pk=dingtalk.pk).is_active is False
        assert Channel.objects.get(pk=wechat.pk).is_active is False
        assert Receiver.objects.get(pk=email_receiver.pk).is_active is True
        migrated_dingtalk = Receiver.objects.get(pk=dingtalk_receiver.pk)
        migrated_wechat = Receiver.objects.get(pk=wechat_receiver.pk)
        assert migrated_dingtalk.is_active is False and migrated_wechat.is_active is False
        assert migrated_dingtalk.webhook_url == "https://legacy.invalid/dingtalk-token"
        assert migrated_wechat.webhook_url == "https://legacy.invalid/wechat-key"
        assert migrated_dingtalk.channel_id == dingtalk.pk
        assert list(ScheduledTask.objects.get(pk=task.pk).notice_targets.values_list("pk", flat=True)) == [dingtalk_receiver.pk]
        connection.close()

    print("PASS: actual notifications 0011->0012 SQLite migration preserves legacy rows and associations; only email remains active")


if __name__ == "__main__":
    main()
