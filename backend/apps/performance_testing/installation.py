"""Validated performance-node release metadata and safe bootstrap commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from cryptography import x509
from django.conf import settings
from django.utils import timezone

from .constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION


SUPPORTED_ARCHITECTURES = ('amd64', 'arm64')
INSTALLATION_REQUIREMENTS = (
    'Linux（x86_64/amd64 或 aarch64/arm64）',
    '使用 root 用户或具备 Docker 操作权限的用户执行',
    '已安装 Docker，且可拉取固定摘要的 Docker Hub 镜像',
    '可通过 HTTPS 访问平台地址',
)

_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_INSTALLER_BYTES = 2 * 1024 * 1024
_MAX_CA_BYTES = 64 * 1024
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_IMAGE_ID_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_REGISTRY_REF_RE = re.compile(
    r'^docker\.io/'
    r'[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?/'
    r'[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?'
    r'@sha256:[0-9a-f]{64}$'
)
_CERTIFICATE_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+\s+-----END CERTIFICATE-----'
)


class ReleaseConfigurationError(ValueError):
    """A safe, user-facing explanation that a node release is unavailable."""


@dataclass(frozen=True)
class ArchitectureRelease:
    architecture: str
    filename: str
    sha256: str
    size_bytes: int
    image_ref: str
    image_id: str
    image_config_id: str
    registry_ref: str | None


@dataclass(frozen=True)
class ReleaseConfiguration:
    platform_url: str
    release: str
    agent_version: str
    registry_index_ref: str
    architectures: tuple[ArchitectureRelease, ...]
    ca_certificate: bytes | None


def _repo_root():
    return Path(settings.BASE_DIR).resolve().parent


def _installer_path():
    return _repo_root() / 'deploy' / 'performance' / 'install-node.sh'


def _runtime_path():
    return (
        _repo_root() / 'performance-node' / 'src' / 'performance_node'
        / 'locust_runtime.py'
    )


def _regular_file(path, *, description, maximum_bytes=None):
    try:
        if path.is_symlink():
            raise ReleaseConfigurationError(f'{description}不能是符号链接。')
        file_stat = path.stat()
    except FileNotFoundError as exc:
        raise ReleaseConfigurationError(f'{description}不存在。') from exc
    except OSError as exc:
        raise ReleaseConfigurationError(f'{description}不可访问。') from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ReleaseConfigurationError(f'{description}必须是普通文件。')
    if maximum_bytes is not None and file_stat.st_size > maximum_bytes:
        raise ReleaseConfigurationError(f'{description}文件过大。')
    return file_stat


def _reject_symlink_components(root, relative_path, *, description):
    current = root
    for part in relative_path.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise ReleaseConfigurationError(f'{description}不存在。') from exc
        except OSError as exc:
            raise ReleaseConfigurationError(f'{description}不可访问。') from exc
        if stat.S_ISLNK(mode):
            raise ReleaseConfigurationError(f'{description}路径不能包含符号链接。')


def _read_small_file(path, *, description, maximum_bytes):
    file_stat = _regular_file(path, description=description, maximum_bytes=maximum_bytes)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ReleaseConfigurationError(f'{description}不可读取。') from exc
    if len(content) != file_stat.st_size:
        raise ReleaseConfigurationError(f'{description}读取期间发生变化。')
    return content


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(64 * 1024), b''):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseConfigurationError('固定运行时文件不可读取。') from exc
    return digest.hexdigest()


def _validated_public_url(raw_value):
    if not raw_value:
        raise ReleaseConfigurationError(
            '缺少 PERFORMANCE_NODE_PUBLIC_URL，无法生成安装命令。'
        )
    if (
        raw_value != raw_value.strip()
        or any(char == '\\' or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw_value)
        or '?' in raw_value or '#' in raw_value
    ):
        raise ReleaseConfigurationError('PERFORMANCE_NODE_PUBLIC_URL 格式无效。')
    try:
        parsed = urlsplit(raw_value)
        port = parsed.port
    except ValueError as exc:
        raise ReleaseConfigurationError('PERFORMANCE_NODE_PUBLIC_URL 格式无效。') from exc
    if (
        parsed.scheme.lower() != 'https'
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(':')
        or port == 0
    ):
        raise ReleaseConfigurationError(
            'PERFORMANCE_NODE_PUBLIC_URL 必须是无用户信息、查询和片段的 HTTPS URL。'
        )
    decoded_segments = [unquote(segment) for segment in parsed.path.split('/')]
    if any(segment in {'.', '..'} for segment in decoded_segments):
        raise ReleaseConfigurationError('PERFORMANCE_NODE_PUBLIC_URL 子路径不能包含路径穿越。')
    path = parsed.path.rstrip('/')
    return urlunsplit(('https', parsed.netloc, path, '', ''))


def _release_directory(raw_value):
    if not raw_value:
        raise ReleaseConfigurationError(
            '缺少 PERFORMANCE_NODE_RELEASE_DIR，性能节点发行包尚未配置。'
        )
    if raw_value != raw_value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in raw_value):
        raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 格式无效。')
    path = Path(raw_value)
    if not path.is_absolute():
        raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 必须是绝对路径。')
    try:
        if path.is_symlink():
            raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 不能是符号链接。')
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 不存在。') from exc
    except OSError as exc:
        raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 不可访问。') from exc
    if not resolved.is_dir():
        raise ReleaseConfigurationError('PERFORMANCE_NODE_RELEASE_DIR 必须是专用目录。')
    return resolved


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseConfigurationError('manifest.json 包含重复字段。')
        result[key] = value
    return result


def _load_manifest(release_directory):
    manifest_path = release_directory / 'manifest.json'
    raw = _read_small_file(
        manifest_path, description='发行 manifest.json', maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    try:
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseConfigurationError('发行 manifest.json 不是有效的 UTF-8 JSON。') from exc
    if not isinstance(manifest, dict):
        raise ReleaseConfigurationError('发行 manifest.json 顶层必须是对象。')
    required = {
        'release', 'protocol_version', 'agent_version', 'engine_version',
        'runtime_sha256', 'registry_index_ref', 'images',
    }
    if set(manifest) != required:
        raise ReleaseConfigurationError('发行 manifest.json 字段不符合固定格式。')
    if (
        manifest['release'] != AGENT_VERSION
        or manifest['agent_version'] != AGENT_VERSION
        or type(manifest['protocol_version']) is not int
        or manifest['protocol_version'] != PROTOCOL_VERSION
        or manifest['engine_version'] != ENGINE_VERSION
    ):
        raise ReleaseConfigurationError('发行 manifest.json 版本与平台固定版本不匹配。')
    runtime_path = _runtime_path()
    _regular_file(runtime_path, description='固定运行时文件')
    runtime_sha256 = manifest['runtime_sha256']
    if not isinstance(runtime_sha256, str) or not _SHA256_RE.fullmatch(runtime_sha256):
        raise ReleaseConfigurationError('发行 manifest.json 的 runtime_sha256 无效。')
    if runtime_sha256 != _sha256_file(runtime_path):
        raise ReleaseConfigurationError('发行包固定运行时哈希与当前平台不匹配。')
    registry_index_ref = manifest['registry_index_ref']
    if (
        not isinstance(registry_index_ref, str)
        or not _REGISTRY_REF_RE.fullmatch(registry_index_ref)
    ):
        raise ReleaseConfigurationError('发行 manifest.json 的 multi-arch Docker Hub 镜像引用无效。')
    return manifest


def _load_architectures(release_directory, manifest):
    images = manifest['images']
    if not isinstance(images, dict) or not images:
        raise ReleaseConfigurationError('发行 manifest.json 未包含已发布架构。')
    if set(images) != set(SUPPORTED_ARCHITECTURES):
        raise ReleaseConfigurationError('发行 manifest.json 必须完整包含 amd64 和 arm64。')

    releases = []
    for architecture in SUPPORTED_ARCHITECTURES:
        if architecture not in images:
            continue
        image = images[architecture]
        required_fields = {
            'filename', 'sha256', 'size_bytes', 'image_ref', 'image_id',
            'image_config_id', 'registry_ref',
        }
        if (
            not isinstance(image, dict)
            or set(image) != required_fields
        ):
            raise ReleaseConfigurationError(f'{architecture} 发行条目不符合固定格式。')
        expected_filename = f'{AGENT_VERSION}/linux-{architecture}.tar.gz'
        filename = image['filename']
        if (
            not isinstance(filename, str)
            or filename != expected_filename
            or PurePosixPath(filename).is_absolute()
            or any(part in {'', '.', '..'} for part in PurePosixPath(filename).parts)
            or '\\' in filename
        ):
            raise ReleaseConfigurationError(f'{architecture} 发行文件路径无效。')
        sha256 = image['sha256']
        size_bytes = image['size_bytes']
        image_ref = image['image_ref']
        image_id = image['image_id']
        image_config_id = image['image_config_id']
        registry_ref = image['registry_ref']
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ReleaseConfigurationError(f'{architecture} 发行文件 SHA256 无效。')
        if type(size_bytes) is not int or size_bytes <= 0:
            raise ReleaseConfigurationError(f'{architecture} 发行文件大小无效。')
        expected_ref = f'automation-platform-performance-node:{AGENT_VERSION}-{architecture}'
        if image_ref != expected_ref:
            raise ReleaseConfigurationError(f'{architecture} 镜像引用与固定发行版本不匹配。')
        if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
            raise ReleaseConfigurationError(f'{architecture} 镜像 ID 无效。')
        if (
            not isinstance(image_config_id, str)
            or not _IMAGE_ID_RE.fullmatch(image_config_id)
        ):
            raise ReleaseConfigurationError(f'{architecture} 镜像配置 ID 无效。')
        if (
            not isinstance(registry_ref, str)
            or not _REGISTRY_REF_RE.fullmatch(registry_ref)
        ):
            raise ReleaseConfigurationError(
                f'{architecture} Docker Hub 镜像引用无效。'
            )

        archive_path = release_directory.joinpath(*PurePosixPath(filename).parts)
        _reject_symlink_components(
            release_directory, PurePosixPath(filename), description=f'{architecture} 发行文件',
        )
        archive_stat = _regular_file(archive_path, description=f'{architecture} 发行文件')
        try:
            archive_path.resolve(strict=True).relative_to(release_directory)
        except (OSError, ValueError) as exc:
            raise ReleaseConfigurationError(f'{architecture} 发行文件越过专用目录。') from exc
        if archive_stat.st_size != size_bytes:
            raise ReleaseConfigurationError(f'{architecture} 发行文件大小与 manifest.json 不匹配。')
        releases.append(ArchitectureRelease(
            architecture=architecture,
            filename=filename,
            sha256=sha256,
            size_bytes=size_bytes,
            image_ref=image_ref,
            image_id=image_id,
            image_config_id=image_config_id,
            registry_ref=registry_ref,
        ))
    return tuple(releases)


def _load_ca_certificate(raw_value):
    if not raw_value:
        return None
    if raw_value != raw_value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in raw_value):
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 格式无效。')
    path = Path(raw_value)
    if not path.is_absolute():
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 必须是绝对路径。')
    content = _read_small_file(path, description='公共 CA PEM', maximum_bytes=_MAX_CA_BYTES)
    if b'PRIVATE KEY' in content.upper():
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 不得包含 PRIVATE KEY。')
    try:
        text = content.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 必须是公共 CA PEM。') from exc
    certificates = _CERTIFICATE_RE.findall(text)
    remainder = _CERTIFICATE_RE.sub('', text)
    if not certificates or remainder.strip():
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 只能包含公共 CA 证书。')
    try:
        parsed_certificates = x509.load_pem_x509_certificates(content)
    except ValueError as exc:
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 不是有效的 CA PEM。') from exc
    if len(parsed_certificates) != len(certificates):
        raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 包含无法解析的证书。')
    now = datetime.now(datetime_timezone.utc)
    for certificate in parsed_certificates:
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints,
            ).value
        except x509.ExtensionNotFound as exc:
            raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 中的证书缺少 CA 约束。') from exc
        if not constraints.ca:
            raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 只能包含 CA 证书。')
        if now < certificate.not_valid_before_utc or now > certificate.not_valid_after_utc:
            raise ReleaseConfigurationError('PERFORMANCE_NODE_CA_CERT_FILE 中的 CA 证书不在有效期内。')
    return content


def installer_script_bytes():
    return _read_small_file(
        _installer_path(), description='通用安装脚本', maximum_bytes=_MAX_INSTALLER_BYTES,
    )


def ca_certificate_bytes():
    """Return only the configured, fully validated public CA PEM bytes."""
    return _load_ca_certificate(os.environ.get('PERFORMANCE_NODE_CA_CERT_FILE', ''))


def load_release_configuration():
    platform_url = _validated_public_url(os.environ.get('PERFORMANCE_NODE_PUBLIC_URL', ''))
    release_directory = _release_directory(os.environ.get('PERFORMANCE_NODE_RELEASE_DIR', ''))
    manifest = _load_manifest(release_directory)
    architectures = _load_architectures(release_directory, manifest)
    ca_certificate = ca_certificate_bytes()
    return ReleaseConfiguration(
        platform_url=platform_url,
        release=manifest['release'],
        agent_version=manifest['agent_version'],
        registry_index_ref=manifest['registry_index_ref'],
        architectures=architectures,
        ca_certificate=ca_certificate,
    )


def _node_resource_names(node_id):
    raw_node_id = str(node_id)
    try:
        canonical_node_id = str(uuid.UUID(raw_node_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseConfigurationError('节点 ID 必须是规范 UUID。') from exc
    if raw_node_id != canonical_node_id:
        raise ReleaseConfigurationError('节点 ID 必须是规范 UUID。')
    return (
        f'performance-node-{canonical_node_id}',
        f'performance-node-{canonical_node_id}-identity',
    )


def _known_container_name(node):
    agent_version = getattr(node, 'agent_version', '')
    if agent_version and agent_version != AGENT_VERSION:
        return None
    container_name, _ = _node_resource_names(node.pk)
    return container_name


def _docker_command(configuration, *, node_id, enrollment_token):
    container_name, identity_volume = _node_resource_names(node_id)
    arguments = [
        'docker', 'run', '-d', '--name', container_name,
        '--restart', 'unless-stopped', '--init', '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '512m', '--cpus', '1', '--pids-limit', '128',
        '--stop-timeout', '25', '--log-opt', 'max-size=10m',
        '--log-opt', 'max-file=3', '--mount',
        f'type=volume,src={identity_volume},dst=/var/lib/performance-node',
        configuration.registry_index_ref,
        'start', '--server', configuration.platform_url,
        '--node-id', str(node_id), '--token', enrollment_token,
    ]
    if configuration.ca_certificate is not None:
        arguments.extend((
            '--ca-sha256', hashlib.sha256(configuration.ca_certificate).hexdigest(),
        ))
    return shlex.join(arguments)


def installation_metadata(*, node=None, enrollment_token=None):
    expires_at = getattr(node, 'enrollment_expires_at', None)
    container_name = None
    if node is not None:
        try:
            container_name = _known_container_name(node)
        except ReleaseConfigurationError as exc:
            return {
                'available': False,
                'reason': str(exc),
                'platform_url': '',
                'supported_architectures': [],
                'agent_version': AGENT_VERSION,
                'expires_at': expires_at,
                'requirements': list(INSTALLATION_REQUIREMENTS),
            }
    try:
        configuration = load_release_configuration()
    except ReleaseConfigurationError as exc:
        platform_url = ''
        try:
            platform_url = _validated_public_url(os.environ.get('PERFORMANCE_NODE_PUBLIC_URL', ''))
        except ReleaseConfigurationError:
            pass
        metadata = {
            'available': False,
            'reason': str(exc),
            'platform_url': platform_url,
            'supported_architectures': [],
            'agent_version': AGENT_VERSION,
            'expires_at': expires_at,
            'requirements': list(INSTALLATION_REQUIREMENTS),
        }
        if container_name is not None:
            metadata['container_name'] = container_name
        return metadata

    metadata = {
        'available': True,
        'reason': '',
        'platform_url': configuration.platform_url,
        'supported_architectures': [
            release.architecture for release in configuration.architectures
        ],
        'agent_version': configuration.agent_version,
        'expires_at': expires_at,
        'requirements': list(INSTALLATION_REQUIREMENTS),
    }
    if container_name is not None:
        metadata['container_name'] = container_name
    if enrollment_token is None:
        return metadata
    if node is None or expires_at is None or expires_at <= timezone.now():
        metadata['available'] = False
        metadata['reason'] = '一次性注册凭证已过期，请重新签发后再安装。'
        return metadata
    metadata['command'] = _docker_command(
        configuration, node_id=node.pk, enrollment_token=enrollment_token,
    )
    return metadata
