from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_core', '0002_llmconfiguration_provider_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='mcpconfiguration',
            name='tools_checked_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='工具检测时间'),
        ),
        migrations.AddField(
            model_name='mcpconfiguration',
            name='tools_error',
            field=models.TextField(blank=True, default='', verbose_name='工具检测错误'),
        ),
        migrations.AddField(
            model_name='mcpconfiguration',
            name='tools_probe_token',
            field=models.UUIDField(blank=True, editable=False, null=True, verbose_name='工具探测令牌'),
        ),
        migrations.AddField(
            model_name='mcpconfiguration',
            name='tools_status',
            field=models.CharField(
                choices=[('unchecked', '尚未检测'), ('ready', '检测成功'), ('error', '检测失败')],
                default='unchecked',
                max_length=16,
                verbose_name='工具清单状态',
            ),
        ),
    ]
