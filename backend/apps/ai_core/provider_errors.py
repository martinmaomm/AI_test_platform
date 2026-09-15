"""Safe, provider-specific LLM error classification.

The result is deliberately suitable for a task-status API: it contains no
provider response body, prompt, key, endpoint, or raw exception text.
"""
from __future__ import annotations

import re
from typing import Any


_MESSAGES = {
    'MODEL_OVERLOADED': '模型服务当前负载较高，请稍后重试。',
    'MODEL_RATE_LIMITED': '模型服务当前请求过于频繁，请稍后重试。',
    'MODEL_TIMEOUT': '模型服务响应超时，请稍后重试。',
    'MODEL_UNAVAILABLE': '模型服务暂时不可用，请稍后重试。',
    'MODEL_QUOTA_EXHAUSTED': '模型服务额度已用尽，请检查服务商额度或计费配置。',
    'MODEL_AUTH_ERROR': '模型服务认证或访问权限异常，请检查模型配置。',
    'MODEL_CONFIG_ERROR': '模型服务或所选模型配置不可用，请检查模型配置。',
}
_RETRYABLE = frozenset({
    'MODEL_OVERLOADED', 'MODEL_RATE_LIMITED', 'MODEL_TIMEOUT', 'MODEL_UNAVAILABLE',
})
_KNOWN_PROVIDER_CLASS_NAMES = frozenset({
    'APIError', 'APIConnectionError', 'APIStatusError', 'APITimeoutError',
    'AuthenticationError', 'PermissionDeniedError', 'RateLimitError',
    'NotFoundError', 'InternalServerError', 'BadRequestError', 'UnprocessableEntityError',
})
_OVERLOAD = re.compile(r'\b(overloaded|overload|server[_ -]?overloaded|capacity\s+exceeded)\b', re.I)
_RATE_LIMIT = re.compile(r'\b(rate[ _-]?limit(?:ed|ing)?|too many requests|quota exceeded)\b', re.I)
_TIMEOUT = re.compile(r'\b(timeout|timed out|deadline exceeded|gateway timeout)\b|超时', re.I)
_UNAVAILABLE = re.compile(r'\b(service unavailable|temporarily unavailable|bad gateway|connection (?:refused|reset|closed)|server disconnected)\b', re.I)
_AUTH = re.compile(r'\b(unauthori[sz]ed|forbidden|authentication|invalid api key|permission denied)\b', re.I)
_CONFIG = re.compile(r'\b(model not found|unknown model|invalid model|not found|unsupported model)\b', re.I)
_QUOTA = re.compile(r'\b(insufficient[_ -]?quota|billing[_ -]?hard[_ -]?limit|billing.{0,24}limit|exceeded your current quota)\b', re.I)
_STREAM_WRAPPER = re.compile(r'(?:流式\s*llm调用失败|(?:stream(?:ing)?|流式).{0,24}(?:llm|model).{0,24}(?:failed|失败))', re.I)


def _error_chain(error: BaseException) -> list[BaseException]:
    """Unwrap common SDK/framework wrappers without following arbitrary objects."""
    values: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error] if isinstance(error, BaseException) else []
    while pending and len(values) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.append(current)
        for name in ('__cause__', '__context__', 'original_exception', 'original_error', 'error'):
            nested = getattr(current, name, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return values


def _is_provider_exception(error: BaseException) -> bool:
    error_type = type(error)
    module = getattr(error_type, '__module__', '')
    return (
        module == 'openai' or module.startswith('openai.')
        or module == 'langchain_openai' or module.startswith('langchain_openai.')
        or (error_type.__name__ in _KNOWN_PROVIDER_CLASS_NAMES and module.startswith(('litellm', 'anthropic')))
    )


def _status_code(error: BaseException) -> int | None:
    for value in (getattr(error, 'status_code', None), getattr(getattr(error, 'response', None), 'status_code', None)):
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _provider_code(error: BaseException) -> str:
    values = [getattr(error, 'code', None)]
    body = getattr(error, 'body', None)
    if isinstance(body, dict):
        values.extend((body.get('code'), (body.get('error') or {}).get('code') if isinstance(body.get('error'), dict) else None))
    return ' '.join(str(value) for value in values if isinstance(value, (str, int))).lower()


def _from_status(status: int | None) -> str | None:
    if status in {401, 403}:
        return 'MODEL_AUTH_ERROR'
    if status == 429:
        return 'MODEL_RATE_LIMITED'
    if status in {408, 504}:
        return 'MODEL_TIMEOUT'
    if status in {400, 404, 422}:
        return 'MODEL_CONFIG_ERROR'
    if status in {500, 502, 503}:
        return 'MODEL_UNAVAILABLE'
    return None


def _from_text(text: str) -> str | None:
    if _QUOTA.search(text):
        return 'MODEL_QUOTA_EXHAUSTED'
    if _OVERLOAD.search(text):
        return 'MODEL_OVERLOADED'
    if _RATE_LIMIT.search(text):
        return 'MODEL_RATE_LIMITED'
    if _TIMEOUT.search(text):
        return 'MODEL_TIMEOUT'
    if _UNAVAILABLE.search(text):
        return 'MODEL_UNAVAILABLE'
    if _AUTH.search(text):
        return 'MODEL_AUTH_ERROR'
    if _CONFIG.search(text):
        return 'MODEL_CONFIG_ERROR'
    return None


def _transport_kind(error: BaseException) -> str | None:
    """SDK class identity is reliable even when its message is generic."""
    if not _is_provider_exception(error):
        return None
    if type(error).__name__ == 'APITimeoutError':
        return 'MODEL_TIMEOUT'
    if type(error).__name__ == 'APIConnectionError':
        return 'MODEL_UNAVAILABLE'
    return None


def _result(code: str, status_code: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        'code': code,
        'message': _MESSAGES[code],
        'retryable': code in _RETRYABLE,
    }
    if status_code is not None:
        result['status_code'] = status_code
    return result


def classify_provider_error(error, model_context: bool = False) -> dict[str, Any] | None:
    """Classify a real model-provider exception into safe public metadata.

    With ``model_context=False`` (the default), ordinary HTTP/page/tool errors
    are intentionally ignored.  Callers wrapping a direct model stream may
    set it to true to classify generic transport exceptions as well.
    """
    if not isinstance(error, BaseException):
        return None
    for current in _error_chain(error):
        provider_exception = _is_provider_exception(current)
        text = str(current)
        wrapper = bool(_STREAM_WRAPPER.search(text))
        if not (provider_exception or model_context or wrapper):
            continue
        status = _status_code(current)
        code_text = _provider_code(current)
        # Authentication failures are persistent until credentials or access
        # policy change; no body marker may make them retryable.
        code = _from_status(status) if status in {401, 403} else None
        if not code and _QUOTA.search(f'{code_text} {text}'):
            code = 'MODEL_QUOTA_EXHAUSTED'
        # A few streaming gateways signal overload in a structured body after
        # HTTP 200/no status. Restrict this exception to server-side statuses.
        if not code and _OVERLOAD.search(code_text) and (status is None or status == 200 or 500 <= status <= 599):
            code = 'MODEL_OVERLOADED'
        code = code or _transport_kind(current) or _from_status(status) or _from_text(f'{code_text} {text}')
        if code:
            return _result(code, status)
    return None


def public_model_failure(value: Any, *, stage: str) -> dict[str, Any] | None:
    """Rebuild a persisted task diagnostic from a trusted classification only."""
    code = value.get('code') if isinstance(value, dict) else None
    if code not in _MESSAGES or stage not in {'initial_model', 'exploring', 'planning', 'generating', 'repairing'}:
        return None
    return {
        'code': code,
        'message': _MESSAGES[code],
        'retryable': code in _RETRYABLE,
        'stage': stage,
    }
