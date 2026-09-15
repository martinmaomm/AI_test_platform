"""Real Vue + Django singleton MCP acceptance, temporary DB and local stdio only."""
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


def fixture_config(*, empty=False, broken=False):
    script = str(BACKEND / 'scripts' / 'fixtures' / 'mcp_tools_fixture.py')
    return json.dumps({'mcpServers': {'playwright': {
        'command': '/nonexistent/mcp-fixture-command' if broken else sys.executable,
        'args': [script] + (['--empty'] if empty else []),
    }}}, ensure_ascii=False, indent=2)


def verify(origin, fixture, output):
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
        card = page.locator('.mcp-config-card')

        def write_config(target_page, text, *, create=False):
            target_page.get_by_role('button', name='添加MCP配置' if create else '编辑', exact=True).click()
            dialog = target_page.get_by_role('dialog', name='添加MCP配置' if create else '编辑MCP配置', exact=True)
            editor = dialog.locator('.monaco-editor textarea').first
            editor.focus()
            target_page.keyboard.press('ControlOrMeta+A')
            editor.evaluate("""(node, value) => {
                const transfer = new DataTransfer();
                transfer.setData('text/plain', value);
                node.dispatchEvent(new ClipboardEvent('paste', {
                    clipboardData: transfer, bubbles: true, cancelable: true
                }));
            }""", text)
            with target_page.expect_response(lambda r: '/ai-core/mcp-configs/' in r.url and r.request.method in ('POST', 'PUT')) as result:
                dialog.get_by_role('button', name='保存' if create else '更新', exact=True).click()
            return result.value, dialog

        def refresh(expected):
            card.get_by_role('button', name='刷新工具', exact=True).click()
            expect(card.get_by_text(expected, exact=True)).to_be_visible(timeout=25000)
            expect(card.get_by_role('button', name='刷新工具', exact=True)).to_be_enabled(timeout=25000)

        try:
            expect(page.get_by_role('button', name='添加MCP配置', exact=True)).to_be_visible()
            expect(page.get_by_placeholder('搜索MCP配置...')).to_have_count(0)
            # Leave a second tab on the empty state before the first admin creates it.
            stale_page = context.new_page()
            stale_page.goto(origin + '/ai-config/mcp')
            expect(stale_page.get_by_role('button', name='添加MCP配置', exact=True)).to_be_visible()
            created, dialog = write_config(page, fixture_config(), create=True)
            assert created.status in (200, 201), created.text()
            config_id = created.json()['data']['id']
            expect(dialog).not_to_be_visible()
            expect(card).to_contain_text('尚未检测')
            expect(page.get_by_role('button', name='添加MCP配置', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='删除', exact=True)).to_have_count(0)
            expect(card.get_by_role('switch')).to_be_checked()
            duplicate, duplicate_dialog = write_config(stale_page, fixture_config(), create=True)
            assert duplicate.status == 409, duplicate.text()
            expect(duplicate_dialog).not_to_be_visible()
            expect(stale_page.locator('.mcp-config-card')).to_contain_text('playwright')
            expect(stale_page.get_by_role('button', name='添加MCP配置', exact=True)).to_have_count(0)
            stale_page.close()
            assert database(lambda: MCPConfiguration.objects.count()) == 1

            refresh('已发现 2 个工具')
            card.locator('.tools-expand-icon').click()
            expect(page.get_by_text('fixture_read_page', exact=True)).to_be_visible()
            page.locator('h2').first.click()
            card.locator('.el-switch').click()
            expect(card.get_by_role('switch')).not_to_be_checked()
            expect(page.get_by_role('button', name='添加MCP配置', exact=True)).to_have_count(0)
            refresh('已发现 2 个工具')
            expect(card.get_by_role('switch')).not_to_be_checked()
            page.screenshot(path=str(output / 'singleton-ready.png'), full_page=True, animations='disabled')

            # Edit the same record through Monaco, invalidate its old catalog, then discover an empty one.
            edited, dialog = write_config(page, fixture_config(empty=True))
            assert edited.status == 200, edited.text()
            expect(dialog).not_to_be_visible()
            expect(card).to_contain_text('尚未检测')
            refresh('已发现 0 个工具')
            edited, dialog = write_config(page, fixture_config(broken=True))
            assert edited.status == 200, edited.text()
            expect(dialog).not_to_be_visible()
            refresh('检测失败')
            expect(card).not_to_contain_text('已发现 0 个工具')
            page.reload()
            expect(card.get_by_text('检测失败', exact=True)).to_be_visible()
            expect(card.get_by_role('switch')).not_to_be_checked()
            headers = {'Authorization': 'Bearer ' + fixture['tokens']['root']}
            denied = context.request.delete(origin + f'/api/v1/ai-core/mcp-configs/{config_id}/', headers=headers)
            assert denied.status == 405, denied.text()
            assert database(lambda: MCPConfiguration.objects.count()) == 1

            # A load failure is not an empty installation and must never reveal Add.
            list_url = origin + '/api/v1/ai-core/mcp-configs/'
            page.route(list_url, lambda route: route.fulfill(status=503, json={'success': False, 'message': '隔离测试：服务暂不可用'}))
            page.reload()
            expect(page.get_by_role('button', name='重试', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='添加MCP配置', exact=True)).to_have_count(0)
            page.unroute(list_url)
            page.get_by_role('button', name='重试', exact=True).click()
            expect(card.get_by_text('检测失败', exact=True)).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(card.get_by_role('button', name='编辑', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflows on mobile'
            page.screenshot(path=str(output / 'singleton-mobile.png'), full_page=True, animations='disabled')
            card.get_by_role('button', name='编辑', exact=True).click()
            mobile_dialog = page.get_by_role('dialog', name='编辑MCP配置', exact=True)
            expect(mobile_dialog).to_be_visible()
            assert mobile_dialog.evaluate('(node) => node.getBoundingClientRect().width <= innerWidth'), 'Editor dialog overflows on mobile'
            mobile_dialog.get_by_role('button', name='取消', exact=True).click()
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
    print(f'PASS: isolated MCP configuration browser acceptance; evidence: {output}')


if __name__ == '__main__':
    main()
