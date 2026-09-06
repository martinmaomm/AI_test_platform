import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api_testing.api_parser_service import APIParserService


class APIParserContractTests(unittest.TestCase):
    def _extract(self, specification):
        parser = APIParserService()
        parser.parsed_spec = specification
        return parser._extract_structured_data(specification)

    def test_swagger_parameters_inherit_override_and_preserve_form_contract(self):
        specification = {
            'swagger': '2.0',
            'info': {'title': 'Contract', 'version': '1.0'},
            'paths': {
                '/records': {
                    'parameters': [
                        {
                            'name': 'limit', 'in': 'query', 'type': 'integer',
                            'minimum': 1, 'maximum': 100,
                        },
                        {
                            'name': 'filter', 'in': 'query', 'type': 'string',
                            'minLength': 2, 'maxLength': 20, 'pattern': '^[a-z]+$',
                        },
                    ],
                    'post': {
                        'consumes': ['application/x-www-form-urlencoded'],
                        'produces': ['application/problem+json'],
                        'security': [],
                        'parameters': [
                            {
                                'name': 'limit', 'in': 'query', 'type': 'integer',
                                'minimum': 2, 'maximum': 50, 'default': 10,
                            },
                            {
                                'name': 'enabled', 'in': 'formData', 'type': 'boolean',
                                'required': True, 'default': False,
                            },
                        ],
                        'responses': {
                            '200': {
                                'description': 'accepted',
                                'schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}},
                                'examples': {'application/problem+json': False},
                            },
                            '201': {
                                'description': 'created',
                                'schema': {'type': 'integer', 'minimum': 0},
                            },
                        },
                    },
                },
                '/document': {
                    'post': {
                        'parameters': [
                            {
                                'name': 'document', 'in': 'body', 'required': True,
                                'schema': {
                                    'type': 'object',
                                    'properties': {'revision': {'type': 'integer', 'minimum': 0}},
                                },
                            },
                        ],
                        'responses': {'204': {'description': 'saved'}},
                    },
                },
            },
            'consumes': ['application/json'],
            'produces': ['application/json'],
            'security': [{'api_key': []}],
        }

        endpoints = self._extract(specification)['endpoints']
        endpoint = next(item for item in endpoints if item['path'] == '/records')
        document = next(item for item in endpoints if item['path'] == '/document')
        parameters = {(item['in'], item['name']): item for item in endpoint['parameters']}

        self.assertEqual(set(parameters), {('query', 'limit'), ('query', 'filter'), ('formData', 'enabled')})
        self.assertEqual(parameters[('query', 'limit')]['schema']['minimum'], 2)
        self.assertEqual(parameters[('query', 'limit')]['schema']['maximum'], 50)
        self.assertEqual(parameters[('query', 'filter')]['schema']['minLength'], 2)
        self.assertEqual(parameters[('query', 'filter')]['schema']['pattern'], '^[a-z]+$')

        request_body = endpoint['request_body']
        self.assertEqual(set(request_body['content']), {'application/x-www-form-urlencoded'})
        self.assertNotIn('application/json', request_body['content'])
        self.assertEqual(request_body['content']['application/x-www-form-urlencoded']['schema']['properties']['enabled']['type'], 'boolean')
        self.assertEqual(request_body['x-aits-operation'], {
            'consumes': ['application/x-www-form-urlencoded'],
            'produces': ['application/problem+json'],
            'security': [],
        })

        response_content = endpoint['responses']['200']['content']['application/problem+json']
        self.assertIn('example', response_content)
        self.assertIs(response_content['example'], False)
        self.assertNotIn('example', endpoint['responses']['201']['content']['application/problem+json'])
        self.assertEqual(set(document['request_body']['content']), {'application/json'})
        self.assertEqual(
            document['request_body']['content']['application/json']['schema']['properties']['revision']['minimum'],
            0,
        )
        self.assertEqual(document['request_body']['x-aits-operation']['security'], [{'api_key': []}])

    def test_openapi_composition_examples_and_local_ref_bounds_are_preserved(self):
        specification = {
            'openapi': '3.0.3',
            'info': {'title': 'Contract', 'version': '1.0'},
            'security': [{'oauth': ['read']}],
            'components': {
                'parameters': {
                    'Trace': {
                        'name': 'trace-id', 'in': 'header', 'required': False,
                        'schema': {'type': 'integer', 'minimum': 0}, 'example': 0,
                    },
                },
                'requestBodies': {
                    'Upload': {
                        'required': True,
                        'content': {
                            'multipart/form-data': {
                                'schema': {'$ref': '#/components/schemas/Pet'},
                                'example': [],
                            },
                        },
                    },
                },
                'responses': {
                    'Saved': {
                        'description': 'saved',
                        'content': {
                            'application/json': {
                                'schema': {'$ref': '#/components/schemas/Pet'},
                                'example': None,
                            },
                        },
                    },
                },
                'schemas': {
                    'BasePet': {
                        'type': 'object',
                        'properties': {'id': {'type': 'integer', 'minimum': 0}},
                    },
                    'Pet': {
                        'allOf': [
                            {'$ref': '#/components/schemas/BasePet'},
                            {
                                'type': 'object',
                                'properties': {
                                    'kind': {
                                        'oneOf': [
                                            {'type': 'string', 'enum': ['cat']},
                                            {'type': 'integer', 'minimum': 1},
                                        ],
                                    },
                                    'owner': {
                                        'anyOf': [
                                            {'type': 'string', 'minLength': 1},
                                            {'type': 'null'},
                                        ],
                                    },
                                },
                            },
                        ],
                    },
                    'Node': {
                        'type': 'object',
                        'properties': {'next': {'$ref': '#/components/schemas/Node'}},
                    },
                },
            },
            'paths': {
                '/upload': {
                    'parameters': [{'$ref': '#/components/parameters/Trace'}],
                    'post': {
                        'requestBody': {'$ref': '#/components/requestBodies/Upload'},
                        'responses': {
                            '200': {'$ref': '#/components/responses/Saved'},
                            '400': {
                                'description': 'unresolved external example',
                                'content': {
                                    'text/plain': {
                                        'schema': {'$ref': 'https://example.invalid/common.yaml#/Message'},
                                        'example': '',
                                    },
                                },
                            },
                        },
                    },
                },
                '/nodes': {
                    'get': {
                        'responses': {
                            '200': {
                                'description': 'node',
                                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Node'}}},
                            },
                        },
                    },
                },
            },
        }

        extracted = self._extract(specification)
        upload = next(item for item in extracted['endpoints'] if item['path'] == '/upload')
        node = next(item for item in extracted['endpoints'] if item['path'] == '/nodes')

        self.assertEqual(upload['parameters'][0]['schema']['minimum'], 0)
        self.assertEqual(upload['parameters'][0]['example'], 0)
        upload_media = upload['request_body']['content']['multipart/form-data']
        self.assertIn('example', upload_media)
        self.assertEqual(upload_media['example'], [])
        pet = upload_media['schema']
        self.assertEqual(pet['allOf'][0]['properties']['id']['minimum'], 0)
        self.assertEqual(pet['allOf'][1]['properties']['kind']['oneOf'][1]['minimum'], 1)
        self.assertEqual(pet['allOf'][1]['properties']['owner']['anyOf'][0]['minLength'], 1)

        saved_media = upload['responses']['200']['content']['application/json']
        self.assertIn('example', saved_media)
        self.assertIsNone(saved_media['example'])
        external_schema = upload['responses']['400']['content']['text/plain']['schema']
        self.assertEqual(external_schema['x-aits-ref-status'], 'unresolved')
        self.assertEqual(external_schema['$ref'], 'https://example.invalid/common.yaml#/Message')
        next_schema = node['responses']['200']['content']['application/json']['schema']['properties']['next']
        self.assertEqual(next_schema['$ref'], '#/components/schemas/Node')
        self.assertIn(next_schema['x-aits-ref-status'], {'resolved', 'cyclic'})
        self.assertEqual(upload['request_body']['x-aits-operation']['security'], [{'oauth': ['read']}])

    def test_legacy_fallback_uses_the_same_endpoint_projection(self):
        specification = {
            'swagger': '2.0',
            'info': {'title': 'Fallback', 'version': '1.0'},
            'paths': {
                '/status': {
                    'parameters': [{'name': 'page', 'in': 'query', 'type': 'integer', 'minimum': 1}],
                    'get': {
                        'parameters': [{'name': 'page', 'in': 'query', 'type': 'integer', 'minimum': 2}],
                        'responses': {'200': {'description': 'ok'}},
                    },
                },
            },
        }

        class RecordingParser(APIParserService):
            def _create_api_endpoints_from_structured_data(self, spec, structured_data):
                self.created = structured_data
                return len(structured_data['endpoints'])

        parser = RecordingParser()
        parser.parsed_spec = specification
        normal = parser._extract_structured_data(specification)
        created = parser._create_api_endpoints(object(), specification)

        self.assertEqual(created, 1)
        self.assertEqual(parser.created['endpoints'], normal['endpoints'])
        self.assertEqual(parser.created['endpoints'][0]['parameters'][0]['schema']['minimum'], 2)

    def test_full_file_entry_rejects_external_ref_before_validator_or_endpoint_creation(self):
        specification = {
            'swagger': '2.0',
            'info': {'title': 'External reference', 'version': '1.0'},
            'paths': {
                '/external': {
                    'get': {
                        'responses': {
                            '200': {
                                'description': 'not fetched',
                                'schema': {'$ref': 'https://example.invalid/common.yaml#/Message'},
                            },
                        },
                    },
                },
            },
        }

        class FileEntryParser(APIParserService):
            created = False

            def _read_file_content(self, file_path, file_extension):
                return specification

            def _create_api_endpoints_from_structured_data(self, spec, structured_data):
                self.created = True
                return len(structured_data['endpoints'])

        parser = FileEntryParser()
        spec = SimpleNamespace(spec_type='swagger', file_name='external.json')
        uploaded_file = SimpleNamespace(
            file_exists=True, file=SimpleNamespace(name='external.json'), original_name='external.json',
            upload_status='pending', save_calls=0,
        )
        uploaded_file.save = lambda: setattr(uploaded_file, 'save_calls', uploaded_file.save_calls + 1)

        with patch('api_testing.api_parser_service.validate_spec') as validator:
            result = parser.parse_api_specification_from_file(spec, uploaded_file)

        self.assertFalse(result['success'])
        self.assertIn('不支持的外部 $ref', result['error'])
        self.assertFalse(parser.created)
        self.assertEqual(uploaded_file.upload_status, 'failed')
        self.assertEqual(uploaded_file.save_calls, 1)
        validator.assert_not_called()

    def test_endpoint_batch_uses_atomic_and_propagates_create_error(self):
        service = APIParserService()

        class AtomicProbe:
            entered = False
            saw_error = False

            def __enter__(self):
                self.entered = True
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.saw_error = exc_type is RuntimeError
                return False

        probe = AtomicProbe()
        endpoints = {'endpoints': [{'path': '/first'}, {'path': '/second'}]}
        with patch('api_testing.api_parser_service.transaction.atomic', return_value=probe), patch.object(
            service, '_create_single_endpoint', side_effect=[True, RuntimeError('database write failed')]
        ):
            with self.assertRaisesRegex(RuntimeError, 'database write failed'):
                service._create_api_endpoints_from_structured_data(object(), endpoints)

        self.assertTrue(probe.entered)
        self.assertTrue(probe.saw_error)

    def test_full_file_entry_returns_failed_when_endpoint_creation_raises(self):
        specification = {
            'swagger': '2.0',
            'info': {'title': 'Write failure', 'version': '1.0'},
            'paths': {'/health': {'get': {'responses': {'200': {'description': 'ok'}}}}},
        }

        class FailingCreateParser(APIParserService):
            def _read_file_content(self, file_path, file_extension):
                return specification

            def _create_api_endpoints_from_structured_data(self, spec, structured_data):
                raise RuntimeError('transaction rolled back')

        parser = FailingCreateParser()
        spec = SimpleNamespace(spec_type='swagger', file_name='write-failure.json')
        uploaded_file = SimpleNamespace(
            file_exists=True, file=SimpleNamespace(name='write-failure.json'),
            original_name='write-failure.json', upload_status='pending', save_calls=0,
        )
        uploaded_file.save = lambda: setattr(uploaded_file, 'save_calls', uploaded_file.save_calls + 1)

        with patch('api_testing.api_parser_service.validate_spec'):
            result = parser.parse_api_specification_from_file(spec, uploaded_file)

        self.assertFalse(result['success'])
        self.assertIn('transaction rolled back', result['error'])
        self.assertEqual(uploaded_file.upload_status, 'failed')
        self.assertEqual(uploaded_file.save_calls, 1)
