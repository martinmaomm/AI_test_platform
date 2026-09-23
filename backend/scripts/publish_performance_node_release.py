"""Publish trusted, locally built node images; never publish a platform image.

Run from any directory with --image amd64=REF [--image arm64=REF].
The script validates the installed runtime in an isolated container, then saves
immutable gzip archives and atomically replaces a small public manifest.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'performance-node/src'))
from performance_node import __version__, PROTOCOL_VERSION, ENGINE_VERSION

RUNTIME = ROOT / 'performance-node/src/performance_node/locust_runtime.py'
_REGISTRY_RE = re.compile(
    r'docker\.io/[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?/'
    r'[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?'
)
_IMAGE_ID_RE = re.compile(r'sha256:[0-9a-f]{64}')
_PROBE_LABEL = 'com.automation-platform.release-probe'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_config_id(path, reference, inspected):
    """Validate the exported image against the already-probed inspect snapshot."""
    with tarfile.open(path, 'r:gz') as archive:
        def read_json_member(name):
            matches = [member for member in archive.getmembers() if member.name == name]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 1024 * 1024:
                raise ValueError('镜像归档配置成员无效')
            return archive.extractfile(matches[0]).read()

        manifest = json.loads(read_json_member('manifest.json'))
        if not isinstance(manifest, list) or len(manifest) != 1 or manifest[0].get('RepoTags') != [reference]:
            raise ValueError('镜像归档必须只包含指定固定镜像')
        name = manifest[0].get('Config', '')
        if not re.fullmatch(r'(?:blobs/sha256/[0-9a-f]{64}|[0-9a-f]{64}\.json)', name):
            raise ValueError('镜像配置路径不是内容寻址摘要')
        content = read_json_member(name)
        digest = hashlib.sha256(content).hexdigest()
        if name.rsplit('/', 1)[-1].removesuffix('.json') != digest:
            raise ValueError('镜像配置文件内容与摘要不匹配')
        document = json.loads(content)
        if not isinstance(document, dict):
            raise ValueError('镜像配置文件格式无效')
        expected = {
            'architecture': inspected['Architecture'],
            'os': inspected['Os'],
            'config': inspected['Config'],
            'rootfs': {
                'type': inspected['RootFS']['Type'],
                'diff_ids': inspected['RootFS']['Layers'],
            },
        }
        actual = {key: document.get(key) for key in expected}
        if actual != expected:
            raise ValueError('镜像归档与已验证镜像不一致；发布期间 tag 可能已被重指')
        return 'sha256:' + digest


def _cleanup_probe_container(name, owner):
    inspection = subprocess.run([
        'docker', 'container', 'inspect', '--format',
        '{{index .Config.Labels "' + _PROBE_LABEL + '"}}', name,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    if inspection.returncode:
        detail = (inspection.stdout + inspection.stderr).lower()
        if 'no such container' in detail:
            return False
        raise ValueError('无法确认固定镜像运行时检查容器是否存在')
    if inspection.stdout.strip() != owner:
        raise ValueError('固定镜像运行时检查容器归属不匹配；拒绝清理')
    removal = subprocess.run(
        ['docker', 'container', 'rm', '--force', '--volumes', name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    if removal.returncode:
        raise ValueError('固定镜像运行时检查容器清理失败')
    return True


def inspect_image(architecture, reference):
    expected_ref = f'automation-platform-performance-node:{__version__}-{architecture}'
    if architecture not in ('amd64', 'arm64') or reference != expected_ref:
        raise ValueError(f'镜像必须为固定引用 {expected_ref}')
    info = json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', reference], stderr=subprocess.PIPE,
    ))[0]
    if info['Os'] != 'linux' or info['Architecture'] != architecture:
        raise ValueError('镜像系统或架构与发布参数不符')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', info['Id']):
        raise ValueError('无效镜像 ID')
    config = info['Config']
    rootfs = info.get('RootFS')
    if (not isinstance(rootfs, dict) or rootfs.get('Type') != 'layers'
            or not isinstance(rootfs.get('Layers'), list)
            or any(not re.fullmatch(r'sha256:[0-9a-f]{64}', item)
                   for item in rootfs['Layers'])):
        raise ValueError('镜像层摘要信息无效')
    if config.get('User') not in ('nodeagent', '10001', '10001:10001'):
        raise ValueError('拒绝发布非节点用户镜像')
    if config.get('Entrypoint') != ['python', '-m', 'performance_node'] or config.get('Cmd') != ['run']:
        raise ValueError('镜像入口不是固定节点 Agent')
    allowed_env = {'PATH', 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256',
                   'PERFORMANCE_NODE_STATE_DIR', 'PERFORMANCE_STUNNEL_BINARY'}
    if any(item.partition('=')[0] not in allowed_env for item in config.get('Env', [])):
        raise ValueError('镜像包含未经审核的环境变量；请勿发布平台凭证')
    probe = (
        'import hashlib,json,pathlib,importlib.metadata,performance_node as n;'
        'print(json.dumps({"agent_version":n.__version__,"protocol_version":n.PROTOCOL_VERSION,'
        '"engine_version":importlib.metadata.version("locust"),'
        '"runtime_sha256":hashlib.sha256((pathlib.Path(n.__file__).parent/"locust_runtime.py").read_bytes()).hexdigest()}))'
    )
    probe_name = 'performance-release-probe-' + uuid.uuid4().hex
    probe_owner = str(uuid.uuid4())
    probe_error = None
    try:
        raw = subprocess.check_output([
            'docker', 'run', '--name', probe_name,
            '--platform', f'linux/{architecture}',
            '--label', f'{_PROBE_LABEL}={probe_owner}',
            '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', '128m', '--pids-limit', '32', '--entrypoint', 'python', info['Id'], '-c', probe,
        ], timeout=60, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as exc:
        probe_error = exc
    try:
        _cleanup_probe_container(probe_name, probe_owner)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('固定镜像运行时检查容器清理失败；清理状态未确认') from exc
    if probe_error is not None:
        raise ValueError('固定镜像运行时检查失败或超时；检查容器已清理或未创建') from probe_error
    actual = json.loads(raw)
    expected = {'agent_version': __version__, 'protocol_version': PROTOCOL_VERSION,
                'engine_version': ENGINE_VERSION, 'runtime_sha256': sha256(RUNTIME)}
    if actual != expected:
        raise ValueError('镜像内 Agent/引擎/协议/固定脚本与当前源码不一致')
    return info, expected


def private_directory_check(root):
    if any(path.is_symlink() for path in [root, *root.parents]):
        raise ValueError('发行目录及其父目录不能是符号链接')
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError('发行路径不是目录')


def registry_repository(value):
    """Return the explicitly permitted Docker Hub repository namespace."""
    if not _REGISTRY_RE.fullmatch(value):
        raise ValueError('--registry 必须为 docker.io/<dockerid>/<repo>')
    return value


def _docker_error(exc):
    details = (getattr(exc, 'output', b''), getattr(exc, 'stderr', b''))
    normalized = []
    for detail in details:
        if isinstance(detail, bytes):
            detail = detail.decode(errors='replace')
        if detail:
            normalized.append(str(detail))
    # Used only to classify a Docker error; callers never render this text.
    return '\n'.join(normalized) or str(exc)


def remote_manifest(reference, *, environment=None, absent_ok=False):
    """Inspect a registry manifest without trusting a local tag cache."""
    try:
        raw = subprocess.check_output(
            ['docker', 'manifest', 'inspect', '--verbose', reference], env=environment,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = _docker_error(exc).lower()
        if absent_ok and ('no such manifest' in detail or 'manifest unknown' in detail):
            return None
        if absent_ok and any(marker in detail for marker in ('unauthorized', 'access denied', 'denied')):
            raise ValueError(
                'Docker Hub 预检未授权或仓库不可见，未将其视为 tag 不存在；'
                '请确认已 docker login 且仓库已创建并公开后重试'
            ) from exc
        if absent_ok:
            raise ValueError('无法确认 Docker Hub 固定 tag 是否存在；拒绝在未知状态下覆盖') from exc
        raise ValueError('无法从 Docker Hub 远端检查发布镜像') from exc
    try:
        document = json.loads(raw)
        descriptor = document['Descriptor']
        image_manifest = document.get('SchemaV2Manifest') or document.get('OCIManifest')
        config = image_manifest['config']
        digest = descriptor['digest']
        config_id = config['digest']
        platform = descriptor.get('platform') or descriptor.get('Platform')
        os_name = platform['os']
        architecture = platform['architecture']
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Docker Hub 远端 manifest 格式无效') from exc
    if (not isinstance(digest, str) or not isinstance(config_id, str)
            or not _IMAGE_ID_RE.fullmatch(digest) or not _IMAGE_ID_RE.fullmatch(config_id)):
        raise ValueError('Docker Hub 远端 manifest 摘要无效')
    if os_name != 'linux' or architecture not in ('amd64', 'arm64'):
        raise ValueError('Docker Hub 远端 manifest 平台无效')
    return {'digest': digest, 'config_id': config_id,
            'os': os_name, 'architecture': architecture}


def current_docker_host():
    """Keep an anonymous client on the already validated local Docker daemon."""
    try:
        raw = subprocess.check_output(
            ['docker', 'context', 'inspect'], stderr=subprocess.PIPE,
        )
        host = json.loads(raw)[0]['Endpoints']['docker']['Host']
    except (subprocess.CalledProcessError, IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取当前 Docker context 的 Unix endpoint') from exc
    if not isinstance(host, str) or not host.startswith('unix://'):
        raise ValueError('当前 Docker context 不是受支持的 Unix endpoint')
    return host


def audit_release_archive(path):
    """Run the shared secret/content gate before any archive becomes public."""
    try:
        audit_archive = importlib.import_module('audit_performance_node_image').audit_archive
    except (ImportError, AttributeError) as exc:
        raise ValueError('缺少 performance-node 归档安全审计模块') from exc
    # The audit returns a sanitized summary and raises ValueError on findings.
    return audit_archive(path)


def _anonymous_remote_image(reference, expected_manifest_digest, expected_config_id, architecture, environment):
    """Confirm that an unauthenticated Docker client can pull the exact image."""
    try:
        subprocess.check_output(['docker', 'image', 'pull', reference], env=environment,
                                stderr=subprocess.PIPE)
        raw = subprocess.check_output(['docker', 'image', 'inspect', reference], env=environment,
                                      stderr=subprocess.PIPE)
        info = json.loads(raw)[0]
    except (subprocess.CalledProcessError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError('Docker Hub 镜像无法匿名拉取或检查') from exc
    if (info.get('Id') not in {expected_manifest_digest, expected_config_id} or info.get('Os') != 'linux'
            or info.get('Architecture') != architecture):
        raise ValueError('Docker Hub 匿名拉取镜像的配置、系统或架构不匹配')


def publish_registry_image(archive, architecture, inspected, image_config_id, registry):
    """Push a verified archive image, then prove the Docker Hub copy is public."""
    # Keep a second gate directly before external publication.  The first gate
    # in publish_image protects the Caddy-served tar path.
    audit_release_archive(archive)

    image_id = inspected['Id']
    tag = f'{__version__}-{architecture}-{image_config_id.removeprefix("sha256:")[:12]}'
    reference = f'{registry}:{tag}'
    existing = remote_manifest(reference, absent_ok=True)
    if existing is not None and existing['config_id'] != image_config_id:
        raise ValueError('Docker Hub 已有同名发布 tag 但内容不同；拒绝覆盖')
    if existing is None:
        try:
            # Never tag the mutable source reference: image_id was inspected
            # and runtime-probed before archive creation.
            subprocess.check_output(['docker', 'image', 'tag', image_id, reference], stderr=subprocess.PIPE)
            subprocess.check_output(['docker', 'image', 'push', '--platform', f'linux/{architecture}', reference],
                                    stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise ValueError('Docker Hub 镜像推送失败') from exc
    verified = remote_manifest(reference)
    if (verified['config_id'] != image_config_id or verified['os'] != 'linux'
            or verified['architecture'] != architecture):
        raise ValueError('Docker Hub 远端 manifest 配置摘要与已检查镜像不一致')
    with tempfile.TemporaryDirectory(prefix='performance-node-registry-') as temporary:
        environment = dict(os.environ)
        # Docker context and inline auth configuration override/augment an
        # empty config directory.  Remove both before the public-read probe.
        environment.pop('DOCKER_CONTEXT', None)
        environment.pop('DOCKER_AUTH_CONFIG', None)
        environment.update(DOCKER_CONFIG=temporary, DOCKER_HOST=current_docker_host())
        anonymous = remote_manifest(reference, environment=environment)
        if anonymous != verified:
            raise ValueError('Docker Hub 匿名 manifest 与已验证远端内容不一致')
        immutable_reference = f'{registry}@{verified["digest"]}'
        _anonymous_remote_image(
            immutable_reference, verified['digest'], image_config_id, architecture, environment,
        )
    return f'{registry}@{verified["digest"]}'


def remote_index(reference, *, environment=None, absent_ok=False):
    """Read an exact two-platform index, not a mutable local manifest cache."""
    try:
        raw = subprocess.check_output([
            'docker', 'buildx', 'imagetools', 'inspect', '--format', '{{json .Manifest}}', reference,
        ], env=environment, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        detail = _docker_error(exc).lower()
        if absent_ok and any(marker in detail for marker in ('not found', 'no such manifest', 'manifest unknown')):
            if not any(marker in detail for marker in ('unauthorized', 'denied', '403', '401')):
                return None
        raise ValueError('无法确认 Docker Hub 多架构索引；拒绝在未知状态下继续') from exc
    try:
        document = json.loads(raw)
        digest = document['digest']
        entries = document['manifests']
        if (document.get('schemaVersion') != 2
                or document.get('mediaType') not in {
                    'application/vnd.oci.image.index.v1+json',
                    'application/vnd.docker.distribution.manifest.list.v2+json',
                }
                or not isinstance(digest, str) or not _IMAGE_ID_RE.fullmatch(digest)
                or not isinstance(entries, list) or len(entries) != 2):
            raise ValueError
        members = {}
        for entry in entries:
            architecture = entry['platform']['architecture']
            member_digest = entry['digest']
            if (entry['platform']['os'] != 'linux' or architecture not in ('amd64', 'arm64')
                    or architecture in members
                    or not isinstance(member_digest, str) or not _IMAGE_ID_RE.fullmatch(member_digest)):
                raise ValueError
            members[architecture] = member_digest
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Docker Hub 多架构索引格式或平台范围无效') from exc
    return {'digest': digest, 'members': members}


def publish_registry_index(manifest, registry):
    """Bind both images to one immutable index and a verified version alias."""
    images = manifest.get('images', {})
    if set(images) != {'amd64', 'arm64'}:
        raise ValueError('一条 Docker 命令发行必须同时包含 amd64 和 arm64')
    version_reference = f'{registry}:{__version__}'
    recorded_version_reference = manifest.get('registry_version_ref')
    if recorded_version_reference not in (None, version_reference):
        raise ValueError('发行 manifest 记录了其他版本 tag；拒绝继续')
    recorded_index_reference = manifest.get('registry_index_ref')
    if recorded_index_reference is not None:
        index_prefix = registry + '@'
        if (
            not isinstance(recorded_index_reference, str)
            or not recorded_index_reference.startswith(index_prefix)
            or not _IMAGE_ID_RE.fullmatch(recorded_index_reference[len(index_prefix):])
        ):
            raise ValueError('发行 manifest 的固定索引不是本仓库合法摘要引用')
    expected = {}
    references = []
    for architecture in ('amd64', 'arm64'):
        record = images[architecture]
        reference = record.get('registry_ref', '')
        prefix = registry + '@'
        if not reference.startswith(prefix) or not _IMAGE_ID_RE.fullmatch(reference[len(prefix):]):
            raise ValueError('多架构索引只能引用本仓库已核验的固定摘要')
        digest = reference[len(prefix):]
        verified = remote_manifest(reference)
        if verified != {'digest': digest, 'config_id': record['image_config_id'],
                        'os': 'linux', 'architecture': architecture}:
            raise ValueError('多架构索引成员与已审计镜像不一致')
        expected[architecture] = digest
        references.append(reference)
    identifier = hashlib.sha256('\n'.join(references).encode()).hexdigest()[:16]
    tag = f'{registry}:{__version__}-multi-{identifier}'
    existing = remote_index(tag, absent_ok=True)
    if existing is not None and existing['members'] != expected:
        raise ValueError('Docker Hub 已有同名索引但镜像不同；拒绝覆盖')
    if existing is None and recorded_index_reference is not None:
        raise ValueError('发行 manifest 已记录固定索引，但对应固定 tag 不存在；拒绝重新生成')
    if existing is None:
        try:
            subprocess.check_output([
                'docker', 'buildx', 'imagetools', 'create', '--tag', tag, *references,
            ], stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise ValueError('Docker Hub 多架构索引发布失败') from exc
    verified = remote_index(tag)
    if verified['members'] != expected:
        raise ValueError('Docker Hub 多架构索引发布后核验失败')
    immutable = f'{registry}@{verified["digest"]}'
    if recorded_index_reference is not None and recorded_index_reference != immutable:
        raise ValueError('发行 manifest 已记录不同固定索引摘要；拒绝补充版本 tag 或改写引用')
    with tempfile.TemporaryDirectory(prefix='performance-node-index-') as temporary:
        # Homebrew discovers buildx through cliPluginsExtraDirs. Preserve only
        # plugin search paths: never copy auths, credsStore or credential helpers.
        original_config = Path(os.environ.get('DOCKER_CONFIG') or Path.home() / '.docker') / 'config.json'
        if original_config.exists():
            plugin_directories = json.loads(original_config.read_text()).get('cliPluginsExtraDirs', [])
            if (not isinstance(plugin_directories, list)
                    or any(not isinstance(item, str) or not Path(item).is_absolute()
                           for item in plugin_directories)):
                raise ValueError('Docker 插件目录配置无效，无法安全执行匿名验证')
            (Path(temporary) / 'config.json').write_text(json.dumps({
                'cliPluginsExtraDirs': plugin_directories,
            }), encoding='utf-8')
        environment = dict(os.environ)
        environment.pop('DOCKER_CONTEXT', None)
        environment.pop('DOCKER_AUTH_CONFIG', None)
        environment.update(DOCKER_CONFIG=temporary, DOCKER_HOST=current_docker_host())
        if remote_index(immutable, environment=environment) != verified:
            raise ValueError('Docker Hub 多架构索引无法匿名读取或摘要不匹配')
        for architecture in ('amd64', 'arm64'):
            try:
                subprocess.check_output([
                    'docker', 'image', 'pull', '--platform', f'linux/{architecture}', immutable,
                ], env=environment, stderr=subprocess.PIPE)
                raw = subprocess.check_output([
                    'docker', 'image', 'inspect', '--platform', f'linux/{architecture}', immutable,
                ], env=environment, stderr=subprocess.PIPE)
                info = json.loads(raw)[0]
            except (subprocess.CalledProcessError, ValueError, IndexError, TypeError) as exc:
                raise ValueError('Docker Hub 多架构索引无法匿名拉取或检查') from exc
            if (info.get('Id') not in {verified['digest'], expected[architecture], images[architecture]['image_config_id']}
                    or info.get('Os') != 'linux' or info.get('Architecture') != architecture):
                raise ValueError('Docker Hub 索引拉取结果不是预期架构或内容')
        existing_version = remote_index(version_reference, absent_ok=True)
        if existing_version is not None and existing_version != verified:
            raise ValueError('Docker Hub 已有同版本 tag 但索引摘要或成员不同；拒绝覆盖')
        if existing_version is None:
            try:
                subprocess.check_output([
                    'docker', 'buildx', 'imagetools', 'create',
                    '--tag', version_reference, immutable,
                ], stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as exc:
                raise ValueError('Docker Hub 版本 tag 发布失败') from exc
        verified_version = remote_index(version_reference)
        if verified_version != verified:
            raise ValueError('Docker Hub 版本 tag 与固定索引摘要或成员不一致')
        if remote_index(version_reference, environment=environment) != verified:
            raise ValueError('Docker Hub 版本 tag 无法匿名读取或内容不一致')
    # Only persist the convenient tag after both the fixed index and alias pass
    # authenticated and anonymous readback. Historical manifests may omit it.
    manifest['registry_version_ref'] = version_reference
    return immutable


def publish_image(root, architecture, reference, manifest):
    inspected, versions = inspect_image(architecture, reference)
    image_id = inspected['Id']
    for key, value in versions.items():
        if manifest.get(key, value) != value:
            raise ValueError('现有发行 manifest 版本与本次不一致；请使用独立发行目录')
    manifest.update(versions, release=__version__)
    filename = f'{__version__}/linux-{architecture}.tar.gz'
    destination = root / filename
    private_directory_check(destination.parent)
    previous = manifest.setdefault('images', {}).get(architecture)
    if previous and previous.get('image_id') != image_id:
        raise ValueError('同版本镜像记录与本次镜像 ID 不匹配；拒绝替换')
    if destination.exists():
        if destination.is_symlink() or not previous:
            raise ValueError('同版本镜像已存在且不匹配；拒绝覆盖，请先规划新版本发布')
        if sha256(destination) != previous.get('sha256') or destination.stat().st_size != previous.get('size_bytes'):
            raise ValueError('已发布镜像与 manifest 不符；拒绝继续')
        config_id = archive_config_id(destination, reference, inspected)
        if previous.get('image_config_id') != config_id:
            raise ValueError('同版本镜像记录与归档配置摘要不匹配；拒绝替换')
        audit_release_archive(destination)
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix='.image-', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as output, tempfile.TemporaryFile() as errors:
            with subprocess.Popen(['docker', 'image', 'save', reference], stdout=subprocess.PIPE, stderr=errors) as process:
                with gzip.GzipFile(fileobj=output, mode='wb', compresslevel=1, mtime=0) as archive:
                    shutil.copyfileobj(process.stdout, archive, length=1024 * 1024)
                if process.wait() != 0:
                    raise ValueError('Docker 镜像导出失败；未发布不完整文件')
            output.flush()
            os.fsync(output.fileno())
        config_id = archive_config_id(temporary, reference, inspected)
        if previous and previous.get('image_config_id') != config_id:
            raise ValueError('同版本镜像记录与归档配置摘要不匹配；拒绝替换')
        # The temporary name is not a public release filename.  Do not expose
        # the archive through Caddy until the all-layer secret audit passes.
        audit_release_archive(temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    record = {
        'filename': filename, 'sha256': sha256(destination),
        'size_bytes': destination.stat().st_size,
        'image_ref': reference, 'image_id': image_id, 'image_config_id': config_id,
    }
    if previous:
        if previous.get('registry_ref'):
            record['registry_ref'] = previous['registry_ref']
    else:
        manifest.pop('registry_index_ref', None)
        manifest.pop('registry_version_ref', None)
    manifest['images'][architecture] = record


def write_manifest(root, manifest, manifest_path):
    descriptor, temporary_name = tempfile.mkstemp(prefix='.manifest-', dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', action='append', required=True, metavar='ARCH=REFERENCE')
    parser.add_argument('--registry', metavar='docker.io/DOCKERID/REPO',
                        help='明确指定时才向公开 Docker Hub 仓库推送')
    parser.add_argument('--publish-index', action='store_true',
                        help='同时发布并匿名验证两种架构索引，供单条 docker run 使用')
    parser.add_argument('--output', type=Path, default=ROOT / 'backend/resource/performance-node')
    options = parser.parse_args(argv)
    registry = registry_repository(options.registry) if options.registry else None
    if options.publish_index and (not registry or len(options.image) != 2
                                 or {item.partition('=')[0] for item in options.image} != {'amd64', 'arm64'}):
        raise ValueError('--publish-index 必须同时指定 --registry 及两种架构 --image')
    root = options.output.absolute()
    private_directory_check(root)
    manifest_path = root / 'manifest.json'
    lock_path = root / '.publish.lock'
    if lock_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError('发行文件不能是符号链接')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if manifest.get('release', __version__) != __version__:
            raise ValueError('发行目录包含其他版本，请使用独立目录')
        for item in options.image:
            architecture, separator, reference = item.partition('=')
            if not separator:
                raise ValueError('--image 必须为 ARCH=REFERENCE')
            publish_image(root, architecture, reference, manifest)
            # Commit each completed architecture; a later failed build must not
            # strand earlier archives without a manifest or block a safe retry.
            write_manifest(root, manifest, manifest_path)
            if registry:
                record = manifest['images'][architecture]
                registry_ref = publish_registry_image(
                    root / record['filename'], architecture,
                    {'Id': record['image_id']}, record['image_config_id'], registry,
                )
                # A push failure intentionally leaves the just-committed tar
                # record (and any prior registry_ref) intact.
                record['registry_ref'] = registry_ref
                write_manifest(root, manifest, manifest_path)
        if options.publish_index:
            manifest['registry_index_ref'] = publish_registry_index(manifest, registry)
            write_manifest(root, manifest, manifest_path)
    print(json.dumps({'release': manifest['release'], 'architectures': sorted(manifest['images']),
                      'manifest': str(manifest_path)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'发布失败：{exc}') from None
