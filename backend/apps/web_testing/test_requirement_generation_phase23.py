"""Phase-two and phase-three tests for requirement-generated WebUI cases."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Project

from . import requirement_case_generator
from .models import (
    WebElement,
    WebPage,
    WebUITestCase,
    WebUITestCaseGeneration,
    WebUITestModule,
)
from .requirement_case_validator import (
    sanitize_requirement_drafts,
    validate_requirement_drafts,
)
from .tasks import _execute_webui_requirement_drafts_generation
from .views import (
    WebUITestCaseGenerationDetailView,
    WebUITestCaseGenerationImportView,
    WebUITestCaseGenerationListCreateView,
    WebUITestCaseGenerationValidateView,
)


class RequirementGenerationPhaseTwoThreeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='requirement-phase23-user',
            email='requirement-phase23-user@example.test',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='Requirement phase 2 and 3',
            project_type='web',
            created_by=self.user,
            owner=self.user,
        )
        self.module = WebUITestModule.objects.create(
            project=self.project,
            name='用户管理',
            description='维护用户数据。',
            business_rules=['用户账号必须唯一'],
        )
        self.page = WebPage.objects.create(
            project=self.project,
            module=self.module,
            name='用户列表',
            url_path='/users',
        )
        WebElement.objects.create(
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
        self.factory = APIRequestFactory()

    def valid_draft(self, key='draft-001', *, category='functional', title='新增用户'):
        return {
            'draft_key': key,
            'module_id': self.module.id,
            'source_refs': [f'module:{self.module.id}', f'page:{self.page.id}'],
            'title': title,
            'description': '验证用户可以进入用户列表并发起新增操作。',
            'priority': 'high',
            'category': category,
            'preconditions': ['当前用户具备用户管理权限'],
            'steps': [
                {
                    'step_id': 1,
                    'action': 'goto',
                    'target': None,
                    'value': '/users',
                    'description': '进入用户列表页面',
                },
                {
                    'step_id': 2,
                    'action': 'click',
                    'target': '新增用户按钮',
                    'value': None,
                    'description': '点击新增用户按钮',
                },
            ],
            'expected_result': '页面显示新增用户表单，并可以继续填写用户信息。',
        }

    def create_generation(self, *, status=WebUITestCaseGeneration.Status.NEEDS_REVIEW, drafts=None):
        return WebUITestCaseGeneration.objects.create(
            project=self.project,
            user=self.user,
            module=self.module,
            model_config=self.model,
            status=status,
            request_text='生成用户管理核心流程',
            generation_scope=WebUITestCaseGeneration.Scope.CORE,
            case_categories=['functional'],
            target_case_count=3,
            context_snapshot={
                'assets': {
                    'pages': [{
                        'id': self.page.id,
                        'url_path': '/users',
                        'elements': [{'name': '新增用户按钮'}],
                    }],
                },
                'knowledge': {'matched_sources': []},
            },
            draft_test_cases=drafts or [],
        )

    def post(self, view, path, data, **kwargs):
        request = self.factory.post(path, data, format='json')
        force_authenticate(request, user=self.user)
        return view.as_view()(request, project_id=self.project.id, **kwargs)

    def test_validator_accepts_contract_and_reports_asset_warnings(self):
        generation = self.create_generation()
        draft = self.valid_draft()

        valid_report = validate_requirement_drafts(
            [draft], generation=generation, context=generation.context_snapshot,
        )
        self.assertTrue(valid_report['valid'])
        self.assertEqual(valid_report['summary']['importable_count'], 1)

        draft['steps'][1]['target'] = '不存在的按钮'
        warning_report = validate_requirement_drafts(
            [draft], generation=generation, context=generation.context_snapshot,
        )
        self.assertTrue(warning_report['valid'])
        self.assertIn(
            'TARGET_NOT_IN_ASSETS',
            {item['code'] for item in warning_report['warnings']},
        )

    def test_validator_blocks_contract_errors_and_redacts_sensitive_values(self):
        generation = self.create_generation()
        draft = self.valid_draft()
        draft['module_id'] = self.module.id + 999
        draft['unexpected_field'] = 'must be rejected'
        draft['steps'][1].update({
            'step_id': 3,
            'action': 'fill',
            'target': '密码输入框',
            'value': 'Danger123!',
            'description': '',
            'unexpected_step_field': 'must be rejected',
        })

        report = validate_requirement_drafts(
            [draft], generation=generation, context=generation.context_snapshot,
        )
        blocker_codes = {item['code'] for item in report['blockers']}
        self.assertFalse(report['valid'])
        self.assertIn('MODULE_MISMATCH', blocker_codes)
        self.assertIn('DRAFT_FIELDS_UNKNOWN', blocker_codes)
        self.assertIn('STEP_ID_SEQUENCE_INVALID', blocker_codes)
        self.assertIn('STEP_FIELDS_UNKNOWN', blocker_codes)
        self.assertIn('STEP_DESCRIPTION_MISSING', blocker_codes)
        self.assertIn('SENSITIVE_VALUE_DETECTED', blocker_codes)
        self.assertEqual(
            sanitize_requirement_drafts([draft])[0]['steps'][1]['value'],
            '<redacted>',
        )

    def test_generator_repairs_invalid_schema_once_and_injects_provenance(self):
        generator = requirement_case_generator.RequirementCaseGenerator.__new__(
            requirement_case_generator.RequirementCaseGenerator,
        )
        generation = SimpleNamespace(
            request_text='生成用户管理核心流程',
            generation_scope='core',
            case_categories=['functional'],
            target_case_count=1,
            module_id=self.module.id,
        )
        valid_model_output = {
            'test_cases': [{
                'title': '新增用户',
                'description': '验证新增入口。',
                'priority': 'high',
                'category': 'functional',
                'preconditions': [],
                'steps': [{
                    'step_id': 1,
                    'action': 'click',
                    'target': '新增用户按钮',
                    'value': None,
                    'description': '点击新增用户按钮',
                }],
                'expected_result': '页面显示新增用户表单。',
            }],
        }
        context = {
            'module': {'id': self.module.id},
            'assets': {'pages': [{
                'id': self.page.id,
                'url_path': '/users',
                'elements': [{'name': '新增用户按钮'}],
            }]},
            'knowledge': {'matched_sources': []},
        }

        with patch.object(
            generator,
            '_invoke',
            side_effect=[
                json.dumps({'test_cases': [{'title': '结构不完整'}]}, ensure_ascii=False),
                json.dumps(valid_model_output, ensure_ascii=False),
            ],
        ) as mock_invoke:
            drafts, repaired = generator.generate(
                generation=generation,
                context=context,
            )

        self.assertTrue(repaired)
        self.assertEqual(mock_invoke.call_count, 2)
        self.assertEqual(drafts[0]['draft_key'], 'draft-001')
        self.assertEqual(drafts[0]['module_id'], self.module.id)
        self.assertIn(f'page:{self.page.id}', drafts[0]['source_refs'])

    @patch('web_testing.tasks.generate_webui_requirement_drafts_task.delay')
    def test_create_generation_is_idempotent_for_same_client_request(self, mock_delay):
        mock_delay.return_value = SimpleNamespace(id='celery-requirement-task')
        client_request_id = str(uuid.uuid4())
        payload = {
            'module_id': self.module.id,
            'model_config_id': self.model.id,
            'client_request_id': client_request_id,
            'description': '生成用户管理核心流程',
            'generation_scope': 'core',
            'case_categories': ['functional'],
            'target_case_count': 3,
        }

        first = self.post(
            WebUITestCaseGenerationListCreateView,
            '/generations/',
            payload,
        )
        second = self.post(
            WebUITestCaseGenerationListCreateView,
            '/generations/',
            payload,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['data']['id'], second.data['data']['id'])
        self.assertTrue(second.data['data']['reused'])
        self.assertEqual(WebUITestCaseGeneration.objects.count(), 1)
        mock_delay.assert_called_once()

    def test_detail_hides_generation_from_other_user(self):
        generation = self.create_generation()
        other_user = get_user_model().objects.create_user(
            username='requirement-phase23-other',
            email='requirement-phase23-other@example.test',
            password='test-password',
        )
        request = self.factory.get('/generation/')
        force_authenticate(request, user=other_user)

        result = WebUITestCaseGenerationDetailView.as_view()(
            request,
            project_id=self.project.id,
            generation_id=generation.id,
        )

        self.assertEqual(result.status_code, 404)

    def test_revalidate_persists_user_edits_without_creating_cases(self):
        generation = self.create_generation(drafts=[self.valid_draft()])
        edited = self.valid_draft(title='新增用户 - 已审核')

        result = self.post(
            WebUITestCaseGenerationValidateView,
            '/validate/',
            {'draft_test_cases': [edited]},
            generation_id=generation.id,
        )

        self.assertEqual(result.status_code, 200)
        generation.refresh_from_db()
        self.assertEqual(generation.draft_test_cases[0]['title'], '新增用户 - 已审核')
        self.assertTrue(generation.validation_report['valid'])
        self.assertEqual(WebUITestCase.objects.count(), 0)

    def test_import_creates_only_selected_drafts_and_repeat_is_idempotent(self):
        first_draft = self.valid_draft('draft-001', title='新增用户')
        second_draft = self.valid_draft('draft-002', title='查询用户')
        generation = self.create_generation(drafts=[first_draft, second_draft])
        payload = {
            'draft_test_cases': [first_draft, second_draft],
            'selected_draft_keys': ['draft-002'],
        }

        first = self.post(
            WebUITestCaseGenerationImportView,
            '/import/',
            payload,
            generation_id=generation.id,
        )
        second = self.post(
            WebUITestCaseGenerationImportView,
            '/import/',
            payload,
            generation_id=generation.id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data['data']['created_count'], 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['data']['created_count'], 0)
        self.assertEqual(WebUITestCase.objects.count(), 1)
        imported = WebUITestCase.objects.get()
        self.assertEqual(imported.source_draft_key, 'draft-002')
        self.assertEqual(imported.script_source, 'manual')
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUITestCaseGeneration.Status.IMPORTED)
        self.assertEqual(generation.created_case_ids, [imported.id])

    def test_import_rejects_blocked_draft_without_creating_cases(self):
        draft = self.valid_draft()
        draft['steps'][0]['value'] = None
        generation = self.create_generation(drafts=[draft])

        result = self.post(
            WebUITestCaseGenerationImportView,
            '/import/',
            {'draft_test_cases': [draft], 'selected_draft_keys': ['draft-001']},
            generation_id=generation.id,
        )

        self.assertEqual(result.status_code, 400)
        self.assertEqual(WebUITestCase.objects.count(), 0)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUITestCaseGeneration.Status.NEEDS_REVIEW)

    def test_import_rolls_back_all_cases_when_one_create_fails(self):
        first_draft = self.valid_draft('draft-001', title='新增用户')
        second_draft = self.valid_draft('draft-002', title='查询用户')
        generation = self.create_generation(drafts=[first_draft, second_draft])
        real_create = WebUITestCase.objects.create
        call_count = 0

        def create_then_fail(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError('simulated database failure')
            return real_create(**kwargs)

        with patch.object(WebUITestCase.objects, 'create', side_effect=create_then_fail):
            result = self.post(
                WebUITestCaseGenerationImportView,
                '/import/',
                {
                    'draft_test_cases': [first_draft, second_draft],
                    'selected_draft_keys': ['draft-001', 'draft-002'],
                },
                generation_id=generation.id,
            )

        self.assertEqual(result.status_code, 500)
        self.assertEqual(WebUITestCase.objects.count(), 0)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUITestCaseGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.created_case_ids, [])

    def test_task_generates_reviewable_drafts_without_creating_cases(self):
        generation = self.create_generation(
            status=WebUITestCaseGeneration.Status.CREATED,
        )
        generated_draft = self.valid_draft()
        generator = MagicMock()
        generator.generate.return_value = ([generated_draft], False)
        fake_task = SimpleNamespace(
            request=SimpleNamespace(id='requirement-task-1'),
            update_state=MagicMock(),
        )

        with patch.object(
            requirement_case_generator,
            'RequirementCaseGenerator',
            return_value=generator,
        ):
            result = _execute_webui_requirement_drafts_generation(
                fake_task,
                str(generation.id),
                user_id=self.user.id,
            )

        self.assertTrue(result['success'])
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUITestCaseGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(generation.draft_test_cases[0]['draft_key'], 'draft-001')
        self.assertEqual(generation.context_snapshot['context_version'], 'webui-requirement-v2.0')
        self.assertEqual(WebUITestCase.objects.count(), 0)
        generator.generate.assert_called_once()
        generator.repair.assert_not_called()

    def test_task_repairs_at_most_once_when_deterministic_validation_blocks(self):
        generation = self.create_generation(
            status=WebUITestCaseGeneration.Status.CREATED,
        )
        blocked_draft = self.valid_draft()
        blocked_draft['steps'][0]['value'] = None
        repaired_draft = self.valid_draft()
        generator = MagicMock()
        generator.generate.return_value = ([blocked_draft], False)
        generator.repair.return_value = [repaired_draft]
        fake_task = SimpleNamespace(
            request=SimpleNamespace(id='requirement-task-2'),
            update_state=MagicMock(),
        )

        with patch.object(
            requirement_case_generator,
            'RequirementCaseGenerator',
            return_value=generator,
        ):
            result = _execute_webui_requirement_drafts_generation(
                fake_task,
                str(generation.id),
                user_id=self.user.id,
            )

        self.assertTrue(result['success'])
        generator.repair.assert_called_once()
        generation.refresh_from_db()
        self.assertTrue(generation.validation_report['valid'])
        self.assertEqual(WebUITestCase.objects.count(), 0)
