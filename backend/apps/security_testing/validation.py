"""Strict, database-independent input contracts for bounded security checks."""
import math
import re
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

CHECKS = frozenset({'authentication', 'authorization', 'information', 'configuration'})
_HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SELECTOR = re.compile(r'^body(?:\.[A-Za-z_][A-Za-z0-9_-]{0,63}|\[(?:0|[1-9][0-9]{0,3})\]){0,12}$')
_SENSITIVE = re.compile(r'auth|cookie|token|password|passwd|secret|credential|api[-_]?key|session|access[-_]?key|密码|口令|令牌|密钥', re.I)
_FORBIDDEN_HEADERS = frozenset({'host', 'connection', 'content-length', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'expect', 'forwarded', 'via', 'origin', 'referer', 'x-http-method-override', 'x-http-method', 'x-method-override'})


def sensitive_key(value):
    return bool(_SENSITIVE.search(str(value)))


def _object(value, allowed, label):
    if type(value) is not dict or any(type(key) is not str for key in value) or set(value) - set(allowed):
        raise ValueError(f'{label}格式或字段不正确')
    return value


def _text(value, label, maximum, empty=False):
    if type(value) is not str or len(value) > maximum or (not empty and not value.strip()) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f'{label}必须是有效文本')
    return value


def normalize_url(value, origin_only=False):
    _text(value, 'URL', 4096)
    if '\\' in value or any(char in unquote(value) for char in '{}'):
        raise ValueError('URL不能包含反斜杠或模板占位符')
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or '#' in value:
            raise ValueError
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if not re.fullmatch(r'[a-z0-9.:-]+', host) or '%' in host or '..' in host:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('URL必须是有效HTTP(S)地址，且不含用户信息或片段') from None
    scheme = parsed.scheme.lower()
    host = f'[{host}]' if ':' in host else host
    netloc = host + (f':{port}' if port is not None and port != (443 if scheme == 'https' else 80) else '')
    origin = f'{scheme}://{netloc}'
    if origin_only:
        if parsed.path not in ('', '/') or parsed.query or '?' in value:
            raise ValueError('允许访问的源只能包含协议、主机和端口')
        return origin
    return urlunsplit((scheme, netloc, parsed.path or '/', parsed.query, ''))


def origin_of(url):
    parsed = urlsplit(url)
    return f'{parsed.scheme}://{parsed.netloc}'


def validate_headers(value, identity=False):
    if type(value) is not dict or len(value) > 30:
        raise ValueError('请求头必须是对象，且最多30项')
    output, seen = {}, set()
    for name, content in value.items():
        _text(name, '请求头名称', 128)
        _text(content, '请求头值', 4096, empty=True)
        lowered = name.lower()
        if not _HEADER.fullmatch(name) or lowered in seen:
            raise ValueError('请求头名称无效或重复')
        if lowered in _FORBIDDEN_HEADERS or lowered.startswith(('proxy-', 'x-forwarded-', 'x-original-', 'x-rewrite-', 'sec-')) or lowered in ('x-real-ip', 'x-original-url', 'x-rewrite-url'):
            raise ValueError('请求头包含不允许自定义的连接、代理或来源字段')
        if not identity and sensitive_key(name):
            raise ValueError('认证请求头只能配置在测试账号中')
        # http.client uses Latin-1 for HTTP/1 header values.
        try:
            content.encode('latin-1')
        except UnicodeEncodeError:
            raise ValueError('请求头值需使用HTTP可传输字符，中文请先编码') from None
        seen.add(lowered)
        output[name] = content
    return output


def _number(value, minimum, maximum, label, integer=False):
    if type(value) not in ((int,) if integer else (int, float)):
        raise ValueError(f'{label}必须为数字')
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{label}须在{minimum}至{maximum}之间')
    return value


def validate_config(value):
    _object(value, {'allowed_origins', 'allow_private_network', 'endpoints', 'checks', 'max_requests', 'rate_per_second', 'timeout_seconds'}, '测试配置')
    raw_origins = value.get('allowed_origins')
    if type(raw_origins) is not list or not 1 <= len(raw_origins) <= 20:
        raise ValueError('请配置1至20个允许访问的源')
    origins = list(dict.fromkeys(normalize_url(item, origin_only=True) for item in raw_origins))
    private = value.get('allow_private_network', False)
    if type(private) is not bool:
        raise ValueError('私网访问选项必须为布尔值')
    raw_endpoints = value.get('endpoints')
    if type(raw_endpoints) is not list or not 1 <= len(raw_endpoints) <= 20:
        raise ValueError('请选择1至20个接口')
    endpoints = []
    for item in raw_endpoints:
        _object(item, {'name', 'url', 'method', 'headers', 'auth_required', 'owner_only_a', 'proof'}, '接口')
        url = normalize_url(item.get('url'))
        if origin_of(url) not in origins:
            raise ValueError('接口地址不在允许访问的精确源中')
        if any(sensitive_key(key) for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
            raise ValueError('认证参数不支持放在URL中，请使用测试账号的请求头配置认证')
        method = item.get('method', 'GET')
        if type(method) is not str or method not in ('GET', 'HEAD'):
            raise ValueError('第一版仅支持GET和HEAD请求')
        flags = {key: item.get(key, False) for key in ('auth_required', 'owner_only_a')}
        if any(type(flag) is not bool for flag in flags.values()):
            raise ValueError('认证及资源归属声明必须为布尔值')
        proof = item.get('proof')
        if proof is not None:
            _object(proof, {'path', 'expected'}, '受保护资源证据')
            path = proof.get('path')
            expected = proof.get('expected')
            if type(path) is not str or not _SELECTOR.fullmatch(path) or 'expected' not in proof or type(expected) not in (str, bool, int, float):
                raise ValueError('证据须配置body路径和非空标量期望值')
            if type(expected) is str and (not expected or len(expected) > 1024):
                raise ValueError('证据期望文本须为1至1024字符')
            if type(expected) in (int, float):
                _number(expected, -1e100, 1e100, '证据期望数值')
            if method == 'HEAD':
                raise ValueError('HEAD不返回响应体，请选择GET后配置资源证据')
            proof = {'path': path, 'expected': expected}
        endpoints.append({'name': _text(item.get('name', '接口'), '接口名称', 120), 'url': url, 'method': method,
                          'headers': validate_headers(item.get('headers', {})), **flags, 'proof': proof})
    checks = value.get('checks', sorted(CHECKS))
    if type(checks) is not list or not checks or any(type(item) is not str or item not in CHECKS for item in checks):
        raise ValueError('请选择有效检查类型')
    return {'allowed_origins': origins, 'allow_private_network': private, 'endpoints': endpoints,
            'checks': list(dict.fromkeys(checks)),
            'max_requests': _number(value.get('max_requests', 40), 1, 100, '请求预算', True),
            'rate_per_second': _number(value.get('rate_per_second', 1), .1, 2, '每秒速率'),
            'timeout_seconds': _number(value.get('timeout_seconds', 10), 1, 30, '请求超时')}


def validate_identities(value):
    _object(value, {'a', 'b'}, '测试账号')
    output = {}
    for key, item in value.items():
        _object(item, {'name', 'headers'}, '测试账号')
        headers = validate_headers(item.get('headers', {}), identity=True)
        if not headers or not any(content.strip() for content in headers.values()):
            raise ValueError('测试账号至少需要一个非空认证请求头')
        output[key] = {'name': _text(item.get('name', f'账号{key.upper()}'), '账号名称', 80), 'headers': headers}
    return output
