"""Real isolated HTTPS Agent -> mTLS Locust -> localhost fixture acceptance.

Run only through scripts/test_webui_generation_offline.py. Each run uses 1 VU;
load runs last at most 3 seconds and validation executes one round with a
120-second safety budget. No production database, NAS, Redis or external target.
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
from urllib.parse import parse_qs, urlsplit

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
    def send_json(self, status, payload, *, cookie=None):
        content = json.dumps(payload, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        self.server.observed.append({
            'method': 'POST', 'path': self.path, 'body': body,
            'authorization': self.headers.get('Authorization', ''),
            'cookie': self.headers.get('Cookie', ''),
        })
        if self.path != '/login':
            self.send_json(404, {'ok': False})
            return
        payload = json.loads(body or b'{}')
        if payload != {'username': 'fixture-user', 'password': 'fixture-password'}:
            self.send_json(401, {'ok': False})
            return
        self.send_json(200, {'token': 'fixture-token'}, cookie='fixture_session=ready; Path=/')

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        self.server.observed.append({
            'method': 'GET', 'path': self.path, 'body': b'',
            'authorization': self.headers.get('Authorization', ''),
            'cookie': self.headers.get('Cookie', ''),
        })
        if (
            parsed.path != '/business'
            or self.headers.get('Authorization') != 'Bearer fixture-token'
            or 'fixture_session=ready' not in self.headers.get('Cookie', '')
            or not query.get('name', [''])[0].startswith('load_')
        ):
            self.send_json(401, {'ok': False, 'data': []})
            return
        self.send_json(200, {'ok': True, 'data': [{'id': 1, 'name': query['name'][0]}]})

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
                    target = PerformanceTarget.objects.create(
                        project=project, name='localhost only',
                        base_url=f'http://127.0.0.1:{fixture.server_port}',
                        allowed_methods=['GET', 'POST'],
                    )
                    plan = PerformancePlan.objects.create(
                        project=project, target=target, name='setup and main fixture',
                        users=1, spawn_rate=1, duration_seconds=3, wait_seconds=.1,
                        variables={'username': 'fixture-user', 'password': 'fixture-password'},
                        unique_variables=[{'name': 'new_name', 'prefix': 'load_'}],
                        steps=[
                            {'name': 'setup login', 'phase': 'setup', 'method': 'POST',
                             'path': '/login', 'query': {}, 'headers': {}, 'body_type': 'json',
                             'body': {'username': '${username}', 'password': '${password}'},
                             'extract': [{'name': 'token', 'check': 'body.token'}],
                             'assertions': [
                                 {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
                                 {'check': 'body.token', 'comparator': 'type', 'expected': 'string'},
                             ]},
                            {'name': 'main business', 'phase': 'main', 'method': 'GET',
                             'path': '/business', 'query': {'name': '${new_name}'},
                             'headers': {'Authorization': 'Bearer ${token}'},
                             'body_type': 'none', 'body': None, 'extract': [],
                             'assertions': [
                                 {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
                                 {'check': 'body.data', 'comparator': 'type', 'expected': 'list'},
                                 {'check': 'body.data', 'comparator': 'length_gt', 'expected': 0},
                                 {'check': 'body.ok', 'comparator': 'eq', 'expected': True},
                             ]},
                        ],
                    )

                    def execute(mode, *, stop_early=False):
                        before = len(fixture.observed)
                        request = {'node_id': identity.node_id, 'request_id': str(uuid.uuid4()),
                                   'mode': mode}
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
                        observed = fixture.observed[before:]
                        self.assertGreater(len(observed), 0)
                        self.assertEqual(run.latest_metrics['requests'], len(observed))
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
                        # Acknowledge the terminal report before the next run.
                        result = agent.heartbeat(run_report=executor.report_for_heartbeat())
                        executor.handle_command(result.command)
                        return run, observed, detail.json()['data']

                    validation_run, validation_observed, validation_detail = execute('validation')
                    self.assertEqual(len(validation_observed), 2)
                    self.assertEqual(validation_run.latest_metrics['failures'], 0)
                    self.assertTrue(validation_run.latest_metrics['validation_complete'])
                    self.assertTrue(validation_run.latest_metrics['validation_passed'])
                    self.assertEqual(validation_run.latest_metrics['main_steps_completed'], 1)
                    self.assertEqual(validation_detail['validation_status'], 'passed')

                    load_run, load_observed, _ = execute('load')
                    self.assertEqual(load_run.latest_metrics['failures'], 0)
                    self.assertEqual(sum(row['path'] == '/login' for row in load_observed), 1)
                    main_rows = [row for row in load_observed if row['method'] == 'GET']
                    self.assertGreater(len(main_rows), 1)
                    self.assertEqual(len({row['path'] for row in main_rows}), len(main_rows))
                    self.assertTrue(all(row['authorization'] == 'Bearer fixture-token' for row in main_rows))
                    self.assertTrue(all('fixture_session=ready' in row['cookie'] for row in main_rows))

                    stopped_run, stopped_observed, _ = execute('load', stop_early=True)
                    self.assertEqual(stopped_run.status, 'cancelled')
                    self.assertEqual(stopped_run.latest_metrics['requests'], len(stopped_observed))

                    plan.steps[1]['assertions'][0]['expected'] = 201
                    plan.steps[1]['assertions'][2]['expected'] = 99
                    plan.save(update_fields=['steps'])
                    stale = api.get(base + f'runs/{validation_run.pk}/')
                    self.assertEqual(stale.json()['data']['validation_status'], 'stale')
                    failed_run, failed_observed, failed_detail = execute('validation')
                    self.assertEqual(len(failed_observed), 2)
                    self.assertEqual(failed_run.latest_metrics['requests'], 2)
                    self.assertEqual(failed_run.latest_metrics['failures'], 1)
                    self.assertFalse(failed_run.latest_metrics['validation_passed'])
                    self.assertEqual(len(failed_run.latest_metrics['failure_samples']), 2)
                    self.assertEqual(failed_detail['validation_status'], 'failed')

                    blocked = api.post(base + f'plans/{plan.pk}/runs/', {
                        'node_id': identity.node_id, 'request_id': str(uuid.uuid4()), 'mode': 'load',
                    }, format='json')
                    self.assertEqual(blocked.status_code, 400, blocked.content)
                    self.assertEqual(blocked.json()['error']['code'], 'run_validation_failed')
                    print('LOCAL_PERFORMANCE_ACCEPTANCE validation/load/stop/failure/gate: '
                          'requests_counted=true, setup_once=true, extraction=true, encryption=mTLS',
                          flush=True)
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
