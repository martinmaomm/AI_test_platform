from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_testing', '0011_webuiscriptgeneration'),
    ]

    operations = [
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='clarifications',
            field=models.JSONField(blank=True, default=list, verbose_name='脱敏补充确认记录'),
        ),
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='resume_count',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='已恢复次数'),
        ),
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='revision',
            field=models.PositiveIntegerField(default=0, verbose_name='暂停处理版本'),
        ),
    ]
