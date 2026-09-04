"""Check the narrow migration boundary without touching the deployment DB."""

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase


class DescriptionURLMigrationTests(SimpleTestCase):
    def test_only_webui_dependencies_are_removed(self):
        loader = MigrationLoader(None)
        before = loader.project_state([
            ('web_testing', '0019_deferred_assertion_execution_state'),
            ('scheduled_tasks', '0007_notice_targets_to_receiver'),
        ])
        after = loader.project_state([
            ('web_testing', '0020_remove_webui_environment_dependencies'),
            ('scheduled_tasks', '0008_scheduledtask_environment_nullable'),
        ])
        generation = after.models[('web_testing', 'webuiscriptgeneration')]
        self.assertIn('target_url', generation.fields)
        for field in ('environment', 'start_path', 'target_url_safe',
                      'credentials_required', 'credentials_provided', 'credentials_expired'):
            self.assertNotIn(field, generation.fields)
        self.assertNotIn('environment', after.models[('web_testing', 'webuitestexecution')].fields)
        self.assertTrue(after.models[('scheduled_tasks', 'scheduledtask')].fields['environment'].null)
        # Shared environment schema remains unchanged. No data reset or cleanup
        # operation is allowed in this migration.
        for model in [('projects', 'environment')]:
            self.assertEqual(
                {name: field.deconstruct() for name, field in before.models[model].fields.items()},
                {name: field.deconstruct() for name, field in after.models[model].fields.items()},
            )
        migration = loader.disk_migrations[('web_testing', '0020_remove_webui_environment_dependencies')]
        self.assertTrue(all(type(op).__name__ in {'RemoveField', 'RenameField', 'AlterField'} for op in migration.operations))
