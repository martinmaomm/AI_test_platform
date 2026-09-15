"""Offline contracts for immutable WebUI case creation provenance."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .models import WebUIScriptAssistant, WebUIScriptGeneration, WebUITestCase
from .script_assistant import apply_repair, candidate_hash, case_edit_version
from .serializers import WebUITestCaseDetailSerializer
from .views import (
    WebUIScriptGenerationDetailView,
    WebUIScriptGenerationSaveView,
    WebUITestCaseListCreateView,
)


SCRIPT = '''\
from playwright.async_api import expect

async def run(page):
    await page.goto('https://web.example.test/items')
    await expect(page.locator('main')).to_be_visible()
'''


class WebUICaseCreationSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='web-source-owner')
        self.project = Project.objects.create(
            name='Web source', project_type='web', owner=self.user, created_by=self.user,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role='owner', can_edit=True, can_delete=True,
        )
        self.factory = APIRequestFactory()

    def request(self, method, payload):
        request = getattr(self.factory, method.lower())('/', payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def test_manual_create_ignores_creation_and_script_source_spoofing(self):
        response = WebUITestCaseListCreateView.as_view()(
            self.request('post', {
                'title': 'manual case', 'description': 'manual',
                'test_script_content': SCRIPT,
                'creation_source': 'ai', 'script_source': 'mcp_exploration',
            }),
            project_id=self.project.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        case = WebUITestCase.objects.get()
        self.assertEqual(case.creation_source, 'manual')
        self.assertEqual(case.script_source, 'manual')
        self.assertEqual(response.data['data']['creation_source'], 'manual')

    def test_generated_case_stays_ai_after_manual_edit_and_generation_delete(self):
        generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.user,
            status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
            target_url='https://web.example.test/items',
            description_safe='generated case', script_draft=SCRIPT,
            exploration_snapshot={'finalization': {'status': 'valid'}},
        )
        response = WebUIScriptGenerationSaveView.as_view()(
            self.request('post', {'mode': 'draft', 'expected_revision': 0}),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        case = WebUITestCase.objects.get(pk=response.data['data']['test_case_id'])
        self.assertEqual(case.creation_source, 'ai')

        serializer = WebUITestCaseDetailSerializer(
            case,
            data={'test_script_content': SCRIPT + '\n# manual edit\n', 'creation_source': 'manual'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        case.refresh_from_db()
        self.assertEqual(case.script_source, 'manual')
        self.assertEqual(case.creation_source, 'ai')

        generation.refresh_from_db()
        deleted = WebUIScriptGenerationDetailView.as_view()(
            self.request('delete', {
                'confirmed': True, 'expected_updated_at': generation.updated_at.isoformat(),
            }),
            project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(deleted.status_code, 200, deleted.data)
        case.refresh_from_db()
        self.assertEqual(case.creation_source, 'ai')

    def test_assistant_repair_adoption_preserves_manual_creation_source(self):
        case = WebUITestCase.objects.create(
            title='manual repair target', description='manual', user=self.user,
            project=self.project, test_script_content=SCRIPT,
            script_source='manual', script_status='ready', creation_source='manual',
        )
        repaired = SCRIPT + '\n# AI repair\n'
        edit_version = case_edit_version(case)
        assistant = WebUIScriptAssistant.objects.create(
            project=self.project, user=self.user, test_case=case,
            mode=WebUIScriptAssistant.Mode.REPAIR, model_config_id=1,
            status=WebUIScriptAssistant.Status.CANDIDATE_READY,
            source_script=case.test_script_content,
            source_script_version=case.script_version,
            source_edit_version=edit_version,
            candidate_script=repaired,
            candidate_hash=candidate_hash(repaired),
        )
        apply_repair(
            assistant,
            expected_edit_version=edit_version,
            expected_hash=assistant.candidate_hash,
            expected_revision=assistant.revision,
        )
        case.refresh_from_db()
        self.assertEqual(case.creation_source, 'manual')
        self.assertEqual(case.script_source, 'manual')
        self.assertIn('# AI repair', case.test_script_content)
