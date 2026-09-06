"""Streaming-only LLM adapter for project knowledge tasks.

This module intentionally does not change ``ai_core``'s shared model manager.
Knowledge tasks need stricter semantics: a broken stream must never be replayed
as an ordinary invoke, because that can duplicate a partially generated draft.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Mapping, Sequence

from ai_core.model_manager import DEFAULT_LLM_TIMEOUT, get_llm_manager


class KnowledgeLLMError(RuntimeError):
    """Base error for a knowledge-task model call."""


class StreamingUnsupportedError(KnowledgeLLMError):
    """The chosen configuration has no synchronous streaming interface."""


class StreamInterruptedError(KnowledgeLLMError):
    """A stream emitted content and then failed, so it cannot be replayed."""


class KnowledgeLLMTimeout(KnowledgeLLMError):
    """The task has no remaining model-call budget."""


class KnowledgeTaskInactive(KnowledgeLLMError):
    """Cancellation, permission, timeout, or source revision checks stopped work."""


MAX_STREAM_ATTEMPTS = 3  # initial request plus at most two transient retries
RETRY_WAIT_CHECK_INTERVAL_SECONDS = 0.5


def _ensure_active(check_active: Callable[[], Any]) -> None:
    if check_active() is False:
        raise KnowledgeTaskInactive('知识任务已停止，不能继续调用模型。')


def _remaining_seconds(value: Callable[[], float] | float) -> float:
    return float(value() if callable(value) else value)


def _is_task_stopped(exc: BaseException) -> bool:
    """Keep runtime cancellation/permission/timeout signals untouched.

    Importing ``TaskStopped`` here would introduce a runtime/workflow import
    cycle.  The runtime exception has a stable public ``code`` attribute.
    """
    return exc.__class__.__name__ == 'TaskStopped' and hasattr(exc, 'code')


def _single_request_budget(manager: Any, remaining_seconds: Callable[[], float] | float) -> float:
    """Never expand the selected model's saved per-request timeout.

    ``ModelManager.config`` is the resolved configuration used to create
    ``current_llm``.  Reading it avoids guessing from client payloads and keeps
    the knowledge task's deadline as an additional upper bound only.
    """
    config = getattr(manager, 'config', {})
    extra_config = config.get('extra_config', {}) if isinstance(config, Mapping) else {}
    raw_timeout = extra_config.get('timeout', DEFAULT_LLM_TIMEOUT) if isinstance(extra_config, Mapping) else DEFAULT_LLM_TIMEOUT
    try:
        configured_timeout = float(raw_timeout)
    except (TypeError, ValueError):
        configured_timeout = float(DEFAULT_LLM_TIMEOUT)
    if configured_timeout <= 0:
        configured_timeout = float(DEFAULT_LLM_TIMEOUT)
    return min(_remaining_seconds(remaining_seconds), configured_timeout)


def _extract_text(chunk: Any) -> str:
    """Accept the same common LangChain chunk shapes as the shared manager."""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, Mapping):
        text = chunk.get('text')
        if isinstance(text, str):
            return text
        return _extract_text(chunk.get('content'))
    if isinstance(chunk, (list, tuple)):
        return ''.join(_extract_text(item) for item in chunk)
    content = getattr(chunk, 'content', None)
    if content is not None:
        return _extract_text(content)
    text = getattr(chunk, 'text', None)
    return text if isinstance(text, str) else ''


def _is_auth_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        '401', '403', 'unauthorized', 'forbidden', 'authentication',
        'invalid api key', 'invalid_api_key', 'permission denied',
    ))


def _is_transient_error(exc: BaseException) -> bool:
    if _is_auth_error(exc):
        return False
    message = str(exc).lower()
    return bool(re.search(
        r'\b429\b|\b5\d\d\b|rate.?limit|too many requests|'
        r'service unavailable|bad gateway|gateway timeout|connection|'
        r'network|temporar|timed out|timeout',
        message,
    ))


def _retry_after_seconds(exc: BaseException) -> float:
    """Use a provider retry hint when exposed, otherwise a small backoff."""
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', None) or getattr(exc, 'headers', None) or {}
    if isinstance(headers, Mapping):
        raw = headers.get('retry-after') or headers.get('Retry-After')
        try:
            if raw is not None:
                return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return 1.0


def _wait_before_retry(
    wait_seconds: float,
    check_active: Callable[[], Any],
    remaining_seconds: Callable[[], float] | float,
) -> None:
    """Wait in short slices so a long provider Retry-After stays cancellable."""
    remaining_wait = wait_seconds
    while remaining_wait > 0:
        _ensure_active(check_active)
        remaining_budget = _remaining_seconds(remaining_seconds)
        if remaining_budget <= 0:
            raise KnowledgeLLMTimeout('等待模型服务重试时知识任务已达到总时限。')
        wait_slice = min(RETRY_WAIT_CHECK_INTERVAL_SECONDS, remaining_wait, remaining_budget)
        if wait_slice <= 0:
            raise KnowledgeLLMTimeout('等待模型服务重试时知识任务已达到总时限。')
        time.sleep(wait_slice)
        remaining_wait -= wait_slice
        _ensure_active(check_active)
        if _remaining_seconds(remaining_seconds) <= 0:
            raise KnowledgeLLMTimeout('等待模型服务重试时知识任务已达到总时限。')


def _as_langchain_messages(messages: Sequence[Any]) -> list[Any]:
    """Allow workflow prompts to stay serializable while preserving native messages."""
    if not messages or not isinstance(messages[0], Mapping):
        return list(messages)

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    converted: list[Any] = []
    for message in messages:
        if not isinstance(message, Mapping):
            converted.append(message)
            continue
        role = str(message.get('role', 'user')).lower()
        content = str(message.get('content', ''))
        if role == 'system':
            converted.append(SystemMessage(content=content))
        elif role in ('assistant', 'ai'):
            converted.append(AIMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def stream_call(
    model_config_id: int | None,
    messages: Sequence[Any],
    on_chunk: Callable[[str], None] | None,
    check_active: Callable[[], Any],
    remaining_seconds: Callable[[], float] | float,
) -> str:
    """Call the selected configured model through its streaming transport only.

    ``remaining_seconds`` bounds both the provider's per-call timeout and retry
    waits.  Retrying is deliberately limited to a failure before the stream has
    emitted text; replaying a partial stream would make drafts ambiguous.
    """
    normalized_messages = _as_langchain_messages(messages)
    last_error: BaseException | None = None

    for attempt in range(MAX_STREAM_ATTEMPTS):
        _ensure_active(check_active)
        remaining = _remaining_seconds(remaining_seconds)
        if remaining <= 0:
            raise KnowledgeLLMTimeout('知识任务的模型调用时间已用尽。') from last_error

        received: list[str] = []
        try:
            # Manager construction can also expose an upstream availability
            # failure (for example a provider health probe), so it belongs to
            # the same bounded pre-output retry policy as stream creation.
            manager = get_llm_manager(config_id=model_config_id)
            stream = getattr(getattr(manager, 'current_llm', None), 'stream', None)
            if not callable(stream):
                raise StreamingUnsupportedError('所选模型不支持流式输出，无法用于项目知识任务。')
            call_budget = _single_request_budget(manager, remaining_seconds)
            if call_budget <= 0:
                raise KnowledgeLLMTimeout('知识任务的模型调用时间已用尽。')
            call_deadline = time.monotonic() + call_budget
            # LangChain providers pass this through to their client transport.
            # It is intentionally per-call instead of mutating saved config.
            for chunk in stream(normalized_messages, timeout=call_budget):
                if time.monotonic() >= call_deadline:
                    raise KnowledgeLLMTimeout('单次流式模型调用已超过其时间预算。')
                _ensure_active(check_active)
                text = _extract_text(chunk)
                if text:
                    received.append(text)
                    if on_chunk:
                        on_chunk(text)
            # Empty streams still have to honour both task activity and the
            # single-call deadline before being treated as a completed response.
            if time.monotonic() >= call_deadline:
                raise KnowledgeLLMTimeout('单次流式模型调用已超过其时间预算。')
            _ensure_active(check_active)
            return ''.join(received)
        except Exception as exc:
            if _is_task_stopped(exc) or isinstance(exc, (KnowledgeLLMTimeout, KnowledgeTaskInactive, StreamingUnsupportedError)):
                raise
            if received:
                raise StreamInterruptedError(
                    '流式输出已中断；为避免重复内容，本批次不会自动重放。'
                ) from exc
            last_error = exc
            if attempt >= MAX_STREAM_ATTEMPTS - 1 or not _is_transient_error(exc):
                raise KnowledgeLLMError(f'流式模型调用失败: {exc}') from exc

            wait_seconds = _retry_after_seconds(exc)
            remaining_after_error = _remaining_seconds(remaining_seconds)
            if wait_seconds >= remaining_after_error:
                raise KnowledgeLLMTimeout('等待模型服务重试将超出知识任务剩余时限。') from exc
            _wait_before_retry(wait_seconds, check_active, remaining_seconds)

    raise KnowledgeLLMError('流式模型调用失败。') from last_error
