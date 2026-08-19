# 动态邮件配置：EmailConfig 模型

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_add_email_channel_and_target_address"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="如：公司邮件服务器", max_length=100, verbose_name="配置名称")),
                ("smtp_server", models.CharField(max_length=255, verbose_name="SMTP 服务器")),
                ("port", models.PositiveIntegerField(default=465, verbose_name="端口")),
                ("sender_email", models.EmailField(max_length=255, verbose_name="发件邮箱")),
                ("smtp_password", models.CharField(max_length=255, verbose_name="SMTP 授权码")),
                ("use_ssl", models.BooleanField(default=True, verbose_name="使用 SSL")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "邮件服务配置",
                "verbose_name_plural": "邮件服务配置",
                "db_table": "notifications_email_config",
                "ordering": ["-created_at"],
            },
        ),
    ]
