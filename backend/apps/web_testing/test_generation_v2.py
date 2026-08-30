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
        response = self.create_generation()
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])

        self.assertEqual(generation.status, WebUIScriptGeneration.Status.CREATED)
        self.assertEqual(generation.current_stage, WebUIScriptGeneration.Stage.CREATED)
        self.assertEqual(generation.progress, 0)
        self.assertEqual(generation.start_path, '/admin/users?tab=all')
        self.assertEqual(generation.target_url_safe, 'https://web.example.test:9443/admin/users?tab=all')
        self.assertFalse(generation.credentials_provided)
        self.assertEqual(generation.script_draft, '')

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

    def test_create_rejects_inline_credentials_and_wrong_environment_scope(self):
        inline = WebUIScriptGenerationCreateView.as_view()(
            self.request('POST', '/script-generations/', {
                'description': '登录账号 admin 123456，然后查询列表。',
                'environment_id': self.environment.id,
                'start_path': '/',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(inline.status_code, 400)
        self.assertIn('description', inline.data['error']['details'])

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
