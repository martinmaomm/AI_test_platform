from django.test import SimpleTestCase

from ai_core.llm_configuration import normalize_openai_compatible_base_url
from ai_core.models import LLMConfiguration
from ai_core.serializers import LLMConfigurationSerializer


class OpenAICompatibleBaseUrlTests(SimpleTestCase):
    def test_normalizes_only_the_terminal_chat_completions_endpoint(self):
        cases = (
            ('openai', 'https://api.example.test/v1/chat/completions', 'https://api.example.test/v1'),
            ('qwen', 'https://api.example.test/gateway/v1/chat/completions/', 'https://api.example.test/gateway/v1'),
            ('ernie', 'https://api.example.test/chat/completions', 'https://api.example.test'),
            ('deepseek', 'https://api.example.test/chat/completions/', 'https://api.example.test'),
            ('other', 'https://api.example.test/proxy/chat/completions/', 'https://api.example.test/proxy'),
            ('zhipu', 'https://api.example.test/custom/v1', 'https://api.example.test/custom/v1'),
            ('ollama', 'http://localhost:11434/chat/completions/', 'http://localhost:11434/chat/completions/'),
        )
        for provider, base_url, expected in cases:
            with self.subTest(provider=provider, base_url=base_url):
                self.assertEqual(
                    normalize_openai_compatible_base_url(provider, base_url), expected
                )

    def test_serializer_normalizes_submitted_openai_compatible_url(self):
        serializer = LLMConfigurationSerializer(data={
            'provider': 'openai',
            'api_key': 'test-key',
            'base_url': 'https://api.example.test/v1/chat/completions/',
            'model_name': 'test-model',
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data['base_url'], 'https://api.example.test/v1'
        )


class LLMConfigurationSerializerUpdateTests(SimpleTestCase):
    def _instance(self, **overrides):
        values = {
            'provider': 'openai',
            'api_key': 'saved-key',
            'base_url': 'https://api.example.test/v1',
            'model_name': 'saved-model',
        }
        values.update(overrides)
        return LLMConfiguration(**values)

    def test_partial_update_keeps_required_saved_values_when_omitted(self):
        serializer = LLMConfigurationSerializer(
            self._instance(), data={'model_name': 'rotated-model'}, partial=True
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_put_style_update_keeps_omitted_api_key(self):
        serializer = LLMConfigurationSerializer(
            self._instance(), data={
                'provider': 'openai',
                'base_url': 'https://api.example.test/v1',
                'model_name': 'saved-model',
            }, partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_explicitly_clearing_required_api_key_is_rejected(self):
        serializer = LLMConfigurationSerializer(
            self._instance(), data={'api_key': ''}, partial=True
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('api_key', serializer.errors)

    def test_api_key_rotation_is_accepted(self):
        serializer = LLMConfigurationSerializer(
            self._instance(), data={'api_key': 'rotated-key'}, partial=True
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['api_key'], 'rotated-key')

    def test_create_requires_complete_openai_configuration(self):
        serializer = LLMConfigurationSerializer(data={
            'provider': 'openai',
            'base_url': 'https://api.example.test/v1',
            'model_name': 'test-model',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('api_key', serializer.errors)

    def test_ollama_does_not_require_an_api_key(self):
        serializer = LLMConfigurationSerializer(data={
            'provider': 'ollama',
            'base_url': 'http://localhost:11434',
            'model_name': 'llama3.2',
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
