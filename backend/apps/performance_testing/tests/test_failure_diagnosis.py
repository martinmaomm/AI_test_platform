import json
from unittest import TestCase

from performance_testing.analysis_data import build_analysis_input
from performance_testing.failure_diagnosis import build_failure_diagnosis
from performance_testing.tests.test_analysis_data import fixture_run


def fixture():
    run = fixture_run()
    run.participants.all()[0].node_id = 'private-node-id'
    run.snapshot['steps'] = [{
        'name': 'private-step-name', 'phase': 'main', 'method': 'POST',
        'path': '/private-path', 'headers': {'Authorization': 'private-token'},
        'assertions': [
            {'check': 'body.private_field', 'comparator': 'eq', 'expected': 'private-expected'},
            {'check': 'status_code', 'comparator': 'eq', 'expected': 200},
        ],
        'extract': [{'check': 'body.private_token', 'name': 'private-variable'}],
    }]
    run.latest_metrics['failure_samples'] = [{
        'step_index': 1, 'step_name': 'private-step-name', 'phase': 'main',
        'node_id': 'private-node-id', 'check': 'body.private_field', 'comparator': 'eq',
        'expected': 'private-expected', 'actual': 'private-actual',
        'error_type': 'assertion_failed', 'message': 'private-message',
        'headers': {'Cookie': 'private-cookie'},
    }]
    return run


class FormalFailureDiagnosisTests(TestCase):
    def test_safe_localization_reaches_ai_without_raw_values(self):
        run = fixture()
        result = build_failure_diagnosis(run)
        item = result['items'][0]
        self.assertEqual((item['step_index'], item['node_index'], item['rule_index']), (1, 1, 1))
        self.assertEqual(item['rule_kind'], 'assertion')
        self.assertEqual(item['check_type'], 'body')
        self.assertEqual(item['actual_meta']['type'], 'string')
        self.assertIn('断言 1', item['label'])
        payload = build_analysis_input(run, {})
        self.assertEqual(next(row['value']['rule_index'] for row in payload['evidence']
                              if row['id'] == 'failure.1'), 1)
        self.assertTrue(any(row['id'] == 'errors.samples' for row in payload['evidence']))
        self.assertNotIn('private-', json.dumps(payload, ensure_ascii=False))
        self.assertNotIn('private-', json.dumps(result, ensure_ascii=False))

    def test_missing_step_phase_or_contradictory_name_is_not_associated(self):
        for changes in ({'step_index': 2}, {'step_index': True}, {'phase': 'setup'},
                        {'phase': None}, {'step_name': 'another-step'}):
            with self.subTest(changes=changes):
                run = fixture()
                run.latest_metrics['failure_samples'][0].update(changes)
                item = build_failure_diagnosis(run)['items'][0]
                self.assertIsNone(item['step_index'])
                self.assertIsNone(item['rule_index'])
                self.assertEqual(item['method'], 'unknown')

    def test_unknown_node_is_not_assigned_to_only_participant(self):
        run = fixture()
        run.latest_metrics['failure_samples'][0]['node_id'] = 'other-private-node'
        self.assertIsNone(build_failure_diagnosis(run)['items'][0]['node_index'])
        del run.latest_metrics['failure_samples'][0]['node_id']
        self.assertIsNone(build_failure_diagnosis(run)['items'][0]['node_index'])

    def test_rule_matching_respects_expected_and_ambiguity(self):
        run = fixture()
        assertion = run.snapshot['steps'][0]['assertions'][0]
        run.snapshot['steps'][0]['assertions'].insert(0, {**assertion, 'expected': 'different'})
        self.assertEqual(build_failure_diagnosis(run)['items'][0]['rule_index'], 2)
        run.snapshot['steps'][0]['assertions'].append(dict(assertion))
        self.assertIsNone(build_failure_diagnosis(run)['items'][0]['rule_index'])

    def test_extraction_and_network_samples_have_bounded_safe_rules(self):
        run = fixture()
        row = run.latest_metrics['failure_samples'][0]
        row.update(check='body.private_token', comparator='extract', expected='present',
                   actual={'missing': True}, error_type='extraction_missing')
        item = build_failure_diagnosis(run)['items'][0]
        self.assertEqual((item['rule_kind'], item['rule_index']), ('extraction', 1))
        self.assertEqual(item['actual_meta']['type'], 'missing_marker')
        row.update(check='network', comparator='eq', error_type='read_timeout')
        item = build_failure_diagnosis(run)['items'][0]
        self.assertEqual(item['rule_kind'], 'network')
        self.assertIsNone(item['rule_index'])

    def test_sample_cap_and_group_counts_are_explicitly_not_request_counts(self):
        run = fixture()
        row = run.latest_metrics['failure_samples'][0]
        run.latest_metrics['failure_samples'] = [{**row, 'actual': f'private-value-{i}'} for i in range(25)]
        result = build_failure_diagnosis(run)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['sample_count'], 20)
        self.assertTrue(any('不是失败请求次数' in text for text in result['limitations']))
        self.assertTrue(any('采集上限' in text for text in result['limitations']))

    def test_empty_and_malformed_evidence_never_implies_success(self):
        run = fixture()
        run.latest_metrics['failure_samples'] = []
        result = build_failure_diagnosis(run)
        self.assertEqual(result['items'], [])
        self.assertTrue(any('不等于运行没有失败' in text for text in result['limitations']))
        run.latest_metrics['failure_samples'] = [None, {'check': [], 'comparator': {}, 'error_type': []}]
        result = build_failure_diagnosis(run)
        self.assertEqual(result['items'][0]['error_type'], 'unknown')
        self.assertIsNone(result['items'][0]['step_index'])
        json.dumps(result, allow_nan=False)

    def test_literal_values_and_nonfinite_numbers_are_never_copied(self):
        run = fixture()
        for actual in ('private-content…', float('nan'), 10 ** 500, {'private-key': 'private-token'}):
            with self.subTest(actual_type=type(actual)):
                run.latest_metrics['failure_samples'][0]['actual'] = actual
                output = json.dumps(build_failure_diagnosis(run), allow_nan=False, ensure_ascii=False)
                self.assertNotIn('private-', output)
        run.mode = 'validation'
        self.assertEqual(build_failure_diagnosis(run), {'items': [], 'limitations': []})
