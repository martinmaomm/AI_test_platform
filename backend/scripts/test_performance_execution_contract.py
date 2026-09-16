"""Real isolated HTTPS Agent -> mTLS Locust -> localhost fixture acceptance.

Run only through scripts/test_webui_generation_offline.py. Each run uses 1 VU
for at most 5 seconds; no production database, NAS, Redis or external target.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import threading
import time
import uuid
from unittest.mock import patch
from wsgiref.simple_server import make_server

from django.contrib.auth import get_user_model
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from performance_testing.controller import PerformanceController, TERMINAL
from performance_testing.models import PerformancePlan, PerformanceRun, PerformanceTarget
from performance_testing.runtime_settings import RuntimeSettings
from performance_node.client import PerformanceNodeClient
from performance_node.config import NodeConfig
from performance_node.executor import RunExecutor
from projects.models import Project
from scripts.test_performance_agent_contract import QuietHandler
from scripts.verify_performance_transport import certificates


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.observed.append(self.path)
        content = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver'])
class PerformanceExecutionIntegrationTests(TransactionTestCase):
    def test_normal_run_and_graceful_stop_use_real_encrypted_worker(self):
        self.assertEqual(os.environ.get('OFFLINE_TEST_NETWORK'), 'blocked')
        self.assertEqual(connections['default'].vendor, 'sqlite')
        self.assertTrue(shutil.which('stunnel'), 'Install stunnel before local integration acceptance')
        admin = get_user_model().objects.create_user(username='local-load-fixture', is_staff=True)
        project = Project.objects.create(name='Local execution fixture', project_type='perf', created_by=admin)
        api = APIClient()
        api.force_authenticate(admin)
        base = f'/api/v1/projects/{project.pk}/performance/'
        created = api.post(base + 'nodes/', {'name': 'Local execution fixture', 'network_mode': 'lan'}, format='json')
        self.assertEqual(created.status_code, 201)
        issued = created.json()['data']
        with tempfile.TemporaryDirectory(prefix='performance-execution-') as temporary:
            root = Path(temporary).resolve()
            certificates(root)
            fixture = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
            fixture.observed = []
            fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
            fixture_thread.start()
            platform = make_server('127.0.0.1', 0, get_wsgi_application(), handler_class=QuietHandler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / 'server.pem', root / 'server.key')
            platform.socket = context.wrap_socket(platform.socket, server_side=True)
            database = connections['default']
            database.inc_thread_sharing()

            def serve():
                connections['default'] = database
                platform.serve_forever()

            platform_thread = threading.Thread(target=serve, daemon=True)
            platform_thread.start()
            with socket.socket() as temporary_port:
                temporary_port.bind(('127.0.0.1', 0))
                rpc_port = temporary_port.getsockname()[1]
            config = RuntimeSettings(True, '127.0.0.1', rpc_port, '127.0.0.1', '127.0.0.1',
                                     root / 'master', shutil.which('stunnel'))
            controller = PerformanceController(config)
            executor = None
            env = {'OFFLINE_ALLOWED_LOOPBACK_PORT': str(platform.server_port),
                   'PERFORMANCE_EXECUTION_ENABLED': 'true', 'PERFORMANCE_RPC_PUBLIC_HOST': '127.0.0.1',
                   'PERFORMANCE_RPC_SERVER_NAME': '127.0.0.1', 'PERFORMANCE_RPC_PORT': str(rpc_port)}
            with patch.dict(os.environ, env):
                try:
                    self.assertTrue(controller.claim())
                    self.assertFalse(PerformanceController(config).claim(), 'Only one controller may own the lease')
                    node_config = NodeConfig(f'https://127.0.0.1:{platform.server_port}', root / 'node',
                                             str(root / 'ca.pem'), shutil.which('stunnel'))
                    agent = PerformanceNodeClient(node_config)
                    identity = agent.enroll(issued['enrollment_token'])
                    agent.heartbeat()
                    executor = RunExecutor(node_config, identity.node_id)
                    target = PerformanceTarget.objects.create(project=project, name='localhost only',
                        base_url=f'http://127.0.0.1:{fixture.server_port}', allowed_methods=['GET'])
                    plan = PerformancePlan.objects.create(project=project, target=target, name='1 VU 5 seconds',
                        users=1, spawn_rate=1, duration_seconds=5, wait_seconds=.5,
                        steps=[{'name': 'local fixture', 'method': 'GET', 'path': '/fixture',
                                'expected_status': 200, 'headers': {}, 'body': None}])
                    for stop_early, expected_status in ((False, 200), (True, 200), (False, 201)):
                        plan.steps[0]['expected_status'] = expected_status
                        plan.save(update_fields=['steps'])
                        before = len(fixture.observed)
                        request = {'node_id': identity.node_id, 'request_id': str(uuid.uuid4())}
                        response = api.post(base + f'plans/{plan.pk}/runs/', request, format='json')
                        self.assertEqual(response.status_code, 201, response.content)
                        run_id = response.json()['data']['id']
                        duplicate = api.post(base + f'plans/{plan.pk}/runs/', request, format='json')
                        self.assertEqual(duplicate.status_code, 200)
                        self.assertEqual(duplicate.json()['data']['id'], run_id)
                        stop_sent = False
                        seen = set()
                        last_heartbeat = 0
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            controller.tick()
                            if time.monotonic() - last_heartbeat >= 1:
                                result = agent.heartbeat(run_report=executor.report_for_heartbeat())
                                executor.handle_command(result.command)
                                last_heartbeat = time.monotonic()
                            run = PerformanceRun.objects.get(pk=run_id)
                            seen.add(run.status)
                            if stop_early and not stop_sent and len(fixture.observed) - before >= 2:
                                stopped = api.post(base + f'runs/{run_id}/stop/', {}, format='json')
                                self.assertEqual(stopped.status_code, 200)
                                stop_sent = True
                            if run.status in TERMINAL:
                                break
                            time.sleep(.15)
                        self.assertEqual(run.status, 'cancelled' if stop_early else 'completed',
                            f'{run.status}: {run.reason_code}: {run.reason}\n{self._diagnostics(root)}')
                        observed = len(fixture.observed) - before
                        self.assertGreater(observed, 0)
                        self.assertEqual(run.latest_metrics['requests'], observed)
                        failures = observed if expected_status != 200 else 0
                        self.assertEqual(run.latest_metrics['failures'], failures)
                        self.assertEqual(run.latest_metrics['error_rate'], 1 if failures else 0)
                        self.assertTrue(run.latest_metrics['complete'])
                        self.assertTrue(run.metrics_samples)
                        self.assertIn('preparing', seen)
                        self.assertIsNone(executor.state['active'])
                        detail = api.get(base + f'runs/{run_id}/')
                        self.assertEqual(detail.status_code, 200)
                        self.assertNotIn('PRIVATE KEY', detail.content.decode())
                        self.assertNotIn('node_command', detail.content.decode())
                        final_count = len(fixture.observed)
                        time.sleep(.6)
                        self.assertEqual(len(fixture.observed), final_count, 'No load may remain after terminal state')
                        print(f'LOCAL_PERFORMANCE_ACCEPTANCE {run.status}: requests={observed}, '
                              f'failures={failures}, final_statistics_complete=true, encryption=mTLS', flush=True)
                        # Acknowledge the terminal report before the next run.
                        result = agent.heartbeat(run_report=executor.report_for_heartbeat())
                        executor.handle_command(result.command)
                finally:
                    if executor:
                        executor.shutdown()
                        executor.close()
                    controller.close()
                    platform.shutdown()
                    platform.server_close()
                    platform_thread.join(timeout=3)
                    database.dec_thread_sharing()
                    fixture.shutdown()
                    fixture.server_close()
                    fixture_thread.join(timeout=3)

    @staticmethod
    def _diagnostics(root):
        # Only local fixture process logs; never include command/certificate/identity files.
        return '\n'.join(f'{path.relative_to(root)}:\n{path.read_text(errors="replace")[-4000:]}'
                         for path in root.rglob('*.log'))
