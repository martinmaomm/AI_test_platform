"""Run project knowledge tests or generate additive migrations without NAS/Redis/LLM access."""
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    backend_dir = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend_dir), str(backend_dir / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'

    arguments = list(sys.argv[1:])
    maria_port = None
    if '--temporary-mariadb-port' in arguments:
        index = arguments.index('--temporary-mariadb-port')
        maria_port = int(arguments[index + 1])
        del arguments[index:index + 2]
        if not 1024 <= maria_port <= 65535:
            raise ValueError('Temporary MariaDB must use an explicit high localhost port')
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded(original):
        def connect(sock, address):
            if maria_port and address == ('127.0.0.1', maria_port):
                return original(sock, address)
            raise RuntimeError('Knowledge tests cannot access services other than the explicit temporary database')
        return connect

    with tempfile.TemporaryDirectory(prefix='aits-knowledge-tests-') as folder, patch.object(socket.socket, 'connect', guarded(original_connect)), patch.object(socket.socket, 'connect_ex', guarded(original_connect_ex)):
        from aits_backend import settings as config
        config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:', 'TEST': {'MIGRATE': False}}}
        if maria_port:
            config.DATABASES = {'default': {
                'ENGINE': 'django.db.backends.mysql', 'HOST': '127.0.0.1', 'PORT': str(maria_port),
                'NAME': 'aits_knowledge_test', 'USER': 'root', 'PASSWORD': '',
                'OPTIONS': {'charset': 'utf8mb4'}, 'TEST': {'NAME': 'test_aits_knowledge_isolated'},
            }}
        config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        config.CELERY_BROKER_URL = 'memory://'
        config.CELERY_RESULT_BACKEND = 'cache+memory://'
        config.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        config.MEDIA_ROOT = folder
        config.PROJECT_KNOWLEDGE_ENABLED = True
        import django
        django.setup()
        from django.core.management import call_command
        if '--makemigrations' in arguments:
            call_command('makemigrations', 'project_knowledge', verbosity=1, skip_checks=True)
            return 0
        from django.test.runner import DiscoverRunner
        call_command('makemigrations', 'project_knowledge', dry_run=True, check=True, verbosity=1)
        return bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(arguments or ['project_knowledge']))


if __name__ == '__main__':
    raise SystemExit(main())
