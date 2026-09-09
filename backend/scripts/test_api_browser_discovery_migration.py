"""Apply the additive browser-discovery migration to disposable 0018 SQLite."""
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'

    def deny(*args):
        raise RuntimeError('Network is forbidden in migration acceptance')

    with tempfile.TemporaryDirectory(prefix='automation-browser-source-migration-') as temp, patch.object(
        socket.socket, 'connect', deny,
    ), patch.object(socket.socket, 'connect_ex', deny):
        from config import settings as config
        config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(temp) / 'migration.sqlite3')}}
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        import django
        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state([('api_testing', '0018_apiworkspace_scenarios')])
        apps = state.apps
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        user = apps.get_model('users', 'User').objects.create(username='migration-source-owner', email='owner@example.test')
        project = apps.get_model('projects', 'Project').objects.create(name='历史 API 项目')
        spec = apps.get_model('api_testing', 'APISpecification').objects.create(
            project_id=project.pk, created_by_id=user.pk, spec_name='已有 Swagger', status='completed',
            metadata={'paths': {'/health': {'get': {}}}},
        )
        case = apps.get_model('api_testing', 'APITestCase').objects.create(
            title='已有用例', test_case_type='scenario', project_id=project.pk, created_by_id=user.pk,
            script_content='{"teststeps":[]}',
        )
        workspace = apps.get_model('api_testing', 'APIWorkspace').objects.create(
            owner_id=user.pk, project_id=project.pk, spec_id=spec.pk, title='已有工作区',
            saved_case_id=case.pk, draft={'version': 1, 'teststeps': []},
        )
        migration = executor.loader.get_migration('api_testing', '0019_apispecification_source_selection_key_and_more')
        with connection.schema_editor() as editor:
            state = migration.apply(state, editor)
        apps = state.apps
        Spec = apps.get_model('api_testing', 'APISpecification')
        old = Spec.objects.get(pk=spec.pk)
        assert old.spec_type == 'swagger' and old.metadata == {'paths': {'/health': {'get': {}}}}
        assert old.source_task_id is None and old.source_version == 0
        assert apps.get_model('api_testing', 'APITestCase').objects.get(pk=case.pk).script_content == '{"teststeps":[]}'
        assert apps.get_model('api_testing', 'APIWorkspace').objects.get(pk=workspace.pk).saved_case_id == case.pk
        task = apps.get_model('api_testing', 'BrowserDiscoveryTask').objects.create(
            project_id=project.pk, owner_id=user.pk, model_id=1, target_url='https://example.test/',
            description='新探索', api_origin='https://api.example.test', task_id='new-migration-task',
        )
        source = Spec.objects.create(project_id=project.pk, created_by_id=user.pk, spec_name='网页来源',
                                    spec_type='browser_capture', source_task_id=task.pk, source_version=1)
        record = apps.get_model('api_testing', 'BrowserDiscoveryRecord').objects.create(task_id=task.pk, sequence=1, method='GET', path='/health')
        assert record.pk and source.source_task_id == task.pk
        print('0018 -> 0019 passed: existing Swagger, workspace and case preserved; new source/task/record usable; network blocked.')


if __name__ == '__main__':
    main()
