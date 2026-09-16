"""The fixed template is safe to import before creating isolated engine processes."""
import copy
import subprocess
import sys
import unittest
import uuid

from performance_node.locust_runtime import canonical_sha256, validate_snapshot


def snapshot():
    return {
        'schema_version': 1, 'run_id': str(uuid.uuid4()), 'node_id': str(uuid.uuid4()),
        'engine_version': '2.43.3', 'plan_name': 'fixture', 'base_url': 'http://127.0.0.1:8080',
        'allowed_methods': ['GET'], 'users': 1, 'spawn_rate': 1, 'duration_seconds': 5,
        'wait_seconds': .5, 'steps': [{'name': 'fixture', 'method': 'GET', 'path': '/ok?a=1',
                                    'expected_status': 200, 'headers': {}, 'body': None}],
    }


class FixedRuntimeTests(unittest.TestCase):
    def test_import_does_not_patch_or_load_locust(self):
        result = subprocess.run([sys.executable, '-c',
            'import sys; import performance_node.locust_runtime; '
            'assert "locust" not in sys.modules; assert "gevent.monkey" not in sys.modules'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_and_canonical_hash(self):
        value = snapshot()
        self.assertIs(validate_snapshot(value), value)
        self.assertEqual(canonical_sha256(value), canonical_sha256(dict(reversed(list(value.items())))))
        changed = copy.deepcopy(value)
        changed['users'] = 2
        self.assertNotEqual(canonical_sha256(value), canonical_sha256(changed))

    def test_bad_load_and_origins_are_rejected(self):
        for field, value in [('users', True), ('users', 101), ('duration_seconds', 601),
                             ('wait_seconds', float('nan')), ('spawn_rate', 0),
                             ('base_url', 'https://user:pass@example.test'),
                             ('base_url', 'https://example.test/a'), ('base_url', 'file:///tmp/a'),
                             ('base_url', 'https://example.test:0'), ('engine_version', 'latest')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                candidate = snapshot()
                candidate[field] = value
                validate_snapshot(candidate)

    def test_no_redirect_path_header_override_or_unsupported_step(self):
        for field, value in [('path', '//evil.test/a'), ('path', '/a\n'), ('path', '/a#b'),
                             ('method', 'DELETE'), ('expected_status', True),
                             ('headers', {'Host': 'evil.test'}), ('headers', {'Connection': 'upgrade'}),
                             ('headers', {'X-Test': 'hello\r\nHost: evil.test'})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                candidate = snapshot()
                candidate['steps'][0][field] = value
                validate_snapshot(candidate)
        candidate = snapshot()
        candidate['python_code'] = 'print(1)'
        with self.assertRaises(ValueError):
            validate_snapshot(candidate)
