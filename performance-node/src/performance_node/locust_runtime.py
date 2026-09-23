"""Frozen schema-v4 request runtime; safe to import outside Locust/gevent."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from urllib.parse import quote, unquote, urlencode, urlsplit


ENGINE_VERSION = '2.43.3'
MAX_SNAPSHOT_BYTES = 65536
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_FAILURE_SAMPLES = 20
MAX_FAILURE_VALUE_BYTES = 2048
MISSING = object()
NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]{0,63})\}')
SELECTOR_RE = re.compile(
    r'^(?:status_code|text|headers\.[!#$%&\'*+.^_`|~0-9A-Za-z-]{1,128}'
    r'|body(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])*)$'
)
METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS')
COMPARATORS = ('eq', 'ne', 'contains', 'not_contains', 'gt', 'ge', 'lt', 'le',
               'type', 'length', 'length_gt', 'exists')
FORBIDDEN_HEADERS = {'host', 'content-length', 'transfer-encoding', 'connection',
                     'proxy-authorization', 'proxy-connection'}

_NETWORK_EXCEPTION_KINDS = {
    'ConnectTimeout': ('connect_timeout', '连接超时'),
    'ConnectTimeoutError': ('connect_timeout', '连接超时'),
    'ReadTimeout': ('read_timeout', '读取响应超时'),
    'ReadTimeoutError': ('read_timeout', '读取响应超时'),
    'NameResolutionError': ('dns_error', 'DNS 解析失败'),
    'SSLError': ('tls_error', 'TLS 连接失败'),
}
_SAFE_NETWORK_EXCEPTION_NAMES = {
    'BrokenPipeError', 'ChunkedEncodingError', 'ConnectionError', 'ConnectionResetError',
    'ContentDecodingError', 'HTTPError', 'MaxRetryError', 'NetworkError',
    'NewConnectionError', 'OSError', 'PoolError', 'ProtocolError', 'ProxyError',
    'ReadTimeout', 'ReadTimeoutError', 'RemoteDisconnected', 'RequestException',
    'ResponseError', 'SSLError', 'Timeout', 'TooManyRedirects',
}


def classify_network_exception(exc):
    """Classify requests/urllib3 failures without importing either library."""
    pending = [exc]
    seen = set()
    names = []
    while pending and len(seen) < 20:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        name = type(current).__name__
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', name):
            names.append(name)
        for linked in (getattr(current, '__cause__', None), getattr(current, '__context__', None)):
            if isinstance(linked, BaseException):
                pending.append(linked)
        pending.extend(item for item in getattr(current, 'args', ()) if isinstance(item, BaseException))
    for expected in ('ReadTimeout', 'ReadTimeoutError', 'ConnectTimeout', 'ConnectTimeoutError',
                     'NameResolutionError', 'SSLError'):
        if expected in names:
            error_type, label = _NETWORK_EXCEPTION_KINDS[expected]
            return error_type, f'{label}（{expected}）'
    safe_name = next((name for name in names if name in _SAFE_NETWORK_EXCEPTION_NAMES),
                     'NetworkError')
    return 'connection_error', f'网络连接失败（{safe_name}）'


class ReportSequence:
    """Accept immutable, monotonic incremental batches and remember any gap."""

    def __init__(self):
        self.last = 0
        self.final = None
        self.gap = False
        self._digests = {}

    def accept(self, sequence, final_sequence, digest):
        if (type(sequence) is not int or sequence <= 0
                or type(digest) is not str or not digest
                or (final_sequence is not None
                    and (type(final_sequence) is not int or final_sequence != sequence))):
            raise ValueError('统计序号无效')
        if sequence <= self.last:
            if self._digests.get(sequence) != digest:
                raise ValueError('重复序号内容不一致')
            return False
        if self.final is not None:
            raise ValueError('最终统计后出现更高序号')
        if sequence != self.last + 1:
            self.gap = True
        self.last = sequence
        self._digests[sequence] = digest
        if final_sequence is not None:
            self.final = final_sequence
        return True


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _json_value(value, depth=0):
    if depth > 10:
        raise ValueError('JSON 嵌套层级过深')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('JSON 数值无效')
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('JSON 对象键无效')
            _json_value(item, depth + 1)
        return
    raise ValueError('JSON 类型无效')


def _refs(value):
    if type(value) is str:
        return set(REF_RE.findall(value))
    if type(value) is list:
        result = set()
        for item in value:
            result.update(_refs(item))
        return result
    if type(value) is dict:
        result = set()
        for key, item in value.items():
            result.update(REF_RE.findall(key))
            result.update(_refs(item))
        return result
    return set()


def _validate_name(value):
    if type(value) is not str or not NAME_RE.fullmatch(value):
        raise ValueError('变量名无效')


def _validate_mapping(mapping):
    if type(mapping) is not dict or len(mapping) > 100:
        raise ValueError('参数对象无效')
    for key, item in mapping.items():
        if type(key) is not str or not key or len(key) > 256 or re.search(r'[\x00-\x1f\x7f]', key):
            raise ValueError('参数名称无效')
        values = item if type(item) is list else [item]
        if not values or any(type(part) not in (type(None), str, bool, int, float) for part in values):
            raise ValueError('参数值无效')
        for part in values:
            _json_value(part)


def _validate_assertion(assertion):
    if type(assertion) is not dict or set(assertion) != {'check', 'comparator', 'expected'}:
        raise ValueError('断言字段无效')
    if type(assertion['check']) is not str or not SELECTOR_RE.fullmatch(assertion['check']):
        raise ValueError('断言选择器无效')
    comparator = assertion['comparator']
    expected = assertion['expected']
    if comparator not in COMPARATORS:
        raise ValueError('断言比较器无效')
    _json_value(expected)
    if comparator == 'type' and (
        type(expected) is not str
        or expected not in ('null', 'boolean', 'number', 'string', 'object', 'list')
    ):
        raise ValueError('类型断言无效')
    if comparator in ('length', 'length_gt') and (type(expected) is not int or expected < 0):
        raise ValueError('长度断言无效')
    if comparator in ('gt', 'ge', 'lt', 'le') and (
        type(expected) not in (int, float) or not math.isfinite(expected)
    ):
        raise ValueError('数值断言无效')
    if comparator == 'exists' and type(expected) is not bool:
        raise ValueError('存在性断言无效')


def validate_snapshot(value):
    fields = {'schema_version', 'run_id', 'nodes', 'engine_version', 'plan_name', 'base_url',
              'allowed_methods', 'mode', 'users', 'spawn_rate',
              'duration_seconds', 'wait_seconds', 'connect_timeout_seconds',
              'read_timeout_seconds', 'variables', 'unique_variables', 'steps'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('运行快照字段无效')
    if type(value['schema_version']) is not int or value['schema_version'] != 4 or value['engine_version'] != ENGINE_VERSION:
        raise ValueError('运行快照版本不匹配')
    if type(value['run_id']) is not str or str(uuid.UUID(value['run_id'])) != value['run_id']:
        raise ValueError('运行身份无效')
    if type(value['plan_name']) is not str or not 1 <= len(value['plan_name']) <= 200:
        raise ValueError('计划名称无效')
    if value['mode'] not in ('validation', 'load'):
        raise ValueError('运行模式无效')
    for key, low, high, integer in [('users', 1, 1000, True), ('spawn_rate', .000001, 100, False),
                                    ('duration_seconds', 1, 600, True), ('wait_seconds', .1, 60, False)]:
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high or (integer and type(number) is not int):
            raise ValueError('运行负载参数无效')
    for key, low, high in (('connect_timeout_seconds', 1, 60),
                           ('read_timeout_seconds', 1, 120)):
        number = value[key]
        if type(number) is not int or not low <= number <= high:
            raise ValueError('请求超时参数无效')
    if value['mode'] == 'validation' and (value['users'] != 1 or value['spawn_rate'] != 1):
        raise ValueError('验证模式必须为单用户')
    nodes = value['nodes']
    if type(nodes) is not list or not 1 <= len(nodes) <= 5:
        raise ValueError('参与节点数量无效')
    normalized_node_ids = []
    for item in nodes:
        if type(item) is not dict or set(item) != {'node_id', 'users', 'validation_key'}:
            raise ValueError('参与节点字段无效')
        try:
            normalized_node_id = str(uuid.UUID(item['node_id']))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('参与节点身份无效') from exc
        if normalized_node_id != item['node_id']:
            raise ValueError('参与节点身份无效')
        if type(item['users']) is not int or item['users'] <= 0:
            raise ValueError('参与节点用户数无效')
        if type(item['validation_key']) is not str or not re.fullmatch(r'[0-9a-f]{64}', item['validation_key']):
            raise ValueError('验证指纹无效')
        normalized_node_ids.append(normalized_node_id)
    if normalized_node_ids != sorted(normalized_node_ids) or len(normalized_node_ids) != len(set(normalized_node_ids)):
        raise ValueError('参与节点必须按身份排序且不得重复')
    if value['users'] < len(nodes) or sum(item['users'] for item in nodes) != value['users']:
        raise ValueError('参与节点用户数与总用户数不一致')
    base, remainder = divmod(value['users'], len(nodes))
    if [item['users'] for item in nodes] != [base + (index < remainder) for index in range(len(nodes))]:
        raise ValueError('参与节点用户数不是稳定均分结果')
    if value['mode'] == 'validation' and len(nodes) != 1:
        raise ValueError('验证模式必须且只能选择一个节点')
    origin = value['base_url']
    if type(origin) is not str or any(character.isspace() or character in '\\?#' or ord(character) < 32 for character in origin):
        raise ValueError('压测目标无效')
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('压测目标无效') from exc
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in ('', '/') or port == 0:
        raise ValueError('压测目标必须是 HTTP(S) origin')
    methods = value['allowed_methods']
    if type(methods) is not list or not methods or len(methods) != len(set(methods)) or any(method not in METHODS for method in methods):
        raise ValueError('目标批准方法无效')
    variables = value['variables']
    if type(variables) is not dict or len(variables) > 100:
        raise ValueError('固定变量无效')
    for name, item in variables.items():
        _validate_name(name)
        _json_value(item)
    unique = value['unique_variables']
    if type(unique) is not list or len(unique) > 100:
        raise ValueError('唯一变量无效')
    unique_names = []
    for item in unique:
        if type(item) is not dict or set(item) != {'name', 'prefix'}:
            raise ValueError('唯一变量字段无效')
        _validate_name(item['name'])
        if type(item['prefix']) is not str or len(item['prefix']) > 200 or re.search(r'[\x00-\x1f\x7f]', item['prefix']):
            raise ValueError('唯一变量前缀无效')
        unique_names.append(item['name'])
    if len(unique_names) != len(set(unique_names)) or set(unique_names) & set(variables):
        raise ValueError('变量名重复')
    steps = value['steps']
    if type(steps) is not list or not 1 <= len(steps) <= 20:
        raise ValueError('请求步骤数量无效')
    extracted = set()
    setup_available = set(variables)
    setup_outputs = set()
    main_available = None
    main_seen = False
    main_count = 0
    step_fields = {'name', 'phase', 'method', 'path', 'query', 'headers', 'body_type',
                   'body', 'extract', 'assertions'}
    for step in steps:
        if type(step) is not dict or set(step) != step_fields:
            raise ValueError('请求步骤字段无效')
        if type(step['name']) is not str or not step['name'].strip() or len(step['name']) > 200 or step['method'] not in methods:
            raise ValueError('请求步骤名称或方法无效')
        if step['phase'] not in ('setup', 'main') or (step['phase'] == 'setup' and main_seen):
            raise ValueError('请求步骤阶段顺序无效')
        if step['phase'] == 'main':
            main_seen = True
            main_count += 1
            if main_available is None:
                main_available = set(variables) | set(unique_names) | setup_outputs
            available = main_available
        else:
            available = setup_available
        path = step['path']
        if type(path) is not str or len(path) > 2048 or not path.startswith('/') or path.startswith('//') or '\\' in path or '?' in path or '#' in path or re.search(r'[\x00-\x20\x7f]', path):
            raise ValueError('请求路径无效')
        if any(unquote(segment).casefold() in ('.', '..') for segment in path.split('/')):
            raise ValueError('请求路径不能包含相对目录段')
        _validate_mapping(step['query'])
        headers = step['headers']
        if type(headers) is not dict or len(headers) > 50:
            raise ValueError('请求头无效')
        for key, item in headers.items():
            if type(key) is not str or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key) or key.lower() in FORBIDDEN_HEADERS or type(item) is not str or len(item) > 4096 or re.search(r'[\x00-\x1f\x7f]', item):
                raise ValueError('请求头不允许覆盖目标或传输协议')
        body_type = step['body_type']
        body = step['body']
        if body_type not in ('none', 'json', 'form', 'raw'):
            raise ValueError('请求体类型无效')
        _json_value(body)
        if body_type == 'none' and body is not None:
            raise ValueError('空请求体无效')
        if body_type == 'form':
            _validate_mapping(body)
        if body_type == 'raw' and type(body) is not str:
            raise ValueError('原始请求体无效')
        if _refs({'path': path, 'query': step['query'], 'headers': headers, 'body': body}) - available:
            raise ValueError('请求引用了尚未定义的变量')
        extracts = step['extract']
        if type(extracts) is not list or len(extracts) > 50:
            raise ValueError('响应提取配置无效')
        names = []
        for extract in extracts:
            if type(extract) is not dict or set(extract) != {'name', 'check'}:
                raise ValueError('响应提取字段无效')
            _validate_name(extract['name'])
            if type(extract['check']) is not str or not SELECTOR_RE.fullmatch(extract['check']):
                raise ValueError('响应提取选择器无效')
            names.append(extract['name'])
        if len(names) != len(set(names)) or set(names) & (set(variables) | set(unique_names) | extracted):
            raise ValueError('响应提取变量名重复')
        extracted.update(names)
        (setup_available if step['phase'] == 'setup' else main_available).update(names)
        if step['phase'] == 'setup':
            setup_outputs.update(names)
        assertions = step['assertions']
        if type(assertions) is not list or not 1 <= len(assertions) <= 50:
            raise ValueError('请求步骤必须配置断言')
        for assertion in assertions:
            _validate_assertion(assertion)
    if not main_count:
        raise ValueError('计划至少需要一个 main 步骤')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError('运行快照过大')
    return value


def _string_value(value):
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if type(value) in (dict, list):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return str(value)


def resolve_value(value, variables):
    if type(value) is str:
        exact = REF_RE.fullmatch(value)
        if exact:
            if exact.group(1) not in variables:
                raise KeyError(exact.group(1))
            return variables[exact.group(1)]
        def replace(match):
            if match.group(1) not in variables:
                raise KeyError(match.group(1))
            return _string_value(variables[match.group(1)])
        return REF_RE.sub(replace, value)
    if type(value) is list:
        return [resolve_value(item, variables) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            resolved_key = resolve_value(key, variables)
            if type(resolved_key) is not str:
                raise ValueError('对象键替换后必须是字符串')
            result[resolved_key] = resolve_value(item, variables)
        return result
    return value


def resolve_path(value, variables):
    def replace(match):
        if match.group(1) not in variables:
            raise KeyError(match.group(1))
        item = variables[match.group(1)]
        if type(item) not in (type(None), str, bool, int, float):
            raise ValueError('path 变量必须是 JSON 标量')
        if type(item) is str and (
            any(character in item for character in '/\\?#')
            or unquote(item).casefold() in ('.', '..')
        ):
            raise ValueError('path 变量必须是单个安全路径段')
        return quote(_string_value(item), safe='')
    resolved = REF_RE.sub(replace, value)
    if (len(resolved) > 2048 or not resolved.startswith('/') or resolved.startswith('//')
            or '\\' in resolved or '?' in resolved or '#' in resolved
            or re.search(r'[\x00-\x20\x7f]', resolved)
            or any(unquote(segment).casefold() in ('.', '..') for segment in resolved.split('/'))):
        raise ValueError('path 变量替换后不安全')
    return resolved


def select_value(response, selector):
    if selector in ('status_code', 'text'):
        return response[selector]
    if selector.startswith('headers.'):
        wanted = selector[8:].lower()
        return next((value for key, value in response['headers'].items() if key.lower() == wanted), MISSING)
    current = response['body']
    if selector == 'body':
        return current
    for name, index in re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]', selector[4:]):
        if name:
            if type(current) is not dict or name not in current:
                return MISSING
            current = current[name]
        else:
            position = int(index)
            if type(current) is not list or position >= len(current):
                return MISSING
            current = current[position]
    return current


def _same(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    return left == right


def compare_value(actual, comparator, expected):
    if comparator == 'exists':
        return (actual is not MISSING) is expected, 'assertion_failed'
    if actual is MISSING:
        return False, 'missing_value'
    if comparator in ('eq', 'ne'):
        equal = _same(actual, expected)
        return (equal if comparator == 'eq' else not equal), 'assertion_failed'
    if comparator in ('gt', 'ge', 'lt', 'le'):
        if type(actual) not in (int, float):
            return False, 'type_mismatch'
        operations = {'gt': actual > expected, 'ge': actual >= expected,
                      'lt': actual < expected, 'le': actual <= expected}
        return operations[comparator], 'assertion_failed'
    if comparator in ('length', 'length_gt'):
        if type(actual) not in (str, list, dict):
            return False, 'type_mismatch'
        return (len(actual) == expected if comparator == 'length' else len(actual) > expected), 'assertion_failed'
    if comparator == 'type':
        matches = {'null': actual is None, 'boolean': type(actual) is bool,
                   'number': type(actual) in (int, float), 'string': type(actual) is str,
                   'object': type(actual) is dict, 'list': type(actual) is list}
        return matches[expected], 'assertion_failed'
    if comparator in ('contains', 'not_contains'):
        if type(actual) is str and type(expected) is str:
            contains = expected in actual
        elif type(actual) is list:
            contains = any(_same(item, expected) for item in actual)
        elif type(actual) is dict and type(expected) is str:
            contains = expected in actual
        else:
            return False, 'type_mismatch'
        return (contains if comparator == 'contains' else not contains), 'assertion_failed'
    return False, 'unsupported_comparator'


def _bounded(value):
    if value is MISSING:
        return {'missing': True}
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        encoded = json.dumps(str(value), ensure_ascii=False)
    raw = encoded.encode('utf-8')
    if len(raw) <= MAX_FAILURE_VALUE_BYTES:
        return value
    return raw[:MAX_FAILURE_VALUE_BYTES].decode('utf-8', errors='ignore') + '…'


def assertion_failures(step, step_index, response):
    failures = []
    for assertion in step['assertions']:
        actual = select_value(response, assertion['check'])
        passed, error_type = compare_value(actual, assertion['comparator'], assertion['expected'])
        if not passed:
            failures.append({
                'step_index': step_index, 'step_name': step['name'], 'phase': step['phase'],
                'check': assertion['check'], 'comparator': assertion['comparator'],
                'expected': _bounded(assertion['expected']), 'actual': _bounded(actual),
                'error_type': error_type, 'message': '响应断言未通过',
            })
    return failures


def add_failure_samples(failures, seen, items):
    for item in items:
        if len(failures) >= MAX_FAILURE_SAMPLES:
            break
        try:
            key = json.dumps(item, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            failures.append(item)


# This file is the standalone, byte-checked worker template. Keep diagnostics
# stdlib-only. Step details preserve original values; shared failure summaries
# remain redacted before entering Locust's reporting transport.
REDACTED = '<redacted>'
EVIDENCE_SECRET = re.compile(r'authorization|cookie|token|secret|password|passwd|pwd|api[_-]?key|private[_-]?key|session|credential|csrf|xsrf|otp|密码|口令|密钥', re.I)
EVIDENCE_ASSIGNMENT = re.compile(
    r'''(?i)((?:["']?)(?:authorization|cookie|token|secret|password|passwd|pwd|api[_-]?key|private[_-]?key|session|credential|csrf|xsrf|otp|密码|口令|密钥)[\w-]*["']?\s*[:：=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s&,;<>]+)''')


def evidence_secrets(value, key='', found=None, depth=0):
    found = set() if found is None else found
    if depth > 12:
        return found
    if isinstance(value, dict):
        for name, item in value.items():
            evidence_secrets(item, key if EVIDENCE_SECRET.search(key) else str(name), found, depth + 1)
    elif isinstance(value, list):
        for item in value:
            evidence_secrets(item, key, found, depth + 1)
    elif EVIDENCE_SECRET.search(key) and value is not None and str(value):
        text = str(value)
        found.add(text)
        if key.lower() in ('authorization', 'proxy-authorization'):
            found.add(text.partition(' ')[2] or text)
        if 'cookie' in key.lower():
            for part in text.split(';'):
                name, separator, item = part.strip().partition('=')
                if separator and name.lower() not in ('path', 'domain', 'samesite', 'expires', 'max-age') and item:
                    found.add(item)
    if isinstance(value, str):
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    evidence_secrets(parsed, key, found, depth + 1)
        for match in EVIDENCE_ASSIGNMENT.finditer(value):
            secret = match.group(2).strip('"\'')
            if secret:
                found.add(secret)
        for match in re.finditer(r'(?i)\b(?:Bearer|Basic)\s+([A-Za-z0-9._~+/=-]+)', value):
            found.add(match.group(1))
    return found


def format_evidence(value, secrets=(), key='', depth=0, *, redact=False):
    if value is MISSING:
        return {'missing': True}
    if redact and EVIDENCE_SECRET.search(key):
        return REDACTED
    if depth > 12:
        return '[嵌套内容已截断]'
    if isinstance(value, dict):
        result = {str(name)[:256]: format_evidence(item, secrets, str(name), depth + 1, redact=redact)
                  for name, item in list(value.items())[:200]}
        if len(value) > 200:
            result['…'] = '[对象内容已截断]'
        return result
    if isinstance(value, list):
        result = [format_evidence(item, secrets, depth=depth + 1, redact=redact) for item in value[:200]]
        return result + (['[列表内容已截断]'] if len(value) > 200 else [])
    if redact and isinstance(value, str):
        text = value
        if text.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    return json.dumps(format_evidence(parsed, secrets, depth=depth + 1, redact=True), ensure_ascii=False, indent=2)
        for secret in sorted(secrets, key=len, reverse=True):
            if secret and secret != REDACTED:
                for variant in {secret, quote(secret, safe=''), quote(secret, safe='').replace('%20', '+')}:
                    text = text.replace(variant, REDACTED)
        text = EVIDENCE_ASSIGNMENT.sub(
            lambda match: match.group(1) + (json.dumps(REDACTED) if match.group(2).startswith('"') else REDACTED), text)
        text = re.sub(r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', REDACTED, text)
        text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', REDACTED, text)
        return text
    if redact and value is not None and str(value) in secrets:
        return REDACTED
    return value


def evidence_preview(value, limit=8192, *, typed=False):
    safe = format_evidence(value)
    text = safe if isinstance(safe, str) and not typed else json.dumps(safe, ensure_ascii=False, indent=2, default=str)
    encoded = text.encode('utf-8')
    return {'content': encoded[:limit].decode('utf-8', errors='ignore'),
            'truncated': len(encoded) > limit or any(marker in text for marker in
                ('[嵌套内容已截断]', '[对象内容已截断]', '[列表内容已截断]'))}


def normalize_validation_steps(value):
    """Allow only the report contract; cap 20 steps and every displayed value."""
    if not isinstance(value, list):
        return []
    def text(item, limit=256):
        return str(format_evidence(item)).encode('utf-8')[:limit].decode('utf-8', errors='ignore')
    def preview(item, limit=8192):
        if not isinstance(item, dict) or not isinstance(item.get('content'), str):
            return {'content': '', 'truncated': False}
        result = evidence_preview(str(item.get('content', '')), limit=limit)
        result['truncated'] |= item.get('truncated') is True
        return result
    def state(item):
        return item if item in ('pending', 'running', 'passed', 'failed', 'skipped') else 'pending'
    result = []
    seen = set()
    for item in value[:20]:
        if (not isinstance(item, dict) or type(item.get('step_index')) is not int
                or not 1 <= item['step_index'] <= 20 or item['step_index'] in seen):
            continue
        seen.add(item['step_index'])
        row = {key: text(item.get(key, ''), limit) for key, limit in
               (('step_name', 200), ('method', 10), ('message', 500))}
        elapsed = item.get('elapsed_ms')
        row.update(step_index=item['step_index'], phase='setup' if item.get('phase') == 'setup' else 'main',
                   status=state(item.get('status')), elapsed_ms=round(elapsed, 2) if type(elapsed) in (int, float) and 0 <= elapsed <= 86400000 else None,
                   request=None, response=None, assertions=[], extractions=[])
        request = item.get('request')
        if isinstance(request, dict):
            row['request'] = {'url': text(request.get('url', ''), 4096),
                              'headers': preview(request.get('headers'), 2048),
                              'body': preview(request.get('body')),
                              'body_type': text(request.get('body_type', ''), 10)}
        response = item.get('response')
        if isinstance(response, dict):
            status = response.get('status_code')
            row['response'] = {'status_code': status if type(status) is int and 100 <= status <= 599 else None,
                               'headers': preview(response.get('headers'), 2048),
                               'body': preview(response.get('body')),
                               'incomplete': response.get('incomplete') is True}
        for kind in ('assertions', 'extractions'):
            checks = item.get(kind)
            if not isinstance(checks, list):
                continue
            for index, check in enumerate(checks[:50], 1):
                if not isinstance(check, dict):
                    continue
                normalized = {'index': index, 'check': text(check.get('check', ''), 512),
                              'status': state(check.get('status')), 'message': text(check.get('message', ''), 200)}
                if kind == 'assertions':
                    normalized.update(comparator=text(check.get('comparator', ''), 32),
                                      error_type=text(check.get('error_type', ''), 64),
                                      expected=preview(check.get('expected'), 384), actual=preview(check.get('actual'), 384))
                else:
                    normalized.update(name=text(check.get('name', ''), 64), value=preview(check.get('value'), 384))
                row[kind].append(normalized)
        # Preserve every check/status even on a worst-case 50+50-check step.
        if len(json.dumps(row, ensure_ascii=False).encode('utf-8')) > 48 * 1024:
            for payload in (row['request'], row['response']):
                if payload:
                    for key in ('headers', 'body'):
                        payload[key] = preview(payload[key], 1024)
            for check in row['assertions'] + row['extractions']:
                check['check'] = text(check['check'], 128)
                check['message'] = text(check['message'], 64)
                for key in ('expected', 'actual', 'value'):
                    if key in check:
                        check[key] = preview(check[key], 64)
        result.append(row)
    return result


class ValidationTrace:
    def __init__(self, snapshot):
        self.enabled = snapshot['mode'] == 'validation'
        self.secrets = evidence_secrets(snapshot.get('variables', {}))
        self.steps = []
        if self.enabled:
            for index, step in enumerate(snapshot['steps'], 1):
                self.steps.append({'step_index': index, 'step_name': step['name'], 'phase': step['phase'],
                                   'method': step['method'], 'status': 'pending', 'message': '',
                                   'elapsed_ms': None, 'request': None, 'response': None,
                                   'assertions': [], 'extractions': []})

    def learn(self, *values):
        for value in values:
            evidence_secrets(value, found=self.secrets)

    def request(self, index, step, url, headers, body):
        if not self.enabled:
            return
        self.learn(headers, body)
        row = self.steps[index - 1]
        row.update(status='running', request={'url': url,
            'headers': evidence_preview(headers, limit=2048),
            'body': evidence_preview(body), 'body_type': step['body_type']})

    def finish_step(self, index, step, response=None, *, failures=(), extracted=None, elapsed=None, incomplete=False):
        if not self.enabled:
            return
        row = self.steps[index - 1]
        row.update(status='failed' if failures else 'passed', elapsed_ms=elapsed,
                   message='；'.join(dict.fromkeys(
                       f'{item["message"]}（{item["check"]}）' if item.get('check') else item['message']
                       for item in failures)))
        if response is not None:
            self.learn(response.get('headers', {}), response.get('body', {}), extracted or {})
            row['response'] = {'status_code': response['status_code'],
                'headers': evidence_preview(response['headers'], limit=2048),
                'body': evidence_preview(response['body'] if response['body'] is not MISSING else response['text']),
                'incomplete': incomplete}
        evaluable = response is not None and not incomplete
        row['assertions'] = []
        for assertion in step['assertions']:
            actual = select_value(response, assertion['check']) if evaluable else MISSING
            passed, error = compare_value(actual, assertion['comparator'], assertion['expected']) if evaluable else (False, '')
            row['assertions'].append({**assertion, 'expected': evidence_preview(assertion['expected'], 384, typed=True),
                'actual': evidence_preview(actual, 384, typed=True) if evaluable else evidence_preview(MISSING),
                'status': ('passed' if passed else 'failed') if evaluable else 'skipped',
                'error_type': error if evaluable and not passed else '',
                'message': ('响应断言未通过' if not passed else '') if evaluable else '请求未获得完整响应，未检查断言'})
        assertions_passed = evaluable and all(item['status'] == 'passed' for item in row['assertions'])
        for item in step['extract']:
            actual = select_value(response, item['check']) if assertions_passed else MISSING
            status = 'skipped' if not assertions_passed else ('failed' if actual is MISSING else 'passed')
            row['extractions'].append({**item, 'status': status,
                'value': evidence_preview(actual, 384, typed=True),
                'message': ('本步骤未通过，提取值未交给后续步骤' if failures and status == 'passed'
                            else '提取字段不存在' if status == 'failed'
                            else '断言未通过或未取得响应，未提取' if status == 'skipped' else '')})

    def finish(self):
        failed = next((row['step_index'] for row in self.steps if row['status'] == 'failed'), None)
        for row in self.steps:
            if row['status'] == 'pending':
                row.update(status='skipped', message=f'第 {failed} 步失败，后续步骤未执行' if failed else '本轮未执行该步骤')

    def report(self):
        return normalize_validation_steps(self.steps) if self.enabled else []


def main():
    # Locust/gevent stay process-local; importing this frozen contract is stdlib-only.
    from gevent import monkey
    monkey.patch_all()
    import argparse
    import copy
    from datetime import datetime, timezone
    import hmac
    from importlib.metadata import version
    import os
    from pathlib import Path
    import signal
    import tempfile
    import time
    import gevent
    from locust import HttpUser, task, constant
    from locust.env import Environment
    from locust.event import Events
    from locust.exception import StopUser
    from locust.dispatch import UsersDispatcher
    from locust.stats import RequestStats, StatsEntry, StatsError
    from requests.exceptions import RequestException

    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('master', 'worker'), required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--master-host', default='127.0.0.1')
    parser.add_argument('--master-port', type=int, required=True)
    parser.add_argument('--node-id')
    parser.add_argument('--start-file')
    parser.add_argument('--metrics-file')
    parser.add_argument('--complete-file')
    args = parser.parse_args()
    if version('locust') != ENGINE_VERSION or args.master_host != '127.0.0.1':
        raise ValueError('引擎版本或本地转接地址无效')
    configuration = json.loads(Path(args.config).read_text(encoding='utf-8'))
    snapshot = validate_snapshot(configuration['snapshot'])
    snapshot_sha256 = canonical_sha256(snapshot)
    assignments = {item['node_id']: item['users'] for item in snapshot['nodes']}
    if args.role == 'worker':
        if set(configuration) != {'snapshot', 'handshake_token'} or args.node_id not in assignments:
            raise ValueError('Worker 运行身份配置无效')
        runtime_node_id = str(uuid.UUID(args.node_id))
        handshake_token = configuration['handshake_token']
        if type(handshake_token) is not str or len(handshake_token) < 32:
            raise ValueError('运行握手凭证无效')
        hello = {'run_id': snapshot['run_id'], 'node_id': runtime_node_id,
                 'snapshot_sha256': snapshot_sha256, 'token': handshake_token}
    else:
        if set(configuration) != {'snapshot', 'handshake_tokens'} or args.node_id is not None:
            raise ValueError('Master 运行身份配置无效')
        handshake_tokens = configuration['handshake_tokens']
        if (type(handshake_tokens) is not dict or set(handshake_tokens) != set(assignments)
                or any(type(token) is not str or len(token) < 32 for token in handshake_tokens.values())):
            raise ValueError('Master 握手白名单无效')
        runtime_node_id = None
    failures = []
    failure_keys = set()
    trace = ValidationTrace(snapshot)
    validation = {'finished': False, 'passed': False, 'main_steps_completed': 0,
                  'main_steps_total': sum(step['phase'] == 'main' for step in snapshot['steps'])}
    user_sequence = [0]
    stop_requested = [False]

    def add_failures(items):
        if trace.enabled:
            items = [{**item,
                      'actual': format_evidence(item.get('actual'), trace.secrets, item.get('check', ''), redact=True),
                      'expected': format_evidence(item.get('expected'), trace.secrets,
                          item.get('check', '') if item.get('comparator') in ('eq', 'ne', 'contains', 'not_contains') else '', redact=True)}
                     for item in items]
        add_failure_samples(failures, failure_keys, items)

    class RequestUser(HttpUser):
        host = snapshot['base_url'].rstrip('/')
        wait_time = constant(snapshot['wait_seconds'])

        def on_start(self):
            self.client.trust_env = False
            user_sequence[0] += 1
            self.platform_user = user_sequence[0]
            self.setup_variables = dict(snapshot['variables'])
            self.round_number = 0
            for index, step in enumerate(snapshot['steps']):
                if step['phase'] != 'setup':
                    break
                if stop_requested[0]:
                    raise StopUser()
                succeeded, extracted = self.execute_step(step, index + 1, self.setup_variables)
                if not succeeded:
                    if snapshot['mode'] == 'validation':
                        self.finish_validation(False)
                    raise StopUser()
                self.setup_variables.update(extracted)

        def finish_validation(self, passed):
            if validation['finished']:
                return
            validation['finished'] = True
            validation['passed'] = bool(passed)
            trace.finish()
            runner = self.environment.runner
            if hasattr(runner, '_send_stats'):
                runner._send_stats()

        def execute_step(self, step, step_index, variables):
            try:
                path = resolve_path(step['path'], variables)
                query = resolve_value(step['query'], variables)
                headers = resolve_value(step['headers'], variables)
                body = resolve_value(step['body'], variables)
                _validate_mapping(query)
                if type(headers) is not dict or any(
                    type(key) is not str or type(item) is not str
                    for key, item in headers.items()
                ):
                    raise ValueError('headers 变量替换后必须是字符串映射')
                if step['body_type'] == 'form':
                    _validate_mapping(body)
                if step['body_type'] == 'raw' and type(body) is not str:
                    raise ValueError('raw 变量替换后必须是字符串')
            except (KeyError, TypeError, ValueError) as exc:
                error_type = 'undefined_variable' if isinstance(exc, KeyError) else 'request_render_error'
                message = f'变量未定义：{exc.args[0]}' if isinstance(exc, KeyError) else '请求参数变量替换后类型或路径无效'
                sample = {'step_index': step_index, 'step_name': step['name'], 'phase': step['phase'],
                    'check': 'variables', 'comparator': 'exists', 'expected': True,
                    'actual': {'missing': True}, 'error_type': error_type, 'message': message}
                add_failures([sample])
                trace.finish_step(step_index, step, failures=[sample])
                self.environment.events.request.fire(
                    request_type=step['method'], name=f'{step_index}. {step["name"]}',
                    response_time=0, response_length=0, exception=ValueError(message),
                    context={}, response=None, url=self.host + step['path'],
                    start_time=time.time(),
                )
                return False, {}
            request_options = {'params': query, 'headers': headers,
                               'timeout': (snapshot['connect_timeout_seconds'],
                                           snapshot['read_timeout_seconds']),
                               'stream': True, 'allow_redirects': False, 'catch_response': True}
            if step['body_type'] == 'json':
                request_options['json'] = body
            elif step['body_type'] in ('form', 'raw'):
                request_options['data'] = body
            if trace.enabled:
                trace.learn(variables, query, headers, body)
                query_string = urlencode({key: value for key, value in query.items() if value is not None}, doseq=True)
                trace.request(step_index, step, self.host + path + ('?' + query_string if query_string else ''), headers, body)
            started = time.perf_counter()
            try:
                with self.client.request(step['method'], self.host + path,
                                         name=f'{step_index}. {step["name"]}', **request_options) as result:
                    payload = bytearray()
                    status_code = getattr(result, 'status_code', None)
                    network_failure = None
                    result_error = getattr(result, 'error', None)
                    if result_error is not None or type(status_code) is not int or status_code <= 0:
                        error_type, message = classify_network_exception(result_error)
                        network_failure = {'step_index': step_index, 'step_name': step['name'],
                            'phase': step['phase'], 'check': 'network', 'comparator': 'eq',
                            'expected': 'success', 'actual': 'request_failed',
                            'error_type': error_type, 'message': message}
                    try:
                        if network_failure is None:
                            for chunk in result.iter_content(65536):
                                payload.extend(chunk)
                                if len(payload) > MAX_RESPONSE_BYTES:
                                    network_failure = {'step_index': step_index, 'step_name': step['name'],
                                        'phase': step['phase'], 'check': 'body', 'comparator': 'length',
                                        'expected': MAX_RESPONSE_BYTES, 'actual': len(payload),
                                        'error_type': 'response_too_large', 'message': '响应内容超过 1 MiB 限制'}
                                    break
                    except RequestException as exc:
                        error_type, message = classify_network_exception(exc)
                        network_failure = {'step_index': step_index, 'step_name': step['name'],
                            'phase': step['phase'], 'check': 'network', 'comparator': 'eq',
                            'expected': 'success', 'actual': 'read_failed',
                            'error_type': error_type, 'message': message}
                    network_elapsed = (time.perf_counter() - started) * 1000
                    result.request_meta['response_time'] = network_elapsed
                    result.request_meta['response_length'] = min(len(payload), MAX_RESPONSE_BYTES)
                    text = bytes(payload[:MAX_RESPONSE_BYTES]).decode(result.encoding or 'utf-8', errors='replace')
                    try:
                        response_body = json.loads(text) if text else MISSING
                    except (TypeError, ValueError, json.JSONDecodeError):
                        response_body = MISSING
                    response = {'status_code': status_code, 'headers': dict(result.headers),
                                'text': text, 'body': response_body}
                    if trace.enabled:
                        trace.learn(response['headers'], response_body)
                        prepared = getattr(result, 'request', None)
                        if prepared is not None:
                            trace.request(step_index, step, prepared.url, dict(prepared.headers), body)
                    step_failures = [network_failure] if network_failure else assertion_failures(step, step_index, response)
                    extracted = {}
                    if not step_failures:
                        for item in step['extract']:
                            actual = select_value(response, item['check'])
                            if actual is MISSING:
                                step_failures.append({'step_index': step_index, 'step_name': step['name'],
                                    'phase': step['phase'], 'check': item['check'], 'comparator': 'extract',
                                    'expected': 'present', 'actual': {'missing': True},
                                    'error_type': 'extraction_missing', 'message': '响应提取字段不存在'})
                            else:
                                extracted[item['name']] = actual
                    trace.finish_step(step_index, step, response, failures=step_failures, extracted=extracted,
                                      elapsed=network_elapsed, incomplete=network_failure is not None)
                    if step_failures:
                        add_failures(step_failures)
                        result.failure('; '.join(item['message'] for item in step_failures)[:1024])
                        return False, {}
                    result.success()
                    return True, extracted
            except RequestException as exc:
                error_type, message = classify_network_exception(exc)
                failed = {'step_index': step_index, 'step_name': step['name'], 'phase': step['phase'],
                    'check': 'network', 'comparator': 'eq', 'expected': 'success', 'actual': 'request_failed',
                    'error_type': error_type, 'message': message}
                add_failures([failed])
                trace.finish_step(step_index, step, failures=[failed], elapsed=(time.perf_counter() - started) * 1000)
                return False, {}

        @task
        def scenario(self):
            self.round_number += 1
            variables = dict(snapshot['variables'])
            variables.update(self.setup_variables)
            for item in snapshot['unique_variables']:
                variables[item['name']] = (
                    f'{item["prefix"]}{snapshot["run_id"][:8]}-{runtime_node_id[:8]}-u{self.platform_user}'
                    f'-r{self.round_number}-{uuid.uuid4().hex[:12]}'
                )
            completed = 0
            for index, step in enumerate(snapshot['steps']):
                if step['phase'] != 'main':
                    continue
                if stop_requested[0]:
                    raise StopUser()
                succeeded, extracted = self.execute_step(step, index + 1, variables)
                if not succeeded:
                    if snapshot['mode'] == 'validation':
                        validation['main_steps_completed'] = completed
                        self.finish_validation(False)
                        raise StopUser()
                    return
                variables.update(extracted)
                completed += 1
            if snapshot['mode'] == 'validation':
                validation['main_steps_completed'] = completed
                self.finish_validation(completed == validation['main_steps_total'])
                raise StopUser()

    environment = Environment(user_classes=[RequestUser], host=RequestUser.host, events=Events(), stop_timeout=10)
    stop = [False]
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
    if args.role == 'worker':
        runner = environment.create_worker_runner(args.master_host, args.master_port)
        finalizing = [False]
        report_state = {'sequence': 0, 'final_sequence': None, 'final_payload': None}

        def worker_report(client_id, data, **_):
            if report_state['final_payload'] is not None:
                data.clear()
                data.update(copy.deepcopy(report_state['final_payload']))
                return
            report_state['sequence'] += 1
            is_final = bool(runner.state == 'stopped' and runner.user_count == 0)
            final_sequence = report_state['sequence'] if is_final else None
            data.update(platform_node_id=runtime_node_id,
                        platform_worker_instance_id=runner.client_id,
                        platform_report_seq=report_state['sequence'],
                        platform_final_seq=final_sequence,
                        platform_failure_samples=failures[:MAX_FAILURE_SAMPLES],
                        platform_validation=dict(validation),
                        platform_worker_cpu=float(runner.current_cpu_usage),
                        platform_worker_memory=int(runner.current_memory_usage),
                        platform_reason_code='stopped' if is_final else '',
                        platform_reason='Worker 已停止' if is_final else '')
            if trace.enabled:
                data['platform_validation_steps'] = trace.report()
            if is_final:
                report_state['final_sequence'] = final_sequence
                report_state['final_payload'] = copy.deepcopy(data)

        environment.events.report_to_master.add_listener(worker_report)
        environment.events.test_stop.add_listener(lambda **_: runner._send_stats())
        hello['worker_instance_id'] = runner.client_id
        runner.send_message('platform_hello', hello)
        original_handle_message = runner.handle_message
        last_spawn_users = [0]

        def reject_spawn(reason_code):
            stop_requested[0] = True
            finalizing[0] = True
            runner.stop()
            if report_state['final_payload'] is None:
                runner._send_stats()
            runner.send_message('platform_protocol_error', {
                'node_id': runtime_node_id, 'reason_code': reason_code,
            })

        def fixed_handle_message(msg):
            if msg.type == 'spawn':
                counts = msg.data.get('user_classes_count') if type(msg.data) is dict else None
                count = counts.get('RequestUser') if type(counts) is dict else None
                if (stop_requested[0] or report_state['final_payload'] is not None
                        or type(counts) is not dict or set(counts) != {'RequestUser'}
                        or type(count) is not int or not last_spawn_users[0] <= count <= assignments[runtime_node_id]):
                    reject_spawn('assigned_users_mismatch')
                    return
                last_spawn_users[0] = count
            elif msg.type == 'spawning_complete':
                if last_spawn_users[0] != assignments[runtime_node_id]:
                    reject_spawn('final_assigned_users_mismatch')
                    return
            elif msg.type == 'stop':
                stop_requested[0] = True
                finalizing[0] = True
                runner.stop()
                if report_state['final_payload'] is None:
                    runner._send_stats()
                runner.worker_state = 'stopped'
                runner.send_message('platform_stopped', {
                    'node_id': runtime_node_id, 'final_seq': report_state['final_sequence'],
                })
                return
            original_handle_message(msg)

        runner.handle_message = fixed_handle_message
        while not stop[0] and len(runner.greenlet):
            gevent.sleep(.2)
        if stop[0] and len(runner.greenlet):
            stop_requested[0] = True
            finalizing[0] = True
            runner.stop()
            if report_state['final_payload'] is None:
                runner._send_stats()
        runner.quit()
        return 0

    if not all((args.start_file, args.metrics_file, args.complete_file)):
        raise ValueError('Master 缺少运行文件配置')

    expected_node_ids = set(assignments)
    client_to_node = {}
    node_to_client = {}
    member_changed = [False]
    stopping = [False]

    class FixedMembersDispatcher(UsersDispatcher):
        def _sort_workers(self):
            if any(worker.id not in client_to_node for worker in self._worker_nodes):
                raise ValueError('存在未准入 Worker')
            self._worker_nodes = sorted(self._worker_nodes, key=lambda worker: client_to_node[worker.id])
            for worker in self._worker_nodes:
                worker._index_within_host = 0

        def add_worker(self, worker_node):
            if not stopping[0]:
                member_changed[0] = True

        def remove_worker(self, worker_node):
            if not stopping[0]:
                member_changed[0] = True

    environment.dispatcher_class = FixedMembersDispatcher
    runner = environment.create_master_runner('127.0.0.1', args.master_port)
    admitted = set()
    invalid = [False]
    invalid_reason = ['worker_identity_invalid']
    master_validation = dict(validation)
    master_steps = trace.report()
    node_reports = {
        node_id: {
            'stats': RequestStats(), 'sequence': ReportSequence(),
            'protocol_error': False, 'users': 0, 'worker_cpu': None,
            'worker_memory': None, 'status': 'waiting', 'reason_code': '', 'reason': '',
        }
        for node_id in assignments
    }
    started_at = None
    metrics_cutoff = None
    reason = 'completed'
    phase = ['preparing']
    spawn_greenlet = [None]

    def on_hello(environment, msg, **_):
        data = msg.data
        if type(data) is not dict or set(data) != {
            'run_id', 'node_id', 'snapshot_sha256', 'token', 'worker_instance_id',
        }:
            invalid[0] = True
            return
        node_id = data.get('node_id')
        expected_token = handshake_tokens.get(node_id)
        valid = (
            data.get('run_id') == snapshot['run_id']
            and data.get('snapshot_sha256') == snapshot_sha256
            and data.get('worker_instance_id') == msg.node_id
            and expected_token is not None
            and hmac.compare_digest(data.get('token', ''), expected_token)
            and (node_id not in node_to_client or node_to_client[node_id] == msg.node_id)
            and (msg.node_id not in client_to_node or client_to_node[msg.node_id] == node_id)
        )
        if not valid or started_at is not None:
            invalid[0] = True
            return
        client_to_node[msg.node_id] = node_id
        node_to_client[node_id] = msg.node_id
        admitted.add(node_id)
        node_reports[node_id]['status'] = 'ready'

    def merge_stats(target, entries, total, errors):
        for entry in entries:
            key = (entry.name, entry.method)
            if key not in target.entries:
                target.entries[key] = StatsEntry(target, entry.name, entry.method, use_response_times_cache=True)
            target.entries[key].extend(entry)
        target.total.extend(total)
        for error_key, error in errors.items():
            if error_key not in target.errors:
                target.errors[error_key] = StatsError.unserialize(error.serialize())
            else:
                target.errors[error_key].occurrences += error.occurrences

    def fail_protocol(node_id, code, message):
        if not invalid[0]:
            invalid_reason[0] = code
        invalid[0] = True
        if node_id in node_reports:
            state = node_reports[node_id]
            state.update(protocol_error=True, status='failed', reason_code=code, reason=message)

    def on_report(client_id, data, **_):
        node_id = client_to_node.get(client_id)
        if node_id is None or type(data) is not dict:
            fail_protocol(node_id, 'unadmitted_report', '未准入 Worker 上报统计')
            return
        state = node_reports[node_id]
        try:
            if data.get('platform_node_id') != node_id or data.get('platform_worker_instance_id') != client_id:
                raise ValueError('统计身份不匹配')
            sequence = data.get('platform_report_seq')
            final_sequence = data.get('platform_final_seq')
            digest = canonical_sha256(data)
            accepted = state['sequence'].accept(sequence, final_sequence, digest)
            if not accepted:
                return
            if state['sequence'].gap:
                state.update(reason_code='report_sequence_gap', reason='统计批次序号存在缺口')
            entries = [StatsEntry.unserialize(item) for item in data['stats']]
            total = StatsEntry.unserialize(data['stats_total'])
            errors = {key: StatsError.unserialize(item) for key, item in data['errors'].items()}
            user_classes_count = data['user_classes_count']
            user_count = data['user_count']
            if (type(user_classes_count) is not dict or type(user_count) is not int or user_count < 0
                    or any(type(value) is not int or value < 0 for value in user_classes_count.values())
                    or sum(user_classes_count.values()) != user_count):
                raise ValueError('Worker 用户数无效')
            worker_cpu = data.get('platform_worker_cpu')
            worker_memory = data.get('platform_worker_memory')
            if type(worker_cpu) not in (int, float) or not math.isfinite(worker_cpu):
                worker_cpu = None
            if type(worker_memory) is not int or worker_memory < 0:
                worker_memory = None
        except (KeyError, TypeError, ValueError):
            fail_protocol(node_id, 'report_protocol_error', 'Worker 统计消息不符合协议')
            return
        merge_stats(environment.stats, entries, total, errors)
        merge_stats(state['stats'], entries, total, errors)
        state['users'] = user_count
        state['worker_cpu'] = worker_cpu
        state['worker_memory'] = worker_memory
        state['status'] = 'stopped' if final_sequence is not None else ('running' if started_at is not None else 'ready')
        if final_sequence is not None:
            state['reason_code'] = data.get('platform_reason_code') or state['reason_code']
            state['reason'] = data.get('platform_reason') or state['reason']
        if client_id in runner.clients:
            runner.clients[client_id].user_classes_count = dict(user_classes_count)
        samples = data.get('platform_failure_samples') or []
        if type(samples) is list:
            add_failures([{**item, 'node_id': node_id} for item in samples if type(item) is dict])
        reported_validation = data.get('platform_validation')
        if type(reported_validation) is dict and reported_validation.get('finished'):
            master_validation.update(reported_validation)
        if trace.enabled and isinstance(data.get('platform_validation_steps'), list):
            master_steps[:] = normalize_validation_steps(data['platform_validation_steps'])

    def on_protocol_error(environment, msg, **_):
        node_id = client_to_node.get(msg.node_id)
        fail_protocol(node_id, 'worker_protocol_error', 'Worker 拒绝了分配命令')

    def on_stopped(environment, msg, **_):
        node_id = client_to_node.get(msg.node_id)
        if node_id is None or type(msg.data) is not dict or msg.data.get('node_id') != node_id:
            fail_protocol(node_id, 'worker_stop_identity_invalid', 'Worker 停止确认身份无效')

    runner.register_message('platform_hello', on_hello)
    runner.register_message('platform_protocol_error', on_protocol_error)
    runner.register_message('platform_stopped', on_stopped)
    # The pinned Locust listener merges before later application listeners. Replace
    # that entry point so identity and monotonic sequence checks happen first.
    environment.events.worker_report._handlers[:] = [on_report]

    actual_runner_start = runner.start
    start_invoked = [False]

    def guarded_start(user_count, spawn_rate, wait=False, user_classes=None):
        if start_invoked[0]:
            if not stopping[0]:
                member_changed[0] = True
            return
        start_invoked[0] = True
        return actual_runner_start(user_count, spawn_rate, wait=wait, user_classes=user_classes)

    runner.start = guarded_start

    def write_json(filename, item):
        path = Path(filename)
        descriptor, temporary = tempfile.mkstemp(prefix='.metric-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(item, output, ensure_ascii=False, allow_nan=False)
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
        report_time = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        endpoint = metrics_cutoff if metrics_cutoff is not None else time.monotonic()
        elapsed = max(.001, endpoint - started_at) if started_at is not None else 0
        nodes = {}
        for node_id in sorted(assignments):
            state = node_reports[node_id]
            node_total = state['stats'].total
            node_complete = bool(
                state['sequence'].final is not None and state['sequence'].final == state['sequence'].last
                and not state['sequence'].gap and not state['protocol_error'] and state['users'] == 0
            )
            nodes[node_id] = {
                **entry_data(node_total),
                'rps': node_total.num_requests / elapsed if elapsed else 0,
                'error_rate': node_total.num_failures / node_total.num_requests if node_total.num_requests else 0,
                'users': state['users'], 'entries': [entry_data(item) for item in state['stats'].entries.values()],
                'assigned_users': assignments[node_id], 'worker_cpu': state['worker_cpu'],
                'worker_memory': state['worker_memory'], 'status': state['status'],
                'report_time': report_time,
                'complete': node_complete, 'report_seq': state['sequence'].last,
                'final_seq': state['sequence'].final, 'reason_code': state['reason_code'], 'reason': state['reason'],
            }
        result = {**entry_data(total), 'rps': total.num_requests / elapsed if elapsed else 0,
                  'error_rate': total.num_failures / total.num_requests if total.num_requests else 0,
                  'users': runner.user_count, 'elapsed_seconds': elapsed,
                  'worker_count': runner.worker_count, 'admitted_workers': len(admitted),
                  'admitted_node_ids': sorted(admitted), 'phase': phase[0], 'nodes': nodes,
                  'started': started_at is not None, 'complete': complete,
                  'failure_samples': failures[:MAX_FAILURE_SAMPLES],
                  'entries': [entry_data(item) for item in environment.stats.entries.values()]}
        if snapshot['mode'] == 'validation':
            result.update(validation_complete=bool(master_validation['finished']),
                          validation_passed=bool(master_validation['finished'] and master_validation['passed']),
                          main_steps_completed=master_validation['main_steps_completed'],
                          main_steps_total=master_validation['main_steps_total'],
                          validation_steps=master_steps)
        return result

    deadline = time.monotonic() + 60
    stop_file = Path(args.start_file + '.stop')
    last_write = 0
    try:
        while True:
            now = time.monotonic()
            if invalid[0]:
                reason = invalid_reason[0]
                break
            if stop[0] or stop_file.exists():
                reason = 'cancelled'
                break
            if started_at is None:
                if now > deadline:
                    reason = 'prepare_timeout'
                    break
                exact_clients = set(runner.clients) == set(client_to_node)
                if (admitted == expected_node_ids and exact_clients
                        and runner.worker_count == len(expected_node_ids) and Path(args.start_file).exists()):
                    started_at = time.monotonic()
                    phase[0] = 'running'
                    for state in node_reports.values():
                        state['status'] = 'running'
                    spawn_greenlet[0] = gevent.spawn(runner.start, snapshot['users'], snapshot['spawn_rate'])
            elif snapshot['mode'] == 'validation' and master_validation['finished']:
                break
            elif now - started_at >= snapshot['duration_seconds']:
                break
            elif member_changed[0] or set(runner.clients) != set(client_to_node) or runner.worker_count != len(expected_node_ids):
                reason = 'worker_lost'
                break
            if now - last_write >= 2:
                write_json(args.metrics_file, metrics())
                last_write = now
            gevent.sleep(.1)
        phase[0] = 'stopping'
        stopping[0] = True
        stop_requested[0] = True
        shutdown_deadline = time.monotonic() + 30
        if started_at is not None:
            if spawn_greenlet[0] is not None and not spawn_greenlet[0].ready():
                spawn_greenlet[0].kill(block=True)
            with gevent.Timeout(30, False):
                runner.stop()
        metrics_cutoff = time.monotonic()
        final_deadline = min(shutdown_deadline, time.monotonic() + 15)
        while (started_at is not None and time.monotonic() < final_deadline
               and any(node_reports[node_id]['sequence'].final is None for node_id in expected_node_ids)):
            gevent.sleep(.1)
        # quit asks Workers to resend their immutable final batch and keeps the
        # listener alive for Locust's bounded .5 second drain window.
        runner.quit()
        node_complete = all(
            node_reports[node_id]['sequence'].final == node_reports[node_id]['sequence'].last
            and node_reports[node_id]['sequence'].final is not None
            and not node_reports[node_id]['sequence'].gap and not node_reports[node_id]['protocol_error']
            and node_reports[node_id]['users'] == 0
            for node_id in expected_node_ids
        )
        complete = bool(started_at is not None and admitted == expected_node_ids and node_complete
                        and not invalid[0] and reason in ('completed', 'cancelled') and runner.user_count == 0)
        if invalid[0] and reason == 'completed':
            reason = invalid_reason[0]
        elif reason == 'completed' and not complete:
            reason = 'final_report_incomplete'
        for node_id, state in node_reports.items():
            if state['sequence'].final is None and started_at is not None:
                state.update(status='lost' if reason == 'worker_lost' else 'incomplete',
                             reason_code=state['reason_code'] or 'final_report_missing',
                             reason=state['reason'] or '未收到最终统计批次')
                if reason == 'worker_lost':
                    state['users'] = None
        phase[0] = 'finished'
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
        raise SystemExit('运行配置无效，执行已停止')
