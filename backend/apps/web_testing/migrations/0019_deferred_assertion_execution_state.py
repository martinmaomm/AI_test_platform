from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('web_testing', '0018_clear_pre_v3_script_generations')]

    operations = [
        migrations.AddField(
            model_name='webuitestsuiteexecutiondetail',
            name='incomplete_cases',
            field=models.PositiveIntegerField(default=0, verbose_name='验证未完成用例数'),
        ),
        migrations.AlterField(
            model_name='webuitestexecution',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', '待执行'), ('running', '执行中'), ('passed', '执行通过'),
                    ('incomplete', '验证未完成'), ('failed', '执行失败'),
                    ('error', '执行错误'), ('stopped', '已停止'),
                ],
                default='pending', max_length=20, verbose_name='执行状态',
            ),
        ),
        migrations.AlterField(
            model_name='webuitestcaseexecutiondetail',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', '待执行'), ('running', '执行中'), ('passed', '执行通过'),
                    ('incomplete', '验证未完成'), ('failed', '执行失败'),
                    ('error', '执行错误'), ('skipped', '已跳过'),
                ],
                default='pending', max_length=20, verbose_name='执行状态',
            ),
        ),
        migrations.AlterField(
            model_name='webuitestsuitecaseexecution',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', '待执行'), ('running', '执行中'), ('passed', '执行通过'),
                    ('incomplete', '验证未完成'), ('failed', '执行失败'),
                    ('error', '执行错误'), ('skipped', '已跳过'),
                ],
                default='pending', max_length=20, verbose_name='执行状态',
            ),
        ),
    ]
