"""Test 0019 and its partial-DDL recovery on a disposable local MariaDB.

Requires a dedicated localhost MariaDB container, not the platform/NAS DB.
Creates one random test database and removes ONLY that database in finally.
No settings/env credentials are read for the connection; nothing is migrated
on the configured platform database. Run each --case in a fresh process.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import re
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--case', choices=['fresh', 'partial', 'conflict', 'duplicate', 'orphan', 'mismatch'], required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use the dedicated container high port on 127.0.0.1')
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    import pymysql

    db_name = f'aits_bd19_test_{args.case}_{uuid.uuid4().hex[:12]}'
    assert re.fullmatch(r'aits_bd19_test_(fresh|partial|conflict|duplicate|orphan|mismatch)_[0-9a-f]{12}', db_name)
    admin = pymysql.connect(host='127.0.0.1', port=args.port, user='root', password='', autocommit=True)
    created = False
    connection = None
    report = {'case': args.case, 'database': db_name}
    try:
        with admin.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            report['server'] = cursor.fetchone()[0]
            assert 'MariaDB' in report['server']
            cursor.execute(f'CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            created = True

        from aits_backend import settings as config
        config.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.mysql', 'NAME': db_name,
            'HOST': '127.0.0.1', 'PORT': args.port, 'USER': 'root', 'PASSWORD': '',
            'OPTIONS': {'charset': 'utf8mb4'},
        }}
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        import django
        django.setup()
        from django.core.management import call_command, CommandError
        from django.db import connection as db_connection, OperationalError
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.operations.models import AddIndex
        from django.db.migrations.recorder import MigrationRecorder
        connection = db_connection
        predecessor = ('api_testing', '0018_apiworkspace_scenarios')
        target = ('api_testing', '0019_apispecification_source_selection_key_and_more')
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state([predecessor])
        with connection.schema_editor() as editor:
            for model in state.apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        recorder = MigrationRecorder(connection)
        recorder.ensure_schema()
        for app, migration_name in executor.loader.graph.forwards_plan(predecessor):
            recorder.record_applied(app, migration_name)

        apps = state.apps
        owner = apps.get_model('users', 'User').objects.create(username='recovery-owner', email='owner@example.test')
        project = apps.get_model('projects', 'Project').objects.create(name='Existing project')
        spec = apps.get_model('api_testing', 'APISpecification').objects.create(
            project_id=project.pk, created_by_id=owner.pk, spec_name='Existing Swagger',
            status='completed', metadata={'preserve': '原有资料'},
        )
        case = apps.get_model('api_testing', 'APITestCase').objects.create(
            project_id=project.pk, created_by_id=owner.pk, title='Existing case',
            script_content='{"preserve":true}', test_case_type='scenario',
        )
        migration = executor.loader.get_migration(*target)
        expected = executor.loader.project_state([target])
        if args.case == 'fresh':
            MigrationExecutor(connection).migrate([target])
        else:
            broken = deepcopy(migration)
            bad_indexes = [op for op in broken.operations if isinstance(op, AddIndex) and op.index.name == 'api_browser_task_id_8c01c0_idx']
            assert len(bad_indexes) == 1
            bad_indexes[0].index.fields = ['task', 'method', 'path']
            bad_indexes[0].index.fields_orders = [('task', ''), ('method', ''), ('path', '')]
            try:
                with connection.schema_editor() as editor:
                    broken.apply(state.clone(), editor)
            except OperationalError as error:
                assert error.args[0] == 1071, error.args[0]
                report['original_error_reproduced'] = 1071
            else:
                raise AssertionError('The original overlong index unexpectedly succeeded')
            assert target not in recorder.applied_migrations()

        Task = expected.apps.get_model('api_testing', 'BrowserDiscoveryTask')
        Record = expected.apps.get_model('api_testing', 'BrowserDiscoveryRecord')
        task = Task.objects.create(
            project_id=project.pk, owner_id=owner.pk, model_id=1, target_url='https://example.test',
            description='Existing discovery must survive', task_id='recovery-evidence',
        )
        path = '/' + '路' * 999
        record = Record.objects.create(task_id=task.pk, sequence=1, method='GET', path=path)
        if args.case == 'conflict':
            with connection.cursor() as cursor:
                cursor.execute('CREATE INDEX api_browser_task_id_8c01c0_idx ON api_browser_discovery_records (method)')
        elif args.case == 'duplicate':
            Record.objects.create(task_id=task.pk, sequence=1, method='GET', path='/duplicate')
        elif args.case == 'orphan':
            task.owner_id = owner.pk + 1000000
            task.save(update_fields=['owner_id'])
        elif args.case == 'mismatch':
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE api_browser_discovery_records MODIFY method varchar(9) NOT NULL')

        must_refuse = args.case in {'conflict', 'duplicate', 'orphan', 'mismatch'}

        def run_repair(apply=False):
            statements = []
            def observe(execute, sql, params, many, context):
                statements.append(sql.lstrip().split(None, 1)[0].upper())
                return execute(sql, params, many, context)
            try:
                with connection.execute_wrapper(observe):
                    call_command('repair_api_browser_discovery_migration', apply=apply, stdout=StringIO(), stderr=StringIO())
            finally:
                assert not {'DROP', 'TRUNCATE', 'DELETE', 'UPDATE'}.intersection(statements), statements
                if not apply or must_refuse:
                    assert not {'CREATE', 'ALTER', 'INSERT'}.intersection(statements), statements
            return statements

        if must_refuse:
            for apply in (False, True):
                try:
                    run_repair(apply=apply)
                except CommandError as error:
                    expected_reason = {
                        'conflict': '索引 api_browser_task_id_8c01c0_idx',
                        'duplicate': '重复键',
                        'orphan': '孤儿引用',
                        'mismatch': 'method',
                    }[args.case]
                    assert expected_reason in str(error), str(error)
                else:
                    raise AssertionError('Conflicting schema/data must stop recovery')
            assert target not in recorder.applied_migrations()
            report['conflicting_schema_or_data_refused'] = True
        else:
            run_repair()
            was_recorded = target in recorder.applied_migrations()
            assert was_recorded == (args.case == 'fresh')
            run_repair(apply=True)
            assert target in recorder.applied_migrations()
            statements = run_repair(apply=True)
            assert not {'CREATE', 'ALTER', 'INSERT'}.intersection(statements), statements
            executor = MigrationExecutor(connection)
            assert executor.migration_plan([target]) == []
            executor.migrate([target])
            with connection.cursor() as cursor:
                record_indexes = connection.introspection.get_constraints(cursor, 'api_browser_discovery_records')
                assert record_indexes['api_browser_task_id_8c01c0_idx']['columns'] == ['task_id', 'method']
                assert record_indexes['unique_browser_discovery_record_sequence']['unique']
                for table, count in [('api_browser_discovery_tasks', 2), ('api_browser_discovery_records', 1), ('api_browser_discovery_handoffs', 3)]:
                    indexes = connection.introspection.get_constraints(cursor, table)
                    assert sum(bool(item.get('foreign_key')) for item in indexes.values()) == count, table
            report['migration_and_repair_idempotent'] = True

        assert Record.objects.get(pk=record.pk).path == path
        assert Task.objects.get(pk=task.pk).description == 'Existing discovery must survive'
        assert expected.apps.get_model('api_testing', 'APISpecification').objects.get(pk=spec.pk).metadata == {'preserve': '原有资料'}
        assert expected.apps.get_model('api_testing', 'APITestCase').objects.get(pk=case.pk).script_content == '{"preserve":true}'
        report.update(old_and_new_data_preserved=True, full_1000_character_path_preserved=True, passed=True)
    finally:
        if connection is not None:
            connection.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP DATABASE `{db_name}`')
            report['isolated_database_removed'] = True
        admin.close()
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
