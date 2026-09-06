from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api_testing', '0015_apiworkspace')]

    operations = [
        migrations.AddField(
            model_name='apitestexecution', name='input_snapshot',
            field=models.JSONField(blank=True, default=dict, verbose_name='执行输入快照'),
        ),
        migrations.AddField(
            model_name='apitestsuite', name='variables',
            field=models.JSONField(blank=True, default=dict, verbose_name='套件变量'),
        ),
    ]
