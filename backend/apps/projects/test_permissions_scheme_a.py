from django.http import Http404
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from api_testing.browser_discovery_views import BrowserDiscoveryConfigView, _owned_task
from api_testing.models import APIEndpoint, APISpecification, APITestCase, APITestSuite, BrowserDiscoveryTask
from api_testing.serializers import APITestSuiteAddTestCaseSerializer
from api_testing.views import (
    APITestCaseListCreateView, APITestCaseRetrieveUpdateDestroyView,
    APITestSuiteListCreateView, APITestExecutionDeleteView, EndpointTestCasesView,
    _source_visible_specifications,
)
from api_testing.workspace_service import (
    WorkspaceValidationError, generation_budget, validate_model_id, validate_spec_id,
)
from projects.access import EDIT, get_project_for_user, projects_for_user
from projects.models import Project, ProjectMember
from projects.views import ProjectMemberDetailView, ProjectMemberListView, ProjectViewSet
from scheduled_tasks.views import ReportExecutionLogPublicView
from users.models import User


class PermissionSchemeATests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username='platform-admin', email='admin@example.test', is_staff=True,
        )
        self.creator = User.objects.create_user(
            username='legacy-creator', email='creator@example.test',
        )
        self.member = User.objects.create_user(
            username='member', email='member@example.test',
        )
        self.outsider = User.objects.create_user(
            username='outsider', email='outsider@example.test',
        )
        self.project = Project.objects.create(
            name='shared-api', project_type='api', created_by=self.creator, owner=self.creator,
        )
        self.membership = ProjectMember.objects.create(
            project=self.project, user=self.member, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )

    def _request(self, user, method='get', path='/'):
        request = getattr(self.factory, method)(path, {}, format='json')
        force_authenticate(request, user=user)
        return request

    def _queryset(self, view_type, user, *, method='get'):
        view = view_type()
        request = self._request(user, method)
        view.request = view.initialize_request(request)
        view.kwargs = {'project_id': self.project.pk}
        view.format_kwarg = None
        return view.get_queryset()

    def test_admin_sees_all_and_normal_user_requires_membership(self):
        other = Project.objects.create(name='other', project_type='web', created_by=self.creator)
        self.assertEqual(set(projects_for_user(self.admin)), {self.project, other})
        self.assertEqual(list(projects_for_user(self.member)), [self.project])
        self.assertEqual(get_project_for_user(self.project.pk, self.admin, EDIT), self.project)
        with self.assertRaises(Http404):
            get_project_for_user(self.project.pk, self.creator)

    def test_project_search_keeps_admin_and_member_visibility_boundaries(self):
        hidden = Project.objects.create(
            name='shared-api-hidden-match', project_type='api', created_by=self.creator,
        )
        for user, expected_ids in (
            (self.admin, {self.project.pk, hidden.pk}),
            (self.member, {self.project.pk}),
        ):
            request = self.factory.get('/', {'search': 'shared-api'})
            force_authenticate(request, user=user)
            reply = ProjectViewSet.as_view({'get': 'list'})(request)
            self.assertEqual(reply.status_code, 200, reply.data)
            self.assertEqual(
                {item['id'] for item in reply.data['data']['items']}, expected_ids,
            )

    def test_removing_member_revokes_legacy_creator_owner_access(self):
        ProjectMember.objects.create(
            project=self.project, user=self.creator, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        ).delete()
        with self.assertRaises(Http404):
            get_project_for_user(self.project.pk, self.creator, EDIT)

    def test_normal_member_cannot_manage_project_or_members(self):
        project_view = ProjectViewSet.as_view({'patch': 'partial_update'})
        request = self.factory.patch('/', {'name': 'forbidden'}, format='json')
        force_authenticate(request, user=self.member)
        self.assertEqual(project_view(request, pk=self.project.pk).status_code, 403)

        member_view = ProjectMemberListView.as_view()
        request = self.factory.get('/')
        force_authenticate(request, user=self.member)
        self.assertEqual(member_view(request, project_id=self.project.pk).status_code, 403)

    def test_admin_member_create_uses_simple_defaults_and_delete_revokes(self):
        create_view = ProjectMemberListView.as_view()
        request = self.factory.post('/', {'user': self.outsider.pk}, format='json')
        force_authenticate(request, user=self.admin)
        created = create_view(request, project_id=self.project.pk)
        self.assertEqual(created.status_code, 201, created.data)
        membership = ProjectMember.objects.get(project=self.project, user=self.outsider)
        self.assertEqual(membership.role, 'editor')
        self.assertTrue(all((membership.can_edit, membership.can_delete,
                            membership.can_execute_tests, membership.can_view_reports)))

        delete_view = ProjectMemberDetailView.as_view()
        request = self.factory.delete('/')
        force_authenticate(request, user=self.admin)
        self.assertEqual(
            delete_view(request, project_id=self.project.pk, pk=membership.pk).status_code,
            204,
        )
        with self.assertRaises(Http404):
            get_project_for_user(self.project.pk, self.outsider)

    def test_admin_is_rejected_as_redundant_project_member(self):
        request = self.factory.post('/', {'user': self.admin.pk}, format='json')
        force_authenticate(request, user=self.admin)
        result = ProjectMemberListView.as_view()(request, project_id=self.project.pk)
        self.assertEqual(result.status_code, 400)
        self.assertFalse(ProjectMember.objects.filter(project=self.project, user=self.admin).exists())

    def test_api_cases_and_suites_are_shared_with_project_members(self):
        case = APITestCase.objects.create(
            project=self.project, created_by=self.creator, title='created by teammate',
            test_case_type='scenario', test_type='', script_content='{}',
        )
        suite = APITestSuite.objects.create(
            project=self.project, user=self.creator, name='teammate suite',
        )
        self.assertIn(case, self._queryset(APITestCaseListCreateView, self.member))
        self.assertIn(
            case,
            self._queryset(APITestCaseRetrieveUpdateDestroyView, self.member, method='patch'),
        )
        self.assertIn(suite, self._queryset(APITestSuiteListCreateView, self.member))

        serializer = APITestSuiteAddTestCaseSerializer(
            data={'test_case_ids': [case.pk]},
            context={'request': self._request(self.member), 'project': self.project},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        self.membership.delete()
        with self.assertRaises(Http404):
            self._queryset(APITestCaseListCreateView, self.member)

    def test_shared_active_llm_is_usable_without_creator_ownership(self):
        model = LLMConfiguration.objects.create(
            created_by=self.creator, provider='openai', model_name='shared-model',
            extra_config={'timeout': 47}, is_active=True,
        )
        disabled = LLMConfiguration.objects.create(
            created_by=self.creator, provider='openai', model_name='disabled', is_active=False,
        )
        vision = LLMConfiguration.objects.create(
            created_by=self.creator, provider='openai', model_name='vision',
            model_type=ModelType.VISION, is_active=True,
        )
        self.assertEqual(validate_model_id(model.pk, owner=self.member), model.pk)
        self.assertEqual(generation_budget(model_id=model.pk, owner=self.member)['timeouts']['llm_seconds'], 47)
        config_reply = BrowserDiscoveryConfigView.as_view()(
            self._request(self.member), project_id=self.project.pk,
        )
        self.assertEqual(config_reply.status_code, 200, config_reply.data)
        self.assertIn(model.pk, {item['id'] for item in config_reply.data['data']['models']})
        self.assertNotIn('api_key', str(config_reply.data['data']['models']))
        for unavailable in (disabled, vision):
            with self.assertRaises(WorkspaceValidationError):
                validate_model_id(unavailable.pk, owner=self.member)

    def test_reading_endpoint_cases_does_not_grant_execution_delete(self):
        self.membership.can_delete = False
        self.membership.save(update_fields=['can_delete'])
        spec = APISpecification.objects.create(project=self.project, created_by=self.creator, spec_name='fixture')
        endpoint = APIEndpoint.objects.create(spec=spec, path='/fixture', method='GET')
        reply = EndpointTestCasesView.as_view()(
            self._request(self.member), project_id=self.project.pk, spec_id=spec.pk, endpoint_id=endpoint.pk,
        )
        self.assertEqual(reply.status_code, 200, reply.data)
        reply = APITestExecutionDeleteView.as_view()(
            self._request(self.member, method='delete'), project_id=self.project.pk, pk=99999,
        )
        self.assertEqual(reply.status_code, 403, reply.data)

    def test_published_browser_api_assets_are_shared_but_task_stays_private(self):
        task = BrowserDiscoveryTask.objects.create(
            project=self.project, owner=self.creator, model_id=1,
            target_url='https://example.test', description='private discovery',
            task_id='private-task',
        )
        spec = APISpecification.objects.create(
            project=self.project, created_by=self.creator, source_task=task,
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE, spec_name='published capture',
            status=APISpecification.TaskStatus.COMPLETED,
        )
        self.assertIn(spec, _source_visible_specifications(self.project.pk, self.member))
        self.assertEqual(validate_spec_id(self.project.pk, spec.pk, owner=self.member), spec)
        with self.assertRaises(LookupError):
            _owned_task(self.project.pk, task.pk, self.member)

    def test_public_report_permission_remains_allow_any(self):
        self.assertEqual(ReportExecutionLogPublicView.permission_classes, [AllowAny])
