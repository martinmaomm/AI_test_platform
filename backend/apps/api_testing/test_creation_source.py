"""Offline contracts for immutable API case creation provenance."""

from copy import deepcopy

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .models import APIEndpoint, APISpecification, APITestCase, APIWorkspace, default_api_workspace_draft
from .workspace_service import create_or_update_case, normalize_draft, update_workspace_draft
from .workspace_verification import draft_hash
from .views import APITestCaseListCreateView


class APICaseCreationSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='api-source-owner')
        self.project = Project.objects.create(
            name='API source', project_type='api', owner=self.user, created_by=self.user,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role='owner', can_edit=True,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.user, spec_name='Source API', status='completed',
        )
        self.endpoint = APIEndpoint.objects.create(
            spec=self.spec, method='GET', path='/health', summary='health',
        )
        self.factory = APIRequestFactory()

    def request(self, method, payload):
        request = getattr(self.factory, method.lower())('/', payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def executable_draft(self, name='health'):
        draft = default_api_workspace_draft()
        draft['config']['name'] = name
        draft['teststeps'] = [{
            'name': name,
            'endpoint_id': self.endpoint.id,
            'request': {'method': 'GET', 'url': '/health'},
            'validate': [{'eq': ['status_code', 200]}],
        }]
        return draft

    def test_manual_endpoint_ignores_spoofed_creation_source_and_serializes_it(self):
        response = APITestCaseListCreateView.as_view()(
            self.request('post', {
                'title': 'manual endpoint', 'description': '', 'test_case_type': 'endpoint',
                'endpoint': self.endpoint.id, 'test_type': 'positive',
                'creation_source': 'ai',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        case = APITestCase.objects.get()
        self.assertEqual(case.creation_source, 'manual')
        self.assertEqual(response.data['data']['creation_source'], 'manual')

    def test_adopt_then_manual_draft_edit_before_first_save_remains_ai(self):
        ai_draft = normalize_draft(self.executable_draft('AI draft'))
        digest = draft_hash(ai_draft)
        workspace = APIWorkspace.objects.create(
            project=self.project,
            owner=self.user,
            spec=self.spec,
            endpoint_ids=[self.endpoint.id],
            draft=default_api_workspace_draft(),
            candidate={'source_revision': 0, 'draft': ai_draft, 'draft_hash': digest},
            generation={
                'status': 'passed', 'source_revision': 0,
                'rounds': [{'draft_hash': digest, 'result': {'success': True}}],
            },
        )

        workspace = update_workspace_draft(workspace, revision=0, draft=ai_draft)
        self.assertEqual(workspace.generation['adopted_revision'], 1)
        manually_edited = deepcopy(workspace.draft)
        manually_edited['teststeps'][0]['name'] = 'manual wording after adoption'
        workspace = update_workspace_draft(workspace, revision=1, draft=manually_edited)
        self.assertEqual(workspace.revision, 2)
        self.assertEqual(workspace.generation['status'], 'stale')

        workspace = create_or_update_case(workspace, revision=2, title=None)
        self.assertEqual(workspace.saved_case.creation_source, 'ai')

    def test_invalid_zero_adoption_is_manual_and_existing_source_survives_overwrite_and_delete(self):
        draft = self.executable_draft()
        zero = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec,
            endpoint_ids=[self.endpoint.id], draft=draft,
            generation={'adopted_revision': 0},
        )
        zero = create_or_update_case(zero, revision=0, title='zero adoption')
        self.assertEqual(zero.saved_case.creation_source, 'manual')

        case = APITestCase.objects.create(
            project=self.project, created_by=self.user, title='existing AI case',
            test_case_type='endpoint', endpoint=self.endpoint, creation_source='ai',
        )
        workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec,
            endpoint_ids=[self.endpoint.id], draft=draft, saved_case=case,
            saved_case_updated_at=case.updated_at,
            generation={'adopted_revision': 1}, revision=1,
        )
        create_or_update_case(workspace, revision=1, title='overwritten')
        case.refresh_from_db()
        self.assertEqual(case.creation_source, 'ai')
        workspace.delete()
        case.refresh_from_db()
        self.assertEqual(case.creation_source, 'ai')
