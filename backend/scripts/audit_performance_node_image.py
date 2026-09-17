"""Audit every saved image layer before public publication, without extracting it.

This is a targeted secret/content gate, not a claim of a comprehensive security
audit or dependency vulnerability scan. Deleted lower-layer secrets still fail.
Only sanitized paths/reasons are reported; matching secret bytes are never printed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[2]
CHUNK_SIZE = 1024 * 1024
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]{40,}')
TOKEN = re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{24,}|dckr_pat_[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{30,})\b')
SENSITIVE_NAME = re.compile(r'(?:^|/)(?:\.env(?:\.[^/]*)?|identity\.json|enrollment-token|id_rsa|id_ed25519|\.netrc|\.npmrc)$')
PRIVATE_DIRECTORIES = ('root/.ssh/', 'root/.docker/', 'app/.git/', 'app/backend/', 'app/logs/', 'app/media/')


def local_secrets():
    """Read only local platform secret settings in memory; never emit values."""
    secret_values = set()
    path = ROOT / 'backend/.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.strip().partition('=')
            if not sep or key.startswith('#'):
                continue
            if re.search(r'(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)', key.upper()):
                value = value.strip().strip('\"\'')
                if len(value) >= 8 and not value.startswith(('${', 'your-', 'change-me')):
                    secret_values.add(value.encode())
    return tuple(secret_values)


def _safe_name(name):
    parts = PurePosixPath(name).parts
    if name.startswith('/') or '..' in parts or any(ord(c) < 32 for c in name):
        raise ValueError('镜像包含不安全的文件路径；未输出原始路径')
    return name.removeprefix('./')


def _check_stream(stream, name, secrets):
    overlap = b''
    tail = max([256, *(len(value) for value in secrets)])
    while block := stream.read(CHUNK_SIZE):
        block = overlap + block
        if PRIVATE_KEY.search(block):
            raise ValueError(f'镜像敏感信息检查未通过：{name} 包含私钥')
        if TOKEN.search(block):
            raise ValueError(f'镜像敏感信息检查未通过：{name} 包含疑似访问令牌')
        if any(value in block for value in secrets):
            raise ValueError(f'镜像敏感信息检查未通过：{name} 包含本地平台敏感配置值')
        overlap = block[-tail:]


def audit_archive(path, *, secrets=None):
    secrets = tuple(local_secrets() if secrets is None else secrets)
    files = 0
    layers = 0
    application_files = []
    path = Path(path)
    with tarfile.open(path, 'r:*') as archive:
        members = {}
        for member in archive.getmembers():
            name = _safe_name(member.name)
            if name in members:
                raise ValueError('镜像归档存在重复成员')
            members[name] = member

        def read_json(name):
            member = members.get(_safe_name(name))
            if member is None or not member.isfile() or member.size > 1024 * 1024:
                raise ValueError('镜像清单或配置成员无效')
            content = archive.extractfile(member).read()
            _check_stream(io.BytesIO(content), name, secrets)
            return json.loads(content)

        manifest = read_json('manifest.json')
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError('审计仅接受一个固定节点镜像')
        entry = manifest[0]
        config = read_json(entry['Config'])
        image_layers = entry.get('Layers')
        diff_ids = config.get('rootfs', {}).get('diff_ids')
        if not image_layers or not isinstance(diff_ids, list) or len(image_layers) != len(diff_ids):
            raise ValueError('镜像层与配置摘要不完整')
        for layer_name, expected_digest in zip(image_layers, diff_ids):
            layer_member = members.get(_safe_name(layer_name))
            if layer_member is None or not layer_member.isfile():
                raise ValueError('镜像层缺失或格式错误')
            # Classic Docker saves raw tar; containerd/OCI saves may retain gzip blobs.
            digest = hashlib.sha256()
            with archive.extractfile(layer_member) as layer_data:
                compressed = layer_data.read(2) == b'\x1f\x8b'
                layer_data.seek(0)
                content = gzip.GzipFile(fileobj=layer_data) if compressed else layer_data
                while block := content.read(CHUNK_SIZE):
                    digest.update(block)
            if 'sha256:' + digest.hexdigest() != expected_digest:
                raise ValueError('镜像层内容与固定摘要不一致')
            with archive.extractfile(layer_member) as layer_data, tarfile.open(fileobj=layer_data, mode='r|*') as layer:
                for member in layer:
                    name = _safe_name(member.name)
                    if SENSITIVE_NAME.search(name) or name.startswith(PRIVATE_DIRECTORIES):
                        raise ValueError(f'镜像包含禁止公开的文件路径：{name}')
                    if member.issym() or member.islnk():
                        # Do not follow links; inspect the metadata only.
                        _check_stream(io.BytesIO(member.linkname.encode()), name, secrets)
                    if not member.isfile():
                        continue
                    files += 1
                    if name.startswith('app/'):
                        application_files.append(name)
                        allowed = name in ('app/README.md', 'app/pyproject.toml') or (
                            name.startswith('app/src/performance_node/') and name.endswith('.py')
                        ) or name.startswith(('app/build/', 'app/src/performance_node.egg-info/'))
                        if not allowed:
                            raise ValueError(f'节点应用层包含非白名单文件：{name}')
                    with layer.extractfile(member) as content:
                        _check_stream(content, name, secrets)
            layers += 1
    if not any(name.endswith('/performance_node/__main__.py') for name in application_files):
        raise ValueError('镜像中缺少节点客户端源码')
    return {'passed': True, 'layers_checked': layers, 'files_checked': files,
            'application_files': sorted(set(application_files)),
            'checks': ['all_layers_including_deleted_files', 'private_keys', 'access_tokens',
                       'local_platform_secret_values', 'sensitive_paths', 'application_allowlist']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='审计节点镜像全部层，不输出敏感值。')
    parser.add_argument('archive', type=Path)
    arguments = parser.parse_args()
    try:
        print(json.dumps(audit_archive(arguments.archive), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, f'镜像审计失败：{exc}\n')
