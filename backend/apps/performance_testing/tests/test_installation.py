from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
from performance_testing.installation import (
    _sha256_file, installation_metadata, load_release_configuration,
)
from performance_testing.models import PerformanceNode
from projects.models import Project, ProjectMember
from users.models import User


class ReleaseFixtureMixin:
    def set_up_release_fixture(self):
        self.release_temporary_directory = TemporaryDirectory(prefix='performance-release-test-')
        self.addCleanup(self.release_temporary_directory.cleanup)
        self.root = Path(self.release_temporary_directory.name)
        self.backend_directory = self.root / 'backend'
        self.backend_directory.mkdir()

        self.runtime_path = (
            self.root / 'performance-node' / 'src' / 'performance_node'
            / 'locust_runtime.py'
        )
        self.runtime_path.parent.mkdir(parents=True)
        self.runtime_path.write_bytes(b'# fixed runtime\n')

        self.installer_path = self.root / 'deploy' / 'performance' / 'install-node.sh'
        self.installer_path.parent.mkdir(parents=True)
        self.installer_bytes = b'#!/usr/bin/env bash\nset -euo pipefail\n'
        self.installer_path.write_bytes(self.installer_bytes)

        self.release_directory = self.root / 'release'
        self.release_directory.mkdir()
        images = {}
        for index, architecture in enumerate(('amd64', 'arm64'), start=1):
            archive = (
                self.release_directory / AGENT_VERSION
                / f'linux-{architecture}.tar.gz'
            )
            archive.parent.mkdir(exist_ok=True)
            content = (f'{architecture}-archive'.encode('ascii')) * index
            archive.write_bytes(content)
            images[architecture] = {
                'filename': f'{AGENT_VERSION}/linux-{architecture}.tar.gz',
                'sha256': hashlib.sha256(content).hexdigest(),
                'size_bytes': len(content),
                'image_ref': f'automation-platform-performance-node:{AGENT_VERSION}-{architecture}',
                'image_id': f"sha256:{str(index) * 64}",
                'image_config_id': f"sha256:{str(index + 2) * 64}",
            }
        self.manifest = {
            'release': AGENT_VERSION,
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
            'runtime_sha256': hashlib.sha256(self.runtime_path.read_bytes()).hexdigest(),
            'images': images,
        }
        self.write_manifest()

    def write_manifest(self):
        (self.release_directory / 'manifest.json').write_text(
            json.dumps(self.manifest), encoding='utf-8',
        )

    @contextmanager
    def release_environment(self, **changes):
        environment = {
            'PERFORMANCE_NODE_PUBLIC_URL': 'https://platform.example.test/base',
            'PERFORMANCE_NODE_RELEASE_DIR': str(self.release_directory),
        }
        for key, value in changes.items():
            if value is not None:
                environment[key] = value
            else:
                environment.pop(key, None)
        with override_settings(BASE_DIR=self.backend_directory), patch.dict(
            os.environ, environment, clear=True,
        ):
            yield

    @staticmethod
    def first_system_ca_certificate():
        from requests.certs import where

        bundle = Path(where()).read_text(encoding='ascii')
        match = re.search(
            r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\s*',
            bundle,
            flags=re.DOTALL,
        )
        if match is None:
            raise AssertionError('测试环境的 CA bundle 不含证书')
        return match.group(0).encode('ascii')

    @staticmethod
    def generated_certificate(*, is_ca, not_before, not_after):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'installation-test')])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
            .sign(private_key, hashes.SHA256())
        )
        return certificate.public_bytes(serialization.Encoding.PEM)


class InstallationConfigurationTests(ReleaseFixtureMixin, SimpleTestCase):
    def setUp(self):
        self.set_up_release_fixture()

    def test_valid_release_supports_subpath_and_does_not_hash_large_archives(self):
        with self.release_environment(), patch(
            'performance_testing.installation._sha256_file', wraps=_sha256_file,
        ) as sha256_file:
            configuration = load_release_configuration()

        self.assertEqual(configuration.platform_url, 'https://platform.example.test/base')
        self.assertEqual(
            [item.architecture for item in configuration.architectures],
            ['amd64', 'arm64'],
        )
        self.assertEqual(configuration.agent_version, AGENT_VERSION)
        hashed_paths = [call.args[0] for call in sha256_file.call_args_list]
        self.assertEqual(hashed_paths, [self.runtime_path.resolve()])

    def test_missing_configuration_or_installer_is_explicitly_unavailable(self):
        with override_settings(BASE_DIR=self.backend_directory), patch.dict(os.environ, {}, clear=True):
            missing_environment = installation_metadata()
        self.assertFalse(missing_environment['available'])
        self.assertIn('PERFORMANCE_NODE_PUBLIC_URL', missing_environment['reason'])
        self.assertNotIn('command', missing_environment)

        self.installer_path.unlink()
        with self.release_environment():
            missing_installer = installation_metadata()
        self.assertFalse(missing_installer['available'])
        self.assertIn('安装脚本', missing_installer['reason'])
        self.assertNotIn('command', missing_installer)

    def test_public_url_rejects_non_https_credentials_query_fragment_and_injection(self):
        invalid_urls = (
            'http://platform.example.test',
            'https://user@platform.example.test',
            'https://platform.example.test/base?token=x',
            'https://platform.example.test/base#fragment',
            'https://platform.example.test/base/../admin',
            'https://platform.example.test/base/%2e%2e/admin',
            'https://platform.example.test/base\nmalicious',
            'https://platform.example.test\\evil',
        )
        for value in invalid_urls:
            with self.subTest(value=value), self.release_environment(
                PERFORMANCE_NODE_PUBLIC_URL=value,
            ):
                metadata = installation_metadata()
                self.assertFalse(metadata['available'])
                self.assertNotIn('command', metadata)

    def test_manifest_rejects_versions_runtime_size_paths_and_injected_image_refs(self):
        mutations = (
            ('wrong version', lambda: self.manifest.update(release='9.9.9')),
            ('wrong protocol type', lambda: self.manifest.update(protocol_version=True)),
            ('wrong runtime', lambda: self.manifest.update(runtime_sha256='0' * 64)),
            ('wrong size', lambda: self.manifest['images']['amd64'].update(size_bytes=999)),
            ('traversal', lambda: self.manifest['images']['amd64'].update(filename='../image.tar.gz')),
            ('injection', lambda: self.manifest['images']['amd64'].update(image_ref='$(touch /tmp/pwned)')),
            ('bad config ID', lambda: self.manifest['images']['amd64'].update(image_config_id='sha256:bad')),
            ('missing config ID', lambda: self.manifest['images']['amd64'].pop('image_config_id')),
        )
        original = json.loads(json.dumps(self.manifest))
        for label, mutate in mutations:
            with self.subTest(label=label):
                self.manifest = json.loads(json.dumps(original))
                mutate()
                self.write_manifest()
                with self.release_environment():
                    metadata = installation_metadata()
                self.assertFalse(metadata['available'], metadata)
                self.assertNotIn('command', metadata)

    def test_release_archive_symlink_is_rejected(self):
        archive = self.release_directory / AGENT_VERSION / 'linux-amd64.tar.gz'
        outside = self.root / 'outside.tar.gz'
        outside.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(outside)
        with self.release_environment():
            metadata = installation_metadata()
        self.assertFalse(metadata['available'])
        self.assertIn('符号链接', metadata['reason'])

    def test_release_archive_parent_symlink_inside_release_root_is_rejected(self):
        version_directory = self.release_directory / AGENT_VERSION
        real_version_directory = self.release_directory / 'real-version-directory'
        version_directory.rename(real_version_directory)
        version_directory.symlink_to(real_version_directory, target_is_directory=True)
        with self.release_environment():
            metadata = installation_metadata()
        self.assertFalse(metadata['available'])
        self.assertIn('符号链接', metadata['reason'])

    def test_empty_architectures_and_invalid_json_types_are_safe_unavailable_results(self):
        cases = (
            {'images': {}},
            {'images': []},
            {'images': {'amd64': 'not-an-object'}},
        )
        original = json.loads(json.dumps(self.manifest))
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.manifest = {**json.loads(json.dumps(original)), **mutation}
                self.write_manifest()
                with self.release_environment():
                    metadata = installation_metadata()
                self.assertFalse(metadata['available'])
                self.assertIsInstance(metadata['reason'], str)

        (self.release_directory / 'manifest.json').write_text('[1, 2, 3]', encoding='utf-8')
        with self.release_environment():
            metadata = installation_metadata()
        self.assertFalse(metadata['available'])
        self.assertIn('顶层', metadata['reason'])

    def test_private_key_ca_is_rejected_and_public_ca_is_embedded(self):
        ca_path = self.root / 'platform-ca.pem'
        ca_path.write_text(
            '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n',
            encoding='ascii',
        )
        with self.release_environment(PERFORMANCE_NODE_CA_CERT_FILE=str(ca_path)):
            rejected = installation_metadata()
        self.assertFalse(rejected['available'])
        self.assertIn('PRIVATE KEY', rejected['reason'])

        certificate = self.first_system_ca_certificate()
        ca_path.write_bytes(certificate)
        node = type('Node', (), {
            'pk': '00000000-0000-0000-0000-000000000001',
            'enrollment_expires_at': timezone.now() + timedelta(minutes=15),
        })()
        with self.release_environment(PERFORMANCE_NODE_CA_CERT_FILE=str(ca_path)):
            accepted = installation_metadata(node=node, enrollment_token='one-time-token')
        command = accepted['command']
        self.assertTrue(accepted['available'])
        self.assertIn('--ca-file', command)
        self.assertIn('base64 --decode', command)
        self.assertIn(base64.b64encode(certificate).decode('ascii'), command)

    def test_leaf_and_expired_certificates_are_not_accepted_as_public_ca(self):
        ca_path = self.root / 'platform-ca.pem'
        now = datetime.now(datetime_timezone.utc)
        cases = (
            ('leaf', self.generated_certificate(
                is_ca=False, not_before=now - timedelta(minutes=1),
                not_after=now + timedelta(days=1),
            ), 'CA 证书'),
            ('expired', self.generated_certificate(
                is_ca=True, not_before=now - timedelta(days=2),
                not_after=now - timedelta(days=1),
            ), '有效期'),
        )
        for label, certificate, expected_reason in cases:
            with self.subTest(label=label):
                ca_path.write_bytes(certificate)
                with self.release_environment(PERFORMANCE_NODE_CA_CERT_FILE=str(ca_path)):
                    metadata = installation_metadata()
                self.assertFalse(metadata['available'])
                self.assertIn(expected_reason, metadata['reason'])

    def test_bootstrap_is_one_safe_command_with_pinned_downloads_and_no_system_ca_override(self):
        token = "node.$(touch /tmp/must-not-run)'"
        node = type('Node', (), {
            'pk': '00000000-0000-0000-0000-000000000001',
            'enrollment_expires_at': timezone.now() + timedelta(minutes=15),
        })()
        with self.release_environment():
            metadata = installation_metadata(node=node, enrollment_token=token)
        command = metadata['command']
        bootstrap_script = shlex.split(command)[2]

        self.assertTrue(metadata['available'])
        self.assertTrue(command.startswith('bash -c '))
        self.assertIn('umask 077', bootstrap_script)
        self.assertIn('mktemp -d', bootstrap_script)
        self.assertIn('chmod 0700', bootstrap_script)
        self.assertIn('chmod 0600', bootstrap_script)
        self.assertIn('trap cleanup', bootstrap_script)
        self.assertIn("--proto '=https'", bootstrap_script)
        self.assertIn('--max-time 120', bootstrap_script)
        self.assertNotIn('--location', bootstrap_script)
        self.assertIn('sha256sum --check --status', bootstrap_script)
        self.assertIn('下载安装脚本失败', bootstrap_script)
        self.assertIn('安装脚本 SHA256 校验失败', bootstrap_script)
        self.assertIn('发行镜像不可达', bootstrap_script)
        self.assertIn('--range 0-0', bootstrap_script)
        self.assertIn('--token-file', bootstrap_script)
        self.assertIn('--image-config-id', bootstrap_script)
        self.assertIn(self.manifest['images']['amd64']['image_config_id'], bootstrap_script)
        self.assertIn(self.manifest['images']['arm64']['image_config_id'], bootstrap_script)
        self.assertNotIn('--ca-file', bootstrap_script)
        self.assertNotIn('curl -k', bootstrap_script)
        self.assertNotIn('| bash', bootstrap_script)
        self.assertNotIn('PERFORMANCE_NODE_ENROLLMENT_TOKEN=', bootstrap_script)
        self.assertNotIn('?token=', bootstrap_script)
        self.assertEqual(bootstrap_script.count(token), 1)
        self.assertEqual(
            subprocess.run(
                ['bash', '-n', '-c', command], capture_output=True, text=True, check=False,
            ).returncode,
            0,
        )


class InstallationAPITests(ReleaseFixtureMixin, TestCase):
    def setUp(self):
        self.set_up_release_fixture()
        self.environment_context = self.release_environment()
        self.environment_context.__enter__()
        self.addCleanup(self.environment_context.__exit__, None, None, None)

        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='installation-admin', email='installation-admin@example.test', is_staff=True,
        )
        self.editor = User.objects.create_user(
            username='installation-editor', email='installation-editor@example.test',
        )
        self.outsider = User.objects.create_user(
            username='installation-outsider', email='installation-outsider@example.test',
        )
        self.project = Project.objects.create(
            name='installation', project_type='perf', created_by=self.admin,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.editor, role='editor', can_edit=True,
        )
        self.root_url = f'/api/v1/projects/{self.project.pk}/performance/'

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def create_node(self):
        self.authenticate(self.admin)
        return self.client.post(self.root_url + 'nodes/', {
            'name': 'safe node name; $(ignored)',
            'network_mode': 'public',
        }, format='json')

    def test_create_get_and_registered_at_contract(self):
        created = self.create_node()
        self.assertEqual(created.status_code, 201, created.data)
        data = created.data['data']
        self.assertIn('node', data)
        self.assertIn('expires_at', data)
        self.assertIn('installation', data)
        self.assertIsNone(data['node']['registered_at'])
        self.assertTrue(data['installation']['available'])
        self.assertIsInstance(data['installation']['requirements'], list)
        self.assertIn('command', data['installation'])
        token = data['enrollment_token']
        node_id = data['node']['id']
        self.assertIn(token, data['installation']['command'])
        self.assertNotIn('safe node name', data['installation']['command'])

        installation_url = self.root_url + f'nodes/{node_id}/installation/'
        fetched = self.client.get(installation_url)
        self.assertEqual(fetched.status_code, 200, fetched.data)
        self.assertEqual(set(fetched.data['data']), {'node', 'installation'})
        self.assertNotIn('command', fetched.data['data']['installation'])
        self.assertNotIn(token, json.dumps(fetched.data, default=str))

        self.client.force_authenticate(user=None)
        enrolled = self.client.post('/api/v1/performance-agent/enroll/', {
            'enrollment_token': token,
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
        }, format='json')
        self.assertEqual(enrolled.status_code, 200, enrolled.data)

        self.authenticate(self.admin)
        detail = self.client.get(self.root_url + f'nodes/{node_id}/')
        self.assertIsNotNone(detail.data['data']['registered_at'])
        reissue = self.client.post(
            self.root_url + f'nodes/{node_id}/enrollment/', {}, format='json',
        )
        self.assertEqual(reissue.status_code, 409, reissue.data)

    def test_installation_get_requires_platform_admin_and_project_visibility(self):
        created = self.create_node()
        node_id = created.data['data']['node']['id']
        path = self.root_url + f'nodes/{node_id}/installation/'

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(path).status_code, 401)
        self.authenticate(self.editor)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.authenticate(self.outsider)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_config_exposes_only_non_sensitive_installation_availability(self):
        created = self.create_node()
        token = created.data['data']['enrollment_token']
        self.authenticate(self.editor)
        response = self.client.get(self.root_url + 'config/')
        self.assertEqual(response.status_code, 200, response.data)
        installation = response.data['data']['installation']
        self.assertTrue(installation['available'])
        self.assertNotIn('command', installation)
        self.assertNotIn(token, json.dumps(response.data, default=str))

    def test_public_installer_is_raw_no_store_and_rejects_queries(self):
        self.client.force_authenticate(user=None)
        path = '/api/v1/performance-agent/install/install.sh'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.installer_bytes)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

        rejected = self.client.get(path + '?token=must-not-appear')
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn(b'must-not-appear', rejected.content)

    def test_public_installer_missing_file_has_safe_unavailable_message(self):
        self.installer_path.unlink()
        response = self.client.get('/api/v1/performance-agent/install/install.sh')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('暂不可用', response.content.decode('utf-8'))
        self.assertNotIn(str(self.root), response.content.decode('utf-8'))

    def test_registered_at_is_read_only(self):
        created = self.create_node()
        node_id = created.data['data']['node']['id']
        attempted = self.client.patch(
            self.root_url + f'nodes/{node_id}/',
            {'registered_at': '2026-01-01T00:00:00Z'},
            format='json',
        )
        self.assertEqual(attempted.status_code, 200, attempted.data)
        self.assertIsNone(PerformanceNode.objects.get(pk=node_id).enrollment_consumed_at)
