"""Retire webhook notification delivery without deleting historical rows."""

from django.db import migrations


EMAIL_CODE = "email"


def retain_email_only(apps, schema_editor):
    NotificationChannel = apps.get_model("notifications", "NotificationChannel")
    NotificationReceiver = apps.get_model("notifications", "NotificationReceiver")

    email_channel, _ = NotificationChannel.objects.get_or_create(
        channel_code=EMAIL_CODE,
        defaults={
            "channel_name": "邮件",
            "description": "填写收件人邮箱；需先配置启用的 SMTP 邮件服务。",
            "is_active": True,
        },
    )
    NotificationChannel.objects.exclude(pk=email_channel.pk).update(is_active=False)
    NotificationReceiver.objects.exclude(channel_id=email_channel.pk).update(is_active=False)


def noop(apps, schema_editor):
    """This retirement is intentionally non-destructive and has no reverse write."""


class Migration(migrations.Migration):
    dependencies = [("notifications", "0011_alter_notificationreceiver_options_and_more")]

    operations = [
        migrations.RunPython(retain_email_only, noop),
        migrations.AlterModelOptions(
            name="emailconfig",
            options={
                "db_table": "notifications_email_config",
                "ordering": ["-updated_at", "-pk"],
                "verbose_name": "邮件服务配置",
                "verbose_name_plural": "邮件服务配置",
            },
        ),
    ]
