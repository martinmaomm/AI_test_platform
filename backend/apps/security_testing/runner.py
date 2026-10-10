"""Execute only reviewed read-only cases and retain bounded, redacted evidence."""
from collections import Counter
from copy import deepcopy
import json
import math
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import transport
from .validation import CHECKS, sensitive_key, validate_config, validate_identities

_PREVIEW_LIMIT = 6000
_TOTAL_SECONDS = 600
_SAFE_RESPONSE_HEADERS = frozenset({
    'content-type', 'content-length', 'content-encoding', 'cache-control', 'pragma',
    'x-content-type-options', 'x-frame-options', 'strict-transport-security',
    'access-control-allow-origin', 'access-control-allow-credentials',
    'content-security-policy', 'referrer-policy', 'permissions-policy',
})
_VARIANTS = {'baseline': '账号A基准', 'anonymous': '匿名请求', 'invalid': '无效认证', 'b': '账号B',
             'cors_one': '跨域来源一', 'cors_two': '跨域来源二'}
_CORS_ORIGINS = ('https://security-check-one.invalid', 'https://security-check-two.invalid')


class BudgetExhausted(Exception):
    pass


class RuntimeDeadlineExceeded(Exception):
    pass


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite JSON number')
    return number


def _json(response):
    if response.truncated or response.header('Content-Encoding').lower() not in ('', 'identity'):
        return None
    try:
        return json.loads(response.body, parse_float=_finite_float, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        return None


def _proof(response, proof):
    if proof is None or not 200 <= response.status_code < 300 or response.truncated:
        return None if proof is None else False
    value = _json(response)
    try:
        for key, index in re.findall(r'\.([A-Za-z_][A-Za-z0-9_-]*)|\[([0-9]+)\]', proof['path'][4:]):
            value = value[int(index)] if index else value[key]
    except (KeyError, IndexError, TypeError):
        return False
    expected = proof['expected']
    # Booleans are not numeric resource identifiers, even though Python equates them.
    return type(value) is type(expected) and value == expected


def _secrets(identities):
    values = set()
    for identity in identities.values():
        for key, value in identity['headers'].items():
            values.add(value)
            if key.lower() == 'authorization' and ' ' in value:
                values.add(value.split(' ', 1)[1])
            if key.lower() == 'cookie':
                values.update(part.split('=', 1)[1].strip() for part in value.split(';') if '=' in part)
    return sorted((value for value in values if value), key=len, reverse=True)


def _scrub(value, secrets):
    text = str(value)
    for secret in secrets:
        text = text.replace(secret, '[已隐藏]')
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_=.-]+', '[已隐藏认证]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[已隐藏令牌]', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*', '[已隐藏私钥]', text)
    return text


def _redact(value, secrets, depth=0):
    if depth > 6:
        return '[深层内容已省略]'
    if type(value) is dict:
        return {_scrub(str(key), secrets)[:160]: '[已隐藏]' if sensitive_key(key) else _redact(item, secrets, depth + 1)
                for key, item in list(value.items())[:40]}
    if type(value) is list:
        return [_redact(item, secrets, depth + 1) for item in value[:15]]
    if type(value) is str:
        return _scrub(value, secrets)[:1024]
    return value


def _safe_url(url, secrets):
    parsed = urlsplit(url)
    query = [(key, '[已隐藏]' if sensitive_key(key) else _scrub(value, secrets))
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    return _scrub(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), '')), secrets)


def _evidence(endpoint, variant, response, secrets):
    proof = endpoint['proof']
    if proof is not None and type(proof['expected']) is str:
        secrets = [*secrets, proof['expected']]
    parsed = _json(response)
    if parsed is None:
        preview = f'非完整JSON响应，仅记录元信息（已读取{len(response.body)}字节）'
    else:
        preview = json.dumps(_redact(parsed, secrets), ensure_ascii=False, allow_nan=False)
        if len(preview) > _PREVIEW_LIMIT:
            preview = preview[:_PREVIEW_LIMIT] + '…[预览已截断]'
    return {'variant': variant, 'variant_label': _VARIANTS[variant], 'url': _safe_url(endpoint['url'], secrets),
            'method': endpoint['method'], 'status_code': response.status_code, 'elapsed_ms': response.elapsed_ms,
            'response_headers': {key.lower(): _scrub(value, secrets)[:512] for key, value in response.headers
                                 if key.lower() in _SAFE_RESPONSE_HEADERS},
            'response_preview': preview, 'response_bytes': len(response.body), 'truncated': response.truncated,
            'proof_match': _proof(response, endpoint['proof'])}


def _similar(first, second):
    if first.truncated or second.truncated or not 200 <= second.status_code < 300:
        return False
    a, b = _json(first), _json(second)
    try:
        return a is not None and b is not None and type(a) is type(b) and a == b and bool(a)
    except RecursionError:
        return False


def _information(response):
    observations = []
    parsed = _json(response)
    count = 0

    def visit(value, depth=0):
        nonlocal count
        if depth > 12:
            return
        if type(value) is dict:
            for key, item in value.items():
                if sensitive_key(key) and item not in (None, '', False, '[已隐藏]', '[REDACTED]', '***', '******'):
                    count += 1
                visit(item, depth + 1)
        elif type(value) is list:
            for item in value:
                visit(item, depth + 1)
    visit(parsed)
    if count:
        observations.append(f'响应包含{count}个有值的敏感命名字段，需核对是否为接口必要输出')
    body = response.body.decode('utf-8', errors='replace')
    if re.search(r'Traceback \(most recent call last\)|SQLSTATE\[|java\.[a-z]+\.[A-Za-z]+Exception|You have an error in your SQL syntax|-----BEGIN [^-]*PRIVATE KEY-----', body):
        observations.append('响应出现内部异常、数据库错误或私钥格式特征')
    return observations


def _configuration(endpoint, baseline, cors):
    observations = []
    if not baseline.header('X-Content-Type-Options'):
        observations.append('未观察到X-Content-Type-Options响应头')
    if endpoint['url'].startswith('https://') and not baseline.header('Strict-Transport-Security'):
        observations.append('HTTPS响应未观察到HSTS配置')
    if 'text/html' in baseline.header('Content-Type').lower() and not baseline.header('Content-Security-Policy'):
        observations.append('HTML响应未观察到Content-Security-Policy配置')
    cookie_count = 0
    for key, value in baseline.headers:
        if key.lower() != 'set-cookie':
            continue
        attributes = {part.strip().split('=', 1)[0].lower() for part in value.split(';')[1:]}
        missing = []
        if 'httponly' not in attributes:
            missing.append('HttpOnly')
        if endpoint['url'].startswith('https://') and 'secure' not in attributes:
            missing.append('Secure')
        if 'samesite' not in attributes:
            missing.append('SameSite')
        if missing:
            cookie_count += 1
            observations.append(f'第{cookie_count}条Cookie缺少属性：' + '、'.join(missing))
    if len(cors) == 2:
        reflected = all(response.header('Access-Control-Allow-Origin') == origin
                        and response.header('Access-Control-Allow-Credentials').lower() == 'true'
                        for origin, response in zip(_CORS_ORIGINS, cors))
        if reflected:
            observations.append('两个测试来源均被反射且允许携带凭据，需核对跨域访问策略')
        elif any(response.header('Access-Control-Allow-Origin') == '*' for response in cors):
            observations.append('跨域策略允许任意来源，需核对资源是否预期公开；通配符与凭据组合不能直接证明浏览器可读取')
    return observations


def _cases(value, config):
    if type(value) is not list or not 1 <= len(value) <= 80:
        raise ValueError('执行检查项须为1至80项')
    output, ids, combinations = [], set(), set()
    for item in value:
        if type(item) is not dict or set(item) - {'id', 'endpoint_index', 'check', 'title', 'rationale'}:
            raise ValueError('检查项格式不正确')
        case_id, index, check = item.get('id'), item.get('endpoint_index'), item.get('check')
        if (type(case_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', case_id)
                or type(index) is not int or not 0 <= index < len(config['endpoints'])
                or type(check) is not str or check not in CHECKS or check not in config['checks']):
            raise ValueError('检查项引用无效')
        if case_id in ids or (index, check) in combinations:
            raise ValueError('检查项不能重复')
        title = item.get('title', '安全检查')
        if type(title) is not str or not 1 <= len(title) <= 160:
            raise ValueError('检查项标题格式不正确')
        ids.add(case_id)
        combinations.add((index, check))
        output.append({'id': case_id, 'endpoint_index': index, 'check': check, 'title': title})
    return output


def execute(snapshot, identities, check_active, on_progress):
    if type(snapshot) is not dict:
        raise ValueError('执行快照格式不正确')
    config = validate_config(snapshot.get('config'))
    identities = validate_identities(identities)
    cases = _cases(snapshot.get('cases'), config)
    secrets = _secrets(identities)
    items, limitations, cache = [], [], {}
    started, next_request, sent = time.monotonic(), 0, 0

    def result_snapshot(complete=False):
        notes = list(limitations)
        if any(entry.get('truncated') for item in items for entry in item['evidence']):
            notes.append('部分响应超过64KiB读取限制，截断内容未用于确认资源证据')
        notes.extend(['仅检查所选GET/HEAD接口及已配置身份，未覆盖写入请求或全部业务权限',
                      '已通过仅表示本次有限检查未发现相应问题，不代表系统整体安全',
                      '敏感字段与认证信息已隐藏；同一接口的相同基准在本次执行内复用'])
        if not complete:
            notes.append('当前仅保存已完成的检查项；执行中断时，未完成或未保存的检查不能视为通过')
        counts = Counter(item['status'] for item in items)
        # Every callback owns a frozen copy. It contains only redacted evidence,
        # never the baseline-response cache or identity headers.
        return {'items': deepcopy(items), 'summary': {'total': len(items), 'expected_total': len(cases),
                'requests_sent': sent, 'complete': complete,
                **{key: counts[key] for key in ('passed', 'confirmed', 'suspected', 'inconclusive', 'error', 'skipped')}},
                'limitations': list(dict.fromkeys(notes))}

    def publish_progress(phase):
        on_progress({'completed': len(items), 'total': len(cases), 'requests_sent': sent,
                     'phase': phase, 'partial_result': result_snapshot()})

    def active():
        check_active()
        if time.monotonic() - started >= _TOTAL_SECONDS:
            raise RuntimeDeadlineExceeded('安全测试达到总执行时限')

    def send(index, variant):
        nonlocal sent, next_request
        active()
        cache_key = (index, variant)
        if cache_key in cache:
            return cache[cache_key]
        if sent >= config['max_requests']:
            raise BudgetExhausted
        while time.monotonic() < next_request:
            active()
            time.sleep(min(.1, max(0, next_request - time.monotonic())))
        active()
        endpoint = config['endpoints'][index]
        headers = dict(endpoint['headers'])
        identity_key = 'b' if variant == 'b' else 'a'
        auth_headers = identities.get(identity_key, {}).get('headers', {})
        if variant not in ('anonymous', 'invalid'):
            for name, value in auth_headers.items():
                headers = {key: val for key, val in headers.items() if key.lower() != name.lower()}
                headers[name] = value
        if variant == 'invalid':
            for name, value in auth_headers.items():
                if name.lower() == 'cookie':
                    value = '; '.join(part.split('=', 1)[0].strip() + '=invalid-security-test-value'
                                      for part in value.split(';') if '=' in part)
                elif name.lower() == 'authorization':
                    value = 'Bearer invalid-security-test-value'
                else:
                    value = 'invalid-security-test-value'
                headers[name] = value
        if variant.startswith('cors_'):
            headers['Origin'] = _CORS_ORIGINS[0 if variant == 'cors_one' else 1]
        sent += 1
        next_request = time.monotonic() + 1 / config['rate_per_second']
        publish_progress('request')
        response = transport.request(endpoint['url'], endpoint['method'], headers,
                                     allowed_origins=config['allowed_origins'], allow_private_network=config['allow_private_network'],
                                     timeout_seconds=min(config['timeout_seconds'], max(.01, _TOTAL_SECONDS - (time.monotonic() - started))), check_active=active)
        active()
        cache[cache_key] = response
        return response

    for case in cases:
        active()
        endpoint = config['endpoints'][case['endpoint_index']]
        item = {'case_id': case['id'], 'check': case['check'], 'endpoint_index': case['endpoint_index'],
                'title': _scrub(case['title'], secrets), 'status': 'inconclusive', 'summary': '', 'recommendation': '', 'evidence': []}
        current_variant = 'baseline'

        def fetch(variant):
            nonlocal current_variant
            current_variant = variant
            response = send(case['endpoint_index'], variant)
            item['evidence'].append(_evidence(endpoint, variant, response, secrets))
            return response

        try:
            if case['check'] in ('authentication', 'authorization'):
                if 'a' not in identities:
                    item['summary'] = '未配置账号A，无法建立有效认证基准'
                elif case['check'] == 'authentication' and not endpoint['auth_required']:
                    item.update(status='skipped', summary='接口未声明需要认证，此项未执行')
                elif case['check'] == 'authorization' and (not endpoint['owner_only_a'] or 'b' not in identities):
                    item['summary'] = '缺少账号B或账号A独占资源声明，无法验证资源越权'
                elif case['check'] == 'authorization' and (
                        {key.lower(): value for key, value in identities['a']['headers'].items()}
                        == {key.lower(): value for key, value in identities['b']['headers'].items()}):
                    item['summary'] = '账号A与账号B的认证请求头相同，无法验证不同账号之间的资源权限'
                    item['recommendation'] = '为账号B配置另一个测试账号的有效认证请求头后重试'
                else:
                    baseline = fetch('baseline')
                    baseline_proof = _proof(baseline, endpoint['proof'])
                    if not 200 <= baseline.status_code < 300 or (endpoint['proof'] is not None and baseline_proof is not True) or baseline.truncated:
                        item['summary'] = '账号A未返回可验证的正常资源基准，不能判定认证或权限通过'
                    else:
                        variants = ('anonymous', 'invalid') if case['check'] == 'authentication' else ('b',)
                        observations = []
                        for variant in variants:
                            response = fetch(variant)
                            observations.append((variant, response, _proof(response, endpoint['proof'])))
                        confirmed = baseline_proof is True and any(match is True for _, _, match in observations)
                        similar = any(_similar(baseline, response) for _, response, _ in observations)
                        denied = all(response.status_code in (401, 403) for _, response, _ in observations)
                        if confirmed:
                            item.update(status='confirmed', summary='替代身份仍返回与账号A一致的受保护资源证据', recommendation='服务端应校验当前身份及该资源的访问权限；修复后使用相同资源复测')
                        elif denied and baseline_proof is True:
                            item.update(status='passed', summary='有效基准已验证，本次替代身份请求均被401/403拒绝')
                        elif similar:
                            item.update(status='suspected', summary='替代身份与账号A返回相同JSON内容，但证据不足以确认受保护资源泄露', recommendation='补充能标识受保护资源的body证据，并核对账号与资源归属')
                        else:
                            item.update(summary='当前响应不足以证明受保护资源被访问或访问控制有效', recommendation='配置明确的资源证据并检查业务响应；HTTP成功状态不代表业务成功')
            elif case['check'] == 'information':
                baseline = fetch('baseline')
                observations = _information(baseline)
                if observations:
                    item.update(status='suspected', summary='；'.join(observations), recommendation='人工核对敏感字段是否为必要业务输出，并关闭内部异常详情')
                elif baseline.truncated or not 200 <= baseline.status_code < 300 or endpoint['method'] == 'HEAD':
                    item['summary'] = '响应不完整、请求未成功或无响应体，信息泄露检查覆盖不足'
                else:
                    item.update(status='passed', summary='本次有限响应中未匹配到预设敏感字段或内部错误特征')
            elif case['check'] == 'configuration':
                baseline = fetch('baseline')
                cors = [fetch('cors_one'), fetch('cors_two')]
                observations = _configuration(endpoint, baseline, cors)
                if observations:
                    item.update(status='suspected', summary='；'.join(observations), recommendation='结合接口公开范围和业务需求复核响应头、Cookie与跨域策略；配置观察不等同可利用漏洞')
                elif not 200 <= baseline.status_code < 300 or any(not 200 <= response.status_code < 300 for response in cors):
                    item['summary'] = '部分配置探测请求未成功，无法完成配置判断'
                else:
                    item.update(status='passed', summary='本次响应头及两个跨域来源探测未发现预设配置问题')
        except BudgetExhausted:
            item.update(status='skipped' if not item['evidence'] else 'inconclusive', summary='请求预算已用完，此项未完成')
            limitations.append('请求预算耗尽，部分检查未执行完整')
        except transport.TransportError as exc:
            item.update(status='error', summary=str(exc), recommendation='检查目标网络、访问范围或超时设置后重试')
            item['evidence'].append({'variant': current_variant, 'variant_label': _VARIANTS[current_variant],
                                     'url': _safe_url(endpoint['url'], secrets), 'method': endpoint['method'], 'error_code': exc.code})
        items.append(item)
        publish_progress('checking')
    return result_snapshot(complete=True)
