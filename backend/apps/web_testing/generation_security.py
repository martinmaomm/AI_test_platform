"""Sensitive-input handling for WebUI script generation.

The database, WebSocket payloads and Celery arguments only receive values from
this module after sanitisation. Temporary login values live solely in Django's
configured cache (Redis in deployment).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.cache import cache


REDACTED_VALUE = '<redacted>'
SENSITIVE_KEY_RE = re.compile(
    r'(?:pass(?:word|wd)|pass\b|pwd|secret|token|api[_-]?key|auth(?:orization)?|'
    r'credential|cookie|session|private[_-]?key)',
    re.IGNORECASE,
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'(?P<prefix>(?:password|passwd|pwd|secret|token|api[_ -]?key|'
    r'authorization|auth|credential|cookie|session|private[_ -]?key|'
    r'密码|口令|令牌|密钥)\s*(?:为|是|[:：=]))\s*'
    r'(?P<value>[^\s,，;；。]+)',
    re.IGNORECASE,
)
LOGIN_PAIR_RE = re.compile(
    r'(?P<prefix>(?:登录(?:账号|用户)|账号|用户名|user(?:name)?)\s*(?:为|是|[:：=])?\s*)'
    r'(?P<username>[^\s,，;；。]+)\s+(?P<password>[^\s,，;；。]+)',
    re.IGNORECASE,
)
URL_RE = re.compile(r'https?://[^\s,，;；。]+', re.IGNORECASE)


class GenerationInputSecurityError(ValueError):
    """Raised when an input cannot safely enter the generation pipeline."""


def _is_sensitive_key(key: Any) -> bool:
    return bool(SENSITIVE_KEY_RE.search(str(key)))


def redact_url(value: str) -> str:
    """Keep a useful URL while redacting user-info and sensitive query values."""
    if not value:
        return ''
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return REDACTED_VALUE

    try:
        hostname = parsed.hostname or ''
        port = parsed.port
        netloc = f'{hostname}:{port}' if port else hostname
        if parsed.username or parsed.password:
            netloc = f'{REDACTED_VALUE}@{netloc}'
    except ValueError:
        # Accessing ParseResult.port validates numeric range and can fail after
        # urlsplit itself succeeds (for example, ``https://host:bad``).
        return REDACTED_VALUE

    query = urlencode([
        (key, REDACTED_VALUE if _is_sensitive_key(key) else item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
    ])
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ''))


def redact_text(value: str) -> str:
    """Redact obvious inline secret assignments and common login pairs."""
    if not value:
        return ''
    text = str(value)
    text = LOGIN_PAIR_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE} {REDACTED_VALUE}",
        text,
    )
    text = SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')} {REDACTED_VALUE}",
        text,
    )
    return URL_RE.sub(lambda match: redact_url(match.group(0)), text)


def redact_metadata(value: Any) -> Any:
    """Return a safe recursive projection suitable for persistence or logging."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE if _is_sensitive_key(key) else redact_metadata(item_value)
            for key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [redact_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_text(redact_url(value) if value.startswith(('http://', 'https://')) else value)
    return value


def find_suspected_credentials(value: str) -> list[str]:
    """Return detected credential categories without returning any secret value."""
    if not value:
        return []
    findings: list[str] = []
    if SENSITIVE_ASSIGNMENT_RE.search(value):
        findings.append('secret_assignment')
    if LOGIN_PAIR_RE.search(value):
        findings.append('login_pair')
    for url_match in URL_RE.finditer(value):
        try:
            parsed = urlsplit(url_match.group(0))
        except ValueError:
            continue
        if parsed.username or parsed.password or any(_is_sensitive_key(key) for key, _ in parse_qsl(parsed.query)):
            findings.append('url_secret')
            break
    return findings


def extract_inline_login_credentials(value: str) -> dict[str, str] | None:
    """Extract one complete login pair without persisting it with the description."""
    if not value:
        return None
    matches = list(LOGIN_PAIR_RE.finditer(str(value)))
    if not matches:
        return None
    pairs = [
        {
            'username': match.group('username').strip(),
            'password': match.group('password').strip(),
        }
        for match in matches
    ]
    first = pairs[0]
    if any(item != first for item in pairs[1:]):
        raise GenerationInputSecurityError('测试描述中只能指定一组被测环境登录账号和密码')
    return validate_temporary_credentials(first)


def normalize_start_path(raw_value: str, base_url: str) -> str:
    """Accept a relative path or a full URL on the environment's exact origin."""
    if not raw_value:
        raise GenerationInputSecurityError('请提供环境内的相对入口路径或目标 URL')
    try:
        base = urlsplit(str(base_url))
    except ValueError as exc:
        raise GenerationInputSecurityError('WebUI 环境的 Base URL 格式无效') from exc
    try:
        base_hostname = base.hostname
        base_port = base.port
        base_has_userinfo = base.username or base.password
    except ValueError as exc:
        raise GenerationInputSecurityError('WebUI 环境的 Base URL 端口格式无效') from exc
    if base.scheme not in {'http', 'https'} or not base_hostname or base_has_userinfo:
        raise GenerationInputSecurityError('WebUI 环境必须配置有效的 HTTP(S) Base URL')

    raw_text = str(raw_value).strip()
    if raw_text.startswith('//'):
        raise GenerationInputSecurityError('入口地址不能使用省略协议的 URL')
    try:
        target = urlsplit(raw_text)
    except ValueError as exc:
        raise GenerationInputSecurityError('入口地址格式无效') from exc

    if target.scheme or target.netloc:
        try:
            target_hostname = target.hostname
            target_port = target.port
            target_has_userinfo = target.username or target.password
        except ValueError as exc:
            raise GenerationInputSecurityError('入口地址端口格式无效') from exc
        if target.scheme not in {'http', 'https'} or target_has_userinfo:
            raise GenerationInputSecurityError('入口地址必须是 HTTP(S) 且不能包含账号密码')
        base_origin = (base.scheme.lower(), base_hostname.lower(), base_port or (443 if base.scheme == 'https' else 80))
        target_origin = (target.scheme.lower(), target_hostname.lower() if target_hostname else '', target_port or (443 if target.scheme == 'https' else 80))
        if target_origin != base_origin:
            raise GenerationInputSecurityError('完整 URL 必须与所选 WebUI 环境的 Base URL 同源')
    elif not raw_text.startswith('/'):
        raise GenerationInputSecurityError('入口路径必须以 / 开头')

    query = [
        (key, item_value)
        for key, item_value in parse_qsl(target.query, keep_blank_values=True)
        if not _is_sensitive_key(key)
    ]
    path = target.path or '/'
    return urlunsplit(('', '', path, urlencode(query), ''))


def build_safe_target_url(base_url: str, start_path: str) -> str:
    """Build a display-only URL without retaining sensitive query data."""
    base = urlsplit(str(base_url))
    path = urlsplit(start_path)
    return redact_url(urlunsplit((base.scheme, base.netloc, path.path, path.query, '')))


def _credentials_cache_key(generation_id: Any) -> str:
    return f'webui:script-generation:credentials:{generation_id}'


def get_credentials_ttl_seconds() -> int:
    value = getattr(settings, 'WEBUI_SCRIPT_GENERATION_CREDENTIAL_TTL_SECONDS', 15 * 60)
    try:
        return max(60, int(value))
    except (TypeError, ValueError):
        return 15 * 60


def validate_temporary_credentials(value: Any) -> dict[str, str]:
    """Validate the intentionally small, short-lived credential payload."""
    if not isinstance(value, dict):
        raise GenerationInputSecurityError('临时登录信息必须是对象')
    allowed = {'username', 'password'}
    unknown = set(value) - allowed
    if unknown:
        raise GenerationInputSecurityError('临时登录信息仅支持 username 和 password 字段')
    result = {key: str(value.get(key, '')).strip() for key in allowed}
    if not result['username'] or not result['password']:
        raise GenerationInputSecurityError('临时登录信息必须同时提供用户名和密码')
    return result


def store_temporary_credentials(generation_id: Any, credentials: dict[str, str], *, timeout: int | None = None) -> None:
    """Store credentials only in cache; callers must never include them in tasks."""
    cache.set(
        _credentials_cache_key(generation_id),
        validate_temporary_credentials(credentials),
        timeout=timeout if timeout is not None else get_credentials_ttl_seconds(),
    )


def get_temporary_credentials(generation_id: Any) -> dict[str, str] | None:
    credentials = cache.get(_credentials_cache_key(generation_id))
    return credentials if isinstance(credentials, dict) else None


def clear_temporary_credentials(generation_id: Any) -> None:
    cache.delete(_credentials_cache_key(generation_id))
