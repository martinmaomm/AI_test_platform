from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
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
                'registry_ref': (
                    f'docker.io/automationplatform/performance-node-{architecture}'
                    f'@sha256:{str(index + 4) * 64}'
                ),
            }
        self.manifest = {
            'release': AGENT_VERSION,
            'protocol_version': PROTOCOL_VERSION,
            'agent_version': AGENT_VERSION,
            'engine_version': ENGINE_VERSION,
            'runtime_sha256': hashlib.sha256(self.runtime_path.read_bytes()).hexdigest(),
            'registry_index_ref': (
                'docker.io/automationplatform/performance-node@sha256:' + '9' * 64
            ),
            'images': images,
        }
        self.write_manifest()

    def write_manifest(self):
        (self.release_directory / 'manifest.json').write_text(
            json.dumps(self.manifest), encoding='utf-8',
        )

    def enable_registry_transport(self):
        for index, (architecture, image) in enumerate(
            self.manifest['images'].items(), start=5,
        ):
            image['registry_ref'] = (
                f'docker.io/automationplatform/performance-node-{architecture}'
                f'@sha256:{str(index) * 64}'
            )
        self.write_manifest()

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

    def test_missing_configuration_is_unavailable_but_web_command_does_not_need_installer(self):
        with override_settings(BASE_DIR=self.backend_directory), patch.dict(os.environ, {}, clear=True):
            missing_environment = installation_metadata()
        self.assertFalse(missing_environment['available'])
        self.assertIn('PERFORMANCE_NODE_PUBLIC_URL', missing_environment['reason'])
        self.assertNotIn('command', missing_environment)

        self.installer_path.unlink()
        with self.release_environment():
            missing_installer = installation_metadata()
        self.assertTrue(missing_installer['available'])
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

    def test_registry_ref_is_strictly_pinned_and_manifest_digest_can_differ(self):
        self.enable_registry_transport()
        with self.release_environment():
            configuration = load_release_configuration()
        self.assertEqual(
            configuration.architectures[0].registry_ref,
            'docker.io/automationplatform/performance-node-amd64@'
            + 'sha256:' + '5' * 64,
        )

        invalid_references = (
            None,
            '',
            'ghcr.io/automationplatform/performance-node@' + 'sha256:' + '1' * 64,
            'docker.io/user:password/performance-node@' + 'sha256:' + '1' * 64,
            'docker.io/automationplatform/performance-node:latest',
            'docker.io/AutomationPlatform/performance-node@' + 'sha256:' + '1' * 64,
            'docker.io/automationplatform/team/performance-node@' + 'sha256:' + '1' * 64,
            'docker.io/automationplatform/performance-node@sha256:' + 'A' * 64,
            'docker.io/automationplatform/performance-node@sha256:' + '1' * 63,
            'docker.io/automationplatform/performance-node@sha256:' + '1' * 64 + '?x=1',
        )
        for registry_ref in invalid_references:
            with self.subTest(registry_ref=registry_ref):
                self.manifest['images']['amd64']['registry_ref'] = registry_ref
                self.write_manifest()
                with self.release_environment():
                    metadata = installation_metadata()
                self.assertFalse(metadata['available'], metadata)
                self.assertNotIn('command', metadata)

    def test_registry_index_ref_is_required_and_strictly_pinned(self):
        expected = self.manifest['registry_index_ref']
        with self.release_environment():
            self.assertEqual(load_release_configuration().registry_index_ref, expected)

        invalid_references = (
            None,
            '',
            'ghcr.io/automationplatform/performance-node@sha256:' + '1' * 64,
            'docker.io/automationplatform/performance-node:0.2.1',
            'docker.io/automationplatform/performance-node@sha256:' + 'A' * 64,
            'docker.io/automationplatform/performance-node@sha256:' + '1' * 63,
        )
        for registry_index_ref in invalid_references:
            with self.subTest(registry_index_ref=registry_index_ref):
                if registry_index_ref is None:
                    self.manifest.pop('registry_index_ref', None)
                else:
                    self.manifest['registry_index_ref'] = registry_index_ref
                self.write_manifest()
                with self.release_environment():
                    metadata = installation_metadata()
                self.assertFalse(metadata['available'], metadata)
                self.assertNotIn('command', metadata)
                self.manifest['registry_index_ref'] = expected

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

    def test_private_key_ca_is_rejected_and_public_ca_digest_is_pinned(self):
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
        arguments = shlex.split(command)
        digest_position = arguments.index('--ca-sha256')
        self.assertEqual(
            arguments[digest_position + 1], hashlib.sha256(certificate).hexdigest(),
        )
        self.assertNotIn(certificate.decode('ascii').strip(), command)

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

    def test_ca_larger_than_agent_limit_is_rejected(self):
        ca_path = self.root / 'oversized-platform-ca.pem'
        ca_path.write_bytes(b'X' * (64 * 1024 + 1))
        with self.release_environment(PERFORMANCE_NODE_CA_CERT_FILE=str(ca_path)):
            metadata = installation_metadata()
        self.assertFalse(metadata['available'])
        self.assertIn('文件过大', metadata['reason'])
        self.assertNotIn('command', metadata)

    def test_web_install_is_one_safe_docker_run_with_fixed_index_and_hardening(self):
        token = "node.$(touch /tmp/must-not-run)'"
        node = type('Node', (), {
            'pk': '00000000-0000-0000-0000-000000000001',
            'enrollment_expires_at': timezone.now() + timedelta(minutes=15),
        })()
        with self.release_environment():
            metadata = installation_metadata(node=node, enrollment_token=token)
        command = metadata['command']
        arguments = shlex.split(command)

        self.assertTrue(metadata['available'])
        self.assertNotIn('\n', command)
        self.assertEqual(arguments[:4], ['docker', 'run', '-d', '--name'])
        self.assertEqual(metadata['container_name'], arguments[4])
        self.assertRegex(
            metadata['container_name'],
            r'^performance-node-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$',
        )
        expected_options = [
            '--restart', 'unless-stopped', '--init', '--read-only',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', '512m', '--cpus', '1', '--pids-limit', '128',
            '--stop-timeout', '25', '--log-opt', 'max-size=10m',
            '--log-opt', 'max-file=3', '--mount',
        ]
        self.assertEqual(arguments[5:5 + len(expected_options)], expected_options)
        mount = arguments[5 + len(expected_options)]
        self.assertRegex(
            mount,
            r'^type=volume,src=performance-node-[0-9a-f-]{36}-identity,dst=/var/lib/performance-node$',
        )
        self.assertEqual(
            mount,
            'type=volume,src=performance-node-00000000-0000-0000-0000-000000000001-identity,'
            'dst=/var/lib/performance-node',
        )
        image_position = 6 + len(expected_options)
        self.assertEqual(arguments[image_position], self.manifest['registry_index_ref'])
        self.assertEqual(arguments[image_position + 1:], [
            'start', '--server', 'https://platform.example.test/base',
            '--node-id', str(node.pk), '--token', token,
        ])
        for forbidden in ('bash', 'curl', 'base64', 'sh', '&&'):
            self.assertNotIn(forbidden, arguments)
        self.assertNotIn('--privileged', arguments)
        self.assertNotIn('--network', arguments)
        self.assertNotIn('/var/run/docker.sock', command)

    def test_regenerated_command_reuses_node_owned_container_and_identity_volume(self):
        node = type('Node', (), {
            'pk': '00000000-0000-0000-0000-000000000001',
            'enrollment_expires_at': timezone.now() + timedelta(minutes=15),
        })()
        with self.release_environment():
            first = installation_metadata(node=node, enrollment_token='first-token')
            second = installation_metadata(node=node, enrollment_token='second-token')
        first_arguments = shlex.split(first['command'])
        second_arguments = shlex.split(second['command'])
        self.assertEqual(first['container_name'], second['container_name'])
        self.assertEqual(
            first_arguments[first_arguments.index('--mount') + 1],
            second_arguments[second_arguments.index('--mount') + 1],
        )
        self.assertNotEqual(first['command'], second['command'])

    def test_noncanonical_node_id_cannot_generate_a_command(self):
        node = type('Node', (), {
            'pk': '00000000-0000-0000-0000-00000000000A',
            'enrollment_expires_at': timezone.now() + timedelta(minutes=15),
        })()
        with self.release_environment():
            metadata = installation_metadata(node=node, enrollment_token='one-time-token')
        self.assertFalse(metadata['available'])
        self.assertIn('规范 UUID', metadata['reason'])
        self.assertNotIn('command', metadata)


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
        self.assertIn('container_name', data['installation'])
        token = data['enrollment_token']
        node_id = data['node']['id']
        command_arguments = shlex.split(data['installation']['command'])
        self.assertEqual(command_arguments[command_arguments.index('--token') + 1], token)
        self.assertIn(self.manifest['registry_index_ref'], command_arguments)
        self.assertNotIn('safe node name', data['installation']['command'])

        installation_url = self.root_url + f'nodes/{node_id}/installation/'
        fetched = self.client.get(installation_url)
        self.assertEqual(fetched.status_code, 200, fetched.data)
        self.assertEqual(set(fetched.data['data']), {'node', 'installation'})
        self.assertNotIn('command', fetched.data['data']['installation'])
        self.assertEqual(
            fetched.data['data']['installation']['container_name'],
            f'performance-node-{node_id}',
        )
        self.assertNotIn(token, json.dumps(fetched.data, default=str))

        listed = self.client.get(self.root_url + 'nodes/')
        self.assertNotIn(token, json.dumps(listed.data, default=str))

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
        self.assertNotIn('container_name', installation)
        self.assertNotIn(token, json.dumps(response.data, default=str))

    def test_registered_020_node_does_not_guess_the_legacy_container_name(self):
        created = self.create_node()
        node_id = created.data['data']['node']['id']
        PerformanceNode.objects.filter(pk=node_id).update(
            agent_version='0.2.0', enrollment_consumed_at=timezone.now(),
        )
        response = self.client.get(
            self.root_url + f'nodes/{node_id}/installation/',
        )
        self.assertEqual(response.status_code, 200, response.data)
        installation = response.data['data']['installation']
        self.assertNotIn('command', installation)
        self.assertNotIn('container_name', installation)

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

    def test_public_ca_is_no_store_query_free_and_independent_of_manifest(self):
        certificate = self.first_system_ca_certificate()
        ca_path = self.root / 'platform-ca.pem'
        ca_path.write_bytes(certificate)
        (self.release_directory / 'manifest.json').write_text('{broken', encoding='utf-8')
        path = '/api/v1/performance-agent/install/ca.pem'
        pem_accept = 'application/x-pem-file'
        self.client.force_authenticate(user=None)
        with patch.dict(
            os.environ, {'PERFORMANCE_NODE_CA_CERT_FILE': str(ca_path)}, clear=False,
        ):
            response = self.client.get(path, HTTP_ACCEPT=pem_accept)
            head = self.client.head(path, HTTP_ACCEPT=pem_accept)
            rejected = self.client.get(
                path + '?token=must-not-appear', HTTP_ACCEPT=pem_accept,
            )
            post = self.client.post(path, {}, format='json', HTTP_ACCEPT=pem_accept)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, certificate)
        self.assertEqual(response['Content-Type'], 'application/x-pem-file')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b'')
        self.assertEqual(head['Cache-Control'], 'no-store')
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn(b'must-not-appear', rejected.content)
        self.assertEqual(post.status_code, 405)

    def test_public_ca_returns_404_for_public_trust_and_fails_closed_on_private_key(self):
        path = '/api/v1/performance-agent/install/ca.pem'
        pem_accept = 'application/x-pem-file'
        self.client.force_authenticate(user=None)
        response = self.client.get(path, HTTP_ACCEPT=pem_accept)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Cache-Control'], 'no-store')

        ca_path = self.root / 'platform-ca.pem'
        ca_path.write_text(
            '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n',
            encoding='ascii',
        )
        with patch.dict(
            os.environ, {'PERFORMANCE_NODE_CA_CERT_FILE': str(ca_path)}, clear=False,
        ):
            rejected = self.client.get(path, HTTP_ACCEPT=pem_accept)
        self.assertEqual(rejected.status_code, 503)
        self.assertIn(b'PRIVATE KEY', rejected.content)

        ca_path.write_bytes(b'X' * (64 * 1024 + 1))
        with patch.dict(
            os.environ, {'PERFORMANCE_NODE_CA_CERT_FILE': str(ca_path)}, clear=False,
        ):
            oversized = self.client.get(path, HTTP_ACCEPT=pem_accept)
        self.assertEqual(oversized.status_code, 503)
        self.assertIn('文件过大', oversized.content.decode('utf-8'))

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
