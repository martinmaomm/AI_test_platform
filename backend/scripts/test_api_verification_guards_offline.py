"""Independent API generation acceptance probes; no external sockets or services.

Creates a disposable SQLite schema and calls the real HTTP boundary/task with
fake streamed model output and fake requests responses. Does not use the NAS.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from test_api_workspace_browser import bootstrap


def main():
    def denied(*args, **kwargs):
        raise AssertionError('External sockets forbidden during acceptance checks')

    with tempfile.TemporaryDirectory(prefix='aits-api-verification-') as root, patch.object(
        socket.socket, 'connect', denied,
    ), patch.object(socket.socket, 'connect_ex', denied):
        fixture = bootstrap(Path(root))
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from requests import Response
        from ai_core.models import LLMConfiguration
        from api_testing.models import APIWorkspace, APITestCase
        from api_testing.workspace_views import APIWorkspaceMessagesView, APIWorkspaceDetailView
        from api_testing.workspace_tasks import generate_and_verify_api_workspace
        from api_testing.requests_runtime import run_case

        owner = get_user_model().objects.get(pk=fixture['user_id'])
        factory = APIRequestFactory()

        def request(method, payload):
            value = getattr(factory, method)('/', payload, format='json')
            force_authenticate(value, user=owner)
            return value

        def draft(*, extract=None, expected=200):
            value = {
                'version': 1, 'config': {'name': '隔离验证', 'base_url': 'https://example.test', 'variables': {}},
                'teststeps': [{'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
                               'request': {'method': 'GET', 'url': '/health'},
                               'validate': [{'eq': ['status_code', expected]}]}],
            }
            if extract is not None:
                value['teststeps'][0]['extract'] = {'health': extract}
            return value

        def exercise(outputs, *, http_status=200, after_model=None):
            scope = [fixture['endpoint_id']]
            parent = APIWorkspace.objects.create(
                owner=owner, project_id=fixture['project_id'], spec_id=fixture['spec_id'], endpoint_ids=scope,
                generation={'status': 'passed', '_snapshot': {'scope_endpoint_ids': scope}},
            )
            workspace = APIWorkspace.objects.create(
                owner=owner, project_id=fixture['project_id'], model_id=fixture['model_id'],
                spec_id=fixture['spec_id'], endpoint_ids=[fixture['endpoint_id']],
                parent=parent, scenario_order=1, scenario_description='单场景边界独立验收',
                generation={'_snapshot': {'scope_endpoint_ids': scope, 'scenario': {
                    'target_endpoint_ids': scope, 'available_endpoint_ids': scope,
                }}},
            )
            with patch.object(generate_and_verify_api_workspace, 'apply_async') as queue:
                reply = APIWorkspaceMessagesView.as_view()(
                    request('post', {'revision': 0, 'message': '依据规范检查健康状态',
                                     'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {}}),
                    project_id=fixture['project_id'], workspace_id=workspace.pk,
                )
            assert reply.status_code == 202, reply.data
            queued = queue.call_args.kwargs
            prompts, requests = [], []

            def answer(messages, callback=None, **kwargs):
                payload = next(json.loads(item.content) for item in messages if item.content.lstrip().startswith('{'))
                prompts.append(payload)
                value = deepcopy(outputs[min(len(prompts) - 1, len(outputs) - 1)])
                if after_model:
                    after_model(workspace)
                text = json.dumps(value)
                if callback:
                    callback(text)
                return text

            def fake_request(**kwargs):
                requests.append(kwargs)
                assert kwargs['url'] == 'https://example.test/health'
                response = Response()
                response.status_code = http_status
                response._content = b'{"state":"ok"}'
                response.headers = {'Content-Type': 'application/json'}
                response.url, response.elapsed = kwargs['url'], timedelta(milliseconds=2)
                return response

            def runner(**kwargs):
                remaining = kwargs.pop('hard_timeout_seconds', None)
                if remaining is not None:
                    kwargs['options'] = {**(kwargs.get('options') or {}), 'total_timeout': min(600, remaining)}
                with patch('api_testing.requests_runtime.requests.Session', return_value=SimpleNamespace(request=fake_request, close=lambda: None)):
                    return run_case(**kwargs)

            with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=answer)), patch(
                'api_testing.requests_runner.requests_runner', side_effect=runner,
            ):
                generate_and_verify_api_workspace.apply(**queued)
                before = (len(prompts), len(requests))
                generate_and_verify_api_workspace.apply(**queued)
                assert before == (len(prompts), len(requests)), 'Duplicate delivery reissued a provider/HTTP call'
            workspace.refresh_from_db()
            return workspace, prompts, requests

        first, prompts, sent = exercise([draft()])
        assert first.generation['status'] == 'passed', first.generation
        assert len(prompts) == len(sent) == 1
        assert first.draft['teststeps'] == [] and APITestCase.objects.count() == 0
        assert sent[0]['allow_redirects'] is False
        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queue:
            denied_reply = APIWorkspaceMessagesView.as_view()(
                request('post', {'revision': first.revision, 'message': '没有执行授权', 'base_url': 'https://example.test'}),
                project_id=fixture['project_id'], workspace_id=first.pk,
            )
        assert denied_reply.status_code == 400
        queue.assert_not_called()

        # Trust only the server-held candidate; adoption binds verified evidence
        # to the new editor revision, while an ordinary edit invalidates it.
        adopted = APIWorkspaceDetailView.as_view()(
            request('patch', {'revision': first.revision, 'draft': first.candidate['draft']}),
            project_id=fixture['project_id'], workspace_id=first.pk,
        )
        assert adopted.status_code == 200, adopted.data
        first.refresh_from_db()
        assert first.generation['adopted_revision'] == first.revision
        assert first.debug_revision == first.revision and first.debug_result['success'] is True
        assert first.candidate is None, 'Adoption left a misleading unadopted candidate'
        edited = deepcopy(first.draft)
        edited['config']['name'] = '用户修改后的版本'
        changed = APIWorkspaceDetailView.as_view()(
            request('patch', {'revision': first.revision, 'draft': edited}),
            project_id=fixture['project_id'], workspace_id=first.pk,
        )
        assert changed.status_code == 200, changed.data
        first.refresh_from_db()
        assert first.generation['status'] == 'stale' and first.generation['rounds']

        repaired, prompts, sent = exercise([draft(extract='body.result.state'), draft(extract='body.state')])
        assert repaired.generation['status'] == 'passed', repaired.generation
        assert len(prompts) == len(sent) == 2
        assert prompts[1]['current_draft']['teststeps'][0]['extract'] == {'health': 'body.result.state'}
        failed_response = repaired.generation['rounds'][0]['result']['step_datas'][0]['data']['req_resps'][0]['response']
        assert failed_response['status_code'] == 200 and failed_response['body'] == {'state': 'ok'}

        exhausted, prompts, sent = exercise([draft(extract='body.missing')])
        assert exhausted.generation['status'] == 'needs_review', exhausted.generation
        assert len(prompts) == len(sent) == 3
        assert exhausted.candidate and exhausted.candidate['draft'], 'Last failed candidate was discarded'

        # Later invalid model replies must not erase an earlier runnable draft.
        retained, prompts, sent = exercise([draft(extract='body.missing'), {'invalid': True}])
        assert retained.generation['status'] == 'needs_review', retained.generation
        assert retained.candidate['draft']['teststeps'][0]['extract'] == {'health': 'body.missing'}
        assert len(prompts) == 3 and len(sent) == 1

        provider_calls = []

        def provider_outage_after_first(_workspace):
            provider_calls.append(True)
            if len(provider_calls) == 2:
                raise RuntimeError('fixture: provider unavailable on repair')

        interrupted, prompts, sent = exercise([draft(extract='body.missing')], after_model=provider_outage_after_first)
        assert interrupted.generation['status'] == 'failed'
        assert interrupted.candidate['draft']['teststeps'][0]['extract'] == {'health': 'body.missing'}
        assert len(prompts) == 2 and len(sent) == 1

        # A repair cannot remove the failed extraction's existing assertions or
        # turn an expected-value variable into a copy of the response itself.
        baseline = draft(extract='body.missing')
        baseline['config']['variables'] = {'expected_state': 'ok'}
        baseline['teststeps'][0]['validate'].append({'eq': ['body.state', '${expected_state}']})
        weakened = deepcopy(baseline)
        weakened['teststeps'][0]['extract'] = {'health': 'body.state', 'expected_state': 'body.state'}
        guarded, prompts, sent = exercise([baseline, weakened])
        assert guarded.generation['status'] != 'passed', guarded.generation
        assert len(sent) == 1, 'Repair shadowed an independent expected variable with response data'

        for mutation in ('method', 'endpoint', 'no_assertions'):
            invalid = draft()
            if mutation == 'method':
                invalid['teststeps'][0]['request']['method'] = 'POST'
            elif mutation == 'endpoint':
                invalid['teststeps'][0]['endpoint_id'] = 999999
            else:
                invalid['teststeps'][0]['validate'] = []
            guarded, prompts, sent = exercise([invalid])
            assert not sent and guarded.generation['status'] != 'passed', (mutation, guarded.generation)

        # Parseable static-invalid output must remain visible and be the actual
        # next repair input, without issuing HTTP for the invalid first round.
        no_assertions = draft()
        no_assertions['teststeps'][0]['validate'] = []
        static_fixed, prompts, sent = exercise([no_assertions, draft()])
        assert static_fixed.generation['status'] == 'passed', static_fixed.generation
        assert len(prompts) == 2 and len(sent) == 1
        assert prompts[1]['current_draft']['teststeps'][0]['validate'] == []
        assert static_fixed.generation['rounds'][0]['draft']['teststeps'], 'Static-invalid candidate was discarded'
        assert not static_fixed.generation['rounds'][0]['result'].get('step_datas'), 'Static-invalid candidate ran HTTP'
        assert prompts[1]['failure_evidence'], 'Repair did not receive the validation error'
        malformed = {'version': 1, 'config': 42, 'teststeps': [None, 42]}
        recovered, prompts, sent = exercise([malformed, draft()])
        assert recovered.generation['status'] == 'passed', recovered.generation
        assert len(sent) == 1 and prompts[1]['current_draft'] == malformed

        for code in (401, 403, 503):
            blocked, prompts, sent = exercise([draft()], http_status=code)
            assert blocked.generation['status'] == 'needs_review', blocked.generation
            assert len(prompts) == len(sent) == 1, 'Unusable service/account caused automatic replay'

        negative, prompts, sent = exercise([draft(expected=401)], http_status=401)
        assert negative.generation['status'] == 'passed', negative.generation
        assert len(sent) == 1

        unsafe = draft()
        unsafe['teststeps'][0]['request']['url'] = 'https://other.invalid/health'
        rejected, prompts, sent = exercise([unsafe])
        assert rejected.generation['status'] != 'passed' and not sent

        # A normal login -> authenticated path-parameter request remains legal.
        # Builtins are resolved afresh by the runtime, not frozen by validation.
        from api_testing.workspace_verification import prepare_candidate
        endpoints = [
            {'id': 101, 'method': 'POST', 'path': '/login', 'parameters': [],
             'request_body': {'required': True, 'content': {'application/json': {'schema': {'type': 'object'}}}}},
            {'id': 102, 'method': 'GET', 'path': '/users/{id}',
             'parameters': [{'name': 'id', 'in': 'path', 'required': True},
                            {'name': 'Authorization', 'in': 'header', 'required': True}]},
        ]
        chain = {'version': 1, 'config': {'name': '顺序登录变量验收', 'variables': {'run_name': 'test_${timestamp_ns}', 'token': '', 'id': None, 'unused_optional': ''}},
                 'teststeps': [
                     {'name': '登录', 'endpoint_id': 101, 'request': {'method': 'POST', 'url': '/login', 'json': {'username': '${run_name}'}},
                      'extract': {'id': 'body.id', 'token': 'body.token'}, 'validate': [{'eq': ['status_code', 200]}]},
                     {'name': '读取用户', 'endpoint_id': 102, 'request': {'method': 'GET', 'url': '/users/${id}', 'headers': {'authorization': 'Bearer ${token}'}},
                      'validate': [{'eq': ['status_code', 200]}]},
                 ]}
        checked = prepare_candidate(chain, endpoints=endpoints, target_url='https://example.test', variables={})
        assert checked['config']['variables']['run_name'] == 'test_${timestamp_ns}'
        sequential_requests = []

        def sequential_response(**kwargs):
            sequential_requests.append(kwargs)
            assert kwargs['url'] in {'https://example.test/login', 'https://example.test/users/7'}
            reply = Response()
            reply.status_code, reply._content = 200, b'{"id":7,"token":"fixture-token"}'
            reply.headers, reply.url, reply.elapsed = {}, kwargs['url'], timedelta(milliseconds=1)
            return reply

        with patch('api_testing.requests_runtime.requests.Session', return_value=SimpleNamespace(request=sequential_response, close=lambda: None)):
            sequential_result = run_case('sequential-acceptance', json.dumps(checked))
        assert sequential_result['success'], sequential_result
        assert [entry['method'] for entry in sequential_requests] == ['POST', 'GET']
        assert sequential_requests[1]['headers']['authorization'] == 'Bearer fixture-token'

        # Common headers with a supplied variable must be substituted too, not
        # sent literally as Bearer ${token}. The exporter embeds this runtime.
        global_header_case = draft()
        global_header_case['config']['variables'] = {'token': 'provided-fixture-token'}
        global_header_case['config']['headers'] = {'Authorization': 'Bearer ${token}'}
        global_sent = []

        def global_response(**kwargs):
            global_sent.append(kwargs)
            reply = Response()
            reply.status_code, reply._content = 200, b'{"state":"ok"}'
            reply.headers, reply.url, reply.elapsed = {}, kwargs['url'], timedelta(milliseconds=1)
            return reply

        with patch('api_testing.requests_runtime.requests.Session', return_value=SimpleNamespace(request=global_response, close=lambda: None)):
            global_result = run_case('global-header-acceptance', json.dumps(global_header_case))
        assert global_result['success'], global_result
        assert global_sent[0]['headers']['Authorization'] == 'Bearer provided-fixture-token'

        def disable_model(workspace):
            LLMConfiguration.objects.filter(pk=fixture['model_id']).update(is_active=False)

        stopped, prompts, sent = exercise([draft()], after_model=disable_model)
        assert not sent, 'Model revoked during generation still authorized execution'
        assert stopped.generation['status'] != 'passed'

    print('PASS: independent generation lifecycle, consent, no duplicate execution, repair, response retention, bounds, negative test, target and model guards')


if __name__ == '__main__':
    main()
