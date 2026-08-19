# 将 notice_targets 从 NotificationChannel 改为 NotificationReceiver
# 使用 RemoveField + AddField 替代 AlterField，避免 Django 4.2 的 M2M 目标变更问题
# 通过临时表保留 M2M 数据（旧 ID 与新 ID 相同，因 notification_receiver 即原 notifications_channel）

from django.db import migrations, models


THROUGH_TABLE = 'scheduled_tasks_scheduledtask_notice_targets'
BACKUP_TABLE = '_m2m_notice_targets_backup'


def _get_table_columns(connection, cursor, table_name):
    """通过 Django 数据库抽象层读取表字段，兼容 MySQL 和 SQLite。"""
    return {
        field.name
        for field in connection.introspection.get_table_description(cursor, table_name)
    }


def save_and_restore_m2m(apps, schema_editor):
    """保存 M2M 数据到临时表（在 RemoveField 之前由 RunPython 调用）"""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        table_names = set(connection.introspection.table_names(cursor))
        if THROUGH_TABLE not in table_names:
            return  # 表不存在则跳过（可能已被删除）

        columns = _get_table_columns(connection, cursor, THROUGH_TABLE)
        fk_col = next(
            (
                name
                for name in ('notificationchannel_id', 'notificationreceiver_id')
                if name in columns
            ),
            None,
        )
        if not fk_col or 'scheduledtask_id' not in columns:
            return

        quote = connection.ops.quote_name
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {quote(BACKUP_TABLE)} "
            f"({quote('scheduledtask_id')} BIGINT NOT NULL, "
            f"{quote('receiver_id')} BIGINT NOT NULL)"
        )
        cursor.execute(f"DELETE FROM {quote(BACKUP_TABLE)}")
        cursor.execute(
            f"INSERT INTO {quote(BACKUP_TABLE)} "
            f"({quote('scheduledtask_id')}, {quote('receiver_id')}) "
            f"SELECT {quote('scheduledtask_id')}, {quote(fk_col)} "
            f"FROM {quote(THROUGH_TABLE)}"
        )


def restore_m2m(apps, schema_editor):
    """从临时表恢复 M2M 数据到新表（在 AddField 之后由 RunPython 调用）"""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        table_names = set(connection.introspection.table_names(cursor))
        if BACKUP_TABLE not in table_names or THROUGH_TABLE not in table_names:
            return

        columns = _get_table_columns(connection, cursor, THROUGH_TABLE)
        if 'scheduledtask_id' not in columns or 'notificationreceiver_id' not in columns:
            return

        quote = connection.ops.quote_name
        cursor.execute(
            f"INSERT INTO {quote(THROUGH_TABLE)} "
            f"({quote('scheduledtask_id')}, {quote('notificationreceiver_id')}) "
            f"SELECT {quote('scheduledtask_id')}, {quote('receiver_id')} "
            f"FROM {quote(BACKUP_TABLE)}"
        )
        cursor.execute(f"DROP TABLE {quote(BACKUP_TABLE)}")


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
