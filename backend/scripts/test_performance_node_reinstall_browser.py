"""Real Vue/Django reinstall acceptance using disposable records and loopback.

Only explicit reinstall/enrollment writes reach the disposable SQLite database.
No real node, Docker command, business target or platform credential is used.
"""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import socket
import tempfile
import threading
from unittest import TestCase
from unittest.mock import patch
import uuid
from wsgiref.simple_server import make_server

from test_performance_plan_browser import BACKEND, bootstrap, guarded_application, loopback_only
from test_project_knowledge_browser import _QuietHandler


def seed(fixture):
    from django.utils import timezone
    from performance_testing.models import PerformanceNode, PerformanceRun
    node = PerformanceNode.objects.create(
        id=uuid.UUID('00000000-0000-4000-8000-000000000041'),
        project_id=fixture['project']['id'], name='保留历史的原节点', network_mode='lan',
        last_seen_at=timezone.now(), agent_version='0.3.2', engine_version='2.43.3',
        protocol_version=2, agent_token_digest='1' * 64,
        enrollment_consumed_at=timezone.now() - timedelta(days=1),
    )
    history = PerformanceRun.objects.create(
        project_id=fixture['project']['id'], request_id=uuid.uuid4(),
        mode='validation', status='completed', snapshot_sha256='0' * 64,
        snapshot={'plan_name': '原节点历史执行', 'users': 1, 'steps': []},
        latest_metrics={'requests': 1, 'failures': 0}, finished_at=timezone.now(),
    )
    history.participants.create(node=node, node_name=node.name, assigned_users=1, status='stopped')
    fixture.update(node_id=str(node.pk), history_id=str(history.pk), node_created_at=node.created_at)
    fixture['original_digest'] = node.agent_token_digest


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
    from performance_testing.models import PerformanceNode, PerformanceRun
    base = f'/api/v1/projects/{fixture["project"]["id"]}/performance/'
    installation_path = base + f'nodes/{fixture["node_id"]}/installation/'
    reinstall_path = base + f'nodes/{fixture["node_id"]}/reinstall/'
    def read_db(read):
        def execute():
            from django.db import connections
            try:
                return read()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(execute).result()
    def current_node():
        return read_db(lambda: PerformanceNode.objects.get(pk=fixture['node_id']))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True,
        )
        context = browser.new_context(viewport={'width': 1500, 'height': 1200})
        context.set_default_timeout(15000)
        context.add_init_script(
            "localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
            "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));"
            "window.reinstallCopies=[]; Object.defineProperty(navigator,'clipboard',{value:{"
            "writeText:async(text)=>{window.reinstallCopies.push(text)}}});"
        )
        page = context.new_page()
        errors, outside, mutations, replies = [], [], [], []
        held = []
        state = {'hold': False, 'fail': False}
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                outside.append(request.url)
                route.abort()
            elif request.method == 'POST' and request.url == origin + reinstall_path:
                mutations.append(request.post_data_json)
                if state['hold']:
                    held.append(route)
                    return
                if state['fail']:
                    route.fulfill(status=503, json={'error': {'message': '隔离验收模拟暂不可用'}})
                    return
                response = route.fetch()
                replies.append(response.json())
                route.fulfill(response=response)
            elif request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                raise AssertionError('Unexpected browser write')
            else:
                route.continue_()

        context.route('**/*', intercept)
        auth_headers = {'Authorization': 'Bearer ' + fixture['auth']['accessToken']}
        def history_readback():
            response = context.request.get(origin + base + f'runs/{fixture["history_id"]}/', headers=auth_headers)
            assert response.status == 200, f'history GET HTTP {response.status}: {response.text()[:800]}'
            return response.json()['data']

        try:
            history_before = history_readback()
            page.goto(origin + '/perf-testing/nodes')
            page.get_by_role('button', name='重新安装', exact=True).click()
            panel = page.get_by_test_id('node-reinstall-panel')
            expect(panel).to_be_visible()
            expect(panel).to_contain_text('0.3.2')
            expect(panel).to_contain_text(AGENT_VERSION)
            confirm = page.get_by_test_id('node-reinstall-confirm')
            submit = page.get_by_test_id('node-reinstall-submit')
            expect(submit).to_be_disabled()
            assert current_node().agent_token_digest == fixture['original_digest']
            confirm.click()
            expect(submit).to_be_enabled()
            page.get_by_role('dialog').filter(has=panel).screenshot(path=str(output / 'reinstall-guide.png'))
            state['hold'] = True
            submit.click()
            expect(submit).to_be_disabled()
            dialog = page.get_by_role('dialog').filter(has=panel)
            expect(dialog.locator('.el-dialog__headerbtn')).to_have_count(0)
            page.keyboard.press('Escape')
            expect(panel).to_be_visible()
            assert len(held) == 1
            # A failed POST must keep the registered node and permit retry.
            held.pop().fulfill(status=503, json={'error': {'message': '隔离验收模拟暂不可用'}})
            state['hold'] = False
            expect(submit).to_be_enabled()
            assert current_node().agent_token_digest == fixture['original_digest']
            submit.click()
            command_input = page.get_by_test_id('installation-command')
            expect(command_input).to_be_visible()
            command = command_input.input_value()
            args = shlex.split(command)
            assert args[:2] == ['docker', 'pull'] and '\n' not in command
            assert args[3:7] == ['&&', 'docker', 'image', 'tag']
            assert args[7] == args[2]
            assert args[8] == args[2].split('@')[0] + ':' + AGENT_VERSION
            assert args[9:13] == ['&&', 'docker', 'run', '-d']
            expect(page.get_by_test_id('installation-image-tag')).to_contain_text(args[8])
            assert 'bash' not in args and 'python3' not in args and 'upgrade-node.py' not in command
            assert args[args.index('--node-id') + 1] == fixture['node_id']
            token = args[args.index('--token') + 1]
            mount = args[args.index('--mount') + 1]
            assert hashlib.sha256(token.encode()).hexdigest()[:16] in mount
            node = current_node()
            assert node.pk == uuid.UUID(fixture['node_id']) and node.name == '保留历史的原节点'
            assert node.created_at == fixture['node_created_at'] and node.agent_token_digest == ''
            assert node.enrollment_consumed_at is None and node.last_seen_at is None
            assert history_readback() == history_before
            page.get_by_role('button', name='复制安装命令', exact=True).click()
            expect(page.get_by_text('安装命令已复制', exact=True)).to_be_visible()
            assert page.evaluate('window.reinstallCopies.length') == 1
            assert '--token' not in page.evaluate('JSON.stringify(localStorage)')
            assert token not in page.evaluate('JSON.stringify(localStorage)')
            # Do not persist a screenshot containing even a synthetic credential.
            page.get_by_role('dialog').filter(has=command_input).screenshot(
                path=str(output / 'new-install-command.png'), mask=[command_input])
            response = context.request.get(origin + installation_path, headers=auth_headers)
            assert response.status == 200 and 'command' not in response.json()['data']['installation']
            assert token not in json.dumps(response.json())
            version_data = {'protocol_version': PROTOCOL_VERSION, 'agent_version': AGENT_VERSION,
                            'engine_version': ENGINE_VERSION}
            enrollment = context.request.post(origin + '/api/v1/performance-agent/enroll/',
                data={'enrollment_token': token, **version_data})
            assert enrollment.status == 200
            identity = enrollment.json()['data']['agent_token']
            heartbeat = context.request.post(origin + '/api/v1/performance-agent/heartbeat/',
                headers={'Authorization': 'Node ' + identity},
                data={**version_data, 'resources': {'cpu_percent': 0.0, 'memory_percent': 0.0}})
            assert heartbeat.status == 200
            expect(page.get_by_role('dialog').get_by_text('在线', exact=True)).to_be_visible(timeout=15000)
            assert current_node().agent_version == AGENT_VERSION
            assert read_db(lambda: PerformanceRun.objects.count()) == 1 and history_readback() == history_before
            assert all(payload == {'confirm_old_container_removed': True} for payload in mutations)
            assert len(mutations) == 2 and len(replies) == 1
            assert not errors and not outside, (errors, outside)
            print(json.dumps({'browser': 'Vue/Django/Chrome', 'same_node_and_history_preserved': True,
                'confirmation_and_inflight_close_guard': True, 'failure_keeps_old_identity': True,
                'single_copyable_install_command_new_volume': True, 'registration_and_heartbeat': True,
                'token_not_persisted_or_returned_by_get': True, 'external_requests': 0,
                'real_nodes_modified': 0, 'load_runs_started': 0}, ensure_ascii=False))
        except Exception:
            # No screenshot on failure: the page can contain an ephemeral token.
            raise
        finally:
            context.close()
            browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='performance-reinstall-browser-') as temporary, \
            patch.object(socket.socket, 'connect', loopback_only(socket.socket.connect)), \
            patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temporary))
        seed(fixture)
        from performance_testing.tests.test_installation import ReleaseFixtureMixin
        class ReleaseFixture(ReleaseFixtureMixin, TestCase):
            pass
        release_fixture = ReleaseFixture()
        release_fixture.set_up_release_fixture()
        runtime = (BACKEND.parent / 'performance-node/src/performance_node/locust_runtime.py').read_bytes()
        release_fixture.runtime_path.write_bytes(runtime)
        release_fixture.manifest['runtime_sha256'] = hashlib.sha256(runtime).hexdigest()
        release_fixture.manifest['registry_version_ref'] = (
            release_fixture.manifest['registry_index_ref'].split('@')[0]
            + ':' + release_fixture.manifest['agent_version']
        )
        release_fixture.write_manifest()
        from django.core.wsgi import get_wsgi_application
        attempted_runs = []
        application = guarded_application(get_wsgi_application(), attempted_runs)
        output = BACKEND / 'temp' / 'performance-node-reinstall-browser'
        output.mkdir(parents=True, exist_ok=True)
        try:
            with release_fixture.release_environment(), patch(
                'performance_testing.views.controller_execution_status', return_value={
                    'controller_online': True, 'available': True, 'enabled': True, 'reason': ''}):
                server = make_server('127.0.0.1', 0, application, handler_class=_QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    verify(f'http://127.0.0.1:{server.server_port}', fixture, output)
                    assert not attempted_runs
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)
        finally:
            release_fixture.doCleanups()


if __name__ == '__main__':
    main()
