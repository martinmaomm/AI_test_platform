from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('scheduled_tasks', '0009_platform_native_reports')]
    operations = [
        migrations.AlterField(
            model_name='scheduledtask', name='status',
            field=models.CharField(choices=[('active', '启用'), ('paused', '暂停')],
                                   default='active', max_length=10, verbose_name='任务状态'),
        ),
        migrations.AlterField(
            model_name='scheduledtask', name='suite_type',
            field=models.CharField(choices=[('web', 'Web测试'), ('api', 'API测试')],
                                   max_length=10, verbose_name='测试类型'),
        ),
    ]
