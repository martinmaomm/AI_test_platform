"""Offline tests for the public-image content gate. All secrets are fake."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('image_audit', Path(__file__).with_name('audit_performance_node_image.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ImageAuditTests(unittest.TestCase):
    def archive(self, path, layers, *, compressed=False, config_extra=None):
        entries = []
        digests = []
        for index, files in enumerate(layers):
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode='w') as layer:
                for name, content in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    layer.addfile(member, io.BytesIO(content))
            content = raw.getvalue()
            digests.append('sha256:' + hashlib.sha256(content).hexdigest())
            entries.append((f'layer{index}.tar', gzip.compress(content) if compressed else content))
        config = {'rootfs': {'diff_ids': digests}, **(config_extra or {})}
        manifest = [{'Config': 'config.json', 'Layers': [name for name, _ in entries]}]
        with tarfile.open(path, 'w:gz') as outer:
            for name, content in entries + [('manifest.json', json.dumps(manifest).encode()), ('config.json', json.dumps(config).encode())]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                outer.addfile(member, io.BytesIO(content))

    def check(self, layers, *, compressed=False, secrets=(), config_extra=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.tar.gz'
            self.archive(path, [{'app/src/performance_node/__main__.py': b'print("node")'}] + layers,
                         compressed=compressed, config_extra=config_extra)
            return audit.audit_archive(path, secrets=secrets)

    def test_clean_raw_and_compressed_layers(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                result = self.check([{'app/README.md': '仅限测试使用'.encode()}], compressed=compressed)
                self.assertTrue(result['passed'])
                self.assertEqual(result['layers_checked'], 2)

    def test_deleted_lower_layer_secret_still_fails_without_echoing_it(self):
        secret = b'fixture-secret-for-image-test'
        with self.assertRaises(ValueError) as raised:
            self.check([{'usr/local/secret.txt': secret}, {'usr/local/.wh.secret.txt': b''}], secrets=(secret,))
        self.assertNotIn(secret.decode(), str(raised.exception))

    def test_private_key_and_token(self):
        for value in (b'-----BEGIN PRIVATE KEY-----\n' + b'A' * 80 + b'\n-----END PRIVATE KEY-----', b'dckr_pat_' + b'x' * 32):
            with self.subTest(value_type=value[:5]):
                with self.assertRaises(ValueError):
                    self.check([{'usr/local/secret.txt': value}])

    def test_source_code_pem_delimiter_is_not_private_key(self):
        result = self.check([{'usr/local/lib/parse_pem.py': b'HEADER = "-----BEGIN PRIVATE KEY-----"'}])
        self.assertTrue(result['passed'])

    def test_forbidden_paths_and_extra_application_files(self):
        for name in ('app/.env', 'app/backend/settings.py', 'var/lib/performance-node/identity.json', 'root/.docker/config.json', 'app/debug.log', 'app/src/performance_node/__pycache__/old.pyc'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check([{name: b'no-secret'}])

    def test_secret_in_image_config_or_history_fails(self):
        secret = b'fixture-secret-in-history'
        with self.assertRaises(ValueError):
            self.check([], secrets=(secret,), config_extra={'history': [{'created_by': secret.decode()}]})

    def test_secret_crossing_read_boundary(self):
        secret = b'fixture-secret-split-across-chunks'
        with self.assertRaises(ValueError):
            audit._check_stream(io.BytesIO(b'x' * (audit.CHUNK_SIZE - 8) + secret), 'test', (secret,))

    def test_unsafe_member_path_fails(self):
        with self.assertRaises(ValueError):
            self.check([{'../../secrets': b'value'}])


if __name__ == '__main__':
    unittest.main()
