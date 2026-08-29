from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('web_testing', '0009_webui_script_metadata_execution_project'),
    ]

    operations = [
        migrations.AddField(
            model_name='webuitestexecution',
            name='error_message',
            field=models.TextField(blank=True, default='', verbose_name='执行错误信息'),
        ),
    ]
