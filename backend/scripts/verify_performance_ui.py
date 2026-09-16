"""Browser smoke against localhost Vite with all API traffic stubbed.

No platform records or credentials are used; this checks UI wiring, not live APIs.
Run from backend: .venv/bin/python scripts/verify_performance_ui.py
"""
import base64
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    project = {'id': 987654, 'name': '性能页面验收样本', 'project_type': 'perf', 'members': []}
    user = {'id': 999999, 'username': 'local-fixture', 'role': 'admin', 'is_staff': True, 'is_superuser': False}
    payload = base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 600}).encode()).decode().rstrip('=')
    auth = {'accessToken': f'fixture.{payload}.fixture', 'refreshToken': '', 'user': user}
    lists = {'targets': [], 'plans': [], 'nodes': []}
    mutations = []
    errors = []

    def fulfill(route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.hostname != '127.0.0.1':
            route.abort()
            return
        if not parsed.path.startswith('/api/'):
            route.continue_()
            return
        path = parsed.path
        data = {}
        if path.endswith('/users/current-user/'):
            data = user
        elif path == f'/api/v1/projects/{project["id"]}/':
            data = project
        elif path.endswith('/projects/'):
            data = {'items': [project]}
        elif '/performance/' in path:
            kind = path.split('/performance/')[1].split('/')[0]
            if kind == 'config':
                data = {'execution_enabled': False, 'phase': 'node_access', 'heartbeat_interval_seconds': 5}
            elif kind in lists:
                if request.method == 'GET':
                    data = {'items': lists[kind]}
                else:
                    body = request.post_data_json
                    mutations.append((kind, request.method, body))
                    row = {**body, 'id': len(lists[kind]) + 1}
                    lists[kind].append(row)
                    data = row
                    if kind == 'nodes':
                        row['status'] = 'pending'
                        data = {'node': row, 'enrollment_token': 'ui-fixture-not-a-real-token',
                                'expires_at': '2026-09-16T23:59:00Z'}
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'success': True, 'data': data}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/*', fulfill)
        context.add_init_script('localStorage.setItem("auth-store", ' + json.dumps(json.dumps(auth)) + ');'
                                'localStorage.setItem("project-store", ' + json.dumps(json.dumps({'currentProject': project})) + ');')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto('http://127.0.0.1:5173/perf-testing/targets')
            expect(page.get_by_text('性能测试第一阶段：仅管理计划、受控目标和接入节点；当前没有压测执行能力。')).to_be_visible()
            page.get_by_role('button', name='新建目标', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.locator('.el-form-item').filter(has_text='名称').locator('input').fill('本机目标')
            dialog.locator('input[placeholder="https://example.com"]').fill('http://127.0.0.1:9991')
            dialog.get_by_role('button', name='保存', exact=True).click()
            expect(page.get_by_role('cell', name='本机目标', exact=True)).to_be_visible()
            page.get_by_role('tab', name='压测计划', exact=True).click()
            page.get_by_role('button', name='新建计划', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.locator('.el-form-item').filter(has_text='名称').first.locator('input').fill('单用户样本计划')
            dialog.locator('.el-select').first.click()
            page.get_by_role('option', name='本机目标', exact=True).click()
            dialog.get_by_placeholder('步骤名称').fill('读取健康页')
            dialog.get_by_placeholder('/relative-path').fill('/probe')
            dialog.get_by_role('button', name='保存', exact=True).click()
            expect(page.get_by_role('cell', name='单用户样本计划', exact=True)).to_be_visible()
            page.get_by_role('tab', name='节点管理', exact=True).click()
            page.get_by_role('button', name='登记节点', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.locator('.el-form-item').filter(has_text='名称').locator('input').fill('本机验收节点')
            dialog.get_by_role('button', name='保存', exact=True).click()
            token_dialog = page.get_by_role('dialog', name='一次性注册凭证', exact=True)
            expect(token_dialog).to_be_visible()
            expect(token_dialog.locator('textarea')).to_have_value('ui-fixture-not-a-real-token')
            token_dialog.get_by_role('button', name='关闭并清除', exact=True).click()
            expect(token_dialog).not_to_be_visible()
            assert not any('ui-fixture-not-a-real-token' in value for value in page.evaluate('Object.values(localStorage)'))
            assert [item[0] for item in mutations] == ['targets', 'plans', 'nodes'], mutations
            assert mutations[1][2]['spawn_rate'] == 1
            assert mutations[1][2]['steps'][0]['path'] == '/probe'
            assert not errors, errors
            with tempfile.NamedTemporaryFile(prefix='performance-ui-', suffix='.png', delete=False) as image:
                page.screenshot(path=image.name, full_page=True)
                print(json.dumps({'mocked_browser_crud': 'passed', 'mutations': len(mutations),
                                  'page_errors': errors, 'screenshot': str(Path(image.name).resolve())}))
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
