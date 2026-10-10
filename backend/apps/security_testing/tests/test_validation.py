import copy
from unittest import TestCase

from security_testing.validation import validate_config, validate_identities


def config():
    return {'allowed_origins': ['https://example.test'], 'endpoints': [{'url': 'https://example.test/items/1', 'name': '资源', 'method': 'GET', 'auth_required': True, 'proof': {'path': 'body.id', 'expected': 1}}]}


class ValidationTests(TestCase):
    def test_normalizes_without_mutating(self):
        source = config()
        source['allowed_origins'] = ['https://EXAMPLE.test:443/']
        source['endpoints'][0]['url'] = 'https://EXAMPLE.test:443/items/1'
        before = copy.deepcopy(source)
        result = validate_config(source)
        self.assertEqual(result['allowed_origins'], ['https://example.test'])
        self.assertEqual(result['endpoints'][0]['url'], 'https://example.test/items/1')
        self.assertEqual(result['max_requests'], 40)
        self.assertEqual(source, before)

    def test_untrusted_json_shapes_are_rejected(self):
        for value in (None, [], '', 1, True, {}, {'endpoints': None}, {'allowed_origins': [{}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config(value)
        for field, values in {'endpoints': [[None], [{}], 'bad'], 'allowed_origins': [[], [None], 'bad'], 'checks': [[{}], ['unknown'], []], 'allow_private_network': [1, 'true'], 'rate_per_second': [True, float('nan'), float('inf'), 10**1000, .01, 3], 'max_requests': [True, 1.1, 0, 101], 'timeout_seconds': [0, 31]}.items():
            for value in values:
                data = config()
                data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_config(data)

    def test_exact_origin_methods_and_url_syntax(self):
        for value in ('https://example.test.evil/items', 'http://example.test/items', 'https://example.test:444/items', 'https://user:pw@example.test/', 'https://example.test/#x', 'https://example.test/{id}', 'https://example.test/%7Bid%7D', 'https://example.test\\@evil/', 'file:///etc/passwd', 'https://example.test:99999/'):
            data = config()
            data['endpoints'][0]['url'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config(data)
        for value in ('POST', 'get', None, ['GET']):
            data = config()
            data['endpoints'][0]['method'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config(data)

    def test_headers_separate_credentials_and_reject_smuggling(self):
        for headers in ({'Authorization': 'secret'}, {'X-API-Key': 'secret'}, {'X-Auth': 'secret'}, {'Authentication': 'secret'}, {'X-HTTP-Method-Override': 'DELETE'}, {'cookie': 'x=secret'}, {'Host': 'evil'}, {'X-Forwarded-For': '127.0.0.1'}, {'Proxy-Authorization': 'x'}, {'Origin': 'https://evil'}, {'Accept': 'a\r\nHost: evil'}, {'Accept': 'a', 'accept': 'b'}, {'bad name': 'x'}, {'x-test': 12}):
            data = config()
            data['endpoints'][0]['headers'] = headers
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                validate_config(data)
        self.assertEqual(validate_identities({'a': {'headers': {'Authorization': 'Bearer secret'}}})['a']['headers']['Authorization'], 'Bearer secret')
        for value in (None, [], {'c': {}}, {'a': None}, {'a': {'headers': {}}}, {'a': {'headers': {'Host': 'evil'}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_identities(value)

    def test_url_cannot_share_authentication_query_between_identities(self):
        for query in ('token=secret', 'api_key=secret', 'session=secret', 'ACCESS_TOKEN=secret',
                      '%74oken=secret', 'api%5Fkey=secret', 'session=', 'password',
                      'keyword=normal&auth=secret', '%E4%BB%A4%E7%89%8C=secret'):
            data = config()
            data['endpoints'][0]['url'] += '?' + query
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, '认证参数不支持放在URL'):
                validate_config(data)
        data = config()
        data['endpoints'][0]['url'] += '?keyword=token&locale=zh-CN&page=1'
        self.assertEqual(validate_config(data)['endpoints'][0]['url'], data['endpoints'][0]['url'])

    def test_proof_selector_is_bounded_and_scalar(self):
        for proof in ({'path': 'body.__class__()', 'expected': 1}, {'path': 'body.id', 'expected': {}}, {'path': 'body.id', 'expected': None}, {'path': 'body.id'}, {'path': 'body..id', 'expected': 1}, {'path': 'body.x[99999]', 'expected': 1}, {'path': 'body.id', 'expected': float('nan')}, {'path': 'body.id', 'expected': ''}, []):
            data = config()
            data['endpoints'][0]['proof'] = proof
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                validate_config(data)
        data = config()
        data['endpoints'][0]['proof'] = {'path': 'body.items[0].id', 'expected': 'resource'}
        self.assertEqual(validate_config(data)['endpoints'][0]['proof']['path'], 'body.items[0].id')
