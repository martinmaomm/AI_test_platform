"""Offline unit checks for the narrowly scoped api_testing 0019 repair command."""
import os
from io import StringIO
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'
os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'


def deny_network(*args, **kwargs):
    raise RuntimeError('Network is forbidden in migration-repair unit tests')


with patch.object(socket.socket, 'connect', deny_network), patch.object(socket.socket, 'connect_ex', deny_network):
    from aits_backend import settings as config
    config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
    config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    config.CELERY_BROKER_URL = 'memory://'
    config.CELERY_RESULT_BACKEND = 'cache+memory://'
    config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
    import django
    django.setup()

from django.db import connection
from django.db.migrations.graph import MigrationGraph

from api_testing.browser_discovery_migration_repair import (
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    RepairAction,
    RepairAssessment,
    apply_actions,
    assess_0019_prefix,
    historical_0019_models,
    later_migration_nodes,
    snapshot_schema,
)
from api_testing.management.commands import repair_api_browser_discovery_migration as command_module


def complete_snapshot(models):
    tables = {model._meta.db_table for model in models.values()}
    columns = {}
    indexes = {}
    foreign_keys = []
    for model in models.values():
        table = model._meta.db_table
        columns[table] = {}
        info = []
        for field in model._meta.local_fields:
            db_type = field.db_type(connection) or 'text'
            columns[table][field.column] = ColumnInfo(
                column_type=db_type,
                data_type=db_type.split('(', 1)[0].split()[0],
                character_maximum_length=field.max_length,
                nullable=field.null,
            )
            if field.primary_key or field.unique:
                info.append(IndexInfo(
                    'PRIMARY' if field.primary_key else f'{field.column}_uniq',
                    (field.column,), True, primary=field.primary_key,
                ))
            elif field.db_index:
                info.append(IndexInfo(f'{field.column}_idx', (field.column,), False))
            if field.remote_field and field.db_constraint:
                target = field.target_field
                foreign_keys.append(ForeignKeyInfo(
                    f'{table}_{field.column}_fk', table, (field.column,),
                    target.model._meta.db_table, (target.column,),
                ))
        for index in model._meta.indexes:
            info.append(IndexInfo(
                index.name,
                tuple(model._meta.get_field(name.lstrip('-')).column for name in index.fields),
                False,
            ))
        for constraint in model._meta.constraints:
            if getattr(constraint, 'fields', None):
                info.append(IndexInfo(
                    constraint.name,
                    tuple(model._meta.get_field(name).column for name in constraint.fields),
                    True,
                ))
        indexes[table] = tuple(info)
    from api_testing.browser_discovery_migration_repair import SchemaSnapshot
    return SchemaSnapshot(frozenset(tables), columns, indexes, tuple(foreign_keys))


class RecordingEditor:
    def __init__(self):
        self.sql = []
        self.deferred_sql = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        self.sql.append(str(statement))

    def _create_fk_sql(self, model, field, suffix):
        return f'ALTER TABLE {model._meta.db_table} ADD CONSTRAINT {field.column}_fk FOREIGN KEY'

    def _field_indexes_sql(self, model, field):
        return [f'CREATE INDEX {field.column}_idx ON {model._meta.db_table}']

    def add_index(self, model, index):
        self.sql.append(f'CREATE INDEX {index.name} ON {model._meta.db_table}')

    def add_constraint(self, model, constraint):
        self.sql.append(f'ALTER TABLE {model._meta.db_table} ADD CONSTRAINT {constraint.name} UNIQUE')


class RecordingConnection:
    def __init__(self):
        self.editor = RecordingEditor()

    def schema_editor(self):
        return self.editor


class Query:
    def __init__(self, state):
        self.state = state

    def filter(self, **kwargs):
        return self

    def exists(self):
        return self.state['recorded']


class Recorder:
    def __init__(self, state):
        self.migration_qs = Query(state)
        self.state = state

    def record_applied(self, app, name):
        self.state['recorded'] = True


class Repair0019UnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = historical_0019_models(connection)

    def test_conflicting_column_is_rejected_before_a_plan(self):
        snapshot = complete_snapshot(self.models)
        record = self.models['BrowserDiscoveryRecord']
        table = record._meta.db_table
        columns = {name: dict(values) for name, values in snapshot.columns.items()}
        columns[table]['path'] = ColumnInfo('varchar(100)', 'varchar', 100, False)
        from api_testing.browser_discovery_migration_repair import SchemaSnapshot
        conflict = SchemaSnapshot(snapshot.tables, columns, snapshot.indexes, snapshot.foreign_keys)
        assessment = assess_0019_prefix(connection, self.models, conflict)
        self.assertEqual(assessment.state, 'invalid')
        self.assertFalse(assessment.actions)
        self.assertIn('path', '\n'.join(assessment.errors))

    def test_missing_deferred_fk_and_indexes_are_planned(self):
        snapshot = complete_snapshot(self.models)
        record = self.models['BrowserDiscoveryRecord']
        task = self.models['BrowserDiscoveryTask']
        record_table = record._meta.db_table
        task_table = task._meta.db_table
        indexes = {name: values for name, values in snapshot.indexes.items()}
        indexes[record_table] = tuple(item for item in indexes[record_table] if item.columns not in {('task_id',), ('is_eligible',)})
        indexes[task_table] = tuple(item for item in indexes[task_table] if item.columns not in {('status',)})
        foreign_keys = tuple(
            item for item in snapshot.foreign_keys
            if not (item.table == record_table and item.columns == ('task_id',))
        )
        from api_testing.browser_discovery_migration_repair import SchemaSnapshot
        partial = SchemaSnapshot(snapshot.tables, snapshot.columns, indexes, foreign_keys)
        assessment = assess_0019_prefix(connection, self.models, partial)
        self.assertEqual(assessment.state, 'repairable')
        planned = {(item.kind, item.model_label, item.fields) for item in assessment.actions}
        self.assertIn(('foreign_key', record._meta.label, ('task_id',)), planned)
        self.assertIn(('field_index', record._meta.label, ('is_eligible',)), planned)
        # The existing explicit (status, heartbeat_at) key covers the ordinary
        # status lookup; it must not cause a redundant field-index repair.
        self.assertNotIn(('field_index', task._meta.label, ('status',)), planned)

    def test_apply_action_set_has_no_drop_or_truncate_path(self):
        assessment = assess_0019_prefix(connection, self.models, complete_snapshot(self.models))
        self.assertTrue(assessment.complete)
        actions = (
            RepairAction('foreign_key', self.models['BrowserDiscoveryRecord']._meta.label, ('task_id',)),
            RepairAction('field_index', self.models['BrowserDiscoveryRecord']._meta.label, ('is_eligible',)),
            RepairAction('index', self.models['BrowserDiscoveryRecord']._meta.label, ('task_id', 'method'), 'api_browser_task_id_8c01c0_idx'),
            RepairAction('unique_constraint', self.models['BrowserDiscoveryRecord']._meta.label, ('task_id', 'sequence'), 'unique_browser_discovery_record_sequence'),
        )
        fake_connection = RecordingConnection()
        apply_actions(fake_connection, self.models, actions)
        emitted = '\n'.join(fake_connection.editor.sql).upper()
        self.assertNotIn('DROP ', emitted)
        self.assertNotIn('TRUNCATE ', emitted)
        self.assertIn('ADD CONSTRAINT', emitted)
        self.assertIn('CREATE INDEX', emitted)

    def test_later_migration_graph_uses_descendants_not_dependencies(self):
        graph = MigrationGraph()
        before = ('api_testing', '0018_apiworkspace_scenarios')
        target = ('api_testing', '0019_apispecification_source_selection_key_and_more')
        after = ('api_testing', '0020_after_browser_discovery')
        other_after = ('reports', '0001_depends_on_browser_discovery')
        graph.add_node(before, None)
        graph.add_node(target, None)
        graph.add_node(after, None)
        graph.add_node(other_after, None)
        graph.add_dependency(None, target, before)
        graph.add_dependency(None, after, target)
        graph.add_dependency(None, other_after, target)
        self.assertEqual(set(graph.forwards_plan(target)), {before, target})
        self.assertEqual(later_migration_nodes(graph), {after, other_after})

    def test_recorded_and_repeated_apply_are_idempotent(self):
        state = {'recorded': False}
        recorder = Recorder(state)
        pending = RepairAssessment(
            'repairable',
            (RepairAction('field_index', self.models['BrowserDiscoveryRecord']._meta.label, ('is_eligible',)),),
            (),
        )
        complete = RepairAssessment('complete', (), ())
        fake_connection = type('Connection', (), {'vendor': 'mysql'})()
        output = StringIO()
        command = command_module.Command(stdout=output)
        with patch.object(command_module, 'connections', {'default': fake_connection}), \
             patch.object(command_module, 'historical_0019_models', return_value=self.models), \
             patch.object(command_module, 'snapshot_schema', return_value=complete_snapshot(self.models)), \
             patch.object(command_module, 'assess_0019_prefix', side_effect=(pending, complete, complete)), \
             patch.object(command_module, 'registration_history_errors', return_value=()), \
             patch.object(command_module, 'validate_pending_data') as validate, \
             patch.object(command_module, 'apply_actions') as apply, \
             patch.object(command_module, 'MigrationRecorder', return_value=recorder):
            command.handle(apply=True)
            command.handle(apply=True)
        self.assertTrue(state['recorded'])
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(validate.call_count, 1)
        self.assertIn('好的马丁同学', output.getvalue())
        self.assertIn('无需处理', output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
