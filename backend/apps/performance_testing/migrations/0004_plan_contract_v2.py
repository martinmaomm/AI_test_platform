from urllib.parse import parse_qs, urlsplit

from django.db import migrations, models


def upgrade_plans(apps, schema_editor):
    PerformancePlan = apps.get_model('performance_testing', 'PerformancePlan')
    for plan in PerformancePlan.objects.all().iterator():
        upgraded = []
        for raw in plan.steps or []:
            if not isinstance(raw, dict):
                upgraded.append(raw)
                continue
            if 'phase' in raw:
                upgraded.append(raw)
                continue
            parsed = urlsplit(str(raw.get('path') or '/'))
            query = {
                key: values[0] if len(values) == 1 else values
                for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            }
            body = raw.get('body')
            upgraded.append({
                'name': raw.get('name', ''),
                'phase': 'main',
                'method': raw.get('method', 'GET'),
                'path': parsed.path or '/',
                'query': query,
                'headers': raw.get('headers') if isinstance(raw.get('headers'), dict) else {},
                'body_type': 'none' if body is None else 'json',
                'body': body,
                'extract': [],
                'assertions': [{
                    'check': 'status_code',
                    'comparator': 'eq',
                    'expected': raw.get('expected_status', 200),
                }],
            })
        plan.steps = upgraded
        plan.variables = plan.variables or {}
        plan.unique_variables = plan.unique_variables or []
        plan.save(update_fields=('steps', 'variables', 'unique_variables'))


class Migration(migrations.Migration):
    dependencies = [('performance_testing', '0003_remove_performancenode_labels_and_add_deleted_at')]

    operations = [
        migrations.AddField(
            model_name='performanceplan',
            name='variables',
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name='performanceplan',
            name='unique_variables',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='performancerun',
            name='mode',
            field=models.CharField(
                choices=[('validation', 'Validation'), ('load', 'Load')],
                default='load', max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='performancerun',
            name='validation_key',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.RunPython(upgrade_plans, migrations.RunPython.noop),
    ]
