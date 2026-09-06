"""Isolated schedule form regression. Build frontend first; no real execution."""
from pathlib import Path
import json
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


def add_schedule_fixtures(fixture):
    from django.contrib.auth import get_user_model
    from projects.models import Project, Environment
    from web_testing.models import WebUITestSuite, WebUITestCase
    from api_testing.models import APITestSuite, APITestCase
    owner = get_user_model().objects.get(pk=fixture['user_id'])
    web = Project.objects.get(pk=fixture['project_id'])
    api = Project.objects.get(pk=fixture['api_project_id'])
    case = WebUITestCase.objects.create(title='离线用例', user=owner, project=web)
    suites = []
    for name in ['套件甲', '套件乙']:
        suite = WebUITestSuite.objects.create(name=name, user=owner, project=web)
        suite.test_cases.add(case)
        suites.append(suite.id)
    WebUITestSuite.objects.create(name='空套件不可选', user=owner, project=web)
    api_suite = APITestSuite.objects.create(name='API 专属套件', user=owner, project=api)
    api_case = APITestCase.objects.create(title='API fixture', project=api, created_by=owner, test_case_type='scenario')
    api_suite.test_cases.add(api_case)
    Environment.objects.create(project=api, name='API 专属环境', category='api', config={'base_url': 'https://fixture.invalid'})
    fixture.update(web_project={'id': web.pk, 'name': web.name, 'project_type': 'web'},
                   api_project={'id': api.pk, 'name': api.name, 'project_type': 'api'},
                   suites=suites)


def verify(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        def context_for(project):
            context = browser.new_context(viewport={'width': 1440, 'height': 1080})
            context.set_default_timeout(12000)
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
            context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
                'accessToken': fixture['token'], 'refreshToken': None,
                'user': {'id': fixture['user_id'], 'username': 'reports-offline'},
            }) + ")); localStorage.setItem('project-store', JSON.stringify(" + json.dumps({'currentProject': project}) + '));')
            return context

        context = context_for(fixture['web_project'])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + '/web-testing/scheduled-tasks')
            # Empty isolated cache means no Beat heartbeat, not worker state.
            expect(page.get_by_text('定时任务服务未启动或已离线', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='创建任务', exact=True)).to_be_enabled()
            expect(page.locator('.scheduled-tasks-card .el-loading-mask')).not_to_be_visible()
            page.screenshot(path=str(output / 'beat-offline.png'), full_page=True, animations='disabled')
            health = {'http_status': 503, 'status': 'unknown'}
            page.route('**/scheduled-tasks/service-status/', lambda route: route.fulfill(
                status=health['http_status'], json={'success': True, 'data': {'status': health['status']}},
            ))
            page.get_by_role('button', name='重新检测', exact=True).click()
            expect(page.get_by_text('定时任务服务状态检测失败', exact=True)).to_be_visible()
            expect(page.get_by_text('定时任务服务未启动或已离线', exact=True)).to_have_count(0)
            health.update(http_status=200, status='online')
            page.get_by_role('button', name='重新检测', exact=True).click()
            expect(page.get_by_text('定时任务服务状态检测失败', exact=True)).to_have_count(0)
            page.get_by_role('button', name='创建任务', exact=True).click()
            dialog = page.get_by_role('dialog', name='创建定时任务')
            expect(dialog).to_be_visible()
            expect(dialog.get_by_text('测试类型', exact=True)).to_have_count(0)
            expect(dialog.get_by_text('执行环境', exact=True)).to_have_count(0)
            selector = dialog.get_by_role('combobox').first
            dialog.get_by_text('请选择测试套件', exact=True).click()
            expect(page.get_by_role('option', name='套件甲', exact=False)).to_be_visible()
            expect(page.get_by_role('option', name='空套件不可选', exact=False)).to_be_disabled()
            page.get_by_role('option', name='套件甲', exact=False).click()
            page.get_by_role('option', name='套件乙', exact=False).click()
            selector.press('Escape')
            order = dialog.get_by_label('测试套件执行顺序')
            order.get_by_role('button', name='下移', exact=True).first.click()
            expect(order).to_contain_text('套件乙')
            dialog.get_by_placeholder('请输入任务名称', exact=True).fill('隔离顺序任务')
            dialog.get_by_placeholder('如: 0 9 * * 1-5 (工作日9点执行)', exact=True).fill('0 9 * * 1-5')
            dialog.get_by_text('暂停', exact=True).click()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/scheduled-tasks/tasks/')) as submitted:
                dialog.get_by_role('button', name='创建', exact=True).click()
            response = submitted.value
            assert response.status == 201, response.text()
            payload = response.request.post_data_json
            assert payload['suite_ids'] == fixture['suites'][::-1], payload
            assert 'suite_type' not in payload and 'environment' not in payload, payload
            expect(dialog).not_to_be_visible()
            expect(page.get_by_text('隔离顺序任务', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'web-schedules.png'), full_page=True, animations='disabled')
            # Read-only suite query failures must not turn into an empty success.
            page.route('**/scheduled-tasks/suite-choices/**', lambda route: route.fulfill(status=503, json={'message': 'fixture query failed'}))
            page.get_by_role('button', name='创建任务', exact=True).click()
            dialog.get_by_text('请选择测试套件', exact=True).click()
            expect(page.get_by_role('button', name='重试', exact=True)).to_be_visible()
            dialog.get_by_role('combobox').first.press('Escape')
            dialog.get_by_role('button', name='取消', exact=True).click()
        except Exception:
            page.screenshot(path=str(output / 'web-failure.png'), full_page=True)
            raise
        finally:
            context.close()

        context = context_for(fixture['api_project'])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + '/api-testing/scheduled-tasks')
            page.get_by_role('button', name='创建任务', exact=True).click()
            dialog = page.get_by_role('dialog', name='创建定时任务')
            expect(dialog.get_by_text('测试类型', exact=True)).to_have_count(0)
            expect(dialog.get_by_text('执行环境', exact=True)).to_be_visible()
            dialog.get_by_text('请选择测试套件', exact=True).click()
            expect(page.get_by_role('option', name='API 专属套件', exact=False)).to_be_visible()
            expect(page.get_by_role('option', name='套件甲', exact=False)).to_have_count(0)
            page.get_by_role('option', name='API 专属套件', exact=False).click()
            dialog.get_by_role('combobox').first.press('Escape')
            page.screenshot(path=str(output / 'api-schedules.png'), full_page=True, animations='disabled')
        finally:
            context.close()
            browser.close()
    assert not errors, errors


def main():
    output = BACKEND / 'logs' / 'schedule-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aits-schedule-browser-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        add_schedule_fixtures(fixture)
        from django.core.wsgi import get_wsgi_application
        server = make_server('127.0.0.1', 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print('PASS: isolated Web/API schedule forms, suite order, load failure and Beat offline/unknown/recovery banner')


if __name__ == '__main__':
    main()
