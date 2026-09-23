import json
from types import SimpleNamespace
from unittest import TestCase

from performance_testing.analysis_data import build_analysis_input, evaluate_targets, number


def fixture_run(**updates):
    metrics = {'requests': 100, 'failures': 2, 'rps': 10, 'p95': 250, 'p99': 500,
               'avg_response_time': 100, 'users': 0, 'complete': True,
               'elapsed_seconds': 10, 'entries': [{'name': '/login?token=secret-path',
                   'method': 'POST', 'requests': 100, 'failures': 2, 'p95': 250}],
               'failure_samples': [{'error_type': 'read_timeout', 'message': 'secret-error',
                   'actual': 'secret-actual', 'expected': 'secret-expected'}],
               'validation_steps': [{'response': {'body': 'secret-response'}}]}
    node = SimpleNamespace(name='secret-node', assigned_users=10, status='stopped',
                           latest_metrics={**metrics, 'worker_cpu': 20, 'worker_memory': 1024 * 1024},
                           metrics_samples=[{'timestamp': '2026-09-23T00:00:01Z', 'metrics': {
                               'worker_cpu': 80, 'worker_memory': 2 * 1024 * 1024}}])
    values = dict(status='completed', mode='load', latest_metrics=metrics,
                  snapshot={'users': 10, 'spawn_rate': 2, 'duration_seconds': 10,
                            'headers': {'Authorization': 'secret-authorization'},
                            'variables': {'token': 'secret-variable'}, 'plan_name': 'secret-plan',
                            'steps': [{'body': 'secret-body'}]},
                  participants=SimpleNamespace(all=lambda: [node]),
                  metrics_samples=[
                      {'timestamp': '2026-09-23T00:00:01Z', 'metrics': {**metrics, 'users': 5, 'elapsed_seconds': 1}},
                      {'timestamp': '2026-09-23T00:00:05Z', 'metrics': {**metrics, 'users': 10, 'elapsed_seconds': 5}},
                      {'timestamp': '2026-09-23T00:00:10Z', 'metrics': {**metrics, 'users': 0, 'elapsed_seconds': 10}},
                  ])
    values.update(updates)
    return SimpleNamespace(**values)


class AnalysisEvidenceTests(TestCase):
    def evidence(self, payload, key):
        return next(item['value'] for item in payload['evidence'] if item['id'] == key)

    def test_only_allowlisted_statistics_leave_platform(self):
        payload = build_analysis_input(fixture_run(), {})
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('secret-', encoded)
        self.assertNotIn('Authorization', encoded)
        self.assertNotIn('/login', encoded)
        self.assertEqual(self.evidence(payload, 'errors.samples'), {'read_timeout': 1})
        self.assertEqual(self.evidence(payload, 'endpoint.1')['method'], 'POST')
        self.assertEqual(payload['assessment']['status'], 'not_configured')

    def test_cpu_memory_and_peak_users_use_history_not_only_finished_zero(self):
        payload = build_analysis_input(fixture_run(), {})
        self.assertEqual(self.evidence(payload, 'overall.users'), 0)
        self.assertEqual(self.evidence(payload, 'load.observed_peak_users'), 10)
        node = self.evidence(payload, 'node.1')
        self.assertEqual(node['peak_worker_cpu_percent'], 80)
        self.assertEqual(node['peak_worker_memory_mib'], 2)

    def test_targets_are_deterministic_with_zero_error_rate_allowed(self):
        payload = build_analysis_input(fixture_run(), {'p95_ms': 250, 'rps_min': 10, 'error_rate_percent': 2})
        self.assertEqual(payload['assessment']['status'], 'met')
        payload = build_analysis_input(fixture_run(), {'error_rate_percent': 0})
        self.assertEqual(payload['assessment']['status'], 'not_met')
        self.assertEqual(payload['assessment']['checks'][0]['actual'], 2)

    def test_cancelled_failed_missing_nodes_or_incomplete_can_never_meet_goals(self):
        for state in ('cancelled', 'failed', 'incomplete'):
            result = build_analysis_input(fixture_run(status=state), {'rps_min': 1})
            self.assertEqual(result['assessment']['status'], 'insufficient_data')
        run = fixture_run(participants=SimpleNamespace(all=lambda: []))
        self.assertEqual(build_analysis_input(run, {'rps_min': 1})['assessment']['status'], 'insufficient_data')
        run = fixture_run()
        run.participants.all()[0].latest_metrics['complete'] = False
        self.assertEqual(build_analysis_input(run, {'rps_min': 1})['assessment']['status'], 'insufficient_data')

    def test_zero_requests_or_missing_metrics_is_insufficient_not_zero_latency_success(self):
        run = fixture_run()
        run.latest_metrics.update(requests=0, failures=0, p95=0)
        result = build_analysis_input(run, {'p95_ms': 1000, 'error_rate_percent': 1})
        self.assertEqual(result['assessment']['status'], 'insufficient_data')
        self.assertIsNone(self.evidence(result, 'overall.error_rate_percent'))
        run.latest_metrics.update(requests=10, p95=None)
        self.assertEqual(build_analysis_input(run, {'p95_ms': 1000})['assessment']['status'], 'insufficient_data')

    def test_nonfinite_booleans_and_malformed_objects_do_not_enter_input(self):
        for invalid in (True, '12', float('nan'), float('inf'), -1, 10 ** 500):
            self.assertIsNone(number(invalid))
        run = fixture_run()
        run.latest_metrics.update(p95=float('nan'), failures=True, entries=[{'method': ['POST']}],
                                  failure_samples=[{'error_type': ['bad']}, {'error_type': 'secret-type'}])
        result = build_analysis_input(run, {})
        json.dumps(result, allow_nan=False)
        self.assertEqual(self.evidence(result, 'errors.samples'), {'other': 2})
        self.assertIsNone(self.evidence(result, 'overall.p95'))
        self.assertIsNone(self.evidence(result, 'overall.error_rate_percent'))
        self.assertNotIn('secret-type', json.dumps(result))

    def test_error_rate_derived_from_counts_not_untrusted_rate(self):
        run = fixture_run()
        run.latest_metrics['error_rate'] = 0
        self.assertEqual(self.evidence(build_analysis_input(run, {}), 'overall.error_rate_percent'), 2)
        run.latest_metrics['failures'] = 101
        self.assertIsNone(self.evidence(build_analysis_input(run, {}), 'overall.error_rate_percent'))

    def test_tiny_nonzero_error_rate_cannot_pass_zero_error_target(self):
        run = fixture_run()
        run.latest_metrics.update(requests=1_000_000_000, failures=1)
        result = build_analysis_input(run, {'error_rate_percent': 0})
        self.assertEqual(result['assessment']['status'], 'not_met')
        self.assertGreater(result['assessment']['checks'][0]['actual'], 0)

    def test_large_series_and_endpoint_lists_bounded_and_disclosed(self):
        run = fixture_run()
        run.latest_metrics['entries'] *= 70
        run.metrics_samples = [{'metrics': {'elapsed_seconds': i, 'users': i % 10, 'p95': i}} for i in range(400)]
        payload = build_analysis_input(run, {})
        points = self.evidence(payload, 'trend.overall')
        self.assertEqual(len(points), 60)
        self.assertEqual(points[0]['elapsed_seconds'], 0)
        self.assertEqual(points[-1]['elapsed_seconds'], 399)
        self.assertEqual(len([x for x in payload['evidence'] if x['id'].startswith('endpoint.')]), 50)
        self.assertTrue(any('50' in line for line in payload['limitations']))
        self.assertTrue(any('60' in line for line in payload['limitations']))

    def test_missing_series_and_empty_input_have_explicit_limits(self):
        payload = build_analysis_input(fixture_run(latest_metrics={}, metrics_samples=[]), {})
        self.assertIsNone(self.evidence(payload, 'load.observed_peak_users'))
        self.assertTrue(any('采样不足' in line for line in payload['limitations']))
        self.assertTrue(any('没有接口级' in line for line in payload['limitations']))
