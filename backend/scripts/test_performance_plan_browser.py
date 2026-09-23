"""Real Vue/Django plan-editor acceptance with disposable SQLite; never sends load.

Build frontend first, then run from backend:
    .venv/bin/python scripts/test_performance_plan_browser.py
Only loopback requests are allowed. No NAS, Redis, Docker, LLM or target requests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django

BACKEND = Path(__file__).resolve().parents[1]
KEY = 'performance-plan-browser-test-only-signing-key'


def loopback_only(original):
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('Performance editor test blocked external network')
        return original(sock, address)
    return connect


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', ANONYMIZED_TELEMETRY='false',
                      MCP_USE_ANONYMIZED_TELEMETRY='false', PERFORMANCE_EXECUTION_ENABLED='false')
    from config import settings as config
    config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(root / 'performance.sqlite3')}}
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
    from projects.models import Project
    from users.serializers import UserSerializer
    from rest_framework_simplejwt.tokens import AccessToken
    admin = get_user_model().objects.create_user(username='plan-browser-admin', is_staff=True)
    project = Project.objects.create(name='压测编辑隔离验收', project_type='perf', created_by=admin)
    return {
        'project': {'id': project.pk, 'name': project.name, 'project_type': 'perf'},
        'auth': {'accessToken': str(AccessToken.for_user(admin)), 'refreshToken': None,
                 'user': dict(UserSerializer(admin).data)},
    }


def guarded_application(django_app, attempted_runs):
    application = _static_or_django(django_app)
    def respond(environ, start_response):
        if environ.get('REQUEST_METHOD') not in {'GET', 'HEAD', 'OPTIONS'} and '/runs/' in environ.get('PATH_INFO', ''):
            attempted_runs.append(environ['PATH_INFO'])
            start_response('403 Forbidden', [('Content-Type', 'application/json')])
            return [b'{"error":"load generation is forbidden in this test"}']
        return application(environ, start_response)
    return respond


def verify_browser(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    project_id = fixture['project']['id']
    base = f'/api/v1/projects/{project_id}/performance'
    with sync_playwright() as pw:
        executable = os.environ.get('TEST_CHROME_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        browser = pw.chromium.launch(executable_path=executable, headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1080})
        context.set_default_timeout(12000)
        context.add_init_script("localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
                                "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));")
        page = context.new_page()
        errors, saves, external = [], [], []
        fail_targets = {'value': False}
        hold_targets = {'next': False, 'routes': []}

        def intercept(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                external.append(request.url)
                route.abort()
            elif hold_targets['next'] and request.method == 'GET' and request.url.endswith(base + '/targets/'):
                hold_targets['next'] = False
                hold_targets['routes'].append(route)
            elif fail_targets['value'] and request.method == 'GET' and request.url.endswith(base + '/targets/'):
                route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': {'message': '隔离验收：目标加载失败'}}))
            else:
                route.continue_()

        context.route('**/*', intercept)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: saves.append(request.post_data_json)
                if request.method in {'POST', 'PATCH'} and re.search(r'/performance/plans/(?:\d+/)?$', request.url) else None)

        def editor():
            return page.get_by_test_id('performance-plan-editor')

        def input_of(test_id):
            return page.locator(f'[data-testid="{test_id}"]:is(input,textarea), [data-testid="{test_id}"] input, [data-testid="{test_id}"] textarea').first

        def open_plan():
            page.get_by_role('button', name=re.compile('^新建(?:压测)?计划$')).click()
            expect(editor()).to_be_visible()

        def close_plan():
            editor().get_by_role('button', name='取消', exact=True).click()
            expect(editor()).not_to_be_visible()
            expect(page.locator('.el-message-box:visible')).to_have_count(0)

        def add_row(test_id, key, value):
            group = page.get_by_test_id(test_id)
            previous = group.locator('input').count()
            group.get_by_role('button', name=re.compile('添加')).click()
            expect(group.locator('input')).to_have_count(previous + 2)
            group.locator('input').nth(previous).fill(key)
            group.locator('input').nth(previous + 1).fill(value)

        try:
            page.goto(origin + '/perf-testing/plans')
            expect(page.locator('.perf-workspace > .el-tabs')).to_have_count(0)
            expect(page.locator('.perf-workspace > .phase-notice')).to_have_count(0)
            open_plan()
            expect(page.get_by_test_id('plan-target-empty')).to_be_visible()
            expect(input_of('plan-connect-timeout')).to_have_value('10')
            expect(input_of('plan-read-timeout')).to_have_value('30')
            close_plan()

            # Create only in the disposable service while plans is cached.
            page.locator('.sidebar-menu').get_by_text('压测目标', exact=True).click()
            expect(page.get_by_role('button', name='新建目标', exact=True)).to_be_visible()
            response = context.request.post(origin + base + '/targets/', headers={
                'Authorization': 'Bearer ' + fixture['auth']['accessToken'],
            }, data={'name': '本轮新增目标', 'base_url': 'https://fixture.invalid', 'allowed_methods': ['GET', 'POST']})
            assert response.status == 201, response.text()
            target_id = response.json()['data']['id']
            page.locator('.sidebar-menu').get_by_text('压测计划', exact=True).click()
            open_plan()
            select = page.get_by_test_id('plan-target')
            select.click()
            option = page.locator('.el-select-dropdown:visible').get_by_role('option', name=re.compile('本轮新增目标'))
            expect(option).to_be_visible()
            option.click()
            input_of('plan-name').fill('可重复编辑的隔离计划')
            input_of('plan-connect-timeout').fill('12')
            input_of('plan-read-timeout').fill('45')
            input_of('step-name').fill('列表查询')
            input_of('step-path').fill('/products')
            input_of('plan-variables').fill('{"page_size":10,"enabled":true}')
            unique = page.get_by_test_id('plan-unique-variables')
            unique.get_by_role('button', name='添加唯一变量').click()
            unique.locator('input').nth(0).fill('iteration_name')
            unique.locator('input').nth(1).fill('fixture_')
            add_row('step-query-rows', 'tag', '手机 配件')
            add_row('step-query-rows', 'tag', 'a+b')
            add_row('step-query-rows', 'empty', '')
            add_row('step-header-rows', 'X-Test', 'fixture-only')
            expect(page.get_by_test_id('step-url-preview')).to_contain_text('https://fixture.invalid/products?')
            with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith(base + '/targets/')):
                page.get_by_test_id('plan-target-refresh').click()
            expect(input_of('plan-name')).to_have_value('可重复编辑的隔离计划')
            expect(page.get_by_test_id('step-query-rows').locator('input')).to_have_count(6)
            editor().get_by_text('JSON', exact=True).click()
            input_of('step-body-json').fill('{"count":')
            page.get_by_test_id('plan-save').click()
            expect(editor()).to_be_visible()
            assert not saves, 'invalid JSON reached the backend'
            expect(editor().locator('.el-form-item__error')).to_contain_text(re.compile('行.*列'))
            input_of('step-body-json').fill('{"count":0,"enabled":false,"items":[1,2]}')

            # Draft step operations must preserve raw edits and ordering.
            editor().get_by_role('button', name='复制', exact=True).click()
            expect(input_of('step-name')).to_have_value('列表查询')
            expect(page.get_by_test_id('step-query-rows').locator('input')).to_have_count(6)
            input_of('step-name').fill('复制步骤')
            input_of('step-body-json').fill('{')
            editor().get_by_role('button', name='上移', exact=True).click()
            expect(page.get_by_test_id('plan-step-list').locator('button').first).to_contain_text('复制步骤')
            page.get_by_test_id('plan-step-list').locator('button').nth(1).click()
            expect(input_of('step-name')).to_have_value('列表查询')
            assert json.loads(input_of('step-body-json').input_value())['count'] == 0
            page.get_by_test_id('plan-step-list').locator('button').first.click()
            expect(input_of('step-body-json')).to_have_value('{')
            editor().locator('.steps .heading').get_by_role('button', name='删除', exact=True).click()
            expect(input_of('step-name')).to_have_value('列表查询')
            expect(page.get_by_test_id('plan-step-list').locator('button')).to_have_count(1)

            # Canceling the discard prompt keeps the complete draft.
            editor().get_by_role('button', name='取消', exact=True).click()
            page.locator('.el-message-box:visible').get_by_role('button', name='继续编辑', exact=True).click()
            expect(input_of('plan-name')).to_have_value('可重复编辑的隔离计划')
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(base + '/plans/')) as saved:
                page.get_by_test_id('plan-save').click()
            assert saved.value.status == 201, saved.value.text()
            saved_plan = saved.value.json()['data']
            assert saved_plan['connect_timeout_seconds'] == 12
            assert saved_plan['read_timeout_seconds'] == 45
            step = saved_plan['steps'][0]
            assert set(step) == {
                'name', 'phase', 'method', 'path', 'query', 'headers',
                'body_type', 'body', 'extract', 'assertions',
            }
            assert step['path'] == '/products'
            assert step['query'] == {'tag': ['手机 配件', 'a+b'], 'empty': ''}
            assert step['body'] == {'count': 0, 'enabled': False, 'items': [1, 2]}
            assert step['body_type'] == 'json' and step['phase'] == 'main'
            assert step['headers'] == {'X-Test': 'fixture-only'}
            assert step['assertions'] == [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}]
            assert saved_plan['variables'] == {'page_size': 10, 'enabled': True}
            assert saved_plan['unique_variables'] == [{'name': 'iteration_name', 'prefix': 'fixture_'}]
            assert saved_plan['target_id'] == target_id
            expect(editor()).not_to_be_visible()
            page.get_by_role('button', name='编辑', exact=True).click()
            expect(editor()).to_be_visible()
            expect(input_of('step-name')).to_have_value('列表查询')
            expect(input_of('plan-connect-timeout')).to_have_value('12')
            expect(input_of('plan-read-timeout')).to_have_value('45')
            page.get_by_test_id('plan-request-timeouts').screenshot(path=str(output / 'request-timeouts.png'), animations='disabled')
            expect(page.get_by_test_id('step-query-rows').locator('input')).to_have_count(6)
            expect(page.get_by_test_id('step-header-rows').locator('input')).to_have_count(2)
            assert json.loads(input_of('step-body-json').input_value()) == step['body']
            page.screenshot(path=str(output / 'plan-editor-desktop.png'), full_page=True)

            # PATCH is verified through the real serializer, not just captured UI.
            page.get_by_test_id('step-query-rows').locator('input').nth(1).fill('更新后的值')
            with page.expect_response(lambda response: response.request.method == 'PATCH' and response.url.endswith(f"/plans/{saved_plan['id']}/")) as updated:
                page.get_by_test_id('plan-save').click()
            assert updated.value.status == 200, updated.value.text()
            assert updated.value.json()['data']['connect_timeout_seconds'] == 12
            assert updated.value.json()['data']['read_timeout_seconds'] == 45
            expect(editor()).not_to_be_visible()
            page.get_by_role('button', name='编辑', exact=True).click()
            expect(page.get_by_test_id('step-query-rows').locator('input').nth(1)).to_have_value('更新后的值')
            page.set_viewport_size({'width': 900, 'height': 720})
            expect(page.get_by_test_id('plan-save')).to_be_in_viewport()
            page.screenshot(path=str(output / 'plan-editor-compact.png'), full_page=True)
            page.evaluate("document.documentElement.classList.add('dark'); document.documentElement.dataset.theme = 'dark'")
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(editor()).to_have_css('background-color', 'rgb(20, 20, 20)')
            expect(input_of('step-name')).to_have_css('color', 'rgb(207, 211, 220)')
            page.screenshot(path=str(output / 'plan-editor-dark.png'), full_page=True)
            page.evaluate("document.documentElement.classList.remove('dark'); document.documentElement.dataset.theme = 'light'")
            close_plan()

            # A failed refresh is distinct from an empty target list and never
            # permits saving against stale target data.
            fail_targets['value'] = True
            open_plan()
            expect(page.get_by_test_id('plan-target-error')).to_be_visible()
            expect(page.get_by_test_id('plan-save')).to_be_disabled()
            fail_targets['value'] = False
            page.get_by_test_id('plan-target-refresh').click()
            expect(page.get_by_test_id('plan-target-error')).not_to_be_visible()
            close_plan()

            # A late response from a previously opened drawer must not replace
            # the newer target list or re-enable stale saves.
            hold_targets['next'] = True
            with page.expect_request(lambda request: request.method == 'GET' and request.url.endswith(base + '/targets/')):
                page.get_by_role('button', name='编辑', exact=True).click()
            expect(page.get_by_test_id('plan-save')).to_be_disabled()
            close_plan()
            page.get_by_role('button', name='编辑', exact=True).click()
            expect(page.get_by_test_id('plan-save')).to_be_enabled()
            assert len(hold_targets['routes']) == 1
            with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith(base + '/targets/')):
                hold_targets['routes'].pop().fulfill(status=200, content_type='application/json', body='{"success":true,"data":{"items":[]}}')
            # Let Vue finish processing the response before checking the UI.
            page.wait_for_timeout(100)
            expect(page.get_by_test_id('plan-save')).to_be_enabled()
            expect(page.get_by_test_id('plan-target-empty')).to_have_count(0)
            close_plan()
            assert not errors, errors
            assert not external, external
            return {'target_cache_refresh': True, 'save_reopen': True,
                    'repeated_unicode_query': True, 'json_types': True,
                    'step_copy_reorder_delete': True, 'dirty_close_confirmation': True,
                    'update_reopen': True, 'compact_footer_visible': True,
                    'target_error_retry': True, 'page_errors': 0,
                    'late_target_response_ignored': True,
                    'plan_id': saved_plan['id']}
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'temp' / 'performance-plan-browser'
    output.mkdir(parents=True, exist_ok=True)
    attempted_runs = []
    with tempfile.TemporaryDirectory(prefix='performance-plan-browser-') as folder, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(folder))
        from django.core.wsgi import get_wsgi_application
        server = make_server('127.0.0.1', 0, guarded_application(get_wsgi_application(), attempted_runs), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = verify_browser(f'http://127.0.0.1:{server.server_port}', fixture, output)
            from performance_testing.models import PerformancePlan, PerformanceRun, PerformanceNode
            from performance_testing.run_services import _snapshot_for, validation_key_for
            import uuid
            plan = PerformancePlan.objects.get(pk=result.pop('plan_id'))
            # Validate the exact saved payload with the real node contract;
            # no run row, node row, Worker or target request is created.
            node = PerformanceNode(project=plan.project)
            allocations = [{'node': node, 'users': plan.users,
                            'validation_key': validation_key_for(plan, node)}]
            snapshot, digest = _snapshot_for(uuid.uuid4(), plan, allocations, 'load')
            assert snapshot['steps'] == plan.steps
            assert snapshot['connect_timeout_seconds'] == 12
            assert snapshot['read_timeout_seconds'] == 45
            assert digest
            assert not PerformanceRun.objects.exists() and not attempted_runs
            print(json.dumps({**result, 'node_snapshot_contract': True, 'pressure_runs': 0}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
