"""Deterministic run comparison; raw configuration is compared locally only."""
from collections import Counter
import json
import math

from .analysis_data import METHODS, STATUSES, _dict, _list, _metrics, number
from .constants import MAX_USERS, MAX_SPAWN_RATE, MAX_DURATION_SECONDS, MAX_CONNECT_TIMEOUT_SECONDS, MAX_READ_TIMEOUT_SECONDS
from .throughput import derive_throughput


MAX_ENDPOINTS = 50
METRIC_DEFINITIONS = (
    ('requests', '请求数', '次'), ('failures', '失败请求数', '次'),
    ('rps', '平均请求吞吐量', 'requests/s'),
    ('error_rate_percent', '错误率', '%'),
    ('avg_response_time', '平均响应时间', 'ms'),
    ('p95', 'P95 响应时间', 'ms'), ('p99', 'P99 响应时间', 'ms'),
    ('elapsed_seconds', '实际统计时长', '秒'),
    ('peak_interval_rps', '采样峰值区间吞吐量', 'requests/s'),
)
LOAD_DEFINITIONS = (
    ('users', '并发用户数'), ('spawn_rate', '每秒启动用户数'),
    ('duration_seconds', '计划持续时间（秒）'), ('wait_seconds', '轮次等待时间（秒）'),
    ('connect_timeout_seconds', '连接超时（秒）'), ('read_timeout_seconds', '读取超时（秒）'),
)
LOAD_BOUNDS = {
    'users': (1, MAX_USERS, True), 'spawn_rate': (0.000001, MAX_SPAWN_RATE, False),
    'duration_seconds': (1, MAX_DURATION_SECONDS, True), 'wait_seconds': (0.1, 60, False),
    'connect_timeout_seconds': (1, MAX_CONNECT_TIMEOUT_SECONDS, True),
    'read_timeout_seconds': (1, MAX_READ_TIMEOUT_SECONDS, True),
}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _finite(value):
    try:
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def metric_delta(key, label, unit, baseline, current):
    baseline, current = number(baseline), number(current)
    delta = _finite(current - baseline) if current is not None and baseline is not None else None
    percent = _finite(delta / baseline * 100) if delta is not None and baseline else None
    return {
        'key': key, 'label': label, 'unit': unit,
        'baseline': baseline, 'current': current, 'delta': delta, 'delta_percent': percent,
        'direction': ('unknown' if delta is None else 'increased' if delta > 0 else
                      'decreased' if delta < 0 else 'unchanged'),
    }


def _metric_values(run):
    latest = _dict(run.latest_metrics)
    throughput = derive_throughput(latest, run.metrics_samples)
    values = _metrics(latest)
    values.update(rps=throughput['average_rps'],
                  elapsed_seconds=number(latest.get('elapsed_seconds')),
                  peak_interval_rps=throughput['peak_interval_rps'])
    if not values['requests']:
        for key in ('avg_response_time', 'p95', 'p99'):
            values[key] = None
    return values, throughput


def _complete(run):
    participants = list(run.participants.all())
    latest = _dict(run.latest_metrics)
    return (run.status == 'completed' and latest.get('complete') is True and bool(participants)
            and all(p.status == 'stopped' and _dict(p.latest_metrics).get('complete') is True
                    for p in participants))


def _nodes(run, runtime=False):
    participants = list(run.participants.all())
    if not participants:
        return None
    rows = []
    for node in participants:
        identifier = getattr(node, 'node_id', None)
        users = number(node.assigned_users)
        if identifier is None or type(users) is not int or users <= 0:
            return None
        row = [str(identifier), users]
        if runtime:
            agent = getattr(node, 'node_agent_version', None)
            engine = getattr(node, 'node_engine_version', None)
            protocol = getattr(node, 'node_protocol_version', None)
            if not isinstance(agent, str) or not agent or not isinstance(engine, str) or not engine:
                return None
            if type(protocol) is not int or protocol <= 0:
                return None
            row.extend((agent, engine, protocol))
        rows.append(row)
    if len({row[0] for row in rows}) != len(rows):
        return None
    return sorted(rows)


def _conditions(current, baseline):
    current_snapshot, baseline_snapshot = _dict(current.snapshot), _dict(baseline.snapshot)
    result = []

    def add(key, label, old, new, display=False):
        old_key, new_key = _canonical(old), _canonical(new)
        equal = old_key == new_key if old is not None and new is not None and old_key is not None and new_key is not None else None
        result.append({
            'key': key, 'label': label, 'equal': equal,
            'baseline': old if display else ('已记录' if old is not None else '缺失'),
            'current': new if display else ('缺失' if new is None else '无法核对' if equal is None else '一致' if equal else '不同'),
        })

    for key, label in LOAD_DEFINITIONS:
        minimum, maximum, integer = LOAD_BOUNDS[key]
        def load_value(snapshot):
            value = number(snapshot.get(key), maximum)
            return value if value is not None and value >= minimum and (not integer or type(value) is int) else None
        old, new = load_value(baseline_snapshot), load_value(current_snapshot)
        add(key, label, old, new, True)

    def valid(snapshot, key, kind):
        value = snapshot.get(key)
        if not isinstance(value, kind):
            return None
        if key in ('steps', 'base_url', 'engine_version') and not value:
            return None
        if key == 'steps' and (len(value) > 20 or any(
            not isinstance(step, dict)
            or not isinstance(step.get('method'), str) or step['method'] not in METHODS
            or step.get('phase') not in ('setup', 'main')
            or not isinstance(step.get('path'), str) or not step['path']
            or not isinstance(step.get('assertions'), list)
            or not isinstance(step.get('extract'), list)
            for step in value
        )):
            return None
        return value if _canonical(value) is not None else None

    add('plan', '所属压测计划', getattr(baseline, 'plan_id', None), getattr(current, 'plan_id', None))
    for key, label, kind in (
        ('base_url', '被测目标地址', str), ('steps', '步骤、断言与提取配置', list),
        ('variables', '固定变量配置', dict), ('unique_variables', '唯一变量配置', list),
        ('allowed_methods', '目标允许的方法', list), ('engine_version', '脚本执行引擎版本', str),
    ):
        add(key, label, valid(baseline_snapshot, key, kind), valid(current_snapshot, key, kind))
    add('nodes', '节点及用户分配', _nodes(baseline), _nodes(current))
    add('node_runtime', '节点运行时版本', _nodes(baseline, True), _nodes(current, True))
    return result


def run_metadata(run):
    snapshot_name = _dict(run.snapshot).get('plan_name')
    name = snapshot_name if isinstance(snapshot_name, str) else getattr(getattr(run, 'plan', None), 'name', '')
    created = getattr(run, 'created_at', None)
    return {'id': str(run.pk), 'plan_name': name[:200] if isinstance(name, str) else '',
            'created_at': created.isoformat() if created is not None else None,
            'status': run.status if isinstance(run.status, str) and run.status in STATUSES else 'unknown'}


def _endpoint_rows(current, baseline, current_throughput, baseline_throughput):
    old_entries = _list(_dict(baseline.latest_metrics).get('entries'))[:MAX_ENDPOINTS]
    new_entries = _list(_dict(current.latest_metrics).get('entries'))[:MAX_ENDPOINTS]

    def key(entry):
        entry = _dict(entry)
        method, name = entry.get('method'), entry.get('name')
        if isinstance(method, str) and method in METHODS and isinstance(name, str) and name:
            return method, name
        return None

    old_counts, new_counts = Counter(map(key, old_entries)), Counter(map(key, new_entries))
    unique_old = {key(entry): i for i, entry in enumerate(old_entries) if key(entry) is not None and old_counts[key(entry)] == 1}
    used, result = set(), []

    def add(old_index, new_index, match):
        old = _dict(old_entries[old_index]) if old_index is not None else {}
        new = _dict(new_entries[new_index]) if new_index is not None else {}
        old_values, new_values = _metrics(old), _metrics(new)
        old_values['rps'] = baseline_throughput['endpoint_rps'][old_index] if old_index is not None else None
        new_values['rps'] = current_throughput['endpoint_rps'][new_index] if new_index is not None else None
        for values in (old_values, new_values):
            if not values['requests']:
                for field in ('avg_response_time', 'p95', 'p99'):
                    values[field] = None
        entry = new if new_index is not None else old
        name, method = entry.get('name'), entry.get('method')
        result.append({
            'id': f'endpoint.{len(result) + 1}',
            'name': name[:200] if isinstance(name, str) else '未命名接口',
            'method': method if isinstance(method, str) and method in METHODS else 'UNKNOWN',
            'match_status': match,
            'baseline_index': old_index + 1 if old_index is not None else None,
            'current_index': new_index + 1 if new_index is not None else None,
            'metrics': [metric_delta(k, label, unit, old_values[k], new_values[k])
                        for k, label, unit in METRIC_DEFINITIONS[:7]],
        })

    for index, entry in enumerate(new_entries):
        identity = key(entry)
        ambiguous = identity is None or new_counts[identity] > 1 or old_counts[identity] > 1
        old_index = unique_old.get(identity) if not ambiguous else None
        if old_index is not None:
            used.add(old_index)
        add(old_index, index, 'ambiguous' if ambiguous else 'matched' if old_index is not None else 'current_only')
    for index, entry in enumerate(old_entries):
        if index not in used:
            identity = key(entry)
            ambiguous = identity is None or old_counts[identity] > 1 or new_counts[identity] > 1
            add(index, None, 'ambiguous' if ambiguous else 'baseline_only')
    return result


def build_comparison_data(current, baseline):
    conditions = _conditions(current, baseline)
    current_values, current_throughput = _metric_values(current)
    baseline_values, baseline_throughput = _metric_values(baseline)
    missing = [row['label'] for row in conditions if row['equal'] is None]
    changed = [row['label'] for row in conditions if row['equal'] is False]
    complete = _complete(current) and _complete(baseline)
    counts_valid = all(values['requests'] and values['failures'] is not None
                       and values['failures'] <= values['requests']
                       for values in (current_values, baseline_values))
    reasons = []
    if changed:
        reasons.append('以下条件不同：' + '、'.join(changed) + '。')
    if missing:
        reasons.append('以下条件缺失或无法核对：' + '、'.join(missing) + '。')
    if not complete:
        reasons.append('至少一次运行未完整结束，或缺少完整的节点报告。')
    if not counts_valid:
        reasons.append('至少一次运行缺少有效请求/失败计数，不能据此评价整体性能变化。')
    state = 'insufficient_data' if missing or not complete or not counts_valid else 'conditions_changed' if changed else 'comparable'
    if state == 'comparable':
        reasons.append('已记录的负载、目标、脚本、变量及节点条件一致；仍需确认被测服务和网络环境是否相同。')
    limitations = [
        '差值为本次减基准；变化率以基准为分母，基准为 0 时不计算变化率。错误率差值的单位为百分点。',
        '接口按请求方法和统计名称精确匹配，名称重复或无效时不强行配对；脚本变化可能导致接口无法匹配。',
        '仅有发压端观测，没有被测服务资源、数据库或调用链证据，不能直接确定变化的服务端根因。',
        '请求吞吐量不等于业务 TPS 或系统最大容量；P95/P99 为直方图估算，峰值为保留采样中的区间平均峰值。',
    ]
    if any(len(_list(_dict(run.latest_metrics).get('entries'))) > MAX_ENDPOINTS for run in (current, baseline)):
        limitations.append(f'两次运行各取前 {MAX_ENDPOINTS} 个接口参与匹配，总体指标仍包含全部接口。')
    endpoints = _endpoint_rows(current, baseline, current_throughput, baseline_throughput)
    if not endpoints:
        limitations.append('两次运行均缺少接口级统计。')
    return {
        'baseline': run_metadata(baseline), 'current': run_metadata(current),
        'comparability': {'status': state, 'reasons': reasons, 'conditions': conditions},
        'overall': [metric_delta(key, label, unit, baseline_values[key], current_values[key])
                    for key, label, unit in METRIC_DEFINITIONS],
        'endpoints': endpoints, 'limitations': limitations,
    }


def build_comparison_input(current, baseline):
    """No user-authored strings, identifiers or raw configuration cross this boundary."""
    comparison = build_comparison_data(current, baseline)
    evidence = [{'id': 'comparison.conditions', 'label': '两次运行的可比性检查',
                 'value': comparison['comparability'], 'unit': ''}]
    for row in comparison['overall']:
        evidence.append({'id': f'comparison.overall.{row["key"]}', 'label': row['label'],
                         'value': row, 'unit': row['unit']})
    for index, endpoint in enumerate(comparison['endpoints'], 1):
        evidence.append({
            'id': f'comparison.endpoint.{index}', 'label': f'对比接口 {index}', 'unit': '',
            'value': {key: endpoint[key] for key in ('method', 'match_status', 'baseline_index', 'current_index', 'metrics')},
        })
    return {'schema_version': 1, 'analysis_type': 'load_comparison', 'targets': {},
            'assessment': comparison['comparability'], 'evidence': evidence,
            'limitations': comparison['limitations']}
