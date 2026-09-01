from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from ai_core.views import LLMConfigurationDetailView, LLMConfigurationViewSet


class LLMConfigurationProviderNameAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='provider-name-user', password='test-password'
        )
        self.factory = APIRequestFactory()

    def request(self, method, path, payload=None):
        request = getattr(self.factory, method.lower())(path, payload or {}, format='json')
        force_authenticate(request, user=self.user)
        return request

    def test_create_detail_and_list_include_editable_provider_name(self):
        payload = {
            'model_type': ModelType.LLM,
            'provider': 'openai',
            'provider_name': 'OpenAI 企业网关',
            'api_key': 'test-api-key',
            'base_url': 'https://llm.example.test',
            'model_name': 'gpt-4.1',
            'is_active': True,
        }
        created = LLMConfigurationViewSet.as_view()(self.request('POST', '/llm-configs/', payload))

        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(created.data['data']['provider'], 'openai')
        self.assertEqual(created.data['data']['provider_name'], 'OpenAI 企业网关')
        self.assertEqual(created.data['data']['provider_display'], 'OpenAI 企业网关')
        configuration_id = created.data['data']['id']

        detail = LLMConfigurationDetailView.as_view()(
            self.request('GET', f'/llm-configs/{configuration_id}/'), config_id=configuration_id
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['provider_name'], 'OpenAI 企业网关')

        listed = LLMConfigurationViewSet.as_view()(self.request('GET', '/llm-configs/'))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['data'][0]['provider_name'], 'OpenAI 企业网关')

    def test_provider_name_can_be_cleared_and_display_falls_back_to_provider(self):
        configuration = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai',
            provider_name='OpenAI 企业网关',
            api_key='test-api-key',
            base_url='https://llm.example.test',
            model_name='gpt-4.1',
            created_by=self.user,
        )
        updated = LLMConfigurationDetailView.as_view()(
            self.request('PUT', f'/llm-configs/{configuration.id}/', {
                'model_type': ModelType.LLM,
                'provider': 'openai',
                'provider_name': '',
                'api_key': 'test-api-key',
                'base_url': 'https://llm.example.test',
                'model_name': 'gpt-4.1',
                'is_active': True,
            }),
            config_id=configuration.id,
        )

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['data']['provider_name'], '')
        self.assertEqual(updated.data['data']['provider_display'], 'openai')
        configuration.refresh_from_db()
        self.assertEqual(configuration.provider_name, '')
