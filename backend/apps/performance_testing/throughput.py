"""Pure, defensive throughput derivation from persisted cumulative metrics."""
from datetime import datetime
import math


MAX_SAMPLES = 400


def _number(value):
    if type(value) not in (int, float) or value < 0:
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, TypeError, ValueError):
        return None


def _count(value):
    if type(value) is not int or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, TypeError, ValueError):
        return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def _elapsed(metrics):
    explicit = _number(metrics.get('elapsed_seconds'))
    if explicit is not None:
        return explicit
    requests = _count(metrics.get('requests'))
    rps = _number(metrics.get('rps'))
    if requests is None or requests == 0 or rps is None or rps <= 0:
        return None
    try:
        value = requests / rps
    except (OverflowError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _point(sample):
    sample = _dict(sample)
    metrics = _dict(sample.get('metrics'))
    timestamp = sample.get('timestamp') if isinstance(sample.get('timestamp'), str) else None
    elapsed = _elapsed(metrics)
    report_time = _timestamp(metrics.get('report_time'))
    sample_time = _timestamp(timestamp)
    if elapsed is not None:
        clock_kind, clock_value = 'elapsed', elapsed
    elif report_time is not None:
        clock_kind, clock_value = 'report_time', report_time
    elif sample_time is not None:
        clock_kind, clock_value = 'sample_time', sample_time
    else:
        clock_kind = clock_value = None
    return {
        'timestamp': timestamp,
        'requests': _count(metrics.get('requests')),
        'clock_kind': clock_kind,
        'clock_value': clock_value,
    }


def _interval(previous, current):
    if previous['requests'] is None or current['requests'] is None:
        return None, None, 'invalid_point'
    if previous['clock_kind'] is None or current['clock_kind'] is None:
        return None, None, 'invalid_point'
    if previous['clock_kind'] != current['clock_kind']:
        return None, None, 'clock_changed'

    seconds = current['clock_value'] - previous['clock_value']
    request_delta = current['requests'] - previous['requests']
    if seconds == 0 and request_delta == 0:
        return None, None, 'duplicate'
    if not math.isfinite(seconds) or seconds <= 0 or request_delta < 0:
        return None, None, 'broken'
    try:
        rps = request_delta / seconds
    except (OverflowError, ZeroDivisionError):
        return None, None, 'broken'
    if not math.isfinite(rps) or rps < 0:
        return None, None, 'broken'
    return rps, seconds, 'valid'


def _duration(latest):
    elapsed = _number(latest.get('elapsed_seconds'))
    if elapsed is not None and elapsed > 0:
        return elapsed
    requests = _count(latest.get('requests'))
    rps = _number(latest.get('rps'))
    if requests is None or requests == 0 or rps is None or rps <= 0:
        return None
    try:
        elapsed = requests / rps
    except (OverflowError, ZeroDivisionError):
        return None
    return elapsed if math.isfinite(elapsed) and elapsed > 0 else None


def derive_throughput(latest_metrics, metrics_samples):
    """Return detail-safe throughput without mutating or persisting source data."""
    latest = _dict(latest_metrics)
    samples = metrics_samples if isinstance(metrics_samples, list) else []
    points = [_point(sample) for sample in samples[-MAX_SAMPLES:]]
    intervals = []
    values = []
    baseline = None
    for point in points:
        rps = seconds = None
        point_valid = point['requests'] is not None and point['clock_kind'] is not None
        if baseline is None:
            if point_valid:
                baseline = point
        else:
            rps, seconds, state = _interval(baseline, point)
            if state == 'valid':
                baseline = point
            elif state == 'duplicate':
                # Re-reading the same immutable snapshot must not break the
                # next real interval or manufacture a terminal zero.
                pass
            elif state == 'clock_changed':
                # The current point is individually valid, but begins a new
                # clock segment and cannot be compared with the prior point.
                baseline = point if point_valid else None
            else:
                # A malformed point, clock reversal, counter rollback, or
                # same-time counter conflict taints the relationship. The next
                # trustworthy point may only establish a fresh baseline.
                baseline = None
        intervals.append({
            'timestamp': point['timestamp'],
            'rps': rps,
            'interval_seconds': seconds,
        })
        if rps is not None:
            values.append(rps)

    average = _number(latest.get('rps'))
    duration = _duration(latest)
    total_requests = _count(latest.get('requests'))
    if average is None and duration is not None and total_requests is not None:
        try:
            candidate = total_requests / duration
        except (OverflowError, ZeroDivisionError):
            candidate = None
        average = candidate if candidate is not None and math.isfinite(candidate) else None

    entries = latest.get('entries') if isinstance(latest.get('entries'), list) else []
    endpoint_rps = []
    for entry in entries:
        requests = _count(_dict(entry).get('requests'))
        if requests is None or duration is None:
            endpoint_rps.append(None)
            continue
        try:
            candidate = requests / duration
        except (OverflowError, ZeroDivisionError):
            candidate = None
        endpoint_rps.append(
            candidate if candidate is not None and math.isfinite(candidate) else None
        )

    return {
        'average_rps': average,
        'peak_interval_rps': max(values) if values else None,
        'valid_intervals': len(values),
        'intervals': intervals,
        'endpoint_rps': endpoint_rps,
    }
