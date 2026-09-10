"""Real MCP network capture -> existing workspace -> fresh requests/Python.

Uses a disposable database, a synthetic loopback website and deterministic model
answers. It proves integration, not real-model reasoning. No NAS, Redis or
provider calls are made. The installed pinned MCP and Chrome are required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from api_browser_discovery_fixture import running_fixture
from test_api_workspace_browser import bootstrap, BACKEND, CHROME
from test_platform_reports_browser import loopback_only


def scripted_model(origin):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import PrivateAttr

    class ScriptedBrowserModel(BaseChatModel):
        _index: int = PrivateAttr(default=0)

        @property
        def _llm_type(self):
            return 'browser-discovery-loopback-fixture'

        def bind_tools(self, tools, **kwargs):
            from api_testing.browser_discovery_agent import ALLOWED_BROWSER_TOOLS

            names = {tool.name for tool in tools}
            assert names <= ALLOWED_BROWSER_TOOLS, ('Unexpected model-visible tools', names - ALLOWED_BROWSER_TOOLS)
            navigate = next(tool for tool in tools if tool.name == 'playwright_navigate')
            assert 'headless' not in navigate.args, 'The model must not configure browser headless mode'
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            operations = [
                # Reproduce the real failure: no model-supplied headless value.
                # The platform must add it before calling the actual MCP tool.
                ('playwright_navigate', {'url': origin + '/app?tenant=fixture#/items'}),
                ('playwright_fill', {'selector': '#username', 'value': 'fixture-user'}),
                ('playwright_fill', {'selector': '#password', 'value': 'fixture-password'}),
                ('playwright_click', {'selector': '#login'}),
                ('playwright_get_visible_text', {}),
                ('playwright_fill', {'selector': '#name', 'value': 'capture-only-item'}),
                ('playwright_click', {'selector': '#add'}),
                ('playwright_get_visible_text', {}),
                ('playwright_click', {'selector': 'button:has-text("编辑")'}),
                ('playwright_fill', {'selector': '#edit-name', 'value': 'capture-only-edited'}),
                ('playwright_click', {'selector': '#save-edit'}),
                ('playwright_get_visible_text', {}),
                ('playwright_click', {'selector': 'button:has-text("删除")'}),
                ('playwright_get_visible_text', {}),
                ('playwright_fill', {'selector': '#query', 'value': 'capture-only-edited'}),
                ('playwright_click', {'selector': '#search'}),
                ('playwright_get_visible_text', {}),
            ]
            index = self._index
            self._index += 1
            message = AIMessage(content='已完成本轮新增、修改、删除和查询，页面显示暂无数据。')
            if index < len(operations):
                name, arguments = operations[index]
                message = AIMessage(content='', tool_calls=[{'name': name, 'args': arguments, 'id': f'fixture-{index}', 'type': 'tool_call'}])
            return ChatResult(generations=[ChatGeneration(message=message)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return self._generate(messages, stop, run_manager, **kwargs)
    return ScriptedBrowserModel()


def case_for(endpoints, origin):
    def endpoint(method, path_prefix):
        return next(item['id'] for item in endpoints if item['method'] == method and item['path'].startswith(path_prefix))

    def step(name, method, url, *, json_body=None, params=None, extract=None, validate=None):
        request = {'method': method, 'url': url}
        if url != '/api/login':
            request['headers'] = {'Authorization': 'Bearer ${token}'}
        if json_body is not None:
            request['json'] = json_body
        if params is not None:
            request['params'] = params
        return {'name': name, 'endpoint_id': endpoint(method, url.split('${')[0]), 'request': request,
                'extract': extract or {}, 'validate': [{'eq': ['status_code', 200]}, {'eq': ['body.code', 200]}] + (validate or [])}

    return {'version': 1, 'config': {'name': '网页发现接口独立生命周期', 'base_url': origin,
            'variables': {'unique_name': 'replay_${timestamp_ns}', 'edited_name': 'edited_${timestamp_ns}'}},
        'teststeps': [
            step('重新登录', 'POST', '/api/login', json_body={'username': 'fixture-user', 'password': 'fixture-password'}, extract={'token': 'body.data.token'}),
            step('新增本轮数据', 'POST', '/api/items', json_body={'name': '${unique_name}'}, extract={'item_id': 'body.data.id'}, validate=[{'eq': ['body.data.name', '${unique_name}']}]),
            step('修改本轮数据', 'PATCH', '/api/items/${item_id}', json_body={'name': '${edited_name}'}, validate=[{'eq': ['body.data.name', '${edited_name}']}]),
            step('按唯一名称查询', 'GET', '/api/items', params={'name': '${edited_name}'}, validate=[{'eq': ['body.data.total', 1]}, {'eq': ['body.data.items[0].id', '${item_id}']}]),
            step('删除本轮数据', 'DELETE', '/api/items/${item_id}'),
            step('精确查询确认不存在', 'GET', '/api/items', params={'name': '${edited_name}'}, validate=[{'eq': ['body.data.total', 0]}, {'eq': ['body.data.items', []]}]),
        ]}


def main():
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    output = BACKEND / 'logs' / 'api-browser-discovery-e2e'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='automation-browser-discovery-e2e-') as temp, running_fixture(split_api=True) as site, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        # This integration deliberately sends real loopback HTTP. The source
        # bootstrap blocks *all* subprocess sockets for UI-only smoke tests;
        # here requests uses its exact fixture-origin guard and redirects off.
        os.environ.pop('OFFLINE_TEST_NETWORK', None)
        from django.conf import settings
        settings.API_BROWSER_DISCOVERY_ENABLED = True
        from django.contrib.auth import get_user_model
        from django.db import transaction
        from ai_core.models import MCPConfiguration
        from api_testing.models import BrowserDiscoveryTask, APIWorkspace, APITestCase
        from api_testing.browser_discovery import discovery_limits, handoff_to_workspace, task_trace_file
        from api_testing.tasks import run_browser_discovery_async
        from api_testing.workspace_service import endpoint_specs
        from api_testing.workspace_views import _queue_pipeline
        from api_testing.workspace_tasks import generate_and_verify_api_workspace
        from api_testing.requests_runner import requests_runner
        from api_testing.requests_runtime import export_python

        owner = get_user_model().objects.get(pk=fixture['user_id'])
        MCPConfiguration.objects.create(created_by=owner, name='playwright', raw_config=json.dumps({'mcpServers': {'playwright': {
            'command': 'npx', 'args': ['--offline', '-y', '@executeautomation/playwright-mcp-server@1.0.12'],
            'env': {'CHROME_EXECUTABLE_PATH': str(CHROME)}, 'timeout': 45,
        }}}))
        task = BrowserDiscoveryTask.objects.create(
            owner=owner, project_id=fixture['project_id'], model_id=fixture['model_id'],
            target_url=site.origin + '/app?tenant=fixture#/items', api_origin='',
            description='使用 fixture-user / fixture-password 登录，探索物品新增、修改、删除和精确查询，只操作本轮数据。',
            allow_test_data_writes=True, exploration_timeout_seconds=120, limits={**discovery_limits(), 'origin_mode': 'auto'},
            task_id=str(__import__('uuid').uuid4()),
        )
        manager = SimpleNamespace(current_llm=scripted_model(site.origin))
        with patch('ai_core.model_manager.get_llm_manager', return_value=manager):
            run_browser_discovery_async.run(str(task.id), task.version, task.task_id)
        task.refresh_from_db()
        assert task.status == 'completed', (task.status, task.error_code, task.error_message, task.evidence_summary)
        mcp_log = BACKEND / 'logs' / 'playwright-mcp' / f'{task.task_id}.log'
        calls = [
            row.get('context', {}).get('body', {}).get('params', {})
            for row in map(json.loads, mcp_log.read_text(encoding='utf-8').splitlines())
            if row.get('message') == 'Incoming CallTool request'
        ]
        navigations = [call for call in calls if call.get('name') == 'playwright_navigate']
        assert len(navigations) == 1, 'Navigation was missing or retried'
        assert navigations[0]['arguments']['headless'] is True, 'MCP did not receive platform-controlled headless mode'
        assert task.api_origin == site.api_origin, ('automatic origin resolution failed', task.api_origin)
        records = list(task.records.filter(is_eligible=True).order_by('sequence'))
        assert records, 'No eligible real browser records'
        assert any(record.path == '/api/login' for record in records)
        assert any(record.method == 'PATCH' for record in records)
        traffic = [json.loads(line) for line in task_trace_file(task).read_text().splitlines()]
        login = next(row for row in traffic if row.get('event') == 'request' and row.get('path') == '/api/login')
        login_body = next(row for row in traffic if row.get('event') == 'request_body' and row.get('request_id') == login['request_id'])
        login_response = next(row for row in traffic if row.get('event') == 'response' and row.get('request_id') == login['request_id'])
        assert login_body['body']['value']['username'] == 'fixture-user'
        assert login_response['body']['value']['data']['token']
        assert not site.items, 'Browser exploration left fixture data'
        assert sum(item['path'] == '/api/login' for item in site.ledger) == 1, 'Capture replayed the login'
        capture_ledger_length = len(site.ledger)
        run_browser_discovery_async.run(str(task.id), task.version, task.task_id)
        assert len(site.ledger) == capture_ledger_length, 'Duplicate task delivery replayed website operations'
        with transaction.atomic():
            handoff, created = handoff_to_workspace(task=task, owner=owner, version=task.version, record_ids=[record.id for record in records])
            again, repeated = handoff_to_workspace(task=task, owner=owner, version=task.version, record_ids=[record.id for record in records])
        assert created and not repeated and handoff.pk == again.pk
        workspace = APIWorkspace.objects.get(pk=handoff.workspace_id)
        endpoints = endpoint_specs(workspace.project_id, workspace.endpoint_ids, spec_id=workspace.spec_id)
        candidate = case_for(endpoints, site.api_origin)
        assert not APITestCase.objects.exists(), 'Handoff saved a formal case without adoption'
        assert len(site.ledger) == capture_ledger_length, 'Handoff executed an API request'
        # The generation stage is deterministic in this harness, but static
        # source validation and the platform requests worker are real.
        site.tokens.clear()
        with patch.object(generate_and_verify_api_workspace, 'apply_async'), transaction.atomic():
            _queue_pipeline(workspace, revision=workspace.revision, mode='generate', target_url=site.api_origin,
                            variables={}, endpoints=endpoints)
        model = SimpleNamespace(stream_invoke=lambda *args, **kwargs: json.dumps(candidate))
        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=model):
            generate_and_verify_api_workspace.run(workspace.id, workspace.revision, workspace.task_id)
        workspace.refresh_from_db()
        assert workspace.generation.get('status') == 'passed', workspace.generation
        accepted = workspace.candidate['draft']
        for index in range(2):
            site.tokens.clear()
            result = requests_runner(f'fresh-replay-{index}', json.dumps(accepted), options={'allowed_origin': site.api_origin})
            assert result['success'], result.get('error')
            assert not site.items, 'Independent replay did not remove its own data'
        site.tokens.clear()
        exported = Path(temp) / 'test_browser_api.py'
        exported.write_text(export_python(accepted), encoding='utf-8')
        completed = subprocess.run([sys.executable, str(exported)], capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, 'Standalone Python export failed'
        assert not site.items
        names = [row['name'] for row in site.ledger if row['method'] == 'POST' and row['path'] == '/api/items']
        assert len(names) == len(set(names)), 'Execution reused a captured data name'
        report = {
            'capture_status': task.status, 'tool_calls': task.tool_calls, 'model_calls': task.model_calls,
            'records': len(records), 'endpoints': len(endpoints), 'capture_ledger_requests': capture_ledger_length,
            'generation_verification': 'passed', 'fresh_session_replays': 2, 'python_export': 'passed',
            'duplicate_delivery': 'ignored', 'handoff_idempotency': 'passed', 'remaining_items': len(site.items),
            'independent_unique_names': len(names), 'trace_file': str(task_trace_file(task)),
            'auto_origin_cross_port': 'passed', 'first_login_body_and_response': 'captured',
            'platform_headless_without_model_argument': 'passed', 'model_tool_whitelist': 'passed',
            'scope': 'Real loopback website and MCP; deterministic model; no live provider/NAS/Redis',
        }
        (output / 'summary.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
