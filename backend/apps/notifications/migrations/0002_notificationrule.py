# 创建 NotificationRule 及 channels M2M（notifications_rule 表）

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
        ('django_celery_beat', '0018_improve_crontab_helptext'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='规则名称')),
                ('trigger_mode', models.CharField(choices=[('always', '始终发送'), ('fail', '仅失败时发送')], db_index=True, default='always', max_length=20, verbose_name='触发条件')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('task', models.ForeignKey(help_text='对应 Celery Beat 的 PeriodicTask，名称为 scheduled_task_{ScheduledTask.id}', on_delete=django.db.models.deletion.CASCADE, related_name='notification_rules', to='django_celery_beat.periodictask', verbose_name='关联定时任务')),
            ],
            options={
                'verbose_name': '通知规则',
                'verbose_name_plural': '通知规则',
                'db_table': 'notifications_rule',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='notificationrule',
            name='channels',
            field=models.ManyToManyField(related_name='rules', to='notifications.notificationchannel', verbose_name='接收渠道'),
        ),
    ]
