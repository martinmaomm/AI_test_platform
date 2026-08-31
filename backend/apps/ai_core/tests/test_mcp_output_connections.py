"""Configuration-probe output routing without MCP processes or database access."""
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ai_core.views import MCPConfigurationActionView


class MCPProbeOutputRoutingTests(SimpleTestCase):
    def test_playwright_probe_uses_managed_outputs_without_mutating_saved_config(self):
        servers = {
            'playwright': {
                'command': 'npx',
                'args': ['-y', '@executeautomation/playwright-mcp-server@1.0.12'],
                'env': {'PLAYWRIGHT_BROWSERS_PATH': '/test/browser-cache'},
            },
        }
        original = deepcopy(servers)

        connection = MCPConfigurationActionView()._build_mcp_connections(servers)['playwright']

        self.assertEqual(servers, original)
        self.assertEqual(connection['transport'], 'stdio')
        self.assertIn('--package', connection['args'])
        self.assertIn('@executeautomation/playwright-mcp-server@1.0.12', connection['args'])
        self.assertEqual(connection['env']['PLAYWRIGHT_BROWSERS_PATH'], '/test/browser-cache')
        self.assertNotIn('HOME', connection['env'])
        self.assertEqual(
            Path(connection['env']['AITS_MCP_LOG_FILE']).parent,
            Path(settings.BASE_DIR) / 'logs' / 'playwright-mcp',
        )
        self.assertEqual(
            Path(connection['env']['AITS_MCP_SCREENSHOT_DIR']).parent.parent,
            Path(settings.BASE_DIR) / 'temp' / 'playwright-mcp',
        )

    def test_each_configuration_probe_has_an_independent_output_id(self):
        servers = {'playwright': {
            'command': 'npx', 'args': ['-y', '@executeautomation/playwright-mcp-server@1.0.12'],
        }}
        view = MCPConfigurationActionView()
        first = view._build_mcp_connections(servers)['playwright']
        second = view._build_mcp_connections(servers)['playwright']
        self.assertNotEqual(first['env']['AITS_MCP_LOG_FILE'], second['env']['AITS_MCP_LOG_FILE'])

    def test_other_mcp_servers_keep_existing_connection_options(self):
        servers = {
            'filesystem': {'command': 'local-server', 'args': ['/test/root'], 'env': {'MODE': 'readonly'}},
            'remote': {'url': 'https://example.test/mcp', 'headers': {'X-Test': 'value'}},
        }
        original = deepcopy(servers)

        connections = MCPConfigurationActionView()._build_mcp_connections(servers)

        self.assertEqual(servers, original)
        self.assertEqual(connections['filesystem'], {
            'command': 'local-server', 'transport': 'stdio', 'args': ['/test/root'], 'env': {'MODE': 'readonly'},
        })
        self.assertEqual(connections['remote'], {
            'url': 'https://example.test/mcp', 'transport': 'streamable_http', 'headers': {'X-Test': 'value'},
        })
