"""No Docker/network or business database access: release validation tests."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).with_name('publish_performance_node_release.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ReleaseTests(unittest.TestCase):
    def info(self):
        return {'Id': 'sha256:' + 'a' * 64, 'Os': 'linux', 'Architecture': 'amd64',
                'Config': {'User': 'nodeagent', 'Entrypoint': ['python', '-m', 'performance_node'],
                           'Cmd': ['run'], 'Env': ['PATH=/usr/bin']},
                'RootFS': {'Type': 'layers', 'Layers': ['sha256:' + 'd' * 64]}}

    def versions(self):
        return {'agent_version': publisher.__version__, 'protocol_version': publisher.PROTOCOL_VERSION,
                'engine_version': publisher.ENGINE_VERSION, 'runtime_sha256': publisher.sha256(publisher.RUNTIME)}

    def ref(self):
        return f'automation-platform-performance-node:{publisher.__version__}-amd64'

    def archive_document(self, info=None):
        info = info or self.info()
        return {'architecture': info['Architecture'], 'os': info['Os'],
                'config': info['Config'],
                'rootfs': {'type': info['RootFS']['Type'], 'diff_ids': info['RootFS']['Layers']}}

    def remote_manifest(self, config_id=None, digest=None):
        return json.dumps({
            'Descriptor': {
                'digest': digest or 'sha256:' + 'b' * 64,
                'platform': {'os': 'linux', 'architecture': 'amd64'},
            },
            'SchemaV2Manifest': {'config': {'digest': config_id or self.info()['Id']}},
        })

    def registry_ref(self):
        return f'docker.io/example/performance-node:{publisher.__version__}-amd64-' + 'c' * 12

    def write_archive(self, path, document, config_layout='classic'):
        content = json.dumps(document, separators=(',', ':')).encode()
        digest = hashlib.sha256(content).hexdigest()
        config_name = digest + '.json' if config_layout == 'classic' else 'blobs/sha256/' + digest
        with tarfile.open(path, 'w:gz') as archive:
            for name, value in [('manifest.json', json.dumps([{'Config': config_name, 'RepoTags': [self.ref()]}]).encode()),
                                (config_name, content)]:
                member = tarfile.TarInfo(name)
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        return digest

    def test_verified_image(self):
        with patch.object(publisher.subprocess, 'check_output', side_effect=[json.dumps([self.info()]), json.dumps(self.versions())]), \
                patch.object(publisher, '_cleanup_probe_container', return_value=True) as cleanup:
            inspected, versions = publisher.inspect_image('amd64', self.ref())
        self.assertEqual(inspected, self.info())
        self.assertEqual(versions, self.versions())
        cleanup.assert_called_once()

    def test_arbitrary_image_is_not_executed(self):
        with patch.object(publisher.subprocess, 'check_output') as command:
            with self.assertRaises(ValueError):
                publisher.inspect_image('amd64', 'unreviewed:latest')
            command.assert_not_called()

    def test_wrong_arch_root_secret_environment_and_entrypoint_rejected(self):
        for mode in ('architecture', 'root', 'secret', 'entrypoint'):
            with self.subTest(mode=mode):
                info = self.info()
                if mode == 'architecture': info['Architecture'] = 'arm64'
                if mode == 'root': info['Config']['User'] = 'root'
                if mode == 'secret': info['Config']['Env'].append('DATABASE_PASSWORD=fixture-not-a-real-secret')
                if mode == 'entrypoint': info['Config']['Entrypoint'] = ['sh']
                with patch.object(publisher.subprocess, 'check_output', return_value=json.dumps([info])) as command:
                    with self.assertRaises(ValueError): publisher.inspect_image('amd64', self.ref())
                    self.assertEqual(command.call_count, 1)

    def test_runtime_drift_rejected(self):
        versions = {**self.versions(), 'runtime_sha256': 'b' * 64}
        with patch.object(publisher.subprocess, 'check_output', side_effect=[json.dumps([self.info()]), json.dumps(versions)]), \
                patch.object(publisher, '_cleanup_probe_container', return_value=True):
            with self.assertRaises(ValueError): publisher.inspect_image('amd64', self.ref())

    def test_runtime_timeout_cleans_only_named_probe_and_has_explicit_platform(self):
        name_uuid = uuid.UUID('11111111-1111-4111-8111-111111111111')
        owner_uuid = uuid.UUID('22222222-2222-4222-8222-222222222222')
        with patch.object(publisher.subprocess, 'check_output', side_effect=[
            json.dumps([self.info()]), subprocess.TimeoutExpired('docker', 60),
        ]) as docker, patch.object(publisher.uuid, 'uuid4', side_effect=[name_uuid, owner_uuid]), \
                patch.object(publisher.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=str(owner_uuid) + '\n', stderr=''),
                    subprocess.CompletedProcess([], 0, stdout='', stderr=''),
                ]) as cleanup:
            with self.assertRaisesRegex(ValueError, '运行时检查失败或超时'):
                publisher.inspect_image('amd64', self.ref())
        command = docker.call_args_list[1].args[0]
        self.assertEqual(command[command.index('--platform') + 1], 'linux/amd64')
        name = command[command.index('--name') + 1]
        self.assertEqual(name, 'performance-release-probe-' + name_uuid.hex)
        self.assertEqual(command[command.index('--label') + 1],
                         publisher._PROBE_LABEL + '=' + str(owner_uuid))
        self.assertEqual(cleanup.call_args_list[0].args[0], [
            'docker', 'container', 'inspect', '--format',
            '{{index .Config.Labels "' + publisher._PROBE_LABEL + '"}}', name,
        ])
        self.assertEqual(cleanup.call_args_list[1].args[0],
                         ['docker', 'container', 'rm', '--force', '--volumes', name])

    def test_runtime_cleanup_failure_is_not_reported_as_cleaned(self):
        owner_uuid = uuid.UUID('22222222-2222-4222-8222-222222222222')
        with patch.object(publisher.subprocess, 'check_output', side_effect=[
            json.dumps([self.info()]), subprocess.TimeoutExpired('docker', 60),
        ]), patch.object(publisher.uuid, 'uuid4', side_effect=[
            uuid.UUID('11111111-1111-4111-8111-111111111111'), owner_uuid,
        ]), patch.object(publisher.subprocess, 'run', side_effect=[
            subprocess.CompletedProcess([], 0, stdout=str(owner_uuid) + '\n', stderr=''),
            subprocess.CompletedProcess([], 1, stdout='', stderr='busy'),
        ]):
            with self.assertRaisesRegex(ValueError, '清理失败；清理状态未确认') as raised:
                publisher.inspect_image('amd64', self.ref())
        self.assertNotIn('已清理', str(raised.exception))

    def test_runtime_cleanup_refuses_foreign_container(self):
        with patch.object(publisher.subprocess, 'run', return_value=
                          subprocess.CompletedProcess([], 0, stdout='another-owner\n', stderr='')) as docker:
            with self.assertRaisesRegex(ValueError, '归属不匹配'):
                publisher._cleanup_probe_container('probe-name', 'expected-owner')
        self.assertEqual(docker.call_count, 1)

    def test_existing_matching_archive_reused_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            filename = f'{publisher.__version__}/linux-amd64.tar.gz'
            archive = root / filename
            archive.parent.mkdir()
            archive.write_bytes(b'fixture archive')
            record = {'image_id': 'sha256:' + 'a' * 64, 'filename': filename,
                      'image_config_id': 'sha256:' + 'c' * 64,
                      'sha256': publisher.sha256(archive), 'size_bytes': archive.stat().st_size}
            manifest = {**self.versions(), 'release': publisher.__version__, 'images': {'amd64': record}}
            inspected = self.info()
            with patch.object(publisher, 'inspect_image', return_value=(inspected, self.versions())), \
                    patch.object(publisher, 'archive_config_id', return_value='sha256:' + 'c' * 64) as archive_check, \
                    patch.object(publisher, 'audit_release_archive') as audit, \
                    patch.object(publisher.subprocess, 'Popen') as export:
                publisher.publish_image(root, 'amd64', self.ref(), manifest)
                self.assertEqual(record['image_config_id'], 'sha256:' + 'c' * 64)
                archive_check.assert_called_once_with(archive, self.ref(), inspected)
                audit.assert_called_once_with(archive)
                export.assert_not_called()
                archive.write_bytes(b'changed')
                with self.assertRaises(ValueError): publisher.publish_image(root, 'amd64', self.ref(), manifest)
                export.assert_not_called()

    def test_missing_archive_resumes_only_the_same_recorded_image_and_config(self):
        class SavedImage:
            stdout = io.BytesIO(b'fixture image bytes')

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            filename = f'{publisher.__version__}/linux-amd64.tar.gz'
            registry_ref = 'docker.io/example/performance-node@sha256:' + 'b' * 64
            index_ref = 'docker.io/example/performance-node@sha256:' + 'f' * 64
            previous = {
                'filename': filename, 'sha256': 'd' * 64, 'size_bytes': 1,
                'image_ref': self.ref(), 'image_id': self.info()['Id'],
                'image_config_id': 'sha256:' + 'c' * 64, 'registry_ref': registry_ref,
            }
            manifest = {**self.versions(), 'release': publisher.__version__,
                        'registry_index_ref': index_ref, 'images': {'amd64': previous}}
            with patch.object(publisher, 'inspect_image', return_value=(self.info(), self.versions())), \
                    patch.object(publisher, 'archive_config_id', return_value='sha256:' + 'c' * 64), \
                    patch.object(publisher, 'audit_release_archive'), \
                    patch.object(publisher.subprocess, 'Popen', return_value=SavedImage()):
                publisher.publish_image(root, 'amd64', self.ref(), manifest)
            self.assertTrue((root / filename).is_file())
            self.assertEqual(manifest['images']['amd64']['registry_ref'], registry_ref)
            self.assertEqual(manifest['registry_index_ref'], index_ref)

    def test_missing_archive_rejects_different_recorded_image_or_config(self):
        class SavedImage:
            stdout = io.BytesIO(b'fixture image bytes')

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            @staticmethod
            def wait():
                return 0

        for mismatch in ('image_id', 'image_config_id'):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                filename = f'{publisher.__version__}/linux-amd64.tar.gz'
                previous = {
                    'filename': filename, 'sha256': 'd' * 64, 'size_bytes': 1,
                    'image_ref': self.ref(), 'image_id': self.info()['Id'],
                    'image_config_id': 'sha256:' + 'c' * 64,
                    'registry_ref': 'docker.io/example/performance-node@sha256:' + 'b' * 64,
                }
                manifest = {**self.versions(), 'release': publisher.__version__,
                            'registry_index_ref': 'docker.io/example/performance-node@sha256:' + 'f' * 64,
                            'images': {'amd64': previous}}
                inspected = self.info()
                if mismatch == 'image_id':
                    inspected['Id'] = 'sha256:' + 'e' * 64
                with patch.object(publisher, 'inspect_image', return_value=(inspected, self.versions())), \
                        patch.object(publisher, 'archive_config_id', return_value='sha256:' + 'e' * 64), \
                        patch.object(publisher.subprocess, 'Popen', return_value=SavedImage()) as export:
                    with self.assertRaisesRegex(ValueError, '拒绝替换'):
                        publisher.publish_image(root, 'amd64', self.ref(), manifest)
                self.assertIs(manifest['images']['amd64'], previous)
                self.assertIn('registry_index_ref', manifest)
                self.assertFalse((root / filename).exists())
                if mismatch == 'image_id':
                    export.assert_not_called()

    def test_failed_archive_audit_never_replaces_public_tar_or_adds_image_record(self):
        class SavedImage:
            stdout = io.BytesIO(b'fixture image bytes')

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = {}
            destination = root / f'{publisher.__version__}/linux-amd64.tar.gz'
            with patch.object(publisher, 'inspect_image', return_value=(self.info(), self.versions())), \
                    patch.object(publisher, 'archive_config_id', return_value='sha256:' + 'c' * 64), \
                    patch.object(publisher, 'audit_release_archive',
                                 side_effect=ValueError('sensitive archive fixture')) as audit, \
                    patch.object(publisher.subprocess, 'Popen', return_value=SavedImage()), \
                    patch.object(publisher.os, 'replace') as replace:
                with self.assertRaisesRegex(ValueError, 'sensitive archive'):
                    publisher.publish_image(root, 'amd64', self.ref(), manifest)
            audit.assert_called_once()
            replace.assert_not_called()
            self.assertFalse(destination.exists())
            self.assertNotIn('amd64', manifest.get('images', {}))

    def test_archive_config_digest_covers_classic_and_oci_layouts(self):
        for config_layout in ('classic', 'oci'):
            with self.subTest(config_layout=config_layout), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'image.tar.gz'
                digest = self.write_archive(path, self.archive_document(), config_layout)
                self.assertEqual(publisher.archive_config_id(path, self.ref(), self.info()), 'sha256:' + digest)
                with self.assertRaises(ValueError):
                    publisher.archive_config_id(path, 'another:tag', self.info())

    def test_tag_repointed_archive_config_or_layers_rejected(self):
        for changed_field in ('config', 'layers'):
            with self.subTest(changed_field=changed_field), tempfile.TemporaryDirectory() as temporary:
                document = self.archive_document()
                if changed_field == 'config':
                    document['config']['Cmd'] = ['unexpected']
                else:
                    document['rootfs']['diff_ids'] = ['sha256:' + 'e' * 64]
                path = Path(temporary) / 'image.tar.gz'
                self.write_archive(path, document)
                with self.assertRaisesRegex(ValueError, 'tag 可能已被重指'):
                    publisher.archive_config_id(path, self.ref(), self.info())

    def test_symlink_root_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            link = root / 'link'
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError): publisher.private_directory_check(link)

    def test_second_architecture_failure_preserves_first_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()

            def publish_one(directory, architecture, reference, manifest):
                if architecture == 'arm64':
                    raise ValueError('fixture second image unavailable')
                manifest.update(self.versions(), release=publisher.__version__, images={'amd64': {'image_id': self.info()['Id']}})

            with patch.object(publisher, 'publish_image', side_effect=publish_one):
                with self.assertRaisesRegex(ValueError, 'second image'):
                    publisher.main(['--output', str(root), '--image', 'amd64=' + self.ref(), '--image', 'arm64=fixture'])
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(set(manifest['images']), {'amd64'})

    def test_registry_repository_requires_explicit_docker_hub_namespace(self):
        self.assertEqual(publisher.registry_repository('docker.io/example/performance-node'),
                         'docker.io/example/performance-node')
        for invalid in ('example/performance-node', 'docker.io/example',
                        'ghcr.io/example/performance-node', 'docker.io/Example/repo',
                        'docker.io/example/nested/repo'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    publisher.registry_repository(invalid)

    def test_registry_push_uses_inspected_image_id_and_records_verified_digest(self):
        archive = Path('/fixture/verified.tar.gz')
        audit = Mock(return_value={'layers': 1})
        missing = subprocess.CalledProcessError(
            1, ['docker', 'manifest', 'inspect'], output=b'manifest unknown',
        )
        outputs = [
            missing,  # collision preflight: tag does not exist
            b'',      # docker image tag
            b'',      # docker image push
            self.remote_manifest('sha256:' + 'c' * 64).encode(),  # authenticated remote inspect
            json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///fixture/docker.sock'}}}]).encode(),
            # Docker context for the anonymous client
            self.remote_manifest('sha256:' + 'c' * 64).encode(),  # anonymous remote inspect
            b'',      # anonymous pull
            json.dumps([{**self.info(), 'Id': 'sha256:' + 'b' * 64}]).encode(),  # containerd manifest ID
        ]
        with patch.dict(os.environ, {'DOCKER_CONTEXT': 'must-not-leak',
                                     'DOCKER_AUTH_CONFIG': 'must-not-leak'}, clear=False), \
                patch.object(publisher.importlib, 'import_module',
                          return_value=SimpleNamespace(audit_archive=audit)), \
                patch.object(publisher.subprocess, 'check_output', side_effect=outputs) as docker:
            registry_ref = publisher.publish_registry_image(
                archive, 'amd64', self.info(), 'sha256:' + 'c' * 64,
                'docker.io/example/performance-node',
            )
        self.assertEqual(registry_ref, 'docker.io/example/performance-node@sha256:' + 'b' * 64)
        audit.assert_called_once_with(archive)
        self.assertIn(call(['docker', 'image', 'tag', self.info()['Id'], self.registry_ref()],
                           stderr=subprocess.PIPE), docker.call_args_list)
        self.assertIn(call(['docker', 'image', 'push', '--platform', 'linux/amd64', self.registry_ref()],
                           stderr=subprocess.PIPE), docker.call_args_list)
        anonymous_manifest_call = docker.call_args_list[5]
        self.assertIn('DOCKER_CONFIG', anonymous_manifest_call.kwargs['env'])
        self.assertEqual(anonymous_manifest_call.kwargs['env']['DOCKER_HOST'], 'unix:///fixture/docker.sock')
        self.assertNotIn('DOCKER_CONTEXT', anonymous_manifest_call.kwargs['env'])
        self.assertNotIn('DOCKER_AUTH_CONFIG', anonymous_manifest_call.kwargs['env'])
        self.assertNotIn('DOCKER_CONFIG', docker.call_args_list[1].kwargs)
        self.assertEqual(
            docker.call_args_list[6].args[0],
            ['docker', 'image', 'pull', 'docker.io/example/performance-node@sha256:' + 'b' * 64],
        )

    def test_registry_conflict_push_and_anonymous_pull_failures_do_not_return_reference(self):
        archive = Path('/fixture/verified.tar.gz')
        audit_module = SimpleNamespace(audit_archive=Mock())
        cases = {
            'conflict': [self.remote_manifest('sha256:' + 'd' * 64).encode()],
            'push': [
                subprocess.CalledProcessError(1, ['docker', 'manifest', 'inspect'], output=b'manifest unknown'),
                b'', subprocess.CalledProcessError(1, ['docker', 'image', 'push'], output=b'push failed'),
            ],
            'anonymous_pull': [
                subprocess.CalledProcessError(1, ['docker', 'manifest', 'inspect'], output=b'manifest unknown'),
                b'', b'', self.remote_manifest('sha256:' + 'c' * 64).encode(),
                json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///fixture/docker.sock'}}}]).encode(),
                self.remote_manifest('sha256:' + 'c' * 64).encode(),
                subprocess.CalledProcessError(1, ['docker', 'image', 'pull'], output=b'pull denied'),
            ],
            'remote_digest': [
                subprocess.CalledProcessError(1, ['docker', 'manifest', 'inspect'], output=b'manifest unknown'),
                b'', b'', self.remote_manifest('sha256:' + 'd' * 64).encode(),
            ],
        }
        for name, outputs in cases.items():
            with self.subTest(name=name), \
                    patch.object(publisher.importlib, 'import_module', return_value=audit_module), \
                    patch.object(publisher.subprocess, 'check_output', side_effect=outputs) as docker:
                with self.assertRaises(ValueError):
                    publisher.publish_registry_image(
                        archive, 'amd64', self.info(), 'sha256:' + 'c' * 64,
                        'docker.io/example/performance-node',
                    )
                if name == 'conflict':
                    self.assertEqual(docker.call_count, 1)

    def test_anonymous_image_accepts_containerd_manifest_or_classic_config_id(self):
        for local_id in ('sha256:' + 'b' * 64, 'sha256:' + 'c' * 64):
            with self.subTest(local_id=local_id), \
                    patch.object(publisher.subprocess, 'check_output', side_effect=[
                        b'', json.dumps([{**self.info(), 'Id': local_id}]).encode(),
                    ]) as docker:
                publisher._anonymous_remote_image(
                    'docker.io/example/performance-node@sha256:' + 'b' * 64,
                    'sha256:' + 'b' * 64, 'sha256:' + 'c' * 64, 'amd64',
                    {'DOCKER_CONFIG': '/empty', 'DOCKER_HOST': 'unix:///fixture/docker.sock'},
                )
            self.assertEqual(
                docker.call_args_list[0].args[0],
                ['docker', 'image', 'pull', 'docker.io/example/performance-node@sha256:' + 'b' * 64],
            )

    def test_preflight_denied_is_not_treated_as_missing_tag(self):
        denied = subprocess.CalledProcessError(
            1, ['docker', 'manifest', 'inspect'], output=b'unauthorized: access denied',
        )
        with patch.object(publisher.subprocess, 'check_output', side_effect=denied):
            with self.assertRaisesRegex(ValueError, '未授权或仓库不可见'):
                publisher.remote_manifest(self.registry_ref(), absent_ok=True)

    def test_preflight_missing_manifest_is_classified_from_docker_stderr(self):
        missing = subprocess.CalledProcessError(
            1, ['docker', 'manifest', 'inspect'], stderr=b'no such manifest: fixture',
        )
        with patch.object(publisher.subprocess, 'check_output', side_effect=missing):
            self.assertIsNone(publisher.remote_manifest(self.registry_ref(), absent_ok=True))

    def test_remote_manifest_invalid_top_level_and_digest_are_value_errors(self):
        invalid_documents = [[], {'Descriptor': {'digest': 1, 'platform': {
            'os': 'linux', 'architecture': 'amd64',
        }}, 'SchemaV2Manifest': {'config': {'digest': 'sha256:' + 'c' * 64}}}]
        for document in invalid_documents:
            with self.subTest(document=document), \
                    patch.object(publisher.subprocess, 'check_output', return_value=json.dumps(document).encode()):
                with self.assertRaises(ValueError):
                    publisher.remote_manifest(self.registry_ref())

    def test_audit_failure_prevents_all_registry_commands(self):
        audit = Mock(side_effect=ValueError('sensitive archive fixture'))
        with patch.object(publisher.importlib, 'import_module',
                          return_value=SimpleNamespace(audit_archive=audit)), \
                patch.object(publisher.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, 'sensitive archive'):
                publisher.publish_registry_image(
                    Path('/fixture/verified.tar.gz'), 'amd64', self.info(),
                    'sha256:' + 'c' * 64, 'docker.io/example/performance-node',
                )
        docker.assert_not_called()

    def test_registry_failure_keeps_existing_tar_manifest_and_registry_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            record = {
                'filename': f'{publisher.__version__}/linux-amd64.tar.gz',
                'sha256': 'd' * 64, 'size_bytes': 1, 'image_ref': self.ref(),
                'image_id': self.info()['Id'], 'image_config_id': 'sha256:' + 'c' * 64,
                'registry_ref': 'docker.io/example/performance-node@sha256:' + 'b' * 64,
            }
            manifest = {**self.versions(), 'release': publisher.__version__, 'images': {'amd64': record}}
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with patch.object(publisher, 'publish_image'), \
                    patch.object(publisher, 'publish_registry_image', side_effect=ValueError('push denied')):
                with self.assertRaisesRegex(ValueError, 'push denied'):
                    publisher.main([
                        '--output', str(root), '--image', 'amd64=' + self.ref(),
                        '--registry', 'docker.io/example/performance-node',
                    ])
            persisted = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(persisted['images']['amd64'], record)


class MultiarchIndexTests(unittest.TestCase):
    registry = 'docker.io/example/performance-node'

    def setUp(self):
        self.members = {'amd64': 'sha256:' + 'a' * 64, 'arm64': 'sha256:' + 'b' * 64}
        self.index = {'digest': 'sha256:' + 'f' * 64, 'members': self.members}
        self.manifest = {'images': {
            arch: {'registry_ref': self.registry + '@' + digest,
                   'image_config_id': 'sha256:' + str(i) * 64}
            for i, (arch, digest) in enumerate(self.members.items(), start=1)
        }}

    def index_document(self):
        return {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                'digest': self.index['digest'], 'manifests': [
                    {'platform': {'os': 'linux', 'architecture': arch}, 'digest': digest}
                    for arch, digest in self.members.items()
                ]}

    def test_read_index_and_reject_missing_duplicate_or_foreign_platforms(self):
        with patch.object(publisher.subprocess, 'check_output', return_value=json.dumps(self.index_document())):
            self.assertEqual(publisher.remote_index('fixture'), self.index)
        for failure in ('missing', 'duplicate', 'foreign', 'digest', 'single_image'):
            doc = self.index_document()
            if failure == 'missing': doc['manifests'].pop()
            if failure == 'duplicate': doc['manifests'][1]['platform']['architecture'] = 'amd64'
            if failure == 'foreign': doc['manifests'][1]['platform']['os'] = 'windows'
            if failure == 'digest': doc['digest'] = 'mutable-tag'
            if failure == 'single_image': doc['mediaType'] = 'application/vnd.oci.image.manifest.v1+json'
            with self.subTest(failure=failure), patch.object(publisher.subprocess, 'check_output', return_value=json.dumps(doc)):
                with self.assertRaises(ValueError): publisher.remote_index('fixture')

    def test_permission_and_network_errors_are_not_an_absent_tag(self):
        for error in (b'403 denied not found', b'connection timed out'):
            with patch.object(publisher.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, [], stderr=error)):
                with self.assertRaises(ValueError): publisher.remote_index('fixture', absent_ok=True)
        with patch.object(publisher.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, [], stderr=b'manifest unknown')):
            self.assertIsNone(publisher.remote_index('fixture', absent_ok=True))

    def remote_member(self, reference):
        arch = next(arch for arch, record in self.manifest['images'].items() if record['registry_ref'] == reference)
        return {'digest': self.members[arch], 'config_id': self.manifest['images'][arch]['image_config_id'],
                'os': 'linux', 'architecture': arch}

    def docker_command(self, command, **kwargs):
        if command[:3] in (['docker', 'image', 'pull'], ['docker', 'image', 'inspect']):
            environment = kwargs['env']
            self.assertNotIn('DOCKER_AUTH_CONFIG', environment)
            self.assertNotIn('DOCKER_CONTEXT', environment)
            config = Path(environment['DOCKER_CONFIG']) / 'config.json'
            if config.exists():
                self.assertLessEqual(set(json.loads(config.read_text())), {'cliPluginsExtraDirs'})
        if command[:3] == ['docker', 'image', 'inspect']:
            arch = command[4].split('/')[1]
            return json.dumps([{'Id': self.manifest['images'][arch]['image_config_id'],
                                'Os': 'linux', 'Architecture': arch}]).encode()
        return b''

    def test_publish_checks_both_anonymous_platform_pulls(self):
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', side_effect=[
                    None, self.index, self.index, None, self.index, self.index,
                ]) as remote_index, \
                patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                patch.object(publisher.subprocess, 'check_output', side_effect=self.docker_command) as docker:
            result = publisher.publish_registry_index(self.manifest, self.registry)
        self.assertEqual(result, self.registry + '@' + self.index['digest'])
        version_reference = self.registry + ':' + publisher.__version__
        self.assertEqual(self.manifest['registry_version_ref'], version_reference)
        commands = [item.args[0] for item in docker.call_args_list]
        self.assertEqual(commands[0][:4], ['docker', 'buildx', 'imagetools', 'create'])
        self.assertIn([
            'docker', 'buildx', 'imagetools', 'create', '--tag', version_reference, result,
        ], commands)
        for arch in self.members:
            self.assertIn(['docker', 'image', 'pull', '--platform', 'linux/' + arch, result], commands)
        self.assertEqual(remote_index.call_args_list[-1].args[0], version_reference)
        self.assertIn('environment', remote_index.call_args_list[-1].kwargs)

    def test_existing_identical_version_alias_is_reused_idempotently(self):
        version_reference = self.registry + ':' + publisher.__version__
        self.manifest['registry_index_ref'] = self.registry + '@' + self.index['digest']
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', side_effect=[
                    self.index, self.index, self.index,
                    self.index, self.index, self.index,
                ]), \
                patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                patch.object(publisher.subprocess, 'check_output', side_effect=self.docker_command) as docker:
            result = publisher.publish_registry_index(self.manifest, self.registry)
        self.assertEqual(result, self.registry + '@' + self.index['digest'])
        self.assertEqual(self.manifest['registry_version_ref'], version_reference)
        creates = [
            item.args[0] for item in docker.call_args_list
            if item.args[0][:4] == ['docker', 'buildx', 'imagetools', 'create']
        ]
        self.assertEqual(creates, [])

    def test_recorded_immutable_digest_mismatch_blocks_alias_even_when_members_match(self):
        self.manifest['registry_index_ref'] = self.registry + '@sha256:' + '0' * 64
        before = json.loads(json.dumps(self.manifest))
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', side_effect=[self.index, self.index]), \
                patch.object(publisher.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, '不同固定索引摘要.*拒绝'):
                publisher.publish_registry_index(self.manifest, self.registry)
        self.assertEqual(self.manifest, before)
        docker.assert_not_called()

    def test_missing_fixed_tag_is_not_recreated_when_manifest_already_records_an_index(self):
        self.manifest['registry_index_ref'] = self.registry + '@' + self.index['digest']
        before = json.loads(json.dumps(self.manifest))
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', return_value=None), \
                patch.object(publisher.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, '固定 tag 不存在.*拒绝重新生成'):
                publisher.publish_registry_index(self.manifest, self.registry)
        self.assertEqual(self.manifest, before)
        docker.assert_not_called()

    def test_recorded_immutable_must_be_a_digest_in_the_selected_repository(self):
        invalid_references = (
            self.registry + ':0.4.1',
            'docker.io/other/performance-node@' + self.index['digest'],
            self.registry + '@not-a-digest',
        )
        for invalid in invalid_references:
            manifest = {**self.manifest, 'registry_index_ref': invalid}
            with self.subTest(invalid=invalid), \
                    patch.object(publisher, 'remote_manifest') as remote_manifest, \
                    patch.object(publisher, 'remote_index') as remote_index:
                with self.assertRaisesRegex(ValueError, '本仓库合法摘要引用'):
                    publisher.publish_registry_index(manifest, self.registry)
            remote_manifest.assert_not_called()
            remote_index.assert_not_called()

    def test_existing_version_alias_digest_or_members_collision_is_never_overwritten(self):
        collisions = (
            {**self.index, 'digest': 'sha256:' + '0' * 64},
            {**self.index, 'members': {**self.members, 'amd64': 'sha256:' + '0' * 64}},
        )
        for collision in collisions:
            with self.subTest(collision=collision), \
                    patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                    patch.object(publisher, 'remote_index', side_effect=[
                        self.index, self.index, self.index, collision,
                    ]), \
                    patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                    patch.object(publisher.subprocess, 'check_output', side_effect=self.docker_command) as docker:
                with self.assertRaisesRegex(ValueError, '同版本 tag.*拒绝覆盖'):
                    publisher.publish_registry_index(self.manifest, self.registry)
            self.assertNotIn('registry_version_ref', self.manifest)
            alias_creates = [
                item.args[0] for item in docker.call_args_list
                if item.args[0][:4] == ['docker', 'buildx', 'imagetools', 'create']
            ]
            self.assertEqual(alias_creates, [])

    def test_version_alias_anonymous_read_failure_is_not_recorded(self):
        anonymous_failure = ValueError('fixture anonymous alias failure')
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', side_effect=[
                    self.index, self.index, self.index, None, self.index, anonymous_failure,
                ]), \
                patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                patch.object(publisher.subprocess, 'check_output', side_effect=self.docker_command):
            with self.assertRaisesRegex(ValueError, 'anonymous alias failure'):
                publisher.publish_registry_index(self.manifest, self.registry)
        self.assertNotIn('registry_version_ref', self.manifest)

    def test_existing_conflicting_index_never_overwritten(self):
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', return_value={**self.index, 'members': {}}), \
                patch.object(publisher.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, '拒绝覆盖'):
                publisher.publish_registry_index(self.manifest, self.registry)
        docker.assert_not_called()

    def test_anonymous_wrong_architecture_never_returns_index(self):
        def wrong_platform(command, **kwargs):
            if command[:3] == ['docker', 'image', 'inspect']:
                return json.dumps([{'Id': self.index['digest'], 'Os': 'linux', 'Architecture': 'wrong'}]).encode()
            return self.docker_command(command, **kwargs)
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', return_value=self.index), \
                patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                patch.object(publisher.subprocess, 'check_output', side_effect=wrong_platform):
            with self.assertRaisesRegex(ValueError, '不是预期架构'):
                publisher.publish_registry_index(self.manifest, self.registry)

    def test_anonymous_index_mismatch_never_attempts_pull(self):
        with patch.object(publisher, 'remote_manifest', side_effect=self.remote_member), \
                patch.object(publisher, 'remote_index', side_effect=[self.index, self.index, {**self.index, 'digest': 'sha256:' + '0' * 64}]), \
                patch.object(publisher, 'current_docker_host', return_value='unix:///fixture.sock'), \
                patch.object(publisher.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, '摘要不匹配'):
                publisher.publish_registry_index(self.manifest, self.registry)
        docker.assert_not_called()

    def test_index_requires_two_images_in_same_repository(self):
        for mode in ('one', 'foreign'):
            with self.subTest(mode=mode):
                images = {key: dict(value) for key, value in self.manifest['images'].items()}
                if mode == 'one': images.pop('arm64')
                else: images['amd64']['registry_ref'] = 'docker.io/other/repo@' + self.members['amd64']
                with patch.object(publisher.subprocess, 'check_output') as docker:
                    with self.assertRaises(ValueError):
                        publisher.publish_registry_index({'images': images}, self.registry)
                    docker.assert_not_called()

    def test_index_cli_rejects_partial_architectures_before_any_publish(self):
        with patch.object(publisher, 'publish_image') as publish:
            with self.assertRaises(ValueError):
                publisher.main(['--publish-index', '--registry', self.registry, '--image', 'amd64=fixture'])
            publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
