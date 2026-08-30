"""Phase-one tests for WebUI requirement generation context and persistence."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType, RAGConfiguration
from projects.knowledge.models import KnowledgeBaseFile
from projects.models import Project

from .models import (
    WebElement,
    WebPage,
    WebUITestCase,
    WebUITestCaseGeneration,
    WebUITestModule,
)
from .requirement_context import build_requirement_generation_context
from .views import GenerateWebUITestCasesView, WebUITestCaseGenerationContextView


class RequirementGenerationPhaseOneTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='requirement-phase1-user',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI requirement project',
            project_type='web',
            created_by=self.user,
            owner=self.user,
        )
        self.module = WebUITestModule.objects.create(
            project=self.project,
            name='权限管理',
            description='维护用户和权限。',
            business_rules=['新增用户账号必须唯一'],
        )
        self.child_module = WebUITestModule.objects.create(
            project=self.project,
            parent=self.module,
            name='用户管理',
        )
        self.page = WebPage.objects.create(
            project=self.project,
            module=self.child_module,
            name='用户列表',
            url_path='/users',
            page_class_name='UserListPage',
        )
        self.element = WebElement.objects.create(
            page=self.page,
            name='新增用户按钮',
            locator_type='role',
            locator_value='button:新增用户',
            action_type='click',
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai-compatible',
            api_key='test-key',
            base_url='https://llm.example.test/v1',
            model_name='test-model',
            is_active=True,
            created_by=self.user,
        )
        self.rag = RAGConfiguration.objects.create(
            name='WebUI knowledge',
            is_active=True,
            is_default=True,
            created_by=self.user,
        )
        self.knowledge_file = KnowledgeBaseFile.objects.create(
            project=self.project,
            uploaded_by=self.user,
            status=KnowledgeBaseFile.RAGIngestionStatus.COMPLETED,
            metadata={'module_id': self.child_module.id},
        )
        self.factory = APIRequestFactory()

    def test_context_includes_descendant_assets_and_module_matched_knowledge(self):
        context = build_requirement_generation_context(
            project_id=self.project.id,
            module_id=self.module.id,
            user=self.user,
        )

        self.assertEqual(context['module']['path'], ['权限管理'])
        self.assertEqual(
            context['module']['included_module_ids'],
            [self.module.id, self.child_module.id],
        )
        self.assertEqual(context['module']['business_rule_count'], 1)
        self.assertEqual(context['assets']['page_count'], 1)
        self.assertEqual(context['assets']['element_count'], 1)
        self.assertEqual(context['assets']['pages'][0]['name'], '用户列表')
        self.assertNotIn('locator_value', context['assets']['pages'][0]['elements'][0])
        self.assertEqual(context['knowledge']['completed_files'], 1)
        self.assertEqual(context['knowledge']['module_matched_files'], 1)
        self.assertTrue(context['knowledge']['files'][0]['module_matched'])
        self.assertEqual(context['default_model_id'], self.model.id)
        self.assertEqual(context['readiness']['status'], 'ready')
        self.assertTrue(context['readiness']['can_generate'])

    def test_context_blocks_generation_when_user_has_no_active_llm(self):
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])

        context = build_requirement_generation_context(
            project_id=self.project.id,
            module_id=self.module.id,
            user=self.user,
        )

        self.assertEqual(context['models'], [])
        self.assertEqual(context['readiness']['status'], 'blocked')
        self.assertFalse(context['readiness']['can_generate'])
        self.assertIn('当前账号没有可用的 LLM 模型，请先配置并启用模型。', context['readiness']['blockers'])

    def test_context_api_validates_module_and_project_scope(self):
        view = WebUITestCaseGenerationContextView.as_view()

        missing_request = self.factory.get('/context/')
        force_authenticate(missing_request, user=self.user)
        missing_response = view(missing_request, project_id=self.project.id)
        self.assertEqual(missing_response.status_code, 400)
        self.assertEqual(missing_response.data['error']['code'], 'validation_error')

        valid_request = self.factory.get('/context/', {'module_id': self.module.id})
        force_authenticate(valid_request, user=self.user)
        valid_response = view(valid_request, project_id=self.project.id)
        self.assertEqual(valid_response.status_code, 200)
        self.assertEqual(valid_response.data['data']['module']['id'], self.module.id)

        other_project = Project.objects.create(
            name='Other WebUI project',
            project_type='web',
            created_by=self.user,
            owner=self.user,
        )
        other_module = WebUITestModule.objects.create(project=other_project, name='其他模块')
        cross_project_request = self.factory.get('/context/', {'module_id': other_module.id})
        force_authenticate(cross_project_request, user=self.user)
        cross_project_response = view(cross_project_request, project_id=self.project.id)
        self.assertEqual(cross_project_response.status_code, 404)

    def test_legacy_generation_endpoint_requires_module_and_limits_description(self):
        view = GenerateWebUITestCasesView.as_view()

        missing_module_request = self.factory.post(
            '/generate/',
            {'user_input': '生成登录用例', 'description': '正常登录'},
            format='json',
        )
        force_authenticate(missing_module_request, user=self.user)
        missing_module_response = view(missing_module_request, project_id=self.project.id)
        self.assertEqual(missing_module_response.status_code, 400)
        self.assertIn('请选择业务模块', missing_module_response.data['message'])

        oversized_request = self.factory.post(
            '/generate/',
            {
                'user_input': '生成登录用例',
                'description': '场' * 2001,
                'module_id': self.module.id,
            },
            format='json',
        )
        force_authenticate(oversized_request, user=self.user)
        oversized_response = view(oversized_request, project_id=self.project.id)
        self.assertEqual(oversized_response.status_code, 400)
        self.assertIn('2000', oversized_response.data['message'])

    def test_import_source_key_is_unique_within_generation(self):
        generation = WebUITestCaseGeneration.objects.create(
            project=self.project,
            user=self.user,
            module=self.module,
            model_config=self.model,
            request_text='生成用户管理核心用例',
        )
        values = {
            'title': '新增用户',
            'description': '新增一个唯一用户。',
            'expected_result': '新增成功。',
            'user': self.user,
            'project': self.project,
            'module': self.module,
            'source_requirement_generation': generation,
            'source_draft_key': 'draft-1',
        }
        WebUITestCase.objects.create(**values)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WebUITestCase.objects.create(**values)
