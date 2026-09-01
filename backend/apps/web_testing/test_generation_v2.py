"""Focused regression tests for WebUI script-generation V2 stages 0 and 1."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import patch

from projects.models import Environment, Project
from ai_core.models import LLMConfiguration, ModelType

from .generation_contracts import GenerationTransitionError
from .generation_repository import (
    cancel_generation,
    get_generation_temporary_credentials,
    transition_generation,
)
from .generation_security import (
    clear_temporary_credentials,
    extract_inline_login_credentials,
    GenerationInputSecurityError,
    find_suspected_credentials,
    get_temporary_credentials,
    normalize_start_path,
    redact_metadata,
    redact_text,
    redact_url,
    store_temporary_credentials,
)
from .models import WebUIScriptGeneration, WebUITestCase
from .serializers import WebUIScriptGenerationCreateSerializer
from .views import (
    WebUIScriptGenerationCancelView,
    WebUIScriptGenerationCreateView,
    WebUIScriptGenerationDetailView,
    WebUIScriptGenerationResolveView,
    WebUIScriptGenerationSettingsView,
)


class WebUIScriptGenerationV2BaseTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='generation-v2-user',
            email='generation-v2@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='Generation V2 project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project,
            name='Generation V2 WebUI',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test:9443'},
            is_active=True,
        )
        self.model_config = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai',
            api_key='test-key',
            base_url='https://llm.example.test',
            model_name='test-model',
            is_active=True,
            created_by=self.user,
        )
        self.factory = APIRequestFactory()

    def request(self, method, path, payload=None):
        request = getattr(self.factory, method.lower())(path, payload or {}, format='json')
        force_authenticate(request, user=self.user)
        return request

    def create_generation(self, **overrides):
        data = {
            'description': '登录后查询用户列表，不修改已有数据。',
            'environment_id': self.environment.id,
            'start_path': '/admin/users?tab=all',
        }
        data.update(overrides)
        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            return_value=type('TaskResult', (), {'id': 'v2-generation-task-id'})(),
        ) as delay_mock:
            response = WebUIScriptGenerationCreateView.as_view()(
                self.request('POST', '/script-generations/', data),
                project_id=self.project.id,
            )
        self.last_generation_delay = delay_mock
        self.assertEqual(response.status_code, 201, response.data)
        return response


class WebUIScriptGenerationModelAndStateTests(WebUIScriptGenerationV2BaseTestCase):
    def test_model_defaults_are_safe_and_recoverable(self):
        with patch('web_testing.serializers.exploration_total_timeout_seconds', return_value=600):
            response = self.create_generation()
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])

        self.assertEqual(generation.status, WebUIScriptGeneration.Status.CREATED)
        self.assertEqual(generation.current_stage, WebUIScriptGeneration.Stage.CREATED)
        self.assertEqual(generation.progress, 0)
        self.assertEqual(generation.start_path, '/admin/users?tab=all')
        self.assertEqual(generation.target_url_safe, 'https://web.example.test:9443/admin/users?tab=all')
        self.assertFalse(generation.credentials_provided)
        self.assertEqual(generation.script_draft, '')
        self.assertEqual(generation.revision, 0)
        self.assertEqual(generation.resume_count, 0)
        self.assertEqual(generation.clarifications, [])
        self.assertEqual(generation.exploration_timeout_seconds, 600)

    def test_state_transition_rejects_skips_and_is_idempotent(self):
        generation_id = self.create_generation().data['data']['id']

        with self.assertRaises(GenerationTransitionError):
            transition_generation(generation_id, WebUIScriptGeneration.Status.GENERATING)

        transition_generation(generation_id, WebUIScriptGeneration.Status.NORMALIZING, progress=5)
        repeated = transition_generation(generation_id, WebUIScriptGeneration.Status.NORMALIZING, progress=5)
        self.assertEqual(repeated.status, WebUIScriptGeneration.Status.NORMALIZING)
        transitioned = transition_generation(generation_id, WebUIScriptGeneration.Status.PREFLIGHTING, progress=10)
        self.assertEqual(transitioned.current_stage, WebUIScriptGeneration.Stage.PREFLIGHTING)
        self.assertIsNotNone(transitioned.started_at)

    def test_terminal_cancel_clears_temporary_credentials(self):
        response = self.create_generation(temporary_credentials={'username': 'admin', 'password': 'super-secret'})
        generation_id = response.data['data']['id']
        self.assertEqual(get_temporary_credentials(generation_id)['password'], 'super-secret')

        generation = cancel_generation(generation_id)
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.CANCELLED)
        self.assertIsNone(get_temporary_credentials(generation_id))
        self.assertIsNotNone(generation.completed_at)

    def test_missing_cached_credentials_are_marked_expired(self):
        response = self.create_generation(temporary_credentials={'username': 'admin', 'password': 'super-secret'})
        generation_id = response.data['data']['id']
        clear_temporary_credentials(generation_id)

        self.assertIsNone(get_generation_temporary_credentials(generation_id))
        generation = WebUIScriptGeneration.objects.get(pk=generation_id)
        self.assertTrue(generation.credentials_expired)


class WebUIScriptGenerationSecurityTests(TestCase):
    def test_redaction_and_credential_detection_never_return_values(self):
        description = '登录账号 admin 123456，password: top-secret，进入用户列表。'
        redacted = redact_text(description)

        self.assertIn('<redacted>', redacted)
        self.assertNotIn('admin', redacted)
        self.assertNotIn('123456', redacted)
        self.assertNotIn('top-secret', redacted)
        self.assertEqual(find_suspected_credentials(description), ['secret_assignment', 'login_pair'])
        self.assertEqual(
            extract_inline_login_credentials('登录账号 admin 123456，然后查询列表。'),
            {'username': 'admin', 'password': '123456'},
        )
        self.assertEqual(find_suspected_credentials('登录后查询列表，密码字段应隐藏。'), [])

        url_description = '打开 https://web.example.test/login?token=super-secret 后登录。'
        self.assertIn('url_secret', find_suspected_credentials(url_description))
        self.assertNotIn('super-secret', redact_text(url_description))

    def test_url_metadata_and_relative_path_are_sanitized(self):
        self.assertEqual(
            redact_url('https://name:secret@web.example.test/a?token=abc&tab=all'),
            'https://<redacted>@web.example.test/a?token=%3Credacted%3E&tab=all',
        )
        self.assertEqual(
            normalize_start_path(
                'https://web.example.test:9443/admin?token=abc&tab=all',
                'https://web.example.test:9443',
            ),
            '/admin?tab=all',
        )
        with self.assertRaises(GenerationInputSecurityError):
            normalize_start_path('https://other.example.test/admin', 'https://web.example.test:9443')
        self.assertEqual(redact_url('https://web.example.test:bad/admin?token=abc'), '<redacted>')
        with self.assertRaises(GenerationInputSecurityError):
            normalize_start_path('https://web.example.test:bad/admin', 'https://web.example.test:9443')

        safe = redact_metadata({'password': 'top-secret', 'nested': {'token': 'abc'}, 'message': 'password: nope'})
        self.assertEqual(safe['password'], '<redacted>')
        self.assertEqual(safe['nested']['token'], '<redacted>')
        self.assertNotIn('nope', safe['message'])

    def test_temporary_credentials_have_explicit_cache_lifecycle(self):
        generation_id = 'temporary-credential-test'
        clear_temporary_credentials(generation_id)
        store_temporary_credentials(generation_id, {'username': 'admin', 'password': 'super-secret'}, timeout=60)
        self.assertEqual(get_temporary_credentials(generation_id), {'username': 'admin', 'password': 'super-secret'})
        clear_temporary_credentials(generation_id)
        self.assertIsNone(get_temporary_credentials(generation_id))


class WebUIScriptGenerationAPITests(WebUIScriptGenerationV2BaseTestCase):
    def test_create_persists_default_or_requested_exploration_timeout(self):
        with patch('web_testing.serializers.exploration_total_timeout_seconds', return_value=720):
            default_response = self.create_generation()
        default_generation = WebUIScriptGeneration.objects.get(pk=default_response.data['data']['id'])
        self.assertEqual(default_generation.exploration_timeout_seconds, 720)
        self.assertEqual(default_response.data['data']['exploration_timeout_seconds'], 720)

        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            return_value=type('TaskResult', (), {'id': 'v2-generation-timeout-override-task-id'})(),
        ):
            requested_response = WebUIScriptGenerationCreateView.as_view()(
                self.request('POST', '/script-generations/', {
                    'description': '查询列表。', 'environment_id': self.environment.id,
                    'start_path': '/', 'exploration_timeout_seconds': 900,
                }),
                project_id=self.project.id,
            )
        self.assertEqual(requested_response.status_code, 201, requested_response.data)
        requested_generation = WebUIScriptGeneration.objects.get(pk=requested_response.data['data']['id'])
        self.assertEqual(requested_generation.exploration_timeout_seconds, 900)

    def test_create_rejects_out_of_range_exploration_timeout(self):
        for timeout in (59, 1801):
            response = WebUIScriptGenerationCreateView.as_view()(
                self.request('POST', '/script-generations/', {
                    'description': '查询列表。', 'environment_id': self.environment.id,
                    'start_path': '/', 'exploration_timeout_seconds': timeout,
                }),
                project_id=self.project.id,
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn('exploration_timeout_seconds', response.data['error']['details'])

    def test_settings_returns_server_default_under_project_read_permission(self):
        with patch('web_testing.views.exploration_total_timeout_seconds', return_value=900):
            response = WebUIScriptGenerationSettingsView.as_view()(
                self.request('GET', '/script-generation-settings/'), project_id=self.project.id,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data'], {
            'exploration_timeout_seconds': 900,
            'min_exploration_timeout_seconds': 60,
            'max_exploration_timeout_seconds': 1800,
        })

    def pause_generation(self, *, status, error_code, scenario_spec=None, warnings=None):
        generation_id = self.create_generation().data['data']['id']
        WebUIScriptGeneration.objects.filter(pk=generation_id).update(
            status=status,
            current_stage=(
                WebUIScriptGeneration.Stage.NORMALIZING
                if status == WebUIScriptGeneration.Status.NEEDS_INPUT
                else WebUIScriptGeneration.Stage.PREFLIGHTING
            ),
            progress=25,
            error_code=error_code,
            error_message='需要用户处理。',
            scenario_spec=scenario_spec or {},
            warnings=warnings or [],
        )
        return WebUIScriptGeneration.objects.get(pk=generation_id)

    def test_create_query_and_cancel_are_project_scoped_and_secret_free(self):
        response = self.create_generation(temporary_credentials={'username': 'admin', 'password': 'super-secret'})
        payload = response.data['data']
        generation_id = payload['id']
        serialized = str(response.data)
        self.assertNotIn('super-secret', serialized)
        self.assertTrue(payload['credentials_provided'])
        self.assertEqual(payload['celery_task_id'], 'v2-generation-task-id')

        detail = WebUIScriptGenerationDetailView.as_view()(
            self.request('GET', f'/script-generations/{generation_id}/'),
            project_id=self.project.id,
            generation_id=generation_id,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['data']['id'], generation_id)

        cancelled = WebUIScriptGenerationCancelView.as_view()(
            self.request('POST', f'/script-generations/{generation_id}/cancel/'),
            project_id=self.project.id,
            generation_id=generation_id,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.data['data']['status'], WebUIScriptGeneration.Status.CANCELLED)
        self.assertIsNone(get_temporary_credentials(generation_id))

    def test_cancel_calls_existing_cancellation_synchronously(self):
        generation_id = self.create_generation().data['data']['id']
        WebUIScriptGeneration.objects.filter(pk=generation_id).update(celery_task_id='active-generation-task')

        with patch('web_testing.views.cancel_task') as cancel_task_mock:
            response = WebUIScriptGenerationCancelView.as_view()(
                self.request('POST', f'/script-generations/{generation_id}/cancel/'),
                project_id=self.project.id,
                generation_id=generation_id,
            )

        self.assertEqual(response.status_code, 200)
        cancel_task_mock.assert_called_once_with('active-generation-task')
        self.assertFalse(cancel_task_mock.delay.called)

    def test_create_extracts_inline_credentials_without_persisting_or_returning_them(self):
        inline = self.create_generation(
            description='登录账号 inline-user inline-secret-123，然后查询列表。',
        )
        generation_id = inline.data['data']['id']
        generation = WebUIScriptGeneration.objects.get(pk=generation_id)

        self.assertTrue(inline.data['data']['credentials_provided'])
        self.assertNotIn('inline-user', str(inline.data))
        self.assertNotIn('inline-secret-123', str(inline.data))
        self.assertNotIn('inline-user', generation.description_safe)
        self.assertNotIn('inline-secret-123', generation.description_safe)
        self.assertEqual(
            get_temporary_credentials(generation_id),
            {'username': 'inline-user', 'password': 'inline-secret-123'},
        )

    def test_create_rejects_wrong_environment_scope(self):

        other_project = Project.objects.create(
            name='Other generation project', project_type='web', owner=self.user, created_by=self.user
        )
        other_environment = Environment.objects.create(
            project=other_project,
            name='Other WebUI',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://other.example.test'},
        )
        wrong_environment = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。',
                'environment_id': other_environment.id,
                'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(wrong_environment.status_code, 400)
        self.assertIn('environment_id', wrong_environment.data['error']['details'])

    def test_create_validates_web_environment_base_url_and_same_origin(self):
        inactive_environment = Environment.objects.create(
            project=self.project,
            name='Disabled WebUI',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://disabled.example.test'},
            is_active=False,
        )
        inactive = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。', 'environment_id': inactive_environment.id, 'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(inactive.status_code, 400)

        wrong_origin = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。',
                'environment_id': self.environment.id,
                'url': 'https://other.example.test/users',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(wrong_origin.status_code, 400)
        self.assertIn('start_path', wrong_origin.data['error']['details'])

        bad_port_environment = Environment.objects.create(
            project=self.project,
            name='Bad Port WebUI',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test:bad'},
        )
        bad_port = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。', 'environment_id': bad_port_environment.id, 'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(bad_port.status_code, 400)
        self.assertIn('start_path', bad_port.data['error']['details'])

    def test_create_rejects_missing_base_url_and_non_web_environment(self):
        missing_base_url = Environment.objects.create(
            project=self.project,
            name='No Base URL',
            category=Environment.EnvironmentCategory.WEB,
            config={},
        )
        missing_base = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。', 'environment_id': missing_base_url.id, 'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(missing_base.status_code, 400)
        self.assertIn('start_path', missing_base.data['error']['details'])

        api_environment = Environment.objects.create(
            project=self.project,
            name='API Environment',
            category=Environment.EnvironmentCategory.API,
            config={'base_url': 'https://api.example.test'},
        )
        api_environment_result = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '查询列表。', 'environment_id': api_environment.id, 'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(api_environment_result.status_code, 400)
        self.assertIn('environment_id', api_environment_result.data['error']['details'])

    def test_create_requires_project_membership(self):
        other_user = get_user_model().objects.create_user(
            username='generation-v2-nonmember',
            email='generation-v2-nonmember@example.com',
            password='test-password',
        )
        request = self.factory.post('/script-generations/', {
            'description': '查询列表。', 'environment_id': self.environment.id, 'start_path': '/',
        }, format='json')
        force_authenticate(request, user=other_user)

        response = WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)
        self.assertEqual(response.status_code, 404)

    def test_detail_does_not_cross_project_boundary(self):
        generation_id = self.create_generation().data['data']['id']
        other_project = Project.objects.create(
            name='Other project for detail', project_type='web', owner=self.user, created_by=self.user
        )

        response = WebUIScriptGenerationDetailView.as_view()(
            self.request('GET', f'/script-generations/{generation_id}/'),
            project_id=other_project.id,
            generation_id=generation_id,
        )
        self.assertEqual(response.status_code, 404)

    def test_cache_write_failure_compensates_generation_record(self):
        serializer_request = self.request('POST', '/script-generations/')
        serializer_request.user = self.user
        serializer = WebUIScriptGenerationCreateSerializer(
            data={
                'description': '查询列表。',
                'environment_id': self.environment.id,
                'start_path': '/',
                'temporary_credentials': {'username': 'admin', 'password': 'super-secret'},
            },
            context={'request': serializer_request, 'project': self.project},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with patch(
            'web_testing.serializers.store_temporary_credentials',
            side_effect=RuntimeError('cache unavailable'),
        ), patch('web_testing.serializers.clear_temporary_credentials') as clear_credentials_mock:
            with self.assertRaisesRegex(RuntimeError, 'cache unavailable'):
                serializer.save()

        self.assertFalse(WebUIScriptGeneration.objects.filter(project=self.project).exists())
        clear_credentials_mock.assert_called_once()

    def test_ambiguous_generation_accepts_all_answers_and_resumes_same_record(self):
        questions = ['新增用户还有哪些必填字段？', '编辑后的昵称如何生成？']
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            error_code='INPUT_AMBIGUOUS',
            scenario_spec={'ambiguities': questions},
            warnings=questions,
        )
        request = self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
            'expected_status': generation.status,
            'expected_revision': generation.revision,
            'clarification_answers': [
                {'question': questions[0], 'answer': '初始密码使用环境变量，角色选择普通用户。'},
                {'question': questions[1], 'answer': '使用原唯一名称加 _edited 后缀。'},
            ],
        })
        task_result = type('TaskResult', (), {'id': 'v2-resumed-task-id'})()
        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            return_value=task_result,
        ) as delay_mock:
            response = WebUIScriptGenerationResolveView.as_view()(
                request,
                project_id=self.project.id,
                generation_id=generation.pk,
            )

        self.assertEqual(response.status_code, 202, response.data)
        generation.refresh_from_db()
        self.assertEqual(str(generation.pk), response.data['data']['id'])
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.NORMALIZING)
        self.assertEqual(generation.revision, 1)
        self.assertEqual(generation.resume_count, 1)
        self.assertEqual(generation.celery_task_id, 'v2-resumed-task-id')
        self.assertEqual(len(generation.clarifications), 1)
        self.assertEqual(generation.clarifications[0]['answers'][1]['answer'], '使用原唯一名称加 _edited 后缀。')
        delay_mock.assert_called_once_with(str(generation.pk))

    def test_pre_exploration_ambiguity_resumes_directly_without_manual_answers(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            error_code='INPUT_AMBIGUOUS',
            scenario_spec={'ambiguities': ['缺少新增表单字段', '缺少用户列表菜单路径']},
        )
        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            return_value=type('TaskResult', (), {'id': 'v2-auto-explore-task-id'})(),
        ) as delay_mock:
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
                    'expected_status': generation.status,
                    'expected_revision': generation.revision,
                }),
                project_id=self.project.id,
                generation_id=generation.pk,
            )

        self.assertEqual(response.status_code, 202, response.data)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.PREFLIGHTING)
        self.assertEqual(generation.current_stage, WebUIScriptGeneration.Stage.PREFLIGHTING)
        self.assertEqual(generation.clarifications[-1]['answers'], [])
        delay_mock.assert_called_once_with(str(generation.pk))

    def test_credentials_resolution_resumes_at_preflight_without_exposing_secret(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
            error_code='CREDENTIALS_REQUIRED',
            scenario_spec={'title': '需要登录的场景'},
        )
        with patch(
            'web_testing.views.generate_webui_script_generation_v2_task.delay',
            return_value=type('TaskResult', (), {'id': 'v2-credential-resume-task'})(),
        ):
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
                    'expected_status': generation.status,
                    'expected_revision': generation.revision,
                    'description': '登录账号 admin super-secret，然后进入用户列表。',
                }),
                project_id=self.project.id,
                generation_id=generation.pk,
            )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertNotIn('super-secret', str(response.data))
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.PREFLIGHTING)
        self.assertTrue(generation.credentials_provided)
        self.assertFalse(generation.credentials_expired)
        self.assertEqual(get_temporary_credentials(generation.pk)['password'], 'super-secret')

    def test_resolution_rejects_stale_revision_without_dispatch(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_INPUT,
            error_code='SCENARIO_CONTRACT_INVALID',
        )
        with patch('web_testing.views.generate_webui_script_generation_v2_task.delay') as delay_mock:
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
                    'expected_status': generation.status,
                    'expected_revision': generation.revision + 1,
                    'description': '目标：查询用户列表。步骤：进入列表。成功标准：列表可见。',
                }),
                project_id=self.project.id,
                generation_id=generation.pk,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['data']['revision'], 0)
        delay_mock.assert_not_called()

    def test_extra_risk_must_be_revised_before_resume(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            error_code='EXPLORATION_WRITE_CONFIRMATION_REQUIRED',
        )
        response = WebUIScriptGenerationResolveView.as_view()(
            self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
                'expected_status': generation.status,
                'expected_revision': generation.revision,
                'description': '探索阶段请付款并查看结果。',
            }),
            project_id=self.project.id,
            generation_id=generation.pk,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('description', response.data['error']['details'])

    def test_legacy_crud_write_pause_can_resume_without_read_only_rewrite(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            error_code='EXPLORATION_WRITE_CONFIRMATION_REQUIRED',
        )
        generation.description_safe = '探索阶段新增本轮用户并查看结果，结束后清理。'
        generation.save(update_fields=['description_safe'])
        with patch('web_testing.views.generate_webui_script_generation_v2_task.delay') as delay_mock:
            delay_mock.return_value.id = 'resumed-crud'
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', '/resolve/', {
                    'expected_status': generation.status, 'expected_revision': generation.revision,
                }), project_id=self.project.id, generation_id=generation.pk,
            )
        self.assertEqual(response.status_code, 202, response.data)
        delay_mock.assert_called_once()

    def test_new_extra_risk_pause_requires_a_description_change(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            error_code='EXPLORATION_EXTRA_RISK_BLOCKED',
        )
        with patch('web_testing.views.generate_webui_script_generation_v2_task.delay') as delay_mock:
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', '/resolve/', {
                    'expected_status': generation.status, 'expected_revision': generation.revision,
                }), project_id=self.project.id, generation_id=generation.pk,
            )
        self.assertEqual(response.status_code, 400)
        delay_mock.assert_not_called()

    def test_resume_limit_moves_paused_generation_to_review_without_dispatch(self):
        generation = self.pause_generation(
            status=WebUIScriptGeneration.Status.NEEDS_INPUT,
            error_code='SCENARIO_CONTRACT_INVALID',
        )
        WebUIScriptGeneration.objects.filter(pk=generation.pk).update(resume_count=3)
        generation.refresh_from_db()
        with patch('web_testing.views.generate_webui_script_generation_v2_task.delay') as delay_mock:
            response = WebUIScriptGenerationResolveView.as_view()(
                self.request('POST', f'/script-generations/{generation.pk}/resolve/', {
                    'expected_status': generation.status,
                    'expected_revision': generation.revision,
                    'description': '目标：查询用户列表。步骤：进入列表。成功标准：列表可见。',
                }),
                project_id=self.project.id,
                generation_id=generation.pk,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['data']['status'], WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(response.data['data']['error_code'], 'RESUME_LIMIT_REACHED')
        delay_mock.assert_not_called()
