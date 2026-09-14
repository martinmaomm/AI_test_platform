"""Independent multi-scenario acceptance: real views/tasks/runtime, fake services.

No NAS, Redis, provider or target sockets are allowed. All data is temporary.
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
        raise AssertionError('External sockets forbidden in multi-scenario acceptance')

    with tempfile.TemporaryDirectory(prefix='automation-api-multi-') as temp, patch.object(
        socket.socket, 'connect', denied,
    ), patch.object(socket.socket, 'connect_ex', denied):
        fixture = bootstrap(Path(temp))
        from django.contrib.auth import get_user_model
        from requests import Response
        from rest_framework.test import APIRequestFactory, force_authenticate
        from api_testing.models import APIEndpoint, APITestCase, APIWorkspace
        from api_testing.requests_runtime import run_case
        from api_testing.workspace_service import serialize_workspace
        from api_testing.workspace_tasks import generate_and_verify_api_workspace
        from api_testing.workspace_views import (
            APIWorkspaceCollectionView, APIWorkspaceDetailView,
            APIWorkspaceMessagesView, APIWorkspaceSaveView,
        )

        factory = APIRequestFactory()
        owner = get_user_model().objects.get(pk=fixture['user_id'])

        def call(view, method, payload=None, workspace=None, user=None):
            request = getattr(factory, method)('/', payload or {}, format='json')
            force_authenticate(request, user=user or owner)
            ids = {'project_id': fixture['project_id']}
            if workspace is not None:
                ids['workspace_id'] = workspace.pk
            return view.as_view()(request, **ids)

        endpoints = {}
        for path, method in (('/login', 'POST'), ('/profile', 'GET'), ('/maintenance', 'GET'), ('/items', 'POST')):
            endpoints[path] = APIEndpoint.objects.create(
                spec_id=fixture['spec_id'], path=path, method=method, summary=path,
                responses={'200': {'description': 'OK'}},
            ).pk

        def step(path, *, token=False):
            request = {'method': 'POST' if path in {'/login', '/items'} else 'GET', 'url': path}
            if token:
                request['headers'] = {'Authorization': 'Bearer ${token}'}
            if path == '/items':
                request['json'] = {'name': 'fixture_${timestamp_ns}'}
            result = {'name': path, 'endpoint_id': endpoints[path], 'request': request,
                      'validate': [{'eq': ['status_code', 200]}]}
            if path == '/login':
                result['extract'] = {'token': 'body.token'}
            return result

        def draft(name, steps):
            return {'version': 1, 'config': {'name': name, 'variables': {}}, 'teststeps': steps}

        alpha = draft('读取账号', [step('/login'), step('/profile', token=True)])
        invalid_alpha = deepcopy(alpha)
        invalid_alpha['config']['headers'] = {'Authorization': 'Bearer ${token}'}
        # An empty assertion list is a separate static defect and must not be
        # papered over by running the invalid candidate or inventing a result.
        invalid_alpha['teststeps'][0]['validate'] = []
        unavailable = draft('不可用服务', [step('/maintenance')])
        beta = draft('独立新增', [step('/login'), step('/items', token=True)])
        plan = {'summary': '三个独立场景', 'scenarios': [
            {'title': '读取账号', 'description': '独立登录并读取账号',
             'endpoint_ids': [endpoints['/login'], endpoints['/profile']]},
            {'title': '不可用服务', 'description': '检查服务可用性', 'endpoint_ids': [endpoints['/maintenance']]},
            {'title': '独立新增', 'description': '重新登录并新增唯一数据',
             'endpoint_ids': [endpoints['/login'], endpoints['/items']]},
        ]}
        outputs = [plan, invalid_alpha, alpha, unavailable, beta]
        prompts, sent, sessions, queue_ids = [], [], [], []
        root = APIWorkspace.objects.create(
            owner=owner, project_id=fixture['project_id'], spec_id=fixture['spec_id'],
            model_id=fixture['model_id'], title='独立工作区名称',
            endpoint_ids=[fixture['endpoint_id'], *endpoints.values()],
        )

        def answer(messages, callback=None, **kwargs):
            payload = next(json.loads(item.content) for item in messages if item.content.lstrip().startswith('{'))
            prompts.append(payload)
            assert len(prompts) <= len(outputs), 'Unexpected extra model call'
            if len(prompts) == 2:
                assert APIWorkspace.objects.filter(parent=root).count() == 3, 'Plan was not published before generation'
                pending = APIWorkspace.objects.filter(parent=root).order_by('scenario_order').first()
                busy_edit = call(APIWorkspaceDetailView, 'patch', {'revision': pending.revision, 'draft': alpha}, pending)
                assert busy_edit.status_code == 409, 'Child was editable during the root pipeline'
            output = json.dumps(outputs[len(prompts) - 1], ensure_ascii=False)
            if callback:
                callback(output)
            return output

        def session_factory():
            identity = len(sessions) + 1
            sessions.append(identity)

            def request(**kwargs):
                sent.append((identity, kwargs))
                path = kwargs['url'].removeprefix('https://example.test')
                assert path in endpoints, kwargs['url']
                if path in {'/profile', '/items'}:
                    assert kwargs['headers']['Authorization'] == f'Bearer fixture-session-{identity}'
                    assert any(sid == identity and r['url'].endswith('/login') for sid, r in sent)
                response = Response()
                response.status_code = 503 if path == '/maintenance' else 200
                response._content = json.dumps({'token': f'fixture-session-{identity}'}).encode()
                response.headers, response.url, response.elapsed = {}, kwargs['url'], timedelta(milliseconds=1)
                return response

            return SimpleNamespace(request=request, close=lambda: None)

        def runner(**kwargs):
            remaining = kwargs.pop('hard_timeout_seconds', None)
            on_progress = kwargs.pop('on_progress', None)
            should_cancel = kwargs.pop('should_cancel', None)
            assert remaining and remaining <= 1800
            kwargs['options'] = {**(kwargs.get('options') or {}), 'total_timeout': min(600, remaining)}
            queue_ids.append(kwargs['script_id'])
            latest = {}

            class FixtureCancelled(Exception):
                pass

            def checkpoint(event):
                latest.update(deepcopy(event['report']))
                if on_progress:
                    on_progress(deepcopy(latest))
                if should_cancel and should_cancel():
                    raise FixtureCancelled()

            try:
                return run_case(**kwargs, on_checkpoint=checkpoint)
            except FixtureCancelled:
                from api_testing.requests_runtime import interrupted_report, normalize_case
                return interrupted_report(
                    kwargs['script_id'], normalize_case(kwargs['script_content']),
                    'Cancelled', '隔离多场景测试取消', partial_report=latest,
                )

        with patch.object(generate_and_verify_api_workspace, 'apply_async') as queue:
            reply = call(APIWorkspaceMessagesView, 'post', {
                'revision': root.revision, 'message': '生成三个独立场景，尽可能覆盖规范，新增数据必须唯一。',
                'execution_confirmed': True, 'base_url': 'https://example.test', 'variables': {},
            }, root)
        assert reply.status_code == 202, reply.data
        queued = queue.call_args.kwargs
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=answer)), patch(
            'api_testing.requests_runner.requests_runner', side_effect=runner,
        ), patch('api_testing.requests_runtime.requests.Session', side_effect=session_factory):
            generate_and_verify_api_workspace.apply(**queued)
            before = (len(prompts), len(sent))
            generate_and_verify_api_workspace.apply(**queued)
            assert before == (len(prompts), len(sent)), 'Duplicate root delivery replayed requests'

        root.refresh_from_db()
        children = list(root.scenarios.order_by('scenario_order', 'id'))
        assert len(children) == 3 and len(prompts) == 5, root.generation
        assert root.generation['status'] == 'partial', root.generation
        assert [item.generation['status'] for item in children] == ['passed', 'needs_review', 'passed']
        assert prompts[1]['current_scenario']['title'] == '读取账号'
        assert prompts[-1]['current_scenario']['title'] == '独立新增'
        # Children are created after planning.  They must retain their own
        # queue timestamp while sharing the root's batch budget;
        # equality with the root request timestamp would hide that distinction.
        root_snapshot = root.generation['_snapshot']
        root_batch_deadline = root_snapshot['deadlines']['batch_at']
        assert all(item.generation['_snapshot']['queue_managed_by_parent'] is True for item in children)
        assert all(item.generation['_snapshot']['queued_at'] == item.generation['queued_at'] for item in children)
        assert all(item.generation['_snapshot']['queued_at'] >= root_snapshot['claimed_at'] for item in children)
        assert all(item.generation['_snapshot']['deadlines']['queue_at'] is None for item in children)
        assert all(item.generation['_snapshot']['deadlines']['batch_at'] == root_batch_deadline for item in children)
        assert len(sent) == 5 and len(sessions) == 3, (sent, sessions)
        assert [entry['url'].split('/')[-1] for _, entry in sent] == ['login', 'profile', 'maintenance', 'login', 'items']
        assert prompts[2]['current_draft']['teststeps'][0]['validate'] == []
        assert children[0].generation['rounds'][0]['draft']['teststeps']
        assert APITestCase.objects.count() == 0, 'Pipeline auto-saved formal cases'
        coverage = serialize_workspace(root)['coverage']
        assert coverage['total'] == 5 and coverage['planned'] == 4, coverage
        assert coverage['generated'] == 4 and coverage['verified'] == 3, coverage
        assert fixture['endpoint_id'] in coverage['uncovered_endpoint_ids'], coverage
        # A planned endpoint is not proof of generated/executed coverage.
        # Deliberately include one extra planned endpoint on a passed scene.
        original_scope = children[0].endpoint_ids[:]
        APIWorkspace.objects.filter(pk=children[0].pk).update(endpoint_ids=[*original_scope, fixture['endpoint_id']])
        planned_only = serialize_workspace(root)['coverage']
        assert planned_only['generated'] == 4 and planned_only['verified'] == 3, planned_only
        APIWorkspace.objects.filter(pk=children[0].pk).update(endpoint_ids=original_scope)

        listing = call(APIWorkspaceCollectionView, 'get')
        assert [entry['id'] for entry in listing.data['data']] == [root.pk], listing.data
        # Renaming the workspace must not invalidate child verification.
        renamed = call(APIWorkspaceDetailView, 'patch', {'revision': root.revision, 'title': '用户自定义名称'}, root)
        assert renamed.status_code == 200, renamed.data
        root.refresh_from_db()
        assert root.title == '用户自定义名称'
        assert serialize_workspace(root)['coverage']['verified'] == 3

        # Adopt and save two independently; the failed scene remains untouched.
        for child in (children[0], children[2]):
            adopted = call(APIWorkspaceDetailView, 'patch', {'revision': child.revision, 'draft': child.candidate['draft']}, child)
            assert adopted.status_code == 200, adopted.data
            child.refresh_from_db()
            saved = call(APIWorkspaceSaveView, 'post', {'revision': child.revision, 'title': child.title + '正式用例'}, child)
            assert saved.status_code == 200, saved.data
            child.refresh_from_db()
            assert child.saved_case_id
        assert APITestCase.objects.count() == 2
        assert children[0].saved_case_id != children[2].saved_case_id
        root.refresh_from_db()
        assert root.title == '用户自定义名称'

        # Current-version proof must expire after a real script edit.
        child = children[0]
        changed = deepcopy(child.draft)
        changed['config']['name'] = '编辑后待重验'
        edited = call(APIWorkspaceDetailView, 'patch', {'revision': child.revision, 'draft': changed}, child)
        assert edited.status_code == 200, edited.data
        assert serialize_workspace(root)['coverage']['verified'] == 2

        # Exact ownership and busy checks precede cascading workspace deletion.
        stranger = get_user_model().objects.create_user(username='unrelated-workspace-owner', email='stranger@example.test')
        unauthorized = call(APIWorkspaceDetailView, 'delete', {'revision': root.revision, 'confirmed': True}, root, user=stranger)
        assert unauthorized.status_code in {403, 404}, unauthorized.data
        APIWorkspace.objects.filter(pk=children[1].pk).update(status='generating')
        busy = call(APIWorkspaceDetailView, 'delete', {'revision': root.revision, 'confirmed': True}, root)
        assert busy.status_code == 409, busy.data
        APIWorkspace.objects.filter(pk=children[1].pk).update(status='ready')
        deleted = call(APIWorkspaceDetailView, 'delete', {'revision': root.revision, 'confirmed': True}, root)
        assert deleted.status_code in {200, 204}, deleted.data
        assert not APIWorkspace.objects.filter(pk=root.pk).exists()
        assert not APIWorkspace.objects.filter(parent_id=root.pk).exists()
        assert APITestCase.objects.count() == 2, 'Deleting workspace deleted saved cases'
        assert APIEndpoint.objects.count() == 5, 'Deleting workspace deleted Swagger endpoints'

    print('PASS: independent multi-scenario planning, static repair, ordered isolated sessions, partial coverage, independent save and safe workspace management; no real services')


if __name__ == '__main__':
    main()
