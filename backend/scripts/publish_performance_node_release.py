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
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'performance-node/src'))
from performance_node import __version__, PROTOCOL_VERSION, ENGINE_VERSION

RUNTIME = ROOT / 'performance-node/src/performance_node/locust_runtime.py'


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


def inspect_image(architecture, reference):
    expected_ref = f'automation-platform-performance-node:{__version__}-{architecture}'
    if architecture not in ('amd64', 'arm64') or reference != expected_ref:
        raise ValueError(f'镜像必须为固定引用 {expected_ref}')
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', reference]))[0]
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
    raw = subprocess.check_output([
        'docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '128m', '--pids-limit', '32', '--entrypoint', 'python', info['Id'], '-c', probe,
    ], timeout=60)
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
    if destination.exists():
        if destination.is_symlink() or not previous or previous.get('image_id') != image_id:
            raise ValueError('同版本镜像已存在且不匹配；拒绝覆盖，请先规划新版本发布')
        if sha256(destination) != previous.get('sha256') or destination.stat().st_size != previous.get('size_bytes'):
            raise ValueError('已发布镜像与 manifest 不符；拒绝继续')
        previous['image_config_id'] = archive_config_id(destination, reference, inspected)
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
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    manifest['images'][architecture] = {
        'filename': filename, 'sha256': sha256(destination),
        'size_bytes': destination.stat().st_size,
        'image_ref': reference, 'image_id': image_id, 'image_config_id': config_id,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', action='append', required=True, metavar='ARCH=REFERENCE')
    parser.add_argument('--output', type=Path, default=ROOT / 'backend/resource/performance-node')
    options = parser.parse_args(argv)
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
    print(json.dumps({'release': manifest['release'], 'architectures': sorted(manifest['images']),
                      'manifest': str(manifest_path)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'发布失败：{exc}') from None
