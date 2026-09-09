"""Safely finish the deferred DDL skipped by the failed api_testing 0019."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder

from api_testing.browser_discovery_migration_repair import (
    APP_LABEL,
    MIGRATION_NAME,
    RepairSchemaError,
    apply_actions,
    assess_0019_prefix,
    historical_0019_models,
    registration_history_errors,
    snapshot_schema,
    validate_pending_data,
)


class Command(BaseCommand):
    help = '只读检查或安全续接 api_testing 0019 的 Browser Discovery 延迟 DDL。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='确认前缀完整且仅缺少已知延迟 DDL 后，执行补齐并登记 0019。',
        )

    def handle(self, *args, **options):
        connection = connections['default']
        if connection.vendor != 'mysql':
            raise CommandError('好的马丁同学：此命令仅支持 MySQL/MariaDB，当前数据库不会被修改。')

        models = historical_0019_models(connection)
        snapshot = snapshot_schema(connection, models)
        assessment = assess_0019_prefix(connection, models, snapshot)
        recorder = MigrationRecorder(connection)
        recorded = recorder.migration_qs.filter(app=APP_LABEL, name=MIGRATION_NAME).exists()

        if recorded:
            if assessment.complete:
                self.stdout.write(self.style.SUCCESS('好的马丁同学：0019 已登记且结构完整，无需处理。'))
                return
            raise CommandError(
                '好的马丁同学：0019 已登记但结构不完整或不匹配；为避免掩盖漂移，本命令不会修改数据库。\n'
                + _details(assessment)
            )

        history_errors = registration_history_errors(connection, recorder)
        if history_errors:
            raise CommandError(
                '好的马丁同学：迁移历史不允许登记 0019，本命令不会修改数据库。\n'
                + '\n'.join(f'- {message}' for message in history_errors)
            )

        if assessment.state == 'fresh':
            self.stdout.write('好的马丁同学：未发现 0019 的新表和新增字段，请使用正常 migrate，不适用本续接命令。')
            return
        if assessment.state == 'invalid':
            raise CommandError(
                '好的马丁同学：数据库不是可安全续接的 0019 前缀，本命令不会修改数据库。\n'
                + _details(assessment)
            )

        self.stdout.write('好的马丁同学：已确认 0019 未登记且前缀结构匹配。')
        if assessment.actions:
            self.stdout.write('待补齐的仅追加 DDL：')
            for action in assessment.actions:
                self.stdout.write(f'- {action.describe()}')
        else:
            self.stdout.write('没有缺失 DDL；仅需复核后登记 0019。')

        # This data read happens in both modes so --apply cannot surprise the
        # operator with a late unique/FK failure after partial DDL.
        try:
            validate_pending_data(connection, models, snapshot, assessment.actions)
        except RepairSchemaError as error:
            raise CommandError(f'好的马丁同学：安全校验未通过，未执行任何 DDL。{error}') from error

        if not options['apply']:
            self.stdout.write('好的马丁同学：以上为只读检查和计划；未执行 DDL，传入 --apply 才会续接。')
            return

        try:
            apply_actions(connection, models, assessment.actions)
        except Exception as error:
            # MySQL/MariaDB DDL is not transactional. Do not hide an error or
            # write migration history; a later dry run can safely show what is
            # still missing.
            raise CommandError(f'好的马丁同学：补齐 DDL 失败，未登记 0019。原始错误：{error}') from error

        final_snapshot = snapshot_schema(connection, models)
        final_assessment = assess_0019_prefix(connection, models, final_snapshot)
        if not final_assessment.complete:
            raise CommandError(
                '好的马丁同学：DDL 后结构仍未完整，未登记 0019。\n' + _details(final_assessment)
            )
        recorder.record_applied(APP_LABEL, MIGRATION_NAME)
        self.stdout.write(self.style.SUCCESS('好的马丁同学：已补齐 0019 延迟 DDL、复核完整性并登记迁移。'))


def _details(assessment) -> str:
    messages = list(assessment.errors)
    messages.extend(action.describe() for action in assessment.actions)
    return '\n'.join(f'- {message}' for message in messages) or '- 未提供可续接的结构信息。'
