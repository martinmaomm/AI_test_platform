"""Check additive endpoint-target persistence on disposable SQLite only."""
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
        raise AssertionError('Migration acceptance cannot access external services')

    with tempfile.TemporaryDirectory(prefix='endpoint-target-migration-') as temp, patch.object(
        socket.socket, 'connect', deny,
    ), patch.object(socket.socket, 'connect_ex', deny):
        from config import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(temp) / 'test.sqlite3')}}
        settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        settings.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        settings.CELERY_BROKER_URL = 'memory://'
        settings.CELERY_RESULT_BACKEND = 'cache+memory://'
        settings.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        import django
        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state([('api_testing', '0019_apispecification_source_selection_key_and_more')])
        apps = state.apps
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        user = apps.get_model('users', 'User').objects.create(username='migration-fixture')
        project = apps.get_model('projects', 'Project').objects.create(name='迁移隔离项目')
        spec = apps.get_model('api_testing', 'APISpecification').objects.create(
            spec_name='已有文档', project_id=project.pk, created_by_id=user.pk, status='completed')
        endpoint = apps.get_model('api_testing', 'APIEndpoint').objects.create(spec_id=spec.pk, method='GET', path='/health')
        draft = {'config': {'name': '保留的草稿'}, 'teststeps': [{'name': '原步骤', 'endpoint_id': endpoint.pk}]}
        workspace = apps.get_model('api_testing', 'APIWorkspace').objects.create(
            owner_id=user.pk, project_id=project.pk, spec_id=spec.pk,
            title='保留的工作区', draft=draft, generation={'summary': '保留的结果'},
        )
        additions = [key for key in executor.loader.disk_migrations if key[0] == 'api_testing' and key[1].startswith('0020_')]
        assert len(additions) == 1, additions
        with connection.schema_editor() as editor:
            state = executor.loader.get_migration(*additions[0]).apply(state, editor)
        Workspace = state.apps.get_model('api_testing', 'APIWorkspace')
        migrated = Workspace.objects.get(pk=workspace.pk)
        assert migrated.target_endpoint_id is None
        assert migrated.draft == draft and migrated.generation == {'summary': '保留的结果'}
        migrated.target_endpoint_id = endpoint.pk
        migrated.save(update_fields=['target_endpoint_id'])
        state.apps.get_model('api_testing', 'APIEndpoint').objects.get(pk=endpoint.pk).delete()
        assert Workspace.objects.get(pk=migrated.pk).target_endpoint_id == endpoint.pk
        assert Workspace.objects.get(pk=migrated.pk).draft == draft
        connection.close()
    print('PASS: 0019 → 0020 adds nullable target; existing drafts survive and deleted endpoint does not silently clear target intent.')


if __name__ == '__main__':
    main()
