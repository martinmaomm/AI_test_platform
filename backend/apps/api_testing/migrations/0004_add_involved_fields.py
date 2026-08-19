# Generated manually for aits_system

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0003_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitestcase',
            name='involved_endpoints',
            field=models.JSONField(blank=True, default=list, help_text='场景涉及的端点列表，格式: [{"method": "GET", "path": "/api/user/login"}]', verbose_name='involved endpoints'),
        ),
        migrations.AddField(
            model_name='apitestcase',
            name='involved_modules',
            field=models.JSONField(blank=True, default=list, help_text='场景涉及的模块列表，格式: ["用户模块", "订单模块"]', verbose_name='involved modules'),
        ),
    ]
