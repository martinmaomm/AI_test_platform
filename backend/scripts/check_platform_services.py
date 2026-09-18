"""Read-only local service probes. No tests, migrations or load generation."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import logging
import os
from pathlib import Path
import socket
import ssl
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
from platform_service_config import load_services


def outcome(healthy, detail):
    return {'healthy': healthy, 'detail': detail}


def backend_probe():
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=2)
    try:
        connection.request('GET', '/api/v1/auth/token/')
        response = connection.getresponse()
        return outcome(response.status == 405, 'HTTP API 可响应' if response.status == 405 else f'HTTP 探针返回 {response.status}')
    except (OSError, http.client.HTTPException):
        return outcome(False, '本机 HTTP API 暂不可达')
    finally:
        connection.close()


def controller_probe():
    from django.db import close_old_connections
    try:
        from performance_testing.run_services import controller_execution_status
        status = controller_execution_status()
        if not status['enabled']:
            return outcome(False, '未启用性能执行')
        if not status['available']:
            return outcome(False, '性能执行配置不完整')
        return outcome(status['controller_online'], '数据库可连接，控制器心跳有效' if status['controller_online'] else '数据库可连接，但控制器心跳已过期')
    except Exception:
        return outcome(False, '无法读取控制器心跳，请检查数据库连接及迁移')
    finally:
        close_old_connections()


def celery_probe():
    try:
        from config.celery import app
        # A busy solo worker cannot answer promptly; this is not proof it died.
        replies = app.control.ping(timeout=2, connection_timeout=2)
        count = sum(1 for item in replies if any(value.get('ok') == 'pong' for value in item.values()))
        return outcome(bool(count), f'{count} 个 Worker 响应心跳（队列级探针）' if count else 'Worker 未及时响应，可能正忙；请结合 PID 和日志判断')
    except Exception:
        return outcome(False, '队列探针失败，请检查 Redis 和 Worker 日志')


def caddy_probe(spec):
    environment = spec['env']
    if not spec['enabled']:
        return outcome(False, '网关未启用')
    ca = environment.get('PERFORMANCE_NODE_CA_CERT_FILE', '')
    if not ca or not Path(ca).is_file():
        return outcome(False, '未找到节点 CA，无法验证网关 TLS')
    host = environment['PERFORMANCE_GATEWAY_BIND']
    host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    domain = environment['PERFORMANCE_GATEWAY_DOMAIN']
    try:
        context = ssl.create_default_context(cafile=ca)
        with socket.create_connection((host, int(environment['PERFORMANCE_GATEWAY_PORT'])), timeout=2) as tcp:
            with context.wrap_socket(tcp, server_hostname=domain) as connection:
                connection.sendall((f'GET /api/v1/performance-agent/install/ca.pem HTTP/1.1\r\n'
                                    f'Host: {domain}\r\nConnection: close\r\n\r\n').encode('ascii'))
                response = http.client.HTTPResponse(connection)
                response.begin()
                valid = response.status == 200 and response.read(65537).strip() == Path(ca).read_bytes().strip()
                return outcome(valid, '本机 TLS 校验及节点入口正常' if valid else 'TLS 可连接，但节点 CA 入口返回异常')
    except (OSError, ValueError, http.client.HTTPException):
        return outcome(False, 'HTTPS 探针失败，请检查网关进程、监听地址和证书；未跳过 TLS 校验')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.parse_args()
    services = load_services()
    os.environ.update(services['backend']['env'])
    # Health checks never use the application's potentially long DB timeouts.
    from config import settings
    settings.LOGGING = {'version': 1, 'disable_existing_loggers': True}
    settings.DATABASES['default'].setdefault('OPTIONS', {}).update(connect_timeout=2, read_timeout=2, write_timeout=2)
    settings.CELERY_BROKER_CONNECTION_TIMEOUT = 2
    settings.CELERY_BROKER_TRANSPORT_OPTIONS = {
        **getattr(settings, 'CELERY_BROKER_TRANSPORT_OPTIONS', {}), 'socket_timeout': 2, 'socket_connect_timeout': 2,
    }
    logging.disable(logging.CRITICAL)
    import django
    django.setup()
    checks = {'backend': backend_probe, 'celery': celery_probe, 'controller': controller_probe,
              'caddy': lambda: caddy_probe(services['caddy'])}
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = {name: executor.submit(probe) for name, probe in checks.items()}
        results = {name: future.result() for name, future in pending.items()}
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
