# 定时任务通知配置：渠道与触发条件

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduled_tasks", "0004_allure_report_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledtask",
            name="notice_channel",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="渠道类型列表，如 ['wechat_work','dingtalk']，空表示使用规则默认渠道",
                verbose_name="通知渠道",
            ),
        ),
        migrations.AddField(
            model_name="scheduledtask",
            name="trigger_condition",
            field=models.CharField(
                choices=[("always", "始终通知"), ("fail", "仅失败时通知")],
                default="always",
                max_length=20,
                verbose_name="触发条件",
            ),
        ),
    ]
