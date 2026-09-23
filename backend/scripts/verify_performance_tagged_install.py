"""Run the generated pull/tag/start command against a temporary HTTPS fixture.

No business database or real node identity is used. Temporary containers and
volumes are cleaned up; the verified local version tag is retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import uuid

from verify_performance_docker_start import Fixture, LABEL, certificates, docker, wait_for
from performance_node import __version__
from publish_performance_node_release import current_docker_host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'), required=True)
    options = parser.parse_args()
    if not re.fullmatch(r'docker\.io/[a-z0-9-]+/[a-z0-9._-]+@sha256:[0-9a-f]{64}', options.image):
        raise ValueError('Use the verified Docker Hub immutable index reference')
    current_docker_host()  # Reject remote daemons before creating anything.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps'))
    from django.conf import settings
    if not settings.configured:
        settings.configure(USE_TZ=True)
    from performance_testing.installation import ReleaseConfiguration, _docker_command

    node_id = str(uuid.uuid4())
    token = node_id + '.' + secrets.token_urlsafe(24)
    container = 'performance-node-' + node_id
    volume = container + '-identity-' + hashlib.sha256(token.encode()).hexdigest()[:16]
    owner = str(uuid.uuid4())
    tag = options.image.split('@')[0] + ':' + __version__
    environment = dict(os.environ, DOCKER_DEFAULT_PLATFORM='linux/' + options.architecture)
    with tempfile.TemporaryDirectory(prefix='performance-tagged-install-') as folder:
        root = Path(folder)
        ca, cert, key = certificates(root)
        server = Fixture(ca, cert, key)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.enrollment_tokens[node_id] = token
        config = ReleaseConfiguration(
            platform_url=f'https://host.docker.internal:{server.server_port}',
            release=__version__, agent_version=__version__,
            registry_index_ref=options.image, architectures=(), ca_certificate=ca,
        )
        command = _docker_command(config, node_id=node_id, enrollment_token=token)
        docker('volume', 'create', '--label', LABEL + '=' + owner, volume)
        try:
            result = subprocess.run(
                ['/bin/sh', '-c', command], env=environment, capture_output=True,
                text=True, timeout=180,
            )
            if result.returncode:
                raise RuntimeError('Generated install command failed; fixture command/output withheld')
            wait_for(lambda: server.counts.get(node_id, {}).get('heartbeat', 0) >= 2,
                     'generated command did not produce heartbeat')
            info = json.loads(docker('container', 'inspect', container))[0]
            assert info['Config']['Image'] == options.image
            image = json.loads(docker('image', 'inspect', '--platform',
                                      'linux/' + options.architecture, tag))[0]
            fixed = json.loads(docker('image', 'inspect', '--platform',
                                      'linux/' + options.architecture, options.image))[0]
            assert image['Id'] == fixed['Id']
            assert image['Architecture'] == options.architecture
            assert image['Config']['Labels']['org.opencontainers.image.version'] == __version__
            assert tag.removeprefix('docker.io/') in image['RepoTags']
            before = server.counts[node_id]['heartbeat']
            docker('restart', container)
            wait_for(lambda: server.counts[node_id]['heartbeat'] >= before + 2,
                     'restart heartbeat missing')
            assert server.counts[node_id]['enroll'] == 1
            assert server.unsafe_requests == 0
            print(json.dumps({
                'architecture': options.architecture, 'generated_command': 'pull/tag/run',
                'local_version_tag': tag, 'tag_matches_digest': True,
                'run_uses_digest': True, 'startup_and_restart': True,
                'enrollments': server.counts[node_id]['enroll'],
            }), flush=True)
        finally:
            found = subprocess.run(['docker', 'container', 'inspect', container],
                                   capture_output=True, text=True)
            if found.returncode == 0:
                info = json.loads(found.stdout)[0]
                args = info['Config']['Cmd']
                if (info['Config']['Image'] != options.image or '--node-id' not in args
                        or args[args.index('--node-id') + 1] != node_id):
                    raise RuntimeError('Refuse cleanup: fixture container ownership mismatch')
                docker('rm', '-f', container)
            actual_owner = docker('volume', 'inspect', '--format',
                                  '{{index .Labels "' + LABEL + '"}}', volume)
            if actual_owner != owner:
                raise RuntimeError('Refuse cleanup: fixture volume ownership mismatch')
            docker('volume', 'rm', volume)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
