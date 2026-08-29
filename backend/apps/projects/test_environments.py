from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .environments.views import EnvironmentListView
from .models import Environment, Project, ProjectMember
from .serializers import EnvironmentSerializer


class ProjectEnvironmentContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='environment-contract-user',
            email='environment-contract@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='Web environment project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role='owner',
            can_edit=True,
        )

    def request(self, method, payload=None, query=''):
        factory = APIRequestFactory()
        request = getattr(factory, method.lower())(
            f'/environments/{query}', payload or {}, format='json'
        )
        force_authenticate(request, user=self.user)
        return request

    def test_web_project_rejects_api_environment_creation(self):
        result = EnvironmentListView.as_view()(
            self.request(
                'POST',
                {
                    'name': 'wrong environment',
                    'category': 'api',
                    'config': {'base_url': 'https://api.example.test'},
                },
            ),
            project_id=self.project.id,
        )

        self.assertEqual(result.status_code, 400)
        self.assertFalse(Environment.objects.exists())

    def test_web_environment_config_drops_other_category_fields(self):
        environment = Environment.objects.create(
            project=self.project,
            name='web environment',
            category='web',
            config={'base_url': 'https://web.example.test'},
        )
        serializer = EnvironmentSerializer(
            environment,
            data={
                'name': environment.name,
                'category': 'web',
                'config': {
                    'base_url': 'https://web.example.test',
                    'variables': {'tenant': 'test'},
                    'browser': 'firefox',
                    'implicit_wait': 10,
                    'app_package': 'invalid.app',
                },
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        saved = serializer.save()
        self.assertEqual(
            saved.config,
            {
                'base_url': 'https://web.example.test',
                'variables': {'tenant': 'test'},
            },
        )

    def test_web_project_list_hides_legacy_non_web_environment(self):
        Environment.objects.create(
            project=self.project,
            name='web environment',
            category='web',
            config={'base_url': 'https://web.example.test'},
        )
        Environment.objects.create(
            project=self.project,
            name='legacy API environment',
            category='api',
            config={'base_url': 'https://api.example.test'},
        )

        result = EnvironmentListView.as_view()(
            self.request('GET'), project_id=self.project.id
        )

        self.assertEqual(result.status_code, 200)
        names = [item['name'] for item in result.data['data']['items']]
        self.assertEqual(names, ['web environment'])
