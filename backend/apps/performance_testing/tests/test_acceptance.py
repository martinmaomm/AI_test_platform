import importlib
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.db.migrations.operations.fields import AddField

from performance_testing.acceptance import run_acceptance, validate_targets


class AcceptanceTests(SimpleTestCase):
    def run_fixture(self, **changes):
        fields = dict(mode='load', status='completed', acceptance_targets={},
                      latest_metrics={'requests': 100, 'failures': 0, 'p95': 200,
                                      'elapsed_seconds': 10, 'complete': True},
                      metrics_samples=[], participants=SimpleNamespace(all=lambda: [
                          SimpleNamespace(status='stopped', latest_metrics={'complete': True})]))
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_unconfigured_validation_and_pending_never_claim_pass(self):
        self.assertEqual(run_acceptance(self.run_fixture())['status'], 'not_configured')
        self.assertEqual(run_acceptance(self.run_fixture(mode='validation'))['status'], 'not_applicable')
        result = run_acceptance(self.run_fixture(status='running', acceptance_targets={'p95_ms': 100}))
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['checks'][0]['status'], 'pending')

    def test_targets_use_complete_metrics_and_keep_zero_error_goal(self):
        targets = {'p95_ms': 200, 'error_rate_percent': 0, 'rps_min': 10}
        run = self.run_fixture(acceptance_targets=targets)
        self.assertEqual(run_acceptance(run)['status'], 'met')
        run.latest_metrics['failures'] = 1
        result = run_acceptance(run)
        self.assertEqual(result['status'], 'not_met')
        self.assertEqual(result['checks'][1]['actual'], 1)
        run.latest_metrics['complete'] = False
        self.assertEqual(run_acceptance(run)['status'], 'insufficient_data')
        run.latest_metrics['complete'] = True
        run.participants = SimpleNamespace(all=lambda: [])
        self.assertEqual(run_acceptance(run)['status'], 'insufficient_data')

    def test_invalid_and_missing_data_cannot_pass(self):
        for value in ([], None, {'unknown': 5}, {'p95_ms': True}, {'p95_ms': float('nan')},
                      {'p95_ms': float('inf')}, {'p95_ms': 10 ** 500}, {'rps_min': 0}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_targets(value)
        for metrics in ({}, {'requests': 0, 'complete': True}, {'requests': 20, 'complete': True}):
            self.assertEqual(run_acceptance(self.run_fixture(
                latest_metrics=metrics, acceptance_targets={'p95_ms': 500}))['status'], 'insufficient_data')

    def test_migration_is_additive_without_rewriting_history(self):
        migration = importlib.import_module('performance_testing.migrations.0010_performance_acceptance_targets').Migration
        self.assertEqual(len(migration.operations), 2)
        self.assertTrue(all(isinstance(item, AddField) for item in migration.operations))
        self.assertTrue(all(item.field.default is dict for item in migration.operations))
