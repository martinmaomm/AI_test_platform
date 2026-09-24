from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest import TestCase

from performance_testing.comparison_data import build_comparison_data, build_comparison_input, metric_delta


def fixture(**updates):
    metrics = {'requests': 100, 'failures': 2, 'rps': 10, 'elapsed_seconds': 10,
               'p95': 250, 'p99': 400, 'avg_response_time': 100, 'complete': True,
               'entries': [{'method': 'POST', 'name': '1. secret-endpoint', 'requests': 100,
                            'failures': 2, 'p95': 250}]}
    node = SimpleNamespace(node_id='secret-node', assigned_users=10, status='stopped',
                           node_agent_version='secret-agent', node_engine_version='secret-engine',
                           node_protocol_version=4, latest_metrics={'complete': True})
    values = dict(pk='secret-run', plan_id=1, created_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
                  status='completed', mode='load', latest_metrics=metrics,
                  snapshot={'plan_name': 'secret-plan', 'run_id': 'secret-run', 'users': 10,
                            'spawn_rate': 2, 'duration_seconds': 10, 'wait_seconds': 1,
                            'connect_timeout_seconds': 15, 'read_timeout_seconds': 30,
                            'base_url': 'http://secret-target/', 'allowed_methods': ['GET', 'POST'],
                            'steps': [{'name': 'secret-step', 'method': 'POST', 'phase': 'main', 'path': '/secret-path',
                                       'assertions': [], 'extract': [], 'headers': {'Authorization': 'secret-token'}}],
                            'variables': {'secret-var': 'secret-value'}, 'unique_variables': [],
                            'engine_version': 'secret-engine'},
                  participants=SimpleNamespace(all=lambda: [node]), metrics_samples=[])
    values.update(updates)
    return SimpleNamespace(**values)


class ComparisonDataTests(TestCase):
    def test_equal_conditions_ignore_run_identity_and_validation_signature(self):
        current, baseline = fixture(), fixture()
        current.snapshot['run_id'] = 'another-run'
        current.snapshot['nodes'] = [{'validation_key': 'different-per-run'}]
        comparison = build_comparison_data(current, baseline)
        self.assertEqual(comparison['comparability']['status'], 'comparable')
        self.assertTrue(all(row['equal'] for row in comparison['comparability']['conditions']))

    def test_configuration_changes_are_visible_without_leaking_configuration(self):
        for change in ('users', 'steps', 'base_url', 'variables', 'unique_variables'):
            current, baseline = fixture(), fixture()
            current.snapshot[change] = {'users': 20, 'steps': [{**current.snapshot['steps'][0], 'body': 'secret-different'}],
                                        'base_url': 'http://secret-different/', 'variables': {},
                                        'unique_variables': [{'name': 'secret-different'}]}[change]
            output = build_comparison_data(current, baseline)
            self.assertEqual(output['comparability']['status'], 'conditions_changed', change)
            self.assertNotIn('secret-different', json.dumps(output))
        current.participants.all()[0].node_id = 'different-node'
        self.assertEqual(build_comparison_data(current, baseline)['comparability']['status'], 'conditions_changed')

    def test_missing_configuration_and_incomplete_reports_prevent_comparable(self):
        for modify in (lambda run: run.snapshot.pop('steps'),
                       lambda run: setattr(run, 'status', 'cancelled'),
                       lambda run: run.latest_metrics.update(complete=False),
                       lambda run: run.latest_metrics.update(requests=0),
                       lambda run: run.latest_metrics.update(failures=101),
                       lambda run: setattr(run.participants.all()[0], 'node_engine_version', ''),
                       lambda run: setattr(run, 'participants', SimpleNamespace(all=lambda: []))):
            current = fixture()
            modify(current)
            self.assertEqual(build_comparison_data(current, fixture())['comparability']['status'], 'insufficient_data')

    def test_math_zero_missing_error_rate_and_overflow(self):
        row = metric_delta('rps', '吞吐量', 'requests/s', 10, 15)
        self.assertEqual((row['delta'], row['delta_percent'], row['direction']), (5, 50, 'increased'))
        row = metric_delta('error_rate_percent', '错误率', '%', 2, 3)
        self.assertEqual((row['delta'], row['delta_percent']), (1, 50))
        self.assertIsNone(metric_delta('rps', '', '', 0, 10)['delta_percent'])
        for invalid in (None, True, float('nan'), float('inf'), -1, 10 ** 500):
            row = metric_delta('rps', '', '', invalid, 10)
            self.assertIsNone(row['delta'])
            self.assertEqual(row['direction'], 'unknown')
            json.dumps(row, allow_nan=False)
        self.assertIsNone(metric_delta('rps', '', '', 1e-300, 1e300)['delta_percent'])

    def test_endpoint_pairing_by_identity_not_position(self):
        baseline, current = fixture(), fixture()
        old_entry = deepcopy(baseline.latest_metrics['entries'][0])
        second = {**old_entry, 'name': '2. another', 'requests': 10}
        baseline.latest_metrics['entries'].append(second)
        current.latest_metrics['entries'] = [second, {**old_entry, 'requests': 80}]
        output = build_comparison_data(current, baseline)['endpoints']
        self.assertEqual([(row['baseline_index'], row['current_index']) for row in output], [(2, 1), (1, 2)])
        self.assertEqual(output[1]['metrics'][0]['delta'], -20)
        self.assertEqual(output[1]['metrics'][2]['baseline'], 10)
        self.assertEqual(output[1]['metrics'][2]['current'], 8)

    def test_duplicate_and_unmatched_endpoints_never_force_pair(self):
        baseline, current = fixture(), fixture()
        current.latest_metrics['entries'] *= 2
        rows = build_comparison_data(current, baseline)['endpoints']
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['match_status'] == 'ambiguous' for row in rows))
        self.assertTrue(all(row['metrics'][0]['delta'] is None for row in rows))
        current.latest_metrics['entries'] = [{'name': 'new', 'method': 'GET', 'requests': 10}]
        rows = build_comparison_data(current, baseline)['endpoints']
        self.assertEqual([r['match_status'] for r in rows], ['current_only', 'baseline_only'])

    def test_zero_request_latency_is_unknown_not_successful_zero(self):
        current = fixture()
        current.latest_metrics.update(requests=0, p95=0)
        current.latest_metrics['entries'][0].update(requests=0, p95=0)
        result = build_comparison_data(current, fixture())
        self.assertIsNone(next(r for r in result['overall'] if r['key'] == 'p95')['current'])
        self.assertIsNone(next(r for r in result['endpoints'][0]['metrics'] if r['key'] == 'p95')['current'])

    def test_ai_projection_excludes_every_raw_name_id_url_value_and_timestamp(self):
        current, baseline = fixture(), fixture()
        current.latest_metrics['failure_samples'] = [{'message': 'secret-error'}]
        current.latest_metrics['validation_steps'] = [{'request': {'body': 'secret-body'}}]
        current.metrics_samples = [{'timestamp': 'secret-timestamp', 'metrics': {'requests': 1}}]
        payload = build_comparison_input(current, baseline)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        self.assertNotIn('secret-', encoded)
        self.assertEqual(payload['analysis_type'], 'load_comparison')
        self.assertEqual(payload['assessment']['status'], 'comparable')
        self.assertIn('comparison.endpoint.1', encoded)

    def test_malformed_entries_and_nonfinite_configuration_do_not_crash_or_leak(self):
        current = fixture()
        current.snapshot.update(users=True, steps=[float('nan')])
        current.latest_metrics.update(entries=[None, {'method': ['bad'], 'name': {'secret': 'value'}}], rps=float('nan'))
        payload = build_comparison_input(current, fixture())
        json.dumps(payload, allow_nan=False)
        self.assertEqual(payload['assessment']['status'], 'insufficient_data')
        self.assertNotIn('secret', json.dumps(payload))

    def test_identically_invalid_load_or_script_is_not_comparable(self):
        for key, value in (('duration_seconds', 1.5), ('wait_seconds', 0),
                           ('connect_timeout_seconds', True), ('steps', [None]), ('steps', [{}])):
            current, baseline = fixture(), fixture()
            for run in (current, baseline):
                run.snapshot[key] = value
            self.assertEqual(build_comparison_data(current, baseline)['comparability']['status'], 'insufficient_data')
