"""Real Vue/Django validation report rendering, disposable database, no requests to targets."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import uuid
from unittest.mock import patch
from urllib.parse import urlencode
from wsgiref.simple_server import make_server

from test_performance_plan_browser import BACKEND, bootstrap, guarded_application, loopback_only
from test_project_knowledge_browser import _QuietHandler


def seed(fixture):
    from performance_testing.models import PerformanceNode, PerformanceRun
    from performance_testing.controller import bounded_metrics
    from performance_node.locust_runtime import ValidationTrace, assertion_failures
    node = PerformanceNode.objects.create(project_id=fixture['project']['id'], name='隔离节点', network_mode='lan')
    def step(name, phase='main'):
        return {'name': name, 'phase': phase, 'method': 'GET', 'path': '/items', 'query': {},
                'headers': {}, 'body_type': 'none', 'body': None, 'extract': [],
                'assertions': [{'check': 'status_code', 'comparator': 'eq', 'expected': 200}]}
    steps = [step('获取登录令牌', 'setup'), step('查询购物车'), step('依赖购物车的后续步骤')]
    steps[0].update(method='POST', body_type='json')
    steps[0]['extract'] = [{'name': 'token', 'check': 'body.token'}]
    steps[1]['assertions'] += [
        {'check': 'body.items', 'comparator': 'length_gt', 'expected': 0},
        {'check': 'body.count', 'comparator': 'eq', 'expected': 3},
    ]
    steps[1]['query'] = {'page': 1, 'tag': ['first', 'second'], 'empty': '',
                         'keyword': '中文 +&', 'note': '<script>window.queryExecuted=true</script>'}
    snapshot = {'mode': 'validation', 'plan_name': '验证明细隔离样本', 'variables': {}, 'steps': steps}
    trace = ValidationTrace(snapshot)
    first = {'status_code': 200, 'headers': {'Set-Cookie': 'fixture_session=browser-cookie; Path=/'}, 'body': {'token': 'fixture-private-token'}, 'text': ''}
    trace.request(1, steps[0], 'https://fixture.invalid/login?token=fixture-query-token',
                  {'Cookie': 'fixture_session=request-cookie'}, {'password': 'fixture-password'})
    trace.finish_step(1, steps[0], first, extracted={'token': 'fixture-private-token'}, elapsed=12)
    response = {'status_code': 200, 'headers': {'Content-Type': 'application/json'},
                'body': {'items': [], 'count': '3', 'diagnostic': '库存不足',
                         'html': '<script>window.evidenceExecuted=true</script>', 'large': 'x' * 10000}, 'text': ''}
    trace.request(2, steps[1], 'https://fixture.invalid/items?' + urlencode(steps[1]['query'], doseq=True),
                  {'Authorization': 'Bearer fixture-private-token'}, None)
    failures = assertion_failures(steps[1], 2, response)
    trace.finish_step(2, steps[1], response, failures=failures, elapsed=45.67)
    trace.finish()
    metrics = bounded_metrics({'requests': 2, 'failures': 1, 'complete': True,
        'validation_complete': True, 'validation_passed': False, 'main_steps_completed': 0,
        'main_steps_total': 2, 'failure_samples': failures, 'validation_steps': trace.report()}, snapshot, 'validation')
    def run(mode, metrics):
        record = PerformanceRun.objects.create(project_id=fixture['project']['id'],
            request_id=uuid.uuid4(), mode=mode, status='completed', snapshot=snapshot,
            snapshot_sha256='0' * 64, latest_metrics=metrics)
        record.participants.create(node=node, node_name=node.name, assigned_users=1, latest_metrics=metrics)
        return record
    fixture['detailed'] = str(run('validation', metrics).pk)
    fixture['legacy'] = str(run('validation', {'requests': 2, 'failures': 1, 'failure_samples': failures}).pk)
    fixture['load'] = str(run('load', {'requests': 2, 'failures': 1, 'failure_samples': failures}).pk)


def verify(origin, fixture):
    from playwright.sync_api import expect, sync_playwright
    output = BACKEND / 'temp' / 'performance-validation-browser'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1080})
        context.set_default_timeout(12000)
        context.add_init_script("localStorage.setItem('auth-store',JSON.stringify(" + json.dumps(fixture['auth']) + "));"
                                "localStorage.setItem('project-store',JSON.stringify(" + json.dumps({'currentProject': fixture['project']}) + "));")
        external, errors = [], []
        def intercept(route):
            if route.request.url.startswith(origin + '/'):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()
        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + '/perf-testing/runs/' + fixture['detailed'])
            details = page.get_by_test_id('validation-step-details')
            expect(details).to_be_visible()
            titles = details.locator('.step-title')
            expect(titles).to_have_count(3)
            expect(titles.nth(0)).to_contain_text('通过')
            expect(titles.nth(1)).to_contain_text('失败')
            expect(titles.nth(2)).to_contain_text('跳过')
            failed_step = details.locator('.el-collapse-item').nth(1)
            checks = failed_step.get_by_test_id('validation-assertions')
            expect(checks).to_be_visible()
            expect(checks.locator('.el-table__body tbody tr')).to_have_count(3)
            expect(checks).to_contain_text('长度大于')
            expect(checks).to_contain_text('"3"')
            expect(checks).to_contain_text('与期望值不符')
            expect(failed_step.get_by_test_id('validation-response')).to_contain_text('库存不足')
            expect(failed_step.get_by_test_id('validation-response')).to_contain_text('内容过长，已截断展示')
            expect(failed_step.get_by_test_id('validation-request-url')).to_contain_text('https://fixture.invalid/items?page=1')
            query = failed_step.get_by_test_id('validation-query')
            expect(query).to_be_visible()
            rows = query.locator('.el-table__body tbody tr')
            expect(rows).to_have_count(6)
            expect(rows.nth(1)).to_contain_text('first')
            expect(rows.nth(2)).to_contain_text('second')
            expect(rows.nth(3)).to_contain_text('（空字符串）')
            expect(rows.nth(4)).to_contain_text('中文 +&')
            expect(rows.nth(5)).to_contain_text('<script>window.queryExecuted=true</script>')
            expect(failed_step).to_contain_text('本步骤未配置请求体')
            assert not page.evaluate('window.queryExecuted === true')
            assert not page.evaluate('window.evidenceExecuted === true')
            expect(failed_step).to_contain_text('Bearer fixture-private-token')
            details.screenshot(path=str(output / 'failed-step.png'), animations='disabled')
            titles.nth(2).click()
            expect(details.get_by_text('第 2 步失败，后续步骤未执行', exact=True)).to_be_visible()
            titles.nth(0).click()
            expect(page.get_by_test_id('validation-extractions')).to_contain_text('fixture-private-token')
            expect(page.get_by_test_id('validation-extractions')).to_contain_text('${token}')
            login = details.locator('.el-collapse-item').nth(0)
            for original in ('fixture-password', 'fixture-query-token', 'fixture_session=request-cookie',
                             'fixture_session=browser-cookie; Path=/'):
                expect(login).to_contain_text(original)
            details.screenshot(path=str(output / 'raw-details.png'), animations='disabled')
            page.goto(origin + '/perf-testing/runs/' + fixture['legacy'])
            expect(page.get_by_test_id('validation-details-unavailable')).to_contain_text('历史缺失响应无法补回')
            expect(page.get_by_text('错误样本', exact=True)).to_be_visible()
            page.goto(origin + '/perf-testing/runs/' + fixture['load'])
            expect(page.get_by_text('错误样本', exact=True)).to_be_visible()
            expect(page.get_by_test_id('validation-step-details')).not_to_be_visible()
            assert not errors and not external, (errors, external)
            print(json.dumps({'browser': 'real Vue/Django/Chrome', 'assertion_details': True,
                'query_repeated_empty_encoded_values': True, 'query_html_escaped': True,
                'response_and_truncation': True, 'skipped_steps': True, 'original_credentials': True,
                'legacy_record_hint': True, 'load_mode_unchanged': True, 'page_errors': 0}, ensure_ascii=False))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='performance-validation-browser-') as temporary, patch.object(
        socket.socket, 'connect', loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, 'connect_ex', loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temporary))
        seed(fixture)
        from django.core.wsgi import get_wsgi_application
        attempted_runs = []
        server = make_server('127.0.0.1', 0, guarded_application(get_wsgi_application(), attempted_runs), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify(f'http://127.0.0.1:{server.server_port}', fixture)
            assert not attempted_runs
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
