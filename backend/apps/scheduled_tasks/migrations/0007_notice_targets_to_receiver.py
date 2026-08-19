# 将 notice_targets 从 NotificationChannel 改为 NotificationReceiver
# 使用 RemoveField + AddField 替代 AlterField，避免 Django 4.2 在 SQLite 上的 M2M 目标变更 bug
# 通过临时表保留 M2M 数据（旧 ID 与新 ID 相同，因 notification_receiver 即原 notifications_channel）

from django.db import migrations, models


def save_and_restore_m2m(apps, schema_editor):
    """保存 M2M 数据到临时表（在 RemoveField 之前由 RunPython 调用）"""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='scheduled_tasks_scheduledtask_notice_targets'
        """)
        if not cursor.fetchone():
            return  # 表不存在则跳过（可能已被删除）
        cursor.execute("PRAGMA table_info(scheduled_tasks_scheduledtask_notice_targets)")
        cols = [c[1] for c in cursor.fetchall()]
        fk_col = 'notificationchannel_id' if 'notificationchannel_id' in cols else 'notificationreceiver_id'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _m2m_notice_targets_backup (
                scheduledtask_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL
            )
        """)
        cursor.execute("DELETE FROM _m2m_notice_targets_backup")
        # 仅保留有效整数 ID（过滤脏数据如字符串 'notificationchannel_id'）
        cursor.execute(f"""
            INSERT INTO _m2m_notice_targets_backup (scheduledtask_id, receiver_id)
            SELECT scheduledtask_id, {fk_col}
            FROM scheduled_tasks_scheduledtask_notice_targets
            WHERE typeof({fk_col}) = 'integer'
        """)


def restore_m2m(apps, schema_editor):
    """从临时表恢复 M2M 数据到新表（在 AddField 之后由 RunPython 调用）"""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='_m2m_notice_targets_backup'
        """)
        if cursor.fetchone():
            cursor.execute("""
                INSERT INTO scheduled_tasks_scheduledtask_notice_targets (scheduledtask_id, notificationreceiver_id)
                SELECT scheduledtask_id, receiver_id FROM _m2m_notice_targets_backup
            """)
            cursor.execute("DROP TABLE _m2m_notice_targets_backup")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("scheduled_tasks", "0006_notice_targets_drop_notice_channel"),
        ("notifications", "0008_refactor_channel_and_receiver"),
    ]

    operations = [
        migrations.RunPython(save_and_restore_m2m, noop),
        migrations.RemoveField(
            model_name="scheduledtask",
            name="notice_targets",
        ),
        migrations.AddField(
            model_name="scheduledtask",
            name="notice_targets",
            field=models.ManyToManyField(
                blank=True,
                help_text="接收执行结果通知的群组（企微/钉钉等）",
                related_name="scheduled_tasks",
                to="notifications.NotificationReceiver",
                verbose_name="通知对象",
            ),
        ),
        migrations.RunPython(restore_m2m, noop),
    ]
