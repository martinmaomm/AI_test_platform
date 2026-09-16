import importlib

from django.db.migrations.operations.models import CreateModel
from django.test import SimpleTestCase


class PerformanceMigrationTests(SimpleTestCase):
    def test_initial_migration_only_creates_new_tables_and_is_reversible(self):
        migration = importlib.import_module(
            'performance_testing.migrations.0001_initial',
        ).Migration
        creates = [item for item in migration.operations if isinstance(item, CreateModel)]
        self.assertEqual(
            {item.name for item in creates},
            {'PerformanceTarget', 'PerformancePlan', 'PerformanceNode'},
        )
        self.assertTrue(all(item.reversible for item in migration.operations))
