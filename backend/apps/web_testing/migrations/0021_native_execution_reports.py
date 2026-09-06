from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_testing', '0020_remove_webui_environment_dependencies'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='webuitestexecution',
            name='report_path',
        ),
        migrations.RemoveField(
            model_name='webuitestsuiteexecutiondetail',
            name='allure_report',
        ),
        migrations.AddField(
            model_name='webuitestsuiteexecutiondetail',
            name='suite_variables',
            field=models.JSONField(blank=True, default=list, verbose_name='套件变量快照'),
        ),
        migrations.AddField(
            model_name='webuitestsuitecaseexecution',
            name='description',
            field=models.TextField(blank=True, default='', verbose_name='用例描述快照'),
        ),
        migrations.AddField(
            model_name='webuitestsuitecaseexecution',
            name='execution_order',
            field=models.PositiveIntegerField(default=0, verbose_name='执行顺序'),
        ),
        migrations.AddField(
            model_name='webuitestsuitecaseexecution',
            name='module_name',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='模块名称快照'),
        ),
        migrations.AddField(
            model_name='webuitestsuitecaseexecution',
            name='script_content',
            field=models.TextField(blank=True, default='', verbose_name='脚本快照'),
        ),
        migrations.AddField(
            model_name='webuitestsuitecaseexecution',
            name='variables',
            field=models.JSONField(blank=True, default=list, verbose_name='用例变量快照'),
        ),
    ]
