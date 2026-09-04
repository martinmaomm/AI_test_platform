"""Callback-owned v4 ledger for one continuous browser exploration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .generation_contracts import AssertionRequirement, GenerationContractError, InputSpec, ScenarioPlan

TRACE_SCHEMA_VERSION = 4
FINALIZATION_TOOL_NAME = 'aits_finalize_path'
_MAX_EVENTS = 120
_MAX_EXCERPT = 1200
_MAX_RAW_OUTPUT = 20000
_ABSOLUTE_URL_RE = re.compile(r'https?://[^\s\'"<>]+', re.I)
_SCREENSHOT_DATA_URI_RE = re.compile(
    r'data:image/[^;,\s]+;base64,[a-z0-9+/=\s]+', re.I,
)
_LARGE_BASE64_RE = re.compile(r'(?<![a-z0-9+/=])[a-z0-9+/]{2048,}={0,2}', re.I)
_OBSERVATION_TOOLS = frozenset({
    'playwright_get_visible_text', 'playwright_get_visible_html', 'playwright_snapshot',
})
_INTERACTION_ACTIONS = ('click', 'fill', 'select', 'press', 'check', 'uncheck', 'hover')
_REPLAY_ACTIONS = frozenset({'navigate', *_INTERACTION_ACTIONS})
_RUNTIME_VALUE_FIELDS = frozenset({
    'value', 'values', 'text', 'option', 'options', 'option_value', 'selected_value',
})
_LOCATION_KEYS = ('current_url', 'page_url', 'url', 'location', 'current_path', 'page_path')


def _relative_path(value: Any) -> str:
    text = str(value or '').strip().rstrip('.,);')
    if not text:
        return ''
    if text.startswith('#/'):
        return f'/{text}'
    if not text.startswith(('/', 'http://', 'https://')):
        return ''
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ''
    path = parsed.path or '/'
    if parsed.fragment:
        path = f'{path}#{parsed.fragment}'
    return path if path.startswith('/') else ''


def _output_text(value: Any, *, _seen: set[int] | None = None) -> str:
    """Extract MCP text payloads without treating normal page copy as an error.

    MCP SDK versions return tool output as plain strings, ``ToolMessage``
    objects, content blocks, or nested ``result``/``structuredContent``
    dictionaries.  Prefer their human-readable payload instead of serializing
    wrapper metadata; the fallback retains unfamiliar output for diagnostics.
    """

    if value is None:
        return ''
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        return ''
    seen.add(identity)
    if isinstance(value, Mapping):
        for key in (
            'text', 'message', 'output', 'result', 'data', 'structuredContent',
            'structured_content', 'content',
        ):
            if key in value and value[key] is not value:
                text = _output_text(value[key], _seen=seen)
                if text:
                    return text
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        return ' '.join(
            text for item in value
            if (text := _output_text(item, _seen=seen))
        )
    for attribute in (
        'text', 'message', 'output', 'result', 'data', 'structuredContent',
        'structured_content', 'content',
    ):
        if hasattr(value, attribute):
            try:
                text = _output_text(getattr(value, attribute), _seen=seen)
            except Exception:
                continue
            if text:
                return text
    return str(value)


def _replace_runtime_values(
    text: str,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> tuple[str, dict[str, str]]:
    """Template non-credential variables while retaining test credentials verbatim."""
    replacements: dict[str, str] = {}
    for index, (ref, runtime_value) in enumerate(
        sorted(runtime_values.items(), key=lambda item: len(item[1]), reverse=True)
    ):
        if not runtime_value or ref in credential_refs:
            continue
        marker = f'__AITS_RUNTIME_{index}__'
        replacements[marker] = f'{{{{{ref}}}}}'
        text = text.replace(runtime_value, marker)
    return text, replacements


def _restore_runtime_markers(text: str, replacements: Mapping[str, str]) -> str:
    for marker, replacement in replacements.items():
        text = text.replace(marker, replacement)
    return text


def _safe_text(
    value: Any,
    *,
    limit: int,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> str:
    text, replacements = _replace_runtime_values(
        _output_text(value), runtime_values, credential_refs,
    )
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or '<url>', text)
    text = _SCREENSHOT_DATA_URI_RE.sub('<screenshot-data>', text)
    text = _LARGE_BASE64_RE.sub('<base64-omitted>', text)
    return _restore_runtime_markers(re.sub(r'\s+', ' ', text).strip(), replacements)[:limit]


def _safe_raw_text(
    value: Any,
    *,
    limit: int,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> str:
    """Keep a bounded recoverable payload; UI summaries use ``_safe_text``."""

    text, replacements = _replace_runtime_values(
        _output_text(value), runtime_values, credential_refs,
    )
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or '<url>', text)
    text = _SCREENSHOT_DATA_URI_RE.sub('<screenshot-data>', text)
    text = _LARGE_BASE64_RE.sub('<base64-omitted>', text)
    return _restore_runtime_markers(text.replace('\x00', '')[:limit], replacements)


def _safe_locator_value(
    value: Any,
    *,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> Any:
    """Keep locator values while retaining URL and size safety constraints."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_locator_value(
                item,
                runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_locator_value(
                item,
                runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for item in value
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text, replacements = _replace_runtime_values(str(value), runtime_values, credential_refs)
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or '<url>', text)
    return _restore_runtime_markers(text.replace('\x00', ''), replacements)


def _safe_action_value(
    value: Any,
    *,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_action_value(
                item,
                runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_action_value(
                item,
                runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for item in value
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(
        value, limit=200, runtime_values=runtime_values, credential_refs=credential_refs,
    )


def _as_mapping(value: Any, fallback: str = '') -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(fallback) if fallback else {}
    except (TypeError, ValueError):
        parsed = {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _tool_failed(output: Any, *, tool_name: str = '') -> bool:
    if isinstance(output, Mapping) and (
        output.get('error') or output.get('isError') or output.get('is_error')
        or str(output.get('status') or '').lower() in {'error', 'failed'}
    ):
        return True
    if (
        getattr(output, 'isError', False)
        or getattr(output, 'is_error', False)
        or str(getattr(output, 'status', '') or '').lower() in {'error', 'failed'}
    ):
        return True
    text = _output_text(output)
    if re.search(
        r'error executing tool|tool[ _-]?error|'
        r'(^|[\r\n])\s*(?:error|exception)\s*:|traceback',
        text, re.I,
    ):
        return True
    # An observation can legitimately include a user's "not found" copy, but
    # the MCP server's selector failure is structurally different and must not
    # be recorded as a successful page state.
    if _action(tool_name) == 'observe' and re.search(
        r'(?:element|locator)\s+(?:with\s+)?(?:selector|role|text|label|'
        r'placeholder)?[^\r\n]{0,500}\bnot found\b|'
        r'element with selector[^\r\n]{0,500}\bnot found\b',
        text,
        re.I,
    ):
        return True
    # Bare failure wording is only authoritative for an interaction. Observation
    # output may legitimately contain page copy such as "not found" or
    # "failed to load archived data".
    return _action(tool_name) in _REPLAY_ACTIONS and bool(
        re.search(r'operation failed|failed to', text, re.I)
        or recoverable_locator_failure(text)
    )


def recoverable_locator_failure(output: Any) -> bool:
    return bool(re.search(
        r'not found|no element|not visible|not enabled|strict mode|'
        r'locator.*resolved to|timeout.*(?:locator|element|waiting)|invalid selector',
        _output_text(output), re.I,
    ))


def _action(tool_name: str) -> str:
    if 'navigate' in tool_name or tool_name.endswith('_goto'):
        return 'navigate'
    if tool_name in _OBSERVATION_TOOLS or 'visible_' in tool_name or 'snapshot' in tool_name:
        return 'observe'
    for action in _INTERACTION_ACTIONS:
        if action in tool_name:
            return action
    if 'screenshot' in tool_name:
        return 'screenshot'
    return 'observe'


def _location_from_text(raw_output: str) -> str:
    stripped = raw_output.strip()
    if re.fullmatch(r'https?://[^\s\]}",\']+', stripped, re.I):
        return _relative_path(stripped)
    if match := re.search(
        r"(?im)(?:^|[\r\n,{])\s*[\"']?"
        r"(?:current[ _-]?(?:url|path)|page[ _-]?(?:url|path)|url|location)"
        r"[\"']?\s*[:=]\s*[\"']?"
        r"(https?://[^\s\]\},\"']+|[/#][^\s\]\},\"']*)",
        raw_output,
    ):
        return _relative_path(match.group(1))
    return ''


def _location_from_callback(value: Any, *, _seen: set[int] | None = None) -> str:
    """Extract only callback-owned URL metadata, including nested MCP results."""

    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        return ''
    seen.add(identity)
    if isinstance(value, Mapping):
        normalized = {str(key).lower(): item for key, item in value.items()}
        for key in _LOCATION_KEYS:
            if key in normalized and (path := _relative_path(normalized[key])):
                return path
        for item in value.values():
            if isinstance(item, (Mapping, list, tuple)) and (
                path := _location_from_callback(item, _seen=seen)
            ):
                return path
    elif isinstance(value, (list, tuple)):
        for item in value:
            if path := _location_from_callback(item, _seen=seen):
                return path
    else:
        for attribute in (
            'current_url', 'page_url', 'url', 'location', 'structuredContent',
            'structured_content', 'artifact', 'content',
        ):
            if hasattr(value, attribute):
                item = getattr(value, attribute)
                if attribute in _LOCATION_KEYS and (path := _relative_path(item)):
                    return path
                if path := _location_from_callback(item, _seen=seen):
                    return path
    return _location_from_text(_output_text(value))


def _locator_input(
    inputs: Mapping[str, Any],
    action: str,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    allowed = {
        'selector', 'locator', 'role', 'name', 'text', 'label', 'placeholder',
        'testid', 'test_id', 'exact', 'kwargs',
    }
    for key, value in inputs.items():
        name = str(key).lower()
        if name not in allowed:
            continue
        safe = _safe_locator_value(
            value, runtime_values=runtime_values, credential_refs=credential_refs,
        )
        if isinstance(safe, str) and len(safe) > 300:
            return {}
        locator[name] = safe
    if action == 'fill':
        locator['input_value'] = '<runtime_test_data>'
    return {key: value for key, value in locator.items() if value not in ('', None)}


def _iter_scalar_values(value: Any):
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_scalar_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_scalar_values(item)
    elif isinstance(value, (str, int, float, bool)):
        yield str(value)


def _matched_runtime_refs(
    inputs: Mapping[str, Any], runtime_values: Mapping[str, str], action: str,
) -> list[str]:
    if action not in {'fill', 'select'}:
        return []
    values: set[str] = set()
    for key, value in inputs.items():
        if str(key).lower() in _RUNTIME_VALUE_FIELDS:
            values.update(_iter_scalar_values(value))
    return [ref for ref, value in runtime_values.items() if value in values]


class PageState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_id: str = Field(pattern=r'^P[a-f0-9]{16}$')
    relative_path: str = Field(pattern=r'^/')
    fingerprint: str = Field(pattern=r'^[a-f0-9]{16}$')
    excerpt: str = Field(max_length=_MAX_EXCERPT)


class ExplorationEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=160)
    action: str = Field(
        pattern=r'^(?:navigate|observe|screenshot|click|fill|select|press|check|uncheck|hover)$'
    )
    status: str = Field(pattern=r'^(?:succeeded|failed|blocked)$')
    relative_path: str = Field(pattern=r'^/')
    locator_input: dict[str, Any] = Field(default_factory=dict)
    input_refs: list[str] = Field(default_factory=list)
    input_source: str = Field(default='', pattern=r'^(?:|generated|runtime|credential)$')
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    before_state_id: str = ''
    after_state_id: str = ''
    result_excerpt: str = Field(default='', max_length=_MAX_EXCERPT)
    # The UI can display ``result_excerpt`` while v5 exploration retains a
    # bounded raw observation for code repair and audit recovery.
    raw_output: str = Field(default='', max_length=_MAX_RAW_OUTPUT)
    screenshot_path: str = Field(default='', max_length=500)


class AssertionEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assertion_id: str = Field(pattern=r'^A[1-9][0-9]*$')
    criterion_index: int = Field(ge=0, le=19)
    phase: str = Field(pattern=r'^(?:main|cleanup)$')
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    kind: str = Field(
        pattern=r'^(?:visible|contains_ref|not_contains_ref|contains_literal|not_contains_literal)$'
    )
    input_ref: str = Field(default='', max_length=128)
    literal: str = Field(default='', max_length=300)


class FinalizedAction(BaseModel):
    """An agent-selected replay action with a safe, human-readable label."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    step_name: str = Field(min_length=1, max_length=80)

    @field_validator('step_name')
    @classmethod
    def _safe_step_name(cls, value: str) -> str:
        from .generation_contracts import _validate_safe_value
        _validate_safe_value(value, 'step_name', reject_absolute_url=True)
        return value


class FinalizedAssertion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assertion_id: str = Field(pattern=r'^A[1-9][0-9]*$')
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    # Concrete requirements use their plan semantics.  A deferred requirement
    # must provide one verified, concrete meaning selected from this callback.
    kind: str = Field(
        default='',
        pattern=r'^(?:|visible|contains_ref|not_contains_ref|contains_literal|not_contains_literal)$',
    )
    input_ref: str = Field(default='', max_length=128)
    literal: str = Field(default='', max_length=300)


class FinalizedPendingAssertion(BaseModel):
    """A target deliberately left for a human instead of faking an expect."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    assertion_id: str = Field(pattern=r'^A[1-9][0-9]*$')
    reason: str = Field(min_length=1, max_length=300)
    after_event_id: str = Field(pattern=r'^E[0-9]{6}$')

    @field_validator('reason')
    @classmethod
    def _safe_reason(cls, value: str) -> str:
        from .generation_contracts import _validate_safe_value
        _validate_safe_value(value, 'pending_assertion_reason', reject_absolute_url=True)
        return value


class PathFinalization(BaseModel):
    """The one-shot, callback-backed final path decision for a trace."""

    model_config = ConfigDict(extra='forbid')
    status: str = Field(default='missing', pattern=r'^(?:missing|valid|invalid)$')
    entry_event_id: str = Field(default='', pattern=r'^(?:|E[0-9]{6})$')
    main_actions: list[FinalizedAction] = Field(default_factory=list, max_length=_MAX_EVENTS)
    assertions: list[FinalizedAssertion] = Field(default_factory=list, max_length=20)
    pending_assertions: list[FinalizedPendingAssertion] = Field(default_factory=list, max_length=20)
    cleanup_actions: list[FinalizedAction] = Field(default_factory=list, max_length=_MAX_EVENTS)
    invalidation_event_id: str = Field(default='', pattern=r'^(?:|E[0-9]{6})$')
    error_code: str = Field(default='', max_length=80)
    message: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def _consistent_state(self):
        if self.status == 'valid' and (self.invalidation_event_id or self.error_code):
            raise ValueError('有效最终路径不能包含失效信息')
        if self.status == 'missing' and any((
            self.entry_event_id, self.main_actions, self.assertions, self.pending_assertions, self.cleanup_actions,
            self.invalidation_event_id, self.error_code, self.message,
        )):
            raise ValueError('缺失最终路径不能保留选择结果')
        return self


class LocatorEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str = Field(pattern=r'^L[0-9]{6}$')
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    action: str
    relative_path: str = Field(pattern=r'^/')
    strategy: str = Field(pattern=r'^(?:path|testid|role|label|placeholder|css|text)$')
    value: str = Field(max_length=300)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    validation: str = Field(pattern=r'^(?:succeeded|acceptable|fragile|rejected)$')
    validation_reasons: list[str] = Field(default_factory=list)
    state_fingerprint: str = ''


class ExplorationTrace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = Field(default=TRACE_SCHEMA_VERSION, frozen=True)
    start_path: str = Field(pattern=r'^/')
    events: list[ExplorationEvent] = Field(default_factory=list, max_length=_MAX_EVENTS)
    assertion_evidence: list[AssertionEvidence] = Field(default_factory=list, max_length=20)
    page_states: list[PageState] = Field(default_factory=list, max_length=_MAX_EVENTS)
    locator_evidence: list[LocatorEvidence] = Field(default_factory=list, max_length=_MAX_EVENTS)
    replay_event_ids: list[str] = Field(default_factory=list, max_length=_MAX_EVENTS)
    cleanup_event_ids: list[str] = Field(default_factory=list, max_length=_MAX_EVENTS)
    cleanup_verification_event_ids: list[str] = Field(default_factory=list, max_length=20)
    assertion_event_ids: list[str] = Field(default_factory=list, max_length=20)
    dynamic_input_refs: list[InputSpec] = Field(default_factory=list, max_length=20)
    finalization: PathFinalization = Field(default_factory=PathFinalization)
    cleanup: dict[str, Any] = Field(default_factory=dict)
    tool_stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    termination_reason: str = Field(default='', max_length=500)
    last_location: str = Field(default='/', pattern=r'^/')

    @field_validator('schema_version')
    @classmethod
    def _only_v4(cls, value):
        if value != TRACE_SCHEMA_VERSION:
            raise ValueError('仅支持 schema_version=4')
        return value

    @model_validator(mode='after')
    def _references_callback_events(self):
        events = {event.event_id: event for event in self.events}
        if len(events) != len(self.events) or len({event.sequence for event in self.events}) != len(self.events):
            raise ValueError('callback event_id 和 sequence 必须唯一')
        dynamic_names = [item.name for item in self.dynamic_input_refs]
        if len(dynamic_names) != len(set(dynamic_names)) or any(
            item.source != 'generated' for item in self.dynamic_input_refs
        ):
            raise ValueError('动态输入必须是唯一的 generated 变量定义')
        selected = {
            *self.replay_event_ids, *self.cleanup_event_ids,
            *self.cleanup_verification_event_ids, *self.assertion_event_ids,
        }
        if not selected <= set(events):
            raise ValueError('replay/cleanup/assertion event 必须来自 callback')
        if self.finalization.status != 'valid' and selected:
            raise ValueError('没有有效最终路径时不能选择回放或断言事件')
        if self.finalization.status == 'valid':
            selected_actions = [
                self.finalization.entry_event_id,
                *(item.event_id for item in self.finalization.main_actions),
            ]
            if self.replay_event_ids != selected_actions:
                raise ValueError('replay_event_ids 必须仅来自成功最终路径定稿')
            if self.cleanup_event_ids != [item.event_id for item in self.finalization.cleanup_actions]:
                raise ValueError('cleanup_event_ids 必须仅来自成功最终路径定稿')
            if self.assertion_event_ids != [item.event_id for item in self.finalization.assertions]:
                raise ValueError('assertion_event_ids 必须仅来自成功最终路径定稿')
        if any(events[event_id].status != 'succeeded' for event_id in selected):
            raise ValueError('最终路径只能选择成功 callback')
        evidence_event_ids = [item.event_id for item in self.assertion_evidence]
        if len({item.assertion_id for item in self.assertion_evidence}) != len(
            self.assertion_evidence
        ):
            raise ValueError('每个 assertion_id 只能保留一条 callback evidence')
        if evidence_event_ids != self.assertion_event_ids:
            raise ValueError('assertion_event_ids 必须精确对应 assertion evidence')
        cleanup_verification_ids = [
            item.event_id for item in self.assertion_evidence if item.phase == 'cleanup'
        ]
        if cleanup_verification_ids != self.cleanup_verification_event_ids:
            raise ValueError('cleanup verification 必须精确对应 cleanup assertion evidence')
        if any(
            events[event_id].status != 'succeeded'
            or events[event_id].action != 'observe'
            for event_id in self.cleanup_verification_event_ids
        ):
            raise ValueError('cleanup verification 必须来自成功页面观察 callback')
        if any(
            events[item.event_id].status != 'succeeded'
            or events[item.event_id].action != 'observe'
            for item in self.assertion_evidence
        ):
            raise ValueError('assertion evidence 必须来自满足语义的成功 observation callback')
        if any(
            event_ids != sorted(event_ids, key=lambda event_id: events[event_id].sequence)
            for event_ids in (
                self.replay_event_ids,
                self.cleanup_event_ids,
                self.cleanup_verification_event_ids,
                self.assertion_event_ids,
            )
        ):
            raise ValueError('选中的 callback event 必须保持真实 sequence 顺序')
        first_cleanup_sequence = min(
            (events[event_id].sequence for event_id in self.cleanup_event_ids), default=0,
        )
        if any(
            item.phase == 'main' and (
                first_cleanup_sequence
                and events[item.event_id].sequence >= first_cleanup_sequence
            )
            for item in self.assertion_evidence
        ):
            raise ValueError('main assertion 必须发生在 cleanup 开始之前')
        first_cleanup_action_sequence = min(
            (events[event_id].sequence for event_id in self.cleanup_event_ids), default=0,
        )
        last_cleanup_sequence = max(
            (events[event_id].sequence for event_id in self.cleanup_event_ids), default=0,
        )
        if self.cleanup_verification_event_ids and (
            not self.cleanup_event_ids
            or any(
                events[event_id].sequence <= first_cleanup_action_sequence
                for event_id in self.cleanup_verification_event_ids
            )
        ):
            raise ValueError('cleanup verification 前必须已有成功 cleanup 动作')
        cleanup_keys = {
            'status', 'attempted', 'evidence_event_ids', 'verification_event_ids',
            'residuals', 'reason',
        }
        if set(self.cleanup) != cleanup_keys:
            raise ValueError('cleanup summary 必须使用完整 v4 字段')
        status = str(self.cleanup.get('status') or '')
        if status not in {'not_required', 'missing', 'attempted', 'completed'}:
            raise ValueError('cleanup status 无效')
        if not isinstance(self.cleanup.get('attempted'), bool):
            raise ValueError('cleanup attempted 必须是布尔值')
        if not isinstance(self.cleanup.get('residuals'), list) or not all(
            isinstance(item, str) for item in self.cleanup['residuals']
        ):
            raise ValueError('cleanup residuals 必须是安全文本数组')
        if not isinstance(self.cleanup.get('reason'), str):
            raise ValueError('cleanup reason 必须是文本')
        if self.cleanup.get('evidence_event_ids') != self.cleanup_event_ids:
            raise ValueError('cleanup summary 动作证据与 callback 选择不一致')
        if self.cleanup.get('verification_event_ids') != self.cleanup_verification_event_ids:
            raise ValueError('cleanup summary 验证证据与 callback 选择不一致')
        final_cleanup_verified = bool(
            self.cleanup_event_ids
            and any(
                events[event_id].sequence > last_cleanup_sequence
                for event_id in self.cleanup_verification_event_ids
            )
        )
        expected_status = (
            'completed' if self.cleanup_event_ids and final_cleanup_verified
            else 'attempted' if self.cleanup_event_ids
            else status
        )
        if self.cleanup_event_ids and status != expected_status:
            raise ValueError('cleanup status 与真实动作/后续验证证据不一致')
        if not self.cleanup_event_ids and (
            self.cleanup_verification_event_ids
            or status not in {'not_required', 'missing'}
        ):
            raise ValueError('没有成功 cleanup 动作时不能声明 cleanup 验证或完成')
        attempted = bool(self.cleanup_event_ids)
        if self.cleanup.get('attempted') != attempted:
            raise ValueError('cleanup attempted 必须对应真实 callback 尝试')
        return self


def _evidence_locator(
    event: ExplorationEvent,
) -> tuple[str, str, dict[str, Any], str, list[str]] | None:
    if event.action == 'navigate':
        return 'path', event.relative_path, {}, 'succeeded', ['relative navigation callback succeeded']
    locator = event.locator_input
    if 'testid' in locator or 'test_id' in locator:
        return (
            'testid', str(locator.get('testid') or locator.get('test_id')), {}, 'succeeded',
            ['callback succeeded; count not independently queried'],
        )
    if 'role' in locator:
        kwargs = dict(locator.get('kwargs') or {}) if isinstance(locator.get('kwargs'), Mapping) else {}
        if locator.get('name'):
            kwargs['name'] = locator['name']
        if 'exact' in locator:
            kwargs['exact'] = bool(locator['exact'])
        return (
            'role', str(locator['role']), kwargs, 'acceptable',
            ['callback succeeded; count not independently queried'],
        )
    for name, strategy in (
        ('label', 'label'), ('placeholder', 'placeholder'), ('selector', 'css'),
        ('locator', 'css'), ('text', 'text'), ('name', 'text'),
    ):
        if locator.get(name):
            value = str(locator[name])
            quality = (
                'fragile'
                if re.search(r'(?:nth\(|:nth-|#[\w-]*\d{4,}|:visible$)', value, re.I)
                else 'acceptable'
            )
            reasons = ['callback succeeded; count not independently queried']
            if quality == 'fragile':
                reasons.append('dynamic or positional locator')
            kwargs = dict(locator.get('kwargs') or {}) if isinstance(locator.get('kwargs'), Mapping) else {}
            if 'exact' in locator and strategy in {'label', 'placeholder', 'text'}:
                kwargs['exact'] = bool(locator['exact'])
            return strategy, value, kwargs, quality, reasons
    return None


def build_locator_evidence(trace: ExplorationTrace) -> ExplorationTrace:
    evidence: list[LocatorEvidence] = []
    for event in trace.events:
        if event.status != 'succeeded':
            continue
        item = _evidence_locator(event)
        if item is None:
            continue
        strategy, value, kwargs, validation, reasons = item
        fingerprint = next((
            state.fingerprint for state in trace.page_states
            if state.state_id in {event.after_state_id, event.before_state_id}
        ), '')
        evidence.append(LocatorEvidence(
            evidence_id=f'L{event.sequence:06d}', event_id=event.event_id,
            action=event.action, relative_path=event.relative_path, strategy=strategy,
            value=value, kwargs=kwargs, validation=validation,
            validation_reasons=reasons, state_fingerprint=fingerprint,
        ))
    return trace.model_copy(update={'locator_evidence': evidence})


class ExplorationTraceRecorder:
    def __init__(
        self,
        start_path: str = '/',
        *,
        sensitive_values: tuple[str, ...] = (),
        trace_file: str | Path | None = None,
        runtime_namespace: str = '',
    ):
        del sensitive_values
        self.start_path = _relative_path(start_path) or '/'
        self._trace_file = Path(trace_file).resolve() if trace_file else None
        self._runtime_namespace = runtime_namespace
        self._events: list[ExplorationEvent] = []
        self._states: list[PageState] = []
        self._active: dict[Any, dict[str, Any]] = {}
        self._next_sequence = 1
        self._runtime_values: dict[str, str] = {}
        self._runtime_sources: dict[str, str] = {}
        self._runtime_value_kinds: dict[str, str] = {}
        self._plan_input_sources: dict[str, str] = {}
        self._plan_input_value_kinds: dict[str, str] = {}
        self._dynamic_input_specs: dict[str, InputSpec] = {}
        self._credential_refs: frozenset[str] = frozenset()
        self._assertion_requirements: dict[str, AssertionRequirement] = {}
        self._original_user_target = ''
        self._cleanup_expected = False
        self._finalization = PathFinalization()
        self._candidate_summary_sequence: int | None = None
        self._warnings: list[str] = []
        self._last_location = self.start_path
        self._last_state_id = ''
        if self._trace_file:
            self._trace_file.parent.mkdir(parents=True, exist_ok=True)
            self._trace_file.unlink(missing_ok=True)

    @property
    def events(self) -> list[ExplorationEvent]:
        return list(self._events)

    def configure_plan(self, plan: ScenarioPlan) -> None:
        self._assertion_requirements = {
            item.assertion_id: item for item in plan.assertion_requirements
        }
        self._original_user_target = plan.original_user_target
        self._plan_input_sources = plan.input_sources()
        self._plan_input_value_kinds = {
            item.name: item.value_kind for item in plan.input_refs
        }
        self._cleanup_expected = plan.cleanup_expected

    def configure_runtime(
        self, runtime_values: Mapping[str, str], input_sources: Mapping[str, str],
    ) -> None:
        self._runtime_values = {
            str(key): str(value) for key, value in runtime_values.items() if key and value
        }
        self._runtime_sources = {
            str(key): str(value) for key, value in input_sources.items() if key
        }
        self._runtime_value_kinds = {
            name: self._plan_input_value_kinds.get(name, 'text')
            for name in self._runtime_sources
        }
        self._credential_refs = frozenset(
            key for key, source in self._runtime_sources.items() if source == 'credential'
        )

    def declare_dynamic_input(self, *, value_kind: str, runtime_value: str) -> InputSpec:
        """Register one in-memory generated value without persisting that value."""
        if len(self._dynamic_input_specs) >= 20:
            raise GenerationContractError('DYNAMIC_INPUT_LIMIT_EXCEEDED')
        index = len(self._dynamic_input_specs) + 1
        name = f'DYNAMIC_INPUT_{index}'
        while name in self._plan_input_sources:
            index += 1
            name = f'DYNAMIC_INPUT_{index}'
        spec = InputSpec(name=name, source='generated', value_kind=value_kind)
        self._dynamic_input_specs[spec.name] = spec
        self._runtime_sources[spec.name] = spec.source
        self._runtime_value_kinds[spec.name] = spec.value_kind
        self._runtime_values[spec.name] = str(runtime_value)
        return spec

    def candidate_summary(self) -> dict[str, Any]:
        """Return callback facts that the same agent may use to finalize."""
        self._candidate_summary_sequence = self._events[-1].sequence if self._events else 0
        candidates = []
        for event in self._events:
            locator = _evidence_locator(event)
            compilable = bool(
                event.status == 'succeeded'
                and event.action in _REPLAY_ACTIONS
                and locator is not None
                and locator[3] not in {'fragile', 'rejected'}
                and not (
                    event.action in {'fill', 'select'}
                    and (len(event.input_refs) != 1 or not event.input_source)
                )
                and not (
                    event.action == 'press'
                    and not str(event.action_arguments.get('key') or '').strip()
                )
            )
            candidates.append({
                'event_id': event.event_id,
                'sequence': event.sequence,
                'action': event.action,
                'status': event.status,
                'relative_path': event.relative_path,
                'locator': event.locator_input,
                'input_refs': event.input_refs,
                'input_source': event.input_source,
                'observation_summary': event.result_excerpt if event.action == 'observe' else '',
                'compilable': compilable,
                'unmapped_input': event.action in {'fill', 'select'} and not compilable,
            })
        entry = next((item.event_id for item in self._events if item.action == 'navigate' and item.status == 'succeeded'), '')
        return {
            'entry_event_id': entry,
            'candidate_sequence': self._candidate_summary_sequence,
            'events': candidates,
            'finalization_status': self._finalization.status,
            'finalization_error_code': self._finalization.error_code,
        }

    def _require_action(self, event_id: str, *, cleanup: bool) -> ExplorationEvent:
        event = next((item for item in self._events if item.event_id == event_id), None)
        if event is None:
            raise GenerationContractError('FINALIZATION_UNKNOWN_EVENT')
        if event.status != 'succeeded' or event.action not in _INTERACTION_ACTIONS:
            raise GenerationContractError('FINALIZATION_ACTION_NOT_SUCCESSFUL')
        locator = _evidence_locator(event)
        if locator is None or locator[3] in {'fragile', 'rejected'}:
            raise GenerationContractError('FINALIZATION_ACTION_LOCATOR_UNSTABLE')
        if event.action in {'fill', 'select'} and (
            len(event.input_refs) != 1
            or event.input_source not in {'generated', 'runtime', 'credential'}
            or self._runtime_sources.get(event.input_refs[0]) != event.input_source
        ):
            raise GenerationContractError('FINALIZATION_INPUT_REF_UNMAPPED')
        if event.action == 'press' and not str(event.action_arguments.get('key') or '').strip():
            raise GenerationContractError('FINALIZATION_PRESS_KEY_MISSING')
        return event

    def _assertion_matches(self, requirement: AssertionRequirement, event: ExplorationEvent) -> bool:
        if event.status != 'succeeded' or event.action != 'observe' or _evidence_locator(event) is None:
            return False
        text = event.result_excerpt
        if not text:
            return False
        if requirement.kind == 'visible':
            return True
        if requirement.kind in {'contains_literal', 'not_contains_literal'}:
            contains = requirement.literal in text
        else:
            value = self._runtime_values.get(requirement.input_ref, '')
            if not value:
                return False
            expected = value if requirement.input_ref in self._credential_refs else f'{{{{{requirement.input_ref}}}}}'
            contains = expected in text
        return contains if requirement.kind.startswith('contains_') else not contains

    def _resolved_assertion(
        self,
        requirement: AssertionRequirement,
        selection: FinalizedAssertion,
        event: ExplorationEvent,
    ) -> tuple[str, str, str]:
        """Return callback-backed concrete semantics without weakening fixed goals."""
        if requirement.kind != 'deferred':
            if any((
                selection.kind and selection.kind != requirement.kind,
                selection.input_ref and selection.input_ref != requirement.input_ref,
                selection.literal and selection.literal != requirement.literal,
            )) or not self._assertion_matches(requirement, event):
                raise GenerationContractError('FINALIZATION_ASSERTION_EVIDENCE_INVALID')
            return requirement.kind, requirement.input_ref, requirement.literal

        kind, input_ref, literal = selection.kind, selection.input_ref, selection.literal
        if kind not in {
            'visible', 'contains_ref', 'not_contains_ref',
            'contains_literal', 'not_contains_literal',
        }:
            raise GenerationContractError('FINALIZATION_DEFERRED_ASSERTION_KIND_INVALID')
        if event.status != 'succeeded' or event.action != 'observe' or _evidence_locator(event) is None:
            raise GenerationContractError('FINALIZATION_ASSERTION_EVIDENCE_INVALID')
        text = event.result_excerpt
        if not text:
            raise GenerationContractError('FINALIZATION_ASSERTION_EVIDENCE_INVALID')
        if kind == 'visible' and (input_ref or literal):
            raise GenerationContractError('FINALIZATION_DEFERRED_ASSERTION_SHAPE_INVALID')
        if kind in {'contains_ref', 'not_contains_ref'}:
            if not input_ref or literal or input_ref not in self._plan_input_sources:
                raise GenerationContractError('FINALIZATION_DEFERRED_ASSERTION_SHAPE_INVALID')
            expected = self._runtime_values.get(input_ref, '')
            expected = expected if input_ref in self._credential_refs else f'{{{{{input_ref}}}}}'
            contains = bool(expected and expected in text)
            if (kind == 'contains_ref' and not contains) or (kind == 'not_contains_ref' and contains):
                raise GenerationContractError('FINALIZATION_ASSERTION_EVIDENCE_INVALID')
        if kind in {'contains_literal', 'not_contains_literal'}:
            if input_ref or not literal:
                raise GenerationContractError('FINALIZATION_DEFERRED_ASSERTION_SHAPE_INVALID')
            # A negative literal cannot be proven from its absence unless it
            # was user-owned or was observed before the asserted transition.
            # This prevents an agent from inventing a harmless string just to
            # obtain an always-passing negative expectation.
            observed_before = any(
                item.sequence < event.sequence
                and item.status == 'succeeded'
                and item.action == 'observe'
                and literal in item.result_excerpt
                for item in self._events
            )
            if (
                kind == 'not_contains_literal'
                and literal not in self._original_user_target
                and not observed_before
            ):
                raise GenerationContractError('FINALIZATION_DEFERRED_ASSERTION_LITERAL_UNPROVEN')
            contains = literal in text
            if (kind == 'contains_literal' and not contains) or (kind == 'not_contains_literal' and contains):
                raise GenerationContractError('FINALIZATION_ASSERTION_EVIDENCE_INVALID')
        return kind, input_ref, literal

    def finalize_path(
        self,
        *,
        main_actions: list[FinalizedAction],
        assertions: list[FinalizedAssertion],
        cleanup_actions: list[FinalizedAction],
        pending_assertions: list[FinalizedPendingAssertion] | None = None,
    ) -> dict[str, str]:
        """Validate a one-shot selection against callback-owned facts only."""
        try:
            latest_sequence = self._events[-1].sequence if self._events else 0
            if self._candidate_summary_sequence is None:
                raise GenerationContractError('FINALIZATION_CANDIDATES_REQUIRED')
            if self._candidate_summary_sequence != latest_sequence:
                raise GenerationContractError('FINALIZATION_CANDIDATES_STALE')
            entry = next((item for item in self._events if item.action == 'navigate' and item.status == 'succeeded'), None)
            if entry is None:
                raise GenerationContractError('FINALIZATION_ENTRY_NAVIGATE_MISSING')
            pending_assertions = list(pending_assertions or [])
            selected = [*(item.event_id for item in main_actions), *(item.event_id for item in cleanup_actions)]
            if len(selected) != len(set(selected)):
                raise GenerationContractError('FINALIZATION_DUPLICATE_EVENT')
            if any(item.event_id == entry.event_id for item in [*main_actions, *cleanup_actions]):
                raise GenerationContractError('FINALIZATION_ENTRY_NAVIGATE_AUTOMATIC')
            main_events = [self._require_action(item.event_id, cleanup=False) for item in main_actions]
            cleanup_events = [self._require_action(item.event_id, cleanup=True) for item in cleanup_actions]
            if not main_events and not assertions:
                raise GenerationContractError('FINALIZATION_MAIN_ACTION_MISSING')
            if any(item.sequence <= entry.sequence for item in main_events):
                raise GenerationContractError('FINALIZATION_MAIN_BEFORE_ENTRY')
            if [item.sequence for item in main_events] != sorted(item.sequence for item in main_events):
                raise GenerationContractError('FINALIZATION_MAIN_SEQUENCE_INVALID')
            if [item.sequence for item in cleanup_events] != sorted(item.sequence for item in cleanup_events):
                raise GenerationContractError('FINALIZATION_CLEANUP_SEQUENCE_INVALID')
            if cleanup_events and main_events and cleanup_events[0].sequence <= main_events[-1].sequence:
                raise GenerationContractError('FINALIZATION_CLEANUP_ORDER_INVALID')
            selected_actions = [*main_events, *cleanup_events]
            main_input_refs = {
                event.input_refs[0]
                for event in main_events
                if event.action in {'fill', 'select'} and len(event.input_refs) == 1
            }
            required_input_refs = {
                ref for ref, source in self._plan_input_sources.items()
                if source in {'generated', 'credential'}
            }
            if not required_input_refs <= main_input_refs:
                raise GenerationContractError('FINALIZATION_NON_RUNTIME_INPUT_MISSING')
            required_ids = set(self._assertion_requirements)
            submitted_ids = [item.assertion_id for item in assertions]
            pending_ids = [item.assertion_id for item in pending_assertions]
            if (
                set([*submitted_ids, *pending_ids]) != required_ids
                or len([*submitted_ids, *pending_ids]) != len(set([*submitted_ids, *pending_ids]))
            ):
                raise GenerationContractError('FINALIZATION_ASSERTION_COVERAGE_INVALID')
            assertion_evidence: list[AssertionEvidence] = []
            for selection in assertions:
                requirement = self._assertion_requirements[selection.assertion_id]
                event = next((item for item in self._events if item.event_id == selection.event_id), None)
                if event is None:
                    raise GenerationContractError('FINALIZATION_UNKNOWN_EVENT')
                kind, input_ref, literal = self._resolved_assertion(requirement, selection, event)
                if requirement.phase == 'main' and event.sequence <= entry.sequence:
                    raise GenerationContractError('FINALIZATION_ASSERTION_ORDER_INVALID')
                if requirement.phase == 'cleanup':
                    if not cleanup_events or event.sequence <= cleanup_events[-1].sequence:
                        raise GenerationContractError('FINALIZATION_CLEANUP_VERIFICATION_INVALID')
                if input_ref and not any(
                    action.sequence < event.sequence
                    and action.action in {'fill', 'select'}
                    and action.input_refs == [input_ref]
                    for action in selected_actions
                ):
                    raise GenerationContractError('FINALIZATION_ASSERTION_INPUT_DEPENDENCY_MISSING')
                assertion_evidence.append(AssertionEvidence(
                    assertion_id=requirement.assertion_id,
                    criterion_index=requirement.criterion_index,
                    phase=requirement.phase, event_id=event.event_id,
                    kind=kind, input_ref=input_ref, literal=literal,
                ))
            main_action_ids = {item.event_id for item in main_actions}
            cleanup_action_ids = {item.event_id for item in cleanup_actions}
            for pending in pending_assertions:
                requirement = self._assertion_requirements[pending.assertion_id]
                anchors = cleanup_action_ids if requirement.phase == 'cleanup' else main_action_ids
                if pending.after_event_id not in anchors:
                    raise GenerationContractError('FINALIZATION_PENDING_ASSERTION_ANCHOR_INVALID')
            if [next(event.sequence for event in self._events if event.event_id == item.event_id) for item in assertion_evidence] != sorted(
                next(event.sequence for event in self._events if event.event_id == item.event_id) for item in assertion_evidence
            ):
                raise GenerationContractError('FINALIZATION_ASSERTION_SEQUENCE_INVALID')
            if any(
                item.phase == 'main' and cleanup_events and next(
                    event.sequence for event in self._events if event.event_id == item.event_id
                ) >= cleanup_events[0].sequence
                for item in assertion_evidence
            ):
                raise GenerationContractError('FINALIZATION_ASSERTION_ORDER_INVALID')
            if self._cleanup_expected and not cleanup_events:
                raise GenerationContractError('FINALIZATION_CLEANUP_MISSING')
            self._finalization = PathFinalization(
                status='valid', entry_event_id=entry.event_id, main_actions=main_actions,
                assertions=assertions, pending_assertions=pending_assertions,
                cleanup_actions=cleanup_actions,
            )
            return {'status': 'accepted', 'entry_event_id': entry.event_id}
        except GenerationContractError as exc:
            self._finalization = PathFinalization(
                status='invalid', error_code=str(exc), message='最终路径定稿被拒绝，请读取候选摘要后修正。',
            )
            raise

    def _finalized_assertion_evidence(self) -> list[AssertionEvidence]:
        evidence: list[AssertionEvidence] = []
        for selection in self._finalization.assertions:
            requirement = self._assertion_requirements[selection.assertion_id]
            event = next(item for item in self._events if item.event_id == selection.event_id)
            kind, input_ref, literal = self._resolved_assertion(requirement, selection, event)
            evidence.append(AssertionEvidence(
                assertion_id=requirement.assertion_id, criterion_index=requirement.criterion_index,
                phase=requirement.phase, event_id=selection.event_id,
                kind=kind, input_ref=input_ref, literal=literal,
            ))
        return sorted(evidence, key=lambda item: next(
            event.sequence for event in self._events if event.event_id == item.event_id
        ))

    def on_tool_start(
        self,
        serialized: Mapping[str, Any] | None,
        input_str: str,
        *,
        run_id: Any = None,
        inputs: Any = None,
    ) -> None:
        name = str((serialized or {}).get('name') or 'browser_tool').lower()
        parsed_inputs = _as_mapping(inputs, input_str)
        if not name.startswith('playwright_') and name != 'browser_console_logs':
            return
        sequence = self._next_sequence
        self._next_sequence += 1
        event_id = f'E{sequence:06d}'
        self._active[run_id] = {
            'sequence': sequence,
            'event_id': event_id,
            'tool_name': name,
            'inputs': parsed_inputs,
            'before_state_id': self._last_state_id,
            'runtime_values': dict(self._runtime_values),
            'input_sources': dict(self._runtime_sources),
        }

    def _assertion_satisfied(
        self, requirement: AssertionRequirement, raw_output: str,
        runtime_values: Mapping[str, str],
    ) -> bool:
        if not raw_output.strip():
            return False
        if requirement.kind == 'visible':
            return True
        if requirement.kind in {'contains_literal', 'not_contains_literal'}:
            contains = requirement.literal in raw_output
            return contains if requirement.kind == 'contains_literal' else not contains
        expected = runtime_values.get(requirement.input_ref, '')
        if not expected:
            return False
        contains = expected in raw_output
        return contains if requirement.kind == 'contains_ref' else not contains

    def _complete(self, output: Any, *, run_id: Any, status: str) -> None:
        active = self._active.pop(run_id, None)
        if active is None or len(self._events) >= _MAX_EVENTS:
            return
        sequence = active['sequence']
        event_id = active['event_id']
        tool_name = active['tool_name']
        inputs = active['inputs']
        runtime_values = active['runtime_values']
        input_sources = active['input_sources']
        action = _action(tool_name)
        raw_output = _output_text(output)
        callback_path = _location_from_callback(output)
        input_path = ''
        if action == 'navigate':
            input_path = next((
                _relative_path(inputs.get(key)) for key in ('url', 'target', 'href', 'path')
                if _relative_path(inputs.get(key))
            ), '')
        path = callback_path or input_path or self._last_location
        excerpt = _safe_text(
            raw_output, limit=_MAX_EXCERPT, runtime_values=runtime_values,
            credential_refs=self._credential_refs,
        )
        raw_observation = _safe_raw_text(
            raw_output, limit=_MAX_RAW_OUTPUT, runtime_values=runtime_values,
            credential_refs=self._credential_refs,
        )
        state_id = ''
        if action == 'observe' and status == 'succeeded' and excerpt:
            fingerprint = hashlib.sha256(f'{path}|{excerpt}'.encode()).hexdigest()[:16]
            state_id = f'P{fingerprint}'
            if not any(item.state_id == state_id for item in self._states):
                self._states.append(PageState(
                    state_id=state_id, relative_path=path or '/',
                    fingerprint=fingerprint, excerpt=excerpt,
                ))
            self._last_state_id = state_id
        screenshot_path = ''
        if action == 'screenshot' and (
            match := re.search(r'(?:[\w.-]+/)*[\w.-]+\.png\b', raw_output)
        ):
            screenshot_path = _safe_text(
                match.group(0), limit=500, runtime_values=runtime_values,
                credential_refs=self._credential_refs,
            )
        input_refs = _matched_runtime_refs(inputs, runtime_values, action)
        arguments = {
            key: _safe_action_value(
                value,
                runtime_values=runtime_values, credential_refs=self._credential_refs,
            )
            for key, value in inputs.items()
            if key in {'key', 'button', 'click_count', 'delay', 'force', 'modifiers'}
        }
        event = ExplorationEvent(
            event_id=event_id, sequence=sequence, tool_name=tool_name, action=action,
            status=status,
            relative_path=path or '/',
            locator_input=_locator_input(
                inputs, action, runtime_values, self._credential_refs,
            ),
            input_refs=input_refs,
            input_source=input_sources.get(input_refs[0], '') if len(input_refs) == 1 else '',
            action_arguments=arguments,
            before_state_id=active['before_state_id'],
            after_state_id=state_id or self._last_state_id,
            result_excerpt=excerpt,
            raw_output=raw_observation,
            screenshot_path=screenshot_path,
        )
        self._events.append(event)
        if self._finalization.status == 'valid':
            self._finalization = self._finalization.model_copy(update={
                'status': 'invalid', 'invalidation_event_id': event.event_id,
                'error_code': 'FINALIZATION_STALE',
                'message': '最终路径定稿后新增了浏览器 callback，必须重新定稿。',
            })
        if status == 'succeeded' and (callback_path or input_path):
            self._last_location = path or self._last_location
        if self._trace_file:
            with self._trace_file.open('a', encoding='utf-8') as stream:
                stream.write(event.model_dump_json() + '\n')

    def on_tool_end(self, output: Any, *, run_id: Any = None) -> None:
        tool_name = str(self._active.get(run_id, {}).get('tool_name') or '')
        self._complete(
            output, run_id=run_id,
            status='failed' if _tool_failed(output, tool_name=tool_name) else 'succeeded',
        )

    def on_tool_error(self, error: BaseException, *, run_id: Any = None) -> None:
        self._complete(error, run_id=run_id, status='failed')

    def mark_blocked(
        self, serialized, input_str, *, run_id=None, inputs=None, error=None,
    ) -> None:
        if run_id not in self._active:
            self.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        self._complete(error or 'blocked by safety policy', run_id=run_id, status='blocked')

    def build(
        self,
        *,
        tool_stats: Mapping[str, Any],
        termination_reason: str = '',
        warnings: list[str] | None = None,
    ) -> ExplorationTrace:
        events_by_id = {item.event_id: item for item in self._events}
        valid = self._finalization.status == 'valid'
        replay = ([self._finalization.entry_event_id] + [
            item.event_id for item in self._finalization.main_actions
        ]) if valid else []
        cleanup_ids = [item.event_id for item in self._finalization.cleanup_actions] if valid else []
        assertion_evidence = self._finalized_assertion_evidence() if valid else []
        assertions = [item.event_id for item in assertion_evidence]
        cleanup_verifications = [item.event_id for item in assertion_evidence if item.phase == 'cleanup']
        last_cleanup_sequence = max((events_by_id[event_id].sequence for event_id in cleanup_ids), default=0)
        final_cleanup_verified = bool(cleanup_ids and any(
            events_by_id[event_id].sequence > last_cleanup_sequence for event_id in cleanup_verifications
        ))
        cleanup_summary = {
            'status': (
                'completed' if self._cleanup_expected and cleanup_ids and final_cleanup_verified
                else 'attempted' if self._cleanup_expected and cleanup_ids
                else 'missing' if self._cleanup_expected
                else 'not_required'
            ),
            'attempted': bool(cleanup_ids),
            'evidence_event_ids': cleanup_ids,
            'verification_event_ids': cleanup_verifications,
            'residuals': [],
            'reason': (
                '' if not self._cleanup_expected or (cleanup_ids and final_cleanup_verified)
                else 'the final cleanup action lacks a later semantic verification callback'
                if cleanup_ids
                else 'cleanup was required but no successful cleanup callback was recorded'
            ),
        }
        trace = ExplorationTrace(
            start_path=self.start_path,
            events=self._events,
            assertion_evidence=assertion_evidence,
            page_states=self._states,
            replay_event_ids=replay,
            cleanup_event_ids=cleanup_ids,
            cleanup_verification_event_ids=cleanup_verifications,
            assertion_event_ids=assertions,
            dynamic_input_refs=list(self._dynamic_input_specs.values()),
            finalization=self._finalization,
            cleanup=cleanup_summary,
            tool_stats=dict(tool_stats),
            warnings=list(dict.fromkeys([
                *(warnings or []), *self._warnings,
            ])),
            termination_reason=termination_reason,
            last_location=self._last_location,
        )
        return build_locator_evidence(trace)

    def discard_active(self, run_id: Any) -> None:
        """Forget a browser call that the finalization-only guard never executed."""
        self._active.pop(run_id, None)


def effective_scenario_plan(plan: ScenarioPlan, trace: ExplorationTrace) -> ScenarioPlan:
    """Merge only dynamic refs selected by the valid final path into a plan."""
    if trace.finalization.status != 'valid' or not trace.dynamic_input_refs:
        return plan
    events = {event.event_id: event for event in trace.events}
    dynamic_specs_by_name = {item.name: item for item in trace.dynamic_input_refs}
    selected_dynamic_refs = {
        ref
        for event_id in [*trace.replay_event_ids, *trace.cleanup_event_ids]
        for ref in events[event_id].input_refs
        if ref in dynamic_specs_by_name
    }
    if not selected_dynamic_refs:
        return plan
    existing_specs = {item.name: item for item in plan.input_refs}
    additions: list[dict[str, Any]] = []
    for name in sorted(selected_dynamic_refs):
        dynamic_spec = dynamic_specs_by_name[name]
        existing = existing_specs.get(name)
        if existing is None:
            additions.append(dynamic_spec.model_dump(mode='json'))
            continue
        if existing.model_dump(mode='json') != dynamic_spec.model_dump(mode='json'):
            raise GenerationContractError('DYNAMIC_INPUT_CONFLICT')
    if not additions:
        return plan
    return ScenarioPlan.model_validate({
        **plan.model_dump(mode='json'),
        'input_refs': [
            *(item.model_dump(mode='json') for item in plan.input_refs),
            *additions,
        ],
    })


def required_replay_evidence_gaps(
    trace: ExplorationTrace, plan: ScenarioPlan | None = None,
) -> list[dict[str, str]]:
    evidence = {item.event_id: item for item in trace.locator_evidence}
    events = {item.event_id: item for item in trace.events}
    gaps: list[dict[str, str]] = []
    if trace.finalization.status != 'valid':
        gaps.append({
            'event_id': 'finalization',
            'reason': trace.finalization.error_code or '缺少有效最终路径定稿',
        })
    if not trace.finalization.entry_event_id:
        gaps.append({'event_id': 'entry-navigate', 'reason': '缺少成功入口 navigate callback'})
    for event_id in [*trace.replay_event_ids, *trace.cleanup_event_ids]:
        event = events.get(event_id)
        item = evidence.get(event_id)
        if event is None or event.status != 'succeeded':
            gaps.append({'event_id': event_id, 'reason': '回放事件不是成功 callback'})
        elif item is None or item.validation in {'fragile', 'rejected'}:
            gaps.append({'event_id': event_id, 'reason': '成功动作缺少稳定定位器证据'})
        elif event.action in {'fill', 'select'} and len(event.input_refs) != 1:
            gaps.append({'event_id': event_id, 'reason': '输入动作未精确映射到一个运行时变量'})
    for item in trace.assertion_evidence:
        locator = evidence.get(item.event_id)
        if locator is None or locator.validation in {'fragile', 'rejected'}:
            gaps.append({
                'event_id': item.event_id,
                'reason': f'{item.assertion_id} 缺少稳定的 callback 断言定位证据',
            })
    if plan is not None:
        requirements = {
            item.assertion_id: item for item in plan.assertion_requirements
        }
        covered = {
            *(item.assertion_id for item in trace.assertion_evidence),
            *(item.assertion_id for item in trace.finalization.pending_assertions),
        }
        for requirement in plan.assertion_requirements:
            if requirement.assertion_id not in covered:
                gaps.append({
                    'event_id': requirement.assertion_id,
                    'reason': 'success criterion 没有满足机器语义的 callback 证据',
                })
        if plan.cleanup_expected and not trace.cleanup_event_ids:
            gaps.append({
                'event_id': 'cleanup',
                'reason': '计划要求清理，但没有成功 cleanup callback 证据',
            })
        elif (
            plan.cleanup_expected
            and not trace.cleanup_verification_event_ids
            and not any(
                requirements.get(item.assertion_id)
                and requirements[item.assertion_id].phase == 'cleanup'
                for item in trace.finalization.pending_assertions
            )
        ):
            gaps.append({
                'event_id': 'cleanup-verification',
                'reason': '清理动作已尝试，但没有后续语义观察 callback 确认清理结果',
            })
    return gaps


def trace_has_minimum_page_state(trace: ExplorationTrace) -> bool:
    return bool(trace.page_states)


def coerce_trace(value: Any) -> ExplorationTrace:
    if isinstance(value, ExplorationTrace):
        return value
    try:
        return ExplorationTrace.model_validate(value)
    except Exception as exc:
        raise GenerationContractError('exploration_trace_invalid') from exc
