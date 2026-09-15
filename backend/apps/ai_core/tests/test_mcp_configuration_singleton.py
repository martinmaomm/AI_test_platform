import importlib
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ai_core.models import MCPConfiguration, MCPTool


def playwright_config(command='offline-playwright'):
    return json.dumps({'mcpServers': {'playwright': {'command': command}}})


class MCPConfigurationSingletonAPITests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(
            'singleton-admin', email='singleton-admin@example.test', is_staff=True,
        )
        self.member = users.objects.create_user(
            'singleton-member', email='singleton-member@example.test',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def create_singleton(self, *, is_active=True):
        return MCPConfiguration.objects.create(
            raw_config=playwright_config(),
            is_active=is_active,
            created_by=self.admin,
        )

    def test_empty_database_lists_empty_and_allows_one_server_managed_create(self):
        empty = self.client.get('/api/v1/ai-core/mcp-configs/?status=active')
        self.assertEqual(empty.status_code, 200, empty.data)
        self.assertEqual(empty.data['data'], [])

        created = self.client.post(
            '/api/v1/ai-core/mcp-configs/',
            {
                'id': 99999,
                'singleton_key': 2,
                'is_active': False,
                'tools_status': 'ready',
                'rawConfig': playwright_config(),
            },
            format='json',
        )

        self.assertEqual(created.status_code, 200, created.data)
        config = MCPConfiguration.objects.get()
        self.assertNotEqual(config.id, 99999)
        self.assertEqual(config.singleton_key, 1)
        self.assertTrue(config.is_active)
        self.assertEqual(config.tools_status, 'unchecked')
        self.assertEqual(created.data['data']['id'], config.id)

    def test_existing_disabled_singleton_still_rejects_another_create(self):
        existing = self.create_singleton(is_active=False)

        duplicate = self.client.post(
            '/api/v1/ai-core/mcp-configs/',
            {'rawConfig': playwright_config('another-command')},
            format='json',
        )

        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertIn('已存在', duplicate.data['message'])
        self.assertEqual(MCPConfiguration.objects.get().id, existing.id)

    def test_concurrent_insert_integrity_error_is_reported_as_conflict(self):
        with (
            patch.object(MCPConfiguration.objects, 'exists', return_value=False),
            patch.object(
                MCPConfiguration.objects,
                'create',
                side_effect=IntegrityError('simulated singleton race'),
            ),
        ):
            result = self.client.post(
                '/api/v1/ai-core/mcp-configs/',
                {'rawConfig': playwright_config()},
                format='json',
            )

        self.assertEqual(result.status_code, 409, result.data)
        self.assertIn('已存在', result.data['message'])

    def test_delete_is_rejected_and_member_cannot_bypass_permission(self):
        config = self.create_singleton()

        rejected = self.client.delete(f'/api/v1/ai-core/mcp-configs/{config.id}/')
        self.assertEqual(rejected.status_code, 405, rejected.data)
        self.assertIn('编辑或禁用', rejected.data['message'])
        self.assertTrue(MCPConfiguration.objects.filter(id=config.id).exists())

        self.client.force_authenticate(self.member)
        denied = self.client.delete(f'/api/v1/ai-core/mcp-configs/{config.id}/')
        self.assertEqual(denied.status_code, 403, denied.data)

    def test_only_one_named_playwright_server_with_string_command_is_accepted(self):
        invalid_configs = (
            {'mcpServers': {'filesystem': {'command': 'offline'}}},
            {'mcpServers': {
                'playwright': {'command': 'offline'},
                'filesystem': {'command': 'offline'},
            }},
            {'mcpServers': {'playwright': {'command': ''}}},
            {'mcpServers': {'playwright': {'command': 123}}},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                result = self.client.post(
                    '/api/v1/ai-core/mcp-configs/',
                    {'rawConfig': json.dumps(config)},
                    format='json',
                )
                self.assertEqual(result.status_code, 400, result.data)
                self.assertEqual(MCPConfiguration.objects.count(), 0)

    def test_edit_rejects_non_playwright_config_without_changing_existing_row(self):
        config = self.create_singleton()
        original = config.raw_config

        result = self.client.put(
            f'/api/v1/ai-core/mcp-configs/{config.id}/',
            {'rawConfig': json.dumps({'mcpServers': {'filesystem': {'command': 'offline'}}})},
            format='json',
        )

        self.assertEqual(result.status_code, 400, result.data)
        config.refresh_from_db()
        self.assertEqual(config.raw_config, original)

    def test_equivalent_edit_preserves_catalog_and_ignores_slot_and_id_fields(self):
        config = self.create_singleton()
        checked_at = timezone.now()
        config.tools_status = 'ready'
        config.tools_checked_at = checked_at
        config.save(update_fields=['tools_status', 'tools_checked_at'])
        MCPTool.objects.create(mcp_config=config, name='known_tool')
        equivalent = json.dumps(json.loads(config.raw_config), indent=2)

        result = self.client.put(
            f'/api/v1/ai-core/mcp-configs/{config.id}/',
            {
                'id': config.id + 100,
                'singleton_key': 2,
                'rawConfig': equivalent,
            },
            format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        config.refresh_from_db()
        self.assertEqual(config.singleton_key, 1)
        self.assertEqual(config.tools_status, 'ready')
        self.assertEqual(config.tools_checked_at, checked_at)
        self.assertEqual(list(config.tools.values_list('name', flat=True)), ['known_tool'])

    def test_member_cannot_create_the_empty_global_slot(self):
        self.client.force_authenticate(self.member)

        result = self.client.post(
            '/api/v1/ai-core/mcp-configs/',
            {'rawConfig': playwright_config()},
            format='json',
        )

        self.assertEqual(result.status_code, 403, result.data)
        self.assertEqual(MCPConfiguration.objects.count(), 0)


class MCPConfigurationSingletonConstraintTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            'constraint-admin', email='constraint-admin@example.test', is_staff=True,
        )

    def test_unique_slot_rejects_a_second_row(self):
        MCPConfiguration.objects.create(
            raw_config=playwright_config(), created_by=self.admin,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            MCPConfiguration.objects.create(
                raw_config=playwright_config('second'), created_by=self.admin,
            )

        self.assertEqual(MCPConfiguration.objects.count(), 1)

    def test_check_constraint_rejects_a_noncanonical_slot(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MCPConfiguration.objects.create(
                singleton_key=2,
                raw_config=playwright_config(),
                created_by=self.admin,
            )

        self.assertEqual(MCPConfiguration.objects.count(), 0)


class MCPConfigurationSingletonMigrationTests(SimpleTestCase):
    def test_preflight_refuses_multiple_rows_without_mutating_them(self):
        migration = importlib.import_module(
            'ai_core.migrations.0004_mcpconfiguration_singleton'
        )

        class Manager:
            def __init__(self, count):
                self._count = count

            def count(self):
                return self._count

        class Apps:
            def __init__(self, count):
                self.model = type('HistoricalMCPConfiguration', (), {'objects': Manager(count)})

            def get_model(self, app_label, model_name):
                self.requested = (app_label, model_name)
                return self.model

        migration.ensure_at_most_one_mcp_configuration(Apps(1), None)
        with self.assertRaisesRegex(RuntimeError, '不会自动删除数据'):
            migration.ensure_at_most_one_mcp_configuration(Apps(2), None)
