# Generated manually for the additive exploration timeout snapshot.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('web_testing', '0016_webuiscriptgeneration_workspace')]

    operations = [
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='exploration_timeout_seconds',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                verbose_name='页面探索总超时时间（秒）',
            ),
        ),
    ]
