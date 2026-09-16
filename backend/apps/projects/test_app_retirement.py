"""Old App project/environment options must not remain writable APIs."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .environments.views import EnvironmentDetailView, EnvironmentListView
from .models import Environment, Project
from .serializers import EnvironmentCreateSerializer, EnvironmentSerializer, ProjectCreateSerializer
from .views import ProjectViewSet


class AppRetirementContractTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='app-retirement-admin', email='app-retirement@example.test', is_staff=True,
        )
        self.factory = APIRequestFactory()

    def request(self, method, payload=None):
        request = getattr(self.factory, method)('/', payload or {}, format='json')
        force_authenticate(request, user=self.admin)
        return request

    def test_app_project_creation_is_rejected_without_writing(self):
        result = ProjectViewSet.as_view({'post': 'create'})(self.request(
            'post', {'name': 'retired app', 'project_type': 'app'},
        ))
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Project.objects.exists())

    def test_supported_project_types_remain_creatable(self):
        for kind in ('api', 'web', 'perf'):
            with self.subTest(kind=kind):
                result = ProjectViewSet.as_view({'post': 'create'})(self.request(
                    'post', {'name': f'{kind} project', 'project_type': kind},
                ))
                self.assertEqual(result.status_code, 201, result.data)
                self.assertTrue(Project.objects.filter(project_type=kind).exists())

    def test_retired_project_filter_does_not_fall_back_to_all_projects(self):
        Project.objects.create(name='API', project_type='api', created_by=self.admin)
        result = ProjectViewSet.as_view({'get': 'list'})(self.request(
            'get', {'project_type': 'app'},
        ))
        self.assertEqual(result.status_code, 400)

    def test_app_is_absent_from_serializer_choices_and_environment_fields(self):
        self.assertNotIn('app', ProjectCreateSerializer().fields['project_type'].choices)
        for serializer in (EnvironmentSerializer(), EnvironmentCreateSerializer()):
            self.assertNotIn('app', serializer.fields['category'].choices)
            self.assertNotIn('is_app_environment', serializer.fields)

    def test_environment_serializers_reject_app_category(self):
        for serializer_class in (EnvironmentSerializer, EnvironmentCreateSerializer):
            with self.subTest(serializer=serializer_class.__name__):
                serializer = serializer_class(data={
                    'name': 'Appium', 'category': 'app',
                    'config': {'device_name': 'emulator', 'app_package': 'example.app'},
                })
                self.assertFalse(serializer.is_valid())
                self.assertIn('category', serializer.errors)

    def test_legacy_app_rows_are_not_deleted_or_exposed_as_active_environments(self):
        # Stored values are intentionally retained; this cleanup never drops tables/data.
        project = Project.objects.create(name='historical', project_type='app', created_by=self.admin)
        environment = Environment.objects.create(
            project=project, name='historical', category='app', config={},
        )
        result = EnvironmentListView.as_view()(self.request('get'), project_id=project.pk)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['data']['items'], [])
        detail = EnvironmentDetailView.as_view()(
            self.request('get'), project_id=project.pk, pk=environment.pk,
        )
        self.assertEqual(detail.status_code, 404)
        result = EnvironmentListView.as_view()(
            self.request('post', {
                'name': 'new', 'category': 'api', 'config': {'base_url': 'https://api.example.test'},
            }), project_id=project.pk,
        )
        self.assertEqual(result.status_code, 400)
        self.assertTrue(Environment.objects.filter(pk=environment.pk).exists())
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

    def test_app_environment_helpers_are_retired(self):
        for name in ('is_app_environment', 'get_app_config'):
            self.assertFalse(hasattr(Environment, name))
