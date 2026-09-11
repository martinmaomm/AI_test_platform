"""Offline coverage for the WebUI script-generation history collection."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Project, ProjectMember

from .models import WebUIScriptGeneration, WebUITestCase
from .views import WebUIScriptGenerationCreateView


class WebUIScriptGenerationHistoryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username='history-owner', email='history-owner@example.test',
        )
        self.member = get_user_model().objects.create_user(
            username='history-member', email='history-member@example.test',
        )
        self.outsider = get_user_model().objects.create_user(
            username='history-outsider', email='history-outsider@example.test',
        )
        self.project = Project.objects.create(
            name='History project', project_type='web',
            owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.member, role='viewer',
            can_edit=False, can_delete=False, can_execute_tests=False,
            can_view_reports=False,
        )
        self.factory = APIRequestFactory()

    def create_generation(self, user, **overrides):
        values = {
            'project': self.project,
            'user': user,
            'description_safe': 'history-description-secret',
            'target_url': 'https://history-private.example.test/secret-path',
            'scenario_spec': {'title': '历史生成'},
            'model_info': {
                'provider_name': 'Fixture provider',
                'model_name': 'fixture-model',
                'api_key': 'history-model-secret',
                'base_url': 'https://private-model.example.test',
            },
            'script_draft': 'history-script-secret',
            'exploration_snapshot': {'trace': 'history-trace-secret'},
            'workspace': {'workspace': 'history-workspace-secret'},
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def get(self, user, params=None, project_id=None):
        request = self.factory.get('/script-generations/', params or {})
        if user is not None:
            force_authenticate(request, user=user)
        return WebUIScriptGenerationCreateView.as_view()(
            request, project_id=project_id or self.project.id,
        )

    def test_requires_authentication(self):
        response = self.get(None)
        self.assertEqual(response.status_code, 401)

    def test_read_member_sees_only_own_records_and_owner_cannot_see_member_records(self):
        owner_generation = self.create_generation(self.owner, scenario_spec={'title': 'Owner record'})
        member_generation = self.create_generation(self.member, scenario_spec={'title': 'Member record'})

        member_response = self.get(self.member)
        self.assertEqual(member_response.status_code, 200, member_response.data)
        self.assertEqual(
            [item['id'] for item in member_response.data['data']['items']],
            [str(member_generation.id)],
        )

        owner_response = self.get(self.owner)
        self.assertEqual(owner_response.status_code, 200, owner_response.data)
        self.assertEqual(
            [item['id'] for item in owner_response.data['data']['items']],
            [str(owner_generation.id)],
        )

    def test_history_includes_failed_empty_draft_and_saved_case_records(self):
        failed = self.create_generation(
            self.owner,
            status=WebUIScriptGeneration.Status.FAILED,
            script_draft='',
            scenario_spec={'title': '失败无草稿'},
        )
        case = WebUITestCase.objects.create(
            title='已保存用例', description='fixture', user=self.owner, project=self.project,
        )
        saved = self.create_generation(
            self.owner,
            status=WebUIScriptGeneration.Status.READY,
            test_case=case,
            scenario_spec={'title': '已保存记录'},
        )

        response = self.get(self.owner)
        self.assertEqual(response.status_code, 200, response.data)
        items = {item['id']: item for item in response.data['data']['items']}
        self.assertIn(str(failed.id), items)
        self.assertEqual(items[str(failed.id)]['test_case_id'], None)
        self.assertIn(str(saved.id), items)
        self.assertEqual(items[str(saved.id)]['test_case_id'], case.id)

    def test_cross_project_and_non_web_projects_follow_project_access_convention(self):
        other_project = Project.objects.create(
            name='Other project', project_type='web',
            owner=self.outsider, created_by=self.outsider,
        )
        other_response = self.get(self.owner, project_id=other_project.id)
        self.assertEqual(other_response.status_code, 404)

        app_project = Project.objects.create(
            name='App project', project_type='app',
            owner=self.owner, created_by=self.owner,
        )
        app_response = self.get(self.owner, project_id=app_project.id)
        self.assertEqual(app_response.status_code, 404)

    def test_pagination_has_stable_order_defaults_and_strict_bounds(self):
        base = timezone.now()
        for index in range(5):
            generation = self.create_generation(
                self.owner, scenario_spec={'title': f'记录 {index}'},
            )
            WebUIScriptGeneration.objects.filter(pk=generation.pk).update(
                created_at=base + timedelta(minutes=index),
            )

        default_response = self.get(self.owner)
        self.assertEqual(default_response.status_code, 200, default_response.data)
        self.assertEqual(default_response.data['data']['page'], 1)
        self.assertEqual(default_response.data['data']['page_size'], 20)
        self.assertEqual(default_response.data['data']['total'], 5)
        self.assertEqual(
            [item['title'] for item in default_response.data['data']['items']],
            ['记录 4', '记录 3', '记录 2', '记录 1', '记录 0'],
        )

        paged_response = self.get(self.owner, {'page': '2', 'page_size': '2'})
        self.assertEqual(paged_response.status_code, 200, paged_response.data)
        self.assertEqual(
            [item['title'] for item in paged_response.data['data']['items']],
            ['记录 2', '记录 1'],
        )

        with self.assertNumQueries(2):
            beyond_total_response = self.get(
                self.owner, {'page': '9223372036854775807'},
            )
        self.assertEqual(beyond_total_response.status_code, 200, beyond_total_response.data)
        self.assertEqual(beyond_total_response.data['data']['total'], 5)
        self.assertEqual(beyond_total_response.data['data']['items'], [])

        for params in (
            {'page': '0'}, {'page': '-1'}, {'page': '1.0'}, {'page': 'not-an-int'},
            {'page_size': '0'}, {'page_size': '51'}, {'page_size': '1.0'}, {'page_size': 'not-an-int'},
        ):
            with self.subTest(params=params):
                response = self.get(self.owner, params)
                self.assertEqual(response.status_code, 400, response.data)

    def test_summary_payload_excludes_private_generation_content(self):
        self.create_generation(
            self.owner,
            scenario_spec={'title': '  受控标题  '},
        )
        self.create_generation(
            self.owner,
            scenario_spec={'title': ' '},
        )

        response = self.get(self.owner)
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data['data']
        self.assertEqual(payload['items'][0]['title'], 'UI 脚本生成')
        self.assertEqual(payload['items'][1]['title'], '受控标题')
        for item in payload['items']:
            self.assertEqual(
                set(item),
                {'id', 'title', 'status', 'created_at', 'updated_at', 'test_case_id', 'model_info'},
            )
            self.assertEqual(set(item['model_info']), {'provider_name', 'model_name'})
        rendered = str(payload)
        for private_value in (
            'history-description-secret', 'history-private.example.test',
            'history-model-secret', 'private-model.example.test',
            'history-script-secret', 'history-trace-secret', 'history-workspace-secret',
        ):
            self.assertNotIn(private_value, rendered)

    def test_existing_post_collection_behavior_is_unchanged(self):
        model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai',
            provider_name='POST fixture provider', api_key='post-fixture-key',
            base_url='https://llm.example.test', model_name='post-fixture-model',
            is_active=True, created_by=self.owner,
        )
        request = self.factory.post('/script-generations/', {
            'description': '打开 https://web.example.test/history，检查列表。',
            'model_config_id': model.id,
        }, format='json')
        force_authenticate(request, user=self.owner)
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='history-create-task'),
        ) as delay:
            response = WebUIScriptGenerationCreateView.as_view()(
                request, project_id=self.project.id,
            )

        self.assertEqual(response.status_code, 201, response.data)
        generation = WebUIScriptGeneration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(generation.user_id, self.owner.id)
        delay.assert_called_once_with(str(generation.pk))
