"""Long-lived provenance contracts for browser-captured API dependencies."""
from types import SimpleNamespace
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from .browser_discovery import _path_template, _refresh_dependency_candidates, _same_resource_lineage, _selection
from .models import BrowserDiscoveryRecord, BrowserDiscoveryTask
from .requests_runtime import ExtractionFailure, _select
from .workspace_service import WorkspaceValidationError
from .workspace_verification import (
    _browser_capture_bindings_match,
    _browser_capture_path_matches,
    prepare_candidate,
)


def _record(record_id, sequence, method, path, body, dependencies=()):
    return SimpleNamespace(
        id=record_id,
        sequence=sequence,
        method=method,
        path=path,
        dependency_record_ids=list(dependencies),
        public_summary={'observed_response': {'body': body}},
    )


class BrowserDiscoveryDependencyContractsTests(TestCase):
    def test_native_id_is_a_resource_identity_but_customer_id_is_not(self):
        source = _record(1, 1, 'POST', '/orders/create', {'data': {'id': 42, 'customer_id': 42}})
        object_read = _record(2, 2, 'GET', '/orders/42', {'data': {'id': 42}})
        update = _record(3, 3, 'POST', '/orders/update/42', {}, [1, 2])

        template = _path_template(update, {1: source, 2: object_read, 3: update})

        self.assertEqual(template['template'], '/orders/update/${browser_dep_1_data_id}')
        self.assertEqual(template['slots'][0]['source_field'], 'data.id')
        self.assertEqual(template['slots'][0]['sources'][0], {
            'record_id': 1, 'method': 'POST', 'path': '/orders/create', 'field': 'data.id',
        })
        self.assertEqual({source['field'] for source in template['slots'][0]['sources']}, {'data.id'})

    def test_static_zero_and_nested_cross_resource_same_id_are_not_templates(self):
        menu_list = _record(1, 1, 'GET', '/menu/list/0', {
            'data': {'list': [{'id': 70, 'parentId': 0, 'level': 0, 'sort': 0}]},
        })
        static_root = _record(2, 2, 'GET', '/menu/list/0', {}, [1])
        product_update = _record(3, 2, 'POST', '/products/update/70', {}, [1])

        self.assertIsNone(_path_template(static_root, {1: menu_list, 2: static_root}))
        self.assertIsNone(_path_template(product_update, {1: menu_list, 3: product_update}))
        # An observed users object must not turn the shared /admin namespace
        # into an anchor for products.
        self.assertFalse(_same_resource_lineage(
            '/api/admin/users/list/0', '/api/admin/products/update/70', anchors={('admin', 'users')},
        ))

    def test_filtered_unique_array_selector_matches_captured_array_field(self):
        source = _record(1, 1, 'GET', '/menu/list/0', {'data': {'list': [{'id': 70}]}})
        # Sibling list/update routes become a resource family only because a
        # separate observed object URL proves the /menu anchor.
        object_read = _record(2, 2, 'GET', '/menu/70', {'data': {'id': 70}})
        update = _record(3, 3, 'POST', '/menu/update/70', {}, [1, 2])
        template = _path_template(update, {1: source, 2: object_read, 3: update})
        endpoint = {
            'id': 20, 'method': 'POST', 'path': '/menu/update/70',
            'document_context': {'browser_capture': {'observed_samples': [{'path_template': template}]}},
        }
        endpoints = {10: {'method': 'GET', 'path': '/menu/list/0'}, 20: endpoint}

        self.assertTrue(_browser_capture_path_matches(
            endpoint, '/menu/update/${menu_id}',
            extracted_by_endpoint={10: {'menu_id': 'body.data.list[?(@.title == ${menu_title})][0].id'}},
            completed_endpoint_ids={10}, endpoints=endpoints,
        ))

    def test_latest_extraction_definition_revokes_old_browser_path_source(self):
        template = {
            'exact_path': '/menu/update/70',
            'template': '/menu/update/${browser_dep_1_data_list_0_id}',
            'slots': [{'segment_index': 3, 'sources': [
                {'method': 'GET', 'path': '/menu/list/0', 'field': 'data.list[0].id'},
            ]}],
        }
        endpoints = [
            {'id': 1, 'method': 'GET', 'path': '/menu/list/0', 'document_context': {}},
            {'id': 2, 'method': 'GET', 'path': '/users/current', 'document_context': {}},
            {'id': 3, 'method': 'POST', 'path': '/menu/update/70',
             'document_context': {'browser_capture': {'observed_samples': [{'path_template': template}]}}},
        ]
        check = [{'eq': ['status_code', 200]}]
        draft = {
            'version': 1,
            'config': {'name': 'latest extraction', 'base_url': '', 'variables': {'menu_title': 'target'}, 'verify': True},
            'teststeps': [
                {'name': 'list', 'endpoint_id': 1, 'request': {'method': 'GET', 'url': '/menu/list/0'},
                 'extract': {'menu_id': 'body.data.list[?(@.title == ${menu_title})][0].id'}, 'validate': check},
                {'name': 'redefine', 'endpoint_id': 2, 'request': {'method': 'GET', 'url': '/users/current'},
                 'extract': {'menu_id': 'body.data.id'}, 'validate': check},
                {'name': 'update', 'endpoint_id': 3, 'request': {'method': 'POST', 'url': '/menu/update/${menu_id}'},
                 'extract': {}, 'validate': check},
            ],
        }

        with self.assertRaisesRegex(WorkspaceValidationError, '方法或路径'):
            prepare_candidate(draft, endpoints=endpoints, target_url='https://api.example.test', variables={})

    def test_runtime_unique_filter_guard_rejects_multiple_actual_matches(self):
        context = {'body': {'data': {'list': [
            {'title': 'same', 'id': 1}, {'title': 'same', 'id': 2},
        ]}}}

        with self.assertRaises(ExtractionFailure):
            _select(
                'body.data.list[?(@.title == ${menu_title})][0].id', context,
                variables={'menu_title': 'same'}, require_unique=True,
            )

    def test_body_and_query_identity_bindings_reject_fixed_capture_values(self):
        source = {'id': 10, 'method': 'GET', 'path': '/menu/list/0'}
        endpoint = {
            'id': 20, 'method': 'POST', 'path': '/menu/update/70',
            'document_context': {'browser_capture': {'observed_samples': [{'dependency_bindings': [
                {'location': 'json.id', 'sources': [{'method': 'GET', 'path': '/menu/list/0', 'field': 'data.list[0].id'}]},
                {'location': 'params.menu_id', 'sources': [{'method': 'GET', 'path': '/menu/list/0', 'field': 'data.list[0].id'}]},
            ]}]}},
        }
        endpoints = {10: source, 20: endpoint}
        extracted = {10: {'menu_id': 'body.data.list[?(@.title == ${menu_title})][0].id'}}

        self.assertTrue(_browser_capture_bindings_match(
            endpoint, {'json': {'id': '${menu_id}', 'parentId': 0}, 'params': {'menu_id': '${menu_id}'}},
            extracted_by_endpoint=extracted, completed_endpoint_ids={10}, endpoints=endpoints,
        ))
        self.assertFalse(_browser_capture_bindings_match(
            endpoint, {'json': {'id': 70, 'parentId': 0}, 'params': {'menu_id': '${menu_id}'}},
            extracted_by_endpoint=extracted, completed_endpoint_ids={10}, endpoints=endpoints,
        ))
        self.assertFalse(_browser_capture_bindings_match(
            endpoint, {'json': {'id': '${menu_id}', 'parentId': 0}, 'params': {'menu_id': 70}},
            extracted_by_endpoint=extracted, completed_endpoint_ids={10}, endpoints=endpoints,
        ))

    def test_selecting_business_record_adds_observed_authentication_prerequisite(self):
        owner = get_user_model().objects.create_user(username='dependency-owner', password='pw')
        project = Project.objects.create(name='Dependency auth', project_type='api', owner=owner, created_by=owner)
        task = BrowserDiscoveryTask.objects.create(
            project=project, owner=owner, model_id=1, task_id=str(uuid.uuid4()),
            target_url='https://shop.example.test/app', api_origin='https://api.example.test',
            description='dependency test', allow_test_data_writes=True,
            limits={'max_requests': 10, 'max_body_bytes': 1024, 'max_total_bytes': 4096},
        )
        authentication = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, request_id='auth', method='POST', path='/session/start',
            is_eligible=True, public_summary={
                'observed_response': {'auth_hints': [{'name': 'set-cookie'}], 'body': {'token': '<redacted>'}},
            },
        )
        business = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=2, request_id='menu-update', method='POST', path='/menu/update/70',
            is_eligible=True, public_summary={'observed_request': {'auth_hints': [{'name': 'authorization'}]}},
        )

        _refresh_dependency_candidates(task)
        business.refresh_from_db()
        selected, _selection_hash = _selection(task, [business.id])

        self.assertIn(authentication.id, business.dependency_record_ids)
        self.assertEqual([record.id for record in selected], [authentication.id, business.id])
