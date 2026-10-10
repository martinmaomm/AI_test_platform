import copy
import json
import time
from unittest import TestCase
from unittest.mock import patch

from security_testing import runner, transport
from security_testing.tests.test_transport import LocalFixture


def snapshot(checks=('authentication',), *, proof=True):
    return {'config': {'allowed_origins': ['https://example.test'], 'rate_per_second': 2,
                      'endpoints': [{'name': '资源', 'url': 'https://example.test/items/7', 'method': 'GET', 'headers': {'Accept-Language': 'zh-CN'},
                                     'auth_required': True, 'owner_only_a': True,
                                     'proof': {'path': 'body.id', 'expected': 7} if proof else None}], 'checks': list(checks)},
            'cases': [{'id': f'case-{index}', 'endpoint_index': 0, 'check': check, 'title': '检查'} for index, check in enumerate(checks)]}


def identities():
    return {'a': {'name': '账号A', 'headers': {'Authorization': 'Bearer local-secret', 'Cookie': 'session=local-cookie'}},
            'b': {'name': '账号B', 'headers': {'Authorization': 'Bearer other-secret'}}}


def response(body=None, status=200, headers=None, truncated=False):
    return transport.Response(status, headers or [('Content-Type', 'application/json')], json.dumps({'id': 7} if body is None else body).encode(), 1.2, truncated)


class RunnerTests(TestCase):
    def execute(self, source, responses, ids=None):
        # Time moves between calls to avoid sleeping on rate limits in pure fixtures.
        ticks = iter(range(10000))
        with patch.object(runner.transport, 'request', side_effect=responses) as send, patch.object(runner.time, 'monotonic', side_effect=lambda: next(ticks)):
            result = runner.execute(source, identities() if ids is None else ids, lambda: None, lambda _: None)
        return result, send

    def test_valid_baseline_and_denials_pass_without_credentials_in_evidence(self):
        result, send = self.execute(snapshot(), [response(), response({'error': 'denied'}, 401), response({'error': 'denied'}, 403)])
        self.assertEqual(result['items'][0]['status'], 'passed')
        headers = [call.args[2] for call in send.call_args_list]
        self.assertEqual(headers[0]['Authorization'], 'Bearer local-secret')
        self.assertNotIn('Authorization', headers[1])
        self.assertNotIn('Cookie', headers[1])
        self.assertEqual(headers[1]['Accept-Language'], 'zh-CN')
        self.assertNotIn('local-secret', json.dumps(headers[2]))
        self.assertNotIn('local-cookie', json.dumps(headers[2]))
        self.assertNotIn('local-secret', json.dumps(result))
        self.assertEqual(result['summary']['requests_sent'], 3)

    def test_confirmation_requires_explicit_declaration_and_matching_proof(self):
        result, _ = self.execute(snapshot(), [response(), response(), response()])
        self.assertEqual(result['items'][0]['status'], 'confirmed')
        data = snapshot()
        data['config']['endpoints'][0]['auth_required'] = False
        result, send = self.execute(data, [])
        self.assertEqual(result['items'][0]['status'], 'skipped')
        send.assert_not_called()
        result, _ = self.execute(snapshot(proof=False), [response(), response(), response()])
        self.assertEqual(result['items'][0]['status'], 'suspected')

    def test_200_business_error_and_invalid_baseline_cannot_pass(self):
        for baseline in (response({'error': 'expired'}), response({'id': True}), response(status=401), response(truncated=True)):
            with self.subTest(baseline=baseline):
                result, send = self.execute(snapshot(), [baseline])
                self.assertEqual(result['items'][0]['status'], 'inconclusive')
                self.assertEqual(send.call_count, 1)
        result, _ = self.execute(snapshot(proof=False), [response({'error': 'expired'}), response({'error': 'expired'}), response({'error': 'expired'})])
        self.assertNotIn(result['items'][0]['status'], ('passed', 'confirmed'))

    def test_ownership_and_b_account_are_required_and_b_cannot_inherit_a_cookie(self):
        result, send = self.execute(snapshot(('authorization',)), [response(), response()])
        self.assertEqual(result['items'][0]['status'], 'confirmed')
        b_headers = send.call_args_list[1].args[2]
        self.assertEqual(b_headers['Authorization'], 'Bearer other-secret')
        self.assertNotIn('Cookie', b_headers)
        data = snapshot(('authorization',))
        data['config']['endpoints'][0]['owner_only_a'] = False
        result, send = self.execute(data, [])
        self.assertEqual(result['items'][0]['status'], 'inconclusive')
        send.assert_not_called()
        result, send = self.execute(snapshot(('authorization',)), [], {'a': identities()['a']})
        self.assertEqual(result['items'][0]['status'], 'inconclusive')
        send.assert_not_called()

    def test_cors_uses_two_real_requests_and_budget_counts_every_probe(self):
        cors = [response(headers=[('Access-Control-Allow-Origin', origin), ('Access-Control-Allow-Credentials', 'true')]) for origin in runner._CORS_ORIGINS]
        result, send = self.execute(snapshot(('configuration',)), [response(), *cors])
        self.assertEqual(result['summary']['requests_sent'], 3)
        self.assertIn('两个测试来源', result['items'][0]['summary'])
        self.assertEqual([call.args[2].get('Origin') for call in send.call_args_list[1:]], list(runner._CORS_ORIGINS))
        source = snapshot(('configuration', 'information'))
        source['config']['max_requests'] = 2
        result, send = self.execute(source, [response(), cors[0]])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['items'][0]['status'], 'inconclusive')
        self.assertNotEqual(result['items'][0]['status'], 'passed')
        self.assertEqual(result['summary']['requests_sent'], 2)

    def test_same_a_b_credentials_cannot_confirm_authorization_failure(self):
        accounts = identities()
        accounts['b']['headers'] = {key.lower(): value for key, value in accounts['a']['headers'].items()}
        result, send = self.execute(snapshot(('authorization',)), [], accounts)
        self.assertEqual(result['items'][0]['status'], 'inconclusive')
        self.assertIn('认证请求头相同', result['items'][0]['summary'])
        self.assertEqual(result['summary']['requests_sent'], 0)
        send.assert_not_called()

    def test_timeout_is_error_and_exception_message_never_persisted(self):
        result, _ = self.execute(snapshot(), [transport.TransportError('timeout')])
        self.assertEqual(result['items'][0]['status'], 'error')
        self.assertEqual(result['items'][0]['evidence'][0]['error_code'], 'timeout')

    def test_sensitive_json_and_response_cookie_not_persisted(self):
        body = {'id': 7, 'password': 'response-password', 'access_token': 'response-token', 'nested': {'Cookie': 'response-cookie'}, 'echo': 'Bearer local-secret', 'other': 'local-cookie'}
        result, _ = self.execute(snapshot(('information',)), [response(body, headers=[('Set-Cookie', 'session=raw-server-cookie'), ('X-Internal-Secret', 'internal'), ('Content-Type', 'application/json')])])
        encoded = json.dumps(result)
        for secret in ('response-password', 'response-token', 'response-cookie', 'local-secret', 'local-cookie', 'raw-server-cookie', 'internal'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(result['items'][0]['status'], 'suspected')

    def test_hostile_json_never_loses_other_case_results(self):
        for raw in (b'{"value":1e999}', b'{"value":NaN}', b'[' * 2000 + b']' * 2000, b'not json'):
            with self.subTest(raw=raw[:30]):
                source = snapshot(('information', 'authentication'))
                malformed = transport.Response(200, [], raw, 1.0)
                result, _ = self.execute(source, [malformed])
                self.assertEqual(len(result['items']), 2)
                self.assertEqual(result['items'][1]['status'], 'inconclusive')
                json.dumps(result, allow_nan=False)

    def test_redirect_is_not_authentication_pass(self):
        result, _ = self.execute(snapshot(), [response(), response(status=302), response(status=302)])
        self.assertEqual(result['items'][0]['status'], 'inconclusive')

    def test_cancel_and_total_deadline_propagate(self):
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            runner.execute(snapshot(), identities(), lambda: (_ for _ in ()).throw(ValueError('cancelled')), lambda _: None)
        with patch.object(runner, '_TOTAL_SECONDS', -1), self.assertRaises(runner.RuntimeDeadlineExceeded):
            runner.execute(snapshot(), identities(), lambda: None, lambda _: None)

    def test_each_completed_case_publishes_frozen_redacted_partial_result(self):
        ticks, progress = iter(range(10000)), []
        source = snapshot(('information', 'authentication'))
        responses = [response({'id': 7, 'password': 'response-secret', 'echo': 'Bearer local-secret'}),
                     response({'error': 'denied'}, 401), response({'error': 'denied'}, 403)]
        with patch.object(runner.transport, 'request', side_effect=responses), patch.object(
                runner.time, 'monotonic', side_effect=lambda: next(ticks)):
            result = runner.execute(source, identities(), lambda: None, progress.append)
        first = next(item for item in progress if item['phase'] == 'checking')
        self.assertEqual(len(first['partial_result']['items']), 1)
        self.assertEqual(first['partial_result']['summary']['expected_total'], 2)
        self.assertFalse(first['partial_result']['summary']['complete'])
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['summary']['complete'])
        second_request = next(item for item in progress if item['phase'] == 'request' and item['requests_sent'] == 2)
        self.assertEqual(second_request['partial_result']['summary']['requests_sent'], 2)
        self.assertEqual(len(second_request['partial_result']['items']), 1)
        for secret in ('response-secret', 'local-secret', 'local-cookie'):
            self.assertNotIn(secret, json.dumps(progress))

    def test_cancel_after_first_case_leaves_its_published_evidence(self):
        saved, ticks = [], iter(range(10000))

        def save_then_stop(progress):
            saved.append(progress)
            if progress['phase'] == 'checking':
                raise ValueError('cancelled')

        with patch.object(runner.transport, 'request', return_value=response()) as send, patch.object(
                runner.time, 'monotonic', side_effect=lambda: next(ticks)), self.assertRaisesRegex(ValueError, 'cancelled'):
            runner.execute(snapshot(('information', 'authentication')), identities(), lambda: None, save_then_stop)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(saved[-1]['partial_result']['items']), 1)
        self.assertEqual(saved[-1]['partial_result']['items'][0]['status'], 'passed')
        self.assertTrue(saved[-1]['partial_result']['items'][0]['evidence'])

    def test_malformed_cases_never_send_request(self):
        for cases in (None, {}, [], [None], [{'id': 'c', 'endpoint_index': True, 'check': 'authentication'}], [{'id': 'c', 'endpoint_index': 0, 'check': 'unknown'}]):
            source = snapshot()
            source['cases'] = cases
            with self.subTest(cases=cases), patch.object(transport, 'request') as send, self.assertRaises(ValueError):
                runner.execute(source, identities(), lambda: None, lambda _: None)
            send.assert_not_called()

    def test_local_http_end_to_end_without_auth_leak(self):
        with LocalFixture() as fixture:
            source = snapshot(('authentication', 'information'))
            source['config']['allowed_origins'] = [fixture.origin]
            source['config']['allow_private_network'] = True
            source['config']['endpoints'][0]['url'] = fixture.origin + '/private'
            source['config']['endpoints'][0]['proof']['expected'] = 7
            before = copy.deepcopy(source)
            progress = []
            result = runner.execute(source, identities(), lambda: None, progress.append)
            self.assertEqual(result['items'][0]['status'], 'passed')
            self.assertEqual(result['items'][1]['status'], 'suspected')
            self.assertEqual(result['summary']['requests_sent'], 3)
            self.assertEqual(len(fixture.server.received), 3)
            self.assertEqual(progress[-1]['completed'], 2)
            self.assertEqual(source, before)
            encoded = json.dumps(result)
            for secret in ('local-secret', 'local-cookie', 'never-persist', 'private-response-token', 'server-secret', 'private-response-cookie'):
                self.assertNotIn(secret, encoded)
