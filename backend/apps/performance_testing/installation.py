"""Validated performance-node release metadata and safe bootstrap commands."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from cryptography import x509
from django.conf import settings
from django.utils import timezone

from .constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION


SUPPORTED_ARCHITECTURES = ('amd64', 'arm64')
INSTALLATION_REQUIREMENTS = (
    'Linux（x86_64/amd64 或 aarch64/arm64）',
    '使用 root 用户执行；非 root 请先通过 sudo -i 切换为 root',
    '已安装 bash、curl、base64、sha256sum、mktemp、chmod、rm、uname',
    '可通过 HTTPS 访问平台地址和发行镜像地址',
)

_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_INSTALLER_BYTES = 2 * 1024 * 1024
_MAX_CA_BYTES = 1024 * 1024
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
    installer_sha256: str
    architectures: tuple[ArchitectureRelease, ...]
    ca_certificate: bytes | None

    @property
    def installer_url(self):
        return f'{self.platform_url}/api/v1/performance-agent/install/install.sh'


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
        'runtime_sha256', 'images',
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
    return manifest


def _load_architectures(release_directory, manifest):
    images = manifest['images']
    if not isinstance(images, dict) or not images:
        raise ReleaseConfigurationError('发行 manifest.json 未包含已发布架构。')
    if not set(images).issubset(SUPPORTED_ARCHITECTURES):
        raise ReleaseConfigurationError('发行 manifest.json 包含不支持的架构。')

    releases = []
    for architecture in SUPPORTED_ARCHITECTURES:
        if architecture not in images:
            continue
        image = images[architecture]
        required_fields = {
            'filename', 'sha256', 'size_bytes', 'image_ref', 'image_id',
            'image_config_id',
        }
        if (
            not isinstance(image, dict)
            or not required_fields.issubset(image)
            or not set(image).issubset(required_fields | {'registry_ref'})
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
        registry_ref = image.get('registry_ref')
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
        if 'registry_ref' in image:
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


def load_release_configuration():
    platform_url = _validated_public_url(os.environ.get('PERFORMANCE_NODE_PUBLIC_URL', ''))
    release_directory = _release_directory(os.environ.get('PERFORMANCE_NODE_RELEASE_DIR', ''))
    manifest = _load_manifest(release_directory)
    architectures = _load_architectures(release_directory, manifest)
    installer = installer_script_bytes()
    if not installer:
        raise ReleaseConfigurationError('通用安装脚本为空。')
    ca_certificate = _load_ca_certificate(os.environ.get('PERFORMANCE_NODE_CA_CERT_FILE', ''))
    return ReleaseConfiguration(
        platform_url=platform_url,
        release=manifest['release'],
        agent_version=manifest['agent_version'],
        installer_sha256=hashlib.sha256(installer).hexdigest(),
        architectures=architectures,
        ca_certificate=ca_certificate,
    )


def _archive_url(configuration, release):
    filename = quote(release.filename, safe='/')
    return (
        f'{configuration.platform_url}/api/v1/performance-agent/install/artifacts/'
        f'{filename}'
    )


def _bootstrap_command(configuration, *, node_id, enrollment_token):
    lines = [
        'set -euo pipefail',
        'if [ "${EUID:-$(id -u)}" -ne 0 ]; then',
        "  printf '%s\\n' '性能节点安装必须由 root 执行；请先运行 sudo -i，再重新粘贴本命令。' >&2",
        '  exit 1',
        'fi',
        'for required_command in bash curl base64 sha256sum mktemp chmod rm uname; do',
        '  if ! command -v "$required_command" >/dev/null 2>&1; then',
        "    printf '缺少必需命令：%s\\n' \"$required_command\" >&2",
        '    exit 1',
        '  fi',
        'done',
        'umask 077',
        'temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/performance-node-install.XXXXXX")"',
        'chmod 0700 "$temporary_directory"',
        'cleanup() { rm -rf -- "$temporary_directory"; }',
        'trap cleanup EXIT HUP INT TERM',
        'token_file="$temporary_directory/enrollment-token"',
        f"printf '%s' {shlex.quote(enrollment_token)} >\"$token_file\"",
        'chmod 0600 "$token_file"',
    ]
    if configuration.ca_certificate is not None:
        encoded_ca = base64.b64encode(configuration.ca_certificate).decode('ascii')
        lines.extend((
            'ca_file="$temporary_directory/platform-ca.pem"',
            f"printf '%s' {shlex.quote(encoded_ca)} | base64 --decode >\"$ca_file\"",
            'chmod 0600 "$ca_file"',
            "curl_arguments=(--fail --silent --show-error --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 120 --cacert \"$ca_file\")",
        ))
    else:
        lines.append(
            "curl_arguments=(--fail --silent --show-error --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 120)"
        )
    lines.extend((
        'installer_file="$temporary_directory/install-node.sh"',
        f'if ! curl "${{curl_arguments[@]}}" --output "$installer_file" {shlex.quote(configuration.installer_url)}; then',
        "  printf '%s\\n' '下载安装脚本失败：请检查平台地址、TLS 信任和网络连通性。' >&2",
        '  exit 1',
        'fi',
        f"if ! printf '%s  %s\\n' {shlex.quote(configuration.installer_sha256)} \"$installer_file\" | sha256sum --check --status; then",
        "  printf '%s\\n' '安装脚本 SHA256 校验失败，已拒绝执行。' >&2",
        '  exit 1',
        'fi',
        'chmod 0700 "$installer_file"',
        'case "$(uname -m)" in',
        '  x86_64) selected_architecture=amd64 ;;',
        '  aarch64|arm64) selected_architecture=arm64 ;;',
        "  *) printf '不支持的 CPU 架构：%s\\n' \"$(uname -m)\" >&2; exit 1 ;;",
        'esac',
        'case "$selected_architecture" in',
    ))
    for release in configuration.architectures:
        lines.extend((
            f'  {release.architecture})',
            f'    image_ref={shlex.quote(release.image_ref)}',
            f'    image_id={shlex.quote(release.image_id)}',
            f'    image_config_id={shlex.quote(release.image_config_id)}',
            f'    registry_ref={shlex.quote(release.registry_ref or "")}',
            f'    archive_url={shlex.quote(_archive_url(configuration, release))}',
            f'    archive_sha256={shlex.quote(release.sha256)}',
            f'    archive_size={shlex.quote(str(release.size_bytes))}',
            '    ;;',
        ))
    lines.extend((
        "  *) printf '当前 CPU 架构尚未发布：%s\\n' \"$selected_architecture\" >&2; exit 1 ;;",
        'esac',
        '# registry_ref 是固定镜像的可选运输源；仅离线归档模式探测平台归档。',
        'if [ -z "$registry_ref" ]; then',
        '  if ! curl "${curl_arguments[@]}" --range 0-0 --max-filesize 1 --output /dev/null "$archive_url"; then',
        "    printf '%s\\n' '发行镜像不可达：请检查 Caddy 发行路径、TLS 信任和网络连通性。' >&2",
        '    exit 1',
        '  fi',
        'fi',
        'installer_arguments=(',
        f'  --platform {shlex.quote(configuration.platform_url)}',
        f'  --node-id {shlex.quote(str(node_id))}',
        '  --token-file "$token_file"',
    ))
    if configuration.ca_certificate is not None:
        lines.append('  --ca-file "$ca_file"')
    lines.extend((
        '  --image-ref "$image_ref"',
        '  --image-id "$image_id"',
        '  --image-config-id "$image_config_id"',
        '  --archive-url "$archive_url"',
        '  --archive-sha256 "$archive_sha256"',
        '  --archive-size "$archive_size"',
        ')',
        'if [ -n "$registry_ref" ]; then',
        '  installer_arguments+=(--registry-ref "$registry_ref")',
        'fi',
        'bash "$installer_file" "${installer_arguments[@]}"',
    ))
    return f'bash -c {shlex.quote(chr(10).join(lines))}'


def installation_metadata(*, node=None, enrollment_token=None):
    expires_at = getattr(node, 'enrollment_expires_at', None)
    try:
        configuration = load_release_configuration()
    except ReleaseConfigurationError as exc:
        platform_url = ''
        try:
            platform_url = _validated_public_url(os.environ.get('PERFORMANCE_NODE_PUBLIC_URL', ''))
        except ReleaseConfigurationError:
            pass
        return {
            'available': False,
            'reason': str(exc),
            'platform_url': platform_url,
            'supported_architectures': [],
            'agent_version': AGENT_VERSION,
            'expires_at': expires_at,
            'requirements': list(INSTALLATION_REQUIREMENTS),
        }

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
    if enrollment_token is None:
        return metadata
    if node is None or expires_at is None or expires_at <= timezone.now():
        metadata['available'] = False
        metadata['reason'] = '一次性注册凭证已过期，请重新签发后再安装。'
        return metadata
    metadata['command'] = _bootstrap_command(
        configuration, node_id=node.pk, enrollment_token=enrollment_token,
    )
    return metadata
