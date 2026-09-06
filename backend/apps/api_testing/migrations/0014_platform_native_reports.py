from django.db import migrations, models
import django.db.models.deletion


def copy_suite_name_snapshots(apps, schema_editor):
    SuiteDetail = apps.get_model('api_testing', 'APITestSuiteExecutionDetail')
    for detail in SuiteDetail.objects.select_related('test_suite').iterator():
        if detail.test_suite_id and not detail.test_suite_name:
            detail.test_suite_name = detail.test_suite.name
            detail.save(update_fields=['test_suite_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0013_alter_apitestexecution_exec_type'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='apitestsuiteexecutiondetail',
            name='allure_report',
        ),
        migrations.AddField(
            model_name='apitestsuiteexecutiondetail',
            name='test_suite_name',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='测试套件名称快照'),
        ),
        migrations.AlterField(
            model_name='apitestcaseexecutiondetail',
            name='test_case',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='api_testing.apitestcase', verbose_name='测试用例'),
        ),
        migrations.AlterField(
            model_name='apitestsuiteexecutiondetail',
            name='test_suite',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='api_testing.apitestsuite', verbose_name='测试套件'),
        ),
        migrations.AlterField(
            model_name='apitestsuitecaseexecution',
            name='test_case',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='api_testing.apitestcase', verbose_name='测试用例'),
        ),
        migrations.RunPython(copy_suite_name_snapshots, migrations.RunPython.noop),
    ]
