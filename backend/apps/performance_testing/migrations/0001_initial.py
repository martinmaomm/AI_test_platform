import uuid

from django.db import migrations, models
import django.db.models.deletion
import performance_testing.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('projects', '0006_retire_app_choices'),
    ]

    operations = [
        migrations.CreateModel(
            name='PerformanceTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('base_url', models.CharField(max_length=2048)),
                ('allowed_methods', models.JSONField(default=performance_testing.models.default_allowed_methods)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_targets', to='projects.project')),
            ],
            options={'db_table': 'performance_targets', 'ordering': ('-created_at', '-id')},
        ),
        migrations.CreateModel(
            name='PerformanceNode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('network_mode', models.CharField(choices=[('lan', 'LAN'), ('public', 'Public')], max_length=10)),
                ('labels', models.JSONField(blank=True, default=dict)),
                ('enrollment_token_digest', models.CharField(blank=True, default='', editable=False, max_length=64)),
                ('enrollment_expires_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('enrollment_consumed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('agent_token_digest', models.CharField(blank=True, default='', editable=False, max_length=64)),
                ('revoked_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('last_seen_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('agent_version', models.CharField(blank=True, default='', editable=False, max_length=32)),
                ('engine_version', models.CharField(blank=True, default='', editable=False, max_length=32)),
                ('protocol_version', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('resources', models.JSONField(blank=True, default=dict, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_nodes', to='projects.project')),
            ],
            options={'db_table': 'performance_nodes', 'ordering': ('-created_at', '-id')},
        ),
        migrations.CreateModel(
            name='PerformancePlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, default='')),
                ('users', models.PositiveIntegerField(default=1)),
                ('spawn_rate', models.FloatField(default=1)),
                ('duration_seconds', models.PositiveIntegerField(default=30)),
                ('wait_seconds', models.FloatField(default=1)),
                ('steps', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_plans', to='projects.project')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='plans', to='performance_testing.performancetarget')),
            ],
            options={'db_table': 'performance_plans', 'ordering': ('-created_at', '-id')},
        ),
        migrations.AddConstraint(
            model_name='performanceplan',
            constraint=models.CheckConstraint(check=models.Q(('users__gte', 1), ('users__lte', 100)), name='perf_plan_users_1_100'),
        ),
        migrations.AddConstraint(
            model_name='performanceplan',
            constraint=models.CheckConstraint(check=models.Q(('spawn_rate__gt', 0), ('spawn_rate__lte', 100)), name='perf_plan_spawn_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='performanceplan',
            constraint=models.CheckConstraint(check=models.Q(('duration_seconds__gte', 1), ('duration_seconds__lte', 600)), name='perf_plan_duration_1_600'),
        ),
        migrations.AddConstraint(
            model_name='performanceplan',
            constraint=models.CheckConstraint(check=models.Q(('wait_seconds__gte', 0.1), ('wait_seconds__lte', 60)), name='perf_plan_wait_01_60'),
        ),
    ]
