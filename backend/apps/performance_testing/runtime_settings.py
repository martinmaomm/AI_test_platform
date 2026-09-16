"""Explicit execution settings; no network access or engine imports at startup."""
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import shutil
import sys

from django.conf import settings

NODE_SOURCE = Path(settings.BASE_DIR).parent / 'performance-node' / 'src'
if str(NODE_SOURCE) not in sys.path:
    sys.path.insert(0, str(NODE_SOURCE))


def valid_host(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value))


@dataclass(frozen=True)
class RuntimeSettings:
    enabled: bool
    bind_host: str
    port: int
    public_host: str
    server_name: str
    root: Path
    stunnel: str

    @classmethod
    def load(cls):
        enabled = os.environ.get('PERFORMANCE_EXECUTION_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        bind_host = os.environ.get('PERFORMANCE_RPC_BIND_HOST', '127.0.0.1')
        public_host = os.environ.get('PERFORMANCE_RPC_PUBLIC_HOST', '')
        server_name = os.environ.get('PERFORMANCE_RPC_SERVER_NAME', '')
        port = int(os.environ.get('PERFORMANCE_RPC_PORT', '9443'))
        if not valid_host(bind_host) or not valid_host(public_host) or public_host in ('0.0.0.0', '::') or not valid_host(server_name) or not 1 <= port <= 65535:
            raise ValueError('请配置性能节点可达的 TLS 地址、端口和证书名称')
        binary = shutil.which(os.environ.get('PERFORMANCE_STUNNEL_BINARY', 'stunnel'))
        if not binary:
            raise ValueError('未安装或未找到 stunnel，无法启动加密协调服务')
        root = Path(os.environ.get('PERFORMANCE_RUNTIME_DIR', str(Path(settings.BASE_DIR) / 'temp/performance-runtime'))).absolute()
        if any(c in str(root) for c in '\n\r\x00'):
            raise ValueError('性能运行目录无效')
        return cls(enabled, bind_host, port, public_host, server_name, root, binary)


def execution_configuration():
    enabled = os.environ.get('PERFORMANCE_EXECUTION_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    if not enabled:
        return {'enabled': False, 'available': False, 'reason': '尚未启用压测执行，请配置执行控制器和 TLS 入口'}
    try:
        RuntimeSettings.load()
        if not (NODE_SOURCE / 'performance_node/locust_runtime.py').is_file():
            raise ValueError('缺少固定压测引擎文件')
    except (ValueError, OSError):
        return {'enabled': True, 'available': False, 'reason': '压测执行配置不完整，请检查 TLS 入口与 stunnel'}
    return {'enabled': True, 'available': True, 'reason': ''}
