"""Verify native report UI against a disposable Django/SQLite service.

Build frontend first. No NAS, Redis, LLM, notification, or target-site requests.
Run from backend: .venv/bin/python scripts/test_platform_reports_browser.py
"""
from __future__ import annotations

import base64
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django


BACKEND = Path(__file__).resolve().parent.parent
CHROME = Path(os.environ.get('AITS_TEST_CHROME_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
KEY = 'native-reports-isolated-browser-test-only-signing-key'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def loopback_only(original):
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('Report browser test blocked external network')
        return original(sock, address)
    return connect


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    from aits_backend import settings as config
    config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(root / 'reports.sqlite3')}}
    config.SECRET_KEY = KEY
    config.SIMPLE_JWT = {**config.SIMPLE_JWT, 'SIGNING_KEY': KEY}
    config.MIGRATION_MODULES = _NoMigrations()
    config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    config.CELERY_BROKER_URL = 'memory://'
    config.CELERY_RESULT_BACKEND = 'cache+memory://'
    config.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    config.MEDIA_ROOT = str(root / 'media')
    config.ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
    config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', run_syncdb=True, verbosity=0)
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from projects.models import Project
    from rest_framework_simplejwt.tokens import AccessToken
    from scheduled_tasks.models import ScheduledTask, TaskExecutionLog
    from web_testing.models import WebUITestExecution, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution
    from api_testing.models import APITestExecution, APITestSuiteExecutionDetail, APITestSuiteCaseExecution

    user = get_user_model().objects.create_user(username='reports-offline', email='reports@example.test', password='fixture-only')
    project = Project.objects.create(name='原生报告隔离项目', project_type='web', created_by=user)
    start = timezone.now() - timedelta(seconds=31)
    execution = WebUITestExecution.objects.create(
        project=project, executor=user, exec_type='suite', name='报告验收套件', status='failed',
        start_time=start, end_time=start + timedelta(seconds=31), duration=31,
        error_message='测试套件中有 1 个用例失败',
    )
    detail = WebUITestSuiteExecutionDetail.objects.create(
        execution=execution, test_suite=None, total_cases=4, passed_cases=3, failed_cases=1,
        start_time=start, end_time=execution.end_time, duration=31, log='验证 用户新增 通过\n菜单删除失败\n测试套件执行完毕',
    )
    for index, name in enumerate(['用户新增验证', '角色编辑验证', '菜单删除验证', '资源查询验证'], 1):
        relative = f'webui_failure_screenshots/execution_{execution.id}/case_{index}.png'
        path = Path(config.MEDIA_ROOT) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        WebUITestSuiteCaseExecution.objects.create(
            suite_execution=detail, test_case=None, name=name,
            status='failed' if index == 3 else 'passed', duration=index + 2,
            error_message='点击「删除」按钮超时' if index == 3 else '',
            log=f'验证 {name} {"失败" if index == 3 else "通过"}', screenshot_path=relative,
        )
    task = ScheduledTask.objects.create(name='定时报告验收', project=project, user=user, suite_type='web', suite_ids=[999999], cron_expression='0 9 * * *')
    log = TaskExecutionLog.objects.create(
        task=task, start_time=start, end_time=execution.end_time, status='failed',
        total_cases=4, passed_cases=3, failed_cases=1,
        step_log='报告验收日志',
        linked_executions=[{'kind': 'web', 'project_id': project.id, 'execution_id': execution.id, 'name': execution.name}],
    )
    api_project = Project.objects.create(name='API 报告隔离项目', project_type='api', created_by=user)
    api_execution = APITestExecution.objects.create(project=api_project, executor=user, exec_type='suite', name='API 报告验收套件', status='passed', duration=1)
    api_detail = APITestSuiteExecutionDetail.objects.create(execution=api_execution, test_suite=None, test_suite_name='API 报告验收套件', total_cases=1, passed_cases=1, duration=1)
    APITestSuiteCaseExecution.objects.create(
        suite_execution=api_detail, test_case=None, name='健康检查接口', status='passed', duration=1,
        log='验证 状态码 通过', httprunner_result=json.dumps({'results': [{
            'name': '健康检查接口', 'case_id': 'fixture', 'success': True,
            'time': {'duration': 1},
            'step_datas': [{'name': '读取健康状态', 'success': True, 'data': {
                'stat': {'response_time_ms': 100, 'content_size': 15},
                'req_resps': [{'request': {'method': 'GET', 'url': 'http://fixture.invalid/health', 'headers': {}},
                              'response': {'status_code': 200, 'headers': {}, 'body': {'status': 'ok'}}}],
            }, 'validators': [{'comparator': 'eq', 'check': 'status_code', 'expect': 200, 'check_value': 200, 'check_result': 'pass'}]}],
        }]}, ensure_ascii=False),
    )
    return {'token': str(AccessToken.for_user(user)), 'user_id': user.id, 'project_id': project.id, 'execution_id': execution.id, 'log_id': log.id, 'api_project_id': api_project.id, 'api_execution_id': api_execution.id}


def verify_browser(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    def settle_expanded_sections(page):
        # Vue's collapse hooks clear the inline height after the CSS transition.
        # Wait for that hook, not just visible text, before visual evidence.
        page.wait_for_function("""() => Array.from(document.querySelectorAll(
            '.el-collapse-item.is-active > .el-collapse-item__wrap'
        )).every(node => !node.style.height && !node.className.includes('-enter-'))""")

    errors = []
    report_path = f'/reports/web/{fixture["project_id"]}/{fixture["execution_id"]}'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'reports-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + report_path)
            expect(page.get_by_text('报告验收套件', exact=False).first).to_be_visible(timeout=20000)
            expect(page.get_by_text('菜单删除验证', exact=False).first).to_be_visible()
            expect(page.get_by_text('75%', exact=False).first).to_be_visible()
            expect(page.locator('iframe')).to_have_count(0)
            page.get_by_text('菜单删除验证', exact=True).click()
            expect(page.get_by_text('点击「删除」按钮超时', exact=True)).to_be_visible()
            expect(page.get_by_role('img', name='异常结束截图', exact=True)).to_be_visible()
            page.wait_for_function("document.querySelector('img[alt=\"异常结束截图\"]')?.naturalWidth > 0")
            page.get_by_text('只看失败', exact=True).click()
            expect(page.get_by_role('switch')).to_be_checked()
            expect(page.get_by_text('用户新增验证', exact=True)).to_have_count(0)
            expect(page.get_by_text('菜单删除验证', exact=True)).to_be_visible()
            settle_expanded_sections(page)
            page.screenshot(path=str(output / 'suite-report.png'), full_page=True, animations='disabled')
            page.reload()
            expect(page.get_by_text('菜单删除验证', exact=False).first).to_be_visible(timeout=15000)
            # Missing/denied record must present an error, never stale report data.
            page.goto(origin + '/reports/web/999999/999999')
            expect(page.get_by_text('报告验收套件', exact=False)).to_have_count(0)
            page.goto(origin + f'/reports/detail/{fixture["log_id"]}')
            expect(page.get_by_text('定时报告验收', exact=False).first).to_be_visible(timeout=15000)
            expect(page.locator(f'a[href$="{report_path}"]')).to_have_count(1)
            expect(page.locator('iframe')).to_have_count(0)
            page.screenshot(path=str(output / 'scheduled-report.png'), full_page=True, animations='disabled')
            page.goto(origin + f'/reports/api/{fixture["api_project_id"]}/{fixture["api_execution_id"]}')
            expect(page.get_by_text('API 报告验收套件', exact=True).first).to_be_visible(timeout=15000)
            page.get_by_text('健康检查接口', exact=True).first.click()
            expect(page.get_by_text('验证 状态码 通过', exact=False).first).to_be_visible()
            page.get_by_text('Test Steps', exact=True).click()
            expect(page.get_by_text('http://fixture.invalid/health', exact=False).first).to_be_visible()
            expect(page.locator('iframe')).to_have_count(0)
            settle_expanded_sections(page)
            page.screenshot(path=str(output / 'api-report.png'), full_page=True, animations='disabled')
            if errors:
                raise AssertionError(f'Browser JS errors: {errors}')
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
        anonymous = browser.new_context()
        anonymous.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        page = anonymous.new_page()
        page.goto(origin + report_path)
        expect(page).to_have_url(__import__('re').compile(r'/login\?redirect='))
        page.get_by_placeholder('用户名', exact=True).fill('reports-offline')
        page.get_by_placeholder('密码', exact=True).fill('fixture-only')
        page.get_by_role('button', name='登录', exact=True).click()
        expect(page).to_have_url(origin + report_path, timeout=15000)
        expect(page.get_by_text('报告验收套件', exact=True).first).to_be_visible()
        anonymous.close()
        browser.close()


def main():
    output = BACKEND / 'logs' / 'native-report-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aits-native-report-browser-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        from django.core.wsgi import get_wsgi_application
        server = make_server('127.0.0.1', 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify_browser(f'http://127.0.0.1:{server.server_port}', fixture, output)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print('PASS: isolated native WebUI / API / scheduled reports, historical snapshots, refresh and auth redirect')


if __name__ == '__main__':
    main()
