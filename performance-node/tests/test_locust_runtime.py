"""The fixed template is safe to import before creating isolated engine processes."""
import copy
import json
import subprocess
import sys
import unittest
import uuid

from performance_node.locust_runtime import (
    MISSING, add_failure_samples, assertion_failures, canonical_sha256, compare_value,
    classify_network_exception, resolve_path, resolve_value, select_value, validate_snapshot,
    ReportSequence, ValidationTrace, evidence_preview, format_evidence, evidence_secrets,
    normalize_validation_steps,
)


def snapshot():
    node_id = str(uuid.uuid4())
    return {
        'schema_version': 4, 'run_id': str(uuid.uuid4()),
        'engine_version': '2.43.3', 'plan_name': 'fixture', 'base_url': 'http://127.0.0.1:8080',
        'allowed_methods': ['GET'], 'mode': 'validation',
        'nodes': [{'node_id': node_id, 'users': 1, 'validation_key': 'a' * 64}],
        'users': 1, 'spawn_rate': 1, 'duration_seconds': 5, 'wait_seconds': .5,
        'connect_timeout_seconds': 10, 'read_timeout_seconds': 30,
        'variables': {'page': 1}, 'unique_variables': [{'name': 'name', 'prefix': 'load_'}],
        'steps': [{'name': 'fixture', 'phase': 'main', 'method': 'GET', 'path': '/ok',
                   'query': {'page': '${page}'}, 'headers': {}, 'body_type': 'none',
                   'body': None, 'extract': [], 'assertions': [
                       {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
                   ]}],
    }


class FixedRuntimeTests(unittest.TestCase):
    def test_report_sequence_deduplicates_and_detects_gaps_and_final_mutation(self):
        sequence = ReportSequence()
        self.assertTrue(sequence.accept(1, None, 'one'))
        self.assertFalse(sequence.accept(1, None, 'one'))
        with self.assertRaises(ValueError):
            sequence.accept(1, None, 'changed')
        self.assertTrue(sequence.accept(3, 3, 'three'))
        self.assertTrue(sequence.gap)
        self.assertEqual((sequence.last, sequence.final), (3, 3))
        with self.assertRaises(ValueError):
            sequence.accept(4, None, 'four')

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

    def test_request_timeouts_are_required_bounded_integers(self):
        for field, invalid_values in (
            ('connect_timeout_seconds', (None, True, 0, 61, 1.5)),
            ('read_timeout_seconds', (None, True, 0, 121, 1.5)),
        ):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    candidate = snapshot()
                    candidate[field] = invalid
                    validate_snapshot(candidate)
        for missing in ('connect_timeout_seconds', 'read_timeout_seconds'):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                candidate = snapshot()
                candidate.pop(missing)
                validate_snapshot(candidate)

    def test_network_exception_classification_uses_nested_evidence_without_leaking_text(self):
        def error(name, text='https://user:secret@private.invalid/?token=hidden', wrapped=None):
            cls = type(name, (Exception,), {})
            return cls(wrapped if wrapped is not None else text)

        cases = (
            (error('ConnectTimeout'), 'connect_timeout', '连接超时（ConnectTimeout）'),
            (error('ConnectionError', wrapped=error('ReadTimeoutError')), 'read_timeout',
             '读取响应超时（ReadTimeoutError）'),
            (error('MaxRetryError', wrapped=error('NameResolutionError')), 'dns_error',
             'DNS 解析失败（NameResolutionError）'),
            (error('ConnectionError', wrapped=error('SSLError')), 'tls_error',
             'TLS 连接失败（SSLError）'),
            (error('ConnectionError'), 'connection_error', '网络连接失败（ConnectionError）'),
            (error('Timeout'), 'connection_error', '网络连接失败（Timeout）'),
            (error('CustomerSecretFailure'), 'connection_error', '网络连接失败（NetworkError）'),
        )
        for exc, expected_type, expected_message in cases:
            with self.subTest(expected_type=expected_type):
                actual_type, message = classify_network_exception(exc)
                self.assertEqual((actual_type, message), (expected_type, expected_message))
                self.assertNotIn('private.invalid', message)
                self.assertNotIn('secret', message)
                self.assertNotIn('hidden', message)

    def test_bad_load_and_origins_are_rejected(self):
        for field, value in [('users', True), ('users', 1001), ('duration_seconds', 601),
                             ('wait_seconds', float('nan')), ('spawn_rate', 0),
                             ('base_url', 'https://user:pass@example.test'),
                             ('base_url', 'https://example.test/a'), ('base_url', 'file:///tmp/a'),
                             ('base_url', 'https://example.test:0'), ('engine_version', 'latest')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                candidate = snapshot()
                candidate[field] = value
                validate_snapshot(candidate)

    def test_multi_node_members_are_sorted_unique_and_evenly_assigned(self):
        value = snapshot()
        node_ids = sorted(str(uuid.uuid4()) for _ in range(3))
        value.update(mode='load', users=1000, nodes=[
            {'node_id': node_id, 'users': users, 'validation_key': str(index) * 64}
            for index, (node_id, users) in enumerate(zip(node_ids, (334, 333, 333)), start=1)
        ])
        self.assertIs(validate_snapshot(value), value)
        for mutation in (
            lambda item: item['nodes'].reverse(),
            lambda item: item['nodes'][0].update(users=333),
            lambda item: item['nodes'].append(copy.deepcopy(item['nodes'][-1])),
        ):
            candidate = copy.deepcopy(value)
            mutation(candidate)
            with self.assertRaises(ValueError):
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

    def test_validation_trace_records_all_assertions_and_skips_dependent_steps(self):
        value = snapshot()
        first = value['steps'][0]
        first['assertions'] += [{'check': 'body.items', 'comparator': 'length_gt', 'expected': 0}]
        value['steps'].append(copy.deepcopy(first))
        trace = ValidationTrace(value)
        trace.request(1, first, 'http://fixture.invalid/ok?page=1', {}, None)
        response = {'status_code': 200, 'headers': {'Content-Type': 'application/json'},
                    'body': {'items': []}, 'text': '{"items":[]}'}
        trace.finish_step(1, first, response, failures=assertion_failures(first, 1, response), elapsed=12.5)
        trace.finish()
        rows = trace.report()
        self.assertEqual([row['status'] for row in rows], ['failed', 'skipped'])
        self.assertEqual([row['status'] for row in rows[0]['assertions']], ['passed', 'failed'])
        self.assertEqual(rows[0]['assertions'][1]['actual']['content'], '[]')
        self.assertEqual(json.loads(rows[0]['response']['body']['content']), {'items': []})
        self.assertEqual(rows[0]['elapsed_ms'], 12.5)
        self.assertIn('第 1 步失败', rows[1]['message'])

    def test_validation_trace_preserves_credentials_in_all_detail_locations(self):
        value = snapshot()
        value['variables']['password'] = 'sample-password'
        step = value['steps'][0]
        step['extract'] = [{'name': 'token', 'check': 'body.token'}]
        step['assertions'].append({'check': 'body.token', 'comparator': 'type', 'expected': 'string'})
        step['assertions'].append({'check': 'body.token', 'comparator': 'eq', 'expected': 'sample-token'})
        trace = ValidationTrace(value)
        trace.request(1, step, 'http://fixture.invalid/ok?password=sample-password',
                      {'Authorization': 'Bearer sample-token', 'Cookie': 'sid=cookie-value'},
                      {'password': 'sample-password', 'echo': 'sample-password'})
        response = {'status_code': 200, 'headers': {'Set-Cookie': 'sid=cookie-value; Path=/'},
                    'body': {'token': 'sample-token', 'echo': 'sample-token', 'ok': True}, 'text': ''}
        trace.finish_step(1, step, response, extracted={'token': 'sample-token'})
        rows = trace.report()
        serialized = json.dumps(rows)
        for secret in ('sample-password', 'sample-token', 'cookie-value'):
            self.assertIn(secret, serialized)
        self.assertNotIn('<redacted>', serialized)
        self.assertEqual(rows[0]['assertions'][1]['status'], 'passed')
        self.assertEqual(rows[0]['assertions'][1]['expected']['content'], '"string"')
        self.assertEqual(rows[0]['extractions'][0]['value']['content'], '"sample-token"')
        self.assertEqual(rows[0]['request']['url'], 'http://fixture.invalid/ok?password=sample-password')
        self.assertEqual(json.loads(rows[0]['request']['headers']['content'])['Cookie'], 'sid=cookie-value')
        self.assertEqual(json.loads(rows[0]['response']['headers']['content'])['Set-Cookie'], 'sid=cookie-value; Path=/')
        self.assertEqual(rows[0]['assertions'][2]['expected']['content'], '"sample-token"')
        self.assertEqual(rows[0]['assertions'][2]['actual']['content'], '"sample-token"')
        self.assertTrue(json.loads(rows[0]['response']['body']['content'])['ok'])

    def test_validation_trace_reports_uncommitted_extractions_and_incomplete_response(self):
        value = snapshot()
        step = value['steps'][0]
        step['extract'] = [{'name': 'found', 'check': 'body.id'}, {'name': 'absent', 'check': 'body.absent'}]
        response = {'status_code': 200, 'headers': {}, 'body': {'id': 4}, 'text': ''}
        trace = ValidationTrace(value)
        trace.finish_step(1, step, response, failures=[{'message': '响应提取字段不存在'}], extracted={'found': 4})
        extracts = trace.report()[0]['extractions']
        self.assertEqual([item['status'] for item in extracts], ['passed', 'failed'])
        self.assertIn('未交给后续步骤', extracts[0]['message'])
        trace = ValidationTrace(value)
        trace.finish_step(1, step, {**response, 'body': MISSING, 'text': 'partial'},
                          failures=[{'message': '读取响应失败或超时'}], incomplete=True)
        row = trace.report()[0]
        self.assertTrue(row['response']['incomplete'])
        self.assertEqual(row['assertions'][0]['status'], 'skipped')
        self.assertEqual(row['response']['body']['content'], 'partial')

    def test_detail_text_is_preserved_while_shared_failure_summary_stays_redacted(self):
        value = {'password': 'fixture-password', 'Authorization': 'Bearer fixture-token',
                 'Cookie': 'sid=fixture-cookie', 'nested': [{'token': 'fixture-token'}]}
        secrets = evidence_secrets(value)
        self.assertEqual(json.loads(evidence_preview(value)['content']), value)
        for raw in ('password=fixture-password&token=fixture-token',
                    'Authorization: Bearer fixture-token', '{"token":"fixture-token"}',
                    'https://fixture.invalid/?password=a%2Bb%20c', 'Basic Zml4dHVyZQ=='):
            self.assertEqual(evidence_preview(raw)['content'], raw)
        summary = format_evidence(value, secrets, redact=True)
        for secret in ('fixture-password', 'fixture-token', 'fixture-cookie'):
            self.assertNotIn(secret, json.dumps(summary))

    def test_validation_evidence_is_bounded_and_formal_load_collects_no_step_trace(self):
        self.assertTrue(evidence_preview('汉' * 9000)['truncated'])
        self.assertTrue(evidence_preview(list(range(250)))['truncated'])
        self.assertLessEqual(len(evidence_preview('汉' * 9000)['content'].encode()), 8192)
        value = snapshot()
        step = value['steps'][0]
        step['assertions'] *= 50
        step['extract'] = [{'name': f'x{i}', 'check': 'body'} for i in range(50)]
        trace = ValidationTrace(value)
        response = {'status_code': 200, 'headers': {}, 'body': {'data': 'x' * 65536}, 'text': ''}
        trace.request(1, step, 'http://fixture.invalid/ok', {}, response['body'])
        trace.finish_step(1, step, response)
        rows = normalize_validation_steps([{**trace.report()[0], 'step_index': i + 1} for i in range(25)])
        self.assertEqual(len(rows), 20)
        self.assertLess(len(json.dumps(rows).encode()), 1024 * 1024)
        self.assertEqual(normalize_validation_steps([None, {'secret': 'bad'}]), [])
        value['mode'] = 'load'
        trace = ValidationTrace(value)
        trace.request(1, step, 'http://fixture.invalid/ok', {}, response['body'])
        trace.finish_step(1, step, response)
        self.assertEqual(trace.report(), [])

    def test_numeric_and_raw_credentials_do_not_corrupt_report_metadata(self):
        value = snapshot()
        value['variables']['password'] = 1
        step = value['steps'][0]
        trace = ValidationTrace(value)
        trace.request(1, step, 'http://fixture.invalid/ok', {}, '{"password":"raw-secret"}')
        response = {'status_code': 200, 'headers': {},
                    'body': {'echo_numeric': 1, 'echo_raw': 'raw-secret'}, 'text': ''}
        trace.finish_step(1, step, response, elapsed=1)
        row = trace.report()[0]
        self.assertEqual(row['step_index'], 1)
        self.assertEqual(row['elapsed_ms'], 1)
        self.assertEqual(row['response']['status_code'], 200)
        self.assertEqual(json.loads(row['response']['body']['content']),
                         {'echo_numeric': 1, 'echo_raw': 'raw-secret'})
        self.assertEqual(row['request']['body']['content'], '{"password":"raw-secret"}')
        self.assertIn('raw-secret', json.dumps(row))
