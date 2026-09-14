#!/usr/bin/env python3
"""Explicit opt-in live delivery acceptance for the reviewed API LIVE-A/B cases.

This helper deliberately has no dotenv loading and no default invocation.  It
uses the saved platform session created by verify_api_live_acceptance.py and
keeps raw platform reports in 0600 private artifacts only.
"""
import argparse
import json
import os
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = 2
ENVIRONMENT_ID = 1
API_BASE = f'http://127.0.0.1:8000/api/v1/projects/{PROJECT_ID}/api-testing/'
SCHEDULE_BASE = f'http://127.0.0.1:8000/api/v1/projects/{PROJECT_ID}/scheduled-tasks/'
TERMINAL_EXECUTION_STATUSES = {'passed', 'failed', 'error', 'stopped'}
TERMINAL_LOG_STATUSES = {'success', 'failed', 'cancelled'}


def write_json(path, data, private=False):
    """Write an artifact without ever loosening permissions on private reports."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    if private:
        # Existing files retain their old mode after open(2); fix it before
        # truncation/write so a previous permissive artifact cannot be exposed.
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def read_ledger(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'账本不可读: {path.name}') from exc
    if not isinstance(data, dict):
        raise RuntimeError(f'账本格式无效: {path.name}')
    return data


def platform_session(args):
    """Reuse the preflight session artifact; do not read .env or print its token."""
    auth_path = args.output / 'platform-auth.private.json'
    try:
        auth = json.loads(auth_path.read_text(encoding='utf-8'))
        access = auth['access']
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError('缺少或无法读取平台私有会话；请先完成已审阅的 preflight。') from exc
    session = requests.Session()
    session.headers['Authorization'] = f'Bearer {access}'
    return session


def request_data(session, method, url, *, payload=None):
    """Return only success data; error paths intentionally omit server bodies."""
    try:
        response = session.request(method, url, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError(f'{method} {url} 请求失败 ({type(exc).__name__})') from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f'{method} {url} HTTP={response.status_code}')
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError(f'{method} {url} 返回非 JSON') from None
    if isinstance(body, dict) and url.startswith(SCHEDULE_BASE) and 'success' not in body:
        # Generic DRF schedule CRUD/detail views return serializers directly.
        return body
    if not isinstance(body, dict) or body.get('success') is not True:
        raise RuntimeError(f'{method} {url} 响应结构无效')
    # Mutations such as add-test-cases acknowledge success without a data body.
    return body.get('data')


def emit(status, ident, count):
    # Public output is deliberately a fixed whitelist.
    print(json.dumps({'status': status, 'id': ident, 'count': count}, ensure_ascii=False), flush=True)


def reviewed_case_ids(args):
    live_a = read_ledger(args.output / 'live-a.json')
    live_b = read_ledger(args.output / 'live-b.json')
    positive, negative = live_a.get('saved_case_id'), live_b.get('negative_case_id')
    if type(positive) is not int or type(negative) is not int or positive <= 0 or negative <= 0:
        raise RuntimeError('LIVE-A/LIVE-B 已审阅账本未包含有效用例 ID。')
    if positive == negative:
        raise RuntimeError('LIVE-A 与 LIVE-B 反向用例 ID 不能相同。')
    return positive, negative


def suite_ledger_path(args):
    return args.output / 'live-delivery-suite.json'


def schedule_ledger_path(args):
    return args.output / 'live-delivery-schedule.json'


def begin_pending_response(ledger, path, action):
    """Persist ambiguity before a non-idempotent create/run POST is sent."""
    ensure_no_pending_response(ledger)
    ledger['pending_response'] = action
    write_json(path, ledger)


def complete_pending_response(ledger, path, **values):
    ledger.update(values)
    ledger.pop('pending_response', None)
    write_json(path, ledger)


def ensure_no_pending_response(ledger):
    pending = ledger.get('pending_response')
    if pending:
        raise RuntimeError(f'账本保留了未确认响应 ({pending})；禁止自动重复创建或运行，请人工按证据核对。')


def verify_suite(data, positive, negative):
    if data.get('variables') != {'acceptance_layer': 'suite'}:
        raise RuntimeError('本次 ledger suite 的变量不符合验收约束。')
    if data.get('test_case_order') != [positive, negative]:
        raise RuntimeError('本次 ledger suite 的用例顺序不符合 A -> negative。')
    cases = data.get('test_cases') or []
    if {item.get('id') for item in cases} != {positive, negative} or len(cases) != 2:
        raise RuntimeError('本次 ledger suite 未恰好关联 LIVE-A 与 LIVE-B 反向用例。')


def ensure_suite(args, session, ledger, positive, negative):
    """Create/configure exactly the suite recorded in this output's ledger."""
    if ledger.get('suite_id') is None:
        begin_pending_response(ledger, suite_ledger_path(args), 'create_suite')
        created = request_data(session, 'POST', API_BASE + 'test-suites/', payload={
            'name': '验收LIVE-DELIVERY-A-NEGATIVE',
            'description': '仅用于已审阅 LIVE-A 正向与 LIVE-B 未认证反向用例的交付链路验收。',
            'status': 'active',
            'variables': {'acceptance_layer': 'suite'},
        })
        suite_id = created.get('id')
        if type(suite_id) is not int or suite_id <= 0:
            raise RuntimeError('创建 suite 未返回有效 ID；禁止自动重复创建。')
        complete_pending_response(
            ledger, suite_ledger_path(args), suite_id=suite_id,
            positive_case_id=positive, negative_case_id=negative,
        )

    suite_id = ledger.get('suite_id')
    if type(suite_id) is not int or suite_id <= 0:
        raise RuntimeError('suite ledger 缺少有效 suite_id。')
    current = request_data(session, 'GET', API_BASE + f'test-suites/{suite_id}/')
    if not current.get('test_cases'):
        request_data(session, 'POST', API_BASE + f'test-suites/{suite_id}/add-test-cases/', payload={
            'test_case_ids': [positive, negative],
        })
    current = request_data(session, 'GET', API_BASE + f'test-suites/{suite_id}/')
    if current.get('test_case_order') != [positive, negative] or current.get('variables') != {'acceptance_layer': 'suite'}:
        current = request_data(session, 'PATCH', API_BASE + f'test-suites/{suite_id}/', payload={
            'test_case_order': [positive, negative],
            'variables': {'acceptance_layer': 'suite'},
        })
    verify_suite(current, positive, negative)
    ledger['suite_ready'] = True
    write_json(suite_ledger_path(args), ledger)
    return suite_id


def assert_two_case_report(report, positive, negative):
    cases = report.get('case_executions') or []
    if len(cases) != 2 or [item.get('test_case') for item in cases] != [positive, negative]:
        raise RuntimeError('真实报告未记录 A -> negative 的两个用例顺序。')
    if any(item.get('status') != 'passed' for item in cases):
        raise RuntimeError('真实报告含未通过用例。')
    return len(cases)


def poll_api_report(session, execution_id, output, filename, positive, negative):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        report = request_data(session, 'GET', API_BASE + f'executions/{execution_id}/report/')
        status = report.get('status')
        if status in TERMINAL_EXECUTION_STATUSES:
            write_json(output / filename, report, private=True)
            count = assert_two_case_report(report, positive, negative)
            if status != 'passed':
                raise RuntimeError('真实 API 执行未通过；已保留私有报告，禁止自动重放。')
            return status, count
        time.sleep(2)
    raise RuntimeError('API 执行轮询超时；已保留派发 ID，禁止自动重放。')


def suite(args):
    positive, negative = reviewed_case_ids(args)
    ledger_path = suite_ledger_path(args)
    ledger = read_ledger(ledger_path) if ledger_path.exists() else {}
    ensure_no_pending_response(ledger)
    session = platform_session(args)
    suite_id = ensure_suite(args, session, ledger, positive, negative)

    execution_id = ledger.get('execution_id')
    if execution_id is None:
        begin_pending_response(ledger, ledger_path, 'run_suite')
        dispatched = request_data(session, 'POST', API_BASE + f'test-suites/{suite_id}/execute/', payload={
            'environment_id': ENVIRONMENT_ID,
            'variables': {'acceptance_layer': 'run'},
        })
        execution_id = dispatched.get('execution_id')
        if type(execution_id) is not int or execution_id <= 0:
            raise RuntimeError('suite 派发未返回有效 execution_id；禁止自动重试运行。')
        complete_pending_response(
            ledger, ledger_path, execution_id=execution_id, dispatch_task_id=dispatched.get('task_id'),
        )

    if type(execution_id) is not int or execution_id <= 0:
        raise RuntimeError('suite ledger 缺少有效 execution_id。')
    status, count = poll_api_report(session, execution_id, args.output, 'live-delivery-suite-report.private.json', positive, negative)
    ledger.update({'status': status, 'case_count': count})
    write_json(ledger_path, ledger)
    emit(status, execution_id, count)


def verify_schedule(data, suite_id):
    if data.get('status') != 'paused' or data.get('environment') != ENVIRONMENT_ID:
        raise RuntimeError('本次计划不是暂停的 API 环境 1 计划。')
    if data.get('suite_ids') != [suite_id]:
        raise RuntimeError('本次计划没有只关联本次 ledger suite。')
    if data.get('notice_targets'):
        raise RuntimeError('本次计划不允许配置收件组。')


def schedule_manual(args):
    positive, negative = reviewed_case_ids(args)
    suite_record = read_ledger(suite_ledger_path(args))
    suite_id = suite_record.get('suite_id')
    if type(suite_id) is not int or suite_id <= 0 or not suite_record.get('suite_ready'):
        raise RuntimeError('请先成功完成 suite 阶段。')
    ledger_path = schedule_ledger_path(args)
    ledger = read_ledger(ledger_path) if ledger_path.exists() else {}
    ensure_no_pending_response(ledger)
    session = platform_session(args)

    if ledger.get('task_id') is None:
        begin_pending_response(ledger, ledger_path, 'create_schedule')
        task_name = f'验收LIVE-DELIVERY-手动计划-{args.output.name}'
        task = request_data(session, 'POST', SCHEDULE_BASE + 'tasks/', payload={
            'name': task_name,
            'description': '仅用于本次交付验收；保持暂停，禁止自动 Beat 触发。',
            'suite_ids': [suite_id],
            'cron_expression': '0 0 1 1 *',
            'environment': ENVIRONMENT_ID,
            'status': 'paused',
            'trigger_condition': 'always',
        })
        task_id = task.get('id')
        if task_id is None:
            # CreateSerializer intentionally omits id; read back the exact new
            # unique name instead of guessing the latest task or creating again.
            tasks = request_data(session, 'GET', SCHEDULE_BASE + 'tasks/?page_size=100')
            matches = [row for row in tasks.get('results', []) if row.get('name') == task_name]
            if len(matches) == 1:
                verify_schedule(matches[0], suite_id)
                task_id = matches[0]['id']
        if type(task_id) is not int or task_id <= 0:
            raise RuntimeError('创建计划未返回有效 task_id；禁止自动重复创建。')
        complete_pending_response(ledger, ledger_path, task_id=task_id, suite_id=suite_id)

    task_id = ledger.get('task_id')
    if type(task_id) is not int or task_id <= 0:
        raise RuntimeError('计划 ledger 缺少有效 task_id。')
    task = request_data(session, 'GET', SCHEDULE_BASE + f'tasks/{task_id}/')
    verify_schedule(task, suite_id)

    execution_log_id = ledger.get('execution_log_id')
    if execution_log_id is None:
        begin_pending_response(ledger, ledger_path, 'run_schedule_manual')
        dispatched = request_data(session, 'POST', SCHEDULE_BASE + f'tasks/{task_id}/run/')
        execution_log_id = dispatched.get('execution_id')
        if type(execution_log_id) is not int or execution_log_id <= 0:
            raise RuntimeError('手动计划派发未返回有效 execution_id；禁止自动重试运行。')
        complete_pending_response(ledger, ledger_path, execution_log_id=execution_log_id)

    if type(execution_log_id) is not int or execution_log_id <= 0:
        raise RuntimeError('计划 ledger 缺少有效 execution_log_id。')
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        log = request_data(session, 'GET', SCHEDULE_BASE + f'execution-logs/{execution_log_id}/')
        if log.get('status') in TERMINAL_LOG_STATUSES:
            write_json(args.output / 'live-delivery-schedule-log.private.json', log, private=True)
            links = log.get('linked_executions') or []
            if log.get('status') != 'success' or len(links) != 1:
                raise RuntimeError('计划执行未成功完成预期的一项 suite；已保留私有日志。')
            link = links[0]
            if link.get('kind') != 'api' or link.get('suite_id') != suite_id:
                raise RuntimeError('计划执行关联了非本次 ledger suite。')
            execution_id = link.get('execution_id')
            if type(execution_id) is not int or execution_id <= 0:
                raise RuntimeError('计划执行关联记录缺少有效 API execution_id。')
            status, count = poll_api_report(
                session, execution_id, args.output, 'live-delivery-schedule-api-report.private.json', positive, negative,
            )
            ledger.update({'status': log['status'], 'api_execution_id': execution_id, 'case_count': count})
            write_json(ledger_path, ledger)
            emit(status, execution_log_id, count)
            return
        time.sleep(2)
    raise RuntimeError('计划执行轮询超时；已保留派发 ID，禁止自动重放。')


def finalize(args):
    ledger_path = schedule_ledger_path(args)
    ledger = read_ledger(ledger_path)
    task_id = ledger.get('task_id')
    if type(task_id) is not int or task_id <= 0:
        raise RuntimeError('计划 ledger 缺少有效 task_id。')
    session = platform_session(args)
    task = request_data(session, 'PATCH', SCHEDULE_BASE + f'tasks/{task_id}/status/', payload={'status': 'paused'})
    if task.get('status') != 'paused':
        raise RuntimeError('计划暂停后状态读回不为 paused。')
    task = request_data(session, 'GET', SCHEDULE_BASE + f'tasks/{task_id}/')
    if task.get('status') != 'paused':
        raise RuntimeError('计划暂停状态读回不为 paused。')
    ledger['finalized_status'] = task['status']
    write_json(ledger_path, ledger)
    emit(task['status'], task_id, len(task.get('suite_ids') or []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('suite', 'schedule-manual', 'finalize'), required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    logs_root = ROOT / 'backend' / 'logs'
    if not args.output.is_relative_to(logs_root):
        parser.error('output 必须在被 git 忽略的 backend/logs 目录内')
    args.output.mkdir(parents=True, exist_ok=True)
    {'suite': suite, 'schedule-manual': schedule_manual, 'finalize': finalize}[args.stage](args)


if __name__ == '__main__':
    main()
