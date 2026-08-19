# 数据修复：将 notification_receiver 的 project_id 更正为关联定时任务所属项目
# 根因：部分接收对象在迁移或历史创建时 project_id 错误（如 project_id=1），
#       导致在「智慧物业」等 project_id=3 的项目下「通知接收管理」查不到数据

from django.db import migrations


def fix_receiver_project_id(apps, schema_editor):
    NotificationReceiver = apps.get_model('notifications', 'NotificationReceiver')
    ScheduledTask = apps.get_model('scheduled_tasks', 'ScheduledTask')

    for task in ScheduledTask.objects.prefetch_related('notice_targets').all():
        task_project_id = task.project_id
        for receiver in task.notice_targets.all():
            if receiver.project_id != task_project_id:
                receiver.project_id = task_project_id
                receiver.save(update_fields=['project_id'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0009_remove_notificationchannel_audit_fk'),
        ('scheduled_tasks', '0007_notice_targets_to_receiver'),
    ]

    operations = [
        migrations.RunPython(fix_receiver_project_id, noop),
    ]
