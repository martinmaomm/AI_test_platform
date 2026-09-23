"""Real Master/Worker requests against loopback fixtures; no platform or load run.

Run with the backend virtualenv from the repository root. Each case uses one
validation user and temporary processes, identities, ports and files.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'performance-node/src'))
from performance_node import locust_runtime


class Target(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.observed.append(self.path)
        body = b'{"ok":true}'
        try:
            if self.path == '/reset':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path == '/slow':
                time.sleep(self.server.delay)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.path == '/stream-stall':
                self.wfile.write(body[:1])
                self.wfile.flush()
                time.sleep(2)
                self.wfile.write(body[1:])
            elif self.path == '/truncated':
                self.wfile.write(body[:1])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected after the client enforces its configured timeout.

    def log_message(self, *_):
        pass


class RequestTimeoutAcceptance(unittest.TestCase):
    def run_case(self, path, *, read_timeout=30, delay=0):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
        server.observed, server.delay = [], delay
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        processes = []
        try:
            with tempfile.TemporaryDirectory(prefix='performance-request-timeouts-') as temporary:
                root = Path(temporary)
                node = str(uuid.uuid4())
                snapshot = {
                    'schema_version': 4, 'run_id': str(uuid.uuid4()),
                    'engine_version': locust_runtime.ENGINE_VERSION,
                    'plan_name': 'Isolated request timeout fixture',
                    'base_url': f'http://127.0.0.1:{server.server_port}',
                    'allowed_methods': ['GET'], 'mode': 'validation',
                    'nodes': [{'node_id': node, 'users': 1, 'validation_key': 'a' * 64}],
                    'users': 1, 'spawn_rate': 1, 'duration_seconds': 120, 'wait_seconds': .1,
                    'connect_timeout_seconds': 10, 'read_timeout_seconds': read_timeout,
                    'variables': {}, 'unique_variables': [],
                    'steps': [{'name': 'probe', 'phase': 'main', 'method': 'GET', 'path': path,
                               'query': {}, 'headers': {}, 'body_type': 'none', 'body': None,
                               'extract': [], 'assertions': [
                                   {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
                               ]}],
                }
                locust_runtime.validate_snapshot(snapshot)
                token = 'temporary-fixture-' + uuid.uuid4().hex
                (root / 'master.json').write_text(json.dumps({'snapshot': snapshot, 'handshake_tokens': {node: token}}))
                (root / 'worker.json').write_text(json.dumps({'snapshot': snapshot, 'handshake_token': token}))
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                common = [sys.executable, locust_runtime.__file__]
                master = subprocess.Popen(common + [
                    '--role', 'master', '--config', str(root / 'master.json'),
                    '--master-port', str(port), '--start-file', str(root / 'start'),
                    '--metrics-file', str(root / 'metrics.json'), '--complete-file', str(root / 'complete.json'),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                processes.append(master)
                worker = subprocess.Popen(common + [
                    '--role', 'worker', '--node-id', node, '--config', str(root / 'worker.json'),
                    '--master-port', str(port),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                processes.append(worker)
                (root / 'start').touch()
                for process in processes:
                    _, stderr = process.communicate(timeout=45)
                    self.assertEqual(process.returncode, 0, stderr[-2000:])
                report = json.loads((root / 'complete.json').read_text())
                self.assertTrue(report['complete'], report.get('reason'))
                metrics = report['metrics']
                self.assertEqual(metrics['requests'], 1)
                self.assertEqual(server.observed, [path], 'A failed request must not be retried')
                return metrics
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_default_accepts_response_slower_than_previous_five_seconds(self):
        metrics = self.run_case('/slow', delay=6)
        self.assertTrue(metrics['validation_passed'])
        step = metrics['validation_steps'][0]
        self.assertEqual(step['response']['status_code'], 200)
        self.assertGreaterEqual(step['elapsed_ms'], 5900)
        self.assertEqual(step['assertions'][0]['status'], 'passed')

    def test_custom_read_timeout_before_headers(self):
        metrics = self.run_case('/slow', read_timeout=1, delay=2)
        self.assertFalse(metrics['validation_passed'])
        self.assertEqual(metrics['failure_samples'][0]['error_type'], 'read_timeout')
        self.assertLess(metrics['validation_steps'][0]['elapsed_ms'], 2500)

    def test_streaming_timeout_keeps_partial_response_incomplete(self):
        metrics = self.run_case('/stream-stall', read_timeout=1)
        self.assertFalse(metrics['validation_passed'])
        self.assertEqual(metrics['failure_samples'][0]['error_type'], 'read_timeout')
        step = metrics['validation_steps'][0]
        self.assertTrue(step['response']['incomplete'])
        self.assertEqual(step['response']['status_code'], 200)
        self.assertEqual(step['assertions'][0]['status'], 'skipped')

    def test_reset_is_connection_error_not_timeout(self):
        metrics = self.run_case('/reset')
        self.assertFalse(metrics['validation_passed'])
        self.assertEqual(metrics['failure_samples'][0]['error_type'], 'connection_error')

    def test_truncated_body_is_connection_error_not_timeout(self):
        metrics = self.run_case('/truncated')
        self.assertFalse(metrics['validation_passed'])
        self.assertEqual(metrics['failure_samples'][0]['error_type'], 'connection_error')
        self.assertTrue(metrics['validation_steps'][0]['response']['incomplete'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
