"""Small, side-effect-free helpers for LLM configuration handling."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

try:  # OpenAI is an optional provider dependency in some deployments.
    from openai import APIStatusError
except ImportError:  # pragma: no cover - depends on installed providers.
    APIStatusError = None


OPENAI_COMPATIBLE_PROVIDERS = frozenset({
    'openai', 'qwen', 'ernie', 'zhipu', 'other', 'deepseek',
})


def normalize_openai_compatible_base_url(provider: str, base_url: str) -> str:
    """Remove a copied OpenAI chat-completions endpoint from a base URL.

    SDKs append ``/chat/completions`` themselves.  Only the exact terminal
    endpoint is removed, and only for providers that use the OpenAI-compatible
    protocol.  Existing version or gateway path prefixes are preserved.
    """
    if provider not in OPENAI_COMPATIBLE_PROVIDERS or not base_url:
        return base_url

    parsed = urlsplit(base_url)
    path_segments = parsed.path.rstrip('/').split('/')
    if path_segments[-2:] != ['chat', 'completions']:
        return base_url

    base_path = '/'.join(path_segments[:-2])
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        base_path,
        parsed.query,
        parsed.fragment,
    ))


def get_html_gateway_error_message(error: Exception) -> Optional[str]:
    """Return a safe message for known upstream HTML gateway responses."""
    status_error_types = (httpx.HTTPStatusError,)
    if APIStatusError is not None:
        status_error_types += (APIStatusError,)
    if not isinstance(error, status_error_types):
        return None

    response = getattr(error, 'response', None)
    if response is None:
        return None

    status_code = getattr(response, 'status_code', None)
    headers = getattr(response, 'headers', {})
    content_type = headers.get('content-type', '')
    if not isinstance(status_code, int) or 'text/html' not in content_type.lower():
        return None

    try:
        response_body = response.text
    except Exception:
        response_body = ''

    if status_code == 403 and 'cloudflare' in response_body.lower():
        return (
            '模型服务网关拒绝请求（HTTP 403，Cloudflare）。'
            '请联系服务商检查访问策略；当前无法验证密钥和模型是否可用。'
        )
    return (
        f'模型服务网关返回非模型 JSON 响应（HTTP {status_code}）。'
        '请检查服务商网关或访问策略。'
    )
