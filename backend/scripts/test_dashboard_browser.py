#!/usr/bin/env python3
"""Real Vue + Django dashboard checks on temporary SQLite; loopback only.

Build frontend first, then run with backend/.venv/bin/python. No real data,
Redis, model API or email is used. Screenshots go to backend/logs/.
"""
import json
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_user_management_browser import bootstrap, BACKEND
from test_project_knowledge_browser import _QuietHandler, _static_or_django
from test_platform_reports_browser import loopback_only, CHROME


def seed(fixture):
    from projects.models import Project
    from api_testing.models import APITestCase, APITestExecution, APITestCaseExecutionDetail
    owner = fixture['root']
    first, second, _ = fixture['projects']
    APITestCase.objects.create(project=second, created_by=owner, title='手工场景', test_case_type='scenario')
    for source in ('ai', 'manual', 'unknown'):
        APITestCase.objects.create(project=first, created_by=owner, title=f'{source} 来源用例',
                                   test_case_type='scenario', creation_source=source)
    for project, name, status, workspace in (
        (first, '注册调试', 'failed', 101), (first, '注册调试', 'failed', 101),
        (first, '退出调试', 'passed', 102), (second, '第二项目成功', 'passed', 103),
    ):
        execution = APITestExecution.objects.create(
            project=project, executor=owner, exec_type='case', name=name, status=status,
            input_snapshot={'workspace_id': workspace},
        )
        APITestCaseExecutionDetail.objects.create(execution=execution, name=name, status=status)
    for n in range(22):
        Project.objects.create(name=f'额外项目 {n:02d}', created_by=owner)


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    first, second, _ = fixture['projects']
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        # Leave the old SPA entirely before swapping isolated test identities;
        # otherwise its in-flight auth refresh can persist the previous user.
        page.route(origin + '/__dashboard_test_login__', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<html><body>test login setup</body></html>',
        ))

        def sign_in(role, project=None):
            user = fixture[role]
            page.goto(origin + '/__dashboard_test_login__')
            page.evaluate("""([auth, project]) => {
              localStorage.clear();
              localStorage.setItem('auth-store', JSON.stringify(auth));
              localStorage.setItem('project-store', JSON.stringify({ currentProject: project }));
              localStorage.setItem('platform-cockpit-layout', JSON.stringify([{i:'portal-app',x:0,y:0,w:12,h:8}]));
            }""", [{'accessToken': fixture['tokens'][role], 'refreshToken': None,
                    'user': {'id': user.pk, 'username': user.username, 'is_staff': user.is_staff,
                             'is_superuser': user.is_superuser, 'role': 'admin' if user.is_staff else 'user'}},
                   {'id': project.pk, 'name': project.name, 'project_type': 'api'} if project else None])
            page.goto(origin + '/dashboard')

        def select(project):
            page.locator('.cockpit-toolbar .el-select').click()
            page.get_by_role('option', name=project.name, exact=True).click()

        try:
            sign_in('root', first)
            expect(page.locator('.metric-pass-rate strong')).to_have_text('33.33%')
            expect(page.locator('.metric-executions strong')).to_have_text('3')
            expect(page.locator('.metric-ai-rate strong')).to_have_text('33.33%')
            expect(page.locator('.metric-ai-rate .metric-label')).to_have_text('AI 生成用例占比')
            page.locator('.metric-ai-rate .metric-label').hover()
            expect(page.get_by_role('tooltip')).to_contain_text('后续编辑、AI 修复或删除工作区不改变来源')
            page.locator('.cockpit-toolbar h1').hover()
            expect(page.locator('.top-failures')).to_contain_text('注册调试')
            expect(page.locator('.top-failures')).not_to_contain_text('None')
            assert page.locator('.vue-grid-layout, .vue-resizable-handle, .drag-handle').count() == 0
            expect(page.get_by_text('自定义布局', exact=True)).to_have_count(0)
            expect(page.locator('.portal-ai-config')).to_be_visible()
            perf = page.locator('.portal-perf')
            app = page.locator('.portal-app')
            assert perf.bounding_box()['x'] < app.bounding_box()['x']
            before = page.url
            perf.click()
            app.click()
            assert page.url == before
            page.locator('.cockpit-toolbar .el-select').click()
            expect(page.get_by_role('option')).to_have_count(25)
            page.keyboard.press('Escape')
            page.screenshot(path=str(output / 'admin.png'), full_page=True)

            # A late response from project A must not overwrite project B.
            select(second)
            expect(page.locator('.metric-total-cases strong')).to_have_text('1')
            held = []
            pattern = f'**/projects/{first.pk}/dashboard/summary/'
            page.route(pattern, lambda route: held.append(route))
            select(first)
            expect(page.locator('.top-failures')).to_contain_text('注册调试')
            assert held, 'Expected an in-flight summary request'
            select(second)
            expect(page.locator('.metric-pass-rate strong')).to_have_text('100%')
            for route in held:
                route.fulfill(status=200, json={'today_pass_rate': 7, 'today_executions': 999,
                                               'ai_contribution_rate': 0, 'total_cases': 999})
            page.unroute(pattern)
            # Wait until other response handlers have completed before checking stale state.
            expect(page.locator('.top-failures')).to_contain_text('近 7 天无失败记录')
            expect(page.locator('.metric-total-cases strong')).to_have_text('1')

            # Real error state + retry, not a fabricated 0 or no-failures result.
            pattern = f'**/projects/{first.pk}/dashboard/**'
            page.route(pattern, lambda route: route.fulfill(status=503, json={'message': '首页统计暂时无法加载，请稍后重试。'}))
            select(first)
            expect(page.locator('.dashboard-alert')).to_contain_text('首页统计暂时无法加载')
            expect(page.locator('.metric-pass-rate strong')).to_have_text('--')
            expect(page.locator('.top-failures')).not_to_contain_text('近 7 天无失败记录')
            page.screenshot(path=str(output / 'error.png'), full_page=True)
            page.unroute(pattern)
            page.locator('.dashboard-alert').get_by_role('button', name='重试').click()
            expect(page.locator('.metric-pass-rate strong')).to_have_text('33.33%')

            # Clearing a selection empties only statistics, not navigation.
            select_box = page.locator('.cockpit-toolbar .el-select')
            select_box.hover()
            select_box.locator('.el-select__clear').click()
            expect(page.locator('.chart .state')).to_contain_text('请选择项目')
            expect(page.locator('.portal-api')).to_be_visible()
            expect(page.locator('.portal-web')).to_be_visible()
            expect(page.locator('.portal-settings')).to_be_visible()
            expect(page.locator('.top-failures')).not_to_contain_text('注册调试')
            page.screenshot(path=str(output / 'cleared.png'), full_page=True)

            sign_in('member', first)
            expect(page.locator('.metric-pass-rate strong')).to_have_text('33.33%')
            expect(page.locator('.portal-ai-config, .portal-settings, .section-infra')).to_have_count(0)
            expect(page.locator('.portal-api')).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.cockpit').evaluate('(e) => e.scrollWidth <= e.clientWidth + 1')
            page.screenshot(path=str(output / 'member-mobile.png'), full_page=True)

            # A user without any projects can still reach project portals.
            sign_in('managed')
            expect(page.locator('.chart .state')).to_contain_text('请选择项目')
            expect(page.locator('.portal-api')).to_be_visible()
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Dashboard widths:', page.locator('.cockpit, .dashboard-grid, .dashboard-item').evaluate_all(
                '(nodes) => nodes.map(e => ({class:e.className, width:e.clientWidth, scroll:e.scrollWidth}))',
            ))
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'logs' / 'dashboard-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='automation-dashboard-browser-') as root, \
            patch.object(socket.socket, 'connect', loopback_only(socket.socket.connect)), \
            patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(root))
        seed(fixture)
        from django.core.wsgi import get_wsgi_application
        server = make_server('127.0.0.1', 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print(f'PASS: isolated dashboard browser acceptance; evidence: {output}')


if __name__ == '__main__':
    main()
