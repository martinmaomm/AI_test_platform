import copy
import json
from types import SimpleNamespace
from unittest import TestCase

from performance_testing.diagnosis_data import MAX_PAYLOAD_BYTES, build_validation_input


def preview(value, truncated=False):
    return {'content': json.dumps(value, ensure_ascii=False), 'truncated': truncated}


def plan_step(phase='main', extracts=None):
    return {'name': 'secret-step-name', 'phase': phase, 'method': 'POST',
            'path': '/secret-path', 'query': {}, 'headers': {}, 'body_type': 'json',
            'body': {'secret-field': 'secret-body'}, 'extract': extracts or [],
            'assertions': [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}]}


def trace_step(plan, index, status='passed'):
    return {
        'step_index': index, 'step_name': plan['name'], 'method': plan['method'],
        'phase': plan['phase'], 'status': status, 'elapsed_ms': 12.5,
        'message': 'secret-step-message',
        'request': {'url': 'https://secret-site.invalid/?token=secret-token',
                    'headers': preview({'Authorization': 'Bearer secret-token',
                                        'Accept-Language': 'secret-language', 'Cookie': 'secret-cookie'}),
                    'body_type': 'json', 'body': preview({'password': 'secret-password'})},
        'response': {'status_code': 200, 'incomplete': False,
                     'headers': preview({'Set-Cookie': 'secret-cookie'}),
                     'body': preview({'token': 'secret-token'})},
        'assertions': [{**item, 'index': offset, 'status': 'passed', 'error_type': '',
                        'expected': preview(item['expected']), 'actual': preview(200),
                        'message': 'secret-assertion-message'}
                       for offset, item in enumerate(plan['assertions'], 1)],
        'extractions': [{**item, 'index': offset, 'status': 'passed',
                         'value': preview('secret-token'), 'message': 'secret-extraction-message'}
                        for offset, item in enumerate(plan['extract'], 1)],
    }


def fixture():
    first = plan_step('setup', [{'name': 'secret_token', 'check': 'body.secret_token'}])
    second = plan_step()
    second['headers'] = {'Authorization': 'Bearer ${secret_token}'}
    second['query'] = {'key': '${secret_fixed}'}
    second['body'] = {'generated': '${secret_unique}'}
    return SimpleNamespace(
        mode='validation', status='completed',
        snapshot={'base_url': 'https://secret-site.invalid', 'name': 'secret-plan',
                  'variables': {'secret_fixed': 'secret-fixed-value'},
                  'unique_variables': [{'name': 'secret_unique', 'prefix': 'secret-prefix'}],
                  'steps': [first, second]},
        latest_metrics={'complete': True, 'validation_complete': True, 'validation_passed': True,
                        'requests': 2, 'main_steps_completed': 1, 'main_steps_total': 1,
                        'validation_steps': [trace_step(first, 1), trace_step(second, 2)],
                        'failure_samples': []},
    )


def evidence(payload, key):
    return next(item['value'] for item in payload['evidence'] if item['id'] == key)


class ValidationDiagnosisDataTests(TestCase):
    def test_projection_is_anonymous_and_does_not_mutate_original(self):
        run = fixture()
        before = copy.deepcopy(vars(run))
        payload = build_validation_input(run)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        self.assertNotIn('secret', encoded)
        self.assertNotIn('https://', encoded)
        self.assertNotIn('body.secret_token', encoded)
        self.assertEqual(vars(run), before)
        self.assertEqual(payload['analysis_type'], 'validation_diagnosis')
        self.assertEqual(payload['schema_version'], 1)
        self.assertEqual(payload['targets'], {})
        self.assertEqual(payload['assessment']['status'], 'passed')
        self.assertTrue(all(set(item) == {'id', 'label', 'value', 'unit'} for item in payload['evidence']))
        headers = evidence(payload, 'step.1')['request_headers']
        self.assertEqual(headers, {'authorization_present': True, 'auth_scheme': 'bearer',
                                  'accept_language_present': True, 'unresolved_placeholders': False})

    def test_failed_extraction_rolls_back_other_passed_extractions_and_skips_consumers(self):
        run = fixture()
        plan = run.snapshot['steps'][0]
        plan['extract'].append({'name': 'secret_absent', 'check': 'body.secret_absent'})
        first = trace_step(plan, 1, 'failed')
        first['extractions'][1].update(status='failed', value=preview({'missing': True}))
        second = trace_step(run.snapshot['steps'][1], 2, 'skipped')
        second.update(request=None, response=None, assertions=[], extractions=[])
        run.latest_metrics.update(validation_passed=False, main_steps_completed=0, validation_steps=[first, second],
            failure_samples=[{'step_index': 1, 'error_type': 'extraction_missing',
                              'message': 'secret-error', 'actual': 'secret-actual'}])
        payload = build_validation_input(run)
        self.assertEqual(payload['assessment'], {'status': 'failed', 'first_failed_step': 1,
                         'failed_steps': [1], 'skipped_steps': [2], 'checks': []})
        step = evidence(payload, 'step.1')
        self.assertEqual([item['status'] for item in step['extractions']], ['passed', 'failed'])
        self.assertTrue(all(item['committed'] is False for item in step['extractions']))
        self.assertEqual(step['extractions'][1]['value']['type'], 'missing')
        self.assertEqual(step['error_types'], ['extraction_missing'])
        edge = next(item for item in evidence(payload, 'variables.dependencies') if item['source'] == 'extraction')
        self.assertFalse(edge['available'])
        self.assertEqual(edge['producer_step'], 1)
        self.assertEqual(edge['consumer_step'], 2)
        self.assertEqual(edge['availability_reason'], 'producer_failed')
        self.assertTrue(edge['producer_failed_and_consumer_skipped'])
        self.assertNotIn('secret', json.dumps(payload))

    def test_missing_trace_does_not_invent_passed_or_skipped_steps(self):
        for value in (None, [], {}, 'secret-trace'):
            with self.subTest(value=value):
                run = fixture()
                run.latest_metrics['validation_steps'] = value
                payload = build_validation_input(run)
                self.assertEqual(payload['assessment']['status'], 'insufficient_data')
                self.assertEqual(payload['assessment']['failed_steps'], [])
                self.assertEqual(payload['assessment']['skipped_steps'], [])
                self.assertEqual(evidence(payload, 'step.1')['status'], 'unknown')

    def test_partial_or_cancelled_records_cannot_pass(self):
        run = fixture()
        run.latest_metrics['validation_steps'].pop()
        self.assertEqual(build_validation_input(run)['assessment']['status'], 'insufficient_data')
        run = fixture()
        run.status = 'cancelled'
        self.assertEqual(build_validation_input(run)['assessment']['status'], 'incomplete')
        run = fixture()
        run.latest_metrics['complete'] = False
        self.assertNotEqual(build_validation_input(run)['assessment']['status'], 'passed')
        run.mode = 'load'
        self.assertEqual(build_validation_input(run)['assessment']['status'], 'insufficient_data')

    def test_pass_requires_runtime_validation_complete_and_exact_main_counts(self):
        cases = [
            {'validation_complete': False}, {'validation_complete': None},
            {'validation_complete': 1}, {'main_steps_completed': 0},
            {'main_steps_completed': True}, {'main_steps_total': True},
            {'main_steps_completed': 1.0}, {'main_steps_total': 1.0},
            {'main_steps_completed': None}, {'main_steps_total': None},
            {'main_steps_completed': -1}, {'main_steps_total': -1},
            {'main_steps_completed': 0, 'main_steps_total': 0},
            {'main_steps_completed': 2, 'main_steps_total': 2},
            {'main_steps_completed': 2, 'main_steps_total': 1},
            {'main_steps_completed': float('nan')}, {'main_steps_total': float('inf')},
            {'requests': 0}, {'requests': True}, {'requests': None}, {'requests': 2.0},
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                run = fixture()
                run.latest_metrics.update(updates)
                payload = build_validation_input(run)
                self.assertEqual(payload['assessment']['status'], 'insufficient_data')
                self.assertEqual(payload['assessment']['failed_steps'], [])
                json.dumps(payload, allow_nan=False)
        for missing in ('validation_complete', 'main_steps_completed', 'main_steps_total', 'requests'):
            run = fixture()
            del run.latest_metrics[missing]
            self.assertEqual(build_validation_input(run)['assessment']['status'], 'insufficient_data')

    def test_passed_trace_requires_a_usable_request_before_committing(self):
        good_request = fixture().latest_metrics['validation_steps'][0]['request']
        cases = [None, {}, [], 'secret-request',
                 {**good_request, 'url': None}, {**good_request, 'url': ''},
                 {**good_request, 'body_type': 'secret-invalid'},
                 {**good_request, 'body': None}, {**good_request, 'body': {'content': 'secret', 'truncated': True}},
                 {**good_request, 'headers': None}]
        for request in cases:
            with self.subTest(request_type=type(request).__name__):
                run = fixture()
                run.latest_metrics['validation_steps'][0]['request'] = request
                payload = build_validation_input(run)
                self.assertEqual(payload['assessment']['status'], 'insufficient_data')
                step = evidence(payload, 'step.1')
                self.assertFalse(step['request_usable'])
                self.assertFalse(step['extractions_committed'])
                self.assertTrue(all(item['committed'] is None for item in step['extractions']))
                self.assertNotIn('secret', json.dumps(payload))

    def test_passed_step_with_truncated_request_has_unknown_commit_not_false(self):
        run = fixture()
        run.latest_metrics['validation_steps'][0]['request']['body']['truncated'] = True
        payload = build_validation_input(run)
        first = evidence(payload, 'step.1')
        self.assertEqual(payload['assessment']['status'], 'insufficient_data')
        self.assertIsNone(first['extractions_committed'])
        self.assertIsNone(first['extractions'][0]['committed'])
        edge = next(item for item in evidence(payload, 'variables.dependencies') if item['source'] == 'extraction')
        self.assertIsNone(edge['available'])
        self.assertTrue(run.latest_metrics['validation_steps'][0]['request']['body']['truncated'])

    def test_unfinished_producer_commit_and_dependency_are_unknown(self):
        for state in ('pending', 'running', 'unknown'):
            run = fixture()
            run.latest_metrics['validation_steps'][0]['status'] = state
            payload = build_validation_input(run)
            self.assertIsNone(evidence(payload, 'step.1')['extractions_committed'])
            edge = next(item for item in evidence(payload, 'variables.dependencies') if item['source'] == 'extraction')
            self.assertIsNone(edge['available'])

    def test_failure_samples_conflicting_with_success_are_insufficient_not_guessed_failure(self):
        cases = [
            [{'step_index': 1, 'error_type': 'request_render_error', 'message': 'secret-error'}],
            [{'step_index': 20, 'error_type': 'read_timeout'}],
            None, {}, 'secret-failures', [None], [{}],
            [{'step_index': True, 'error_type': 'request_render_error'}],
            [{'step_index': 1, 'error_type': 'secret-type'}],
        ]
        for failures in cases:
            with self.subTest(failure_type=type(failures).__name__):
                run = fixture()
                run.latest_metrics['failure_samples'] = failures
                payload = build_validation_input(run)
                self.assertEqual(payload['assessment']['status'], 'insufficient_data')
                self.assertEqual(payload['assessment']['failed_steps'], [])
                self.assertNotIn('secret', json.dumps(payload))
        run = fixture()
        del run.latest_metrics['failure_samples']
        self.assertEqual(build_validation_input(run)['assessment']['status'], 'insufficient_data')
        run = fixture()
        run.latest_metrics.update(validation_passed=False,
                                  failure_samples=[{'step_index': 1, 'error_type': 'request_render_error'}])
        payload = build_validation_input(run)
        self.assertEqual(payload['assessment']['status'], 'insufficient_data')
        self.assertFalse(evidence(payload, 'step.1')['extractions_committed'])

    def test_normal_failed_run_preserves_prior_committed_producer(self):
        run = fixture()
        run.latest_metrics.update(validation_passed=False, main_steps_completed=0,
                                  failure_samples=[{'step_index': 2, 'error_type': 'assertion_failed'}])
        second = run.latest_metrics['validation_steps'][1]
        second['status'] = 'failed'
        second['assertions'][0].update(status='failed', error_type='assertion_failed')
        payload = build_validation_input(run)
        self.assertEqual(payload['assessment']['status'], 'failed')
        self.assertEqual(payload['assessment']['failed_steps'], [2])
        self.assertTrue(evidence(payload, 'step.1')['extractions_committed'])
        self.assertFalse(evidence(payload, 'step.2')['extractions_committed'])

    def test_fixed_unique_and_extracted_dependencies_have_anonymous_ids(self):
        payload = build_validation_input(fixture())
        edges = evidence(payload, 'variables.dependencies')
        self.assertEqual({edge['source'] for edge in edges}, {'fixed', 'unique', 'extraction'})
        self.assertTrue(all(edge['variable_id'].startswith('variable.') for edge in edges))
        self.assertTrue(all(edge['available'] is True for edge in edges))
        self.assertEqual(next(edge for edge in edges if edge['source'] == 'extraction')['producer_step'], 1)

    def test_unique_variables_are_unavailable_in_setup_and_forward_refs_are_not_produced(self):
        run = fixture()
        run.snapshot['steps'][0]['query'] = {'key': '${secret_unique}', '${secret_token}': '${undefined_secret}'}
        payload = build_validation_input(run)
        edges = [edge for edge in evidence(payload, 'variables.dependencies') if edge['consumer_step'] == 1]
        self.assertEqual({edge['availability_reason'] for edge in edges},
                         {'not_available_in_setup', 'not_yet_produced', 'not_declared'})
        self.assertTrue(all(edge['available'] is False for edge in edges))
        self.assertNotIn('secret', json.dumps(payload))
        self.assertNotEqual(payload['assessment']['status'], 'passed')

    def test_duplicate_definitions_do_not_claim_one_producer(self):
        run = fixture()
        run.snapshot['variables']['secret_token'] = 'secret-shadow'
        payload = build_validation_input(run)
        edge = next(edge for edge in evidence(payload, 'variables.dependencies') if edge['source'] == 'ambiguous')
        self.assertIsNone(edge['available'])
        self.assertIsNone(edge['producer_step'])
        self.assertNotEqual(payload['assessment']['status'], 'passed')

    def test_assertion_metadata_never_exports_literals_or_selectors(self):
        run = fixture()
        plan = run.snapshot['steps'][0]
        plan['assertions'] = [{'check': 'body.secret_response', 'comparator': 'type', 'expected': 'object'}]
        trace = trace_step(plan, 1)
        trace['assertions'][0]['actual'] = preview({})
        run.latest_metrics['validation_steps'][0] = trace
        item = evidence(build_validation_input(run), 'step.1')['assertions'][0]
        self.assertEqual(item['check_type'], 'body')
        self.assertEqual(item['expected_type'], 'object')
        self.assertEqual(item['actual'], {'type': 'object', 'is_null': False, 'empty': True, 'truncated': False})
        self.assertNotIn('secret', json.dumps(item))
        self.assertNotIn('content', item['actual'])

    def test_null_empty_missing_and_truncated_values_are_distinct(self):
        cases = [(None, 'null', True, None), ('', 'string', False, True),
                 ([], 'list', False, True), ({'missing': True}, 'missing', False, False),
                 ({'missing': 1}, 'object', False, False)]
        for value, kind, is_null, empty in cases:
            with self.subTest(value=value):
                run = fixture()
                run.latest_metrics['validation_steps'][0]['extractions'][0]['value'] = preview(value)
                meta = evidence(build_validation_input(run), 'step.1')['extractions'][0]['value']
                self.assertEqual((meta['type'], meta['is_null'], meta['empty']), (kind, is_null, empty))
        run = fixture()
        run.latest_metrics['validation_steps'][0]['extractions'][0]['value'] = preview('secret', True)
        payload = build_validation_input(run)
        meta = evidence(payload, 'step.1')['extractions'][0]['value']
        self.assertEqual(meta['type'], 'unknown')
        self.assertIsNone(meta['empty'])
        self.assertTrue(meta['truncated'])
        self.assertNotEqual(payload['assessment']['status'], 'passed')

    def test_incomplete_response_is_not_successful_or_committed(self):
        run = fixture()
        run.latest_metrics['validation_steps'][0]['response']['incomplete'] = True
        payload = build_validation_input(run)
        self.assertFalse(evidence(payload, 'step.1')['extractions_committed'])
        self.assertNotEqual(payload['assessment']['status'], 'passed')

    def test_numeric_booleans_nonfinite_values_and_enum_injections_are_unknown(self):
        for value in (True, float('nan'), float('inf'), -1, 10 ** 500, 'secret-number'):
            with self.subTest(value=repr(value)):
                run = fixture()
                row = run.latest_metrics['validation_steps'][0]
                row.update(elapsed_ms=value, method=['POST'], status='secret-state')
                row['response'].update(status_code=value, incomplete='secret-boolean')
                row['assertions'][0].update(comparator=['eq'], error_type='secret-error')
                payload = build_validation_input(run)
                item = evidence(payload, 'step.1')
                self.assertIsNone(item['elapsed_ms'])
                self.assertIsNone(item['response']['status_code'])
                self.assertEqual(item['status'], 'unknown')
                self.assertNotIn('secret', json.dumps(payload, allow_nan=False))
                self.assertNotEqual(payload['assessment']['status'], 'passed')

    def test_duplicate_and_boolean_step_indices_are_not_trusted(self):
        run = fixture()
        run.latest_metrics['validation_steps'].append(copy.deepcopy(run.latest_metrics['validation_steps'][0]))
        self.assertEqual(evidence(build_validation_input(run), 'step.1')['status'], 'unknown')
        run = fixture()
        run.latest_metrics['validation_steps'][0]['step_index'] = True
        self.assertEqual(evidence(build_validation_input(run), 'step.1')['status'], 'unknown')

    def test_unknown_or_truncated_header_preview_does_not_invent_absence(self):
        run = fixture()
        row = run.latest_metrics['validation_steps'][0]
        row['request']['headers'] = {'content': 'secret-incomplete-json', 'truncated': True}
        headers = evidence(build_validation_input(run), 'step.1')['request_headers']
        self.assertIsNone(headers['authorization_present'])
        self.assertIsNone(headers['accept_language_present'])
        row['request']['headers'] = preview({'Authorization': 'Bearer ${secret_token}'})
        headers = evidence(build_validation_input(run), 'step.1')['request_headers']
        self.assertTrue(headers['unresolved_placeholders'])
        row['request']['headers'] = {'content': '{"Authorization":"secret-one","Authorization":"secret-two"}', 'truncated': False}
        headers = evidence(build_validation_input(run), 'step.1')['request_headers']
        self.assertIsNone(headers['authorization_present'])

    def test_missing_elapsed_and_invalid_type_expectation_are_not_normal(self):
        run = fixture()
        run.latest_metrics['validation_steps'][0]['elapsed_ms'] = True
        self.assertNotEqual(build_validation_input(run)['assessment']['status'], 'passed')
        run = fixture()
        run.snapshot['steps'][0]['assertions'][0].update(comparator='type', expected='secret-invalid-type')
        run.latest_metrics['validation_steps'][0] = trace_step(run.snapshot['steps'][0], 1)
        payload = build_validation_input(run)
        self.assertNotEqual(payload['assessment']['status'], 'passed')
        self.assertFalse(evidence(payload, 'step.1')['extractions_committed'])

    def test_check_and_step_limits_and_payload_envelope(self):
        run = fixture()
        plans = []
        for index in range(21):
            plan = plan_step()
            plan['assertions'] *= 51
            plan['extract'] = [{'name': f'secret_{index}_{i}', 'check': f'body.secret_{i}'} for i in range(51)]
            plans.append(plan)
        run.snapshot['steps'] = plans
        run.latest_metrics['validation_steps'] = [trace_step(plan, i) for i, plan in enumerate(plans, 1)]
        payload = build_validation_input(run)
        rows = [item['value'] for item in payload['evidence'] if item['id'].startswith('step.')]
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(len(row['assertions']) <= 50 and len(row['extractions']) <= 50 for row in rows))
        self.assertLess(len(json.dumps(payload, ensure_ascii=False).encode()), MAX_PAYLOAD_BYTES)
        self.assertNotEqual(payload['assessment']['status'], 'passed')
        self.assertNotIn('secret', json.dumps(payload))

    def test_malformed_json_preview_and_missing_check_results_cannot_claim_pass(self):
        for content in ('NaN', 'Infinity', '1e999', 'secret-plaintext', '[' * 1000):
            run = fixture()
            run.latest_metrics['validation_steps'][0]['assertions'][0]['actual'] = {'content': content, 'truncated': False}
            payload = build_validation_input(run)
            self.assertEqual(evidence(payload, 'step.1')['assertions'][0]['actual']['type'], 'unknown')
            self.assertNotEqual(payload['assessment']['status'], 'passed')
            json.dumps(payload, allow_nan=False)
        run = fixture()
        run.latest_metrics['validation_steps'][0]['assertions'] = []
        payload = build_validation_input(run)
        self.assertFalse(evidence(payload, 'step.1')['extractions_committed'])
        self.assertNotEqual(payload['assessment']['status'], 'passed')
