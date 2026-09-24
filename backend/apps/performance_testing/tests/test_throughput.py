from unittest import TestCase

from performance_testing.throughput import derive_throughput


def sample(requests, *, elapsed=None, rps=None, report_time=None, timestamp=None):
    metrics = {'requests': requests}
    if elapsed is not None:
        metrics['elapsed_seconds'] = elapsed
    if rps is not None:
        metrics['rps'] = rps
    if report_time is not None:
        metrics['report_time'] = report_time
    return {'timestamp': timestamp, 'metrics': metrics}


class ThroughputTests(TestCase):
    def test_average_endpoint_and_elapsed_intervals(self):
        result = derive_throughput({
            'requests': 60, 'rps': 10, 'elapsed_seconds': 6,
            'entries': [
                {'requests': 30}, {'requests': 0}, {'requests': True},
            ],
        }, [
            sample(0, elapsed=0, timestamp='2026-09-24T00:00:00Z'),
            sample(10, elapsed=2, timestamp='2026-09-24T00:00:02Z'),
            sample(30, elapsed=4, timestamp='2026-09-24T00:00:04Z'),
        ])

        self.assertEqual(result['average_rps'], 10.0)
        self.assertEqual(result['endpoint_rps'], [5.0, 0.0, None])
        self.assertEqual(result['valid_intervals'], 2)
        self.assertEqual([row['rps'] for row in result['intervals']], [None, 5.0, 10.0])
        self.assertEqual([row['interval_seconds'] for row in result['intervals']], [None, 2.0, 2.0])
        self.assertEqual(result['peak_interval_rps'], 10.0)

    def test_existing_average_wins_and_duration_can_be_recovered_for_endpoints(self):
        result = derive_throughput({
            'requests': 100, 'rps': 20,
            'entries': [{'requests': 50}],
        }, [])
        self.assertEqual(result['average_rps'], 20.0)
        self.assertEqual(result['endpoint_rps'], [10.0])

        fallback = derive_throughput({
            'requests': 100, 'rps': float('nan'), 'elapsed_seconds': 10,
            'entries': [{'requests': 50}],
        }, [])
        self.assertEqual(fallback['average_rps'], 10.0)
        self.assertEqual(fallback['endpoint_rps'], [5.0])

    def test_zero_over_zero_does_not_invent_duration(self):
        result = derive_throughput({
            'requests': 0, 'rps': 0, 'entries': [{'requests': 0}],
        }, [])
        self.assertEqual(result['average_rps'], 0.0)
        self.assertEqual(result['endpoint_rps'], [None])
        self.assertIsNone(result['peak_interval_rps'])

    def test_node_elapsed_is_recovered_from_cumulative_requests_and_average_rps(self):
        result = derive_throughput({}, [
            sample(20, rps=10, timestamp='2026-09-24T00:00:20Z'),
            sample(50, rps=10, timestamp='2026-09-24T00:00:40Z'),
        ])
        self.assertEqual(result['valid_intervals'], 1)
        self.assertEqual(result['intervals'][1]['interval_seconds'], 3.0)
        self.assertEqual(result['intervals'][1]['rps'], 10.0)

    def test_same_report_time_beats_later_collection_timestamp(self):
        report_time = '2026-09-24T00:00:10Z'
        result = derive_throughput({}, [
            sample(0, rps=0, report_time=report_time,
                   timestamp='2026-09-24T00:00:10Z'),
            sample(5, rps=0, report_time=report_time,
                   timestamp='2026-09-24T00:00:12Z'),
        ])
        self.assertEqual(result['valid_intervals'], 0)
        self.assertIsNone(result['intervals'][1]['rps'])
        self.assertIsNone(result['peak_interval_rps'])

    def test_same_clock_fallback_accepts_zero_request_interval(self):
        result = derive_throughput({}, [
            sample(0, rps=0, report_time='2026-09-24T00:00:00Z'),
            sample(0, rps=0, report_time='2026-09-24T00:00:02Z'),
        ])
        self.assertEqual(result['valid_intervals'], 1)
        self.assertEqual(result['intervals'][1]['rps'], 0.0)
        self.assertEqual(result['peak_interval_rps'], 0.0)

    def test_timestamp_fallback_requires_both_points_and_never_mixes_clocks(self):
        fallback = derive_throughput({}, [
            sample(1, timestamp='2026-09-24T00:00:00Z'),
            sample(5, timestamp='2026-09-24T00:00:02Z'),
        ])
        self.assertEqual(fallback['intervals'][1]['rps'], 2.0)

        different_sources = derive_throughput({}, [
            sample(1, report_time='2026-09-24T00:00:00Z',
                   timestamp='2026-09-24T00:00:00Z'),
            sample(5, timestamp='2026-09-24T00:00:02Z'),
            sample(9, timestamp='2026-09-24T00:00:04Z'),
        ])
        self.assertEqual(
            [row['rps'] for row in different_sources['intervals']],
            [None, None, 2.0],
        )

        mixed = derive_throughput({}, [
            sample(1, elapsed=1, timestamp='2026-09-24T00:00:01Z'),
            sample(5, report_time='2026-09-24T00:00:03Z',
                   timestamp='2026-09-24T00:00:03Z'),
            sample(9, elapsed=5, timestamp='2026-09-24T00:00:05Z'),
        ])
        self.assertEqual([row['rps'] for row in mixed['intervals']], [None, None, None])
        self.assertIsNone(mixed['peak_interval_rps'])

    def test_duplicate_reversed_time_and_counter_rollback_are_invalid(self):
        result = derive_throughput({}, [
            sample(10, elapsed=2),
            sample(20, elapsed=2),
            sample(30, elapsed=1),
            sample(25, elapsed=4),
        ])
        self.assertEqual([row['rps'] for row in result['intervals']], [None] * 4)
        self.assertEqual(result['valid_intervals'], 0)
        self.assertIsNone(result['peak_interval_rps'])

    def test_bad_relationship_cannot_be_the_next_interval_baseline(self):
        result = derive_throughput({}, [
            sample(100, elapsed=10),
            sample(0, elapsed=9),
            sample(110, elapsed=11),
            sample(120, elapsed=12),
        ])
        self.assertEqual(
            [row['rps'] for row in result['intervals']],
            [None, None, None, 10.0],
        )
        self.assertEqual(result['peak_interval_rps'], 10.0)

    def test_exact_duplicate_keeps_baseline_but_does_not_add_interval(self):
        result = derive_throughput({}, [
            sample(10, elapsed=1),
            sample(10, elapsed=1),
            sample(30, elapsed=3),
        ])
        self.assertEqual(
            [row['rps'] for row in result['intervals']],
            [None, None, 10.0],
        )
        self.assertEqual(result['valid_intervals'], 1)

    def test_single_or_invalid_points_do_not_become_zero_or_spikes(self):
        single = derive_throughput({}, [sample(100, elapsed=10)])
        self.assertEqual(single['intervals'][0]['rps'], None)
        self.assertEqual(single['valid_intervals'], 0)
        self.assertIsNone(single['peak_interval_rps'])

        invalid = derive_throughput({}, [
            sample(True, elapsed=1),
            sample(10, elapsed=float('nan')),
            sample(20, elapsed=float('inf')),
            sample(10 ** 1000, elapsed=4),
        ])
        self.assertTrue(all(row['rps'] is None for row in invalid['intervals']))

    def test_last_400_samples_remain_positionally_aligned(self):
        samples = [
            sample(index, elapsed=index, timestamp=f'point-{index}')
            for index in range(401)
        ]
        result = derive_throughput({'requests': 400, 'rps': 1}, samples)
        self.assertEqual(len(result['intervals']), 400)
        self.assertEqual(result['intervals'][0]['timestamp'], 'point-1')
        self.assertIsNone(result['intervals'][0]['rps'])
        self.assertEqual(result['intervals'][-1]['timestamp'], 'point-400')
        self.assertEqual(result['valid_intervals'], 399)
