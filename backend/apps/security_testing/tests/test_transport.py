import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase
from unittest.mock import MagicMock, patch

from security_testing import transport


class LocalHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.server.received.append((self.path, dict(self.headers)))
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://169.254.169.254/latest/meta-data')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/slow':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            for _ in range(100):
                try:
                    self.wfile.write(b'x')
                    self.wfile.flush()
                except (OSError, ValueError):
                    return
                time.sleep(.05)
            return
        if self.path == '/large':
            body = b'a' * (transport.MAX_RESPONSE_BYTES + 1000)
            code = 200
        elif self.path == '/private':
            authenticated = self.headers.get('Authorization') == 'Bearer local-secret'
            body = json.dumps({'id': 7, 'password': 'never-persist', 'token': 'private-response-token', 'nested': {'cookie': 'server-secret'}} if authenticated else {'message': 'denied'}).encode()
            code = 200 if authenticated else 401
        else:
            body, code = b'{"id":7}', 200
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'SESSION=private-response-cookie; HttpOnly')
        self.end_headers()
        self.wfile.write(body)


class LocalFixture:
    def __enter__(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
        self.server.daemon_threads = True
        self.server.received = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.env = patch.dict(os.environ, {'OFFLINE_ALLOWED_LOOPBACK_PORT': str(self.server.server_port)})
        self.env.start()
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.env.stop()


class TransportTests(TestCase):
    def request(self, fixture, path, **kwargs):
        return transport.request(fixture.origin + path, 'GET', {}, allowed_origins=[fixture.origin], allow_private_network=True, timeout_seconds=kwargs.pop('timeout_seconds', 2), check_active=kwargs.pop('check_active', lambda: None), **kwargs)

    def test_private_requires_opt_in_and_metadata_always_blocked(self):
        for value in ('127.0.0.1', '10.1.2.3', '192.168.1.1', '100.64.0.1', '::1', '::ffff:127.0.0.1'):
            with self.subTest(value=value), self.assertRaises(transport.TransportError):
                transport.validate_address(value, False)
        self.assertEqual(str(transport.validate_address('127.0.0.1', True)), '127.0.0.1')
        for value in ('169.254.169.254', '100.100.100.200', '168.63.129.16', '192.0.0.192', '0.0.0.0', '224.0.0.1', 'fe80::1', '::', 'ff02::1', '::ffff:169.254.169.254', 'fd00:ec2::254', '64:ff9b::a9fe:a9fe', '2002:a9fe:a9fe::'):
            with self.subTest(value=value), self.assertRaises(transport.TransportError):
                transport.validate_address(value, True)

    def test_mixed_dns_result_is_rejected_before_connect(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 80)), (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with patch.object(socket, 'getaddrinfo', return_value=addresses), patch.object(socket, 'socket') as create:
            with self.assertRaises(transport.TransportError):
                transport.request('http://example.test/', 'GET', {}, allowed_origins=['http://example.test'], allow_private_network=False, timeout_seconds=1, check_active=lambda: None)
            create.assert_not_called()

    def test_local_host_header_no_redirect_and_bounded_response(self):
        with LocalFixture() as fixture, patch.dict(os.environ, {'HTTP_PROXY': 'http://invalid:9999'}):
            response = self.request(fixture, '/redirect')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(fixture.server.received), 1)
            self.assertEqual(fixture.server.received[0][1]['Host'], f'127.0.0.1:{fixture.server.server_port}')
            response = self.request(fixture, '/large')
            self.assertTrue(response.truncated)
            self.assertEqual(len(response.body), transport.MAX_RESPONSE_BYTES)

    def test_resolver_is_not_reused_between_validation_and_connection(self):
        with LocalFixture() as fixture:
            origin = f'http://allowed.test:{fixture.server.server_port}'
            answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', fixture.server.server_port))]
            with patch.object(socket, 'getaddrinfo', side_effect=[answer, AssertionError('must not resolve again')]) as resolve:
                result = transport.request(origin + '/', 'GET', {}, allowed_origins=[origin], allow_private_network=True, timeout_seconds=2, check_active=lambda: None)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(fixture.server.received[0][1]['Host'], f'allowed.test:{fixture.server.server_port}')

    def test_dns_and_slow_response_obey_wall_clock_timeout(self):
        def slow_dns(*args, **kwargs):
            time.sleep(.5)
            return []
        started = time.monotonic()
        with patch.object(socket, 'getaddrinfo', side_effect=slow_dns), self.assertRaises(transport.TransportError) as caught:
            transport.request('http://example.test/', 'GET', {}, allowed_origins=['http://example.test'], allow_private_network=False, timeout_seconds=.1, check_active=lambda: None)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertLess(time.monotonic() - started, .4)
        with LocalFixture() as fixture:
            started = time.monotonic()
            with self.assertRaises(transport.TransportError) as caught:
                self.request(fixture, '/slow', timeout_seconds=.15)
            self.assertEqual(caught.exception.code, 'timeout')
            self.assertLess(time.monotonic() - started, .6)

    def test_cancel_exception_is_preserved_even_if_value_error(self):
        calls = 0
        def active():
            nonlocal calls
            calls += 1
            if calls > 4:
                raise ValueError('cancelled')
        with LocalFixture() as fixture, self.assertRaisesRegex(ValueError, 'cancelled'):
            self.request(fixture, '/', check_active=active)

    def test_https_pins_ip_but_uses_original_tls_and_host_name(self):
        sock = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = sock
        response = MagicMock(status=200)
        response.read1.return_value = b''
        response.getheaders.return_value = []
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(transport, 'resolve_target', return_value=(socket.AF_INET, 6, ('8.8.8.8', 443))), patch.object(socket, 'socket', return_value=sock), patch.object(transport.ssl, 'create_default_context', return_value=context), patch.object(transport.http.client, 'HTTPConnection', return_value=connection):
            transport.request('https://example.test/resource', 'GET', {}, allowed_origins=['https://example.test'], allow_private_network=False, timeout_seconds=2, check_active=lambda: None)
        sock.connect.assert_called_once_with(('8.8.8.8', 443))
        context.wrap_socket.assert_called_once_with(sock, server_hostname='example.test')
        self.assertEqual(connection.request.call_args.kwargs['headers']['Host'], 'example.test')
