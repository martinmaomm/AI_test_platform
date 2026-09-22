"""Verify the performance multi-node migration on a disposable MariaDB.

The caller must provide the high loopback port of an isolated temporary
MariaDB container. This script creates and removes one random database only;
it never reads the platform database credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use the dedicated container high port on 127.0.0.1')

    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'

    import pymysql

    database_name = f'automation_perf_multi_{uuid.uuid4().hex[:12]}'
    assert re.fullmatch(r'automation_perf_multi_[0-9a-f]{12}', database_name)
    admin = pymysql.connect(
        host='127.0.0.1', port=args.port, user='root', password='', autocommit=True,
    )
    created = False
    database_connection = None
    try:
        with admin.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            server_version = cursor.fetchone()[0]
            if 'MariaDB' not in server_version:
                raise AssertionError(f'Expected MariaDB, got {server_version}')
            cursor.execute(
                f'CREATE DATABASE `{database_name}` '
                'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
            )
            created = True

        from config import settings as config
        config.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': database_name,
            'HOST': '127.0.0.1',
            'PORT': args.port,
            'USER': 'root',
            'PASSWORD': '',
            'OPTIONS': {'charset': 'utf8mb4'},
        }}
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}

        import django
        django.setup()
        from django.db import IntegrityError, connection, transaction
        from django.db.migrations.exceptions import IrreversibleError
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.recorder import MigrationRecorder

        database_connection = connection
        predecessor = ('performance_testing', '0004_plan_contract_v2')
        target = ('performance_testing', '0005_performancerunnode_and_more')
        executor = MigrationExecutor(connection)
        executor.migrate([predecessor])
        old_apps = executor.loader.project_state([predecessor]).apps

        User = old_apps.get_model('users', 'User')
        Project = old_apps.get_model('projects', 'Project')
        Target = old_apps.get_model('performance_testing', 'PerformanceTarget')
        Plan = old_apps.get_model('performance_testing', 'PerformancePlan')
        Node = old_apps.get_model('performance_testing', 'PerformanceNode')
        Run = old_apps.get_model('performance_testing', 'PerformanceRun')
        user = User.objects.create(
            username='migration-owner', email='migration-owner@example.test', password='x',
        )
        project = Project.objects.create(
            name='Performance migration fixture', project_type='perf', created_by_id=user.pk,
        )
        target_row = Target.objects.create(
            project_id=project.pk, name='Approved target',
            base_url='https://example.test', allowed_methods=['GET'],
        )
        plan = Plan.objects.create(
            project_id=project.pk, target_id=target_row.pk, name='Frozen plan',
            users=7, spawn_rate=2, duration_seconds=15, wait_seconds=1, steps=[],
        )
        node = Node.objects.create(
            project_id=project.pk, name='Legacy node', network_mode='lan',
        )
        statuses = (
            'queued', 'preparing', 'running', 'stopping',
            'completed', 'failed', 'cancelled', 'incomplete',
        )
        source = {}
        for sequence, status in enumerate(statuses, start=1):
            run_id = uuid.uuid4()
            snapshot = {'users': sequence, 'legacy': status}
            digest = f'{sequence:064x}'
            run = Run.objects.create(
                id=run_id,
                project_id=project.pk,
                plan_id=plan.pk,
                node_id=node.pk,
                created_by_id=user.pk,
                request_id=uuid.uuid4(),
                mode='validation' if status == 'completed' else 'load',
                validation_key=f'{sequence:064x}',
                status=status,
                snapshot=snapshot,
                snapshot_sha256=digest,
                node_command={'type': 'prepare', 'marker': status},
                node_report={'state': 'stopped', 'marker': status},
                node_report_seq=sequence,
                latest_metrics={'requests': sequence},
                metrics_samples=[{'metrics': {'requests': sequence}}],
                reason_code=f'reason_{status}',
                reason=f'evidence {status}',
            )
            source[str(run.pk)] = {
                'node_id': node.pk,
                'snapshot': snapshot,
                'snapshot_sha256': digest,
                'node_command': run.node_command,
                'node_report': run.node_report,
                'node_report_seq': sequence,
                'latest_metrics': run.latest_metrics,
                'metrics_samples': run.metrics_samples,
                'validation_key': run.validation_key,
                'status': status,
            }

        MigrationExecutor(connection).migrate([target])
        migrated_executor = MigrationExecutor(connection)
        new_apps = migrated_executor.loader.project_state([target]).apps
        NewPlan = new_apps.get_model('performance_testing', 'PerformancePlan')
        NewRun = new_apps.get_model('performance_testing', 'PerformanceRun')
        Participant = new_apps.get_model('performance_testing', 'PerformanceRunNode')
        expected_status = {
            'queued': 'queued', 'preparing': 'preparing', 'running': 'running',
            'stopping': 'stopping', 'completed': 'stopped', 'failed': 'failed',
            'cancelled': 'stopped', 'incomplete': 'lost',
        }
        if NewRun.objects.count() != len(statuses) or Participant.objects.count() != len(statuses):
            raise AssertionError('Run or participant count changed during migration')
        for run in NewRun.objects.order_by('id'):
            before = source[str(run.pk)]
            member = Participant.objects.get(run_id=run.pk)
            assert run.snapshot == before['snapshot']
            assert run.snapshot_sha256 == before['snapshot_sha256']
            assert member.node_id == before['node_id']
            assert member.assigned_users == before['snapshot']['users']
            assert member.validation_key == before['validation_key']
            assert member.validation_run_id == (run.pk if run.mode == 'validation' else None)
            assert member.status == expected_status[before['status']]
            assert member.node_command == before['node_command']
            assert member.node_report == before['node_report']
            assert member.node_report_seq == before['node_report_seq']
            assert member.latest_metrics == before['latest_metrics']
            assert member.metrics_samples == before['metrics_samples']
            assert member.node_name == ''
            assert member.node_agent_version == ''
            assert member.node_protocol_version is None
            assert member.ready_at is None and member.stopped_at is None

        NewPlan.objects.filter(pk=plan.pk).update(users=1000)
        try:
            with transaction.atomic():
                NewPlan.objects.filter(pk=plan.pk).update(users=1001)
        except IntegrityError:
            pass
        else:
            raise AssertionError('The users <= 1000 database constraint was not enforced')

        first_run = NewRun.objects.order_by('id').first()
        try:
            with transaction.atomic():
                Participant.objects.create(
                    run_id=first_run.pk, node_id=node.pk, assigned_users=1,
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError('The unique (run, node) constraint was not enforced')

        try:
            MigrationExecutor(connection).migrate([predecessor])
        except IrreversibleError:
            pass
        else:
            raise AssertionError('Reverse migration must be blocked before discarding evidence')
        applied = MigrationRecorder(connection).applied_migrations()
        assert target in applied
        assert Participant.objects.count() == len(statuses)

        print(json.dumps({
            'server': server_version,
            'historical_runs': len(statuses),
            'participants': Participant.objects.count(),
            'statuses': list(statuses),
            'snapshot_and_sha_preserved': True,
            'unknown_snapshot_fields_blank': True,
            'users_1000_constraint': True,
            'run_node_unique_constraint': True,
            'reverse_blocked': True,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        if database_connection is not None:
            database_connection.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS `{database_name}`')
        admin.close()


if __name__ == '__main__':
    raise SystemExit(main())
