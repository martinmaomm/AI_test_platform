# Generated manually for additive browser-discovery persistence.  This does
# not alter or delete existing Swagger/API workspace data.
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0018_apiworkspace_scenarios'),
    ]

    operations = [
        migrations.CreateModel(
            name='BrowserDiscoveryTask',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('model_id', models.PositiveBigIntegerField()),
                ('target_url', models.URLField(max_length=1000)),
                ('description', models.TextField()),
                ('api_origin', models.CharField(blank=True, max_length=500)),
                ('allow_test_data_writes', models.BooleanField(default=False)),
                ('exploration_timeout_seconds', models.PositiveIntegerField(default=900)),
                ('limits', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('finalizing', 'Finalizing'), ('completed', 'Completed'), ('partial', 'Partial'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=16)),
                ('version', models.PositiveIntegerField(default=1)),
                ('task_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('cancellation_requested', models.BooleanField(default=False)),
                ('current_action', models.CharField(blank=True, max_length=500)),
                ('tool_calls', models.PositiveIntegerField(default=0)),
                ('model_calls', models.PositiveIntegerField(default=0)),
                ('request_count', models.PositiveIntegerField(default=0)),
                ('summary', models.TextField(blank=True)),
                ('error_code', models.CharField(blank=True, max_length=80)),
                ('error_message', models.TextField(blank=True)),
                ('evidence_summary', models.JSONField(blank=True, default=dict)),
                ('source_version', models.PositiveIntegerField(default=0)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('heartbeat_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='browser_discoveries', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='browser_discoveries', to='projects.project')),
            ],
            options={'db_table': 'api_browser_discovery_tasks', 'ordering': ['-updated_at']},
        ),
        migrations.CreateModel(
            name='BrowserDiscoveryRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField()),
                ('request_id', models.CharField(blank=True, max_length=200)),
                ('captured_at', models.DateTimeField(blank=True, null=True)),
                ('origin', models.CharField(blank=True, max_length=500)),
                ('method', models.CharField(blank=True, max_length=10)),
                ('path', models.CharField(blank=True, max_length=1000)),
                ('resource_type', models.CharField(blank=True, max_length=80)),
                ('status_code', models.PositiveIntegerField(blank=True, null=True)),
                ('content_type', models.CharField(blank=True, max_length=200)),
                ('is_eligible', models.BooleanField(db_index=True, default=False)),
                ('exclusion_reason', models.CharField(blank=True, max_length=200)),
                ('dependency_record_ids', models.JSONField(blank=True, default=list)),
                ('public_summary', models.JSONField(blank=True, default=dict)),
                ('raw_line', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='api_testing.browserdiscoverytask')),
            ],
            options={'db_table': 'api_browser_discovery_records', 'ordering': ['sequence', 'id']},
        ),
        migrations.AddField(
            model_name='apispecification', name='source_selection_key', field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='apispecification', name='source_version', field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='apispecification', name='spec_type',
            field=models.CharField(choices=[('swagger', 'Swagger/OpenAPI'), ('postman', 'Postman Collection'), ('raml', 'RAML'), ('api_blueprint', 'API Blueprint'), ('browser_capture', 'Browser capture'), ('other', '其他')], db_index=True, default='swagger', max_length=20, verbose_name='specification type'),
        ),
        migrations.AddField(
            model_name='apispecification', name='source_task',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='published_specs', to='api_testing.browserdiscoverytask'),
        ),
        migrations.CreateModel(
            name='BrowserDiscoveryHandoff',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_version', models.PositiveIntegerField()),
                ('selection_hash', models.CharField(max_length=64)),
                ('selected_record_ids', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('spec', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='browser_handoffs', to='api_testing.apispecification')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='handoffs', to='api_testing.browserdiscoverytask')),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='browser_handoffs', to='api_testing.apiworkspace')),
            ],
            options={'db_table': 'api_browser_discovery_handoffs'},
        ),
        migrations.AddIndex(model_name='browserdiscoverytask', index=models.Index(fields=['project', 'owner', '-updated_at'], name='api_browser_project_523c91_idx')),
        migrations.AddIndex(model_name='browserdiscoverytask', index=models.Index(fields=['status', 'heartbeat_at'], name='api_browser_status_bedcea_idx')),
        migrations.AddIndex(model_name='browserdiscoveryrecord', index=models.Index(fields=['task', 'is_eligible', 'sequence'], name='api_browser_task_id_0c10fb_idx')),
        # Do not put `path` (VARCHAR(1000), utf8mb4) in this index: it exceeds
        # the 3072-byte InnoDB key limit on the affected MySQL/MariaDB setup.
        migrations.AddIndex(model_name='browserdiscoveryrecord', index=models.Index(fields=['task', 'method'], name='api_browser_task_id_8c01c0_idx')),
        migrations.AddConstraint(model_name='browserdiscoveryrecord', constraint=models.UniqueConstraint(fields=('task', 'sequence'), name='unique_browser_discovery_record_sequence')),
        migrations.AddConstraint(model_name='browserdiscoveryhandoff', constraint=models.UniqueConstraint(fields=('task', 'source_version', 'selection_hash'), name='unique_browser_discovery_handoff_selection')),
    ]
