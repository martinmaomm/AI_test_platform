"""Run WebUI regression tests without using configured NAS/Redis/LLM services.

Builds current models in SQLite, not production migrations. Run from backend:
    python scripts/test_webui_generation_offline.py
Optional positional arguments select Django test labels.
"""

import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    backend_dir = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend_dir), str(backend_dir / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['OFFLINE_TEST_NETWORK'] = 'blocked'
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def network_blocked(original):
        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                allowed_port = os.environ.get('OFFLINE_ALLOWED_LOOPBACK_PORT')
                host = address[0] if isinstance(address, tuple) and address else None
                port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
                if host in {'127.0.0.1', '::1'} and allowed_port == str(port):
                    return original(sock, address)
                raise RuntimeError('Network access is disabled in WebUI offline tests')
            return original(sock, address)
        return connect

    with tempfile.TemporaryDirectory(prefix='automation-webui-tests-') as test_dir, patch.object(
        socket.socket, 'connect', network_blocked(original_connect),
    ), patch.object(socket.socket, 'connect_ex', network_blocked(original_connect_ex)):
        from config import settings as config
        config.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
            'TEST': {'MIGRATE': False},
        }}
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        config.MEDIA_ROOT = str(Path(test_dir) / 'media')
        import django
        django.setup()
        from django.core.management import call_command
        from django.test.runner import DiscoverRunner
        call_command('makemigrations', 'ai_core', 'api_testing', 'web_testing', 'scheduled_tasks', 'notifications', dry_run=True, check=True, verbosity=1)
        labels = sys.argv[1:] or [
            'ai_core.tests.test_webui_playwright_agent',
            'ai_core.tests.test_mcp_output_connections',
            'ai_core.tests.test_mcp_agent_budget',
            'scheduled_tasks.test_environment_contract',
            'scheduled_tasks.test_notification_targets',
            'notifications',
            'web_testing',
        ]
        return bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(labels))


if __name__ == '__main__':
    raise SystemExit(main())
