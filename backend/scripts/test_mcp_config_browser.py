"""Real Vue + Django + offline stdio MCP tool-list acceptance, temporary DB only."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_user_management_browser import bootstrap, database, BACKEND
from test_project_knowledge_browser import _QuietHandler, _static_or_django
from test_platform_reports_browser import loopback_only, CHROME


def seed(fixture):
    from ai_core.models import MCPConfiguration
    script = str(BACKEND / 'scripts' / 'fixtures' / 'mcp_tools_fixture.py')
    configs = []
    for name, active, args in (
        ('工具清单测试', True, [script]),
        ('空清单测试', False, [script, '--empty']),
        ('故障清单测试', True, None),
    ):
        configs.append(MCPConfiguration.objects.create(
            name=name, created_by=fixture['root'], is_active=active,
            raw_config=json.dumps({'mcpServers': {name: {
                'command': sys.executable if args else '/nonexistent/mcp-fixture-command',
                'args': args or [],
            }}}),
        ))
    return configs


def verify(origin, fixture, configs, output):
    from playwright.sync_api import expect, sync_playwright
    from ai_core.models import MCPConfiguration
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1500, 'height': 950})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route(origin + '/__fixture_login__', lambda route: route.fulfill(
            content_type='text/html', body='<html>Isolated MCP check</html>',
        ))
        page.goto(origin + '/__fixture_login__')
        user = fixture['root']
        page.evaluate("""auth => localStorage.setItem('auth-store', JSON.stringify(auth))""", {
            'accessToken': fixture['tokens']['root'], 'refreshToken': None,
            'user': {'id': user.pk, 'username': user.username, 'is_staff': True,
                     'is_superuser': True, 'role': 'admin'},
        })
        page.goto(origin + '/ai-config/mcp')
        rows = [page.get_by_role('row').filter(has=page.get_by_text(c.name, exact=True)) for c in configs]
        first, empty, broken = rows
        try:
            for row in rows:
                expect(row).to_contain_text('尚未检测')
            expect(first.get_by_role('switch')).to_be_checked()
            expect(empty.get_by_role('switch')).not_to_be_checked()

            first.get_by_role('button', name='刷新工具', exact=True).click()
            expect(first).to_contain_text('已发现 2 个工具', timeout=25000)
            expect(empty).to_contain_text('尚未检测')
            first.locator('.tools-expand-icon').click()
            expect(page.get_by_text('fixture_read_page', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'tools-ready.png'), full_page=True, animations='disabled')
            page.locator('.header-text h2').click()

            # State switches are per configuration; turning off does not erase a successful probe.
            first.locator('.el-switch').click()
            expect(first.get_by_role('switch')).not_to_be_checked()
            expect(empty.get_by_role('switch')).not_to_be_checked()
            empty.locator('.el-switch').click()
            expect(empty.get_by_role('switch')).to_be_checked()
            expect(first.get_by_role('switch')).not_to_be_checked()
            expect(first).to_contain_text('已发现 2 个工具')
            expect(empty).to_contain_text('尚未检测')

            # Probe a disabled configuration without enabling it.
            first.get_by_role('button', name='刷新工具', exact=True).click()
            expect(first).to_contain_text('已发现 2 个工具', timeout=25000)
            expect(first.get_by_role('button', name='刷新工具', exact=True)).to_be_enabled(timeout=25000)
            expect(first.get_by_role('switch')).not_to_be_checked()
            empty.get_by_role('button', name='刷新工具', exact=True).click()
            expect(empty).to_contain_text('已发现 0 个工具', timeout=25000)
            broken.get_by_role('button', name='刷新工具', exact=True).click()
            expect(broken).to_contain_text('检测失败', timeout=25000)
            expect(broken).not_to_contain_text('已发现 0 个工具')
            page.reload()
            expect(first).to_contain_text('已发现 2 个工具')
            expect(empty).to_contain_text('已发现 0 个工具')
            expect(broken).to_contain_text('检测失败')
            expect(first.get_by_role('switch')).not_to_be_checked()
            expect(empty.get_by_role('switch')).to_be_checked()
            expect(first.get_by_text('已发现 2 个工具', exact=True)).to_be_visible()
            expect(empty.get_by_text('已发现 0 个工具', exact=True)).to_be_visible()
            expect(broken.get_by_text('检测失败', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'states-persisted.png'), full_page=True, animations='disabled')
            persisted = database(lambda: list(MCPConfiguration.objects.order_by('id').values(
                'is_active', 'tools_status', 'tools_error',
            )))
            assert [c['is_active'] for c in persisted] == [False, True, True], persisted
            assert [c['tools_status'] for c in persisted] == ['ready', 'ready', 'error'], persisted
            assert persisted[2]['tools_error'], persisted
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / 'logs' / 'mcp-config-browser-check'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='automation-mcp-browser-') as temp, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        configs = seed(fixture)
        from django.core.wsgi import get_wsgi_application
        server = make_server('127.0.0.1', 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify(f'http://127.0.0.1:{server.server_port}', fixture, configs, output)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print(f'PASS: isolated MCP configuration browser acceptance; evidence: {output}')


if __name__ == '__main__':
    main()
