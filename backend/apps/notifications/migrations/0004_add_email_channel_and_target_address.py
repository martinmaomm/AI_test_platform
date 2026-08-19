# 邮件渠道：新增 channel_type=email、target_address 字段，webhook_url 改为可选

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_delete_notificationrule"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationchannel",
            name="target_address",
            field=models.CharField(
                blank=True,
                default="",
                help_text="邮件渠道时使用，多个邮箱用英文逗号分隔",
                max_length=1000,
                verbose_name="收件人邮箱",
            ),
        ),
        migrations.AlterField(
            model_name="notificationchannel",
            name="webhook_url",
            field=models.URLField(blank=True, default="", max_length=2000, verbose_name="Webhook 地址"),
        ),
        migrations.AlterField(
            model_name="notificationchannel",
            name="channel_type",
            field=models.CharField(
                choices=[
                    ("dingtalk", "钉钉"),
                    ("wechat_work", "企微"),
                    ("email", "邮件"),
                ],
                db_index=True,
                default="dingtalk",
                max_length=20,
                verbose_name="渠道类型",
            ),
        ),
    ]
