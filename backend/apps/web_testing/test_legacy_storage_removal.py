"""Exercise the real schema migration without touching the configured business DB."""
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase


class RetiredModelRegistryTests(SimpleTestCase):
    def test_legacy_model_and_reverse_relations_are_not_registered(self):
        with self.assertRaises(LookupError):
            apps.get_model('web_testing', 'MidSceneScript')
        for model in (get_user_model(), apps.get_model('projects', 'Project')):
            self.assertNotIn('midscene_scripts', {
                relation.get_accessor_name() for relation in model._meta.related_objects
            })


class LegacyStorageMigrationTests(TransactionTestCase):
    def setUp(self):
        self.before = MigrationLoader(connection).project_state([
            ('web_testing', '0023_webuitestcase_creation_source'),
        ])
        self.legacy = self.before.apps.get_model('web_testing', 'MidSceneScript')
        cls = import_module('web_testing.migrations.0024_remove_midscenescript').Migration
        self.migration = cls('0024_remove_midscenescript', 'web_testing')
        with connection.schema_editor() as editor:
            editor.create_model(self.legacy)

    def tearDown(self):
        if self.legacy._meta.db_table in connection.introspection.table_names():
            with connection.schema_editor() as editor:
                editor.delete_model(self.legacy)

    def test_empty_table_is_dropped_and_reverse_restores_empty_schema(self):
        user_count = get_user_model().objects.count()
        with connection.schema_editor() as editor:
            after = self.migration.apply(self.before.clone(), editor)
        self.assertNotIn(self.legacy._meta.db_table, connection.introspection.table_names())
        with self.assertRaises(LookupError):
            after.apps.get_model('web_testing', 'MidSceneScript')
        self.assertEqual(get_user_model().objects.count(), user_count)
        with connection.schema_editor() as editor:
            self.migration.unapply(self.before.clone(), editor)
        self.assertIn(self.legacy._meta.db_table, connection.introspection.table_names())
        self.assertEqual(self.legacy.objects.count(), 0)

    def test_populated_table_is_preserved_and_migration_aborts(self):
        user = get_user_model().objects.create_user(username='legacy-storage-fixture')
        project = apps.get_model('projects', 'Project').objects.create(
            name='retained fixture', project_type='web', created_by=user,
        )
        row = self.legacy.objects.create(
            name='retained script', description='fixture', natural_language='fixture',
            created_by_id=user.pk, project_id=project.pk,
        )
        with self.assertRaisesRegex(RuntimeError, '仍有数据'):
            with connection.schema_editor() as editor:
                self.migration.apply(self.before.clone(), editor)
        self.assertTrue(self.legacy.objects.filter(pk=row.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertTrue(type(project).objects.filter(pk=project.pk).exists())
