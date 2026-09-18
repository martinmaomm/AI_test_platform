"""Real Vue/Django discovery-to-plan acceptance; simulated capture, no model/load.

Only a disposable SQLite database and loopback UI are used. The mocked dispatch
creates known captured records; task routes, serialization, draft compilation,
frontend selection and plan save are real. This is not MCP/provider acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_performance_plan_browser import BACKEND, bootstrap, guarded_application, loopback_only
from test_project_knowledge_browser import _QuietHandler


def complete_capture(task):
    from api_testing.models import BrowserDiscoveryRecord, BrowserDiscoveryTask
    from django.utils import timezone
    task.status = BrowserDiscoveryTask.Status.COMPLETED
    task.version = task.source_version = 7
    task.api_origin = 'https://fixture.invalid'
    task.started_at = task.finished_at = task.heartbeat_at = timezone.now()
    task.current_action = '隔离夹具：采集完成'
    task.summary = '已采集两种查询参数样本。'
    task.save()
    for sequence, keyword in enumerate(('first-item', 'second-item'), 1):
        BrowserDiscoveryRecord.objects.create(
            task=task, sequence=sequence, request_id=f'fixture-{sequence}',
            origin=task.api_origin, method='GET', path='/items', status_code=200,
            resource_type='fetch', is_eligible=True,
            public_summary={
                'source_authorized': True, 'capture_complete': True,
                'method': 'GET', 'path': '/items', 'origin': task.api_origin,
                'observed_request': {'query': [{'name': 'keyword', 'value': keyword}],
                                     'headers': {}, 'auth_hints': [], 'json': None, 'form': []},
                'observed_response': {'headers': {}, 'auth_hints': [],
                                      'body': {'items': [{'id': sequence, 'name': keyword}]}},
            },
        )
    return True


def verify(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    base = f"/api/v1/projects/{fixture['project']['id']}/performance"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True,
        )
        context = browser.new_context(viewport={'width': 1440, 'height': 1080})
        context.set_default_timeout(12000)
        context.add_init_script(
            "localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
            "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));"
        )
        external, errors, drafts = [], [], []

        def intercept(route):
            if route.request.url.startswith(origin + '/'):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: drafts.append(req.post_data_json)
                if req.method == 'POST' and req.url.endswith('/draft/') else None)
        try:
            page.goto(origin + '/perf-testing/discovery')
            expect(page.get_by_text('新建网页探索', exact=True)).to_be_visible()
            form = page.locator('.el-form')
            expect(form.get_by_role('spinbutton')).to_have_value('900')
            origin_switch = form.get_by_role('switch', name='自动允许跨域请求', exact=True)
            expect(origin_switch).to_be_checked()
            form.locator('.el-form-item').filter(has_text=re.compile('^目标页面')).locator('input').fill('https://fixture.invalid/app')
            form.locator('textarea').fill('探索列表查询，仅使用本轮测试数据。')
            form.locator('.el-checkbox').click()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/discovery/tasks/')) as created:
                form.get_by_role('button', name='开始探索', exact=True).click()
            assert created.value.status == 202, created.value.text()
            task = created.value.json()['data']
            assert task['exploration_timeout_seconds'] == 900
            assert task['auto_approve_origins'] is True
            assert task['version'] == 7
            form.locator('.el-switch').click()
            expect(origin_switch).not_to_be_checked()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/discovery/tasks/')) as manual:
                form.get_by_role('button', name='开始探索', exact=True).click()
            assert manual.value.status == 202, manual.value.text()
            assert manual.value.json()['data']['auto_approve_origins'] is False
            expect(page.get_by_text('已采集两种查询参数样本。', exact=False)).to_be_visible()
            # Selecting a table row, not just its action, must use the task ID.
            page.locator('.el-table__body tbody tr').first.click()
            page.get_by_role('button', name='查看样本', exact=True).click()
            expect(page.locator('.group')).to_have_count(1)
            group = page.locator('.group')
            expect(group.get_by_role('checkbox')).to_have_count(2)
            group.locator('.el-checkbox').nth(1).click()
            page.locator('.detail .actions .el-select').click()
            page.get_by_role('option', name=re.compile('本机样本目标')).click()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/draft/')) as response:
                page.get_by_role('button', name='生成计划草稿', exact=True).click()
            assert response.value.status == 200, response.value.text()
            assert drafts[-1]['version'] == 7
            expect(page).to_have_url(origin + '/perf-testing/plans')
            editor = page.get_by_test_id('performance-plan-editor')
            expect(editor).to_be_visible()
            # Neither request data nor credentials should be put into navigation URL.
            assert not page.url.partition('?')[2]
            expect(page.get_by_test_id('step-query-rows').locator('input').nth(1)).to_have_value('second-item')
            expect(editor).to_contain_text('业务成功语义')
            page.screenshot(path=str(output / 'imported-draft.png'), full_page=True)
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(base + '/plans/')) as saved:
                page.get_by_test_id('plan-save').click()
            assert saved.value.status == 201, saved.value.text()
            plan = saved.value.json()['data']
            assert plan['steps'][0]['query']['keyword'] == 'second-item'
            assert len(plan['steps']) == 1
            assert plan['steps'][0]['assertions'][0]['expected'] == 200
            page.reload()
            expect(editor).not_to_be_visible()
            assert not external, external
            assert not errors, errors
            return {'real_http_routes': True, 'default_timeout': 900, 'automatic_origins_default': True,
                    'manual_origins_option_verified': True, 'task_version': 7, 'variant_selected': 'second-item',
                    'draft_in_url': False, 'draft_consumed_once': True, 'warnings_visible': True,
                    'saved_plan_id': plan['id'], 'page_errors': 0, 'model_calls': 0, 'pressure_runs': 0}
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'temp' / 'performance-discovery-browser'
    output.mkdir(parents=True, exist_ok=True)
    attempted_runs = []
    with tempfile.TemporaryDirectory(prefix='performance-discovery-browser-') as temporary, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temporary))
        from django.conf import settings
        from django.core.wsgi import get_wsgi_application
        from ai_core.models import LLMConfiguration
        from performance_testing.models import PerformanceRun, PerformanceTarget
        settings.API_BROWSER_DISCOVERY_ENABLED = True
        LLMConfiguration.objects.create(
            created_by_id=fixture['auth']['user']['id'], model_type='llm', provider='openai',
            provider_name='隔离夹具', model_name='fixture-only', api_key='not-a-real-key', is_active=True,
        )
        PerformanceTarget.objects.create(
            project_id=fixture['project']['id'], name='本机样本目标',
            base_url='https://fixture.invalid', allowed_methods=['GET'],
        )
        with patch('performance_testing.discovery_views.dispatch_performance_discovery', side_effect=complete_capture), patch(
            'performance_testing.discovery_views.resolve_browser_discovery_mcp_config', return_value={},
        ):
            server = make_server('127.0.0.1', 0, guarded_application(get_wsgi_application(), attempted_runs), handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
                assert not attempted_runs and not PerformanceRun.objects.exists()
                print(json.dumps(result, ensure_ascii=False))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == '__main__':
    main()
