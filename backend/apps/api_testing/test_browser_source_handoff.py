"""SQLite-safe regression contracts for terminal browser-source handoff."""
import json
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project

from .browser_discovery import _refresh_dependency_candidates, origin_state_file
from .browser_discovery_views import BrowserDiscoveryHandoffView, BrowserDiscoveryOriginsView
from .models import BrowserDiscoveryHandoff, BrowserDiscoveryRecord, BrowserDiscoveryTask


class BrowserDiscoverySourceHandoffTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='source-handoff-owner', password='fixture-only')
        self.project = Project.objects.create(
            name='Browser source handoff', project_type='api', owner=self.owner, created_by=self.owner,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', model_name='source-handoff-offline', created_by=self.owner,
            is_active=True,
        )
        self.factory = APIRequestFactory()

    def _post(self, view, task, payload):
        request = self.factory.post('/', payload, format='json')
        force_authenticate(request, self.owner)
        return view.as_view()(request, project_id=self.project.id, task_id=task.id)

    @staticmethod
    def _summary(*, path, response=None, request=None):
        summary = {'source_authorized': True, 'capture_complete': True, 'path': path}
        if response is not None:
            summary['observed_response'] = response
        if request is not None:
            summary['observed_request'] = request
        return summary

    def test_terminal_selection_rebuilds_handoff_closure_and_locks_origin(self):
        selected_origin = 'https://api.example.test'
        other_origin = 'https://other.example.test'
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            task = BrowserDiscoveryTask.objects.create(
                project=self.project, owner=self.owner, model_id=self.model.id, task_id=str(uuid.uuid4()),
                target_url='https://shop.example.test/app', description='创建并更新本轮商品',
                status=BrowserDiscoveryTask.Status.PARTIAL, version=7, source_version=7,
                limits={'origin_mode': 'auto', 'max_requests': 20, 'max_body_bytes': 4096, 'max_total_bytes': 16384},
            )
            origin_state_file(task).parent.mkdir(parents=True)
            origin_state_file(task).write_text(json.dumps({
                'version': 1, 'resolved_origins': [selected_origin, other_origin],
                'pending': [], 'rejected_origins': [],
            }), encoding='utf-8')
            auth = BrowserDiscoveryRecord.objects.create(
                task=task, sequence=1, request_id='auth', origin=selected_origin, method='POST', path='/sessions',
                resource_type='fetch', content_type='application/json', is_eligible=False,
                public_summary=self._summary(path='/sessions', response={
                    'auth_hints': [{'name': 'set-cookie'}], 'body': {'token': '<redacted>'},
                }),
            )
            create = BrowserDiscoveryRecord.objects.create(
                task=task, sequence=2, request_id='create', origin=selected_origin, method='POST', path='/items',
                resource_type='fetch', content_type='application/json', is_eligible=False,
                public_summary=self._summary(path='/items',
                    request={'auth_hints': [{'name': 'authorization'}]},
                    response={'body': {'data': {'id': 'item-123'}}},
                ),
            )
            query = BrowserDiscoveryRecord.objects.create(
                task=task, sequence=3, request_id='query', origin=selected_origin, method='GET', path='/items/item-123',
                resource_type='fetch', content_type='application/json', is_eligible=False,
                public_summary=self._summary(path='/items/item-123',
                    request={'auth_hints': [{'name': 'authorization'}]},
                    response={'body': {'data': {'id': 'item-123'}}},
                ),
            )
            update = BrowserDiscoveryRecord.objects.create(
                task=task, sequence=4, request_id='update', origin=selected_origin, method='PATCH', path='/items/item-123',
                resource_type='fetch', content_type='application/json', is_eligible=False,
                public_summary=self._summary(path='/items/item-123',
                    request={'auth_hints': [{'name': 'authorization'}], 'json': {'id': 'item-123', 'name': 'changed'}},
                    response={'body': {'data': {'id': 'item-123'}}},
                ),
            )
            BrowserDiscoveryRecord.objects.create(
                task=task, sequence=5, request_id='other', origin=other_origin, method='GET', path='/unrelated',
                resource_type='fetch', content_type='application/json', is_eligible=False,
                public_summary=self._summary(path='/unrelated', response={'body': {'data': {'id': 'other-456'}}}),
            )

            with patch(
                'api_testing.browser_discovery._refresh_dependency_candidates',
                wraps=_refresh_dependency_candidates,
            ) as rebuild:
                selected = self._post(BrowserDiscoveryOriginsView, task, {
                    'version': task.version, 'origin': selected_origin, 'decision': 'select',
                })
            self.assertEqual(selected.status_code, 200, selected.data)
            rebuild.assert_called_once_with(task)
            task.refresh_from_db()
            update.refresh_from_db()
            query.refresh_from_db()
            self.assertEqual(task.api_origin, selected_origin)
            self.assertEqual(task.limits['selected_origin'], selected_origin)
            self.assertEqual((task.version, task.source_version), (7, 7))
            self.assertEqual(update.dependency_record_ids, [auth.id, create.id, query.id])
            self.assertEqual(query.dependency_record_ids, [auth.id, create.id])

            with patch('api_testing.browser_discovery._refresh_dependency_candidates') as rebuild:
                same_origin = self._post(BrowserDiscoveryOriginsView, task, {
                    'version': task.version, 'origin': selected_origin, 'decision': 'select',
                })
            self.assertEqual(same_origin.status_code, 200, same_origin.data)
            rebuild.assert_not_called()
            stale_other_origin = self._post(BrowserDiscoveryOriginsView, task, {
                'version': task.version, 'origin': other_origin, 'decision': 'select',
            })
            self.assertEqual(stale_other_origin.status_code, 409, stale_other_origin.data)
            task.refresh_from_db()
            self.assertEqual((task.api_origin, task.version, task.source_version), (selected_origin, 7, 7))

            handoff_reply = self._post(BrowserDiscoveryHandoffView, task, {
                'version': task.version, 'record_ids': [update.id],
            })
            self.assertEqual(handoff_reply.status_code, 201, handoff_reply.data)
            handoff = BrowserDiscoveryHandoff.objects.select_related('spec').get(task=task)
            capture = handoff.spec.metadata['browser_capture']
            self.assertEqual(capture['selected_record_ids'], [auth.id, create.id, query.id, update.id])
            self.assertEqual(capture['dependency_candidates'][str(update.id)], [auth.id, create.id, query.id])
            update_sample = next(
                sample for sample in capture['observed_samples']
                if sample.get('observed_request', {}).get('json', {}).get('name') == 'changed'
            )
            self.assertEqual(update_sample['path_template']['exact_path'], '/items/item-123')
            self.assertEqual(update_sample['path_template']['template'], f'/items/${{browser_dep_{create.id}_data_id}}')
