"""No Docker/network or business database access: release validation tests."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

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
        with patch.object(publisher.subprocess, 'check_output', side_effect=[json.dumps([self.info()]), json.dumps(self.versions())]):
            inspected, versions = publisher.inspect_image('amd64', self.ref())
        self.assertEqual(inspected, self.info())
        self.assertEqual(versions, self.versions())

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
        with patch.object(publisher.subprocess, 'check_output', side_effect=[json.dumps([self.info()]), json.dumps(versions)]):
            with self.assertRaises(ValueError): publisher.inspect_image('amd64', self.ref())

    def test_existing_matching_archive_reused_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            filename = f'{publisher.__version__}/linux-amd64.tar.gz'
            archive = root / filename
            archive.parent.mkdir()
            archive.write_bytes(b'fixture archive')
            record = {'image_id': 'sha256:' + 'a' * 64, 'filename': filename,
                      'sha256': publisher.sha256(archive), 'size_bytes': archive.stat().st_size}
            manifest = {**self.versions(), 'release': publisher.__version__, 'images': {'amd64': record}}
            inspected = self.info()
            with patch.object(publisher, 'inspect_image', return_value=(inspected, self.versions())), patch.object(publisher, 'archive_config_id', return_value='sha256:' + 'c' * 64) as archive_check, patch.object(publisher.subprocess, 'Popen') as export:
                publisher.publish_image(root, 'amd64', self.ref(), manifest)
                self.assertEqual(record['image_config_id'], 'sha256:' + 'c' * 64)
                archive_check.assert_called_once_with(archive, self.ref(), inspected)
                export.assert_not_called()
                archive.write_bytes(b'changed')
                with self.assertRaises(ValueError): publisher.publish_image(root, 'amd64', self.ref(), manifest)
                export.assert_not_called()

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


if __name__ == '__main__':
    unittest.main()
