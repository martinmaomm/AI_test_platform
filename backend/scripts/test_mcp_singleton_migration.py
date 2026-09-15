"""Verify singleton migration and database constraints in disposable SQLite only."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    from config import settings as config
    with tempfile.TemporaryDirectory(prefix='mcp-singleton-migration-') as root:
        config.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(root) / 'migration.sqlite3'),
        }}
        config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
        import django
        django.setup()
        from django.db import connection, IntegrityError, transaction
        from django.db.migrations.executor import MigrationExecutor

        previous = [('ai_core', '0003_mcpconfiguration_tool_discovery_status')]
        for count in (0, 1, 2):
            connection.close()
            connection.settings_dict['NAME'] = str(Path(root) / f'count-{count}.sqlite3')
            executor = MigrationExecutor(connection)
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            User = apps.get_model('users', 'User')
            MCP = apps.get_model('ai_core', 'MCPConfiguration')
            Tool = apps.get_model('ai_core', 'MCPTool')
            owner = User.objects.create(username='migration-owner', email='migration@example.test')
            raw = '{"mcpServers":{"playwright":{"command":"offline"}}}'
            for n in range(count):
                entry = MCP.objects.create(name=f'保留配置 {n}', raw_config=raw, created_by=owner, is_active=False)
                Tool.objects.create(mcp_config=entry, name='fixture-tool')
            original_fields = [field.name for field in MCP._meta.fields]

            def fingerprint(model, tool_model):
                values = {'configs': list(model.objects.order_by('id').values(*original_fields)),
                          'tools': list(tool_model.objects.order_by('id').values())}
                return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

            before = fingerprint(MCP, Tool)
            executor = MigrationExecutor(connection)
            target = executor.loader.graph.leaf_nodes('ai_core')
            assert len(target) == 1 and target != previous, 'Singleton migration not found'
            if count > 1:
                try:
                    executor.migrate(target)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Migration must refuse multiple configurations without deleting them')
                assert fingerprint(MCP, Tool) == before
                columns = {column.name for column in connection.introspection.get_table_description(
                    connection.cursor(), MCP._meta.db_table,
                )}
                assert 'singleton_key' not in columns, 'Guard must run before schema changes'
                print('PASS: multiple existing configurations refused without mutation')
                continue
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            NewMCP = apps.get_model('ai_core', 'MCPConfiguration')
            NewTool = apps.get_model('ai_core', 'MCPTool')
            assert fingerprint(NewMCP, NewTool) == before
            if count == 0:
                NewMCP.objects.create(name='首次配置', raw_config=raw, created_by_id=owner.pk)
            assert NewMCP.objects.get().singleton_key == 1
            for slot in (1, 2):
                try:
                    with transaction.atomic():
                        NewMCP.objects.create(name='不能追加', raw_config=raw,
                                              created_by_id=owner.pk, singleton_key=slot)
                except IntegrityError:
                    pass
                else:
                    raise AssertionError(f'Database must reject duplicate/forged singleton slot {slot}')
            assert NewMCP.objects.count() == 1
            print(f'PASS: {count} existing configuration(s) preserved; unique/check constraints reject extra rows')
        connection.close()


if __name__ == '__main__':
    main()
