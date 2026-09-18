"""Real Docker startup/restart acceptance against an ephemeral HTTPS fixture.

No platform database, enrollment credentials, targets or load generation are used.
Creates only labeled temporary containers/volumes and removes those on completion.
Example: .venv/bin/python scripts/verify_performance_docker_start.py --image REF
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


LABEL = 'com.automation-platform.acceptance'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'performance-node/src'))
from performance_node import __version__


def certificates(root):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'temporary-docker-start-ca')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=None, decipher_only=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
          .sign(key, hashes.SHA256()))
    ca_bytes = ca.public_bytes(serialization.Encoding.PEM)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'host.docker.internal')]))
            .issuer_name(name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=True, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=None, decipher_only=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('host.docker.internal')]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = root / 'server.pem', root / 'server.key'
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    key_path.chmod(0o600)
    return ca_bytes, cert_path, key_path


class Fixture(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, ca_bytes, cert_path, key_path):
        super().__init__(('0.0.0.0', 0), Handler)
        self.ca_bytes = ca_bytes
        self.counts = {}
        self.registrations = {}
        self.reject = set()
        self.lock = threading.Lock()
        self.unsafe_requests = 0
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self.socket = context.wrap_socket(self.socket, server_side=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, code, data, *, raw=False):
        content = data if raw else json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Content-Type', 'application/x-pem-file' if raw else 'application/json')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path != '/api/v1/performance-agent/install/ca.pem':
            self.respond(404, {})
            return
        if self.headers.get('Authorization') or self.headers.get('Cookie'):
            self.server.unsafe_requests += 1
        self.respond(200, self.server.ca_bytes, raw=True)

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 65536:
            self.respond(413, {})
            return
        payload = json.loads(self.rfile.read(size))
        common = {'protocol_version': 2, 'heartbeat_interval_seconds': 1,
                  'lease_seconds': 15, 'execution_enabled': False}
        if self.path == '/api/v1/performance-agent/enroll/':
            supplied = payload.get('enrollment_token', '')
            node_id = supplied.partition('.')[0]
            with self.server.lock:
                counts = self.server.counts.setdefault(node_id, {'enroll': 0, 'heartbeat': 0})
                counts['enroll'] += 1
                if node_id in self.server.reject:
                    self.respond(401, {})
                    return
                if node_id in self.server.registrations:
                    self.respond(409, {})
                    return
                self.server.registrations[node_id] = node_id + '.' + secrets.token_urlsafe(32)
            self.respond(200, {'success': True, 'data': {
                **common, 'node_id': node_id, 'agent_token': self.server.registrations[node_id],
            }})
        elif self.path == '/api/v1/performance-agent/heartbeat/':
            token = self.headers.get('Authorization', '').removeprefix('Node ')
            node_id = token.partition('.')[0]
            with self.server.lock:
                if token != self.server.registrations.get(node_id):
                    self.respond(403, {})
                    return
                self.server.counts[node_id]['heartbeat'] += 1
            self.respond(200, {'success': True, 'data': {
                **common, 'node_id': node_id, 'server_time': datetime.now(timezone.utc).isoformat(),
                'command': {'type': 'idle'},
            }})
        else:
            self.respond(404, {})


def docker(*arguments):
    result = subprocess.run(['docker', *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=90)
    if result.returncode:
        # Avoid printing command arguments or docker inspect, which can contain credentials.
        raise RuntimeError(f'Docker {arguments[0]} failed ({result.returncode})')
    return result.stdout.strip()


def container_logs(name):
    result = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def wait_for(predicate, message, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(message)


def run_case(server, image, architecture, *, mode):
    node_id = str(uuid.uuid4())
    token = node_id + '.' + secrets.token_urlsafe(24)
    name = 'performance-start-check-' + uuid.uuid4().hex[:12]
    volume = name + '-identity'
    run_label = str(uuid.uuid4())
    created = False
    docker('volume', 'create', '--label', LABEL + '=' + run_label, volume)
    if mode == 'denied':
        server.reject.add(node_id)
    fingerprint = hashlib.sha256(server.ca_bytes).hexdigest() if mode != 'wrong-ca' else '0' * 64
    try:
        args = [
            'run', '-d', '--platform', 'linux/' + architecture, '--name', name,
            '--label', LABEL + '=' + run_label, '--restart', 'unless-stopped', '--init',
            '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', '512m', '--cpus', '1', '--pids-limit', '128',
            '--stop-timeout', '10', '--mount', f'type=volume,src={volume},dst=/var/lib/performance-node',
            image, 'start', '--server', f'https://host.docker.internal:{server.server_port}',
            '--node-id', node_id, '--token', token, '--ca-sha256', fingerprint,
        ]
        docker(*args)
        created = True
        if mode == 'valid':
            wait_for(lambda: server.counts.get(node_id, {}).get('heartbeat', 0) >= 2, 'startup heartbeat missing')
            assert server.counts[node_id]['enroll'] == 1
            before = server.counts[node_id]['heartbeat']
            docker('restart', name)
            wait_for(lambda: server.counts[node_id]['heartbeat'] >= before + 2, 'restart heartbeat missing')
            assert server.counts[node_id]['enroll'] == 1, 'restart attempted re-enrollment'
            result = docker('exec', name, 'python', '-c',
                            'import os,stat,pathlib; p=pathlib.Path("/var/lib/performance-node/identity.json");'
                            'print(os.getuid(),oct(stat.S_IMODE(p.stat().st_mode)))')
            assert result == '10001 0o600', result
        else:
            if mode == 'denied':
                wait_for(lambda: server.counts.get(node_id, {}).get('enroll') == 1, 'denied enrollment not attempted')
            else:
                wait_for(lambda: bool(container_logs(name)), 'wrong-CA failure not visible')
            docker('restart', name)
            time.sleep(3)
            assert server.counts.get(node_id, {}).get('enroll', 0) == (1 if mode == 'denied' else 0)
            assert server.counts.get(node_id, {}).get('heartbeat', 0) == 0
        assert token not in container_logs(name)
        assert server.unsafe_requests == 0
        print(json.dumps({'architecture': architecture, 'case': mode, 'passed': True,
                          'counts': server.counts.get(node_id, {})}), flush=True)
    except Exception:
        if created:
            # Tokens here are ephemeral fixture-only values, but keep even
            # those out of persistent tool output and exception diagnostics.
            logs = container_logs(name)
            logs = logs.replace(token, '[fixture-token]')
            for value in server.registrations.values():
                logs = logs.replace(value, '[fixture-identity]')
            print(logs[-3000:], flush=True)
        raise
    finally:
        exists = subprocess.run(['docker', 'container', 'inspect', name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if exists:
            owner = docker('inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', name)
            if owner != run_label:
                raise RuntimeError('refuse cleanup: container ownership mismatch')
            docker('rm', '-f', name)
        owner = docker('volume', 'inspect', '--format', '{{index .Labels "' + LABEL + '"}}', volume)
        if owner != run_label:
            raise RuntimeError('refuse cleanup: volume ownership mismatch')
        docker('volume', 'rm', volume)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], default='arm64')
    args = parser.parse_args()
    inspected = json.loads(docker('image', 'inspect', '--platform', 'linux/' + args.architecture,
                                  args.image))[0]
    assert inspected['Os'] == 'linux' and inspected['Architecture'] == args.architecture
    immutable_image = inspected['Id']
    version = inspected['Config']['Labels'].get('org.opencontainers.image.version')
    assert version == __version__, 'acceptance image version differs from current source'
    print(json.dumps({'architecture': args.architecture, 'image_id': immutable_image,
                      'agent_version': version}), flush=True)
    with tempfile.TemporaryDirectory(prefix='performance-start-check-') as folder:
        root = Path(folder)
        ca, cert, key = certificates(root)
        server = Fixture(ca, cert, key)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for mode in ('valid', 'wrong-ca', 'denied'):
                run_case(server, immutable_image, args.architecture, mode=mode)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
