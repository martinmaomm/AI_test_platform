"""The fixed template is safe to import before creating isolated engine processes."""
import copy
import subprocess
import sys
import unittest
import uuid

from performance_node.locust_runtime import (
    MISSING, add_failure_samples, assertion_failures, canonical_sha256, compare_value,
    resolve_path, resolve_value, select_value, validate_snapshot,
)


def snapshot():
    return {
        'schema_version': 2, 'run_id': str(uuid.uuid4()), 'node_id': str(uuid.uuid4()),
        'engine_version': '2.43.3', 'plan_name': 'fixture', 'base_url': 'http://127.0.0.1:8080',
        'allowed_methods': ['GET'], 'mode': 'validation', 'validation_key': 'a' * 64,
        'users': 1, 'spawn_rate': 1, 'duration_seconds': 5, 'wait_seconds': .5,
        'variables': {'page': 1}, 'unique_variables': [{'name': 'name', 'prefix': 'load_'}],
        'steps': [{'name': 'fixture', 'phase': 'main', 'method': 'GET', 'path': '/ok',
                   'query': {'page': '${page}'}, 'headers': {}, 'body_type': 'none',
                   'body': None, 'extract': [], 'assertions': [
                       {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
                   ]}],
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
                             ('method', 'DELETE'),
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

    def test_setup_data_flow_and_undefined_or_late_variables_are_rejected(self):
        value = snapshot()
        value['allowed_methods'].append('POST')
        value['steps'].insert(0, {
            'name': 'login', 'phase': 'setup', 'method': 'POST', 'path': '/login',
            'query': {}, 'headers': {}, 'body_type': 'json',
            'body': {'name': '${page}'},
            'extract': [{'name': 'token', 'check': 'body.token'}],
            'assertions': [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}],
        })
        value['steps'][1]['headers'] = {'Authorization': 'Bearer ${token}'}
        self.assertIs(validate_snapshot(value), value)
        value['steps'][0]['body'] = {'name': '${name}'}
        with self.assertRaises(ValueError):
            validate_snapshot(value)

    def test_variables_selectors_strict_types_and_missing_are_deterministic(self):
        variables = {'number': 7, 'flag': True, 'obj': {'id': 3}}
        self.assertEqual(resolve_value('${number}', variables), 7)
        self.assertEqual(resolve_value('n=${number};f=${flag}', variables), 'n=7;f=true')
        response = {'status_code': 200, 'text': '{"data":[{"id":3}]}',
                    'headers': {'Content-Type': 'application/json'},
                    'body': {'data': [{'id': 3}], 'none': None}}
        self.assertEqual(select_value(response, 'body.data[0].id'), 3)
        self.assertEqual(select_value(response, 'headers.content-type'), 'application/json')
        self.assertIs(select_value(response, 'body.missing'), MISSING)
        self.assertEqual(compare_value(True, 'eq', 1)[0], False)
        self.assertEqual(compare_value({'x': True}, 'eq', {'x': 1})[0], False)
        self.assertEqual(compare_value(MISSING, 'exists', False)[0], True)
        self.assertEqual(compare_value([1, 2], 'length_gt', 1)[0], True)

    def test_dynamic_paths_are_segment_encoded_and_bad_runtime_types_fail_readably(self):
        self.assertEqual(resolve_path('/items/${id}', {'id': 'hello world'}),
                         '/items/hello%20world')
        with self.assertRaises(ValueError):
            resolve_path('/items/${id}', {'id': '../x?bad#fragment'})
        with self.assertRaises(ValueError):
            resolve_path('/items/${id}', {'id': {'nested': True}})
        with self.assertRaises(ValueError):
            resolve_value({'${key}': 'value'}, {'key': []})

    def test_multiple_assertion_evidence_is_returned_for_one_request_result(self):
        step = snapshot()['steps'][0]
        step['assertions'] = [
            {'check': 'status_code', 'comparator': 'eq', 'expected': 201},
            {'check': 'body.items', 'comparator': 'length_gt', 'expected': 0},
            {'check': 'body.absent', 'comparator': 'exists', 'expected': False},
        ]
        failures = assertion_failures(step, 1, {
            'status_code': 200, 'text': '{}', 'headers': {}, 'body': {'items': []},
        })
        self.assertEqual(len(failures), 2)
        self.assertEqual({item['check'] for item in failures}, {'status_code', 'body.items'})

        collected, seen = [], set()
        add_failure_samples(collected, seen, failures)
        add_failure_samples(collected, seen, failures)
        self.assertEqual(collected, failures)

    def test_type_expected_must_be_a_supported_string_without_type_error(self):
        for expected in ([], {}, None, True, 'integer'):
            value = snapshot()
            value['steps'][0]['assertions'][0] = {
                'check': 'body', 'comparator': 'type', 'expected': expected,
            }
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                validate_snapshot(value)
