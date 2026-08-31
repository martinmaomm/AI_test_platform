"""Classify confirmed model-service HTTP failures without exposing provider details.

Callers may supply ``stage`` at a boundary where the failed operation is known
to be an LLM invocation.  Without it, this helper accepts status codes only
from known OpenAI/LangChain SDK exception modules.  It never infers a model
failure from an MCP/browser tool's message text.
"""

from __future__ import annotations

from collections.abc import Iterator


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


def classify_model_service_error(
    error: BaseException,
    *,
    stage: str | None = None,
) -> tuple[str, str] | None:
    """Return a stable safe error for a confirmed model-service status.

    An explicit non-empty ``stage`` permits status classification at an
    unambiguous model-invocation boundary.  When it is absent, the exception
    must instead originate from an OpenAI/LangChain SDK module.  ``stage`` is
    not included in the returned message, so arbitrary exception details,
    response bodies, requests, and credentials can never reach the user.
    """
    known_model_stage = isinstance(stage, str) and bool(stage.strip())

    for candidate in _iter_exception_chain(error):
        status = _status_code(candidate)
        if status is None or not (known_model_stage or _is_trusted_model_sdk_error(candidate)):
            continue
        classified = _model_service_error(status)
        if classified is not None:
            return classified
    return None
