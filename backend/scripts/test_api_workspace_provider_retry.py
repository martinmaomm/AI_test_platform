"""Run provider-retry backend tests against disposable SQLite only."""
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

from test_api_workspace_browser import bootstrap


def main():
    def denied(*_args, **_kwargs):
        raise AssertionError('External sockets are forbidden in provider retry tests')

    with tempfile.TemporaryDirectory(prefix='automation-api-provider-retry-') as temp, \
            patch.object(socket.socket, 'connect', denied), \
            patch.object(socket.socket, 'connect_ex', denied):
        bootstrap(Path(temp))
        from django.core.management import call_command
        failures = call_command(
            'test', 'api_testing.test_workspace_provider_retry',
            verbosity=2, interactive=False,
        )
        if failures:
            raise SystemExit(1)
        print('PASS: provider retry backend contracts use disposable SQLite and fake model/HTTP only')


if __name__ == '__main__':
    main()
