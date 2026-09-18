"""Fixed local service definitions; never initialize Django or persist secrets."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parent


def load_services(backend_dir=BACKEND, environ=None):
    backend = Path(backend_dir).resolve()
    environment = {key: value for key, value in dotenv_values(backend / '.env').items() if value is not None}
    environment.update(os.environ if environ is None else environ)
    environment['PYTHONUNBUFFERED'] = '1'
    environment['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    search_path = environment.get('PATH', os.defpath).split(os.pathsep)
    # GUI-launched terminals on macOS may not have Homebrew in PATH.
    for folder in ('/opt/homebrew/bin', '/usr/local/bin'):
        if Path(folder).is_dir() and folder not in search_path:
            search_path.append(folder)
    environment['PATH'] = os.pathsep.join(search_path)
    python = str(backend / '.venv' / 'bin' / 'python')
    public_url = environment.get('PERFORMANCE_NODE_PUBLIC_URL', '')
    try:
        parsed = urlsplit(public_url)
        domain = parsed.hostname or ''
        port = parsed.port or 443
    except ValueError:
        domain, port = '', 18443
    environment.setdefault('PERFORMANCE_GATEWAY_DOMAIN', domain)
    environment.setdefault('PERFORMANCE_GATEWAY_PORT', str(port))
    environment.setdefault('PERFORMANCE_GATEWAY_BIND', '127.0.0.1')
    environment.setdefault('PERFORMANCE_GATEWAY_DATA', str(backend / 'temp/performance-gateway/storage'))
    environment.setdefault('PERFORMANCE_NODE_RELEASE_DIR', str(backend / 'resource/performance-node'))

    def spec(label, command, *, enabled=True, reason='', timeout=30, delay=2, ports=()):
        return {'label': label, 'command': command, 'cwd': str(backend), 'env': dict(environment),
                'enabled': enabled, 'disabled_reason': reason, 'stop_timeout': timeout,
                'restart_delay': delay, 'ports': list(ports)}

    performance = environment.get('PERFORMANCE_EXECUTION_ENABLED', 'false').lower() in ('true', '1', 'yes')
    gateway = environment.get('PLATFORM_CADDY_ENABLED', str(bool(public_url)).lower()).lower() in ('true', '1', 'yes')
    caddy_binary = environment.get('PLATFORM_CADDY_BINARY', 'caddy')
    caddy = shutil.which(caddy_binary, path=environment['PATH']) or caddy_binary
    return {
        'backend': spec('后端 API', [python, 'run_asgi.py'], ports=[{'host': '0.0.0.0', 'port': 8000}]),
        'celery': spec('Celery 异步任务', [python, '-m', 'celery', '-A', 'config', 'worker', '--loglevel=info', '--pool=solo'], timeout=60),
        'controller': spec('性能控制器', [python, 'manage.py', 'run_performance_controller'], enabled=performance,
                           reason='PERFORMANCE_EXECUTION_ENABLED 未启用', timeout=45, delay=20),
        'caddy': spec('节点 HTTPS 入口', [caddy, 'run', '--config', str(backend.parent / 'deploy/performance/Caddyfile'), '--adapter', 'caddyfile'],
                      enabled=gateway, reason='未配置节点 HTTPS 入口，或 PLATFORM_CADDY_ENABLED=false',
                      ports=[{'host': environment['PERFORMANCE_GATEWAY_BIND'], 'port': environment['PERFORMANCE_GATEWAY_PORT']}]),
    }


def preflight_service(name, spec):
    """Return a safe configuration error; do not start services or open sockets."""
    if not spec['enabled']:
        return spec['disabled_reason']
    command, environment = spec['command'], spec['env']
    binary = command[0]
    if not (Path(binary).is_file() and os.access(binary, os.X_OK)) and not shutil.which(binary, path=environment['PATH']):
        return '找不到 Caddy，请先安装或设置 PLATFORM_CADDY_BINARY。' if name == 'caddy' else '找不到 backend/.venv/bin/python，请先安装后端虚拟环境。'
    for endpoint in spec['ports']:
        try:
            port = int(endpoint['port'])
            if not 1 <= port <= 65535:
                raise ValueError
        except (ValueError, TypeError):
            return '服务监听端口无效，请检查配置。'
    if name == 'controller':
        if not environment.get('PERFORMANCE_RPC_PUBLIC_HOST') or not environment.get('PERFORMANCE_RPC_SERVER_NAME'):
            return '请配置性能执行的 PERFORMANCE_RPC_PUBLIC_HOST 和 PERFORMANCE_RPC_SERVER_NAME。'
        if not shutil.which(environment.get('PERFORMANCE_STUNNEL_BINARY', 'stunnel'), path=environment['PATH']):
            return '找不到 stunnel，无法启动性能控制器。'
    if name == 'caddy':
        domain = environment.get('PERFORMANCE_GATEWAY_DOMAIN', '')
        if not domain or any(character.isspace() or character in '/\\{}\x00' for character in domain):
            return '请配置有效的 PERFORMANCE_GATEWAY_DOMAIN 或 PERFORMANCE_NODE_PUBLIC_URL。'
        if not Path(command[3]).is_file():
            return '找不到 deploy/performance/Caddyfile。'
        storage = Path(environment['PERFORMANCE_GATEWAY_DATA'])
        if not storage.is_absolute():
            return 'PERFORMANCE_GATEWAY_DATA 必须为绝对路径，避免更换工作目录后重建节点 CA。'
        ca = environment.get('PERFORMANCE_NODE_CA_CERT_FILE')
        if ca and Path(ca).resolve() != (storage / 'pki/authorities/local/root.crt').resolve():
            return '网关存储目录与现有节点 CA 路径不一致，已拒绝启动以免改变节点信任。'
    return None
