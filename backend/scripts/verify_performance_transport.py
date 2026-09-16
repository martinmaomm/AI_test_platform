"""Opt-in, loopback-only Locust/stunnel prototype; never targets a business site.

This verifies the proposed transport, not the platform's future run controller.
Run with the backend venv and --confirm-local-load. Requires stunnel on PATH.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificates(directory: Path):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Local performance fixture CA')])
    ca = (x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    (directory / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name, usage in [('server', ExtendedKeyUsageOID.SERVER_AUTH), ('client', ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                .issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                ]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (directory / f'{name}.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file = directory / f'{name}.key'
        descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))


def unused_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_listener(port, processes, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError('A prototype process exited before its listener was ready')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise TimeoutError('Local prototype listener startup timed out')


def check_tls_rejections(directory, port):
    context = ssl.create_default_context(cafile=str(directory / 'ca.pem'))
    missing_client_rejected = False
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname='localhost') as stream:
                stream.sendall(b'unauthenticated-probe')
                missing_client_rejected = stream.recv(1) == b''
    except (ssl.SSLError, ConnectionResetError):
        missing_client_rejected = True
    if not missing_client_rejected:
        raise AssertionError('TLS listener did not reject missing client identity')
    context.load_cert_chain(str(directory / 'client.pem'), str(directory / 'client.key'))
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname='wrong.invalid'):
                raise AssertionError('Wrong server certificate identity was accepted')
    except ssl.SSLCertVerificationError:
        return


def verify():
    executable = shutil.which('stunnel')
    if not executable:
        raise RuntimeError('Install stunnel before running this opt-in prototype')
    processes = []
    logs = []
    count = [0]

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/probe':
                self.send_error(404)
                return
            count[0] += 1
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'OK')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='performance-transport-') as temporary:
            directory = Path(temporary)
            certificates(directory)
            master_port, tls_port, local_port = unused_port(), unused_port(), unused_port()
            if len({master_port, tls_port, local_port}) != 3:
                raise RuntimeError('Ephemeral port allocation collision; retry the prototype')
            target = f'http://127.0.0.1:{server.server_port}'
            (directory / 'locustfile.py').write_text(
                'from locust import HttpUser, task, constant\n'
                'class LocalFixtureUser(HttpUser):\n'
                '    wait_time = constant(1)\n'
                '    def on_start(self):\n'
                '        self.client.trust_env = False\n'
                '    @task\n'
                '    def probe(self):\n'
                '        self.client.get("/probe", name="local fixture", timeout=2, allow_redirects=False)\n',
                encoding='utf-8',
            )

            def start(args, name):
                log = (directory / f'{name}.log').open('wb')
                logs.append(log)
                process = subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, start_new_session=True)
                processes.append(process)
                return process

            base = [sys.executable, '-m', 'locust', '-f', str(directory / 'locustfile.py'),
                    '--host', target, '--loglevel', 'WARNING']
            master = start(base + ['--master', '--master-bind-host', '127.0.0.1',
                                  '--master-bind-port', str(master_port), '--headless',
                                  '--users', '1', '--spawn-rate', '1', '--run-time', '5s',
                                  '--expect-workers', '1', '--expect-workers-max-wait', '20',
                                  '--csv', str(directory / 'result'), '--only-summary'], 'master')
            wait_listener(master_port, processes)
            for name, body in [
                ('server', f'accept = 127.0.0.1:{tls_port}\nconnect = 127.0.0.1:{master_port}\n'),
                ('client', f'client = yes\naccept = 127.0.0.1:{local_port}\nconnect = 127.0.0.1:{tls_port}\n'
                           'checkHost = localhost\nsni = localhost\n'),
            ]:
                configuration = directory / f'{name}.conf'
                configuration.write_text(
                    'foreground = yes\npid =\nsyslog = no\ndebug = warning\n'
                    f'[rpc]\n{body}cert = {directory / (name + ".pem")}\n'
                    f'key = {directory / (name + ".key")}\nCAfile = {directory / "ca.pem"}\n'
                    'verifyChain = yes\nsslVersionMin = TLSv1.2\nTIMEOUTclose = 0\n', encoding='utf-8',
                )
                start([executable, str(configuration)], f'tls-{name}')
                wait_listener(tls_port if name == 'server' else local_port, processes)
            check_tls_rejections(directory, tls_port)
            worker = start(base + ['--worker', '--master-host', '127.0.0.1',
                                  '--master-port', str(local_port)], 'worker')
            try:
                if master.wait(timeout=35) != 0:
                    raise AssertionError('Locust Master returned a failed result')
                worker.wait(timeout=10)
                with (directory / 'result_stats.csv').open(newline='', encoding='utf-8') as source:
                    aggregate = next(row for row in csv.DictReader(source) if row['Name'] == 'Aggregated')
                requests = int(aggregate['Request Count'])
                failures = int(aggregate['Failure Count'])
                stopped_count = count[0]
                time.sleep(2)
                if not (0 < requests <= 15 and requests == stopped_count == count[0] and failures == 0):
                    raise AssertionError('Master totals, fixture totals, or post-stop request counts disagree')
                return {'users': 1, 'duration_seconds': 5, 'master_requests': requests,
                        'fixture_requests': count[0], 'failures': failures, 'requests_after_stop': 0,
                        'missing_client_certificate_rejected': True, 'wrong_server_name_rejected': True,
                        'scope': 'loopback transport prototype; not platform execution acceptance'}
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                for log in logs:
                    log.close()
    finally:
        # Also cover exceptions during certificate/listener setup, before worker launch.
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for log in logs:
            if not log.closed:
                log.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-local-load', action='store_true')
    if not parser.parse_args().confirm_local_load:
        parser.error('--confirm-local-load is required for the 1-user, 5-second loopback test')
    print(json.dumps(verify(), ensure_ascii=False))
