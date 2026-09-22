"""Exercise upgrade guidance in real Vue/Chrome using a disposable Django DB.

Only loopback GET requests are allowed. Commands are inspected/copied into an
in-memory clipboard, never executed, and no real node is enrolled or upgraded.
"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import uuid
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_performance_plan_browser import BACKEND, bootstrap, guarded_application, loopback_only
from test_project_knowledge_browser import _QuietHandler


def seed(fixture):
    from django.utils import timezone
    from performance_testing.models import PerformanceNode
    from performance_testing.serializers import PerformanceNodeSerializer
    node = PerformanceNode.objects.create(
        id=uuid.UUID('00000000-0000-4000-8000-000000000041'),
        project_id=fixture['project']['id'], name='原节点升级验收', network_mode='lan',
        last_seen_at=timezone.now(), agent_version='0.3.2', engine_version='2.43.3',
        protocol_version=2, agent_token_digest='1' * 64,
        enrollment_consumed_at=timezone.now(),
    )
    node.active_run_count = 0
    fixture['node'] = dict(PerformanceNodeSerializer(node).data)
    fixture['upgrade'] = {
        'available': True, 'reason': '', 'agent_version': '0.4.0',
        'node_id': str(node.pk), 'platform_url': 'https://fixture.invalid',
        'script_url': 'https://fixture.invalid/api/v1/performance-agent/install/upgrade-node.py',
        'script_sha256': 'a' * 64,
        'image_ref': 'docker.io/example/performance-node@sha256:' + 'b' * 64,
    }


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True,
        )
        context = browser.new_context(viewport={'width': 1500, 'height': 1200})
        context.set_default_timeout(12000)
        context.add_init_script(
            "localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
            "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));"
            "window.upgradeCopies=[]; Object.defineProperty(navigator,'clipboard',{value:{"
            "writeText:async(text)=>{window.upgradeCopies.push(text)}}});"
        )
        page = context.new_page()
        errors, outside, mutations = [], [], []
        state = {'busy': False, 'reads': 0, 'failed': False}
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                outside.append(request.url)
                route.abort()
            elif request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                mutations.append(request.url)
                route.abort()
            elif '/nodes/' in request.url and request.url.endswith('/installation/'):
                state['reads'] += 1
                if state['failed']:
                    route.fulfill(status=503, json={'error': {'message': '隔离验收读取失败'}})
                    return
                node = {**fixture['node'], 'active_run_count': 1 if state['busy'] else 0}
                upgrade = {'available': False, 'reason': '节点存在未结束任务，请等待任务结束后再升级。'} if state['busy'] else fixture['upgrade']
                route.fulfill(json={'success': True, 'data': {'node': node, 'installation': {
                    'available': True, 'upgrade_required': True, 'agent_version': '0.4.0',
                    'platform_url': 'https://fixture.invalid', 'supported_architectures': ['amd64', 'arm64'],
                    'upgrade': upgrade,
                }}})
            else:
                route.continue_()

        context.route('**/*', intercept)
        try:
            page.goto(origin + '/perf-testing/nodes')
            page.get_by_role('button', name='升级节点', exact=True).click()
            dialog = page.get_by_role('dialog', name='节点升级指导')
            expect(dialog).to_be_visible()
            panel = page.get_by_test_id('node-upgrade-panel')
            expect(panel).to_contain_text('0.3.2')
            expect(panel).to_contain_text('0.4.0')
            command = page.get_by_test_id('node-upgrade-command')
            container = page.get_by_test_id('node-upgrade-container')
            copy = page.get_by_test_id('node-upgrade-copy')
            value = command.input_value()
            assert '--node-id' in value and fixture['upgrade']['node_id'] in value
            assert 'sha256sum -c -' in value and fixture['upgrade']['script_sha256'] in value
            assert '--token' not in value and '--container' not in value and 'docker restart' not in value
            await_reads = state['reads']
            copy.click()
            expect(page.get_by_text('Docker 升级命令已复制', exact=True)).to_be_visible()
            assert state['reads'] > await_reads
            assert page.evaluate('window.upgradeCopies.length') == 1
            container.fill('node; touch /tmp/forbidden')
            expect(copy).to_be_disabled()
            container.fill('existing-node-41')
            expect(copy).to_be_enabled()
            assert "'--container' 'existing-node-41'" in command.input_value()
            dialog.screenshot(path=str(output / 'docker-upgrade.png'))
            panel.get_by_text('Unraid 模板', exact=True).first.click()
            expect(panel).to_contain_text('Repository')
            expect(panel).to_contain_text('Apply')
            panel.get_by_text('Compose', exact=True).click()
            service = panel.get_by_placeholder('请输入原 Compose 文件中的服务名')
            compose_copy = panel.get_by_role('button', name='复制 Compose 命令')
            expect(compose_copy).to_be_disabled()
            service.fill('performance-agent')
            compose_copy.click()
            expect(page.get_by_text('Compose 命令已复制', exact=True)).to_be_visible()
            copied = page.evaluate('window.upgradeCopies.at(-1)')
            assert '--no-deps --no-build --pull never' in copied
            assert "'performance-agent'" in copied and 'down' not in copied
            dialog.screenshot(path=str(output / 'compose-upgrade.png'))
            panel.get_by_text('Docker 命令升级', exact=True).first.click()
            state['busy'] = True
            copy.click()
            expect(panel).to_contain_text('请等待任务结束后再升级')
            expect(copy).to_have_count(0)
            assert page.evaluate('window.upgradeCopies.length') == 2
            state['busy'] = False
            dialog.locator('.el-dialog__headerbtn').click()
            expect(dialog).not_to_be_visible()
            page.get_by_role('button', name='升级节点', exact=True).click()
            expect(dialog).to_be_visible()
            expect(container).to_have_value('')
            state['failed'] = True
            copy.click()
            expect(page.get_by_text('无法刷新节点升级信息，请稍后重试', exact=True)).to_be_visible()
            assert page.evaluate('window.upgradeCopies.length') == 2
            assert not errors and not outside and not mutations, (errors, outside, mutations)
            print(json.dumps({
                'browser': 'Vue/Django/Chrome', 'old_node_upgrade_entry': True,
                'copy_refreshes_status': True, 'invalid_names_blocked': True,
                'active_runs_and_failed_refresh_block_copy': True,
                'unraid_and_compose_guides': True, 'reopen_clears_input': True,
                'external_requests': 0, 'mutations': 0, 'remote_upgrades': 0,
            }, ensure_ascii=False))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='performance-upgrade-browser-') as temporary, \
            patch.object(socket.socket, 'connect', loopback_only(socket.socket.connect)), \
            patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temporary))
        seed(fixture)
        from django.core.wsgi import get_wsgi_application
        attempts = []
        application = guarded_application(get_wsgi_application(), attempts)
        output = BACKEND / 'temp' / 'performance-node-upgrade-browser'
        output.mkdir(parents=True, exist_ok=True)
        with patch('performance_testing.views.controller_execution_status', return_value={
                'controller_online': True, 'available': True, 'enabled': True, 'reason': ''}):
            server = make_server('127.0.0.1', 0, application, handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
                assert not attempts
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == '__main__':
    main()
