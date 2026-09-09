"""Narrow, additive recovery for the interrupted api_testing 0019 migration.

This is deliberately not a general migration-repair framework.  MariaDB/MySQL
commits DDL independently, so an exception while Django leaves a schema editor
can leave the non-deferred operations in place and omit every deferred SQL
statement.  The only supported shape is that exact 0019 prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from django.db.migrations.executor import MigrationExecutor


APP_LABEL = 'api_testing'
MIGRATION_NAME = '0019_apispecification_source_selection_key_and_more'
TABLE_MODELS = (
    'BrowserDiscoveryTask',
    'BrowserDiscoveryRecord',
    'BrowserDiscoveryHandoff',
)
SPEC_MODEL = 'APISpecification'
SPEC_FIELDS = ('source_selection_key', 'source_version', 'spec_type', 'source_task')
NEW_SPEC_FIELDS = ('source_selection_key', 'source_version', 'source_task')


class RepairSchemaError(RuntimeError):
    """The database is not the exact, safe-to-repair 0019 prefix."""


@dataclass(frozen=True)
class ColumnInfo:
    column_type: str
    data_type: str
    character_maximum_length: int | None
    nullable: bool


@dataclass(frozen=True)
class IndexInfo:
    name: str
    columns: tuple[str, ...]
    unique: bool
    primary: bool = False
    has_prefix: bool = False


@dataclass(frozen=True)
class ForeignKeyInfo:
    name: str
    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: frozenset[str]
    columns: dict[str, dict[str, ColumnInfo]]
    indexes: dict[str, tuple[IndexInfo, ...]]
    foreign_keys: tuple[ForeignKeyInfo, ...]


@dataclass(frozen=True)
class RepairAction:
    kind: str
    model_label: str
    fields: tuple[str, ...]
    name: str | None = None

    def describe(self) -> str:
        title = {
            'foreign_key': '外键',
            'field_index': '普通字段索引',
            'index': '显式索引',
            'unique_constraint': '唯一约束',
        }[self.kind]
        suffix = f'（{self.name}）' if self.name else ''
        return f'{title} {self.model_label}({", ".join(self.fields)}){suffix}'


@dataclass(frozen=True)
class RepairAssessment:
    state: str
    actions: tuple[RepairAction, ...]
    errors: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.state == 'complete' and not self.actions and not self.errors


def historical_0019_models(connection):
    """Return models from 0019's state, never from the current app registry."""
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state([(APP_LABEL, MIGRATION_NAME)])
    apps = state.apps
    return {name: apps.get_model(APP_LABEL, name) for name in (*TABLE_MODELS, SPEC_MODEL)}


def registration_history_errors(connection, recorder) -> tuple[str, ...]:
    """Require the immediate dependency and reject a non-linear history write."""
    applied = set(recorder.applied_migrations())
    dependency = (APP_LABEL, '0018_apiworkspace_scenarios')
    if dependency not in applied:
        return ('0018_apiworkspace_scenarios 未登记，不能仅因表存在就登记 0019。',)
    executor = MigrationExecutor(connection)
    later_nodes = later_migration_nodes(executor.loader.graph)
    later_applied = sorted(node for node in later_nodes if node in applied)
    if later_applied:
        rendered = ', '.join(f'{app}.{name}' for app, name in later_applied)
        return (f'检测到 0019 的后续迁移已登记（{rendered}），拒绝倒序登记 0019。',)
    return ()


def later_migration_nodes(graph) -> set[tuple[str, str]]:
    """Return descendants, not the dependency chain needed to reach 0019."""
    target = (APP_LABEL, MIGRATION_NAME)
    # Django's backwards plan is the set that must be unapplied to unapply the
    # target, i.e. the target and every migration that depends on it.
    nodes = set(graph.backwards_plan(target))
    nodes.discard(target)
    return nodes


def snapshot_schema(connection, models: dict[str, object]) -> SchemaSnapshot:
    """Read only the small set of tables used by 0019 from information_schema."""
    table_names = [model._meta.db_table for model in models.values()]
    table_set = set(connection.introspection.table_names())
    selected_tables = [name for name in table_names if name in table_set]
    columns: dict[str, dict[str, ColumnInfo]] = {name: {} for name in selected_tables}
    indexes: dict[str, tuple[IndexInfo, ...]] = {name: () for name in selected_tables}
    foreign_keys: list[ForeignKeyInfo] = []
    if not selected_tables:
        return SchemaSnapshot(frozenset(table_set), columns, indexes, tuple(foreign_keys))

    placeholders = ', '.join(['%s'] * len(selected_tables))
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, '
            'CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE '
            'FROM information_schema.COLUMNS '
            'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (' + placeholders + ')',
            selected_tables,
        )
        for table, name, column_type, data_type, max_length, nullable in cursor.fetchall():
            columns[table][name] = ColumnInfo(
                column_type=column_type,
                data_type=data_type,
                character_maximum_length=max_length,
                nullable=nullable == 'YES',
            )

        cursor.execute(
            'SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART '
            'FROM information_schema.STATISTICS '
            'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (' + placeholders + ') '
            'ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX',
            selected_tables,
        )
        grouped_indexes: dict[tuple[str, str, bool], tuple[list[str], bool]] = {}
        for table, name, non_unique, _position, column, sub_part in cursor.fetchall():
            fields, has_prefix = grouped_indexes.setdefault((table, name, not bool(non_unique)), ([], False))
            fields.append(column)
            grouped_indexes[(table, name, not bool(non_unique))] = (fields, has_prefix or sub_part is not None)
        for table in selected_tables:
            indexes[table] = tuple(
                IndexInfo(name, tuple(fields), unique, primary=name == 'PRIMARY', has_prefix=has_prefix)
                for (item_table, name, unique), (fields, has_prefix) in grouped_indexes.items()
                if item_table == table
            )

        cursor.execute(
            'SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, '
            'REFERENCED_COLUMN_NAME, ORDINAL_POSITION, REFERENCED_TABLE_SCHEMA = DATABASE() '
            'FROM information_schema.KEY_COLUMN_USAGE '
            'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (' + placeholders + ') '
            'AND REFERENCED_TABLE_NAME IS NOT NULL '
            'ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION',
            selected_tables,
        )
        grouped_fks: dict[tuple[str, str, str], tuple[list[str], list[str]]] = {}
        for table, name, column, ref_table, ref_column, _position, same_database in cursor.fetchall():
            # A same-named table in another database isn't this migration's
            # target. Keep it distinguishable so the conflict check refuses it.
            if not same_database:
                ref_table = f'[other database].{ref_table}'
            fields, ref_fields = grouped_fks.setdefault((table, name, ref_table), ([], []))
            fields.append(column)
            ref_fields.append(ref_column)
        foreign_keys = [
            ForeignKeyInfo(name, table, tuple(fields), ref_table, tuple(ref_fields))
            for (table, name, ref_table), (fields, ref_fields) in grouped_fks.items()
        ]
    return SchemaSnapshot(frozenset(table_set), columns, indexes, tuple(foreign_keys))


def assess_0019_prefix(connection, models: dict[str, object], snapshot: SchemaSnapshot) -> RepairAssessment:
    """Classify the schema and list only additive, known 0019 deferred work."""
    required_tables = {models[name]._meta.db_table for name in TABLE_MODELS}
    spec_model = models[SPEC_MODEL]
    spec_table = spec_model._meta.db_table
    present_tables = required_tables.intersection(snapshot.tables)
    present_new_fields = {
        name for name in NEW_SPEC_FIELDS
        if spec_model._meta.get_field(name).column in snapshot.columns.get(spec_table, {})
    }
    if not present_tables and not present_new_fields:
        return RepairAssessment('fresh', (), ())
    errors: list[str] = []
    if present_tables != required_tables:
        errors.append('三张 Browser Discovery 新表未同时存在，拒绝在未知迁移阶段续接。')
    if present_new_fields != set(NEW_SPEC_FIELDS):
        errors.append('api_specifications 的 0019 新增字段不完整，拒绝在未知迁移阶段续接。')
    if errors:
        return RepairAssessment('invalid', (), tuple(errors))

    for name in TABLE_MODELS:
        model = models[name]
        table = model._meta.db_table
        expected = {field.column for field in model._meta.local_fields}
        actual = set(snapshot.columns.get(table, {}))
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            details = []
            if missing:
                details.append('缺少 ' + ', '.join(missing))
            if unexpected:
                details.append('额外 ' + ', '.join(unexpected))
            errors.append(f'{table} 的列集合与 0019 历史状态不一致（{"；".join(details)}）。')
        errors.extend(_field_errors(connection, model, model._meta.local_fields, snapshot))
        for field in model._meta.local_fields:
            if field.primary_key and not _has_primary_key(snapshot, model, (field.column,)):
                errors.append(f'{table}.{field.column} 缺少 0019 创建表时应有的主键。')
            elif field.unique and not _has_index(snapshot, model, (field.column,), unique=True):
                errors.append(f'{table}.{field.column} 缺少 0019 创建表时应有的唯一索引。')
    errors.extend(_field_errors(
        connection, spec_model,
        [spec_model._meta.get_field(name) for name in SPEC_FIELDS],
        snapshot,
    ))
    if errors:
        return RepairAssessment('invalid', (), tuple(errors))

    # source_task is created by AddField, not a CreateModel deferred FK. Its
    # absence means the prefix itself is not the observed failure shape.
    source_task = spec_model._meta.get_field('source_task')
    if _has_conflicting_foreign_key(snapshot, spec_model, source_task):
        return RepairAssessment(
            'invalid', (), ('api_specifications.source_task_id 外键指向与 0019 历史状态不一致。',),
        )
    if not _has_foreign_key(snapshot, spec_model, source_task):
        return RepairAssessment(
            'invalid', (), ('api_specifications.source_task_id 外键缺失，不是可安全续接的 0019 前缀。',),
        )

    actions: list[RepairAction] = []
    with connection.schema_editor() as editor:
        for name in TABLE_MODELS:
            model = models[name]
            for field in model._meta.local_fields:
                if not (field.remote_field and field.db_constraint):
                    continue
                if _has_conflicting_foreign_key(snapshot, model, field):
                    errors.append(f'{model._meta.db_table}.{field.column} 外键指向与 0019 历史状态不一致。')
                elif not _has_foreign_key(snapshot, model, field):
                    actions.append(RepairAction('foreign_key', model._meta.label, (field.column,)))
            for field in model._meta.local_fields:
                # MySQL deliberately suppresses a separate Django field index
                # for an InnoDB FK: the FK itself supplies it.  Ask Django's
                # schema editor rather than duplicating that backend rule.
                if editor._field_should_be_indexed(model, field) and not _has_index(
                    snapshot, model, (field.column,), unique=False,
                ):
                    actions.append(RepairAction('field_index', model._meta.label, (field.column,)))
            for index in model._meta.indexes:
                fields = tuple(model._meta.get_field(field_name.lstrip('-')).column for field_name in index.fields)
                named = _find_named_index(snapshot, model, index.name)
                if named and (named.columns != fields or named.unique or named.primary or named.has_prefix):
                    errors.append(f'{model._meta.db_table} 的索引 {index.name} 列或唯一性与 0019 不一致。')
                elif not named:
                    actions.append(RepairAction('index', model._meta.label, fields, index.name))
            for constraint in model._meta.constraints:
                if not getattr(constraint, 'fields', None):
                    continue
                fields = tuple(model._meta.get_field(field_name).column for field_name in constraint.fields)
                named = _find_named_index(snapshot, model, constraint.name)
                if named and (named.columns != fields or not named.unique or named.primary or named.has_prefix):
                    errors.append(f'{model._meta.db_table} 的唯一约束 {constraint.name} 列或唯一性与 0019 不一致。')
                elif not named:
                    actions.append(RepairAction('unique_constraint', model._meta.label, fields, constraint.name))
    if errors:
        return RepairAssessment('invalid', (), tuple(errors))
    return RepairAssessment('complete' if not actions else 'repairable', tuple(actions), ())


def validate_pending_data(connection, models: dict[str, object], snapshot: SchemaSnapshot, actions: Iterable[RepairAction]):
    """Reject data that would make an additive FK/unique DDL fail mid-repair."""
    by_label = {model._meta.label: model for model in models.values()}
    for action in actions:
        model = by_label[action.model_label]
        if action.kind == 'unique_constraint' and _has_duplicate_key(connection, model, action.fields):
            raise RepairSchemaError(f'{model._meta.db_table} 存在重复键 {action.fields}，不能创建唯一约束。')
        if action.kind == 'foreign_key':
            field = model._meta.get_field(action.fields[0])
            if _has_orphaned_foreign_key(connection, model, field):
                raise RepairSchemaError(f'{model._meta.db_table}.{field.column} 存在孤儿引用，不能创建外键。')


def apply_actions(connection, models: dict[str, object], actions: Iterable[RepairAction]):
    """Execute only Django-generated ADD INDEX/CONSTRAINT statements."""
    by_label = {model._meta.label: model for model in models.values()}
    with connection.schema_editor() as editor:
        for action in actions:
            model = by_label[action.model_label]
            if action.kind == 'foreign_key':
                field = model._meta.get_field(action.fields[0])
                editor.execute(editor._create_fk_sql(model, field, '_fk_%(to_table)s_%(to_column)s'))
            elif action.kind == 'field_index':
                field = model._meta.get_field(action.fields[0])
                for statement in editor._field_indexes_sql(model, field):
                    editor.execute(statement)
            elif action.kind == 'index':
                index = next(index for index in model._meta.indexes if index.name == action.name)
                editor.add_index(model, index)
            elif action.kind == 'unique_constraint':
                constraint = next(item for item in model._meta.constraints if item.name == action.name)
                editor.add_constraint(model, constraint)
            else:  # Defensive: this helper must remain a closed, additive set.
                raise RepairSchemaError(f'不支持的修复动作：{action.kind}')


def _field_errors(connection, model, fields, snapshot: SchemaSnapshot) -> list[str]:
    table = model._meta.db_table
    actual = snapshot.columns.get(table, {})
    errors = []
    for field in fields:
        observed = actual.get(field.column)
        if observed is None:
            errors.append(f'{table}.{field.column} 缺失。')
            continue
        if not _column_type_matches(field.db_type(connection), observed):
            errors.append(
                f'{table}.{field.column} 类型不匹配：实际 {observed.column_type}，'
                f'0019 期望 {field.db_type(connection)}。'
            )
        if field.max_length is not None and observed.character_maximum_length != field.max_length:
            errors.append(
                f'{table}.{field.column} 长度不匹配：实际 {observed.character_maximum_length}，'
                f'0019 期望 {field.max_length}。'
            )
        if observed.nullable != field.null:
            errors.append(
                f'{table}.{field.column} NULL 属性不匹配：实际 {observed.nullable}，'
                f'0019 期望 {field.null}。'
            )
    return errors


def _column_type_matches(expected: str | None, observed: ColumnInfo) -> bool:
    if expected is None:
        return True
    expected = re.sub(r'\s+', ' ', expected.lower()).replace(' auto_increment', '')
    actual = re.sub(r'\s+', ' ', observed.column_type.lower()).replace(' auto_increment', '')
    expected_base = expected.split('(', 1)[0].split(' ', 1)[0]
    actual_base = actual.split('(', 1)[0].split(' ', 1)[0]
    aliases = {
        'bool': {'bool', 'boolean', 'tinyint'},
        # MariaDB exposes JSON as LONGTEXT although Django emits JSON.
        'json': {'json', 'longtext'},
        'integer': {'integer', 'int'},
    }
    allowed = aliases.get(expected_base, {expected_base})
    if actual_base not in allowed:
        return False
    if ('unsigned' in expected) != ('unsigned' in actual):
        return False
    if expected_base in {'char', 'varchar', 'datetime'}:
        expected_precision = re.search(r'\((\d+)\)', expected)
        actual_precision = re.search(r'\((\d+)\)', actual)
        if (expected_precision.group(1) if expected_precision else None) != (
            actual_precision.group(1) if actual_precision else None
        ):
            return False
    return True


def _find_named_index(snapshot: SchemaSnapshot, model, name: str) -> IndexInfo | None:
    return next((item for item in snapshot.indexes.get(model._meta.db_table, ()) if item.name == name), None)


def _has_index(snapshot: SchemaSnapshot, model, columns: tuple[str, ...], unique: bool) -> bool:
    candidates = snapshot.indexes.get(model._meta.db_table, ())
    if unique:
        return any(
            item.columns == columns and item.unique and not item.has_prefix
            for item in candidates
        )
    # A non-unique single-column Django index is already served by a normal
    # index whose leading columns exactly start with that field. Prefix indexes
    # never qualify because they don't preserve complete-column semantics.
    return any(
        item.columns[:len(columns)] == columns and not item.has_prefix
        for item in candidates
    )


def _has_primary_key(snapshot: SchemaSnapshot, model, columns: tuple[str, ...]) -> bool:
    return any(
        item.primary and item.columns == columns and not item.has_prefix
        for item in snapshot.indexes.get(model._meta.db_table, ())
    )


def _has_foreign_key(snapshot: SchemaSnapshot, model, field) -> bool:
    target = field.target_field
    return any(
        item.table == model._meta.db_table
        and item.columns == (field.column,)
        and item.referenced_table == target.model._meta.db_table
        and item.referenced_columns == (target.column,)
        for item in snapshot.foreign_keys
    )


def _has_conflicting_foreign_key(snapshot: SchemaSnapshot, model, field) -> bool:
    target = field.target_field
    same_column = [
        item for item in snapshot.foreign_keys
        if item.table == model._meta.db_table and item.columns == (field.column,)
    ]
    return any(
        item.referenced_table != target.model._meta.db_table
        or item.referenced_columns != (target.column,)
        for item in same_column
    )


def _has_duplicate_key(connection, model, columns: tuple[str, ...]) -> bool:
    quote = connection.ops.quote_name
    sql = (
        f'SELECT 1 FROM {quote(model._meta.db_table)} '
        f'GROUP BY {", ".join(quote(column) for column in columns)} HAVING COUNT(*) > 1 LIMIT 1'
    )
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone() is not None


def _has_orphaned_foreign_key(connection, model, field) -> bool:
    quote = connection.ops.quote_name
    target = field.target_field
    sql = (
        f'SELECT 1 FROM {quote(model._meta.db_table)} child '
        f'LEFT JOIN {quote(target.model._meta.db_table)} parent '
        f'ON child.{quote(field.column)} = parent.{quote(target.column)} '
        f'WHERE child.{quote(field.column)} IS NOT NULL '
        f'AND parent.{quote(target.column)} IS NULL LIMIT 1'
    )
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone() is not None
