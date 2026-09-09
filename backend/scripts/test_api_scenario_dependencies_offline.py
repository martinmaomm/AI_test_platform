"""Independent acceptance for target-only plans and explicit dependency repair.

Uses temporary SQLite, fake streaming model answers, and fake requests.Session.
All external sockets are blocked; no live model, target, NAS, or Redis is used.
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
        raise AssertionError('External network is forbidden in dependency acceptance')

    with tempfile.TemporaryDirectory(prefix='automation-api-dependencies-') as temp, patch.object(
        socket.socket, 'connect', denied,
    ), patch.object(socket.socket, 'connect_ex', denied):
        fixture = bootstrap(Path(temp))
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from requests import Response
        from api_testing.models import APISpecification, APIEndpoint, APIWorkspace, APITestCase
        from api_testing.requests_runtime import run_case
        from api_testing.workspace_service import normalize_draft
        from api_testing.workspace_tasks import generate_and_verify_api_workspace
        from api_testing.workspace_views import APIWorkspaceMessagesView

        owner = get_user_model().objects.get(pk=fixture['user_id'])
        spec = APISpecification.objects.get(pk=fixture['spec_id'])
        spec.metadata['securityDefinitions'] = {'access': {'type': 'apiKey', 'in': 'header', 'name': 'X-Access'}}
        spec.metadata['paths'].update({
            '/session-ticket': {'post': {'security': [], 'responses': {'200': {'description': 'OK'}}}},
            '/who': {'get': {'security': [{'access': []}], 'responses': {'200': {'description': 'OK'}}}},
        })
        spec.save(update_fields=['metadata'])
        ticket = APIEndpoint.objects.create(spec=spec, path='/session-ticket', method='POST',
                                            summary='获取本场景的访问凭证', responses={'200': {'description': 'OK'}})
        who = APIEndpoint.objects.create(spec=spec, path='/who', method='GET',
                                         summary='读取本场景的用户信息', responses={'200': {'description': 'OK'}})
        root = APIWorkspace.objects.create(owner=owner, project_id=fixture['project_id'], spec=spec,
                                          model_id=fixture['model_id'], title='依赖范围验收',
                                          endpoint_ids=[ticket.pk, who.pk, fixture['endpoint_id']])

        target_step = {'name': '读取当前用户', 'endpoint_id': who.pk,
                       'request': {'method': 'GET', 'url': '/who', 'headers': {'X-Access': '${access_key}'}},
                       'validate': [{'eq': ['status_code', 200]}, {'eq': ['body.code', 200]}]}
        ticket_step = {'name': '获取访问凭证', 'endpoint_id': ticket.pk,
                       'request': {'method': 'POST', 'url': '/session-ticket'},
                       'extract': {'access_key': 'body.ticket'}, 'validate': [{'eq': ['status_code', 200]}]}
        good = normalize_draft({'config': {'name': '独立读取当前用户'}, 'teststeps': [ticket_step, target_step]})
        health = normalize_draft({'config': {'name': '独立健康检查'}, 'teststeps': [{
            'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
            'request': {'method': 'GET', 'url': '/health'}, 'validate': [{'eq': ['status_code', 200]}],
        }]})
        outputs = [
            {'summary': '两个独立场景', 'scenarios': [
                {'title': '读取当前用户', 'description': '自行获取凭证后读取当前用户，业务码为200',
                 'endpoint_ids': [who.pk], 'requires_authenticated_context': True,
                 'dependency_evidence': 'OpenAPI access 声明需要 X-Access，获取凭证接口返回 ticket。'},
                {'title': '健康检查', 'description': '检查HTTP200', 'endpoint_ids': [fixture['endpoint_id']]},
            ]}, good, health,
        ]
        prompts, requests, sessions = [], [], []

        def answer(messages, callback=None, **kwargs):
            payload = next(json.loads(item.content) for item in messages if item.content.lstrip().startswith('{'))
            prompts.append(payload)
            assert outputs, 'Unexpected model retry'
            text = json.dumps(outputs.pop(0), ensure_ascii=False)
            if callback:
                callback(text)
            return text

        def session_factory():
            identity = len(sessions) + 1
            sessions.append(identity)
            granted = []

            def request(**kwargs):
                requests.append((identity, kwargs))
                path = kwargs['url'].removeprefix('https://example.test')
                assert path in {'/session-ticket', '/who', '/health'}, kwargs['url']
                body = {'code': 200}
                if path == '/session-ticket':
                    granted.append(f'fixture-ticket-{identity}')
                    body['ticket'] = granted[-1]
                elif path == '/who':
                    assert granted, 'Target was called without an independent credential request'
                    assert kwargs['headers'].get('X-Access') == granted[-1], kwargs['headers']
                response = Response()
                response.status_code = 200
                response._content = json.dumps(body).encode()
                response.headers, response.url, response.elapsed = {}, kwargs['url'], timedelta(milliseconds=1)
                return response

            return SimpleNamespace(request=request, close=lambda: None)

        def runner(**kwargs):
            remaining = kwargs.pop('hard_timeout_seconds', None)
            assert remaining and remaining <= 1800
            kwargs['options'] = {**(kwargs.get('options') or {}), 'total_timeout': min(600, remaining)}
            return run_case(**kwargs)

        def queue(workspace, mode='generate'):
            request = APIRequestFactory().post('/', {'revision': workspace.revision, 'mode': mode,
                'message': '仅补齐当前场景的鉴权依赖，保留读取用户信息的200断言',
                'base_url': 'https://example.test', 'execution_confirmed': True, 'variables': {}}, format='json')
            force_authenticate(request, user=owner)
            with patch.object(generate_and_verify_api_workspace, 'apply_async') as dispatch:
                response = APIWorkspaceMessagesView.as_view()(request, project_id=fixture['project_id'], workspace_id=workspace.pk)
            assert response.status_code == 202, response.data
            return dispatch.call_args.kwargs

        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=answer)), patch(
            'api_testing.requests_runner.requests_runner', side_effect=runner,
        ), patch('api_testing.requests_runtime.requests.Session', side_effect=session_factory):
            generate_and_verify_api_workspace.apply(**queue(root))
            root.refresh_from_db()
            children = list(root.scenarios.order_by('scenario_order'))
            assert len(children) == 2 and root.generation['status'] == 'passed', root.generation
            first, sibling = children
            assert first.candidate['draft']['teststeps'][0]['endpoint_id'] == ticket.pk
            assert len(requests) == 3 and len(sessions) == 2, (requests, sessions)
            assert not outputs, 'Fixture answers were not used as expected'

            # Persist a current failed draft like the reported single-step
            # candidate. Explicit repair must be allowed to prepend auth without
            # matching original assertions by their absolute list position.
            bad = normalize_draft({'config': {'name': '读取当前用户'}, 'teststeps': [deepcopy(target_step)]})
            bad['teststeps'][0]['request']['headers'] = {}
            failure = {'success': False, 'status': 'failed', 'error_type': 'ValidationFailure',
                       'step_datas': [{'name': '读取当前用户', 'status': 'failed', 'data': {'req_resps': [{
                           'response': {'status_code': 200, 'body': {'code': 401, 'message': '未登录'}}}]}}]}
            stale_generation = {**deepcopy(first.generation), 'status': 'stale'}
            APIWorkspace.objects.filter(pk=first.pk).update(draft=bad, candidate=None, generation=stale_generation,
                                                            debug_result=failure, debug_revision=first.revision)
            first.refresh_from_db()
            before_sibling = APIWorkspace.objects.filter(pk=sibling.pk).values().get()
            weakened = deepcopy(good)
            weakened['teststeps'][1]['validate'][1] = {'eq': ['body.code', 401]}
            outputs.extend([weakened, good])
            previous_calls = len(requests)
            generate_and_verify_api_workspace.apply(**queue(first, 'repair'))
            first.refresh_from_db()
            assert first.generation['status'] == 'passed', first.generation
            assert len(first.generation['rounds']) == 2, first.generation
            assert first.generation['rounds'][0]['runnable'] is False
            assert first.generation['rounds'][0]['result'] == {}, 'Weakened assertions reached the target'
            assert len(requests) == previous_calls + 2 and len(sessions) == 3, (requests, sessions)
            assert first.candidate['draft']['teststeps'][1]['validate'] == good['teststeps'][1]['validate']
            assert APIWorkspace.objects.filter(pk=sibling.pk).values().get() == before_sibling
            assert APITestCase.objects.count() == 0
            assert not outputs
    print('PASS: target-only scenario uses selected dependencies; manual repair prepends auth, preserves assertions and other scenes')


if __name__ == '__main__':
    main()
