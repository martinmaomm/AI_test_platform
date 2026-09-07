# Generated manually for the API workspace generate-verify pipeline.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0016_api_execution_snapshot_suite_variables'),
    ]

    operations = [
        migrations.AddField(
            model_name='apiworkspace',
            name='generation',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='apiworkspace',
            name='spec',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='workspaces', to='api_testing.apispecification',
            ),
        ),
    ]
