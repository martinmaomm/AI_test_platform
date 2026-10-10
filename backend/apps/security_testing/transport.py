"""One bounded request pinned to a validated DNS address; no proxy or redirects."""
from dataclasses import dataclass, field
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import quote, urlsplit

from .validation import normalize_url, origin_of

MAX_RESPONSE_BYTES = 64 * 1024
_METADATA_IPS = {'100.100.100.200', '168.63.129.16', '192.0.0.192', 'fd00:ec2::254'}
_METADATA_HOSTS = {'metadata.google.internal', 'metadata.goog', 'instance-data', 'instance-data.ec2.internal'}


class _ActiveCheckError(Exception):
    def __init__(self, original):
        self.original = original


class TransportError(Exception):
    MESSAGES = {
        'blocked_address': '目标解析到不允许访问的地址',
        'dns_failed': '目标域名解析失败', 'timeout': '请求超过等待时限',
        'tls_error': 'HTTPS证书或TLS校验失败', 'network_error': '网络连接或响应读取失败',
        'invalid_target': '请求目标或请求方法不在允许范围',
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES.get(code, '请求失败'))


@dataclass(repr=False)
class Response:
    status_code: int
    headers: list = field(repr=False)
    body: bytes = field(repr=False)
    elapsed_ms: float
    truncated: bool = False

    def header(self, name):
        return ', '.join(value for key, value in self.headers if key.lower() == name.lower())


def validate_address(value, allow_private_network):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise TransportError('blocked_address') from None
    # Transition/NAT64 addresses can tunnel around IPv4 metadata/private checks.
    if isinstance(address, ipaddress.IPv6Address) and (address.sixtofour is not None or address.teredo is not None
            or address in ipaddress.ip_network('64:ff9b::/96')
            or address in ipaddress.ip_network('64:ff9b:1::/48')):
        raise TransportError('blocked_address')
    effective = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    if (str(effective) in _METADATA_IPS or effective.is_link_local or effective.is_unspecified
            or effective.is_multicast or effective.is_reserved):
        raise TransportError('blocked_address')
    if not allow_private_network and not effective.is_global:
        raise TransportError('blocked_address')
    return address


def resolve_target(host, port, allow_private_network, deadline, check_active):
    if host.lower().rstrip('.') in _METADATA_HOSTS:
        raise TransportError('blocked_address')
    done, result = threading.Event(), []

    def resolve():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, ValueError, UnicodeError):
            result.append(None)
        finally:
            done.set()

    # getaddrinfo has no portable timeout; a daemon lookup cannot hold the task open.
    threading.Thread(target=resolve, daemon=True, name='security-dns').start()
    while not done.is_set():
        check_active()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError('timeout')
        done.wait(min(.05, remaining))
    check_active()
    if not result or not result[0] or len(result[0]) > 32:
        raise TransportError('dns_failed')
    addresses = []
    for family, kind, protocol, _, sockaddr in result[0]:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            continue
        validate_address(sockaddr[0], allow_private_network)
        addresses.append((family, protocol, sockaddr))
    if not addresses:
        raise TransportError('dns_failed')
    # Every result was checked before choosing one. Connect directly to its sockaddr.
    return addresses[0]


def request(url, method, headers, *, allowed_origins, allow_private_network, timeout_seconds, check_active):
    started = time.monotonic()
    deadline = started + timeout_seconds
    check_active()
    try:
        url = normalize_url(url)
        if method not in ('GET', 'HEAD') or origin_of(url) not in allowed_origins:
            raise ValueError
    except (ValueError, TypeError):
        raise TransportError('invalid_target') from None
    parsed = urlsplit(url)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    family, protocol, sockaddr = resolve_target(host, port, allow_private_network, deadline, check_active)
    sock, connection, timer = None, None, None
    expired = threading.Event()

    def active():
        try:
            check_active()
        except Exception as exc:
            raise _ActiveCheckError(exc) from None

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0 or expired.is_set():
            raise TransportError('timeout')
        return value

    def terminate():
        expired.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    try:
        active()
        sock = socket.socket(family, socket.SOCK_STREAM, protocol)
        sock.settimeout(remaining())
        timer = threading.Timer(remaining(), terminate)
        timer.daemon = True
        timer.start()
        sock.connect(sockaddr)
        active()
        if parsed.scheme == 'https':
            sock.settimeout(remaining())
            # Connect to the pinned IP while checking the original DNS name and SNI.
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        sock.settimeout(remaining())
        connection = http.client.HTTPConnection(host, port, timeout=remaining())
        connection.sock = sock
        path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            path += '?' + quote(parsed.query, safe="%/:?@!$&'()*+,;=-._~")
        host_header = f'[{host}]' if ':' in host else host
        if port != (443 if parsed.scheme == 'https' else 80):
            host_header += f':{port}'
        outgoing = {key: value for key, value in headers.items()
                    if key.lower() not in ('host', 'connection', 'accept-encoding')}
        outgoing.update({'Host': host_header, 'Connection': 'close', 'Accept-Encoding': 'identity'})
        connection.request(method, path, headers=outgoing)
        active()
        response = connection.getresponse()
        body = bytearray()
        while method != 'HEAD' and len(body) <= MAX_RESPONSE_BYTES:
            active()
            sock.settimeout(remaining())
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        active()
        remaining()
        return Response(response.status, response.getheaders(), bytes(body[:MAX_RESPONSE_BYTES]),
                        round((time.monotonic() - started) * 1000, 2), len(body) > MAX_RESPONSE_BYTES)
    except _ActiveCheckError as exc:
        raise exc.original from None
    except ssl.SSLError:
        raise TransportError('timeout' if expired.is_set() else 'tls_error') from None
    except (socket.timeout, TimeoutError):
        raise TransportError('timeout') from None
    except (OSError, http.client.HTTPException, ValueError):
        raise TransportError('timeout' if expired.is_set() else 'network_error') from None
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            connection.close()
        if sock is not None:
            sock.close()
