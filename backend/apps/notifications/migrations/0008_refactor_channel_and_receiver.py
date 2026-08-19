# 重构：全局渠道 NotificationChannel + 项目接收对象 NotificationReceiver

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_global_channels_and_migrate(apps, schema_editor):
    """创建预置渠道，并将旧 channel_type 映射到新 channel_id"""
    NotificationChannel = apps.get_model('notifications', 'NotificationChannel')
    NotificationReceiver = apps.get_model('notifications', 'NotificationReceiver')
    Project = apps.get_model('projects', 'Project')

    # 1. 创建预置渠道
    channels_data = [
        {
            'channel_code': 'dingtalk',
            'channel_name': '钉钉',
            'description': '1. 在钉钉群中添加「自定义机器人」；2. 选择「加签」安全设置并获取 Webhook 地址；3. 将 Webhook 地址填入下方。',
        },
        {
            'channel_code': 'wechat_work',
            'channel_name': '企业微信',
            'description': '1. 在企业微信群中添加「群机器人」；2. 获取 Webhook 地址；3. 将 Webhook 地址填入下方。',
        },
        {
            'channel_code': 'email',
            'channel_name': '邮件',
            'description': '在下方填写收件人邮箱，多个邮箱用英文逗号分隔。需先在「系统设置 - 邮件服务配置」中配置 SMTP。',
        },
    ]
    channel_by_code = {}
    for d in channels_data:
        ch, _ = NotificationChannel.objects.get_or_create(
            channel_code=d['channel_code'],
            defaults={
                'channel_name': d['channel_name'],
                'description': d['description'],
                'is_active': True,
            },
        )
        channel_by_code[d['channel_code']] = ch

    # 2. 为旧记录设置 channel_id，并处理 project_id 为空的情况
    first_project = Project.objects.order_by('id').first()
    for rec in list(NotificationReceiver.objects.all()):
        code = (rec.channel_type or 'dingtalk').strip()
        ch = channel_by_code.get(code) or channel_by_code['dingtalk']
        rec.channel_id = ch.id
        if rec.project_id is None:
            if first_project:
                rec.project_id = first_project.id
                rec.save()
            else:
                rec.delete()  # 无项目时删除孤立记录
        else:
            rec.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('projects', '0001_initial'),
        ('notifications', '0007_notificationchannel_project'),
    ]

    operations = [
        # 1. 将原 NotificationChannel 重命名为 NotificationReceiver（表仍为 notifications_channel）
        migrations.RenameModel(
            old_name='NotificationChannel',
            new_name='NotificationReceiver',
        ),
        # 2. 创建新的全局渠道模型
        migrations.CreateModel(
            name='NotificationChannel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel_code', models.CharField(db_index=True, help_text='如 dingtalk, wechat_work, email', max_length=32, unique=True, verbose_name='渠道编码')),
                ('channel_name', models.CharField(max_length=64, verbose_name='渠道名称')),
                ('description', models.TextField(blank=True, default='', help_text='接入步骤、Webhook 获取方式等', verbose_name='配置引导说明')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_notification_channels', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_notification_channels', to=settings.AUTH_USER_MODEL, verbose_name='修改人')),
            ],
            options={
                'verbose_name': '全局渠道',
                'verbose_name_plural': '全局渠道',
                'db_table': 'notification_channel',
                'ordering': ['channel_code'],
            },
        ),
        # 3. 为 NotificationReceiver 添加 channel FK（先可空）
        migrations.AddField(
            model_name='notificationreceiver',
            name='channel',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='receivers', to='notifications.notificationchannel', verbose_name='渠道类型'),
        ),
        # 4. 数据迁移
        migrations.RunPython(create_global_channels_and_migrate, noop),
        # 5. 移除 channel_type
        migrations.RemoveField(
            model_name='notificationreceiver',
            name='channel_type',
        ),
        # 6. channel 改为必填
        migrations.AlterField(
            model_name='notificationreceiver',
            name='channel',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='receivers', to='notifications.notificationchannel', verbose_name='渠道类型'),
        ),
        # 7. project 改为必填（旧数据已在 RunPython 中补全）
        migrations.AlterField(
            model_name='notificationreceiver',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_receivers', to='projects.project', verbose_name='所属项目'),
        ),
        # 8. 重命名表
        migrations.AlterModelTable(
            name='notificationreceiver',
            table='notification_receiver',
        ),
    ]
