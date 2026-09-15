"""Offline deletion contract: ownership, active runs, stale versions and assets."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .generation_deletion import DELETABLE_GENERATION_STATUSES
from .models import (
    WebUIScriptGeneration, WebUITestCase, WebUITestExecution,
    WebUITestCaseExecutionDetail,
)
from .views import WebUIScriptGenerationCreateView, WebUIScriptGenerationDetailView


class WebUIScriptGenerationDeletionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username='delete-owner', email='delete-owner@example.test',
        )
        self.member = get_user_model().objects.create_user(
            username='delete-member', email='delete-member@example.test',
        )
        self.outsider = get_user_model().objects.create_user(
            username='delete-outsider', email='delete-outsider@example.test',
        )
        self.project = Project.objects.create(
            name='Deletion project', project_type='web',
            owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.owner, role='owner',
            can_edit=True, can_delete=True, can_execute_tests=True,
            can_view_reports=True,
        )
        self.membership = ProjectMember.objects.create(
            project=self.project, user=self.member, role='editor',
            can_edit=True, can_delete=True,
        )
        self.factory = APIRequestFactory()

    def generation(self, **overrides):
        values = dict(
            project=self.project, user=self.owner,
            status=WebUIScriptGeneration.Status.READY,
            target_url='https://fixture.example.test/', script_draft='# draft',
        )
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def delete(self, generation, *, user=None, payload=None, project_id=None):
        request = self.factory.delete('/script-generations/', payload if payload is not None else {
            'confirmed': True, 'expected_updated_at': generation.updated_at.isoformat(),
        }, format='json')
        if user is not False:
            force_authenticate(request, user=user or self.owner)
        return WebUIScriptGenerationDetailView.as_view()(
            request, project_id=project_id or self.project.id, generation_id=generation.id,
        )

    def history(self, user=None):
        request = self.factory.get('/script-generations/')
        force_authenticate(request, user=user or self.owner)
        return WebUIScriptGenerationCreateView.as_view()(request, project_id=self.project.id)

    def test_each_finished_or_paused_status_can_be_deleted(self):
        for status in DELETABLE_GENERATION_STATUSES:
            with self.subTest(status=status):
                generation = self.generation(status=status)
                response = self.delete(generation)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data, {'success': True, 'data': {'id': str(generation.id)}})
                self.assertFalse(WebUIScriptGeneration.objects.filter(pk=generation.id).exists())

    def test_active_and_unknown_generation_statuses_cannot_be_deleted(self):
        statuses = set(WebUIScriptGeneration.Status.values) - DELETABLE_GENERATION_STATUSES
        for status in statuses | {'unknown'}:
            with self.subTest(status=status):
                generation = self.generation(status=status)
                response = self.delete(generation)
                self.assertEqual(response.status_code, 409, response.data)
                self.assertTrue(WebUIScriptGeneration.objects.filter(pk=generation.id).exists())

    def test_pending_or_running_debug_and_repair_block_deletion_even_after_cancel(self):
        for field in ('verification', 'repair'):
            for busy_status in ('pending', 'running'):
                with self.subTest(field=field, status=busy_status):
                    generation = self.generation(
                        status=WebUIScriptGeneration.Status.CANCELLED,
                        workspace={field: {'status': busy_status}},
                    )
                    response = self.delete(generation)
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertTrue(WebUIScriptGeneration.objects.filter(pk=generation.id).exists())

    def test_non_running_repair_candidate_may_be_discarded_with_record(self):
        generation = self.generation(workspace={
            'verification': {'status': 'failed'},
            'repair': {'status': 'candidate_ready', 'candidate_script': '# candidate'},
        })
        self.assertEqual(self.delete(generation).status_code, 200)

    def test_requires_explicit_confirmation_and_valid_version(self):
        generation = self.generation()
        for payload in (
            {}, {'confirmed': True},
            {'confirmed': True, 'expected_updated_at': 'bad-date'},
            {'confirmed': True, 'expected_updated_at': None},
            *({'confirmed': value, 'expected_updated_at': generation.updated_at.isoformat()}
              for value in (False, 'true', 1, None)),
            [],
        ):
            with self.subTest(payload=payload):
                response = self.delete(generation, payload=payload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertTrue(WebUIScriptGeneration.objects.filter(pk=generation.id).exists())

    def test_changes_after_confirmation_preserve_new_draft(self):
        generation = self.generation()
        WebUIScriptGeneration.objects.filter(pk=generation.id).update(
            script_draft='# a newer draft', updated_at=timezone.now() + timedelta(seconds=1),
        )
        response = self.delete(generation)
        self.assertEqual(response.status_code, 409, response.data)
        generation.refresh_from_db()
        self.assertEqual(generation.script_draft, '# a newer draft')
        self.assertEqual(self.delete(generation).status_code, 200)

    def test_starting_a_new_run_after_confirmation_blocks_deletion(self):
        generation = self.generation()
        WebUIScriptGeneration.objects.filter(pk=generation.id).update(status='exploring')
        response = self.delete(generation)
        self.assertEqual(response.status_code, 409, response.data)

    def test_deleted_record_cannot_be_restored_and_repeated_delete_is_404(self):
        generation = self.generation()
        self.assertEqual(self.delete(generation).status_code, 200)
        self.assertEqual(self.delete(generation).status_code, 404)
        request = self.factory.get('/script-generations/')
        force_authenticate(request, user=self.owner)
        response = WebUIScriptGenerationDetailView.as_view()(
            request, project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.history().data['data']['total'], 0)

    def test_authentication_capability_ownership_and_project_boundaries(self):
        generation = self.generation(user=self.member)
        self.assertEqual(self.delete(generation, user=False).status_code, 401)
        self.assertEqual(self.delete(generation, user=self.outsider).status_code, 404)
        self.membership.can_delete = False
        self.membership.save()
        self.assertEqual(self.delete(generation, user=self.member).status_code, 403)
        self.membership.can_delete = True
        self.membership.save()
        other_record = self.generation()
        self.assertEqual(self.delete(other_record, user=self.member).status_code, 403)
        other_project = Project.objects.create(
            name='Another project', project_type='web', owner=self.owner, created_by=self.owner,
        )
        self.assertEqual(self.delete(generation, project_id=other_project.id).status_code, 404)
        self.assertEqual(self.delete(generation, user=self.member).status_code, 200)

    def test_non_creator_admin_without_membership_can_read_and_delete_own_generation(self):
        admin = get_user_model().objects.create_user(
            username='delete-admin', email='delete-admin@example.test', is_staff=True,
        )
        generation = self.generation(user=admin)

        history = self.history(admin)
        self.assertEqual(history.status_code, 200, history.data)
        self.assertTrue(history.data['data']['items'][0]['can_delete'])

        request = self.factory.get('/script-generations/')
        force_authenticate(request, user=admin)
        detail = WebUIScriptGenerationDetailView.as_view()(
            request, project_id=self.project.id, generation_id=generation.id,
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(self.delete(generation, user=admin).status_code, 200)

    def test_demoted_project_creator_cannot_access_another_users_private_draft(self):
        creator = get_user_model().objects.create_user(
            username='demoted-creator',
            email='demoted-creator@example.test',
            is_staff=True,
        )
        author = get_user_model().objects.create_user(
            username='private-draft-author',
            email='private-draft-author@example.test',
        )
        project = Project.objects.create(
            name='Demoted creator project', project_type='web',
            owner=creator, created_by=creator,
        )
        ProjectMember.objects.create(
            project=project, user=creator, role='owner',
            can_edit=True, can_delete=True, can_execute_tests=True,
            can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=project, user=author, role='editor', can_edit=True,
        )
        generation = self.generation(project=project, user=author)
        creator.is_staff = False
        creator.save(update_fields=['is_staff'])

        request = self.factory.get('/script-generations/')
        force_authenticate(request, user=creator)
        detail = WebUIScriptGenerationDetailView.as_view()(
            request, project_id=project.id, generation_id=generation.id,
        )
        self.assertEqual(detail.status_code, 403, detail.data)
        self.assertEqual(
            self.delete(generation, user=creator, project_id=project.id).status_code,
            403,
        )

    def test_only_generation_deleted_saved_case_execution_detail_and_files_survive(self):
        case = WebUITestCase.objects.create(
            title='Saved case', user=self.owner, project=self.project, description='fixture',
        )
        sibling = self.generation(test_case=case)
        with TemporaryDirectory() as directory:
            artifact = Path(directory) / 'report.log'
            artifact.write_text('execution evidence', encoding='utf-8')
            execution = WebUITestExecution.objects.create(
                name='Saved run', exec_type='case', status='passed', executor=self.owner,
                project=self.project, log_path=str(artifact),
            )
            detail = WebUITestCaseExecutionDetail.objects.create(
                execution=execution, test_case=case, source_script='# executed script',
                screenshot_path=str(artifact), log='saved logs',
            )
            generation = self.generation(test_case=case, workspace={
                'verification': {'status': 'passed', 'execution_id': execution.id},
                'trace_path': str(artifact),
            })
            response = self.delete(generation)
            self.assertEqual(response.status_code, 200, response.data)
            for model, pk in (
                (WebUITestCase, case.pk), (WebUITestExecution, execution.pk),
                (WebUITestCaseExecutionDetail, detail.pk), (WebUIScriptGeneration, sibling.pk),
            ):
                self.assertTrue(model.objects.filter(pk=pk).exists())
            detail.refresh_from_db()
            self.assertEqual(detail.source_script, '# executed script')
            self.assertEqual(detail.log, 'saved logs')
            self.assertEqual(artifact.read_text(encoding='utf-8'), 'execution evidence')

    def test_history_reports_deletion_policy_without_loading_workspaces_per_row(self):
        ready = self.generation()
        busy = self.generation(workspace={'verification': {'status': 'running'}})
        repair = self.generation(workspace={'repair': {'status': 'pending'}})
        active = self.generation(status='exploring')
        # Project access and record-delete capability each check membership;
        # the history itself remains one count plus one page query.
        with self.assertNumQueries(5):
            response = self.history()
        self.assertEqual(response.status_code, 200, response.data)
        items = {item['id']: item for item in response.data['data']['items']}
        self.assertTrue(items[str(ready.id)]['can_delete'])
        self.assertEqual(items[str(ready.id)]['delete_block_reason'], '')
        for record in (busy, repair, active):
            self.assertFalse(items[str(record.id)]['can_delete'])
            self.assertTrue(items[str(record.id)]['delete_block_reason'])

    def test_history_hides_delete_capability_for_read_only_members(self):
        self.generation(user=self.member)
        self.membership.can_delete = False
        self.membership.save()
        response = self.history(self.member)
        item = response.data['data']['items'][0]
        self.assertFalse(item['can_delete'])
        self.assertIn('权限', item['delete_block_reason'])

    def test_malformed_workspace_metadata_does_not_break_history_or_delete(self):
        generation = self.generation(workspace={
            'verification': {'status': []}, 'repair': None,
        })
        response = self.history()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['data']['items'][0]['can_delete'])
        self.assertEqual(self.delete(generation).status_code, 200)

    def test_unknown_record_is_404(self):
        generation = self.generation()
        generation.id = uuid4()
        self.assertEqual(self.delete(generation).status_code, 404)
