import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ai_core.mcp_tool_discovery import MCPToolDiscoveryError, discover_mcp_tools
from ai_core.models import MCPConfiguration, MCPTool


def raw_config(command='offline-mcp'):
    return json.dumps({'mcpServers': {'fixture': {'command': command}}})


def discovered_tool(name, description='tool description'):
    return {
        'name': name,
        'description': description,
        'tool_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
        'server_name': 'fixture',
    }


class MCPConfigurationToolsAPITests(TestCase):
    required_configuration_fields = {
        'id', 'name', 'rawConfig', 'is_active', 'tools_count', 'tools_status',
        'tools_checked_at', 'tools_error', 'tools',
    }

    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(
            'mcp-admin', email='mcp-admin@example.test', is_staff=True,
        )
        self.member = users.objects.create_user(
            'mcp-member', email='mcp-member@example.test',
        )
        self.config = MCPConfiguration.objects.create(
            raw_config=raw_config(), created_by=self.admin, is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def assert_contract(self, data):
        self.assertTrue(self.required_configuration_fields.issubset(data), data)

    @patch('ai_core.views.discover_mcp_tools')
    def test_create_is_active_but_unchecked_without_discovery(self, discover):
        result = self.client.post(
            '/api/v1/ai-core/mcp-configs/',
            {'rawConfig': raw_config('new-offline-mcp')},
            format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        data = result.data['data']
        self.assert_contract(data)
        self.assertTrue(data['is_active'])
        self.assertEqual(data['tools_status'], 'unchecked')
        self.assertEqual(data['tools_count'], 0)
        self.assertIsNone(data['tools_checked_at'])
        self.assertEqual(data['tools_error'], '')
        discover.assert_not_called()

    def test_list_and_detail_return_status_and_tool_fields(self):
        MCPTool.objects.create(
            mcp_config=self.config,
            name='existing',
            description='existing tool',
            tool_schema={'type': 'object'},
        )

        listed = self.client.get('/api/v1/ai-core/mcp-configs/')
        detail = self.client.get(f'/api/v1/ai-core/mcp-configs/{self.config.id}/')

        self.assertEqual(listed.status_code, 200, listed.data)
        list_item = next(item for item in listed.data['data'] if item['id'] == self.config.id)
        self.assert_contract(list_item)
        self.assertEqual(list_item['tools_count'], 1)
        self.assertEqual([tool['name'] for tool in list_item['tools']], ['existing'])
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assert_contract(detail.data['data'])

    @patch('ai_core.views.discover_mcp_tools')
    def test_toggle_only_changes_target_row_and_never_discovers(self, discover):
        other = MCPConfiguration.objects.create(
            raw_config=raw_config('other-mcp'), created_by=self.admin, is_active=False,
        )

        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/toggle_active/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assert_contract(result.data['data'])
        self.assertFalse(result.data['data']['is_active'])
        self.config.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(self.config.is_active)
        self.assertFalse(other.is_active)
        discover.assert_not_called()

    @patch('ai_core.views.discover_mcp_tools', return_value=[])
    def test_successful_zero_tool_refresh_is_ready_and_clears_old_manifest(self, discover):
        MCPTool.objects.create(mcp_config=self.config, name='stale')

        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        self.assert_contract(result.data['data'])
        self.assertEqual(result.data['data']['tools_status'], 'ready')
        self.assertTrue(result.data['data']['tools_refresh_applied'])
        self.assertEqual(result.data['data']['tools_count'], 0)
        self.assertEqual(result.data['data']['tools'], [])
        self.assertIsNotNone(result.data['data']['tools_checked_at'])
        self.assertFalse(self.config.tools.exists())
        discover.assert_called_once_with(self.config.raw_config)

    @patch('ai_core.views.discover_mcp_tools')
    def test_failed_refresh_keeps_last_manifest_but_marks_error(self, discover):
        MCPTool.objects.create(mcp_config=self.config, name='last_good')
        self.config.tools_status = MCPConfiguration.ToolsStatus.READY
        self.config.tools_checked_at = timezone.now()
        self.config.save(update_fields=['tools_status', 'tools_checked_at'])
        discover.side_effect = MCPToolDiscoveryError(
            'timeout', 'MCP工具检测超时（20秒），请检查服务启动和网络连接。',
            cause_type='TimeoutError',
        )

        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        data = result.data['data']
        self.assertEqual(data['tools_status'], 'error')
        self.assertTrue(data['tools_refresh_applied'])
        self.assertEqual(data['tools_count'], 1)
        self.assertEqual([tool['name'] for tool in data['tools']], ['last_good'])
        self.assertIn('超时', data['tools_error'])
        self.assertIsNotNone(data['tools_checked_at'])

    @patch('ai_core.views.discover_mcp_tools')
    def test_oversized_tool_name_is_saved_as_error_before_manifest_write(self, discover):
        MCPTool.objects.create(mcp_config=self.config, name='last_good')
        discover.return_value = [discovered_tool('x' * 101)]

        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['tools_status'], 'error')
        self.assertIn('超过100', result.data['data']['tools_error'])
        self.assertEqual(
            list(self.config.tools.values_list('name', flat=True)), ['last_good'],
        )

    @patch('ai_core.views.discover_mcp_tools')
    def test_successful_refresh_replaces_entire_manifest(self, discover):
        MCPTool.objects.create(mcp_config=self.config, name='removed')
        discover.return_value = [discovered_tool('new_a'), discovered_tool('new_b')]

        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['tools_status'], 'ready')
        self.assertEqual(result.data['data']['tools_count'], 2)
        self.assertEqual(
            set(self.config.tools.values_list('name', flat=True)), {'new_a', 'new_b'},
        )

    def test_changed_raw_config_invalidates_manifest_but_equivalent_json_does_not(self):
        MCPTool.objects.create(mcp_config=self.config, name='known')
        self.config.tools_status = MCPConfiguration.ToolsStatus.ERROR
        self.config.tools_checked_at = timezone.now()
        self.config.tools_error = 'old safe error'
        self.config.save(update_fields=['tools_status', 'tools_checked_at', 'tools_error'])
        equivalent = json.dumps(json.loads(self.config.raw_config), indent=2)

        same = self.client.put(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/',
            {'rawConfig': equivalent}, format='json',
        )
        self.assertEqual(same.status_code, 200, same.data)
        self.assertEqual(same.data['data']['tools_status'], 'error')
        self.assertEqual(same.data['data']['tools_count'], 1)

        changed = self.client.put(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/',
            {'rawConfig': raw_config('changed-mcp')}, format='json',
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['data']['tools_status'], 'unchecked')
        self.assertEqual(changed.data['data']['tools_count'], 0)
        self.assertIsNone(changed.data['data']['tools_checked_at'])
        self.assertEqual(changed.data['data']['tools_error'], '')

    @patch('ai_core.views.discover_mcp_tools', return_value=[discovered_tool('disabled_tool')])
    def test_disabled_configuration_can_refresh_and_test_connection_reuses_refresh(self, discover):
        self.config.is_active = False
        self.config.save(update_fields=['is_active'])

        refreshed = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )
        tested = self.client.post(
            '/api/v1/ai-core/mcp-configs/test-connection/',
            {'config_id': self.config.id}, format='json',
        )

        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertFalse(refreshed.data['data']['is_active'])
        self.assertEqual(tested.status_code, 200, tested.data)
        self.assertFalse(tested.data['data']['is_active'])
        self.assertEqual(tested.data['data']['tools_status'], 'ready')
        self.assertEqual(discover.call_count, 2)

    @patch('ai_core.views.discover_mcp_tools')
    def test_non_admin_cannot_refresh_or_test_connection(self, discover):
        self.client.force_authenticate(self.member)

        refreshed = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )
        tested = self.client.post(
            '/api/v1/ai-core/mcp-configs/test-connection/',
            {'config_id': self.config.id}, format='json',
        )

        self.assertEqual(refreshed.status_code, 403, refreshed.data)
        self.assertEqual(tested.status_code, 403, tested.data)
        discover.assert_not_called()

    @patch('ai_core.views.discover_mcp_tools')
    def test_edit_during_refresh_invalidates_in_flight_result(self, discover):
        def edit_configuration(_raw):
            edited = self.client.put(
                f'/api/v1/ai-core/mcp-configs/{self.config.id}/',
                {'rawConfig': raw_config('edited-during-refresh')},
                format='json',
            )
            self.assertEqual(edited.status_code, 200, edited.data)
            return [discovered_tool('must_not_be_saved')]

        discover.side_effect = edit_configuration
        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn('未保存', result.data['message'])
        self.assertFalse(result.data['data']['tools_refresh_applied'])
        self.config.refresh_from_db()
        self.assertEqual(self.config.tools_status, 'unchecked')
        self.assertEqual(self.config.tools.count(), 0)
        self.assertIn('edited-during-refresh', self.config.raw_config)

    @patch('ai_core.views.discover_mcp_tools')
    def test_later_concurrent_refresh_invalidates_older_result(self, discover):
        call_count = 0

        def nested_refresh(_raw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                newer = self.client.post(
                    f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/',
                    {}, format='json',
                )
                self.assertEqual(newer.status_code, 200, newer.data)
                return [discovered_tool('older_result')]
            return [discovered_tool('newer_result')]

        discover.side_effect = nested_refresh
        result = self.client.post(
            f'/api/v1/ai-core/mcp-configs/{self.config.id}/refresh_tools/', {}, format='json',
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn('未保存', result.data['message'])
        self.assertFalse(result.data['data']['tools_refresh_applied'])
        self.assertEqual(
            list(self.config.tools.values_list('name', flat=True)), ['newer_result'],
        )


class FakeSessionContext:
    def __init__(self, session):
        self.session = session
        self.closed = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakeClient:
    def __init__(self, sessions):
        self.sessions = sessions
        self.contexts = []

    def session(self, server_name):
        context = FakeSessionContext(self.sessions[server_name])
        self.contexts.append(context)
        return context


class MCPToolDiscoveryProtocolTests(SimpleTestCase):
    def test_tools_list_follows_all_pages(self):
        tool_a = SimpleNamespace(name='tool_a', description='A', inputSchema={'type': 'object'})
        tool_b = SimpleNamespace(name='tool_b', description='B', inputSchema={'type': 'object'})

        class PaginatedSession:
            def __init__(self):
                self.cursors = []

            async def list_tools(self, cursor=None):
                self.cursors.append(cursor)
                if cursor is None:
                    return SimpleNamespace(tools=[tool_a], nextCursor='page-2')
                return SimpleNamespace(tools=[tool_b], nextCursor=None)

        session = PaginatedSession()
        client = FakeClient({'fixture': session})
        result = discover_mcp_tools(
            raw_config(), client_factory=lambda connections: client,
        )

        self.assertEqual(session.cursors, [None, 'page-2'])
        self.assertEqual([tool['name'] for tool in result], ['tool_a', 'tool_b'])
        self.assertTrue(client.contexts[0].closed)

    def test_repeated_or_invalid_cursor_is_an_explicit_failure(self):
        class BadCursorSession:
            def __init__(self, next_cursor):
                self.next_cursor = next_cursor

            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[], nextCursor=self.next_cursor)

        for next_cursor, error_code in (('repeat', 'repeated_cursor'), (123, 'invalid_cursor')):
            with self.subTest(next_cursor=next_cursor):
                session = BadCursorSession(next_cursor)
                client = FakeClient({'fixture': session})
                with self.assertRaises(MCPToolDiscoveryError) as raised:
                    discover_mcp_tools(
                        raw_config(), client_factory=lambda connections, client=client: client,
                    )
                self.assertEqual(raised.exception.code, error_code)
                self.assertTrue(client.contexts[0].closed)

    def test_nested_discovery_error_keeps_its_safe_code_and_message(self):
        class NestedFailureSession:
            async def list_tools(self, cursor=None):
                safe = MCPToolDiscoveryError(
                    'repeated_cursor',
                    'MCP服务重复返回相同的工具分页游标，工具清单未更新。',
                    cause_type='RepeatedCursor',
                )
                raise ExceptionGroup('stdio task group failed', [safe])

        client = FakeClient({'fixture': NestedFailureSession()})
        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(raw_config(), client_factory=lambda connections: client)

        self.assertEqual(raised.exception.code, 'repeated_cursor')
        self.assertIn('重复返回', str(raised.exception))

    def test_missing_tool_name_is_a_structure_failure(self):
        class MissingNameSession:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(
                    tools=[SimpleNamespace(name='', description='', inputSchema={})],
                    nextCursor=None,
                )

        client = FakeClient({'fixture': MissingNameSession()})
        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(raw_config(), client_factory=lambda connections: client)

        self.assertEqual(raised.exception.code, 'missing_tool_name')

    def test_invalid_server_entry_prevents_partial_ready_result(self):
        config = {'mcpServers': {
            'valid': {'command': 'offline'},
            'invalid': {'args': ['missing-command']},
        }}

        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(config, client_factory=lambda connections: None)

        self.assertEqual(raised.exception.code, 'invalid_server_config')

    def test_duplicate_tool_names_across_servers_fail_instead_of_being_dropped(self):
        duplicate = SimpleNamespace(name='same_name', description='', inputSchema={})

        class OnePageSession:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[duplicate], nextCursor=None)

        client = FakeClient({'one': OnePageSession(), 'two': OnePageSession()})
        config = {'mcpServers': {
            'one': {'command': 'offline-one'},
            'two': {'command': 'offline-two'},
        }}

        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(config, client_factory=lambda connections: client)

        self.assertEqual(raised.exception.code, 'duplicate_tool_name')
        self.assertTrue(all(context.closed for context in client.contexts))

    def test_total_timeout_cancels_and_closes_every_server_session(self):
        class HangingSession:
            def __init__(self):
                self.cancelled = False

            async def list_tools(self, cursor=None):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        sessions = {'one': HangingSession(), 'two': HangingSession()}
        client = FakeClient(sessions)
        config = {'mcpServers': {
            'one': {'command': 'offline-one'},
            'two': {'command': 'offline-two'},
        }}

        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(
                config,
                timeout_seconds=0.01,
                client_factory=lambda connections: client,
            )

        self.assertEqual(raised.exception.code, 'timeout')
        self.assertTrue(all(session.cancelled for session in sessions.values()))
        self.assertTrue(all(context.closed for context in client.contexts))

    def test_one_server_failure_makes_whole_multi_server_discovery_fail(self):
        tool = SimpleNamespace(name='available', description='', inputSchema={})

        class GoodSession:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[tool], nextCursor=None)

        class FailingSession:
            async def list_tools(self, cursor=None):
                raise ConnectionError('contains-sensitive-provider-detail')

        client = FakeClient({'good': GoodSession(), 'bad': FailingSession()})
        config = {'mcpServers': {
            'good': {'command': 'offline-good'},
            'bad': {'command': 'offline-bad'},
        }}

        with self.assertRaises(MCPToolDiscoveryError) as raised:
            discover_mcp_tools(config, client_factory=lambda connections: client)

        self.assertEqual(raised.exception.code, 'connection_failed')
        self.assertNotIn('sensitive', str(raised.exception))
        self.assertTrue(all(context.closed for context in client.contexts))
