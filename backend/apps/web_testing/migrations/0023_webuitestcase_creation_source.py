from django.db import migrations, models


def backfill_proven_ai_cases(apps, schema_editor):
    Case = apps.get_model('web_testing', 'WebUITestCase')
    Generation = apps.get_model('web_testing', 'WebUIScriptGeneration')

    proven_case_ids = set()
    generations = Generation.objects.exclude(test_case_id__isnull=True).select_related('test_case')
    for generation in generations.iterator(chunk_size=500):
        case_id = generation.test_case_id
        case = generation.test_case
        # The active save path creates a case only after its generation record.
        # If the case predates the generation, the generation may merely have
        # overwritten a manual case and is not evidence of its initial source.
        if (
            case.project_id == generation.project_id
            and case.user_id == generation.user_id
            and generation.created_at is not None
            and case.created_at is not None
            and generation.created_at <= case.created_at
        ):
            proven_case_ids.add(case_id)
    if proven_case_ids:
        Case.objects.filter(id__in=proven_case_ids).update(creation_source='ai')


class Migration(migrations.Migration):

    dependencies = [('web_testing', '0022_webuitestcaseexecutiondetail_execution_options_and_more')]

    operations = [
        migrations.AddField(
            model_name='webuitestcase',
            name='creation_source',
            field=models.CharField(
                choices=[('manual', '手工创建'), ('ai', 'AI 生成'), ('unknown', '未知')],
                default='unknown', editable=False, max_length=16, verbose_name='初始创建来源',
            ),
        ),
        migrations.RunPython(backfill_proven_ai_cases, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='webuitestcase',
            name='creation_source',
            field=models.CharField(
                choices=[('manual', '手工创建'), ('ai', 'AI 生成'), ('unknown', '未知')],
                default='manual', editable=False, max_length=16, verbose_name='初始创建来源',
            ),
        ),
    ]
