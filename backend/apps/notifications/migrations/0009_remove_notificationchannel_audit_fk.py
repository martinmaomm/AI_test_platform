# 移除 NotificationChannel 的 created_by/updated_by（原指向 auth.user，项目使用 users.User 导致 no such table）

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0008_refactor_channel_and_receiver'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='notificationchannel',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='notificationchannel',
            name='updated_by',
        ),
    ]
