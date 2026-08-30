from django.db import migrations, models
import django.db.models.deletion


def reset_legacy_webui_data(apps, schema_editor):
    """The new script-asset workflow intentionally starts without legacy data."""
    model_names = [
        'WebUITestExecution',
        'WebUIScriptGeneration',
        'WebUITestCaseGeneration',
        'WebUITestSuite',
        'WebUITestCase',
        'WebElement',
        'WebPage',
        'WebUITestModule',
    ]
    for model_name in model_names:
        apps.get_model('web_testing', model_name).objects.all().delete()

    Project = apps.get_model('projects', 'Project')
    WebUITestModule = apps.get_model('web_testing', 'WebUITestModule')
    WebUITestModule.objects.bulk_create([
        WebUITestModule(
            project_id=project_id,
            name='默认模块',
            parent_id=None,
            order=0,
            is_default=True,
        )
        for project_id in Project.objects.filter(project_type='web').values_list('id', flat=True)
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('web_testing', '0014_webuitestcasegeneration_client_request_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='webuitestmodule',
            name='is_default',
            field=models.BooleanField(default=False, verbose_name='是否默认模块'),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='variables',
            field=models.JSONField(blank=True, default=list, verbose_name='用例变量'),
        ),
        migrations.AddField(
            model_name='webuitestsuite',
            name='variables',
            field=models.JSONField(blank=True, default=list, verbose_name='套件变量'),
        ),
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='module',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='script_generations',
                to='web_testing.webuitestmodule',
                verbose_name='目标业务模块',
            ),
        ),
        migrations.RunPython(reset_legacy_webui_data, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='webuitestsuite',
            name='test_cases',
        ),
        migrations.CreateModel(
            name='WebUITestSuiteCase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='执行顺序')),
                ('suite', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='case_memberships', to='web_testing.webuitestsuite', verbose_name='测试套件')),
                ('test_case', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='suite_memberships', to='web_testing.webuitestcase', verbose_name='测试用例')),
            ],
            options={
                'db_table': 'webui_test_suite_cases',
                'ordering': ['order', 'id'],
            },
        ),
        migrations.AddField(
            model_name='webuitestsuite',
            name='test_cases',
            field=models.ManyToManyField(blank=True, related_name='test_suites', through='web_testing.WebUITestSuiteCase', to='web_testing.webuitestcase', verbose_name='测试用例'),
        ),
        migrations.AddIndex(
            model_name='webuitestsuitecase',
            index=models.Index(fields=['suite', 'order'], name='webui_test__suite_i_50f8f8_idx'),
        ),
        migrations.AddConstraint(
            model_name='webuitestsuitecase',
            constraint=models.UniqueConstraint(fields=('suite', 'test_case'), name='unique_webui_suite_case'),
        ),
        migrations.AlterField(
            model_name='webuitestcaseexecutiondetail',
            name='test_case',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='web_testing.webuitestcase', verbose_name='测试用例'),
        ),
        migrations.AlterField(
            model_name='webuitestsuitecaseexecution',
            name='test_case',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='web_testing.webuitestcase', verbose_name='测试用例'),
        ),
        migrations.AlterField(
            model_name='webuitestsuiteexecutiondetail',
            name='test_suite',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='web_testing.webuitestsuite', verbose_name='测试套件'),
        ),
        migrations.RemoveConstraint(
            model_name='webuitestcase',
            name='unique_webui_requirement_draft',
        ),
        migrations.RemoveIndex(
            model_name='webuitestcase',
            name='webui_test__categor_adfa41_idx',
        ),
        migrations.RemoveField(model_name='webuitestcase', name='category'),
        migrations.RemoveField(model_name='webuitestcase', name='expected_result'),
        migrations.RemoveField(model_name='webuitestcase', name='preconditions'),
        migrations.RemoveField(model_name='webuitestcase', name='priority'),
        migrations.RemoveField(model_name='webuitestcase', name='source_draft_key'),
        migrations.RemoveField(model_name='webuitestcase', name='source_requirement_generation'),
        migrations.RemoveField(model_name='webuitestcase', name='steps'),
        migrations.RemoveField(model_name='webuitestcase', name='url'),
        migrations.RemoveField(model_name='webuiscriptgeneration', name='source_mode'),
        migrations.RemoveField(model_name='webuitestmodule', name='business_rules'),
        migrations.RemoveField(model_name='webuitestmodule', name='description'),
        migrations.AlterField(
            model_name='webuitestcase',
            name='script_source',
            field=models.CharField(choices=[('manual', '手工编写'), ('mcp_exploration', 'MCP 探索')], default='manual', max_length=30, verbose_name='脚本来源'),
        ),
        migrations.AlterField(
            model_name='webuitestcase',
            name='script_status',
            field=models.CharField(choices=[('none', '无脚本'), ('ready', '可执行'), ('invalid', '无效')], default='none', max_length=20, verbose_name='脚本状态'),
        ),
        migrations.RemoveField(model_name='webelement', name='page'),
        migrations.RemoveField(model_name='webpage', name='module'),
        migrations.RemoveField(model_name='webpage', name='project'),
        migrations.RemoveField(model_name='webuitestcasegeneration', name='model_config'),
        migrations.RemoveField(model_name='webuitestcasegeneration', name='module'),
        migrations.RemoveField(model_name='webuitestcasegeneration', name='project'),
        migrations.RemoveField(model_name='webuitestcasegeneration', name='user'),
        migrations.DeleteModel(name='WebElement'),
        migrations.DeleteModel(name='WebPage'),
        migrations.DeleteModel(name='WebUITestCaseGeneration'),
    ]
