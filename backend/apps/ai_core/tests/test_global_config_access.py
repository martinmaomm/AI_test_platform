import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from ai_core.config_access import usable_llm_configurations, usable_mcp_configurations
from ai_core.model_manager import ModelManager
from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType, RAGConfiguration


class GlobalAIConfigurationAccessTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(
            'platform-admin', email='platform-admin@example.test', is_staff=True,
        )
        self.member = users.objects.create_user(
            'project-member', email='project-member@example.test',
        )
        self.legacy_creator = users.objects.create_user(
            'legacy-creator', email='legacy-creator@example.test', is_active=False,
        )
        self.llm = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai',
            provider_name='Shared gateway',
            api_key='must-not-leak',
            base_url='https://secret-gateway.example.test/v1',
            model_name='shared-model',
            extra_config={'secret_header': 'must-not-leak'},
            is_active=True,
            created_by=self.legacy_creator,
        )
        self.disabled_llm = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai',
            api_key='disabled-secret',
            base_url='https://disabled.example.test/v1',
            model_name='disabled-model',
            is_active=False,
            created_by=self.member,
        )
        self.vision = LLMConfiguration.objects.create(
            model_type=ModelType.VISION,
            provider='openai',
            api_key='vision-secret',
            base_url='https://vision.example.test/v1',
            model_name='vision-model',
            is_active=True,
            created_by=self.member,
        )
        self.mcp = MCPConfiguration.objects.create(
            raw_config=json.dumps({'mcpServers': {'playwright': {'command': 'offline'}}}),
            is_active=True,
            created_by=self.legacy_creator,
        )
        self.rag = RAGConfiguration.objects.create(
            name='legacy-rag',
            is_active=True,
            created_by=self.member,
        )
        self.client = APIClient()

    def test_runtime_querysets_only_apply_active_and_type_contract(self):
        self.assertEqual(list(usable_llm_configurations()), [self.llm])
        self.assertEqual(list(usable_mcp_configurations()), [self.mcp])

    def test_model_manager_never_falls_back_to_disabled_llm(self):
        self.llm.is_active = False
        self.llm.save(update_fields=['is_active'])
        with self.assertRaisesRegex(ValueError, '没有可用的llm配置'):
            ModelManager(model_type=ModelType.LLM)

    def test_member_safe_options_include_global_model_without_secrets(self):
        self.client.force_authenticate(self.member)
        result = self.client.get('/api/v1/ai-core/llm-configs/available/')

        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        self.assertEqual(len(result.data['data']), 1)
        item = result.data['data'][0]
        self.assertEqual(set(item), {
            'id', 'name', 'model_name', 'provider', 'provider_display',
            'provider_name', 'display_name', 'model_type', 'is_active',
        })
        self.assertEqual(item['id'], self.llm.id)
        self.assertEqual(item['name'], 'Shared gateway - shared-model')
        self.assertEqual(item['display_name'], item['name'])
        rendered = json.dumps(result.data, ensure_ascii=False)
        for secret in ('must-not-leak', 'secret-gateway.example.test', 'secret_header'):
            self.assertNotIn(secret, rendered)

    def test_member_cannot_access_global_configuration_management(self):
        self.client.force_authenticate(self.member)
        for path in (
            '/api/v1/ai-core/llm-configs/',
            '/api/v1/ai-core/rag-configs/',
            '/api/v1/ai-core/mcp-configs/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

        for path, payload in (
            (f'/api/v1/ai-core/llm-configs/{self.llm.id}/toggle_active/', {}),
            (f'/api/v1/ai-core/rag-configs/{self.rag.id}/set_default/', {}),
            (f'/api/v1/ai-core/mcp-configs/{self.mcp.id}/toggle_active/', {}),
            ('/api/v1/ai-core/rag-configs/test_connection/', {'config_id': self.rag.id}),
            ('/api/v1/ai-core/mcp-configs/test-connection/', {'config_id': self.mcp.id}),
        ):
            with self.subTest(path=path):
                result = self.client.post(path, payload, format='json')
                self.assertEqual(result.status_code, 403, result.data)

    def test_admin_manages_existing_configuration_from_non_admin_creator(self):
        self.client.force_authenticate(self.admin)
        listed = self.client.get('/api/v1/ai-core/llm-configs/')
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(
            {item['id'] for item in listed.data['data']},
            {self.llm.id, self.disabled_llm.id, self.vision.id},
        )

        detail = self.client.get(f'/api/v1/ai-core/llm-configs/{self.llm.id}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['api_key'], 'must-not-leak')

        updated = self.client.put(
            f'/api/v1/ai-core/llm-configs/{self.llm.id}/',
            {'model_name': 'shared-model-v2'},
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.llm.refresh_from_db()
        self.assertEqual(self.llm.model_name, 'shared-model-v2')
        self.assertEqual(self.llm.created_by_id, self.legacy_creator.id)

    @patch('ai_core.views.test_llm_connection_with_config')
    def test_only_admin_can_test_existing_global_llm(self, test_connection):
        test_connection.return_value = {'success': True, 'response_time': 0}
        self.client.force_authenticate(self.member)
        denied = self.client.post(
            '/api/v1/ai-core/llm-configs/test_connection/',
            {'config_id': self.llm.id},
            format='json',
        )
        self.assertEqual(denied.status_code, 403)
        test_connection.assert_not_called()

        self.client.force_authenticate(self.admin)
        accepted = self.client.post(
            '/api/v1/ai-core/llm-configs/test_connection/',
            {'config_id': self.llm.id},
            format='json',
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        test_connection.assert_called_once_with(self.llm)

    def test_admin_lists_global_rag_and_mcp_configurations(self):
        self.client.force_authenticate(self.admin)
        rag = self.client.get('/api/v1/ai-core/rag-configs/')
        mcp = self.client.get('/api/v1/ai-core/mcp-configs/')
        self.assertEqual(rag.status_code, 200, rag.data)
        self.assertEqual(mcp.status_code, 200, mcp.data)
        self.assertEqual([item['id'] for item in rag.data['data']], [self.rag.id])
        self.assertEqual(
            {item['id'] for item in mcp.data['data']},
            {self.mcp.id},
        )

    def test_rag_default_is_platform_global_not_creator_scoped(self):
        other = RAGConfiguration.objects.create(
            name='other-legacy-rag',
            is_active=True,
            is_default=True,
            created_by=self.legacy_creator,
        )
        self.rag.is_default = True
        self.rag.save()

        other.refresh_from_db()
        self.assertFalse(other.is_default)
        self.assertTrue(self.rag.is_default)

    def test_admin_mcp_list_query_parameters_never_hide_the_singleton(self):
        self.client.force_authenticate(self.admin)
        self.mcp.is_active = False
        self.mcp.save(update_fields=['is_active'])

        for query in ('search=does-not-match', 'provider=does-not-match', 'status=active'):
            with self.subTest(query=query):
                result = self.client.get(f'/api/v1/ai-core/mcp-configs/?{query}')
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual([item['id'] for item in result.data['data']], [self.mcp.id])
