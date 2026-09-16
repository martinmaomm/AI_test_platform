"""Platform-owned request template, imported safely; Locust runs only in main()."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from urllib.parse import urlsplit


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def validate_snapshot(value):
    fields = {'schema_version', 'run_id', 'node_id', 'engine_version', 'plan_name', 'base_url',
              'allowed_methods', 'users', 'spawn_rate', 'duration_seconds', 'wait_seconds', 'steps'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('运行快照字段无效')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['engine_version'] != '2.43.3':
        raise ValueError('运行快照版本不匹配')
    for key in ('run_id', 'node_id'):
        if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
            raise ValueError('运行身份无效')
    if not isinstance(value['plan_name'], str) or not 1 <= len(value['plan_name']) <= 200:
        raise ValueError('计划名称无效')
    for key, low, high, integer in [('users', 1, 100, True), ('spawn_rate', .000001, 100, False),
                                    ('duration_seconds', 1, 600, True), ('wait_seconds', .1, 60, False)]:
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high or (integer and type(number) is not int):
            raise ValueError('运行负载参数无效')
    origin = value['base_url']
    if not isinstance(origin, str) or any(c.isspace() or c in '\\?#' or ord(c) < 32 for c in origin):
        raise ValueError('压测目标无效')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in ('', '/') or parsed.port == 0:
        raise ValueError('压测目标必须是 HTTP(S) origin')
    methods = value['allowed_methods']
    if not isinstance(methods, list) or not methods or any(m not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') for m in methods):
        raise ValueError('目标批准方法无效')
    if not isinstance(value['steps'], list) or not 1 <= len(value['steps']) <= 20:
        raise ValueError('请求步骤数量无效')
    forbidden_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'proxy-authorization', 'proxy-connection'}
    for step in value['steps']:
        if not isinstance(step, dict) or set(step) != {'name', 'method', 'path', 'expected_status', 'headers', 'body'}:
            raise ValueError('请求步骤字段无效')
        if not isinstance(step['name'], str) or not step['name'].strip() or len(step['name']) > 200 or step['method'] not in methods:
            raise ValueError('请求步骤名称或方法无效')
        path = step['path']
        if not isinstance(path, str) or len(path) > 2048 or not path.startswith('/') or path.startswith('//') or '\\' in path or '#' in path or re.search(r'[\x00-\x20\x7f]', path):
            raise ValueError('请求路径无效')
        if type(step['expected_status']) is not int or not 100 <= step['expected_status'] <= 599:
            raise ValueError('预期状态码无效')
        headers = step['headers']
        if not isinstance(headers, dict) or len(headers) > 50:
            raise ValueError('请求头无效')
        for key, item in headers.items():
            if not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key) or key.lower() in forbidden_headers or not isinstance(item, str) or len(item) > 4096 or re.search(r'[\x00-\x1f\x7f]', item):
                raise ValueError('请求头不允许覆盖目标或传输协议')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 65536:
        raise ValueError('运行快照过大')
    return value


def main():
    # Never import gevent/Locust in the Agent, controller, Django or Celery process.
    from gevent import monkey
    monkey.patch_all()
    import argparse
    from importlib.metadata import version
    import logging
    import os
    from pathlib import Path
    import signal
    import tempfile
    import time
    import gevent
    from locust import HttpUser, task, constant
    from locust.env import Environment
    from locust.event import Events
    from requests.exceptions import RequestException

    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('master', 'worker'), required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--master-host', default='127.0.0.1')
    parser.add_argument('--master-port', type=int, required=True)
    parser.add_argument('--start-file')
    parser.add_argument('--metrics-file')
    parser.add_argument('--complete-file')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if version('locust') != '2.43.3' or args.master_host != '127.0.0.1':
        raise ValueError('引擎版本或本地转接地址无效')
    configuration = json.loads(Path(args.config).read_text(encoding='utf-8'))
    snapshot = validate_snapshot(configuration['snapshot'])
    hello = {'run_id': snapshot['run_id'], 'node_id': snapshot['node_id'],
             'snapshot_sha256': canonical_sha256(snapshot), 'token': configuration['handshake_token']}
    if not isinstance(hello['token'], str) or len(hello['token']) < 32:
        raise ValueError('运行握手凭证无效')

    class RequestUser(HttpUser):
        host = snapshot['base_url'].rstrip('/')
        wait_time = constant(snapshot['wait_seconds'])

        def on_start(self):
            self.client.trust_env = False

        @task
        def scenario(self):
            for index, step in enumerate(snapshot['steps']):
                started = time.perf_counter()
                with self.client.request(step['method'], self.host + step['path'],
                                         name=f'{index + 1}. {step["name"]}', headers=step['headers'],
                                         json=step['body'], timeout=(3, 5), stream=True,
                                         allow_redirects=False, catch_response=True) as result:
                    size = 0
                    try:
                        if result.status_code:
                            for chunk in result.iter_content(65536):
                                size += len(chunk)
                                if size > 1024 * 1024:
                                    result.failure('响应内容超过 1 MiB 限制')
                                    break
                        if result.status_code != step['expected_status']:
                            result.failure(f'状态码不符合预期（期望 {step["expected_status"]}，实际 {result.status_code}）')
                    except RequestException:
                        result.failure('读取响应失败或超时')
                    finally:
                        result.request_meta['response_time'] = (time.perf_counter() - started) * 1000
                        result.request_meta['response_length'] = size
                        result.close()

    environment = Environment(user_classes=[RequestUser], host=RequestUser.host, events=Events(), stop_timeout=3)
    stop = [False]
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
    if args.role == 'worker':
        runner = environment.create_worker_runner(args.master_host, args.master_port)
        # A post-stop report contains all final counters, not an average of percentiles.
        environment.events.report_to_master.add_listener(
            lambda client_id, data, **_: data.update(platform_final=runner.state == 'stopped')
        )
        environment.events.test_stop.add_listener(lambda **_: runner._send_stats())
        runner.send_message('platform_hello', hello)
        while not stop[0] and len(runner.greenlet):
            gevent.sleep(.2)
        runner.quit()
        return 0

    if not all((args.start_file, args.metrics_file, args.complete_file)):
        raise ValueError('Master 缺少运行文件配置')
    runner = environment.create_master_runner('127.0.0.1', args.master_port)
    admitted = set()
    final_reports = set()
    invalid = [False]
    started_at = None
    reason = 'completed'

    def on_hello(environment, msg, **_):
        if msg.data != hello or (admitted and msg.node_id not in admitted):
            invalid[0] = True
        else:
            admitted.add(msg.node_id)

    def on_report(client_id, data, **_):
        if data.get('platform_final') and client_id in admitted and started_at is not None:
            final_reports.add(client_id)

    runner.register_message('platform_hello', on_hello)
    environment.events.worker_report.add_listener(on_report)

    def write_json(filename, value):
        path = Path(filename)
        descriptor, temporary = tempfile.mkstemp(prefix='.metric-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(value, output, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def entry_data(entry):
        return {'name': entry.name, 'method': entry.method, 'requests': entry.num_requests,
                'failures': entry.num_failures, 'avg_response_time': entry.avg_response_time,
                'p95': entry.get_response_time_percentile(.95) or 0,
                'p99': entry.get_response_time_percentile(.99) or 0}

    def metrics(complete=False):
        total = environment.stats.total
        elapsed = max(.001, time.monotonic() - started_at) if started_at is not None else 0
        return {**entry_data(total), 'rps': total.num_requests / elapsed if elapsed else 0,
                'error_rate': total.num_failures / total.num_requests if total.num_requests else 0,
                'users': runner.user_count, 'elapsed_seconds': elapsed,
                'worker_count': runner.worker_count, 'admitted_workers': len(admitted),
                'started': started_at is not None, 'complete': complete,
                'entries': [entry_data(item) for item in environment.stats.entries.values()]}

    deadline = time.monotonic() + 60
    stop_file = Path(args.start_file + '.stop')
    last_write = 0
    try:
        while True:
            now = time.monotonic()
            if invalid[0] or runner.worker_count > 1:
                reason = 'worker_identity_invalid'
                break
            if stop[0] or stop_file.exists():
                reason = 'cancelled'
                break
            if started_at is None:
                if now > deadline:
                    reason = 'prepare_timeout'
                    break
                if len(admitted) == 1 and runner.worker_count == 1 and Path(args.start_file).exists():
                    started_at = time.monotonic()
                    # start waits for ramp-up; use a greenlet so stop/deadline remain responsive.
                    gevent.spawn(runner.start, snapshot['users'], snapshot['spawn_rate'])
            elif now - started_at >= snapshot['duration_seconds']:
                break
            elif runner.worker_count != 1:
                reason = 'worker_lost'
                break
            if now - last_write >= 2:
                write_json(args.metrics_file, metrics())
                last_write = now
            gevent.sleep(.1)
        with gevent.Timeout(8, False):
            runner.stop()
            limit = time.monotonic() + 5
            while started_at is not None and final_reports != admitted and time.monotonic() < limit:
                gevent.sleep(.1)
        complete = bool(started_at is not None and len(admitted) == 1 and final_reports == admitted
                        and reason in ('completed', 'cancelled') and runner.user_count == 0)
        runner.quit()
        result = metrics(complete)
        write_json(args.metrics_file, result)
        write_json(args.complete_file, {'reason': reason, 'metrics': result, 'complete': complete})
        return 0 if complete or (started_at is None and reason == 'cancelled') else 2
    finally:
        if len(runner.greenlet):
            with gevent.Timeout(5, False):
                runner.quit()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError):
        # Never print private configuration/handshake payloads in a traceback.
        raise SystemExit('运行配置无效，执行已停止')
