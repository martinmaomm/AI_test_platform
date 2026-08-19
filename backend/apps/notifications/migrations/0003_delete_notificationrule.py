# 废除通知规则，改为任务内聚 notice_targets

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0002_notificationrule"),
    ]

    operations = [
        migrations.DeleteModel(name="NotificationRule"),
    ]
