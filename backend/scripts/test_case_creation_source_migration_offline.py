"""Apply both creation-source migrations to disposable SQLite with no external I/O."""

from datetime import timedelta
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch

from test_platform_reports_browser import loopback_only


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['OFFLINE_TEST_NETWORK'] = 'blocked'
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    with tempfile.TemporaryDirectory(prefix='case-source-migration-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(original_connect),
    ), patch.object(
        socket.socket, 'connect_ex', loopback_only(original_connect_ex),
    ):
        from config import settings

        settings.DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(Path(temp) / 'migration.sqlite3'),
            }
        }
        settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        settings.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        settings.CELERY_BROKER_URL = 'memory://'
        settings.CELERY_RESULT_BACKEND = 'cache+memory://'
        settings.LOGGING = {'version': 1, 'disable_existing_loggers': True}

        import django

        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone

        previous = [
            ('api_testing', '0020_apiworkspace_target_endpoint_id'),
            ('web_testing', '0022_webuitestcaseexecutiondetail_execution_options_and_more'),
        ]
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state(previous)
        old_apps = state.apps
        # Materialize the exact historical state directly. Replaying the full
        # repository history on SQLite is blocked by an unrelated retired
        # WebUITestCaseGeneration index removal; the product migration checks
        # in this repository use the same historical-state technique.
        with connection.schema_editor() as editor:
            for model in old_apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)

        User = old_apps.get_model('users', 'User')
        Project = old_apps.get_model('projects', 'Project')
        APICase = old_apps.get_model('api_testing', 'APITestCase')
        Workspace = old_apps.get_model('api_testing', 'APIWorkspace')
        WebCase = old_apps.get_model('web_testing', 'WebUITestCase')
        Generation = old_apps.get_model('web_testing', 'WebUIScriptGeneration')

        user = User.objects.create(username='migration-source-fixture')
        api_project = Project.objects.create(
            name='API migration source', project_type='api', created_by_id=user.pk,
        )
        web_project = Project.objects.create(
            name='Web migration source', project_type='web', created_by_id=user.pk,
        )
        base = timezone.now() - timedelta(days=1)
        api_draft = {
            'version': 1,
            'config': {'name': 'source migration', 'base_url': '', 'variables': {}},
            'teststeps': [{'name': 'health', 'request': {'method': 'GET', 'url': '/health'}}],
        }

        # Proven API creation: the workspace and its completed AI generation
        # both predate the linked case.
        api_ai_workspace = Workspace.objects.create(
            project_id=api_project.pk, owner_id=user.pk, draft=api_draft, revision=1,
            generation={'adopted_revision': 1, 'finished_at': (base + timedelta(seconds=1)).isoformat()},
        )
        api_ai = APICase.objects.create(
            project_id=api_project.pk, created_by_id=user.pk, title='proven API AI',
            test_case_type='scenario', script_content='{"ai": true}',
        )
        Workspace.objects.filter(pk=api_ai_workspace.pk).update(
            created_at=base, saved_case_id=api_ai.pk,
        )
        APICase.objects.filter(pk=api_ai.pk).update(
            created_at=base + timedelta(seconds=2), updated_at=base + timedelta(seconds=4),
        )

        # Counterexample: this workspace existed first, saved a manual case,
        # then adopted AI later.  finished_at after case creation must keep it unknown.
        api_late_workspace = Workspace.objects.create(
            project_id=api_project.pk, owner_id=user.pk, draft=api_draft, revision=3,
            generation={'adopted_revision': 3, 'finished_at': (base + timedelta(seconds=8)).isoformat()},
        )
        api_late = APICase.objects.create(
            project_id=api_project.pk, created_by_id=user.pk, title='API later AI overwrite',
            test_case_type='scenario', script_content='{"manual_first": true}',
        )
        Workspace.objects.filter(pk=api_late_workspace.pk).update(
            created_at=base + timedelta(seconds=5), saved_case_id=api_late.pk,
        )
        APICase.objects.filter(pk=api_late.pk).update(
            created_at=base + timedelta(seconds=6), updated_at=base + timedelta(seconds=9),
        )
        api_missing_finish = APICase.objects.create(
            project_id=api_project.pk, created_by_id=user.pk, title='API missing finish',
            test_case_type='scenario', script_content='{"unknown": true}',
        )
        missing_workspace = Workspace.objects.create(
            project_id=api_project.pk, owner_id=user.pk, saved_case_id=api_missing_finish.pk,
            draft=api_draft, revision=1, generation={'adopted_revision': 1},
        )
        Workspace.objects.filter(pk=missing_workspace.pk).update(created_at=base)
        APICase.objects.filter(pk=api_missing_finish.pk).update(
            created_at=base + timedelta(seconds=2), updated_at=base + timedelta(seconds=10),
        )

        web_script = "async def run(page):\n    return None\n"
        web_ai_generation = Generation.objects.create(
            project_id=web_project.pk, user_id=user.pk, status='ready', script_draft=web_script,
        )
        web_ai = WebCase.objects.create(
            project_id=web_project.pk, user_id=user.pk, title='proven Web AI',
            description='later manually edited', variables=[{'name': 'A', 'value': '1'}],
            test_script_content=web_script, script_source='manual', script_status='ready',
        )
        Generation.objects.filter(pk=web_ai_generation.pk).update(
            created_at=base, test_case_id=web_ai.pk,
        )
        WebCase.objects.filter(pk=web_ai.pk).update(
            created_at=base + timedelta(seconds=1), updated_at=base + timedelta(seconds=11),
        )

        # Counterexample: a generation linked after a manual case may only be
        # an overwrite, so it must not establish initial AI provenance.
        web_manual = WebCase.objects.create(
            project_id=web_project.pk, user_id=user.pk, title='Web later AI overwrite',
            description='manual first', variables=[{'name': 'B', 'value': '2'}],
            test_script_content=web_script, script_source='mcp_exploration', script_status='ready',
        )
        web_late_generation = Generation.objects.create(
            project_id=web_project.pk, user_id=user.pk, status='ready',
            script_draft=web_script, test_case_id=web_manual.pk,
        )
        WebCase.objects.filter(pk=web_manual.pk).update(
            created_at=base, updated_at=base + timedelta(seconds=12),
        )
        Generation.objects.filter(pk=web_late_generation.pk).update(
            created_at=base + timedelta(seconds=1),
        )
        web_source_only = WebCase.objects.create(
            project_id=web_project.pk, user_id=user.pk, title='Web source only',
            description='no linked generation', variables=[{'name': 'C', 'value': '3'}],
            test_script_content=web_script, script_source='mcp_exploration', script_status='ready',
        )
        WebCase.objects.filter(pk=web_source_only.pk).update(
            created_at=base, updated_at=base + timedelta(seconds=13),
        )

        api_before = {
            item.pk: (item.script_content, item.updated_at)
            for item in APICase.objects.filter(pk__in=[api_ai.pk, api_late.pk, api_missing_finish.pk])
        }
        web_before = {
            item.pk: (item.test_script_content, item.variables, item.script_source, item.updated_at)
            for item in WebCase.objects.filter(pk__in=[web_ai.pk, web_manual.pk, web_source_only.pk])
        }

        targets = [
            ('api_testing', '0021_apitestcase_creation_source'),
            ('web_testing', '0023_webuitestcase_creation_source'),
        ]
        # Execute each real Migration object so all three operations run
        # against the disposable database, while preserving the combined app
        # state between the independent API and WebUI migration branches.
        for target in targets:
            with connection.schema_editor() as editor:
                state = executor.loader.get_migration(*target).apply(state, editor)
        new_apps = state.apps
        APICase = new_apps.get_model('api_testing', 'APITestCase')
        WebCase = new_apps.get_model('web_testing', 'WebUITestCase')

        assert APICase.objects.get(pk=api_ai.pk).creation_source == 'ai'
        assert APICase.objects.get(pk=api_late.pk).creation_source == 'unknown'
        assert APICase.objects.get(pk=api_missing_finish.pk).creation_source == 'unknown'
        assert WebCase.objects.get(pk=web_ai.pk).creation_source == 'ai'
        assert WebCase.objects.get(pk=web_manual.pk).creation_source == 'unknown'
        assert WebCase.objects.get(pk=web_source_only.pk).creation_source == 'unknown'

        api_after = {
            item.pk: (item.script_content, item.updated_at)
            for item in APICase.objects.filter(pk__in=api_before)
        }
        web_after = {
            item.pk: (item.test_script_content, item.variables, item.script_source, item.updated_at)
            for item in WebCase.objects.filter(pk__in=web_before)
        }
        assert api_after == api_before, (api_before, api_after)
        assert web_after == web_before, (web_before, web_after)

        new_api = APICase.objects.create(
            project_id=api_project.pk, created_by_id=user.pk, title='new API manual',
            test_case_type='scenario', script_content='{}',
        )
        new_web = WebCase.objects.create(
            project_id=web_project.pk, user_id=user.pk, title='new Web manual',
            description='new default',
        )
        assert new_api.creation_source == 'manual'
        assert new_web.creation_source == 'manual'
        connection.close()

    print(
        'PASS: 0021/0023 AddField unknown -> evidence backfill -> AlterField manual; '
        'late AI overwrites stay unknown and scripts/variables/script_source/updated_at are unchanged.'
    )


if __name__ == '__main__':
    main()
