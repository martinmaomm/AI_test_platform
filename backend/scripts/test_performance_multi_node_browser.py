"""Real Vue/Chrome multi-node UI with a disposable Django DB; no load is run."""
import json
import re
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
    from performance_testing.constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
    from performance_testing.models import PerformanceNode, PerformancePlan, PerformanceRun, PerformanceTarget
    from performance_testing.run_services import validation_key_for
    project_id = fixture['project']['id']
    target = PerformanceTarget.objects.create(project_id=project_id, name='No requests allowed',
        base_url='https://fixture.invalid', allowed_methods=['GET'])
    plan = PerformancePlan.objects.create(project_id=project_id, target=target, name='多节点隔离计划',
        users=1000, spawn_rate=100, duration_seconds=120, wait_seconds=1,
        steps=[{'name': 'probe', 'phase': 'main', 'method': 'GET', 'path': '/probe', 'query': {},
                'headers': {}, 'body_type': 'none', 'body': None, 'extract': [],
                'assertions': [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}]}])
    nodes = [PerformanceNode.objects.create(
        id=uuid.UUID(f'00000000-0000-4000-8000-{number:012d}'), project_id=project_id,
        name=f'节点 {name}', network_mode='lan', last_seen_at=timezone.now(),
        agent_token_digest='1' * 64, agent_version=AGENT_VERSION,
        engine_version=ENGINE_VERSION, protocol_version=PROTOCOL_VERSION)
        for number, name in [(1, 'A'), (2, 'B')]]
    validations = []
    for node in nodes:
        key = validation_key_for(plan, node)
        metrics = {'requests': 1, 'failures': 0, 'complete': True, 'validation_complete': True,
                   'validation_passed': True, 'main_steps_completed': 1, 'main_steps_total': 1, 'users': 0}
        run = PerformanceRun.objects.create(project_id=project_id, plan=plan,
            request_id=uuid.uuid4(), mode='validation', status='completed', validation_key=key,
            snapshot={'plan_name': plan.name, 'users': 1, 'spawn_rate': 1, 'steps': plan.steps},
            snapshot_sha256='0' * 64, latest_metrics=metrics, finished_at=timezone.now())
        run.participants.create(node=node, node_name=node.name, validation_key=key,
            assigned_users=1, status='stopped', stopped_at=timezone.now(), latest_metrics=metrics)
        validations.append(str(run.pk))
    aggregate = {'requests': 30, 'failures': 1, 'users': 0, 'complete': False,
                 'rps': 3, 'error_rate': 1/30, 'p95': 90, 'p99': 100, 'avg_response_time': 30}
    record = PerformanceRun.objects.create(project_id=project_id, plan=plan,
        request_id=uuid.uuid4(), mode='load', status='cancelled', snapshot={'plan_name': plan.name, 'users': 1000, 'spawn_rate': 100},
        snapshot_sha256='0' * 64, latest_metrics=aggregate, reason='用户停止，节点 B 缺失尾部统计')
    for index, node in enumerate(nodes):
        metrics = {**aggregate, 'requests': 10 * (index + 1), 'users': 500,
                   'worker_cpu': 12.5, 'worker_memory': 64*1024*1024, 'complete': index == 0,
                   'entries': [{'name': f'node-{index}', 'method': 'GET', 'requests': 10, 'failures': 0}]}
        record.participants.create(node=node, node_name=node.name, assigned_users=500,
            status='stopped' if index == 0 else 'lost', latest_metrics=metrics,
            stopped_at=timezone.now() if index == 0 else None,
            metrics_samples=[{'timestamp': timezone.now().isoformat(), 'metrics': metrics}])
    fixture.update(plan_id=plan.pk, node_ids=[str(n.pk) for n in nodes],
                   validations=validations, load_run_id=str(record.pk))


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
        context = browser.new_context(viewport={'width': 1500, 'height': 1100})
        context.set_default_timeout(12000)
        context.add_init_script("localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
            "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));")
        page = context.new_page()
        errors, outside, creates = [], [], []
        state = {'b_valid': False, 'fail_eligibility': False}
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                outside.append(request.url)
                route.abort()
            elif request.url.endswith('/node-eligibility/'):
                if state['fail_eligibility']:
                    route.fulfill(status=503, json={'error': {'message': '隔离资格读取失败'}})
                else:
                    route.fulfill(json={'data': {'items': [{
                        'node_id': node_id, 'node_name': f'节点 {"AB"[i]}', 'status': 'online',
                        'compatible': True, 'validation_valid': i == 0 or state['b_valid'],
                        'validation_run_id': fixture['validations'][i], 'reason_code': '',
                        'reason': '' if i == 0 or state['b_valid'] else '尚未完成验证',
                    } for i, node_id in enumerate(fixture['node_ids'])]}})
            elif request.method == 'POST' and '/runs/' in request.url:
                payload = request.post_data_json
                creates.append(payload)
                assert payload['mode'] == 'validation'
                assert payload['node_ids'] == [fixture['node_ids'][1]]
                state['b_valid'] = True
                route.fulfill(status=201, json={'data': {'id': fixture['validations'][1]}})
            else:
                route.continue_()

        context.route('**/*', intercept)
        try:
            page.goto(origin + '/perf-testing/plans')
            page.get_by_role('button', name='正式压测', exact=True).click()
            dialog = page.get_by_role('dialog', name='确认执行压测')
            expect(dialog).to_be_visible()
            dialog.locator('label.el-checkbox').filter(has_text='节点 B').click()
            expect(dialog.get_by_text('预计 500 用户', exact=True)).to_have_count(2)
            expect(dialog.get_by_role('button', name='开始正式压测', exact=True)).to_be_disabled()
            dialog.get_by_role('button', name='刷新节点资格').click()
            expect(dialog.get_by_role('checkbox', name='节点 B')).to_be_checked()
            state['fail_eligibility'] = True
            dialog.get_by_role('button', name='刷新节点资格').click()
            expect(dialog.get_by_text('隔离资格读取失败', exact=True).first).to_be_visible()
            expect(dialog.get_by_role('button', name='开始正式压测')).to_be_disabled()
            state['fail_eligibility'] = False
            dialog.get_by_role('button', name='刷新节点资格').click()
            dialog.get_by_role('button', name='单用户验证', exact=True).click()
            dialog.get_by_role('button', name='开始单用户验证', exact=True).click()
            expect(page).to_have_url(re.compile('/perf-testing/runs/' + fixture['validations'][1]))
            back = page.locator('.performance-run-detail .toolbar').get_by_role('button', name='返回正式压测节点选择')
            expect(back).to_be_visible()
            back.click()
            expect(page).to_have_url(re.compile(r'/perf-testing/plans\?'))
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('checkbox', name='节点 A')).to_be_checked()
            expect(dialog.get_by_role('checkbox', name='节点 B')).to_be_checked()
            expect(dialog.get_by_role('button', name='开始正式压测')).to_be_enabled()
            dialog.screenshot(path=str(output / 'node-selection.png'))
            dialog.get_by_role('button', name='查看验证').first.click()
            expect(page).to_have_url(re.compile('/perf-testing/runs/'))
            expect(page.locator('.performance-run-detail .toolbar').get_by_role('button', name='返回正式压测节点选择')).to_be_visible()
            assert len(creates) == 1, 'Viewing validation must not create another execution'
            page.goto(origin + '/perf-testing/runs/' + fixture['load_run_id'])
            expect(page.get_by_text('本轮结果不完整：', exact=False)).to_be_visible()
            node_table = page.locator('.el-table').filter(has_text='Worker CPU / RSS')
            expect(node_table).to_contain_text('已失联')
            expect(node_table).to_contain_text('未知')
            expect(node_table).to_contain_text('12.50% / 64.00 MiB')
            page.locator('label.el-radio-button').filter(has_text='节点 B').click()
            expect(page.locator('.metric-card').filter(has_text='请求数')).to_contain_text('20')
            expect(page.locator('.metric-card').filter(has_text='虚拟用户')).to_contain_text('未知')
            expect(page.get_by_text('node-1', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'node-report.png'), full_page=True)
            assert not errors and not outside, (errors, outside)
            print(json.dumps({'browser': 'Vue/Django/Chrome', 'multi_select': True,
                'qualification_failure_blocks': True, 'validation_return_preserves_nodes': True,
                'view_validation_does_not_run': True, 'node_metrics_and_unknown_state': True,
                'external_requests': 0, 'load_runs_started': 0}, ensure_ascii=False))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='performance-multi-browser-') as temporary, \
            patch.object(socket.socket, 'connect', loopback_only(socket.socket.connect)), \
            patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temporary))
        seed(fixture)
        from django.core.wsgi import get_wsgi_application
        attempts = []
        application = guarded_application(get_wsgi_application(), attempts)
        output = BACKEND / 'temp' / 'performance-multi-node-browser'
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
