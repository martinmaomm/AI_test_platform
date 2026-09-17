"""Browser smoke against localhost Vite with all API traffic stubbed.

No platform records or credentials are used; this checks UI wiring, not live APIs.
Run from backend: .venv/bin/python scripts/verify_performance_ui.py
"""
import base64
import json
import re
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
    runtime = {'enabled': False, 'run': None}
    installation = {
        'available': True,
        'reason': '',
        'platform_url': 'https://platform.fixture.test',
        'supported_architectures': ['amd64', 'arm64'],
        'agent_version': '0.2.0',
        'expires_at': None,
        'requirements': ['Linux', 'Docker', 'root 权限'],
    }
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
            segments = path.split('/performance/')[1].strip('/').split('/')
            kind = segments[0]
            if kind == 'config':
                data = {'execution_enabled': runtime['enabled'], 'controller_online': runtime['enabled'],
                        'phase': 'execution', 'heartbeat_interval_seconds': 5,
                        'execution_unavailable_reason': '' if runtime['enabled'] else '控制器尚未启动（本机模拟）',
                        'installation': installation}
            elif kind == 'nodes' and len(segments) == 3 and segments[2] == 'installation':
                node = next(item for item in lists['nodes'] if str(item['id']) == segments[1])
                # GET installation is intentionally non-sensitive: no enrollment token or command.
                data = {'node': node, 'installation': installation}
            elif kind == 'nodes' and len(segments) >= 2:
                node = next(item for item in lists['nodes'] if str(item['id']) == segments[1])
                body = request.post_data_json if request.method in ('POST', 'PATCH') else None
                action = segments[2] if len(segments) > 2 else request.method.lower()
                mutations.append((f'node_{action}', request.method, body))
                if action == 'enrollment':
                    assert not node.get('registered_at') and node['status'] == 'pending'
                    data = {'node': node, 'installation': {**installation,
                            'expires_at': '2099-09-16T23:59:00Z', 'command': 'mock-regenerated-command'}}
                elif action == 'patch':
                    assert set(body) == {'name', 'network_mode'}
                    node.update(body)
                    data = node
                elif action == 'revoke':
                    # Simulate a run starting after the row was fetched: confirmation must come from the user.
                    if runtime.pop('race_on_revoke', False):
                        node['active_run_count'] = 1
                    if node.get('active_run_count', 0) and not body.get('confirm_stop'):
                        route.fulfill(status=409, content_type='application/json', body=json.dumps({
                            'success': False, 'error': {'code': 'node_has_active_runs',
                                                       'message': '仍有 1 个未结束的测试，是否请求停止并吊销？', 'count': 1}}))
                        return
                    node['status'] = 'revoked'
                    data = node
                elif action == 'delete':
                    assert node['status'] == 'revoked' and node.get('active_run_count', 0) == 0
                    lists['nodes'].remove(node)
                    data = {'id': node['id']}
                else:
                    raise AssertionError(f'Unexpected node action: {action}')
            elif kind == 'plans' and len(segments) == 3 and segments[2] == 'runs':
                mutations.append(('runs', request.method, request.post_data_json))
                runtime['run'] = {
                    'id': 'b91d46dc-821f-4db9-b198-f917f34c2323', 'plan_name': '单用户样本计划',
                    'node_name': '本机验收节点', 'status': 'running', 'reason': '',
                    'created_at': '2026-09-16T10:00:00Z', 'started_at': '2026-09-16T10:00:01Z',
                    'latest_metrics': {'requests': 12, 'failures': 1, 'error_rate': 1 / 12,
                                       'rps': 2.4, 'p95': 25, 'p99': 30, 'avg_response_time': 12, 'users': 1,
                                       'entries': [{'name': '读取健康页', 'method': 'GET', 'requests': 12,
                                                    'failures': 1, 'avg_response_time': 12, 'p95': 25, 'p99': 30}]},
                    'metrics_samples': [{'timestamp': '2026-09-16T10:00:03Z',
                                         'metrics': {'rps': 2.4, 'failures': 1, 'p95': 25}}],
                }
                data = runtime['run']
            elif kind == 'runs':
                if segments[-1] == 'stop':
                    mutations.append(('stop', request.method, request.post_data_json))
                    runtime['run']['status'] = 'stopping'
                data = {'items': [runtime['run']]} if len(segments) == 1 else runtime['run']
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
                        row['active_run_count'] = 0
                        data = {'node': row, 'expires_at': '2099-09-16T23:59:00Z',
                                'installation': {**installation,
                                                 'expires_at': '2099-09-16T23:59:00Z',
                                                 'command': 'mock-installer-command --from-platform'}}
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'success': True, 'data': data}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/*', fulfill)
        context.add_init_script('localStorage.setItem("auth-store", ' + json.dumps(json.dumps(auth)) + ');'
                                'localStorage.setItem("project-store", ' + json.dumps(json.dumps({'currentProject': project})) + ');'
                                'Object.defineProperty(crypto, "randomUUID", {value: undefined});'
                                'Object.defineProperty(navigator, "clipboard", {value: undefined, configurable: true});'
                                'window.__copyFallbackCalls = 0;'
                                'document.execCommand = (command) => { window.__copyFallbackCalls += command === "copy" ? 1 : 0; return true; };')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto('http://127.0.0.1:5173/perf-testing/targets')
            expect(page.get_by_text('性能测试第一阶段：每次运行仅选择一个在线节点。')).to_be_visible()
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
            page.get_by_role('button', name='添加节点', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.locator('.el-form-item').filter(has_text='节点名称').locator('input').fill('本机验收节点')
            dialog.get_by_role('button', name='创建并查看安装命令', exact=True).click()
            guide = page.get_by_role('dialog', name='节点安装向导', exact=True)
            expect(guide).to_be_visible()
            expect(guide.locator('textarea')).to_have_value('mock-installer-command --from-platform')
            guide.get_by_role('button', name='复制安装命令', exact=True).click()
            expect(guide).to_be_visible()  # Copy must not close the guide.
            assert page.evaluate('window.__copyFallbackCalls') == 1
            guide.get_by_role('button', name='关闭', exact=True).click()
            expect(guide).not_to_be_visible()
            assert not any('mock-installer-command' in value for value in page.evaluate('Object.values(localStorage)'))
            page.get_by_role('button', name='安装指导', exact=True).click()
            guide = page.get_by_role('dialog', name='节点安装向导', exact=True)
            expect(guide).to_be_visible()
            expect(guide.locator('textarea')).to_have_count(0)
            assert 'mock-installer-command' not in guide.inner_text()
            lists['nodes'][0]['registered_at'] = '2026-09-16T10:00:00Z'
            expect(guide.get_by_text('节点已注册，等待首次心跳。', exact=True)).to_be_visible(timeout=12000)
            expect(guide.get_by_role('button', name='重新生成安装命令', exact=True)).to_have_count(0)
            lists['nodes'][0].update(status='online', agent_version='0.2.0', engine_version='2.43.3')
            expect(guide.get_by_text('节点已在线，可保留此页查看安装条件。', exact=True)).to_be_visible(timeout=12000)
            guide.get_by_role('button', name='关闭', exact=True).click()
            assert [item[0] for item in mutations] == ['targets', 'plans', 'nodes'], mutations
            assert mutations[1][2]['spawn_rate'] == 1
            assert mutations[1][2]['steps'][0]['path'] == '/probe'
            page.get_by_role('tab', name='压测计划', exact=True).click()
            expect(page.get_by_role('button', name='执行', exact=True)).to_be_disabled()
            runtime['enabled'] = True
            # Config polling must discover the controller without a browser reload.
            expect(page.get_by_role('button', name='执行', exact=True)).to_be_enabled(timeout=12000)
            page.get_by_role('button', name='执行', exact=True).click()
            run_dialog = page.get_by_role('dialog', name='确认执行压测', exact=True)
            expect(run_dialog).to_be_visible()
            run_dialog.get_by_role('button', name='确认执行', exact=True).click()
            expect(page.get_by_role('button', name='停止执行', exact=True)).to_be_visible()
            expect(page.get_by_text('8.33%', exact=True)).to_be_visible()
            page.get_by_role('button', name='停止执行', exact=True).click()
            page.wait_for_timeout(2500)  # A detail poll must not invalidate an open stop confirmation.
            page.get_by_role('button', name='停止', exact=True).click()
            expect(page.get_by_text('停止中', exact=True)).to_be_visible()
            runtime['run']['status'] = 'completed'
            expect(page.get_by_text('执行完成，存在失败请求。', exact=True)).to_be_visible(timeout=6000)
            page.get_by_role('button', name='返回执行记录', exact=True).click()
            expect(page).to_have_url(re.compile(r'/perf-testing/runs$'))
            expect(page.get_by_role('cell', name='单用户样本计划', exact=True)).to_be_visible()
            import uuid
            uuid.UUID(mutations[3][2]['request_id'])
            assert [item[0] for item in mutations] == ['targets', 'plans', 'nodes', 'runs', 'stop']

            # Lifecycle UI checks below are fully stubbed: no remote container or real load is touched.
            page.goto('http://127.0.0.1:5173/perf-testing/nodes')
            row = page.get_by_role('row').filter(has=page.get_by_role('cell', name='本机验收节点', exact=True))
            expect(row.get_by_role('button', name='重置身份', exact=True)).to_have_count(0)
            expect(row.get_by_role('button', name='删除', exact=True)).to_have_count(0)
            row.get_by_role('button', name='编辑', exact=True).click()
            edit = page.get_by_role('dialog', name='编辑节点', exact=True)
            expect(edit.get_by_text('高级选项', exact=True)).to_have_count(0)
            assert '创建后会提供一条安装命令' not in edit.inner_text()
            edit.locator('.el-form-item').filter(has_text='节点名称').locator('input').fill('本机验收节点-已修改')
            edit.get_by_role('button', name='保存', exact=True).click()
            row = page.get_by_role('row').filter(has=page.get_by_role('cell', name='本机验收节点-已修改', exact=True))
            expect(row).to_be_visible()

            # An expired or lost command on an unregistered node can be regenerated, without identity reset.
            lists['nodes'][0].update(status='pending', registered_at=None)
            row.get_by_role('button', name='安装指导', exact=True).click()
            guide = page.get_by_role('dialog', name='节点安装向导', exact=True)
            guide.get_by_role('button', name='重新生成安装命令', exact=True).click()
            # Element Plus keeps the closing box visible briefly while the next confirmation opens.
            message = page.locator('.el-message-box:visible').last
            expect(guide.locator('textarea')).to_have_value('mock-regenerated-command')
            guide.get_by_role('button', name='关闭', exact=True).click()

            # A stale zero-active count must not bypass the second confirmation on HTTP 409.
            runtime['race_on_revoke'] = True
            row.get_by_role('button', name='吊销', exact=True).click()
            message.locator('.el-message-box__btns button').last.click()
            expect(message).to_contain_text('停止')
            assert [entry[2] for entry in mutations if entry[0] == 'node_revoke'] == [{}]
            message.locator('.el-message-box__btns button').first.click()  # Cancel, no implicit retry.
            expect(message).not_to_be_visible()
            assert lists['nodes'][0]['status'] != 'revoked'
            assert [entry[2] for entry in mutations if entry[0] == 'node_revoke'] == [{}]
            page.reload()  # Fetch the now-known nonzero count for the next explicit attempt.
            row.get_by_role('button', name='吊销', exact=True).click()
            expect(message).to_contain_text('停止')
            message.locator('.el-message-box__btns button').last.click()
            expect(row.get_by_role('button', name='删除', exact=True)).to_be_disabled(timeout=10000)
            expect(row.get_by_role('button', name='安装指导', exact=True)).to_have_count(0)
            expect(row.get_by_role('button', name='编辑', exact=True)).to_have_count(0)
            assert [entry[2] for entry in mutations if entry[0] == 'node_revoke'][-1] == {'confirm_stop': True}
            lists['nodes'][0]['active_run_count'] = 0
            expect(row.get_by_role('button', name='删除', exact=True)).to_be_enabled(timeout=12000)
            row.get_by_role('button', name='删除', exact=True).click()
            expect(message).to_contain_text('保留历史')
            message.locator('.el-message-box__btns button').last.click()
            expect(page.get_by_text('暂无节点', exact=True)).to_be_visible()
            assert not errors, errors
            with tempfile.NamedTemporaryFile(prefix='performance-ui-', suffix='.png', delete=False) as image:
                page.screenshot(path=image.name, full_page=True)
                print(json.dumps({'mocked_browser_crud_and_execution': 'passed', 'mutations': len(mutations),
                                  'page_errors': errors, 'screenshot': str(Path(image.name).resolve())}))
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
