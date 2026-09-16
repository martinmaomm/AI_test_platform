import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('performance_testing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PerformanceRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_id', models.UUIDField()),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('preparing', 'Preparing'), ('running', 'Running'), ('stopping', 'Stopping'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled'), ('incomplete', 'Incomplete')], default='queued', max_length=20)),
                ('snapshot', models.JSONField(default=dict)),
                ('snapshot_sha256', models.CharField(max_length=64)),
                ('node_command', models.JSONField(blank=True, default=dict, editable=False)),
                ('node_report', models.JSONField(blank=True, default=dict, editable=False)),
                ('node_report_seq', models.PositiveBigIntegerField(default=0, editable=False)),
                ('latest_metrics', models.JSONField(blank=True, default=dict)),
                ('metrics_samples', models.JSONField(blank=True, default=list)),
                ('reason_code', models.CharField(blank=True, default='', max_length=64)),
                ('reason', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('stop_requested_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_performance_runs', to=settings.AUTH_USER_MODEL)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='runs', to='performance_testing.performancenode')),
                ('plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='runs', to='performance_testing.performanceplan')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_runs', to='projects.project')),
            ],
            options={
                'db_table': 'performance_runs',
                'ordering': ('-created_at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='PerformanceControllerState',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('owner_id', models.CharField(blank=True, default='', max_length=200)),
                ('heartbeat_at', models.DateTimeField(blank=True, null=True)),
                ('lease_until', models.DateTimeField(blank=True, null=True)),
                ('current_run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='performance_testing.performancerun')),
            ],
            options={'db_table': 'performance_controller_state'},
        ),
        migrations.AddConstraint(
            model_name='performancerun',
            constraint=models.UniqueConstraint(fields=('project', 'request_id'), name='perf_run_project_request_unique'),
        ),
        migrations.AddIndex(
            model_name='performancerun',
            index=models.Index(fields=['status', 'created_at'], name='perf_run_status_created'),
        ),
        migrations.AddConstraint(
            model_name='performancecontrollerstate',
            constraint=models.CheckConstraint(check=models.Q(('id', 1)), name='perf_controller_singleton_id'),
        ),
    ]
