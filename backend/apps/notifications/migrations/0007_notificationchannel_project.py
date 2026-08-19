# Generated migration: add project_id to NotificationChannel

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_initial'),
        ('notifications', '0006_alter_notificationchannel_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationchannel',
            name='project',
            field=models.ForeignKey(
                blank=True,
                help_text='为空表示全局；有值表示仅当前项目可见',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='notification_receivers',
                to='projects.project',
                verbose_name='所属项目',
            ),
        ),
    ]
