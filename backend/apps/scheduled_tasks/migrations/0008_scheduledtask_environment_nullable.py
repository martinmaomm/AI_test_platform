from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduled_tasks', '0007_notice_targets_to_receiver'),
    ]

    operations = [
        migrations.AlterField(
            model_name='scheduledtask',
            name='environment',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, to='projects.environment', verbose_name='执行环境'),
        ),
    ]
