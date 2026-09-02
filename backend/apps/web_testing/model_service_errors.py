"""Classify confirmed model-service HTTP failures without exposing provider details.

Callers may supply ``stage`` at a boundary where the failed operation is known
to be an LLM invocation.  Without it, this helper accepts status codes only
from known OpenAI/LangChain SDK exception modules.  It never infers a model
failure from an MCP/browser tool's message text.
"""

from __future__ import annotations

from collections.abc import Iterator

from openai import APIConnectionError, APIError, APITimeoutError, OpenAIError


_MODEL_ERROR_MESSAGES = {
    429: (
        "MODEL_RATE_LIMITED",
        "本次锁定的模型触发限流，请稍后重试。",
    ),
    401: (
        "MODEL_AUTHENTICATION_FAILED",
        "本次锁定的模型认证或权限校验失败，请检查模型配置后重试。",
    ),
    403: (
        "MODEL_AUTHENTICATION_FAILED",
        "本次锁定的模型认证或权限校验失败，请检查模型配置后重试。",
    ),
}
_TRUSTED_MODEL_EXCEPTION_MODULE_PREFIXES = ("openai", "langchain")
_NON_RETRYABLE_STREAM_ERROR_TYPES = frozenset({
    "authentication_error",
    "authorization_error",
    "invalid_request_error",
    "permission_error",
})
_NON_RETRYABLE_STREAM_ERROR_CODES = frozenset({
    "invalid_api_key",
    "invalid_request",
    "invalid_request_error",
    "model_not_found",
})
_STREAM_SERVICE_UNAVAILABLE_MESSAGES = frozenset({
    "模型服务暂时不可用，请稍后重试",
    "model service temporarily unavailable, please try again later",
})


def _iter_exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Yield an exception, then its cause/context, without looping forever."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(error: BaseException) -> int | None:
    """Read a provider status code without inspecting exception text or body."""
    try:
        status = getattr(error, "status_code", None)
    except Exception:
        status = None
    if status is None:
        try:
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None)
        except Exception:
            status = None
    if isinstance(status, bool):
        return None
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_trusted_model_sdk_error(error: BaseException) -> bool:
    """Return whether an exception class originated in an installed model SDK."""
    module = type(error).__module__
    return any(
        module == prefix or module.startswith(f"{prefix}.") or module.startswith(f"{prefix}_")
        for prefix in _TRUSTED_MODEL_EXCEPTION_MODULE_PREFIXES
    )


def _model_service_error(status: int) -> tuple[str, str] | None:
    if status == 504:
        return (
            "MODEL_GATEWAY_TIMEOUT",
            "本次锁定的模型服务请求超时（HTTP 504），请稍后重试。",
        )
    if status in {500, 502, 503}:
        return (
            "MODEL_SERVICE_ERROR",
            f"本次锁定的模型服务异常（HTTP {status}），请稍后重试。",
        )
    return _MODEL_ERROR_MESSAGES.get(status)


def _stateless_model_service_error(error: BaseException) -> tuple[str, str] | None:
    """Classify safe OpenAI stream and transport failures without a status code."""
    if not _is_trusted_model_sdk_error(error):
        return None
    if isinstance(error, APITimeoutError):
        return (
            "MODEL_GATEWAY_TIMEOUT",
            "本次锁定的模型服务请求超时，请稍后重试。",
        )
    if isinstance(error, APIConnectionError):
        return (
            "MODEL_SERVICE_ERROR",
            "本次锁定的模型服务连接异常，请稍后重试。",
        )
    if isinstance(error, APIError):
        error_type = str(getattr(error, "type", "") or "").lower()
        error_code = str(getattr(error, "code", "") or "").lower()
        if (
            error_type in _NON_RETRYABLE_STREAM_ERROR_TYPES
            or error_code in _NON_RETRYABLE_STREAM_ERROR_CODES
        ):
            return None
        if error_type in {"rate_limit_error", "rate_limit_exceeded"}:
            return _MODEL_ERROR_MESSAGES[429]
        if error_type in {"timeout_error", "gateway_timeout"}:
            return (
                "MODEL_GATEWAY_TIMEOUT",
                "本次锁定的模型服务请求超时，请稍后重试。",
            )
        return (
            "MODEL_SERVICE_ERROR",
            "本次锁定的模型服务流式响应异常，请稍后重试。",
        )
    if (
        isinstance(error, OpenAIError)
        and str(error).strip().lower() in _STREAM_SERVICE_UNAVAILABLE_MESSAGES
    ):
        return (
            "MODEL_SERVICE_ERROR",
            "本次锁定的模型服务暂时不可用，请稍后重试。",
        )
    return None


def classify_model_service_error(
    error: BaseException,
    *,
    stage: str | None = None,
) -> tuple[str, str] | None:
    """Return a stable safe error for confirmed model-service failures.

    An explicit non-empty ``stage`` permits status classification at an
    unambiguous model-invocation boundary.  When it is absent, the exception
    must instead originate from an OpenAI/LangChain SDK module. OpenAI's SSE
    decoder can report API, connection, and timeout failures without a status;
    those are recognised by trusted exception type and safe metadata only.
    A base ``OpenAIError`` is accepted only for the exact known unavailable
    message. ``stage`` and arbitrary exception details, response bodies,
    requests, and credentials never reach the user.
    """
    known_model_stage = isinstance(stage, str) and bool(stage.strip())

    for candidate in _iter_exception_chain(error):
        status = _status_code(candidate)
        if status is None:
            classified = _stateless_model_service_error(candidate)
            if classified is not None:
                return classified
            continue
        if not (known_model_stage or _is_trusted_model_sdk_error(candidate)):
            continue
        classified = _model_service_error(status)
        if classified is not None:
            return classified
    return None
