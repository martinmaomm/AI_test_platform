#!/usr/bin/env python3
"""Real Vue user-management acceptance on temporary SQLite and loopback only.

Run after ``cd frontend && npm run build``:
``backend/.venv/bin/python backend/scripts/test_user_management_browser.py``.
No NAS, Redis, SMTP, LLM provider, or external browser request is allowed.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django
from test_platform_reports_browser import loopback_only, CHROME


BACKEND = Path(__file__).resolve().parent.parent
KEY = 'user-management-isolated-browser-signing-key'


def database(call):
    """Keep Django ORM outside Playwright's sync event-loop facade."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result()


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
    import os
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['OFFLINE_TEST_NETWORK'] = 'blocked'
    from config import settings as config
    config.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(root / 'users.sqlite3')}}
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
    from projects.models import Project, ProjectMember
    from ai_core.models import LLMConfiguration
    from rest_framework_simplejwt.tokens import AccessToken

    User = get_user_model()
    root_user = User.objects.create_superuser('root-fixture', 'root@example.test', 'fixture-only')
    admin = User.objects.create_user('admin-fixture', 'admin@example.test', 'fixture-only', is_staff=True)
    other_admin = User.objects.create_user('other-admin-fixture', 'other-admin@example.test', 'fixture-only', is_staff=True)
    member = User.objects.create_user('member-fixture', 'member@example.test', 'fixture-only')
    managed = User.objects.create_user('managed-fixture', 'managed@example.test', 'fixture-only')
    projects = [Project.objects.create(name=f'隔离项目 {name}', project_type='api', created_by=root_user)
                for name in ('甲', '乙', '丙')]
    ProjectMember.objects.create(project=projects[0], user=member, role='editor', can_edit=True,
                                 can_delete=True, can_execute_tests=True, can_view_reports=True)
    LLMConfiguration.objects.create(created_by=root_user, provider='openai', provider_name='fixture provider',
                                    model_name='safe-fixture-model', api_key='must-not-leak',
                                    base_url='https://must-not-leak.invalid', extra_config={'secret': 'must-not-leak'})
    return {
        'root': root_user, 'admin': admin, 'other_admin': other_admin, 'member': member, 'managed': managed,
        'projects': projects,
        'tokens': {name: str(AccessToken.for_user(value)) for name, value in {
            'root': root_user, 'admin': admin, 'other_admin': other_admin, 'member': member, 'managed': managed,
        }.items()},
    }


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    from django.contrib.auth import get_user_model
    from projects.models import ProjectMember

    def identity(name):
        user = fixture[name]
        return {'id': user.pk, 'username': user.username,
                'role': 'admin' if user.is_staff or user.is_superuser else 'user',
                'is_staff': user.is_staff, 'is_superuser': user.is_superuser}

    def sign_in(page, name, project=None):
        # localStorage is only accessible after the page has a loopback origin.
        page.goto(origin + '/', wait_until='domcontentloaded')
        page.evaluate("""([auth, project]) => {
          localStorage.setItem('auth-store', JSON.stringify(auth));
          if (project) localStorage.setItem('project-store', JSON.stringify({currentProject: project}));
          else localStorage.removeItem('project-store');
        }""", [{'accessToken': fixture['tokens'][name], 'refreshToken': None, 'user': identity(name)}, project])

    def project_checkbox(dialog, name):
        return dialog.locator('.el-checkbox').filter(has_text=name)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            # Superuser creates both supported roles through real Vue dialogs.
            sign_in(page, 'root')
            page.goto(origin + '/settings/users')
            expect(page.get_by_text('用户管理', exact=True)).to_be_visible(timeout=15000)
            for username, email, role in [('created-user', 'created-user@example.test', 'user'),
                                          ('created-admin', 'created-admin@example.test', 'admin')]:
                page.get_by_role('button', name='创建账号', exact=True).click()
                dialog = page.locator('.el-dialog:visible')
                inputs = dialog.locator('input')
                inputs.nth(0).fill(username)
                inputs.nth(1).fill(email)
                inputs.nth(2).fill('fixture-password')
                if role == 'admin':
                    dialog.locator('.el-select').click()
                    page.get_by_role('option', name='管理员', exact=True).click()
                with page.expect_response(lambda item: item.request.method == 'POST' and item.url.endswith('/api/v1/users/manage/')) as created:
                    dialog.get_by_role('button', name='保存', exact=True).click()
                assert created.value.ok, created.value.text()
                expect(page.locator('.el-dialog:visible')).to_have_count(0)
                expect(page.locator('.el-table__body tr').filter(has_text=username)).to_have_count(1)
            created_admin = database(lambda: get_user_model().objects.get(username='created-admin'))
            assert created_admin.is_staff and not created_admin.is_superuser

            # A normal admin can assign several projects then remove all assignments.
            sign_in(page, 'admin')
            page.goto(origin + '/settings/users')
            expect(page.get_by_text('用户管理', exact=True)).to_be_visible(timeout=15000)
            row = page.locator('.el-table__body tr').filter(has_text='managed-fixture')
            expect(row.get_by_role('button', name='分配项目', exact=True)).to_be_visible()
            row.get_by_role('button', name='分配项目', exact=True).click()
            dialog = page.locator('.el-dialog:visible')
            expect(project_checkbox(dialog, '隔离项目 甲')).to_be_visible(timeout=15000)
            project_checkbox(dialog, '隔离项目 甲').click()
            project_checkbox(dialog, '隔离项目 乙').click()
            with page.expect_response(lambda item: item.request.method == 'PUT' and item.url.endswith(f'/users/manage/{fixture["managed"].pk}/projects/')) as assigned:
                dialog.get_by_role('button', name='保存分配', exact=True).click()
            assert assigned.value.ok, assigned.value.text()
            assert database(lambda: list(ProjectMember.objects.filter(user=fixture['managed']).order_by('project_id').values_list('project_id', flat=True))) == [fixture['projects'][0].pk, fixture['projects'][1].pk]
            row.get_by_role('button', name='分配项目', exact=True).click()
            dialog = page.locator('.el-dialog:visible')
            project_checkbox(dialog, '隔离项目 甲').click()
            project_checkbox(dialog, '隔离项目 乙').click()
            with page.expect_response(lambda item: item.request.method == 'PUT' and item.url.endswith(f'/users/manage/{fixture["managed"].pk}/projects/')) as cleared:
                dialog.get_by_role('button', name='保存分配', exact=True).click()
            assert cleared.value.ok, cleared.value.text()
            assert not database(lambda: ProjectMember.objects.filter(user=fixture['managed']).exists())
            for protected in ('admin-fixture', 'other-admin-fixture', 'root-fixture'):
                protected_row = page.locator('.el-table__body tr').filter(has_text=protected)
                expect(protected_row.get_by_role('button', name='编辑/重置密码', exact=True)).to_have_count(0)
                expect(protected_row.get_by_role('button', name='分配项目', exact=True)).to_have_count(0)

            # If current assignments cannot be read, the dialog cannot submit
            # an empty list and must leave persisted memberships unchanged.
            managed_projects_url = f'**/api/v1/users/manage/{fixture["managed"].pk}/projects/'
            page.route(managed_projects_url, lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'message': '隔离模拟读取失败'})))
            row.get_by_role('button', name='分配项目', exact=True).click()
            dialog = page.locator('.el-dialog:visible')
            expect(dialog.get_by_role('button', name='保存分配', exact=True)).to_be_disabled(timeout=15000)
            expect(dialog.get_by_text('隔离模拟读取失败', exact=True)).to_be_visible()
            assert not database(lambda: ProjectMember.objects.filter(user=fixture['managed']).exists())
            dialog.get_by_role('button', name='取消', exact=True).click()
            page.unroute(managed_projects_url)

            # The safe option endpoint remains usable by a member and contains no secret fields.
            safe = page.request.get(origin + '/api/v1/ai-core/llm-configs/available/', headers={'Authorization': f'Bearer {fixture["tokens"]["member"]}'})
            assert safe.ok, safe.text()
            safe_data = safe.json()['data']
            assert safe_data and safe_data[0]['model_name'] == 'safe-fixture-model'
            assert not ({'api_key', 'base_url', 'extra_config'} & set(safe_data[0]))

            # Member sees neither privileged dashboard controls nor project metadata controls, and direct URLs return to dashboard.
            sign_in(page, 'member', {'id': fixture['projects'][0].pk, 'name': fixture['projects'][0].name})
            page.goto(origin + '/dashboard')
            expect(page.get_by_text('AI 实验室配置', exact=True)).to_have_count(0)
            expect(page.get_by_text('全局系统设置', exact=True)).to_have_count(0)
            expect(page.locator('.edit-switch')).to_have_count(0)
            page.goto(origin + '/settings/users')
            expect(page).to_have_url(origin + '/dashboard', timeout=15000)
            page.goto(origin + '/ai-config')
            expect(page).to_have_url(origin + '/dashboard', timeout=15000)
            page.goto(origin + '/project/project-list')
            expect(page.get_by_role('button', name='新建项目', exact=True)).to_have_count(0)
            page.goto(origin + f'/project/project-detail/{fixture["projects"][0].pk}')
            expect(page.get_by_role('button', name='编辑项目', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='删除项目', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='管理成员', exact=True)).to_have_count(0)

            # A service failure while refreshing current-user preserves the
            # valid token and lands once on the retry page, never /login.
            current_user_url = '**/api/v1/users/current-user/'
            current_user_calls = []
            def current_user_503(route):
                current_user_calls.append(route.request.url)
                route.fulfill(status=503, content_type='application/json', body=json.dumps({'message': '隔离模拟权限读取失败'}))
            page.route(current_user_url, current_user_503)
            page.goto(origin + '/dashboard')
            expect(page).to_have_url(__import__('re').compile(r'/permission-load-failure\?redirect=(?:%2F|/)dashboard$'), timeout=15000)
            page.wait_for_timeout(500)
            assert len(current_user_calls) == 1, current_user_calls
            assert page.evaluate("localStorage.getItem('auth-store')").find(fixture['tokens']['member']) >= 0
            assert '/login' not in page.url
            page.unroute(current_user_url)
            page.get_by_role('button', name='重试', exact=True).click()
            expect(page).to_have_url(origin + '/dashboard', timeout=15000)

            # Membership removal is enforced by the refreshed project request;
            # direct project detail no longer exposes the former project.
            database(lambda: ProjectMember.objects.filter(user=fixture['member'], project=fixture['projects'][0]).delete())
            project_url = f'/api/v1/projects/{fixture["projects"][0].pk}/'
            with page.expect_response(lambda item: item.url.endswith(project_url)) as inaccessible:
                page.goto(origin + f'/project/project-detail/{fixture["projects"][0].pk}')
            assert inaccessible.value.status == 404, inaccessible.value.text()
            expect(page.get_by_text('加载项目详情失败', exact=True)).to_be_visible(timeout=15000)
            page.screenshot(path=str(output / 'member-gates.png'), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'logs' / 'user-management-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='automation-user-browser-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
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
    print(f'PASS: isolated user-management Vue/browser acceptance; evidence: {output}')


if __name__ == '__main__':
    main()
