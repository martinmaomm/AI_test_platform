#!/usr/bin/env python3
"""Explicit opt-in live acceptance helpers; never print credentials or tokens.

Preflight performs real browser login and read-only target checks. Further paid
generation or business mutations are separate, explicitly selected test steps.
All artifacts belong in the ignored backend/logs directory.
"""
import argparse
import hashlib
import json
import os
import time
import subprocess
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, data, private=False):
    # Private artifacts are created with restrictive permissions before writing.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    if private:
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def target_login(args):
    password = os.environ.get('TEST_TARGET_PASSWORD')
    if not password:
        raise RuntimeError('缺少 TEST_TARGET_PASSWORD；不尝试默认密码。')
    session = requests.Session()
    response = session.post(args.target_api + '/admin/login', json={
        'username': args.target_username, 'password': password,
    }, timeout=20)
    payload = response.json()
    if response.status_code != 200 or payload.get('code') != 200:
        raise RuntimeError(f'被测站登录失败 HTTP={response.status_code}, business={payload.get("code")}')
    data = payload['data']
    session.headers['Authorization'] = data.get('tokenHead', 'Bearer ') + data['token']
    return session


def preflight(args):
    for key in ('TEST_PLATFORM_USERNAME', 'TEST_PLATFORM_PASSWORD'):
        if not os.environ.get(key):
            raise RuntimeError(f'缺少 {key}')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 1680, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(args.frontend + '/login')
        page.get_by_placeholder('用户名', exact=True).fill(os.environ['TEST_PLATFORM_USERNAME'])
        page.get_by_placeholder('密码', exact=True).fill(os.environ['TEST_PLATFORM_PASSWORD'])
        with page.expect_response(lambda r: r.url.endswith('/users/login/') and r.request.method == 'POST') as login:
            page.get_by_role('button', name='登录', exact=True).click()
        result = login.value.json()
        if login.value.status != 200 or not result.get('success'):
            raise RuntimeError(f'平台真实登录失败 HTTP={login.value.status}')
        page.wait_for_url(lambda url: '/login' not in str(url))
        page.screenshot(path=str(args.output / 'platform-login.png'), full_page=True)
        write_json(args.output / 'browser-state.private.json', context.storage_state(), private=True)
        write_json(args.output / 'platform-auth.private.json', result['data'], private=True)
        user = result['data']['user']
        platform_user = {'id': user['id'], 'username': user['username']}
        browser.close()
    session = target_login(args)
    identity = session.get(args.target_api + '/admin/info', timeout=20)
    identity.raise_for_status()
    info = identity.json()
    if info.get('code') != 200:
        raise RuntimeError('被测账号身份读回失败')
    data = info.get('data', {})
    # Never save a token or credentials in the public identity baseline.
    baseline = {k: data[k] for k in ('username', 'roles') if k in data}
    baseline['menus_sha256'] = hashlib.sha256(json.dumps(data.get('menus', []), sort_keys=True).encode()).hexdigest()
    queries = []
    for authenticated in (False, True):
        client = session if authenticated else requests.Session()
        r = client.get(args.target_api + args.query_path, timeout=20)
        body = r.json()
        d = body.get('data') or {}
        queries.append({'authenticated': authenticated, 'http_status': r.status_code,
                        'business_code': body.get('code'), 'data_fields': sorted(d) if isinstance(d, dict) else [],
                        'item_count': len(d) if isinstance(d, list) else len(d.get('list', [])) if isinstance(d, dict) else None})
    public = {'platform_user': platform_user, 'target_identity': baseline,
              'frontend': args.frontend, 'target_api': args.target_api, 'queries': queries}
    write_json(args.output / 'preflight.json', public)
    print(json.dumps(public, ensure_ascii=False), flush=True)


def platform_session(args):
    auth = json.loads((args.output / 'platform-auth.private.json').read_text())
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + auth['access']
    return session


def launch_a(args):
    ledger = args.output / 'live-a.json'
    if ledger.exists():
        raise RuntimeError('LIVE-A 已有记录；先检查原任务，不重复创建/运行。')
    if not os.environ.get('TEST_TARGET_PASSWORD'):
        raise RuntimeError('缺少 TEST_TARGET_PASSWORD')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(storage_state=str(args.output / 'browser-state.private.json'),
                                      viewport={'width': 1680, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(args.frontend + '/api-testing/test-cases/endpoint')
        expect(page.locator('.project-name')).to_have_text('后台商城API')
        page.get_by_test_id('endpoint-generate-entry').first.click()
        dialog = page.get_by_test_id('endpoint-generation-dialog')
        dialog.get_by_test_id('endpoint-generation-spec').click()
        page.get_by_role('option').filter(has_text='商城后台api-docs.json').click()
        dialog.get_by_test_id('endpoint-generation-target').click()
        page.get_by_role('option').filter(has_text='/resourceCategory/listAll').click()
        with page.expect_response(lambda r: '/workspaces/' in r.url and r.request.method == 'POST') as creation:
            dialog.get_by_test_id('endpoint-generation-open').click()
        assert creation.value.status == 201, f'创建失败 HTTP={creation.value.status}'
        workspace = creation.value.json()['data']
        assert workspace['target_endpoint_id'] == 29
        record = {'root_id': workspace['id'], 'target_endpoint_id': 29, 'model_id': 5,
                  'started_at': time.time(), 'generation_submitted': False}
        write_json(ledger, record)
        page.get_by_role('combobox', name='模型', exact=True).click()
        page.get_by_role('option').filter(has_text='思辰科技').filter(has_text='gpt-5.6-luna').click()
        group = page.get_by_role('group', name='可用辅助接口 / 依赖范围', exact=True)
        login = group.get_by_role('checkbox', name='POST /admin/login', exact=True)
        if not login.is_checked():
            group.get_by_text('POST /admin/login', exact=True).click()
        expect(login).to_be_checked()
        # The target and login are the only approved scope for this query sample.
        selected = group.locator('input:checked').count()
        assert selected == 2, f'辅助范围异常，共选中 {selected} 个接口'
        save = page.get_by_role('button', name='保存工作区设置', exact=True)
        if save.is_enabled():
            with page.expect_response(lambda r: '/workspaces/' in r.url and r.request.method == 'PATCH'):
                save.click()
        page.get_by_role('textbox', name='描述测试目标', exact=True).fill(
            '仅生成1个正向端点用例：每次先调用登录接口，使用本次变量 username、password 登录并动态提取 token 和 tokenHead；'
            '然后带认证请求 GET /resourceCategory/listAll 查询资源分类。'
            '验证 HTTP 状态200、响应业务 code 为数字200、data 为列表且列表长度大于0。'
            '登录也是辅助步骤，必须保留在同一独立可重跑用例中。'
            '只允许登录和查询，不新增、修改、删除任何数据，不改变账户和角色权限。'
            '不要硬编码 token，不使用上次执行的会话或对象ID。')
        page.get_by_role('button', name='生成并验证全流程', exact=True).click()
        confirmation = page.get_by_role('dialog', name='生成并验证确认', exact=True)
        confirmation.get_by_role('textbox', name='目标地址', exact=True).fill(args.target_api)
        for key, value in [('username', args.target_username), ('password', os.environ['TEST_TARGET_PASSWORD'])]:
            confirmation.get_by_role('button', name='+ 添加', exact=True).click()
            row = confirmation.locator('.key-value-row').last
            row.get_by_placeholder('变量名').fill(key)
            row.get_by_placeholder('本次值').fill(value)
        with page.expect_response(lambda r: r.url.endswith('/messages/') and r.request.method == 'POST') as sent:
            confirmation.get_by_role('button', name='确认并开始验证', exact=True).click()
        record['submission_http'] = sent.value.status
        record['generation_submitted'] = sent.value.status in (200, 202)
        write_json(ledger, record)
        expect(confirmation).to_be_hidden()
        page.screenshot(path=str(args.output / 'live-a-started.png'), full_page=True)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        browser.close()


def finish_a(args):
    ledger_path = args.output / 'live-a.json'
    record = json.loads(ledger_path.read_text())
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    root = session.get(base + f'workspaces/{record["root_id"]}/', timeout=15).json()['data']
    assert len(root['scenarios']) == 1
    child = root['scenarios'][0]
    candidate = child.get('candidate') or {}
    assert candidate.get('verification_status') == 'passed'
    assert not candidate.get('risks')
    steps = candidate['draft']['teststeps']
    assert [(s['request']['method'], s['request']['url']) for s in steps] == [
        ('POST', '/admin/login'), ('GET', '/resourceCategory/listAll')]
    assert {'length_gt': ['body.data', 0]} in steps[1]['validate']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(storage_state=str(args.output / 'browser-state.private.json'),
                                      viewport={'width': 1680, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(args.frontend + f'/api-testing/workspace/documents?workspace_id={root["id"]}')
        expect(page.get_by_test_id('api-workspace-overview')).to_contain_text('已验证通过')
        page.get_by_test_id('api-workspace-overview').screenshot(path=str(args.output / 'live-a-passed-overview.png'))
        page.get_by_test_id(f'api-workspace-scenario-{child["id"]}').click()
        if not child['saved_case_id']:
            if not child['draft']['teststeps']:
                page.get_by_role('button', name='采用候选并替换草稿', exact=True).click()
                page.get_by_role('button', name='采用', exact=True).click()
            page.get_by_role('button', name='保存为端点用例', exact=True).click()
            save_dialog = page.get_by_role('dialog', name='保存为端点用例', exact=True)
            # Use the form's first textbox (title); leave type positive.
            save_dialog.get_by_role('textbox').first.fill('验收LIVE-A-登录查询资源分类')
            with page.expect_response(lambda r: r.url.endswith('/save/') and r.request.method == 'POST') as saved:
                save_dialog.get_by_role('button', name='确认保存', exact=True).click()
            assert saved.value.status == 200
            child = saved.value.json()['data']
        record.update({'child_id': child['id'], 'saved_case_id': child['saved_case_id'],
                       'generation_attempts': child['generation'].get('attempt')})
        write_json(ledger_path, record)
        browser.close()
    case = session.get(base + f'test-cases/{record["saved_case_id"]}/', timeout=15).json()['data']
    assert case['test_case_type'] == 'endpoint' and case['endpoint_info']['id'] == 29
    assert len(json.loads(case['script_content'])['teststeps']) == 2
    print(json.dumps(record, ensure_ascii=False), flush=True)


def replay_a(args):
    ledger_path = args.output / 'live-a.json'
    record = json.loads(ledger_path.read_text())
    if record.get('executions'):
        raise RuntimeError('已有重跑记录，先核对原执行结果，禁止静默重放')
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    record['executions'] = []
    for index in range(3):
        entry = {'round': index + 1, 'dispatch_state': 'pending_response'}
        record['executions'].append(entry)
        write_json(ledger_path, record)
        r = session.post(base + f'test-cases/{record["saved_case_id"]}/execute/', json={'environment_id': 1}, timeout=20)
        assert r.status_code in (200, 202), f'派发失败 HTTP={r.status_code}'
        entry.update({**r.json()['data'], 'dispatch_state': 'accepted'})
        write_json(ledger_path, record)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            r = session.get(base + f'executions/{entry["execution_id"]}/report/', timeout=15)
            assert r.status_code == 200
            report = r.json()['data']
            status = report.get('status') or report.get('execution', {}).get('status')
            if status in ('passed', 'failed', 'error', 'stopped'):
                write_json(args.output / f'live-a-run-{index + 1}.private.json', report, private=True)
                entry['status'] = status
                write_json(ledger_path, record)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
                assert status == 'passed', '真实执行失败，停止重复运行并分析证据'
                break
            time.sleep(2)
        else:
            raise RuntimeError('执行轮询超时；保留 execution_id，不能盲目重复派发')


def launch_b(args):
    ledger = args.output / 'live-b.json'
    if ledger.exists():
        raise RuntimeError('LIVE-B 已有任务记录，不重复发起')
    target = target_login(args)
    initial = target.get(args.target_api + '/resourceCategory/listAll', timeout=20).json()
    assert initial.get('code') == 200
    write_json(args.output / 'category-baseline.private.json', initial['data'], private=True)
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    r = session.post(base + 'workspaces/', json={
        'title': '验收LIVE-B-资源分类CRUD与未认证查询', 'model_id': 5, 'spec_id': 1,
        'endpoint_ids': [4, 27, 28, 29, 30],
    }, timeout=20)
    assert r.status_code == 201
    root = r.json()['data']
    record = {'root_id': root['id'], 'model_id': 5, 'started_at': time.time(), 'generation_submitted': False}
    write_json(ledger, record)
    prompt = (
        '请只生成两个互相独立可重复执行的场景。'
        '场景1：每次用变量 username/password 登录，然后创建一个本轮临时资源分类，'
        '名字以 qa_api_live_b_ 开头，用平台运行时唯一值保证每次执行都不同，不写死时间戳。'
        '查询完整资源分类列表，按本次唯一名称精确确认新建项并提取它的ID，不能使用列表第一项或历史ID。'
        '仅更新该新建分类的名称（同样唯一），查询并验证名称更新，删除本轮新建分类，再查询完整列表断言该ID不存在。'
        '新增、编辑、删除和查询都要有业务断言，不仅检查HTTP200。只操作本轮新建分类，禁止修改任何既有分类，'
        '不要操作用户、角色、授权或菜单。'
        '场景2：不要登录、不带Authorization和Cookie，直接请求GET /resourceCategory/listAll，'
        '验证HTTP200但响应业务code是数字401；这是未认证拒绝的反向测试，不要改成登录成功的正向用例。'
        '每个场景有独立会话；凭据由本次变量提供，不复用另一场景的token。')
    r = session.post(base + f'workspaces/{root["id"]}/messages/', json={
        'revision': root['revision'], 'mode': 'generate', 'message': prompt,
        'execution_confirmed': True, 'base_url': args.target_api,
        'variables': {'username': args.target_username, 'password': os.environ['TEST_TARGET_PASSWORD']},
    }, timeout=25)
    record.update({'submission_http': r.status_code, 'generation_submitted': r.status_code == 202})
    write_json(ledger, record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    assert r.status_code == 202


def launch_c(args):
    ledger = args.output / 'live-c.json'
    if ledger.exists():
        raise RuntimeError('LIVE-C 已有任务记录，不重复发起')
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    prior = json.loads((args.output / 'live-b.json').read_text())
    status = session.get(base + f'workspaces/{prior["root_id"]}/', timeout=15).json()['data']['status']
    assert status not in ('generating', 'debugging'), '保持串行：上一生成任务未结束'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(storage_state=str(args.output / 'browser-state.private.json'),
                                      viewport={'width': 1680, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(args.frontend + '/api-testing/workspace/browser')
        page.get_by_test_id('api-browser-discovery-new').click()
        form = page.get_by_test_id('api-browser-discovery-create-form')
        form.get_by_role('textbox', name='完整页面 URL', exact=True).fill('http://192.168.31.188:9990/')
        form.get_by_role('textbox', name='探索目标说明', exact=True).fill(
            f'仅探索登录后查询资源分类这一个小流程。使用账号 {args.target_username}，密码 {os.environ["TEST_TARGET_PASSWORD"]} 登录。'
            '进入权限模块的资源列表，打开资源分类管理，查看现有分类列表。'
            '只需获取本次登录和资源分类列表查询的真实网络样本，确认列表加载完成即可停止。'
            '本次不新增、不编辑、不删除数据，不分配资源、不改账户或角色权限，不探索其他模块。'
            '接口地址请依据网页真实网络请求识别。')
        # Element Plus' selected label overlays its readonly inner input.
        form.locator('.el-select__wrapper').click()
        page.get_by_role('option').filter(has_text='思辰科技').filter(has_text='gpt-5.6-luna').click()
        form.get_by_text('我确认允许在上述授权测试范围内修改测试数据', exact=True).click()
        expect(form.get_by_role('checkbox', name='允许测试数据写入')).to_be_checked()
        # Do not fill optional API origin or increase the existing limits.
        write_json(ledger, {'dispatch_state': 'pending_response', 'started_at': time.time()})
        with page.expect_response(lambda r: '/browser-discoveries/' in r.url and r.request.method == 'POST') as created:
            form.get_by_role('button', name='开始探索', exact=True).click()
        assert created.value.status == 202, f'探索派发失败 HTTP={created.value.status}'
        task = created.value.json()['data']
        assert not task['api_origin']
        record = {k: task[k] for k in ('id', 'task_id', 'model_id', 'status', 'limits', 'exploration_timeout_seconds')}
        write_json(ledger, record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        browser.close()


def run_formal_case(session, base, case_id, output, label):
    """Dispatch once, persist identity immediately, and stop on the first failure."""
    ledger_path = output / f'{label}-executions.json'
    if ledger_path.exists():
        raise RuntimeError(f'{label} 已执行，禁止未经检查重放')
    entries = []
    for index in range(3):
        item = {'dispatch_state': 'pending_response', 'round': index + 1}
        entries.append(item)
        write_json(ledger_path, entries)
        r = session.post(base + f'test-cases/{case_id}/execute/', json={'environment_id': 1}, timeout=20)
        assert r.status_code in (200, 202)
        item.update({k: r.json()['data'][k] for k in ('execution_id', 'task_id')})
        item['dispatch_state'] = 'accepted'
        write_json(ledger_path, entries)
        for _ in range(60):
            report = session.get(base + f'executions/{item["execution_id"]}/report/', timeout=15).json()['data']
            if report['status'] in ('passed', 'failed', 'error', 'stopped'):
                write_json(output / f'{label}-run-{index + 1}.private.json', report, private=True)
                item['status'] = report['status']
                item['steps'] = report['httprunner_result']['stat']['teststeps']
                write_json(ledger_path, entries)
                print(json.dumps({'label': label, 'round': index + 1, **item}, ensure_ascii=False), flush=True)
                assert item['status'] == 'passed', '失败后停止独立重跑，保留原始证据'
                break
            time.sleep(2)
        else:
            raise RuntimeError('轮询超时，不重复派发')


def finish_b_negative(args):
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    ledger_path = args.output / 'live-b.json'
    record = json.loads(ledger_path.read_text())
    root = session.get(base + f'workspaces/{record["root_id"]}/', timeout=15).json()['data']
    assert root['status'] not in ('generating', 'debugging')
    c = root['scenarios'][1]
    assert c['candidate']['verification_status'] == 'passed' and not c['candidate']['risks']
    draft = c['candidate']['draft']
    assert len(draft['teststeps']) == 1 and not draft['config'].get('headers')
    step = draft['teststeps'][0]
    assert step['request']['url'] == '/resourceCategory/listAll'
    assert step['request']['method'] == 'GET' and not step['request'].get('headers')
    assert step['validate'] == [{'eq': ['status_code', 200]}, {'eq': ['body.code', 401]}]
    if not c['saved_case_id']:
        r = session.patch(base + f'workspaces/{c["id"]}/', json={'revision': c['revision'], 'draft': draft}, timeout=20)
        assert r.status_code == 200
        c = r.json()['data']
        r = session.post(base + f'workspaces/{c["id"]}/save/', json={
            'revision': c['revision'], 'title': '验收LIVE-B-未认证资源分类查询', 'test_type': 'negative',
        }, timeout=20)
        assert r.status_code == 200
        c = r.json()['data']
    record.update({'negative_child_id': c['id'], 'negative_case_id': c['saved_case_id'], 'crud_status': root['scenarios'][0]['status']})
    write_json(ledger_path, record)
    run_formal_case(session, base, c['saved_case_id'], args.output, 'live-b-negative')


def export_a(args):
    output = args.output
    if (output / 'export-result.private.json').exists():
        raise RuntimeError('已有导出运行结果，不重复运行')
    record = json.loads((output / 'live-a.json').read_text())
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    data = session.get(base + f'workspaces/{record["child_id"]}/python/', timeout=20).json()['data']
    script = output / data['filename']
    fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(data['code'])
    # Run unchanged export in a separate venv with only requests installed.
    python = output / 'export-venv/bin/python'
    result = subprocess.run([str(python), '-I', str(script)], cwd=str(output),
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        write_json(output / 'export-failure.private.json', {'stdout': result.stdout, 'stderr': result.stderr}, private=True)
        raise RuntimeError('独立导出运行失败；不回显含认证信息的输出')
    report = json.loads(result.stdout)
    write_json(output / 'export-result.private.json', report, private=True)
    summary = {'returncode': result.returncode, 'success': report['success'],
               'steps': report['stat']['teststeps'], 'workspace_revision': data['revision'],
               'python_isolated': True, 'only_explicit_dependency': 'requests==2.32.5'}
    write_json(output / 'export-summary.json', summary)
    assert report['success'] and report['stat']['teststeps']['successes'] == 2
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def handoff_c(args):
    ledger_path = args.output / 'live-c.json'
    record = json.loads(ledger_path.read_text())
    if record.get('root_id') or record.get('handoff_pending'):
        raise RuntimeError('已有交接记录，先检查原任务，不重复交接或生成')
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    task = session.get(base + f'browser-discoveries/{record["id"]}/', timeout=15).json()['data']
    assert task['status'] == 'completed' and task['api_origin'] == args.target_api
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(storage_state=str(args.output / 'browser-state.private.json'),
                                      viewport={'width': 1680, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(args.frontend + '/api-testing/workspace/browser')
        page.get_by_role('button', name='查看', exact=True).first.click()
        page.get_by_role('button', name='查看已授权样本', exact=True).click()
        expect(page.locator('.record-row')).to_have_count(4)
        for row in page.locator('.record-row').all():
            wanted = row.locator('strong').inner_text() in ('POST /admin/login', 'GET /resourceCategory/listAll')
            for index, checkbox in enumerate(row.locator('.el-checkbox').all()):
                selected = checkbox.locator('input').is_checked()
                if selected != (wanted and index == 0):
                    checkbox.locator('.el-checkbox__label').click()
        assert page.locator('.record-row input:checked').count() == 2
        # Only the sample group area is captured; task description contains login credentials.
        page.locator('.record-row').filter(has_text='GET /resourceCategory/listAll').screenshot(
            path=str(args.output / 'live-c-deduplicated-query.png'))
        record['handoff_pending'] = True
        write_json(ledger_path, record)
        with page.expect_response(lambda r: r.url.endswith('/handoff/') and r.request.method == 'POST') as handoff:
            page.get_by_role('button', name='创建来源并进入工作区', exact=True).click()
        assert handoff.value.status in (200, 201)
        data = handoff.value.json()['data']
        assert data['spec']['spec_type'] == 'browser_capture'
        record.update({'root_id': data['workspace']['id'], 'spec_id': data['spec']['id'],
                       'source_type': data['workspace']['source_type'], 'endpoint_ids': data['endpoint_ids'],
                       'handoff_pending': False})
        write_json(ledger_path, record)
        browser.close()
    generate_c(args)


def generate_c(args):
    ledger_path = args.output / 'live-c.json'
    record = json.loads(ledger_path.read_text())
    if record.get('generation_dispatch_state'):
        raise RuntimeError('已有生成派发记录，先检查任务，不重复派发')
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    root = session.get(base + f'workspaces/{record["root_id"]}/', timeout=15).json()['data']
    assert root['source_type'] == 'browser_capture' and root['model_id'] == 5
    record['generation_dispatch_state'] = 'pending_response'
    write_json(ledger_path, record)
    r = session.post(base + f'workspaces/{root["id"]}/messages/', json={
        'revision': root['revision'], 'mode': 'generate', 'execution_confirmed': True,
        'base_url': args.target_api, 'variables': {'username': args.target_username, 'password': os.environ['TEST_TARGET_PASSWORD']},
        'message': '仅根据本次网页捕获的登录和资源分类查询样本生成1个独立可重复执行的正向场景。'
                   '每次使用变量username/password重新登录，从本次实际响应动态提取token和tokenHead，'
                   '再携带该认证查询资源分类完整列表，断言HTTP200、业务code数字200、data为列表且长度大于0。'
                   '禁止使用探索期间保存的token或Cookie，禁止新增修改删除数据。只使用本次网页来源，不使用Swagger。',
    }, timeout=25)
    record['generation_dispatch_state'] = 'accepted' if r.status_code == 202 else 'rejected'
    write_json(ledger_path, record)
    assert r.status_code == 202
    print(json.dumps({k:record.get(k) for k in ('id', 'root_id', 'spec_id', 'source_type', 'endpoint_ids', 'generation_dispatch_state')}, ensure_ascii=False), flush=True)


def finish_c(args):
    ledger_path = args.output / 'live-c.json'
    record = json.loads(ledger_path.read_text())
    session = platform_session(args)
    base = 'http://127.0.0.1:8000/api/v1/projects/2/api-testing/'
    root = session.get(base + f'workspaces/{record["root_id"]}/', timeout=15).json()['data']
    assert root['status'] == 'ready' and len(root['scenarios']) == 1
    c = root['scenarios'][0]
    candidate = c.get('candidate')
    if candidate:
        assert candidate['verification_status'] == 'passed' and not candidate['risks']
        draft = candidate['draft']
    else:
        assert c['generation']['status'] == 'passed' and c['generation']['adopted_revision'] == c['revision']
        draft = c['draft']
    assert [(s['request']['method'], s['request']['url']) for s in draft['teststeps']] == [
        ('POST', '/admin/login'), ('GET', '/resourceCategory/listAll')]
    assert {'length_gt': ['body.data', 0]} in draft['teststeps'][1]['validate']
    assert draft['teststeps'][1]['request']['headers']['Authorization'] == '${tokenHead}${token}'
    if not c['saved_case_id']:
        if candidate:
            r = session.patch(base + f'workspaces/{c["id"]}/', json={'revision': c['revision'], 'draft': draft}, timeout=20)
            assert r.status_code == 200
            c = r.json()['data']
        r = session.post(base + f'workspaces/{c["id"]}/save/', json={
            'revision': c['revision'], 'title': '验收LIVE-C-网页采集登录查询资源分类',
        }, timeout=20)
        assert r.status_code == 200
        c = r.json()['data']
    record.update({'child_id': c['id'], 'saved_case_id': c['saved_case_id'], 'generation_attempts': c['generation']['attempt']})
    write_json(ledger_path, record)
    case = session.get(base + f'test-cases/{c["saved_case_id"]}/', timeout=15).json()['data']
    assert case['test_case_type'] == 'scenario' and len(json.loads(case['script_content'])['teststeps']) == 2
    run_formal_case(session, base, c['saved_case_id'], args.output, 'live-c')


def main():
    load_dotenv(ROOT / 'backend' / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    stages = {'preflight': preflight, 'launch-a': launch_a, 'finish-a': finish_a,
              'replay-a': replay_a, 'launch-b': launch_b, 'launch-c': launch_c,
              'finish-b-negative': finish_b_negative, 'export-a': export_a, 'handoff-c': handoff_c, 'generate-c': generate_c,
              'finish-c': finish_c}
    parser.add_argument('--stage', choices=tuple(stages), default='preflight')
    parser.add_argument('--frontend', default='http://127.0.0.1:5173')
    parser.add_argument('--target-api', default='http://192.168.31.188:9999')
    parser.add_argument('--query-path', default='/resourceCategory/listAll')
    parser.add_argument('--target-username', required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT / 'backend' / 'logs'):
        parser.error('output 必须在被 git 忽略的 backend/logs 目录内')
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        stages[args.stage](args)
    except Exception as exc:
        write_json(args.output / f'{args.stage}-error.private.json',
                   {'exception': type(exc).__name__, 'traceback': traceback.format_exc()}, private=True)
        print(f'{args.stage} 未通过：{type(exc).__name__}；诊断已保存在本地私有日志。', flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
