import importlib

from django.db.migrations.operations.fields import AddField, RemoveField
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

    def test_execution_migration_is_additive_and_reversible(self):
        migration = importlib.import_module(
            'performance_testing.migrations.0002_execution_control_plane',
        ).Migration
        creates = [item for item in migration.operations if isinstance(item, CreateModel)]
        self.assertEqual(
            {item.name for item in creates},
            {'PerformanceRun', 'PerformanceControllerState'},
        )
        self.assertTrue(all(item.reversible for item in migration.operations))

    def test_node_lifecycle_migration_removes_only_app_labels_and_adds_soft_delete(self):
        migration = importlib.import_module(
            'performance_testing.migrations.0003_remove_performancenode_labels_and_add_deleted_at',
        ).Migration
        self.assertEqual(len(migration.operations), 2)
        self.assertIsInstance(migration.operations[0], RemoveField)
        self.assertEqual(migration.operations[0].model_name, 'performancenode')
        self.assertEqual(migration.operations[0].name, 'labels')
        self.assertIsInstance(migration.operations[1], AddField)
        self.assertEqual(migration.operations[1].model_name, 'performancenode')
        self.assertEqual(migration.operations[1].name, 'deleted_at')
        self.assertTrue(all(item.reversible for item in migration.operations))
