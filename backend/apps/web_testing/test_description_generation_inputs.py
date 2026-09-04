"""HTTP input boundaries for description-only generation, with no environment."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Project
from .models import WebUIScriptGeneration
from .views import WebUIScriptGenerationCreateView


class DescriptionGenerationInputTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='description-input-owner', email='description-input@example.test',
        )
        self.project = Project.objects.create(
            name='No environments', project_type='web',
            owner=self.user, created_by=self.user,
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='Fixture',
            api_key='fixture-not-a-key', base_url='https://llm.example.test',
            model_name='fixture', is_active=True, created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def request(self, payload):
        request = self.factory.post('/script-generations/', payload, format='json')
        force_authenticate(request, user=self.user)
        return WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.pk)

    def test_description_url_and_login_survive_create_without_an_environment(self):
        url = 'https://app.example.test/deep/path?next=%2Fusers&sort=asc#/users'
        description = f'打开 {url}，使用测试账号 fixture-user 和密码 fixture-password 登录。'
        with patch('web_testing.views.generate_webui_script_generation_task.delay',
                   return_value=SimpleNamespace(id='description-create')) as delay:
            response = self.request({'description': description, 'model_config_id': self.model.pk})
        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.target_url, url)
        self.assertEqual(response.data['data']['target_url'], url)
        self.assertEqual(generation.description_safe, description)
        self.assertFalse(self.project.environments.exists())
        self.assertNotIn('environment_id', response.data['data'])
        delay.assert_called_once_with(str(generation.pk))

    def test_missing_or_ambiguous_url_is_400_and_never_dispatches(self):
        for description in ('登录后测试列表。', '打开 /users',
                            '打开 https://first.example/ 或 https://second.example/'):
            with self.subTest(description=description), patch(
                'web_testing.views.generate_webui_script_generation_task.delay',
            ) as delay:
                response = self.request({'description': description})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('description', str(response.data))
                delay.assert_not_called()
        self.assertFalse(WebUIScriptGeneration.objects.exists())

    def test_removed_input_fields_are_rejected_instead_of_used_as_fallbacks(self):
        for field, value in (
            ('environment_id', 99), ('start_path', '/'),
            ('url', 'https://different.example/'),
            ('temporary_credentials', {'username': 'fixture', 'password': 'fixture'}),
        ):
            with self.subTest(field=field), patch(
                'web_testing.views.generate_webui_script_generation_task.delay',
            ) as delay:
                response = self.request({'description': '打开 https://app.example/', field: value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, str(response.data))
                delay.assert_not_called()

    def test_malformed_top_level_json_returns_400_not_500(self):
        for payload in ([], [{'description': '打开 https://app.example/'}], 'not an object'):
            with self.subTest(payload=payload), patch(
                'web_testing.views.generate_webui_script_generation_task.delay',
            ) as delay:
                response = self.request(payload)
                self.assertEqual(response.status_code, 400, response.data)
                delay.assert_not_called()
