"""Two real Agents/Workers, mTLS, isolated DB and a loopback-only target.

Use test_webui_generation_offline.py. This fixture uses at most three VUs;
it is a protocol/failure acceptance test, not a capacity benchmark.
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


class TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.observed.append(self.path)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver'])
class MultiNodeExecutionTests(TransactionTestCase):
    def test_two_nodes_share_load_finalize_cancel_and_stop_on_member_failure(self):
        self.assertEqual(os.environ.get('OFFLINE_TEST_NETWORK'), 'blocked')
        self.assertEqual(connections['default'].vendor, 'sqlite')
        self.assertTrue(shutil.which('stunnel'))
        admin = get_user_model().objects.create_user(username='multi-node-fixture', is_staff=True)
        project = Project.objects.create(name='Isolated multi-node', project_type='perf', created_by=admin)
        api = APIClient()
        api.force_authenticate(admin)
        base = f'/api/v1/projects/{project.pk}/performance/'
        with tempfile.TemporaryDirectory(prefix='performance-multi-node-') as temporary:
            root = Path(temporary).resolve()
            certificates(root)
            target_server = ThreadingHTTPServer(('127.0.0.1', 0), TargetHandler)
            target_server.observed = []
            target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
            target_thread.start()
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
            with socket.socket() as port:
                port.bind(('127.0.0.1', 0))
                rpc_port = port.getsockname()[1]
            controller = PerformanceController(RuntimeSettings(
                True, '127.0.0.1', rpc_port, '127.0.0.1', '127.0.0.1', root / 'master', shutil.which('stunnel')))
            agents = []
            env = {'OFFLINE_ALLOWED_LOOPBACK_PORT': str(platform.server_port),
                   'PERFORMANCE_EXECUTION_ENABLED': 'true', 'PERFORMANCE_RPC_PUBLIC_HOST': '127.0.0.1',
                   'PERFORMANCE_RPC_SERVER_NAME': '127.0.0.1', 'PERFORMANCE_RPC_PORT': str(rpc_port)}
            with patch.dict(os.environ, env):
                try:
                    for number in range(2):
                        issued = api.post(base + 'nodes/', {'name': f'Fixture {number}', 'network_mode': 'lan'}, format='json')
                        self.assertEqual(issued.status_code, 201, issued.content)
                        config = NodeConfig(f'https://127.0.0.1:{platform.server_port}', root / f'node-{number}',
                                            str(root / 'ca.pem'), shutil.which('stunnel'))
                        agent = PerformanceNodeClient(config)
                        identity = agent.enroll(issued.json()['data']['enrollment_token'])
                        agent.heartbeat()
                        agents.append((identity.node_id, agent, RunExecutor(config, identity.node_id)))
                    self.assertTrue(controller.claim())
                    target = PerformanceTarget.objects.create(project=project, name='Loopback fixture',
                        base_url=f'http://127.0.0.1:{target_server.server_port}', allowed_methods=['GET'])
                    steps = [{'name': name, 'phase': phase, 'method': 'GET', 'path': path,
                              'query': {}, 'headers': {}, 'body_type': 'none', 'body': None, 'extract': [],
                              'assertions': [{'check': 'body.ok', 'comparator': 'eq', 'expected': True}]}
                             for name, phase, path in [('setup', 'setup', '/setup'), ('probe', 'main', '/probe')]]
                    plan = PerformancePlan.objects.create(project=project, target=target, name='Two-node fixture',
                        users=3, spawn_rate=1, duration_seconds=5, wait_seconds=.1, steps=steps)

                    def heartbeat_all():
                        for _, agent, executor in agents:
                            response = agent.heartbeat(run_report=executor.report_for_heartbeat())
                            executor.handle_command(response.command)

                    def execute(mode, selected, action=None):
                        before = len(target_server.observed)
                        payload = {'node_ids': selected, 'mode': mode, 'request_id': str(uuid.uuid4())}
                        response = api.post(base + f'plans/{plan.pk}/runs/', payload, format='json')
                        self.assertEqual(response.status_code, 201, response.content)
                        run_id = response.json()['data']['id']
                        retried = api.post(base + f'plans/{plan.pk}/runs/',
                                          {**payload, 'node_ids': list(reversed(selected))}, format='json')
                        self.assertEqual(retried.status_code, 200, retried.content)
                        self.assertEqual(retried.json()['data']['id'], run_id)
                        deadline, acted, last_heartbeat = time.monotonic() + 90, False, 0
                        while time.monotonic() < deadline:
                            controller.tick()
                            if time.monotonic() - last_heartbeat >= .5:
                                heartbeat_all()
                                last_heartbeat = time.monotonic()
                            run = PerformanceRun.objects.get(pk=run_id)
                            if (action and not acted and run.status == 'running'
                                    and len(target_server.observed) - before >= 6):
                                action(run)
                                acted = True
                            if run.status in TERMINAL:
                                break
                            time.sleep(.1)
                        expected = 'incomplete' if action is fail_member else 'cancelled' if action else 'completed'
                        self.assertEqual(run.status, expected,
                                         f'{run.reason_code}: {run.reason}\n{self.diagnostics(root)}')
                        self.assertEqual(run.participants.count(), len(selected))
                        self.assertTrue(all(executor.state['active'] is None for _, _, executor in agents))
                        observed = target_server.observed[before:]
                        members = list(run.participants.order_by('node_id'))
                        self.assertEqual(run.latest_metrics['requests'], sum(m.latest_metrics.get('requests', 0) for m in members))
                        if action is not fail_member:
                            self.assertTrue(run.latest_metrics['complete'], json.dumps(run.latest_metrics, ensure_ascii=False) + '\n' + self.diagnostics(root))
                            self.assertEqual(run.latest_metrics['requests'], len(observed))
                            self.assertTrue(all(member.latest_metrics['complete'] for member in members))
                        else:
                            self.assertFalse(run.latest_metrics['complete'])
                        count = len(target_server.observed)
                        time.sleep(.6)
                        self.assertEqual(count, len(target_server.observed), 'No requests after termination')
                        heartbeat_all()
                        detail = api.get(base + f'runs/{run.pk}/').json()['data']
                        self.assertEqual(detail['node_count'], len(selected))
                        self.assertNotIn('PRIVATE KEY', json.dumps(detail))
                        self.assertNotIn('node_command', json.dumps(detail))
                        return run, observed

                    def stop(run):
                        response = api.post(base + f'runs/{run.pk}/stop/', {}, format='json')
                        self.assertEqual(response.status_code, 200, response.content)

                    def fail_member(run):
                        agents[0][2].stop_active('fixture_failure', 'isolated failure injection', report_state='failed')

                    selected = [item[0] for item in agents]
                    for node_id in selected:
                        validation, observed = execute('validation', [node_id])
                        self.assertEqual(len(observed), 2)
                        self.assertTrue(validation.latest_metrics['validation_passed'])
                    normal, observed = execute('load', selected)
                    self.assertEqual(sorted(m.assigned_users for m in normal.participants.all()), [1, 2])
                    self.assertEqual(observed.count('/setup'), 3, 'Total VUs must not be multiplied by node count')
                    execute('load', selected, stop)
                    plan.duration_seconds = 10
                    plan.save(update_fields=['duration_seconds'])
                    failed, observed = execute('load', selected, fail_member)
                    self.assertLessEqual(observed.count('/setup'), 3, 'Failure must not spawn replacement users')
                    for member in failed.participants.all():
                        for sample in member.metrics_samples:
                            users = sample['metrics'].get('users')
                            if users is not None:
                                self.assertLessEqual(users, member.assigned_users)
                    print('MULTI_NODE_ACCEPTANCE nodes=2 max_vus=3: validation/load/stop/failure/counts/mTLS passed', flush=True)
                finally:
                    for _, _, executor in agents:
                        executor.shutdown()
                        executor.close()
                    controller.close()
                    platform.shutdown()
                    platform.server_close()
                    platform_thread.join(timeout=3)
                    database.dec_thread_sharing()
                    target_server.shutdown()
                    target_server.server_close()
                    target_thread.join(timeout=3)

    @staticmethod
    def diagnostics(root):
        return '\n'.join(f'{path.relative_to(root)}:\n{path.read_text(errors="replace")[-3000:]}'
                         for path in root.rglob('*.log'))
