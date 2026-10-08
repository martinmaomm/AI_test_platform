"""Optional plan targets and deterministic, platform-only run acceptance."""
import math

TARGETS = {'p95_ms': ('P95 响应时间', 'ms'),
           'error_rate_percent': ('错误率', '%'),
           'rps_min': ('平均请求吞吐量', '次/秒')}


def validate_targets(value):
    if type(value) is not dict or set(value) - TARGETS.keys():
        raise ValueError('验收标准必须是包含 P95、错误率或吞吐量目标的对象。')
    for key, target in value.items():
        try:
            valid = type(target) in (int, float) and math.isfinite(target)
        except (OverflowError, ValueError):
            valid = False
        if valid:
            valid = 0 <= target <= 100 if key == 'error_rate_percent' else target > 0
        if not valid:
            raise ValueError('P95 和吞吐量目标须大于 0，错误率须在 0–100 之间，且必须是有限数值。')
    return dict(value)


def run_acceptance(run):
    from .analysis_data import _dict, _metrics, evaluate_targets
    from .throughput import derive_throughput
    if run.mode != 'load':
        return {'status': 'not_applicable', 'checks': []}
    try:
        targets = validate_targets(getattr(run, 'acceptance_targets', {}))
    except ValueError:
        return {'status': 'insufficient_data', 'checks': []}
    if not targets:
        return {'status': 'not_configured', 'checks': []}
    latest = _dict(run.latest_metrics)
    metrics = _metrics(latest)
    if 'rps_min' in targets:
        metrics['rps'] = derive_throughput(latest, run.metrics_samples)['average_rps']
    nodes = list(run.participants.all())
    complete = (run.status == 'completed' and latest.get('complete') is True and bool(nodes)
                and all(node.status == 'stopped' and _dict(node.latest_metrics).get('complete') is True for node in nodes))
    result = evaluate_targets(targets, metrics, complete)
    if targets and run.status in ('queued', 'preparing', 'running', 'stopping'):
        result['status'] = 'pending'
        for check in result['checks']:
            check['status'] = 'pending'
    return result
