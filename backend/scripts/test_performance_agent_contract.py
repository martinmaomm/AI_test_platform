"""Actual Agent -> TLS -> Django contract, only in the isolated SQLite runner.

python scripts/test_webui_generation_offline.py scripts.test_performance_agent_contract
"""

import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

from django.contrib.auth import get_user_model
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from projects.models import Project
from performance_testing.models import PerformanceNode
from scripts.verify_performance_transport import certificates

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'performance-node' / 'src'))
from performance_node.client import PerformanceNodeClient
from performance_node.config import NodeConfig
from performance_node.errors import AgentStopped


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        pass


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver'])
class PerformanceAgentTLSContractTests(TransactionTestCase):
    def test_real_tls_enrollment_restart_heartbeat_and_revocation(self):
        self.assertEqual(os.environ.get('OFFLINE_TEST_NETWORK'), 'blocked',
                         'Use the isolated runner; never the configured business database')
        admin = get_user_model().objects.create_user(username='performance-tls-fixture', is_staff=True)
        project = Project.objects.create(name='Local TLS fixture', project_type='perf', created_by=admin)
        api = APIClient()
        api.force_authenticate(admin)
        endpoint = f'/api/v1/projects/{project.pk}/performance/nodes/'
        result = api.post(endpoint, {'name': 'Local TLS fixture', 'network_mode': 'lan'}, format='json')
        self.assertEqual(result.status_code, 201, result.json() if result.status_code != 201 else None)
        issued = result.json()['data']
        with tempfile.TemporaryDirectory(prefix='performance-agent-tls-') as temporary:
            directory = Path(temporary).resolve()
            certificates(directory)
            server = make_server('127.0.0.1', 0, get_wsgi_application(), handler_class=QuietHandler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(directory / 'server.pem', directory / 'server.key')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            # Match Django LiveServerTestCase's in-memory SQLite thread sharing.
            database = connections['default']
            self.assertEqual(database.vendor, 'sqlite')
            database.inc_thread_sharing()

            def serve():
                connections['default'] = database
                server.serve_forever()

            worker = threading.Thread(target=serve, daemon=True)
            with patch.dict(os.environ, {'OFFLINE_ALLOWED_LOOPBACK_PORT': str(server.server_port)}):
                worker.start()
                try:
                    config = NodeConfig(
                        platform_url=f'https://127.0.0.1:{server.server_port}',
                        state_dir=directory / 'identity', ca_bundle=str(directory / 'ca.pem'),
                    )
                    agent = PerformanceNodeClient(config)
                    diagnostics = []
                    agent.transport.session.hooks['response'].append(
                        lambda response, **_kwargs: diagnostics.append(
                            response.text.replace(issued['enrollment_token'], '<fixture-token>')
                        ) if response.status_code >= 400 else None
                    )
                    try:
                        identity = agent.enroll(issued['enrollment_token'])
                    except AgentStopped:
                        self.fail(f'Local TLS enrollment rejected: {diagnostics}')
                    self.assertEqual(identity.node_id, issued['node']['id'])
                    self.assertEqual(agent.heartbeat().interval_seconds, 5)
                    node = PerformanceNode.objects.get(pk=identity.node_id)
                    self.assertEqual(node.status_at(), 'online')
                    self.assertEqual(node.resources.keys(), {'cpu_percent', 'memory_percent'})
                    self.assertFalse(node.enrollment_token_digest)
                    self.assertNotIn(identity.agent_token, api.get(endpoint).content.decode())
                    # A new process/client instance reuses only the persisted identity.
                    restarted = PerformanceNodeClient(config)
                    self.assertEqual(restarted.store.load(), identity)
                    self.assertEqual(restarted.heartbeat().lease_seconds, 30)
                    with self.assertRaises(AgentStopped):
                        PerformanceNodeClient(NodeConfig(
                            platform_url=config.platform_url, state_dir=directory / 'replay',
                            ca_bundle=config.ca_bundle,
                        )).enroll(issued['enrollment_token'])
                    self.assertEqual(api.post(endpoint + identity.node_id + '/revoke/', {}, format='json').status_code, 200)
                    with self.assertRaises(AgentStopped):
                        restarted.heartbeat()
                    node.refresh_from_db()
                    self.assertEqual(node.status_at(), 'revoked')
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)
                    database.dec_thread_sharing()
