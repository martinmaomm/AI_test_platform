#!/usr/bin/env python
"""
修复 scheduled_tasks 0007 迁移导致的 M2M 脏数据（SQLite）

使用方式（在 backend 目录下，已激活虚拟环境）：
  python scripts/fix_m2m_notice_targets.py

或 Django shell：
  python manage.py shell
  >>> exec(open('scripts/fix_m2m_notice_targets.py').read())
"""
import os
import sys
import django

# 确保能导入 Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection


def main():
    with connection.cursor() as cursor:
        # 1. 检查 through 表是否存在
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='scheduled_tasks_scheduledtask_notice_targets'
        """)
        if not cursor.fetchone():
            print("表 scheduled_tasks_scheduledtask_notice_targets 不存在，无需清理。")
            return

        # 2. 查看表结构
        cursor.execute("PRAGMA table_info(scheduled_tasks_scheduledtask_notice_targets)")
        cols = cursor.fetchall()
        print("当前表结构:", [c[1] for c in cols])

        # 3. 查找脏数据（外键列为非整数的行）
        fk_col = None
        for c in cols:
            if 'notificationchannel' in c[1].lower() or 'notificationreceiver' in c[1].lower():
                fk_col = c[1]
                break
        if not fk_col:
            fk_col = 'notificationchannel_id'  # 默认

        cursor.execute(f"SELECT * FROM scheduled_tasks_scheduledtask_notice_targets")
        rows = cursor.fetchall()
        dirty = []
        for row in rows:
            # 第二列通常是 FK
            idx = next((i for i, c in enumerate(cols) if c[1] == fk_col), 1)
            val = row[idx] if idx < len(row) else row[1]
            if not isinstance(val, (int, type(None))) or (val is not None and not isinstance(val, int)):
                try:
                    int(val)
                except (TypeError, ValueError):
                    dirty.append(row)

        if not dirty:
            print("未发现脏数据。")
            return

        print(f"发现 {len(dirty)} 条脏数据，正在删除...")
        # 4. 删除脏数据（外键列应为整数，非整数视为脏数据）
        cursor.execute(
            f"DELETE FROM scheduled_tasks_scheduledtask_notice_targets WHERE typeof({fk_col}) != 'integer'"
        )
        deleted = cursor.rowcount
        print(f"已删除 {deleted} 条脏数据。")

        # 5. 删除临时表（若存在）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_m2m_notice_targets_backup'")
        if cursor.fetchone():
            cursor.execute("DROP TABLE _m2m_notice_targets_backup")
            print("已删除临时备份表 _m2m_notice_targets_backup。")


if __name__ == '__main__':
    main()
