import importlib
from types import SimpleNamespace
from unittest.mock import Mock

from django.db.migrations.operations.fields import AddField, RemoveField
from django.db.migrations.operations.special import RunPython
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

    def test_v2_migration_is_additive_and_upgrades_legacy_steps_without_deleting_history(self):
        module = importlib.import_module('performance_testing.migrations.0004_plan_contract_v2')
        migration = module.Migration
        self.assertEqual(len([item for item in migration.operations if isinstance(item, AddField)]), 4)
        self.assertEqual(len([item for item in migration.operations if isinstance(item, RunPython)]), 1)
        self.assertTrue(all(item.reversible for item in migration.operations))

        plan = SimpleNamespace(
            steps=[{'name': 'legacy', 'method': 'GET', 'path': '/items?page=1&tag=a&tag=b',
                    'expected_status': 201, 'headers': {}, 'body': None}],
            variables={}, unique_variables=[], save=Mock(),
        )
        manager = Mock()
        manager.all.return_value.iterator.return_value = iter([plan])
        model = SimpleNamespace(objects=manager)
        apps = Mock()
        apps.get_model.return_value = model
        module.upgrade_plans(apps, None)
        self.assertEqual(plan.steps[0]['phase'], 'main')
        self.assertEqual(plan.steps[0]['path'], '/items')
        self.assertEqual(plan.steps[0]['query'], {'page': '1', 'tag': ['a', 'b']})
        self.assertEqual(plan.steps[0]['assertions'], [
            {'check': 'status_code', 'comparator': 'eq', 'expected': 201},
        ])
        self.assertNotIn('expected_status', plan.steps[0])
        plan.save.assert_called_once_with(update_fields=('steps', 'variables', 'unique_variables'))

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
