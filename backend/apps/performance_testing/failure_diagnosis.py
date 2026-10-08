"""Safe, bounded localization of formal-load failure samples.

Raw values are used only for local rule matching. This projection can be sent
to the analysis model or included in an offline report without request data.
"""
import json
import math

from .diagnosis_data import COMPARATORS, ERROR_TYPES, METHODS, PHASES, _selector


MAX_SAMPLES = 20
MAX_STEPS = 20
MAX_RULES = 50
ERROR_LABELS = {
    'connect_timeout': '连接超时', 'read_timeout': '读取超时', 'dns_error': '域名解析失败',
    'tls_error': 'TLS 连接失败', 'connection_error': '网络连接失败',
    'undefined_variable': '变量未定义', 'request_render_error': '请求参数生成失败',
    'response_too_large': '响应内容过大', 'extraction_missing': '响应提取字段缺失',
    'assertion_failed': '响应断言未通过', 'type_mismatch': '断言值类型不符',
    'unsupported_comparator': '比较方式不支持', 'missing_value': '断言字段缺失',
    'invalid_json': '响应 JSON 无效', 'unknown': '未识别的失败类型',
}
SUGGESTIONS = {
    'network': '先用单用户验证检查节点到目标的连接与请求超时设置，再结合被测服务监控排查。',
    'variables': '用单用户验证检查上游变量提取、变量引用名称和请求参数类型。',
    'extraction': '用单用户验证核对响应结构与对应提取规则，并检查后续步骤依赖。',
    'assertion': '用单用户验证查看该断言的期望值、实际值和响应内容，核对规则是否符合业务预期。',
    'response': '用单用户验证核对响应格式和内容大小，再检查被测服务返回是否符合预期。',
    'unknown': '用单用户验证补充步骤和响应证据，当前采样不足以确认具体失败位置。',
}


def _dict(value):
    return value if type(value) is dict else {}


def _list(value):
    return value if type(value) is list else []


def _enum(value, choices):
    return value if type(value) is str and value in choices else 'unknown'


def _value_meta(row, key):
    if key not in row:
        return {'type': 'unknown', 'empty': None, 'possibly_truncated': None}
    value = row[key]
    kind = {type(None): 'null', bool: 'boolean', int: 'number', float: 'number',
            str: 'string', dict: 'object', list: 'list'}.get(type(value), 'unknown')
    if kind == 'number':
        try:
            if not math.isfinite(value):
                kind = 'unknown'
        except OverflowError:
            kind = 'unknown'
    if type(value) is dict and set(value) == {'missing'} and value['missing'] is True:
        # The worker uses this marker, but an actual response object can have
        # the same shape. Its meaning is not asserted without an error signal.
        kind = 'missing_marker'
    return {
        'type': kind,
        'empty': len(value) == 0 if type(value) in (str, list, dict) else None,
        'possibly_truncated': value.endswith('…') if type(value) is str else False,
    }


def _same_literal(left, right):
    if type(left) is not type(right):
        return False
    try:
        a = json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False)
        b = json.dumps(right, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return len(a.encode('utf-8')) <= 2048 and a == b
    except (TypeError, ValueError, RecursionError):
        return False


def _rule(row, step, error_type):
    if error_type in {'undefined_variable', 'request_render_error'}:
        return 'variables', None
    if error_type in {'connect_timeout', 'read_timeout', 'dns_error', 'tls_error', 'connection_error'}:
        return 'network', None
    if error_type in {'response_too_large', 'invalid_json'}:
        return 'response', None
    if error_type == 'extraction_missing' and row.get('comparator') == 'extract':
        kind, field = 'extraction', 'extract'
    elif error_type in {'assertion_failed', 'missing_value', 'type_mismatch', 'unsupported_comparator'}:
        kind, field = 'assertion', 'assertions'
    else:
        return 'unknown', None
    matches = []
    for index, raw in enumerate(_list(step.get(field))[:MAX_RULES], 1):
        rule = _dict(raw)
        if type(row.get('check')) is not str or row['check'] != rule.get('check'):
            continue
        if kind == 'assertion' and (
            row.get('comparator') != rule.get('comparator')
            or 'expected' not in row or 'expected' not in rule
            or not _same_literal(row['expected'], rule['expected'])
        ):
            continue
        matches.append(index)
    # Identical/ambiguous rules cannot be uniquely recovered from old samples.
    return kind, matches[0] if len(matches) == 1 else None


def build_failure_diagnosis(run):
    """Return anonymous sample groups, never failure/request frequencies."""
    if getattr(run, 'mode', None) != 'load':
        return {'items': [], 'limitations': []}
    latest = _dict(run.latest_metrics)
    samples = _list(latest.get('failure_samples'))
    steps = _list(_dict(run.snapshot).get('steps'))[:MAX_STEPS]
    participants = list(run.participants.all())
    node_positions = {}
    for index, participant in enumerate(participants, 1):
        node_id = getattr(participant, 'node_id', None)
        if node_id is not None:
            node_positions.setdefault(str(node_id), []).append(index)
    limitations = [
        '仅分析现有去重失败样本，最多保留 20 条；样本数不是失败请求次数，也不能用于推算失败频率。',
        '样本不包含完整调用链和被测服务监控；定位和建议用于后续验证，不能据此确定服务端根因。',
        '仅展示值的类型和空值特征；原始期望值、实际值、请求与响应内容未进入此诊断。',
    ]
    if len(samples) >= MAX_SAMPLES:
        limitations.append('失败样本已达到采集上限，可能还有未保留的失败类型或位置。')
    if not samples:
        limitations.append('没有可用的失败样本；这不等于运行没有失败，请结合失败请求数和数据完整性判断。')
    groups = {}
    unresolved = False
    for raw in samples[:MAX_SAMPLES]:
        row = _dict(raw)
        position = row.get('step_index')
        step = _dict(steps[position - 1]) if type(position) is int and 1 <= position <= len(steps) else {}
        phase = _enum(row.get('phase'), PHASES)
        if not step or phase == 'unknown' or phase != step.get('phase'):
            position, step = None, {}
        elif (type(row.get('step_name')) is str and type(step.get('name')) is str
              and row['step_name'] != step['name']):
            position, step = None, {}
        node_id = row.get('node_id')
        nodes = node_positions.get(node_id, []) if type(node_id) is str else []
        node_index = nodes[0] if len(nodes) == 1 else None
        error_type = _enum(row.get('error_type'), ERROR_TYPES)
        rule_kind, rule_index = _rule(row, step, error_type)
        check = row.get('check')
        check_type = check if type(check) is str and check in {'network', 'variables'} else _selector(check)
        item = {
            'step_index': position, 'node_index': node_index,
            'phase': phase if position is not None else 'unknown',
            'method': _enum(step.get('method'), METHODS),
            'check_type': check_type, 'rule_kind': rule_kind, 'rule_index': rule_index,
            'comparator': _enum(row.get('comparator'), COMPARATORS | {'extract'}),
            'error_type': error_type,
            'expected_meta': _value_meta(row, 'expected'),
            'actual_meta': _value_meta(row, 'actual'),
        }
        unresolved |= (position is None or node_index is None or error_type == 'unknown'
                       or (rule_kind in {'assertion', 'extraction'} and rule_index is None))
        key = json.dumps(item, sort_keys=True)
        if key not in groups:
            where = f'步骤 {position}' if position is not None else '步骤未确认'
            node_label = f'节点 {node_index}' if node_index is not None else '节点未确认'
            rule_label = ''
            if rule_kind in {'assertion', 'extraction'}:
                name = '断言' if rule_kind == 'assertion' else '提取规则'
                rule_label = f' · {name} {rule_index}' if rule_index else f' · {name}位置未确认'
            groups[key] = {
                'id': f'failure.{len(groups) + 1}',
                'label': f'{where} · {node_label}{rule_label} · {ERROR_LABELS[error_type]}',
                **item, 'sample_count': 0, 'suggestion': SUGGESTIONS[rule_kind],
            }
        groups[key]['sample_count'] += 1
    if unresolved:
        limitations.append('部分样本不能唯一关联到步骤、节点或规则，位置标为未确认，不按名称或单节点假设补全。')
    if any(item['expected_meta']['possibly_truncated'] or item['actual_meta']['possibly_truncated']
           for item in groups.values()):
        limitations.append('部分样本值可能已被截断，类型仅反映保留值，不能恢复完整内容或据此推断字段值。')
    return {'items': list(groups.values()), 'limitations': limitations}
