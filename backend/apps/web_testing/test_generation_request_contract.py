from django.test import SimpleTestCase

from .serializers import WebUIScriptGenerationCreateSerializer


class WebUIScriptGenerationRequestContractTests(SimpleTestCase):
    def test_top_level_list_is_a_validation_error(self):
        serializer = WebUIScriptGenerationCreateSerializer(
            data=['description', '目标网址：https://web.example.test/items'],
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)
