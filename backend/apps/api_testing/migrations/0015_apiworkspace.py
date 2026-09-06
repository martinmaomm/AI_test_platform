# Generated manually for the API Workspace B contract.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import api_testing.models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0014_platform_native_reports'),
    ]

    operations = [
        migrations.CreateModel(
            name='APIWorkspace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(blank=True, max_length=200)),
                ('model_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('endpoint_ids', models.JSONField(blank=True, default=list)),
                ('draft', models.JSONField(blank=True, default=api_testing.models.default_api_workspace_draft)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('idle', 'Idle'), ('generating', 'Generating'), ('debugging', 'Debugging'), ('ready', 'Ready'), ('failed', 'Failed')], db_index=True, default='idle', max_length=16)),
                ('error', models.TextField(blank=True)),
                ('messages', models.JSONField(blank=True, default=list)),
                ('candidate', models.JSONField(blank=True, null=True)),
                ('debug_result', models.JSONField(blank=True, default=dict)),
                ('debug_snapshot', models.JSONField(blank=True, default=dict)),
                ('debug_revision', models.PositiveIntegerField(blank=True, null=True)),
                ('saved_case_updated_at', models.DateTimeField(blank=True, null=True)),
                ('task_id', models.CharField(blank=True, max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='api_workspaces', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='api_workspaces', to='projects.project')),
                ('saved_case', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='workspaces', to='api_testing.apitestcase')),
            ],
            options={'db_table': 'api_workspaces', 'ordering': ['-updated_at']},
        ),
        migrations.AddIndex(model_name='apiworkspace', index=models.Index(fields=['project', 'owner', '-updated_at'], name='api_workspa_project_ccb575_idx')),
        migrations.AddIndex(model_name='apiworkspace', index=models.Index(fields=['status'], name='api_workspa_status_970d43_idx')),
    ]
