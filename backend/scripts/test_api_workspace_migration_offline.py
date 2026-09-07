"""Apply API workspace migrations to a disposable SQLite 0014 state only.

The script deliberately materializes the historical state instead of migrating
the developer database. Socket guards ensure neither NAS, Redis nor any other
external service can be contacted while the migrations are exercised.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['AITS_OFFLINE_TEST_NETWORK'] = 'blocked'

    def denied(_socket, _address):
        raise RuntimeError('External connections forbidden during migration verification')

    with tempfile.TemporaryDirectory(prefix='aits-api-workspace-migration-') as temp, patch.object(
        socket.socket, 'connect', denied
    ), patch.object(socket.socket, 'connect_ex', denied):
        from aits_backend import settings as config

        config.DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(Path(temp) / 'migration.sqlite3'),
            }
        }
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}

        import django

        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        previous = ('api_testing', '0014_platform_native_reports')
        workspace_migration = ('api_testing', '0015_apiworkspace')
        target = ('api_testing', '0016_api_execution_snapshot_suite_variables')
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state([previous])
        apps = state.apps
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)

        user = apps.get_model('users', 'User').objects.create(
            username='migration-only', email='migration@example.test'
        )
        project = apps.get_model('projects', 'Project').objects.create(
            name='API 工作区历史记录保留验收'
        )
        case = apps.get_model('api_testing', 'APITestCase').objects.create(
            title='既有场景用例', test_case_type='scenario', project_id=project.pk,
            created_by_id=user.pk, script_content='{"version": 1, "teststeps": []}',
        )
        execution = apps.get_model('api_testing', 'APITestExecution').objects.create(
            name='既有执行记录', exec_type='case', status='failed', project_id=project.pk,
            executor_id=user.pk, execution_log='保留的历史失败日志',
        )
        suite = apps.get_model('api_testing', 'APITestSuite').objects.create(
            name='既有顺序套件', user_id=user.pk, project_id=project.pk,
        )

        with connection.schema_editor() as editor:
            state = executor.loader.get_migration(*workspace_migration).apply(state, editor)
        apps = state.apps
        Case = apps.get_model('api_testing', 'APITestCase')
        Execution = apps.get_model('api_testing', 'APITestExecution')
        Workspace = apps.get_model('api_testing', 'APIWorkspace')
        assert Case.objects.get(pk=case.pk).script_content == '{"version": 1, "teststeps": []}'
        assert Execution.objects.get(pk=execution.pk).execution_log == '保留的历史失败日志'

        workspace = Workspace.objects.create(
            title='新工作区', owner_id=user.pk, project_id=project.pk, saved_case_id=case.pk,
            model_id=7, endpoint_ids=[11, 12], revision=3, status='ready',
            draft={
                'version': 1,
                'config': {'name': '迁移验收', 'base_url': 'https://api.example.test', 'variables': {}, 'verify': True},
                'teststeps': [],
            },
            error='', messages=[{'role': 'user', 'content': '保留草稿'}],
            candidate={'version': 1}, debug_result={'status': 'passed'},
            debug_snapshot={'request': {'url': '/health'}}, debug_revision=3, task_id='offline-task',
        )
        workspace = Workspace.objects.get(pk=workspace.pk)
        assert workspace.title == '新工作区'
        assert workspace.owner_id == user.pk and workspace.project_id == project.pk
        assert workspace.saved_case_id == case.pk
        assert workspace.model_id == 7
        assert workspace.endpoint_ids == [11, 12]
        assert workspace.draft['config']['base_url'] == 'https://api.example.test'
        assert workspace.revision == 3 and workspace.status == 'ready' and workspace.error == ''
        assert workspace.messages == [{'role': 'user', 'content': '保留草稿'}]
        assert workspace.candidate == {'version': 1}
        assert workspace.debug_result == {'status': 'passed'}
        assert workspace.debug_snapshot == {'request': {'url': '/health'}}
        assert workspace.debug_revision == 3 and workspace.saved_case_updated_at is None
        assert workspace.task_id == 'offline-task'
        assert workspace.created_at is not None and workspace.updated_at is not None

        with connection.schema_editor() as editor:
            state = executor.loader.get_migration(*target).apply(state, editor)
        apps = state.apps
        Case = apps.get_model('api_testing', 'APITestCase')
        Execution = apps.get_model('api_testing', 'APITestExecution')
        Suite = apps.get_model('api_testing', 'APITestSuite')
        Workspace = apps.get_model('api_testing', 'APIWorkspace')
        assert Case.objects.get(pk=case.pk).title == '既有场景用例'
        assert Execution.objects.get(pk=execution.pk).execution_log == '保留的历史失败日志'
        assert Execution.objects.get(pk=execution.pk).input_snapshot == {}
        assert Suite.objects.get(pk=suite.pk).variables == {}

        execution_after = Execution.objects.get(pk=execution.pk)
        execution_after.input_snapshot = {'frozen': {'base_url': 'https://api.example.test'}}
        execution_after.save(update_fields=['input_snapshot'])
        suite_after = Suite.objects.get(pk=suite.pk)
        suite_after.variables = {'tenant': 'offline'}
        suite_after.save(update_fields=['variables'])
        assert Execution.objects.get(pk=execution.pk).input_snapshot == {
            'frozen': {'base_url': 'https://api.example.test'}
        }
        assert Suite.objects.get(pk=suite.pk).variables == {'tenant': 'offline'}
        assert Workspace.objects.get(pk=workspace.pk).debug_result == {'status': 'passed'}

        # Exercise the actual additive generation migration without contacting
        # the configured database. Keep its file name discoverable during work.
        generation_migrations = [
            key for key in executor.loader.disk_migrations
            if key[0] == 'api_testing' and key[1].startswith('0017_')
        ]
        assert len(generation_migrations) == 1, generation_migrations
        with connection.schema_editor() as editor:
            state = executor.loader.get_migration(*generation_migrations[0]).apply(state, editor)
        apps = state.apps
        Workspace = apps.get_model('api_testing', 'APIWorkspace')
        Spec = apps.get_model('api_testing', 'APISpecification')
        migrated = Workspace.objects.get(pk=workspace.pk)
        assert migrated.spec_id is None and migrated.generation == {}
        spec = Spec.objects.create(project_id=project.pk, created_by_id=user.pk, spec_name='迁移验证规范')
        migrated.spec_id = spec.pk
        migrated.generation = {'status': 'needs_review', 'rounds': [{'attempt': 1, 'result': {'success': False}}]}
        migrated.save(update_fields=['spec', 'generation'])
        assert Workspace.objects.get(pk=workspace.pk).spec_id == spec.pk
        assert Workspace.objects.get(pk=workspace.pk).generation['rounds'][0]['attempt'] == 1
        assert apps.get_model('api_testing', 'APITestCase').objects.get(pk=case.pk).title == '既有场景用例'
        connection.close()

    print(
        'PASS: actual 0015/0016/0017 SQLite migrations preserve API case/execution data; '
        'workspace spec/generation, suite.variables and input_snapshot are readable and writable; NAS not accessed'
    )


if __name__ == '__main__':
    main()
