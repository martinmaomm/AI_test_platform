from django.db import migrations, models
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def backfill_proven_ai_cases(apps, schema_editor):
    Case = apps.get_model('api_testing', 'APITestCase')
    Workspace = apps.get_model('api_testing', 'APIWorkspace')

    proven_case_ids = set()
    workspaces = Workspace.objects.exclude(saved_case_id__isnull=True).select_related('saved_case')
    for workspace in workspaces.iterator(chunk_size=500):
        generation = workspace.generation if isinstance(workspace.generation, dict) else {}
        adopted_revision = generation.get('adopted_revision')
        if (
            not isinstance(adopted_revision, int)
            or isinstance(adopted_revision, bool)
            or adopted_revision <= 0
            or adopted_revision > workspace.revision
        ):
            continue
        finished_at = generation.get('finished_at')
        if not isinstance(finished_at, str):
            continue
        try:
            finished_at = parse_datetime(finished_at)
            if finished_at is not None and timezone.is_naive(finished_at):
                finished_at = timezone.make_aware(finished_at)
        except (TypeError, ValueError):
            finished_at = None
        case_id = workspace.saved_case_id
        case = workspace.saved_case
        # A workspace created after the case may only have edited an existing
        # manual case.  Only a same-project workspace that predates the case
        # proves this save path created the case after adopting an AI draft.
        if (
            case.project_id == workspace.project_id
            and case.created_by_id == workspace.owner_id
            and workspace.created_at is not None
            and case.created_at is not None
            and workspace.created_at <= case.created_at
            and finished_at is not None
            and finished_at <= case.created_at
        ):
            proven_case_ids.add(case_id)
    if proven_case_ids:
        Case.objects.filter(id__in=proven_case_ids).update(creation_source='ai')


class Migration(migrations.Migration):

    dependencies = [('api_testing', '0020_apiworkspace_target_endpoint_id')]

    operations = [
        migrations.AddField(
            model_name='apitestcase',
            name='creation_source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('ai', 'AI generated'), ('unknown', 'Unknown')],
                default='unknown', editable=False, max_length=16, verbose_name='creation source',
            ),
        ),
        migrations.RunPython(backfill_proven_ai_cases, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='apitestcase',
            name='creation_source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('ai', 'AI generated'), ('unknown', 'Unknown')],
                default='manual', editable=False, max_length=16, verbose_name='creation source',
            ),
        ),
    ]
