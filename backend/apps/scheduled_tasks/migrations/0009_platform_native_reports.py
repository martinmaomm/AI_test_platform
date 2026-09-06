from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduled_tasks', '0008_scheduledtask_environment_nullable'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='taskexecutionlog',
            name='report_path',
        ),
        migrations.RemoveField(
            model_name='taskexecutionlog',
            name='allure_report_url',
        ),
        migrations.AddField(
            model_name='taskexecutionlog',
            name='linked_executions',
            field=models.JSONField(blank=True, default=list, verbose_name='关联执行记录'),
        ),
        migrations.AddField(
            model_name='taskexecutionlog',
            name='notification_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='报告通知发送时间'),
        ),
    ]
