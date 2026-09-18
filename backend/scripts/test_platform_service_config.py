"""Configuration/probe regression; no Django setup, real services or network."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / 'scripts')]
from platform_service_config import load_services, preflight_service
from check_platform_services import backend_probe, caddy_probe


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='platform-service-config-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.backend = self.root / 'backend'
        self.backend.mkdir()

    def config(self, **environment):
        return load_services(self.backend, {'PATH': '', **environment})

    def test_defaults_never_start_beat_or_optional_performance(self):
        specs = self.config()
        self.assertEqual(set(specs), {'backend', 'celery', 'controller', 'caddy'})
        self.assertTrue(specs['backend']['enabled'])
        self.assertTrue(specs['celery']['enabled'])
        self.assertFalse(specs['controller']['enabled'])
        self.assertFalse(specs['caddy']['enabled'])
        self.assertEqual(specs['backend']['cwd'], str(self.backend))
        self.assertEqual(specs['backend']['command'][0], str(self.backend / '.venv/bin/python'))

    def test_gateway_origin_and_explicit_overrides(self):
        specs = self.config(PERFORMANCE_NODE_PUBLIC_URL='https://example.test:18443', PERFORMANCE_EXECUTION_ENABLED='true')
        self.assertTrue(specs['caddy']['enabled'])
        self.assertTrue(specs['controller']['enabled'])
        self.assertEqual(specs['caddy']['env']['PERFORMANCE_GATEWAY_DOMAIN'], 'example.test')
        self.assertEqual(specs['caddy']['ports'], [{'host': '127.0.0.1', 'port': '18443'}])
        disabled = self.config(PERFORMANCE_NODE_PUBLIC_URL='https://example.test', PLATFORM_CADDY_ENABLED='false')
        self.assertFalse(disabled['caddy']['enabled'])

    def test_no_django_import_or_database_access_in_configuration(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            specs = self.config(DB_PASSWORD='fixture-only-private')
        self.assertEqual(specs['backend']['env']['DB_PASSWORD'], 'fixture-only-private')
        self.assertNotIn('fixture-only-private', repr(specs['backend']['command']))

    def test_preflight_disabled_and_missing_binary(self):
        specs = self.config()
        self.assertIn('未启用', preflight_service('controller', specs['controller']))
        with patch('platform_service_config.shutil.which', return_value=None):
            self.assertIn('虚拟环境', preflight_service('backend', specs['backend']))

    def test_invalid_explicit_caddy_binary_is_not_silently_replaced(self):
        spec = self.config(PLATFORM_CADDY_ENABLED='true', PLATFORM_CADDY_BINARY='/nonexistent/caddy')['caddy']
        self.assertEqual('/nonexistent/caddy', spec['command'][0])
        self.assertIn('Caddy', preflight_service('caddy', spec))

    def gateway_spec(self, **environment):
        config_dir = self.root / 'deploy/performance'
        config_dir.mkdir(parents=True)
        (config_dir / 'Caddyfile').touch()
        spec = self.config(PERFORMANCE_NODE_PUBLIC_URL='https://example.test:18443', **environment)['caddy']
        spec['command'][0] = sys.executable
        return spec

    def test_gateway_keeps_exact_existing_ca_storage(self):
        storage = self.backend / 'temp/performance-gateway/storage'
        spec = self.gateway_spec(PERFORMANCE_NODE_CA_CERT_FILE=str(storage / 'pki/authorities/local/root.crt'))
        self.assertIsNone(preflight_service('caddy', spec))
        spec['env']['PERFORMANCE_GATEWAY_DATA'] = str(self.backend / 'different')
        self.assertIn('节点信任', preflight_service('caddy', spec))

    def test_invalid_gateway_port_and_relative_storage_rejected(self):
        spec = self.gateway_spec(PERFORMANCE_GATEWAY_PORT='bad')
        self.assertIn('端口', preflight_service('caddy', spec))
        spec['ports'][0]['port'] = 18443
        spec['env']['PERFORMANCE_GATEWAY_DATA'] = 'relative/storage'
        self.assertIn('绝对路径', preflight_service('caddy', spec))

    def test_controller_checks_stunnel_without_starting_it(self):
        spec = self.config(PERFORMANCE_EXECUTION_ENABLED='true', PERFORMANCE_RPC_PUBLIC_HOST='example.test', PERFORMANCE_RPC_SERVER_NAME='example.test')['controller']
        spec['command'][0] = sys.executable
        with patch('platform_service_config.shutil.which', return_value=None):
            self.assertIn('stunnel', preflight_service('controller', spec))

    def test_http_probe_distinguishes_api_from_unrelated_http_server(self):
        with patch('check_platform_services.http.client.HTTPConnection') as factory:
            factory.return_value.getresponse.return_value.status = 200
            self.assertFalse(backend_probe()['healthy'])
            factory.return_value.getresponse.return_value.status = 405
            self.assertTrue(backend_probe()['healthy'])
            factory.return_value.request.assert_called_with('GET', '/api/v1/auth/token/')

    def test_https_probe_does_not_disable_certificate_validation(self):
        spec = self.gateway_spec()
        cert = self.root / 'root.crt'
        cert.touch()
        spec['env']['PERFORMANCE_NODE_CA_CERT_FILE'] = str(cert)
        with patch('check_platform_services.ssl.create_default_context', side_effect=ValueError('bad CA')) as create:
            result = caddy_probe(spec)
        create.assert_called_once_with(cafile=str(cert))
        self.assertFalse(result['healthy'])
        self.assertIn('未跳过 TLS 校验', result['detail'])


if __name__ == '__main__':
    unittest.main()
