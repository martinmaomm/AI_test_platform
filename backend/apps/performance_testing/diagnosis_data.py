"""Bounded, anonymous facts for single-user validation diagnosis.

Raw plan/trace values are inspected locally only. Nothing copied into the
projection may contain a URL, name, selector, literal value, or message.
"""
import json
import math
import re


MAX_STEPS = 20
MAX_CHECKS = 50
MAX_DEPENDENCIES = 400
MAX_PAYLOAD_BYTES = 384 * 1024
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
PHASES = {'setup', 'main'}
STEP_STATES = {'pending', 'running', 'passed', 'failed', 'skipped'}
RUN_STATES = {'queued', 'preparing', 'running', 'stopping', 'completed', 'failed', 'cancelled', 'incomplete'}
COMPARATORS = {'eq', 'ne', 'contains', 'not_contains', 'gt', 'ge', 'lt', 'le',
               'type', 'length', 'length_gt', 'exists'}
ERROR_TYPES = {'connect_timeout', 'read_timeout', 'dns_error', 'tls_error',
               'connection_error', 'undefined_variable', 'request_render_error',
               'response_too_large', 'extraction_missing', 'assertion_failed',
               'type_mismatch', 'unsupported_comparator', 'missing_value', 'invalid_json'}
VALUE_TYPES = {'null', 'boolean', 'number', 'string', 'object', 'list'}
NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]{0,63})\}')
SELECTOR_RE = re.compile(
    r"^(?:status_code|text|headers\.[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}"
    r'|body(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])*)$'
)
LIMITATIONS = {
    'missing_trace': '缺少可用的步骤执行记录，不能据此推断请求成功或下游已执行。',
    'incomplete': '执行未完整结束或记录不完整，不能将部分步骤通过视为整轮验证通过。',
    'malformed': '部分字段缺失、格式异常或相互矛盾，相关事实标为 unknown，不猜测原始内容。',
    'truncated': '步骤、检查项、依赖或证据预览存在截断，未展示内容不能视为不存在或通过。',
    'dependencies': '部分变量依赖无法完整确认，其可用性标为 unknown。',
    'payload': '为限制诊断输入大小，部分检查详情已省略；步骤结论不能补足被省略的证据。',
}


def _dict(value):
    return value if type(value) is dict else {}


def _enum(value, allowed, default='unknown'):
    return value if type(value) is str and value in allowed else default


def _bool(value):
    return value if type(value) is bool else None


def _number(value, maximum):
    if type(value) not in (int, float):
        return None
    try:
        return value if 0 <= value <= maximum and math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def _index(value, maximum=MAX_STEPS):
    return value if type(value) is int and 1 <= value <= maximum else None


def _selector(value):
    if type(value) is not str or len(value) > 512 or not SELECTOR_RE.fullmatch(value):
        return 'unknown'
    if value.startswith('headers.'):
        return 'header'
    if value.startswith('body'):
        return 'body'
    return value  # Only the literal enum values status_code / text can reach here.


def _json(text, limit=4096):
    if type(text) is not str or len(text) > limit:
        return None, False
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        return None, False
    return value, True


def _preview(value, notes):
    value = _dict(value)
    truncated = _bool(value.get('truncated'))
    parsed, valid = _json(value.get('content'))
    result = {'type': 'unknown', 'is_null': None, 'empty': None, 'truncated': truncated}
    if truncated is True:
        notes.add('truncated')
    if not valid or truncated is not False:
        notes.add('malformed' if truncated is not True else 'truncated')
        return result, None
    kind = {type(None): 'null', bool: 'boolean', int: 'number', float: 'number',
            str: 'string', dict: 'object', list: 'list'}.get(type(parsed), 'unknown')
    if kind == 'number' and _number(abs(parsed), 1e308) is None:
        notes.add('malformed')
        return result, None
    if type(parsed) is dict and set(parsed) == {'missing'} and parsed['missing'] is True:
        kind = 'missing'
    result.update(type=kind, is_null=parsed is None,
                  empty=len(parsed) == 0 if type(parsed) in (str, dict, list) else None)
    return result, parsed


def _headers(request, notes):
    unknown = {'authorization_present': None, 'auth_scheme': 'unknown',
               'accept_language_present': None, 'unresolved_placeholders': None}
    if type(request) is not dict:
        return unknown
    preview = _dict(request.get('headers'))
    headers, valid = _json(preview.get('content'), 8192)
    if preview.get('truncated') is not False or not valid or type(headers) is not dict:
        notes.add('truncated' if preview.get('truncated') is True else 'malformed')
        return unknown
    if any(type(k) is not str or type(v) is not str for k, v in headers.items()):
        notes.add('malformed')
        return unknown
    normalized = {key.lower(): value for key, value in headers.items()}
    if len(normalized) != len(headers):
        notes.add('malformed')
        return unknown
    present = 'authorization' in normalized
    scheme = 'none' if not present else 'other'
    if present:
        pieces = normalized['authorization'].strip().split(None, 1)
        candidate = pieces[0].lower() if pieces else ''
        scheme = candidate if candidate in {'bearer', 'basic', 'digest', 'negotiate'} else 'other'
    unresolved = any(REF_RE.search(key) or REF_RE.search(value) for key, value in headers.items())
    return {'authorization_present': present, 'auth_scheme': scheme,
            'accept_language_present': 'accept-language' in normalized,
            'unresolved_placeholders': bool(unresolved)}


def _request_usable(request, header_flags):
    if type(request) is not dict:
        return False
    url = request.get('url')
    body = _dict(request.get('body'))
    return (type(url) is str and bool(url.strip()) and len(url) <= 4096
            and _enum(request.get('body_type'), {'none', 'json', 'form', 'raw'}) != 'unknown'
            and type(body.get('content')) is str and body.get('truncated') is False
            and header_flags['authorization_present'] is not None)


def _checks(trace, plan, kind, status, notes):
    rows = trace.get(kind)
    expected = plan.get('assertions' if kind == 'assertions' else 'extract')
    if type(rows) is not list or type(expected) is not list:
        notes.add('malformed')
        return [], False
    # A skipped step has no executed checks; do not turn the plan into fake results.
    if status == 'skipped' and not rows:
        return [], True
    complete = len(rows) == len(expected) and len(rows) <= MAX_CHECKS
    if len(rows) > MAX_CHECKS or len(expected) > MAX_CHECKS:
        notes.add('truncated')
    if not complete:
        notes.add('malformed')
    result = []
    for index, raw in enumerate(rows[:MAX_CHECKS], 1):
        row = _dict(raw)
        state = _enum(row.get('status'), STEP_STATES)
        selector = _selector(row.get('check'))
        expected_row = _dict(expected[index - 1]) if index <= len(expected) else {}
        if (_index(row.get('index'), MAX_CHECKS) != index or state == 'unknown'
                or selector == 'unknown' or row.get('check') != expected_row.get('check')):
            complete = False
            notes.add('malformed')
        item = {'index': index, 'check_type': selector, 'status': state}
        if kind == 'assertions':
            comparator = _enum(row.get('comparator'), COMPARATORS)
            if comparator == 'unknown' or row.get('comparator') != expected_row.get('comparator'):
                complete = False
                notes.add('malformed')
            expected_meta, expected_value = _preview(row.get('expected'), notes)
            actual_meta, _ = _preview(row.get('actual'), notes)
            if expected_meta['type'] == 'unknown' or actual_meta['type'] == 'unknown':
                complete = False
            item.update(comparator=comparator,
                        error_type=_enum(row.get('error_type'), ERROR_TYPES | {''}, 'unknown') or 'none',
                        expected=expected_meta, actual=actual_meta)
            if comparator == 'type':
                item['expected_type'] = _enum(expected_value, VALUE_TYPES)
                if item['expected_type'] == 'unknown':
                    complete = False
                    notes.add('malformed')
        else:
            if row.get('name') != expected_row.get('name'):
                complete = False
                notes.add('malformed')
            item['value'], _ = _preview(row.get('value'), notes)
            if item['value']['type'] == 'unknown' or (state == 'passed' and item['value']['type'] == 'missing'):
                complete = False
                notes.add('malformed')
        result.append(item)
    return result, complete


def _references(value, notes):
    """Inspect bounded request material locally, including templated object keys."""
    pending = [(value, 0)]
    names = set()
    remaining = 65536
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 4096:
            notes.add('dependencies')
            return names, False
        if type(value) is str:
            remaining -= len(value)
            if remaining < 0:
                notes.add('dependencies')
                return names, False
            names.update(REF_RE.findall(value))
        elif type(value) is dict:
            if len(value) > 4096:
                notes.add('dependencies')
                return names, False
            for key, item in value.items():
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif type(value) is list:
            if len(value) > 4096:
                notes.add('dependencies')
                return names, False
            pending.extend((item, depth + 1) for item in value)
    return names, True


def _dependencies(snapshot, steps, notes):
    definitions = {}

    def define(name, source, producer=None, extraction=None):
        if type(name) is not str or not NAME_RE.fullmatch(name):
            notes.add('dependencies')
            return
        if name in definitions:
            definitions[name]['source'] = 'ambiguous'
            definitions[name]['producer_step'] = None
            definitions[name]['extraction_index'] = None
            notes.add('dependencies')
            return
        definitions[name] = {'variable_id': f'variable.{len(definitions) + 1}',
                             'source': source, 'producer_step': producer,
                             'extraction_index': extraction}

    fixed = snapshot.get('variables')
    unique = snapshot.get('unique_variables')
    definitions_complete = type(fixed) is dict and type(unique) is list
    if not definitions_complete:
        notes.add('dependencies')
    for name in list(_dict(fixed))[:100]:
        define(name, 'fixed')
    for item in (unique if type(unique) is list else [])[:100]:
        define(_dict(item).get('name'), 'unique')
    if len(_dict(fixed)) > 100 or (type(unique) is list and len(unique) > 100):
        definitions_complete = False
        notes.add('truncated')
    plans = snapshot.get('steps') if type(snapshot.get('steps')) is list else []
    for index, plan in enumerate(plans[:MAX_STEPS], 1):
        extracts = _dict(plan).get('extract')
        if type(extracts) is not list:
            definitions_complete = False
            notes.add('dependencies')
            continue
        if len(extracts) > MAX_CHECKS:
            definitions_complete = False
            notes.add('truncated')
        for offset, item in enumerate(extracts[:MAX_CHECKS], 1):
            define(_dict(item).get('name'), 'extraction', index, offset)

    edges = []
    for index, plan in enumerate(plans[:MAX_STEPS], 1):
        plan = _dict(plan)
        references, inspected = _references({key: plan.get(key) for key in ('path', 'query', 'headers', 'body')}, notes)
        for name in sorted(references):
            if len(edges) >= MAX_DEPENDENCIES:
                notes.add('truncated')
                return edges
            if name not in definitions:
                define(name, 'undefined' if definitions_complete and inspected else 'unknown')
            definition = definitions[name]
            source = definition['source']
            producer_index = definition['producer_step']
            producer = steps.get(producer_index, {})
            consumer = steps.get(index, {})
            available = None
            reason = 'unknown'
            if source == 'fixed':
                available, reason = True, 'configured'
            elif source == 'unique':
                phase = consumer.get('phase')
                if phase in PHASES:
                    available = phase == 'main'
                    reason = 'generated_for_main' if available else 'not_available_in_setup'
            elif source == 'undefined':
                available, reason = False, 'not_declared'
            elif source == 'extraction':
                if producer_index >= index:
                    available, reason = False, 'not_yet_produced'
                elif producer.get('status') in {'failed', 'skipped'}:
                    available, reason = False, 'producer_failed' if producer['status'] == 'failed' else 'producer_skipped'
                elif producer.get('extractions_committed') is True:
                    available, reason = True, 'committed'
            edges.append({**definition, 'consumer_step': index,
                          'consumer_status': consumer.get('status', 'unknown'),
                          'producer_status': producer.get('status', 'unknown'),
                          'available': available, 'availability_reason': reason,
                          'producer_failed_and_consumer_skipped':
                              producer.get('status') == 'failed' and consumer.get('status') == 'skipped'})
    return edges


def build_validation_input(run):
    """Build an anonymous, bounded projection; never call providers or mutate run."""
    snapshot = _dict(getattr(run, 'snapshot', None))
    metrics = _dict(getattr(run, 'latest_metrics', None))
    notes = set()
    plans = snapshot.get('steps')
    plans_valid = type(plans) is list and 0 < len(plans) <= MAX_STEPS
    if not plans_valid:
        notes.add('malformed')
    if type(plans) is list and len(plans) > MAX_STEPS:
        notes.add('truncated')
    plans = plans[:MAX_STEPS] if type(plans) is list else []
    raw_trace = metrics.get('validation_steps')
    traces = {}
    duplicate_indices = set()
    if type(raw_trace) is not list or not raw_trace:
        notes.add('missing_trace')
    else:
        if len(raw_trace) > MAX_STEPS:
            notes.add('truncated')
        for raw in raw_trace[:MAX_STEPS]:
            row = _dict(raw)
            index = _index(row.get('step_index'))
            if index is None:
                notes.add('malformed')
                continue
            if index in traces:
                duplicate_indices.add(index)
                notes.add('malformed')
            traces[index] = row
    for index in duplicate_indices:
        traces.pop(index, None)
    if not traces:
        notes.add('missing_trace')
    errors = {}
    failures = metrics.get('failure_samples')
    failure_collection_valid = type(failures) is list and len(failures) <= 20
    failure_sample_steps = set()
    for item in (failures if type(failures) is list else [])[:20]:
        row = _dict(item)
        index = _index(row.get('step_index'))
        error_type = _enum(row.get('error_type'), ERROR_TYPES)
        if index is None or error_type == 'unknown':
            failure_collection_valid = False
        else:
            failure_sample_steps.add(index)
        if index is not None:
            errors.setdefault(index, set()).add(error_type)
    if not failure_collection_valid:
        notes.add('malformed')

    steps = {}
    for index in sorted(set(range(1, len(plans) + 1)) | set(traces)):
        plan = _dict(plans[index - 1]) if index <= len(plans) else {}
        trace = traces.get(index, {})
        state = _enum(trace.get('status'), STEP_STATES)
        method = _enum(trace.get('method', plan.get('method')), METHODS)
        phase = _enum(trace.get('phase', plan.get('phase')), PHASES)
        if (not trace or state == 'unknown' or method == 'unknown' or phase == 'unknown'
                or method != plan.get('method') or phase != plan.get('phase')):
            notes.add('malformed')
        request = trace.get('request')
        header_flags = _headers(request, notes)
        request_usable = _request_usable(request, header_flags)
        if _dict(_dict(request).get('body')).get('truncated') is True:
            notes.add('truncated')
        elapsed = _number(trace.get('elapsed_ms'), 86400000)
        if elapsed is None and (trace.get('elapsed_ms') is not None or state == 'passed'):
            notes.add('malformed')
        response = _dict(trace.get('response'))
        response_present = type(trace.get('response')) is dict
        status_code = response.get('status_code')
        status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        incomplete = _bool(response.get('incomplete')) if response_present else None
        assertions, assertions_complete = _checks(trace, plan, 'assertions', state, notes)
        extractions, extractions_complete = _checks(trace, plan, 'extractions', state, notes)
        checks_passed = all(item['status'] == 'passed' for item in assertions + extractions)
        commit_verified = (state == 'passed' and assertions_complete and extractions_complete
                           and bool(assertions) and checks_passed and response_present
                           and incomplete is False and status_code is not None
                           and request_usable and failure_collection_valid
                           and index not in failure_sample_steps
                           and method == plan.get('method') and phase == plan.get('phase'))
        committed = True if commit_verified else False if state in {'failed', 'skipped'} else None
        if state == 'passed' and (not committed or status_code is None or not assertions):
            notes.add('malformed')
        for item in extractions:
            item['committed'] = committed
        steps[index] = {
            'step_index': index, 'status': state, 'method': method, 'phase': phase,
            'elapsed_ms': elapsed,
            'request_present': type(request) is dict,
            'request_usable': request_usable,
            'request_headers': header_flags,
            'response': {'present': response_present, 'status_code': status_code, 'incomplete': incomplete},
            'assertions': assertions, 'extractions': extractions,
            'extractions_committed': committed,
            'error_types': sorted(errors.get(index, set())),
            'checks_complete': assertions_complete and extractions_complete,
        }
    dependencies = _dependencies(snapshot, steps, notes)
    if any(edge['consumer_status'] == 'passed' and edge['available'] is not True
           for edge in dependencies):
        notes.add('malformed')
    failed = [index for index, step in steps.items() if step['status'] == 'failed']
    skipped = [index for index, step in steps.items() if step['status'] == 'skipped']
    run_status = _enum(getattr(run, 'status', None), RUN_STATES)
    valid_mode = getattr(run, 'mode', None) == 'validation'
    expected_main = sum(_dict(plan).get('phase') == 'main' for plan in plans)
    main_completed = metrics.get('main_steps_completed')
    main_total = metrics.get('main_steps_total')
    counts_valid = (plans_valid and type(main_completed) is int and type(main_total) is int
                    and 0 <= main_completed <= main_total <= MAX_STEPS
                    and main_total > 0 and main_total == expected_main)
    runtime_complete = (metrics.get('validation_complete') is True and counts_valid
                        and main_completed == main_total
                        and _index(metrics.get('requests'), 9223372036854775807) is not None)
    if (not counts_valid or _bool(metrics.get('validation_complete')) is None
            or (metrics.get('validation_passed') is True and not runtime_complete)):
        notes.add('malformed')
    failure_conflict = (
        bool(failure_sample_steps) and metrics.get('validation_passed') is True
        or any(steps.get(index, {}).get('status') == 'passed' for index in failure_sample_steps)
        or (not failure_collection_valid and (metrics.get('validation_passed') is True
            or bool(steps) and all(step['status'] == 'passed' for step in steps.values())))
    )
    if failure_conflict:
        notes.add('malformed')
    full = (valid_mode and run_status == 'completed' and metrics.get('complete') is True
            and runtime_complete
            and plans_valid and len(traces) == len(plans) and not duplicate_indices
            and all(step['status'] == 'passed' for step in steps.values())
            and not notes)
    if not traces or not valid_mode:
        status = 'insufficient_data'
        notes.add('missing_trace' if not traces else 'malformed')
    elif failure_conflict:
        status = 'insufficient_data'
    elif failed:
        status = 'failed'
    elif full and metrics.get('validation_passed') is True:
        status = 'passed'
    elif notes or run_status == 'completed':
        status = 'insufficient_data'
    else:
        status = 'incomplete'
    if status in {'incomplete', 'insufficient_data'}:
        notes.add('incomplete')
    assessment = {'status': status, 'first_failed_step': failed[0] if failed else None,
                  'failed_steps': failed, 'skipped_steps': skipped, 'checks': []}
    evidence = [{'id': 'run.validation', 'label': '单用户验证记录与完整性', 'unit': '', 'value': {
        'status': run_status, 'complete': _bool(metrics.get('complete')),
        'validation_complete': _bool(metrics.get('validation_complete')),
        'validation_passed': _bool(metrics.get('validation_passed')),
        'main_steps_completed': main_completed if counts_valid else None,
        'main_steps_total': main_total if counts_valid else None,
        'planned_steps': len(plans) if plans_valid else None, 'reported_steps': len(traces),
    }}]
    evidence.extend({'id': f'step.{index}', 'label': f'步骤 {index} 的匿名执行证据',
                     'value': step, 'unit': ''} for index, step in steps.items())
    evidence.append({'id': 'variables.dependencies', 'label': '匿名变量生产与消费关系',
                     'value': dependencies, 'unit': ''})
    payload = {'schema_version': 1, 'analysis_type': 'validation_diagnosis', 'targets': {},
               'assessment': assessment, 'evidence': evidence, 'limitations': []}
    # Preserve every step status. Drop only check detail when an extreme report
    # would exceed the subprocess envelope, and make that loss explicit.
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > MAX_PAYLOAD_BYTES - 4096:
        notes.add('payload')
        for step in steps.values():
            step['assertions'] = step['assertions'][:10]
            step['extractions'] = step['extractions'][:10]
            step['checks_complete'] = False
        if assessment['status'] == 'passed':
            assessment['status'] = 'insufficient_data'
    payload['limitations'] = [
        '诊断仅使用匿名结构与执行状态；原始请求、响应、变量值和选择器留在平台详情中，模型无法核对其业务内容。',
        '提取结果只有整步通过才提交；单项提取 passed 不代表下游已得到该值。提交状态 null 表示证据不足，不能解读为未提交。依赖关系不等于失败根因。',
        *[LIMITATIONS[key] for key in LIMITATIONS if key in notes],
    ]
    return payload
