# 任务内聚通知：notice_targets M2M 替代 notice_channel

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduled_tasks", "0005_add_notice_config"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="scheduledtask",
            name="notice_channel",
        ),
        migrations.AddField(
            model_name="scheduledtask",
            name="notice_targets",
            field=models.ManyToManyField(
                blank=True,
                help_text="接收执行结果通知的群组（企微/钉钉等）",
                related_name="scheduled_tasks",
                to="notifications.NotificationChannel",
                verbose_name="通知对象",
            ),
        ),
    ]
