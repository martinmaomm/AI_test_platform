"""Run browser-discovery navigation DiscoverRunner tests against temporary SQLite."""
from __future__ import annotations

import tempfile
from pathlib import Path

from test_api_workspace_browser import bootstrap


def main():
    with tempfile.TemporaryDirectory(prefix='browser-discovery-navigation-') as directory:
        bootstrap(Path(directory))
        from django.test.runner import DiscoverRunner

        failures = DiscoverRunner(verbosity=1).run_tests([
            'api_testing.test_browser_discovery_navigation.BrowserDiscoveryNavigationTests',
            'api_testing.test_browser_discovery.BrowserDiscoveryContractsTests',
        ])
    if failures:
        raise SystemExit(failures)
    print('PASS: browser-discovery navigation DiscoverRunner contracts are SQLite-only')


if __name__ == '__main__':
    main()
