"""Real Vue + Django workspace smoke test, temporary SQLite and fake model/HTTP.

Run after frontend build. Nothing is sent to Redis, the NAS, or a target API.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django
from test_platform_reports_browser import loopback_only, CHROME

BACKEND = Path(__file__).resolve().parent.parent
KEY = 'api-workspace-isolated-browser-test-signing-key'


def database(call):
    # Playwright's sync facade still owns an event loop; keep Django ORM outside it.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result()


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['AITS_OFFLINE_TEST_NETWORK'] = 'blocked'
    from aits_backend import settings as config
    config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(root / 'test.sqlite3')}}
    config.SECRET_KEY = KEY
    config.SIMPLE_JWT = {**config.SIMPLE_JWT, 'SIGNING_KEY': KEY}
    config.MIGRATION_MODULES = _NoMigrations()
    config.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    config.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    config.CELERY_BROKER_URL = 'memory://'
    config.CELERY_RESULT_BACKEND = 'cache+memory://'
    config.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    config.ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
    config.MEDIA_ROOT = str(root / 'media')
    config.LOGGING = {'version': 1, 'disable_existing_loggers': True}
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', run_syncdb=True, verbosity=0)
    from django.contrib.auth import get_user_model
    from projects.models import Project
    from api_testing.models import APISpecification, APIEndpoint
    from ai_core.models import LLMConfiguration
    from rest_framework_simplejwt.tokens import AccessToken
    user = get_user_model().objects.create_user(username='workspace-offline', password='fixture-only')
    project = Project.objects.create(name='API 工作区隔离验收', project_type='api', created_by=user)
    spec = APISpecification.objects.create(project=project, spec_name='验收 Swagger', status='completed', created_by=user)
    endpoint = APIEndpoint.objects.create(spec=spec, path='/health', method='GET', summary='健康检查', responses={'200': {'description': 'OK'}})
    model = LLMConfiguration.objects.create(created_by=user, provider='openai', provider_name='离线模拟',
                                             model_name='fixture-model', api_key='fixture-only', base_url='https://never-called.invalid')
    LLMConfiguration.objects.create(created_by=user, provider='openai', model_name='disabled-fixture', is_active=False)
    LLMConfiguration.objects.create(created_by=user, provider='openai', model_name='vision-fixture', model_type='vision')
    return {'token': str(AccessToken.for_user(user)), 'user_id': user.pk, 'project_id': project.pk,
            'endpoint_id': endpoint.pk, 'model_id': model.pk}


def verify(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    from api_testing.models import APIWorkspace, APITestCase
    from ai_core.models import LLMConfiguration
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1536, 'height': 1050}, accept_downloads=True,
                                      permissions=['clipboard-read', 'clipboard-write'])
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + f'/api-testing/workspace?endpoint_id={fixture["endpoint_id"]}')
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible(timeout=20000)
            expect(page).to_have_url(__import__('re').compile(r'workspace_id=\d+'), timeout=15000)
            workspace_url = page.url
            expect(page.get_by_text('API功能导航', exact=True)).to_have_count(0)
            expect(page.get_by_text('工作区标题', exact=True)).to_have_count(0)
            expect(page.locator('.header-actions')).to_contain_text('未命名工作区 #')
            model_picker = page.get_by_role('combobox', name='模型', exact=True)
            expect(model_picker).to_be_enabled(timeout=15000)
            model_picker.click()
            expect(page.get_by_role('option').filter(has_text='fixture-model')).to_be_visible(timeout=15000)
            expect(page.get_by_role('option').filter(has_text='disabled-fixture')).to_have_count(0)
            expect(page.get_by_role('option').filter(has_text='vision-fixture')).to_have_count(0)
            page.get_by_role('option').filter(has_text='fixture-model').click()
            message = page.get_by_placeholder('例如：先登录取得 token，再查询当前用户；需要覆盖未授权场景。')
            expect(message).to_be_visible()
            message.fill('生成健康检查，验证 HTTP 状态码为 200')
            page.get_by_role('button', name='生成候选', exact=True).click()
            adopt = page.get_by_role('button', name='采用候选并替换草稿')
            expect(adopt).to_be_visible(timeout=15000)
            assert database(APITestCase.objects.count) == 0, '生成不应自动保存正式用例'
            adopt.click()
            page.get_by_role('button', name='采用', exact=True).click()
            expect(page.get_by_text('AI 候选草稿（尚未采用）', exact=True)).to_have_count(0)
            page.get_by_role('button', name='查看代码', exact=True).click()
            expect(page.locator('pre.code')).to_contain_text('requests')
            page.get_by_role('button', name='复制', exact=True).click()
            assert 'requests' in page.evaluate('navigator.clipboard.readText()')
            # LAN HTTP deployments may not expose navigator.clipboard.
            expect(page.get_by_text('代码已复制', exact=True)).to_have_count(0, timeout=6000)
            page.evaluate("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })")
            page.get_by_role('button', name='复制', exact=True).click()
            expect(page.get_by_text('代码已复制', exact=True).first).to_be_visible()
            with page.expect_download() as download:
                page.get_by_role('button', name='导出 .py', exact=True).click()
            exported = Path(download.value.path()).read_text(encoding='utf-8')
            compile(exported, '<browser-export>', 'exec')
            page.get_by_role('button', name='显式调试执行', exact=True).click()
            page.get_by_role('button', name='确认执行', exact=True).click()
            repair = page.get_by_role('button', name='基于失败结果修复', exact=True)
            expect(repair).to_be_enabled(timeout=15000)
            expect(page.locator('.debug-panel')).to_contain_text('500')
            page.screenshot(path=str(output / 'failed-debug.png'), full_page=True)
            repair.click()
            expect(adopt).to_be_visible(timeout=15000)
            assert database(lambda: APIWorkspace.objects.get().debug_result.get('success')) is False
            adopt.click()
            page.get_by_role('button', name='采用', exact=True).click()
            expect(page.locator('pre.code')).to_have_count(0)
            page.get_by_role('button', name='查看代码', exact=True).click()
            expect(page.locator('pre.code')).to_contain_text('X-Repaired')
            page.get_by_role('button', name='显式调试执行', exact=True).click()
            page.get_by_role('button', name='确认执行', exact=True).click()
            expect(page.locator('.debug-panel')).to_contain_text('200', timeout=15000)
            page.get_by_role('button', name='保存为测试用例', exact=True).click()
            page.get_by_role('button', name='确认保存', exact=True).click()
            expect(page.get_by_text('测试用例已保存', exact=True)).to_be_visible(timeout=15000)
            assert database(APITestCase.objects.count) == 1
            saved = database(APITestCase.objects.get)
            assert json.loads(saved.script_content)['teststeps'][0]['request']['headers']['X-Repaired'] == 'true'
            page.reload()
            expect(page.get_by_text('生成健康检查，验证 HTTP 状态码为 200', exact=True)).to_be_visible(timeout=15000)
            expect(page.locator('.debug-panel')).to_contain_text('200')
            page.screenshot(path=str(output / 'workspace-ready.png'), full_page=True)
            execution_response = page.request.post(
                origin + f'/api/v1/projects/{fixture["project_id"]}/api-testing/test-cases/{saved.pk}/execute/',
                data={'sync': True}, headers={'Authorization': f'Bearer {fixture["token"]}'},
            )
            assert execution_response.ok, execution_response.text()
            execution_data = execution_response.json()['data']
            assert execution_data['success'] is True, execution_data
            page.goto(origin + f'/reports/api/{fixture["project_id"]}/{execution_data["execution_id"]}')
            expect(page.get_by_text('Execution Logs', exact=True)).to_be_visible(timeout=15000)
            page.get_by_text('Test Steps', exact=True).click()
            expect(page.get_by_text('https://example.test/health', exact=True).first).to_be_visible()
            expect(page.locator('iframe')).to_have_count(0)
            page.screenshot(path=str(output / 'native-api-report.png'), full_page=True)
            database(lambda: LLMConfiguration.objects.filter(pk=fixture['model_id']).update(is_active=False))
            page.goto(workspace_url)
            expect(page.get_by_text('此工作区原先选择的模型已禁用、类型不匹配或不再可用；请重新选择可用聊天模型后再发起 AI 对话。', exact=True)).to_be_visible(timeout=15000)
            page.get_by_placeholder('例如：先登录取得 token，再查询当前用户；需要覆盖未授权场景。').fill('模型禁用后不能偷偷改用其他模型')
            expect(page.get_by_role('button', name='生成候选', exact=True)).to_be_disabled()
            page.screenshot(path=str(output / 'disabled-model.png'), full_page=True)
            page.goto(origin + '/api-testing/function-navigation')
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible(timeout=15000)
            expect(page.get_by_text('API功能导航', exact=True)).to_have_count(0)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'logs' / 'api-workspace-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aits-api-browser-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        from api_testing.workspace_tasks import generate_api_workspace_candidate, debug_api_workspace
        from api_testing.requests_runtime import run_case
        from requests import Response, Request
        from django.core.wsgi import get_wsgi_application

        def model_answer(messages):
            payload = json.loads(messages[-1].content)
            if payload['failure_evidence']:
                draft = deepcopy(payload['current_draft'])
                draft['teststeps'][0]['request']['headers'] = {'X-Repaired': 'true'}
            else:
                draft = {'version': 1, 'config': {'name': '健康检查', 'base_url': 'https://example.test', 'variables': {}, 'verify': True},
                         'teststeps': [{'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
                                        'request': {'method': 'GET', 'url': '/health', 'headers': {}},
                                        'validate': [{'eq': ['status_code', 200]}]}]}
            return json.dumps(draft)

        def fake_http(**kwargs):
            result = Response()
            result.status_code = 200 if kwargs.get('headers', {}).get('X-Repaired') == 'true' else 500
            result._content = json.dumps({'state': 'ok' if result.status_code == 200 else 'error'}).encode()
            result.url, result.headers, result.elapsed = kwargs['url'], {'Content-Type': 'application/json'}, timedelta(milliseconds=5)
            result.request = Request(kwargs['method'], kwargs['url'], headers=kwargs.get('headers')).prepare()
            return result

        def local_runner(**kwargs):
            fake_session = SimpleNamespace(request=fake_http, close=lambda: None)
            with patch('api_testing.requests_runtime.requests.Session', return_value=fake_session):
                return run_case(**kwargs)

        def eager(task):
            def dispatch(args, task_id, **kwargs):
                return task.apply(args=args, task_id=task_id)
            return dispatch

        with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace(stream_invoke=model_answer)), patch(
            'api_testing.requests_runner.requests_runner', side_effect=local_runner,
        ), patch.object(generate_api_workspace_candidate, 'apply_async', side_effect=eager(generate_api_workspace_candidate)), patch.object(
            debug_api_workspace, 'apply_async', side_effect=eager(debug_api_workspace),
        ), patch('api_testing.execution_service.requests_runner', side_effect=local_runner):
            server = make_server('127.0.0.1', 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
    print(f'PASS: API workspace generation/adoption/debug/repair/save/export browser flow; evidence: {output}')


if __name__ == '__main__':
    main()
