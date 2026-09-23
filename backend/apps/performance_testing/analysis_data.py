"""Allowlisted, bounded evidence for passive performance-result analysis.

Never copy a plan, endpoint name/URL, exception message, or request/response
evidence into this document. Display names are resolved locally by the UI.
"""
from collections import Counter
from datetime import datetime
import math


MAX_ENDPOINTS = 50
MAX_TREND_POINTS = 60
MAX_NODES = 20
METRICS = ('requests', 'failures', 'rps', 'avg_response_time', 'p95', 'p99', 'users')
ERROR_TYPES = {
    'connect_timeout', 'read_timeout', 'dns_error', 'tls_error', 'connection_error',
    'undefined_variable', 'request_render_error', 'response_too_large',
    'extraction_missing', 'assertion_failed', 'type_mismatch', 'unsupported_comparator',
    'missing_value', 'invalid_json',
}
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
STATUSES = {'completed', 'failed', 'cancelled', 'incomplete'}
NODE_STATUSES = {'queued', 'preparing', 'ready', 'running', 'stopping', 'stopped', 'failed', 'lost'}


def number(value, maximum=None):
    if type(value) not in (int, float) or value < 0:
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return None
    if not finite:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _metrics(value):
    value = _dict(value)
    clean = {key: number(value.get(key)) for key in METRICS}
    for key in ('requests', 'failures', 'users'):
        if type(value.get(key)) is not int:
            clean[key] = None
    requests, failures = clean['requests'], clean['failures']
    clean['error_rate_percent'] = (
        failures / requests * 100
        if requests and failures is not None and failures <= requests else None
    )
    return clean


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def _series(samples):
    rows = []
    base = None
    for sample in _list(samples)[-400:]:
        sample = _dict(sample)
        point = _dict(sample.get('metrics'))
        timestamp = _timestamp(sample.get('timestamp'))
        if timestamp is not None and base is None:
            base = timestamp
        elapsed = number(point.get('elapsed_seconds'))
        if elapsed is None and timestamp is not None:
            elapsed = max(0, round(timestamp - base, 3))
        if elapsed is None:
            continue
        rows.append({'elapsed_seconds': elapsed, **_metrics(point)})
    return rows


def _downsample(rows):
    if len(rows) <= MAX_TREND_POINTS:
        return rows
    indices = {round(i * (len(rows) - 1) / (MAX_TREND_POINTS - 1)) for i in range(MAX_TREND_POINTS)}
    return [rows[i] for i in sorted(indices)]


def evaluate_targets(targets, metrics, complete):
    """Compute SLO comparisons independently of the model; never invent goals."""
    definitions = (
        ('p95_ms', 'p95', 'ms', lambda actual, target: actual <= target),
        ('error_rate_percent', 'error_rate_percent', '%', lambda actual, target: actual <= target),
        ('rps_min', 'rps', 'requests/s', lambda actual, target: actual >= target),
    )
    checks = []
    usable = complete and bool(metrics.get('requests'))
    for key, metric, unit, compare in definitions:
        if key not in targets:
            continue
        actual = metrics.get(metric)
        state = 'insufficient_data'
        if usable and actual is not None:
            state = 'met' if compare(actual, targets[key]) else 'not_met'
        checks.append({'key': key, 'target': targets[key], 'actual': actual, 'unit': unit, 'status': state})
    if not checks:
        status = 'not_configured'
    elif any(item['status'] == 'not_met' for item in checks):
        status = 'not_met'
    elif any(item['status'] == 'insufficient_data' for item in checks):
        status = 'insufficient_data'
    else:
        status = 'met'
    return {'status': status, 'checks': checks}


def build_analysis_input(run, targets):
    latest = _dict(run.latest_metrics)
    metrics = _metrics(latest)
    snapshot = _dict(run.snapshot)
    participants = list(run.participants.all())
    nodes_complete = bool(participants) and all(
        _dict(node.latest_metrics).get('complete') is True and node.status == 'stopped'
        for node in participants
    )
    complete = run.status == 'completed' and latest.get('complete') is True and nodes_complete
    limitations = [
        '当前仅有发压端数据，没有被测服务的 CPU、数据库、调用链等监控，无法直接确定服务端根因。',
        'RPS 是累计请求数除以运行时间；趋势中的 RPS 和响应分位数均为截至采样时的累计统计，不是瞬时值。',
        'P95/P99 是响应时间直方图估算值；请求数包含准备步骤，失败按请求计数，非 2xx 是否失败取决于配置的断言。',
        '节点 CPU/RSS 是 Worker 进程观测，不代表整台节点或被测服务；结束时当前用户数为 0 不代表未达到目标并发。',
    ]
    if not complete:
        limitations.append('本次运行未完整结束、节点报告缺失或完整性未确认，不能据此宣称达到性能目标。')
    if not metrics['requests']:
        limitations.append('没有有效的请求计数，无法评价本次压测的响应表现。')
    if not targets:
        limitations.append('未设置性能目标；只能解释实际表现，不能判断性能达标。')

    evidence = []

    def add(identifier, label, value, unit=''):
        evidence.append({'id': identifier, 'label': label, 'value': value, 'unit': unit})

    state = run.status if run.status in STATUSES else 'unknown'
    add('run.status', '运行状态与数据完整性', {'status': state, 'complete': complete})
    load = {key: number(snapshot.get(key)) for key in (
        'users', 'spawn_rate', 'duration_seconds', 'wait_seconds',
        'connect_timeout_seconds', 'read_timeout_seconds',
    )}
    add('load.config', '本次运行的负载与超时配置', load)
    labels = {
        'requests': ('请求数', '次'), 'failures': ('失败请求数', '次'),
        'rps': ('平均 RPS', 'requests/s'), 'avg_response_time': ('平均响应时间', 'ms'),
        'p95': ('P95 响应时间', 'ms'), 'p99': ('P99 响应时间', 'ms'),
        'error_rate_percent': ('错误率', '%'), 'users': ('最后上报的当前用户数', '人'),
    }
    for key, (label, unit) in labels.items():
        add(f'overall.{key}', label, metrics[key], unit)
    add('overall.elapsed_seconds', '实际统计时长', number(latest.get('elapsed_seconds')), '秒')

    entries = _list(latest.get('entries'))
    for index, entry in enumerate(entries[:MAX_ENDPOINTS], 1):
        entry = _dict(entry)
        method = entry.get('method')
        method = method if isinstance(method, str) and method in METHODS else 'UNKNOWN'
        add(f'endpoint.{index}', f'接口 {index}（{method}）累计指标', {'method': method, **_metrics(entry)})
    if len(entries) > MAX_ENDPOINTS:
        limitations.append(f'接口明细仅包含前 {MAX_ENDPOINTS} 项，全局统计仍包含全部接口。')
    if not entries:
        limitations.append('没有接口级统计，无法定位具体慢接口。')

    series = _series(run.metrics_samples)
    add('trend.overall', '整体累计指标趋势（elapsed_seconds 单位为秒）', _downsample(series))
    peaks = [row['users'] for row in series if row['users'] is not None]
    add('load.observed_peak_users', '采样中观测到的最高并发用户数', max(peaks) if peaks else None, '人')
    if len(series) < 2:
        limitations.append('有效趋势采样不足，不能判断随负载增长的变化。')
    if len(series) > MAX_TREND_POINTS:
        limitations.append(f'趋势按时间位置均匀保留至多 {MAX_TREND_POINTS} 个采样点，短时波动可能未展示。')

    for index, node in enumerate(participants[:MAX_NODES], 1):
        node_latest = _dict(node.latest_metrics)
        resources = [_dict(_dict(row).get('metrics')) for row in _list(node.metrics_samples)[-400:]]
        resources.append(node_latest)
        cpu = [number(row.get('worker_cpu')) for row in resources]
        memory = [number(row.get('worker_memory')) for row in resources]
        cpu = [v for v in cpu if v is not None]
        memory = [v for v in memory if v is not None]
        node_metrics = _metrics(node_latest)
        add(f'node.{index}', f'节点 {index} 的累计指标与资源采样', {
            **node_metrics, 'assigned_users': number(node.assigned_users),
            'status': node.status if node.status in NODE_STATUSES else 'unknown',
            'complete': node_latest.get('complete') is True,
            'peak_worker_cpu_percent': max(cpu) if cpu else None,
            'peak_worker_memory_mib': round(max(memory) / 1024 / 1024, 3) if memory else None,
        })
    if len(participants) > MAX_NODES:
        limitations.append(f'节点明细仅包含前 {MAX_NODES} 项。')
    if not participants:
        limitations.append('没有参与节点记录，不能进行节点对比。')

    counts = Counter()
    for sample in _list(latest.get('failure_samples'))[:20]:
        error_type = _dict(sample).get('error_type')
        counts[error_type if isinstance(error_type, str) and error_type in ERROR_TYPES else 'other'] += 1
    add('errors.samples', '去重失败样本的类型分布（不是失败请求次数）', dict(counts))
    if counts:
        limitations.append('失败样本经过去重和数量截断，样本类型分布不代表全部失败的频率。')

    return {
        'schema_version': 1,
        'targets': dict(targets),
        'assessment': evaluate_targets(targets, metrics, complete),
        'evidence': evidence,
        'limitations': limitations,
    }
