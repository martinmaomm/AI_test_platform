from django.db import migrations, models
import django.db.models.deletion


def backfill_webui_metadata_and_execution_projects(apps, schema_editor):
    WebUITestCase = apps.get_model('web_testing', 'WebUITestCase')
    WebUITestExecution = apps.get_model('web_testing', 'WebUITestExecution')
    CaseDetail = apps.get_model('web_testing', 'WebUITestCaseExecutionDetail')
    SuiteDetail = apps.get_model('web_testing', 'WebUITestSuiteExecutionDetail')

    WebUITestCase.objects.filter(
        models.Q(test_script_content__isnull=True) | models.Q(test_script_content='')
    ).update(
        script_source='manual',
        script_status='none',
        script_version=0,
        script_validation_error='',
    )
    WebUITestCase.objects.filter(
        test_script_content__isnull=False
    ).exclude(test_script_content='').update(
        script_source='legacy',
        script_status='legacy',
        script_version=1,
        script_validation_error='',
    )

    for execution in WebUITestExecution.objects.filter(project__isnull=True).iterator():
        project_id = None
        case_detail = CaseDetail.objects.filter(
            execution_id=execution.pk
        ).select_related('test_case').first()
        if case_detail and case_detail.test_case:
            project_id = case_detail.test_case.project_id
        else:
            suite_detail = SuiteDetail.objects.filter(
                execution_id=execution.pk
            ).select_related('test_suite').first()
            if suite_detail and suite_detail.test_suite:
                project_id = suite_detail.test_suite.project_id

        if project_id is not None:
            WebUITestExecution.objects.filter(pk=execution.pk).update(project_id=project_id)


class Migration(migrations.Migration):

    dependencies = [
        ('web_testing', '0008_merge_into_webuitestmodule'),
    ]

    operations = [
        migrations.AddField(
            model_name='webuitestcase',
            name='script_source',
            field=models.CharField(
                choices=[
                    ('manual', '手工编写'),
                    ('requirement_ai', '需求 AI'),
                    ('mcp_exploration', 'MCP 探索'),
                    ('step_generator', '步骤生成器'),
                    ('legacy', '历史脚本'),
                ],
                default='manual',
                max_length=30,
                verbose_name='脚本来源',
            ),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='script_status',
            field=models.CharField(
                choices=[
                    ('none', '无脚本'),
                    ('ready', '可执行'),
                    ('legacy', '旧格式'),
                    ('invalid', '无效'),
                ],
                default='none',
                max_length=20,
                verbose_name='脚本状态',
            ),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='script_framework',
            field=models.CharField(
                choices=[('playwright_python_async', 'Playwright Python Async')],
                default='playwright_python_async',
                max_length=50,
                verbose_name='脚本框架',
            ),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='script_version',
            field=models.PositiveIntegerField(default=0, verbose_name='脚本版本'),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='script_validation_error',
            field=models.TextField(blank=True, default='', verbose_name='脚本校验错误'),
        ),
        migrations.AddField(
            model_name='webuitestcase',
            name='generation_metadata',
            field=models.JSONField(blank=True, default=dict, verbose_name='生成元数据'),
        ),
        migrations.AddField(
            model_name='webuitestexecution',
            name='project',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='webui_executions',
                to='projects.project',
                verbose_name='所属项目',
            ),
        ),
        migrations.AddIndex(
            model_name='webuitestexecution',
            index=models.Index(
                fields=['project', 'executor', 'created_at'],
                name='webui_exec_proj_exec_created',
            ),
        ),
        migrations.AddIndex(
            model_name='webuitestexecution',
            index=models.Index(
                fields=['project', 'status'],
                name='webui_exec_project_status',
            ),
        ),
        migrations.RunPython(
            backfill_webui_metadata_and_execution_projects,
            migrations.RunPython.noop,
        ),
    ]
