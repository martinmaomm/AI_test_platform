"""Fresh credentials are required across request locations, not only headers."""
from copy import deepcopy

from django.test import SimpleTestCase

from .workspace_service import WorkspaceValidationError
from .workspace_verification import prepare_candidate


class BrowserDiscoveryCredentialTests(SimpleTestCase):
    endpoint = {'id': 1, 'method': 'POST', 'path': '/protected', 'parameters': [], 'request_body': {},
                'document_context': {'browser_capture': {'observed_samples': []}}}

    def draft(self, extra):
        return {'version': 1, 'config': {'name': 'credential fixture', 'base_url': '', 'variables': {}},
                'teststeps': [{'name': 'request', 'endpoint_id': 1,
                               'request': {'method': 'POST', 'url': '/protected', **extra},
                               'extract': {}, 'validate': [{'eq': ['status_code', 200]}]}]}

    def test_captured_credentials_cannot_hide_in_custom_headers_query_body_or_cookie(self):
        for extra in (
            {'headers': {'X-API-Key': 'captured'}}, {'params': {'access_token': 'captured'}},
            {'json': {'nested': {'refreshToken': 'captured'}}}, {'cookies': {'sid': 'captured'}},
            {'data': {'session_id': 'captured'}}, {'data': [['token', 'captured']]},
            {'data': 'access_token=captured'}, {'url': '/protected?token=captured'},
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(WorkspaceValidationError, '不能固化'):
                prepare_candidate(self.draft(extra), endpoints=[self.endpoint], target_url='https://api.example.test', variables={})

    def test_explicit_user_input_variable_allowed_but_ai_invented_static_token_not(self):
        draft = self.draft({'headers': {'X-API-Key': '${api_key}'}})
        prepare_candidate(draft, endpoints=[self.endpoint], target_url='https://api.example.test', variables={'api_key': 'user-input'})
        draft['config']['variables'] = {'api_key': 'captured'}
        with self.assertRaisesRegex(WorkspaceValidationError, '不能固化'):
            prepare_candidate(draft, endpoints=[self.endpoint], target_url='https://api.example.test', variables={})

    def test_login_password_and_original_swagger_contract_are_not_changed(self):
        prepare_candidate(self.draft({'json': {'username': 'fixture', 'password': 'fixture-only', 'token_count': 3}}),
                          endpoints=[self.endpoint], target_url='https://api.example.test', variables={})
        swagger = deepcopy(self.endpoint)
        swagger['document_context'] = {}
        prepare_candidate(self.draft({'headers': {'X-API-Key': 'explicit-existing-config'}}),
                          endpoints=[swagger], target_url='https://api.example.test', variables={})

    def test_new_session_login_extraction_is_accepted(self):
        draft = self.draft({'json': {'username': 'fixture', 'password': 'fixture-only'}})
        draft['teststeps'][0]['extract'] = {'token': 'body.data.token'}
        draft['teststeps'].append(self.draft({'headers': {'Authorization': 'Bearer ${token}'}})['teststeps'][0])
        prepare_candidate(draft, endpoints=[self.endpoint], target_url='https://api.example.test', variables={})
