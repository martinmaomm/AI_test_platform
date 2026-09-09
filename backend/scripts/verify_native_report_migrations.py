"""Round-trip report migrations using ONLY a disposable local MariaDB database.

Database is fixed to automation_reports_verify, host to loopback, and credentials are
test-only. The caller must create and later remove its temporary container.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    from config import settings as config
    config.DATABASES = {'default': {
        'ENGINE': 'django.db.backends.mysql', 'NAME': 'automation_reports_verify',
        'USER': 'root', 'PASSWORD': 'offline-migration-only',
        'HOST': '127.0.0.1', 'PORT': args.port,
        'OPTIONS': {'charset': 'utf8mb4', 'connect_timeout': 5},
    }}
    config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    config.CELERY_BROKER_URL = 'memory://'
    config.CELERY_RESULT_BACKEND = 'cache+memory://'
    config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
    with tempfile.TemporaryDirectory(prefix='automation-report-migration-media-') as media:
        config.MEDIA_ROOT = media
        import django
        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = {
            'web_testing': '0020_remove_webui_environment_dependencies',
            'api_testing': '0013_alter_apitestexecution_exec_type',
            'scheduled_tasks': '0008_scheduledtask_environment_nullable',
        }
        baseline = [(app, before.get(app, name)) for app, name in latest]
        executor.migrate(baseline)
        apps = executor.loader.project_state(baseline).apps
        user = apps.get_model('users', 'User').objects.create(username='migration-fixture', email='migration@example.test', password='!')
        project = apps.get_model('projects', 'Project').objects.create(name='迁移隔离项目', project_type='web', created_by_id=user.pk, owner_id=user.pk)
        run = apps.get_model('web_testing', 'WebUITestExecution').objects.create(
            project_id=project.pk, executor_id=user.pk, exec_type='suite', name='保留的报告', status='passed', start_time=timezone.now(),
        )
        detail = apps.get_model('web_testing', 'WebUITestSuiteExecutionDetail').objects.create(
            execution_id=run.pk, total_cases=1, passed_cases=1, log='保留的运行日志', allure_report='/old/report/index.html',
        )
        item = apps.get_model('web_testing', 'WebUITestSuiteCaseExecution').objects.create(
            suite_execution_id=detail.pk, name='保留的用例快照', status='passed', log='验证通过', screenshot_path='fixture.png',
        )
        picture = Path(media) / 'fixture.png'
        picture.write_bytes(b'fixture-preservation-check')
        for direction, targets in [('upgrade', latest), ('rollback', baseline), ('upgrade-again', latest)]:
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
            state = executor.loader.project_state(targets).apps
            d = state.get_model('web_testing', 'WebUITestSuiteExecutionDetail').objects.get(pk=detail.pk)
            c = state.get_model('web_testing', 'WebUITestSuiteCaseExecution').objects.get(pk=item.pk)
            assert d.log == '保留的运行日志' and d.passed_cases == 1
            assert c.log == '验证通过' and c.name == '保留的用例快照' and c.screenshot_path == 'fixture.png'
            assert picture.read_bytes() == b'fixture-preservation-check'
            fields = {field.name for field in d._meta.fields}
            assert ('allure_report' in fields) == (direction == 'rollback')
            print(f'PASS {direction}: original records, logs and screenshot paths preserved')
        from django.core.management import call_command
        call_command('makemigrations', 'api_testing', 'web_testing', 'scheduled_tasks', dry_run=True, check=True, verbosity=1)
        connection.close()


if __name__ == '__main__':
    main()
