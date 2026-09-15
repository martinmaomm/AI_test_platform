"""Browser workspace navigation against disposable Django + real Vue, no live jobs."""
import json
import re
import socket
import tempfile
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_api_browser_discovery_browser import bootstrap, database
from test_platform_reports_browser import CHROME, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django

BACKEND = Path(__file__).resolve().parent.parent


def seed(fixture):
    from django.utils import timezone
    from django.contrib.auth import get_user_model
    from api_testing.models import BrowserDiscoveryTask, BrowserDiscoveryRecord
    from api_testing.browser_discovery import handoff_to_workspace
    owner = get_user_model().objects.get(pk=fixture['user_id'])
    tasks = []
    workspaces = []
    for index in range(3):
        task = BrowserDiscoveryTask.objects.create(
            project_id=fixture['project_id'], owner=owner, model_id=fixture['model_id'],
            task_id=str(uuid.uuid4()), target_url='https://app.example.test/same-page',
            description=f'独立目标 {index + 1}', api_origin='https://api.example.test',
            allow_test_data_writes=True, status='completed', version=2, source_version=2,
            started_at=timezone.now()-timedelta(minutes=index+1), finished_at=timezone.now(),
            limits={'origin_mode': 'manual'},
        )
        record = BrowserDiscoveryRecord.objects.create(
            task=task, sequence=1, request_id=f'fixture-{index}', origin=task.api_origin,
            method='GET', path=f'/observed/{index+1}', resource_type='fetch', is_eligible=True,
            status_code=200, content_type='application/json', public_summary={
                'source_authorized': True, 'capture_complete': True,
                'observed_request': {'method': 'GET', 'path': f'/observed/{index+1}', 'query': []},
                'observed_response': {'status_code': 200, 'body': {'value': index+1}},
            },
        )
        tasks.append(str(task.id))
        if index < 2:
            handoff, _ = handoff_to_workspace(task=task, owner=owner, version=2, record_ids=[record.id])
            workspaces.append(handoff.workspace_id)
    fixture.update(task_ids=tasks, workspace_ids=workspaces)


def verify(origin, fixture, output, dispatch):
    from playwright.sync_api import sync_playwright, expect
    from api_testing.models import APIWorkspace, BrowserDiscoveryTask, APITestExecution
    api = f'/api/v1/projects/{fixture["project_id"]}/api-testing/'
    task_a, task_b, task_c = fixture['task_ids']
    workspace_a, workspace_b = fixture['workspace_ids']
    errors = []
    writes = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1600, 'height': 1080})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'browser-discovery-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on('pageerror', lambda err: errors.append(str(err)))
        page.on('request', lambda req: writes.append((req.method, req.url)) if req.method not in ('GET','HEAD','OPTIONS') else None)
        path = origin + '/api-testing/workspace/browser'
        form = page.get_by_test_id('api-browser-discovery-create-form')
        detail = page.locator('.browser-discovery-panel .task-detail')
        editor = page.locator('.workspace-main')

        def row(task_id):
            return page.locator('.browser-discovery-panel .el-table__row').filter(has_text=task_id[:8])

        def listing():
            page.get_by_test_id('api-browser-discovery-list').click()
            expect(page).to_have_url(path)
            expect(row(task_a)).to_be_visible()
            expect(editor).to_have_count(0)
            expect(form).to_have_count(0)
            expect(detail).to_have_count(0)

        try:
            page.goto(path)
            expect(row(task_a)).to_be_visible()
            expect(row(task_b)).to_be_visible()
            expect(page.get_by_test_id('api-workspace-select')).to_have_count(0)
            expect(editor).to_have_count(0)
            expect(form).to_have_count(0)
            expect(row(task_a)).to_contain_text(re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'))
            page.screenshot(path=str(output/'task-list.png'), full_page=True)

            row(task_a).get_by_role('button', name='查看', exact=True).click()
            expect(page).to_have_url(path + f'?discovery_id={task_a}')
            expect(detail).to_contain_text('独立目标 1')
            expect(detail).to_contain_text('开始时间')
            expect(detail).to_contain_text('GET /observed/1')
            expect(editor).to_have_count(0)
            expect(form).to_have_count(0)
            page.reload()
            expect(detail).to_contain_text('独立目标 1')

            # The handoff is a navigation read, not a fresh publication/generation.
            before = len(writes)
            detail.get_by_role('button', name=re.compile('网页探索工作区')).click()
            expect(page).to_have_url(path + f'?workspace_id={workspace_a}')
            expect(editor).to_be_visible()
            expect(page.get_by_test_id('api-browser-discovery-panel')).to_have_count(0)
            expect(page.get_by_test_id('api-workspace-source-readonly')).to_contain_text('网页探索发现')
            assert len(writes) == before
            page.get_by_test_id('api-browser-source-task').click()
            expect(detail).to_contain_text('独立目标 1')

            listing()
            row(task_b).get_by_role('button', name=re.compile('网页探索工作区')).click()
            expect(page).to_have_url(path + f'?workspace_id={workspace_b}')
            expect(editor).to_be_visible()
            page.get_by_test_id('api-browser-source-task').click()
            expect(detail).to_contain_text('独立目标 2')
            expect(detail).not_to_contain_text('独立目标 1')
            page.go_back()
            expect(page).to_have_url(path + f'?workspace_id={workspace_b}')
            expect(editor).to_be_visible()
            page.go_forward()
            expect(detail).to_contain_text('独立目标 2')

            # A fresh form neither shows nor overwrites old task/workspace state.
            page.get_by_test_id('api-browser-discovery-new').click()
            expect(page).to_have_url(path + '?view=new')
            expect(form).to_be_visible()
            expect(form.get_by_role('textbox', name='完整页面 URL', exact=True)).to_have_value('')
            expect(detail).to_have_count(0)
            expect(editor).to_have_count(0)
            expect(page.get_by_test_id('api-workspace-select')).to_have_count(0)
            form.get_by_role('textbox', name='探索目标说明', exact=True).fill('尚未提交的新目标')
            page.get_by_test_id('api-browser-discovery-list').click()
            prompt = page.locator('.el-message-box:visible')
            expect(prompt).to_be_visible()
            prompt.get_by_role('button', name='取消', exact=True).click()
            expect(form.get_by_role('textbox', name='探索目标说明', exact=True)).to_have_value('尚未提交的新目标')
            page.get_by_test_id('api-browser-discovery-list').click()
            prompt.get_by_role('button', name='继续', exact=True).click()
            expect(row(task_c)).to_be_visible()

            # Handoff opens confirmation only. Cancel + refresh must not dispatch a job.
            row(task_c).get_by_role('button', name='查看', exact=True).click()
            expect(detail).to_contain_text('GET /observed/3')
            page.locator('.sample-select .el-checkbox').click()
            with page.expect_response(lambda r: r.url.endswith('/handoff/') and r.request.method == 'POST') as handoff:
                page.get_by_role('button', name='确认接口并生成场景', exact=True).click()
            assert handoff.value.status == 201
            new_workspace_id = handoff.value.json()['data']['workspace']['id']
            dialog = page.get_by_role('dialog', name='生成并验证确认', exact=True)
            expect(dialog).to_be_visible()
            expect(dialog).to_have_css('opacity', '1')
            expect(dialog).to_be_in_viewport()
            expect(dialog).to_contain_text('GET /observed/3')
            expect(dialog.get_by_role('textbox', name='目标地址', exact=True)).to_have_value('https://api.example.test')
            assert not any(url.endswith('/messages/') for _, url in writes)
            assert dispatch.call_count == 0
            page.screenshot(path=str(output/'handoff-confirmation.png'), full_page=True, animations='disabled')
            dialog.get_by_role('button', name='取消', exact=True).click()
            page.reload()
            expect(editor).to_be_visible()
            expect(dialog).to_have_count(0)
            assert dispatch.call_count == 0
            assert database(lambda: APIWorkspace.objects.get(pk=new_workspace_id).messages)[0]['content'] == '独立目标 3'

            # Stale samples from A must never overwrite B when returning later.
            page.get_by_test_id('api-browser-source-task').click()
            expect(detail).to_contain_text('独立目标 3')
            held = []
            record_url = re.compile(re.escape(api + f'browser-discoveries/{task_c}/records/') + r'.*')
            def hold(route):
                held.append((route, route.fetch()))
            page.route(record_url, hold)
            page.get_by_role('button', name='查看已授权样本', exact=True).click()
            for _ in range(100):
                if held: break
                page.wait_for_timeout(20)
            assert held
            listing()
            row(task_b).get_by_role('button', name='查看', exact=True).click()
            expect(detail).to_contain_text('独立目标 2')
            for route, response in held:
                try: route.fulfill(response=response)
                except Exception: pass  # Departed component may have aborted its read.
            page.unroute(record_url, hold)
            expect(detail).not_to_contain_text('GET /observed/3')
            expect(detail).to_contain_text('GET /observed/2')

            # Starting a new task is explicit and creates another UUID, no old overwrite.
            page.get_by_test_id('api-browser-discovery-new').click()
            expect(form).to_be_visible()
            form.get_by_role('textbox', name='完整页面 URL', exact=True).fill('https://app.example.test/new')
            form.get_by_role('textbox', name='探索目标说明', exact=True).fill('新任务目标')
            form.get_by_text('我确认允许在上述授权测试范围内修改测试数据', exact=True).click()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/browser-discoveries/')) as creation:
                form.get_by_role('button', name='开始探索', exact=True).click()
            assert creation.value.status == 202
            new_task_id = creation.value.json()['data']['id']
            expect(page).to_have_url(path + '?discovery_id=' + new_task_id)
            expect(detail).to_contain_text('新任务目标')
            assert new_task_id not in fixture['task_ids'] and dispatch.call_count == 1
            assert database(lambda: BrowserDiscoveryTask.objects.get(pk=task_a).description) == '独立目标 1'
            expect(editor).to_have_count(0)
            expect(form).to_have_count(0)

            # Invalid or missing IDs cannot fall back to a previous workspace.
            page.goto(path + '?discovery_id=bad')
            expect(page.get_by_text('探索任务 ID 无效', exact=False).first).to_be_visible()
            expect(editor).to_have_count(0)
            page.goto(path + '?discovery_id=' + str(uuid.uuid4()))
            expect(page.get_by_text('探索任务不存在、已删除或无权访问', exact=False)).to_be_visible()
            expect(editor).to_have_count(0)
            assert database(lambda: APITestExecution.objects.count()) == 0
            assert not errors, errors
            (output/'result.json').write_text(json.dumps({'passed': True, 'page_errors': errors,
                'old_workspaces': fixture['workspace_ids'], 'handoff_workspace': new_workspace_id,
                'new_task_count': dispatch.call_count, 'generation_dispatches': 0,
                'checks': ['independent-views','timestamps','source-links','refresh','back-forward',
                           'dirty-cancel','handoff-confirm-cancel','stale-response','new-task','invalid-id']}, indent=2), encoding='utf-8')
        except Exception:
            page.screenshot(path=str(output/'failure.png'), full_page=True)
            print({'page_errors': errors}, flush=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND/'logs/api-browser-workspace-navigation'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='api-browser-navigation-') as tmp, patch.object(socket.socket,'connect',loopback_only):
        fixture = bootstrap(Path(tmp))
        from django.conf import settings
        settings.API_BROWSER_DISCOVERY_ENABLED = True
        seed(fixture)
        from django.core.wsgi import get_wsgi_application
        with patch('api_testing.browser_discovery.resolve_browser_discovery_mcp_config',return_value={'mcpServers': {}}), \
             patch('api_testing.tasks.run_browser_discovery_async.apply_async',return_value=SimpleNamespace(id='fixture-only')) as dispatch:
            server = make_server('127.0.0.1',0,_static_or_django(get_wsgi_application()),handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                verify(f'http://127.0.0.1:{server.server_port}',fixture,output,dispatch)
            finally:
                server.shutdown(); thread.join(timeout=5); server.server_close()
    print(f'PASS: browser task/workspace navigation; evidence: {output}')


if __name__ == '__main__':
    main()
