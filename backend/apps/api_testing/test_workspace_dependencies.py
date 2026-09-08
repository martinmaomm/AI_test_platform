"""Focused offline contracts for API workspace scenario dependencies."""
from copy import deepcopy

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project
from .models import APIEndpoint, APISpecification, APIWorkspace
from .workspace_service import WorkspaceValidationError, endpoint_specs, generation_endpoint_specs, normalize_draft
from .workspace_verification import assertions_preserved, prepare_candidate, protected_expected_values, step_assertions


class WorkspaceDependencyContractsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='dependency-owner', email='dependency-owner@example.test', password='pw',
        )
        self.project = Project.objects.create(name='Dependency contracts', project_type='api', owner=self.user, created_by=self.user)

    def spec(self, *, security=None, schemes=None, project=None):
        project = project or self.project
        metadata = {
            'security': security or [],
            'components': {'securitySchemes': schemes or {}},
        }
        return APISpecification.objects.create(
            project=project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
            spec_name='Dependency spec', metadata=metadata,
        )

    @staticmethod
    def draft(*steps, variables=None):
        return normalize_draft({
            'version': 1,
            'config': {'name': 'dependency contract', 'base_url': '', 'variables': variables or {}, 'verify': True},
            'teststeps': list(steps),
        })

    def test_explicit_auth_context_respects_negative_unknown_or_and_cookie_session_dependency(self):
        access = {'type': 'apiKey', 'in': 'header', 'name': 'X-Access'}
        spec = self.spec(security=[{'access': []}], schemes={'access': access})
        who = APIEndpoint.objects.create(spec=spec, method='GET', path='/who')
        endpoints = endpoint_specs(self.project.id, [who.id], spec_id=spec.id)
        positive = self.draft({
            'name': 'who', 'endpoint_id': who.id,
            'request': {'method': 'GET', 'url': '/who', 'headers': {'X-Access': '${ticket}'}},
            'validate': [{'eq': ['status_code', 200]}],
        }, variables={'ticket': 'issued'})
        removed_header = deepcopy(positive)
        removed_header['teststeps'][0]['request']['headers'] = {}
        with self.assertRaisesRegex(WorkspaceValidationError, 'security'):
            prepare_candidate(
                removed_header, endpoints=endpoints, target_url='https://example.test', variables={},
                authenticated_target_ids={who.id},
            )
        # A negative unauthenticated scenario deliberately does not claim an
        # authenticated context, even though the endpoint declares security.
        prepare_candidate(
            removed_header, endpoints=endpoints, target_url='https://example.test', variables={},
            authenticated_target_ids=set(),
        )

        unknown_or = self.spec(
            security=[{'access': []}, {'unknown': []}], schemes={'access': access},
        )
        unknown_endpoint = APIEndpoint.objects.create(spec=unknown_or, method='GET', path='/unknown-or')
        prepare_candidate(
            self.draft({
                'name': 'unknown OR', 'endpoint_id': unknown_endpoint.id,
                'request': {'method': 'GET', 'url': '/unknown-or'},
                'validate': [{'eq': ['status_code', 200]}],
            }),
            endpoints=endpoint_specs(self.project.id, [unknown_endpoint.id], spec_id=unknown_or.id),
            target_url='https://example.test', variables={}, authenticated_target_ids={unknown_endpoint.id},
        )

        cookie_spec = self.spec(
            security=[{'session': []}], schemes={'session': {'type': 'apiKey', 'in': 'cookie', 'name': 'sid'}},
        )
        session = APIEndpoint.objects.create(spec=cookie_spec, method='POST', path='/session')
        cookie_target = APIEndpoint.objects.create(spec=cookie_spec, method='GET', path='/profile')
        cookie_endpoints = endpoint_specs(self.project.id, [session.id, cookie_target.id], spec_id=cookie_spec.id)
        cookie_case = self.draft(
            {
                'name': 'establish session', 'endpoint_id': session.id,
                'request': {'method': 'POST', 'url': '/session'}, 'validate': [{'eq': ['status_code', 200]}],
            },
            {
                'name': 'profile', 'endpoint_id': cookie_target.id,
                'request': {'method': 'GET', 'url': '/profile'}, 'validate': [{'eq': ['status_code', 200]}],
            },
        )
        prepare_candidate(
            cookie_case, endpoints=cookie_endpoints, target_url='https://example.test', variables={},
            authenticated_target_ids={cookie_target.id}, cookie_session_dependency_ids={session.id},
        )
        without_dependency = deepcopy(cookie_case)
        without_dependency['teststeps'] = without_dependency['teststeps'][1:]
        with self.assertRaisesRegex(WorkspaceValidationError, 'security'):
            prepare_candidate(
                without_dependency, endpoints=cookie_endpoints, target_url='https://example.test', variables={},
                authenticated_target_ids={cookie_target.id}, cookie_session_dependency_ids={session.id},
            )

    def test_assertion_identity_allows_dependency_prefix_and_path_variable_but_rejects_reorder_or_weaken(self):
        target = self.draft({
            'name': 'target', 'endpoint_id': 10,
            'request': {'method': 'GET', 'url': '/items/42'}, 'validate': [{'eq': ['body.code', 200]}],
        })
        with_dependency = self.draft(
            {
                'name': 'dependency', 'endpoint_id': 9,
                'request': {'method': 'POST', 'url': '/session'}, 'validate': [{'eq': ['status_code', 200]}],
            },
            {
                'name': 'target', 'endpoint_id': 10,
                'request': {'method': 'GET', 'url': '/items/${item_id}'}, 'validate': [{'eq': ['body.code', 200]}],
            },
        )
        self.assertTrue(assertions_preserved(step_assertions(target), protected_expected_values(target), with_dependency))

        ordered = self.draft(
            {'name': 'first', 'endpoint_id': 10, 'request': {'method': 'GET', 'url': '/first'}, 'validate': [{'eq': ['status_code', 200]}]},
            {'name': 'second', 'endpoint_id': 11, 'request': {'method': 'GET', 'url': '/second'}, 'validate': [{'eq': ['body.code', 200]}]},
        )
        reordered = self.draft(
            {'name': 'second', 'endpoint_id': 11, 'request': {'method': 'GET', 'url': '/second'}, 'validate': [{'eq': ['body.code', 200]}]},
            {'name': 'first', 'endpoint_id': 10, 'request': {'method': 'GET', 'url': '/first'}, 'validate': [{'eq': ['status_code', 200]}]},
        )
        weakened = deepcopy(target)
        weakened['teststeps'][0]['validate'] = [{'eq': ['body.code', 401]}]
        self.assertFalse(assertions_preserved(step_assertions(ordered), protected_expected_values(ordered), reordered))
        self.assertFalse(assertions_preserved(step_assertions(target), protected_expected_values(target), weakened))

        variable_target = self.draft({
            'name': 'variable target', 'endpoint_id': 12,
            'request': {'method': 'GET', 'url': '/variable'}, 'validate': [{'eq': ['body.code', '${expected}']}],
        }, variables={'expected': 200})
        rewritten_expected = deepcopy(variable_target)
        rewritten_expected['config']['variables']['expected'] = 401
        self.assertFalse(assertions_preserved(
            step_assertions(variable_target), protected_expected_values(variable_target), rewritten_expected,
        ))

    def test_child_scope_is_frozen_and_rejects_current_root_or_identity_mismatches(self):
        spec = self.spec()
        login = APIEndpoint.objects.create(spec=spec, method='POST', path='/session')
        target = APIEndpoint.objects.create(spec=spec, method='GET', path='/who')
        extra = APIEndpoint.objects.create(spec=spec, method='GET', path='/extra')
        root = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=spec, endpoint_ids=[login.id, target.id],
        )
        child = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=root, spec=spec, endpoint_ids=[target.id],
            generation={'_snapshot': {'scope_endpoint_ids': [login.id, target.id]}},
        )
        self.assertEqual(
            [item['id'] for item in generation_endpoint_specs(child)], [login.id, target.id],
        )
        root.endpoint_ids = [login.id, target.id, extra.id]
        root.save(update_fields=['endpoint_ids', 'updated_at'])
        self.assertEqual(
            [item['id'] for item in generation_endpoint_specs(child)], [login.id, target.id],
        )
        root.endpoint_ids = [target.id]
        root.save(update_fields=['endpoint_ids', 'updated_at'])
        with self.assertRaisesRegex(WorkspaceValidationError, '缩小|变更'):
            generation_endpoint_specs(child)
        root.endpoint_ids = [login.id, target.id]
        root.generation = {'status': 'stale'}
        root.save(update_fields=['endpoint_ids', 'generation', 'updated_at'])
        with self.assertRaisesRegex(WorkspaceValidationError, '重新生成'):
            generation_endpoint_specs(child)

        other_user = get_user_model().objects.create_user(
            username='dependency-other', email='dependency-other@example.test', password='pw',
        )
        owner_mismatch = APIWorkspace.objects.create(
            project=self.project, owner=other_user, parent=root, spec=spec,
            generation={'_snapshot': {'scope_endpoint_ids': [target.id]}},
        )
        with self.assertRaisesRegex(WorkspaceValidationError, '归属不一致'):
            generation_endpoint_specs(owner_mismatch)

        other_project = Project.objects.create(name='Other dependency project', project_type='api', owner=self.user, created_by=self.user)
        other_spec = self.spec(project=other_project)
        project_mismatch = APIWorkspace.objects.create(
            project=other_project, owner=self.user, parent=root, spec=other_spec,
            generation={'_snapshot': {'scope_endpoint_ids': []}},
        )
        with self.assertRaisesRegex(WorkspaceValidationError, '归属不一致'):
            generation_endpoint_specs(project_mismatch)

        spec_mismatch = APIWorkspace.objects.create(
            project=self.project, owner=self.user, parent=root, spec=APISpecification.objects.create(
                project=self.project, created_by=self.user, status=APISpecification.TaskStatus.COMPLETED,
                spec_name='Other dependency spec',
            ), generation={'_snapshot': {'scope_endpoint_ids': [target.id]}},
        )
        with self.assertRaisesRegex(WorkspaceValidationError, '规范已变化'):
            generation_endpoint_specs(spec_mismatch)
