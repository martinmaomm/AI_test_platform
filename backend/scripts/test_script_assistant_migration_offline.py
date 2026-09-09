"""Apply 0022 to a materialized 0021 state in disposable SQLite, never the NAS.

This isolates the new migration from unrelated historical SQLite migration
incompatibilities. It does not assert a clean-install or MariaDB migration run.
"""

from pathlib import Path
import os
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / "apps")]
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"

    def denied(_socket, _address):
        raise RuntimeError(
            "External connections forbidden during migration verification"
        )

    with tempfile.TemporaryDirectory(
        prefix="automation-assistant-migration-"
    ) as temp, patch.object(socket.socket, "connect", denied), patch.object(
        socket.socket, "connect_ex", denied
    ):
        from config import settings as config

        config.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(Path(temp) / "migration.sqlite3"),
            }
        }
        config.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        }
        config.CHANNEL_LAYERS = {
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
        config.CELERY_BROKER_URL = "memory://"
        config.CELERY_RESULT_BACKEND = "cache+memory://"
        config.LOGGING = {"version": 1, "disable_existing_loggers": True}
        import django

        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        previous = ("web_testing", "0021_native_execution_reports")
        target = (
            "web_testing",
            "0022_webuitestcaseexecutiondetail_execution_options_and_more",
        )
        executor = MigrationExecutor(connection)
        state = executor.loader.project_state([previous])
        apps = state.apps
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        user = apps.get_model("users", "User").objects.create(
            username="migration-only", email="migration@example.test"
        )
        project = apps.get_model("projects", "Project").objects.create(
            name="历史记录保留验收", created_by_id=user.pk, project_type="web"
        )
        case = apps.get_model("web_testing", "WebUITestCase").objects.create(
            title="历史脚本",
            user_id=user.pk,
            project_id=project.pk,
            test_script_content="async def run(page):\n    pass\n",
        )
        execution = apps.get_model("web_testing", "WebUITestExecution").objects.create(
            name="历史失败",
            project_id=project.pk,
            executor_id=user.pk,
            exec_type="case",
            status="failed",
        )
        detail = apps.get_model(
            "web_testing", "WebUITestCaseExecutionDetail"
        ).objects.create(
            execution_id=execution.pk,
            test_case_id=case.pk,
            status="failed",
            log="历史失败日志",
        )
        with connection.schema_editor() as editor:
            state = executor.loader.get_migration(*target).apply(state, editor)
        apps = state.apps
        preserved = apps.get_model(
            "web_testing", "WebUITestCaseExecutionDetail"
        ).objects.get(pk=detail.pk)
        assert preserved.log == "历史失败日志"
        assert preserved.status == "failed"
        assert preserved.source_script == ""
        assert (
            apps.get_model("web_testing", "WebUITestCase").objects.get(pk=case.pk).title
            == "历史脚本"
        )
        assert (
            apps.get_model("web_testing", "WebUIScriptAssistant").objects.count() == 0
        )
        connection.close()
    print(
        "PASS: actual 0022 SQLite migration preserves previous execution records; NAS not accessed"
    )


if __name__ == "__main__":
    main()
