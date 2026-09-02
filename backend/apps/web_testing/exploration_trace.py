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

from .generation_contracts import AssertionRequirement, GenerationContractError, ScenarioPlan
from .generation_security import redact_text

TRACE_SCHEMA_VERSION = 4
CHECKPOINT_TOOL_NAME = 'aits_record_checkpoint'
_MAX_EVENTS = 120
_MAX_EXCERPT = 1200
_SECRET_KEY_RE = re.compile(r'(?i)(password|passwd|token|secret|cookie|authorization|api[_-]?key)')
_ABSOLUTE_URL_RE = re.compile(r'https?://[^\s\'"<>]+', re.I)
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


def _output_text(value: Any) -> str:
    if hasattr(value, 'content'):
        return _output_text(value.content)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        return ' '.join(_output_text(item) for item in value)
    return str(value or '')


def _replace_runtime_values(
    text: str,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    for index, (ref, runtime_value) in enumerate(
        sorted(runtime_values.items(), key=lambda item: len(item[1]), reverse=True)
    ):
        if not runtime_value:
            continue
        marker = f'__AITS_RUNTIME_{index}__'
        replacements[marker] = (
            '<runtime_sensitive_data>' if ref in credential_refs else f'{{{{{ref}}}}}'
        )
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
    text = redact_text(text)
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or '<url>', text)
    text = _restore_runtime_markers(text, replacements)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _safe_locator_value(
    value: Any,
    *,
    runtime_values: Mapping[str, str],
    credential_refs: frozenset[str],
) -> Any:
    """Redact runtime values without rewriting valid selector syntax."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_locator_value(
                item, runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for key, item in value.items()
            if not _SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_locator_value(
                item, runtime_values=runtime_values, credential_refs=credential_refs,
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
                item, runtime_values=runtime_values, credential_refs=credential_refs,
            )
            for key, item in value.items()
            if not _SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_action_value(
                item, runtime_values=runtime_values, credential_refs=credential_refs,
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
        if name not in allowed or _SECRET_KEY_RE.search(name):
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
    phase: str = Field(default='exploration', pattern=r'^(?:exploration|main|assertion|cleanup)$')
    intent: str = Field(default='probe', pattern=r'^(?:probe|replay|evidence|cleanup)$')
    checkpoint_id: str = Field(default='', pattern=r'^(?:|M[0-9]{6})$')
    assertion_id: str = Field(default='', pattern=r'^(?:|A[1-9][0-9]*)$')
    assertion_kind: str = Field(
        default='',
        pattern=r'^(?:|visible|contains_ref|not_contains_ref|contains_literal|not_contains_literal)$',
    )
    assertion_status: str = Field(default='', pattern=r'^(?:|satisfied|not_satisfied)$')
    relative_path: str = Field(pattern=r'^/')
    locator_input: dict[str, Any] = Field(default_factory=dict)
    input_refs: list[str] = Field(default_factory=list)
    input_source: str = Field(default='', pattern=r'^(?:|generated|runtime|credential)$')
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    before_state_id: str = ''
    after_state_id: str = ''
    result_excerpt: str = Field(default='', max_length=_MAX_EXCERPT)
    screenshot_path: str = Field(default='', max_length=500)


class CheckpointMarker(BaseModel):
    model_config = ConfigDict(extra='forbid')
    checkpoint_id: str = Field(pattern=r'^M[0-9]{6}$')
    phase: str = Field(pattern=r'^(?:main|assertion|cleanup)$')
    intent: str = Field(pattern=r'^(?:replay|evidence|cleanup)$')
    assertion_id: str = Field(default='', pattern=r'^(?:|A[1-9][0-9]*)$')
    bound_event_id: str = Field(default='', pattern=r'^(?:|E[0-9]{6})$')
    binding_status: str = Field(
        default='pending', pattern=r'^(?:pending|succeeded|failed|blocked|invalid|unbound)$',
    )


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
    checkpoints: list[CheckpointMarker] = Field(default_factory=list, max_length=_MAX_EVENTS)
    assertion_evidence: list[AssertionEvidence] = Field(default_factory=list, max_length=20)
    page_states: list[PageState] = Field(default_factory=list, max_length=_MAX_EVENTS)
    locator_evidence: list[LocatorEvidence] = Field(default_factory=list, max_length=_MAX_EVENTS)
    replay_event_ids: list[str] = Field(default_factory=list, max_length=_MAX_EVENTS)
    cleanup_event_ids: list[str] = Field(default_factory=list, max_length=_MAX_EVENTS)
    cleanup_verification_event_ids: list[str] = Field(default_factory=list, max_length=20)
    assertion_event_ids: list[str] = Field(default_factory=list, max_length=20)
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
        checkpoints = {item.checkpoint_id: item for item in self.checkpoints}
        if len(checkpoints) != len(self.checkpoints):
            raise ValueError('checkpoint_id 必须唯一')
        selected = {
            *self.replay_event_ids, *self.cleanup_event_ids,
            *self.cleanup_verification_event_ids, *self.assertion_event_ids,
        }
        if not selected <= set(events):
            raise ValueError('replay/cleanup/assertion event 必须来自 callback')
        for event_id in selected:
            event = events[event_id]
            checkpoint = checkpoints.get(event.checkpoint_id)
            if (
                checkpoint is None
                or checkpoint.bound_event_id != event_id
                or checkpoint.binding_status != 'succeeded'
                or checkpoint.phase != event.phase
                or checkpoint.intent != event.intent
                or checkpoint.assertion_id != event.assertion_id
            ):
                raise ValueError('选中 event 必须由匹配的成功 checkpoint 绑定')
        if any(
            events[event_id].status != 'succeeded'
            or events[event_id].phase != 'main'
            or events[event_id].intent != 'replay'
            for event_id in self.replay_event_ids
        ):
            raise ValueError('主回放事件必须是 checkpoint 绑定的成功 callback')
        if any(
            events[event_id].status != 'succeeded'
            or events[event_id].phase != 'cleanup'
            or events[event_id].intent != 'cleanup'
            for event_id in self.cleanup_event_ids
        ):
            raise ValueError('清理事件必须是 checkpoint 绑定的成功 callback')
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
            or events[event_id].phase != 'cleanup'
            or events[event_id].intent != 'evidence'
            for event_id in self.cleanup_verification_event_ids
        ):
            raise ValueError('cleanup verification 必须来自成功页面观察 callback')
        if any(
            events[item.event_id].status != 'succeeded'
            or events[item.event_id].action != 'observe'
            or events[item.event_id].intent != 'evidence'
            or events[item.event_id].assertion_status != 'satisfied'
            or (
                item.phase == 'main'
                and events[item.event_id].phase != 'assertion'
            )
            or (
                item.phase == 'cleanup'
                and events[item.event_id].phase != 'cleanup'
            )
            for item in self.assertion_evidence
        ):
            raise ValueError('assertion evidence 必须来自显式绑定且语义满足的 observation callback')
        if any(item.bound_event_id and item.bound_event_id not in events for item in self.checkpoints):
            raise ValueError('checkpoint 只能绑定真实 callback event')
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
        first_cleanup_sequence = min((
            event.sequence for event in self.events
            if event.phase == 'cleanup' and event.checkpoint_id
        ), default=0)
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
        attempted = any(
            event.phase == 'cleanup' and event.intent == 'cleanup'
            and event.action in _INTERACTION_ACTIONS
            for event in self.events
        )
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
    selected = {
        *trace.replay_event_ids, *trace.cleanup_event_ids, *trace.assertion_event_ids,
    }
    evidence: list[LocatorEvidence] = []
    for event in trace.events:
        if event.event_id not in selected or event.status != 'succeeded':
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


def select_replay_events(
    events: list[ExplorationEvent], assertion_evidence: list[AssertionEvidence],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    replay = [
        event.event_id for event in events
        if event.status == 'succeeded' and event.phase == 'main'
        and event.intent == 'replay' and event.action in _REPLAY_ACTIONS
    ]
    cleanup = [
        event.event_id for event in events
        if event.status == 'succeeded' and event.phase == 'cleanup'
        and event.intent == 'cleanup' and event.action in _INTERACTION_ACTIONS
    ]
    assertions = [item.event_id for item in assertion_evidence]
    cleanup_verifications = [
        item.event_id for item in assertion_evidence if item.phase == 'cleanup'
    ]
    warnings: list[str] = []
    if not replay:
        warnings.append('连续探索未取得显式主场景回放动作；未标记的探索动作仅保留诊断。')
    if not assertions:
        warnings.append('未取得满足机器语义的 callback 断言证据；生成草稿不会伪装为已验证。')
    return replay, cleanup, cleanup_verifications, assertions, warnings


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
        self._checkpoints: list[CheckpointMarker] = []
        self._assertion_evidence: list[AssertionEvidence] = []
        self._states: list[PageState] = []
        self._active: dict[Any, dict[str, Any]] = {}
        self._active_checkpoint_calls: dict[Any, dict[str, Any]] = {}
        self._next_sequence = 1
        self._next_checkpoint_sequence = 1
        self._pending_checkpoint_id = ''
        self._runtime_values: dict[str, str] = {}
        self._runtime_sources: dict[str, str] = {}
        self._credential_refs: frozenset[str] = frozenset()
        self._assertion_requirements: dict[str, AssertionRequirement] = {}
        self._cleanup_expected = False
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
        self._credential_refs = frozenset(
            key for key, source in self._runtime_sources.items() if source == 'credential'
        )

    def _checkpoint(self, checkpoint_id: str) -> CheckpointMarker | None:
        return next(
            (item for item in self._checkpoints if item.checkpoint_id == checkpoint_id), None,
        )

    def _update_checkpoint(self, checkpoint_id: str, **updates: Any) -> None:
        for index, item in enumerate(self._checkpoints):
            if item.checkpoint_id == checkpoint_id:
                self._checkpoints[index] = item.model_copy(update=updates)
                return

    def _record_checkpoint(self, inputs: Mapping[str, Any]) -> None:
        phase = str(inputs.get('phase') or '')
        intent = str(inputs.get('intent') or '')
        assertion_id = str(inputs.get('assertion_id') or '')
        valid_pairs = {
            ('main', 'replay'), ('assertion', 'evidence'),
            ('cleanup', 'cleanup'), ('cleanup', 'evidence'),
        }
        valid = (phase, intent) in valid_pairs
        valid = valid and ((intent == 'evidence') == bool(assertion_id))
        valid = valid and (not assertion_id or assertion_id in self._assertion_requirements)
        valid = valid and (phase != 'cleanup' or self._cleanup_expected)
        if assertion_id in self._assertion_requirements:
            required_phase = self._assertion_requirements[assertion_id].phase
            valid = valid and (
                (phase == 'assertion' and required_phase == 'main')
                or (phase == 'cleanup' and required_phase == 'cleanup')
            )
        checkpoint_id = f'M{self._next_checkpoint_sequence:06d}'
        self._next_checkpoint_sequence += 1
        if self._pending_checkpoint_id:
            self._update_checkpoint(self._pending_checkpoint_id, binding_status='unbound')
            self._warnings.append(
                f'{self._pending_checkpoint_id} 未绑定 Playwright callback，已被后续 checkpoint 替代。'
            )
        marker = CheckpointMarker(
            checkpoint_id=checkpoint_id,
            phase=phase if phase in {'main', 'assertion', 'cleanup'} else 'main',
            intent=intent if intent in {'replay', 'evidence', 'cleanup'} else 'replay',
            assertion_id=assertion_id if re.fullmatch(r'A[1-9][0-9]*', assertion_id) else '',
            binding_status='pending' if valid else 'invalid',
        )
        self._checkpoints.append(marker)
        self._pending_checkpoint_id = checkpoint_id if valid else ''
        if not valid:
            self._warnings.append(f'{checkpoint_id} checkpoint 参数或 assertion_id 无效，未绑定事件。')

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
        if name == CHECKPOINT_TOOL_NAME:
            self._active_checkpoint_calls[run_id] = parsed_inputs
            return
        if not name.startswith('playwright_') and name != 'browser_console_logs':
            return
        sequence = self._next_sequence
        self._next_sequence += 1
        event_id = f'E{sequence:06d}'
        checkpoint = self._checkpoint(self._pending_checkpoint_id)
        if checkpoint is not None:
            self._pending_checkpoint_id = ''
            self._update_checkpoint(checkpoint.checkpoint_id, bound_event_id=event_id)
        self._active[run_id] = {
            'sequence': sequence,
            'event_id': event_id,
            'tool_name': name,
            'inputs': parsed_inputs,
            'before_state_id': self._last_state_id,
            'runtime_values': dict(self._runtime_values),
            'input_sources': dict(self._runtime_sources),
            'checkpoint_id': checkpoint.checkpoint_id if checkpoint else '',
            'phase': checkpoint.phase if checkpoint else 'exploration',
            'intent': checkpoint.intent if checkpoint else 'probe',
            'assertion_id': checkpoint.assertion_id if checkpoint else '',
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
        if run_id in self._active_checkpoint_calls:
            inputs = self._active_checkpoint_calls.pop(run_id)
            if status == 'succeeded':
                self._record_checkpoint(inputs)
            return
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
                value, runtime_values=runtime_values, credential_refs=self._credential_refs,
            )
            for key, value in inputs.items()
            if key in {'key', 'button', 'click_count', 'delay', 'force', 'modifiers'}
            and not _SECRET_KEY_RE.search(str(key))
        }
        event = ExplorationEvent(
            event_id=event_id, sequence=sequence, tool_name=tool_name, action=action,
            status=status, phase=active['phase'], intent=active['intent'],
            checkpoint_id=active['checkpoint_id'], assertion_id=active['assertion_id'],
            assertion_kind=(
                self._assertion_requirements[active['assertion_id']].kind
                if active['assertion_id'] in self._assertion_requirements else ''
            ),
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
            screenshot_path=screenshot_path,
        )
        binding_status = status
        if status == 'succeeded' and event.checkpoint_id:
            if event.phase == 'main':
                valid_binding = event.intent == 'replay' and event.action in _REPLAY_ACTIONS
            elif event.phase == 'cleanup' and event.intent == 'cleanup':
                valid_binding = (
                    event.intent == 'cleanup' and event.action in _INTERACTION_ACTIONS
                )
            else:
                requirement = self._assertion_requirements.get(event.assertion_id)
                requirement_phase_matches = bool(
                    requirement
                    and (
                        (event.phase == 'assertion' and requirement.phase == 'main')
                        or (event.phase == 'cleanup' and requirement.phase == 'cleanup')
                    )
                )
                valid_binding = bool(
                    event.intent == 'evidence'
                    and event.action == 'observe'
                    and requirement_phase_matches
                    and _evidence_locator(event)
                    and self._assertion_satisfied(requirement, raw_output, runtime_values)
                )
                event = event.model_copy(update={
                    'assertion_status': 'satisfied' if valid_binding else 'not_satisfied',
                })
                if valid_binding and requirement is not None:
                    self._assertion_evidence = [
                        item for item in self._assertion_evidence
                        if item.assertion_id != requirement.assertion_id
                    ]
                    self._assertion_evidence.append(AssertionEvidence(
                        assertion_id=requirement.assertion_id,
                        criterion_index=requirement.criterion_index,
                        phase=requirement.phase,
                        event_id=event.event_id,
                        kind=requirement.kind,
                        input_ref=requirement.input_ref,
                        literal=requirement.literal,
                    ))
                    self._assertion_evidence.sort(key=lambda item: item.criterion_index)
            if not valid_binding:
                binding_status = 'invalid'
                self._warnings.append(
                    f'{event.checkpoint_id} 后续成功 callback 与声明 phase/intent/断言语义不匹配。'
                )
        if event.checkpoint_id:
            self._update_checkpoint(
                event.checkpoint_id, bound_event_id=event.event_id,
                binding_status=binding_status,
            )
        self._events.append(event)
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
        if self._pending_checkpoint_id:
            self._update_checkpoint(self._pending_checkpoint_id, binding_status='unbound')
            self._warnings.append(
                f'{self._pending_checkpoint_id} 未绑定 Playwright callback，不能作为证据。'
            )
            self._pending_checkpoint_id = ''
        replay, cleanup_ids, cleanup_verifications, assertions, selected_warnings = select_replay_events(
            self._events, self._assertion_evidence,
        )
        attempted_cleanup = any(
            item.phase == 'cleanup' and item.intent == 'cleanup'
            and item.action in _INTERACTION_ACTIONS
            for item in self._events
        )
        events_by_id = {item.event_id: item for item in self._events}
        first_cleanup_sequence = min((
            item.sequence for item in self._events
            if item.phase == 'cleanup' and item.checkpoint_id
        ), default=0)
        first_cleanup_action_sequence = min(
            (events_by_id[event_id].sequence for event_id in cleanup_ids), default=0,
        )
        last_cleanup_sequence = max(
            (events_by_id[event_id].sequence for event_id in cleanup_ids), default=0,
        )
        confirmed_cleanup_verifications = [
            event_id for event_id in cleanup_verifications
            if cleanup_ids
            and events_by_id[event_id].sequence > first_cleanup_action_sequence
        ] if cleanup_ids else []
        confirmed_main_evidence = [
            item for item in self._assertion_evidence
            if item.phase == 'main'
            and (
                not first_cleanup_sequence
                or events_by_id[item.event_id].sequence < first_cleanup_sequence
            )
        ]
        effective_assertion_evidence = [
            *confirmed_main_evidence,
            *(
                item for item in self._assertion_evidence
                if item.event_id in confirmed_cleanup_verifications
            ),
        ]
        effective_assertion_evidence.sort(
            key=lambda item: events_by_id[item.event_id].sequence,
        )
        assertions = [item.event_id for item in effective_assertion_evidence]
        if any(
            item.phase == 'main' and item not in confirmed_main_evidence
            for item in self._assertion_evidence
        ):
            selected_warnings.append(
                'main assertion 发生在 cleanup 开始之后，已从主场景回放排除。'
            )
        if len(cleanup_verifications) != len(confirmed_cleanup_verifications):
            selected_warnings.append(
                'cleanup verification 前没有成功 cleanup 动作，不能作为清理证据。'
            )
        final_cleanup_verified = bool(
            cleanup_ids
            and any(
                events_by_id[event_id].sequence > last_cleanup_sequence
                for event_id in confirmed_cleanup_verifications
            )
        )
        cleanup_summary = {
            'status': (
                'completed' if self._cleanup_expected and cleanup_ids and final_cleanup_verified
                else 'attempted' if self._cleanup_expected and cleanup_ids
                else 'missing' if self._cleanup_expected
                else 'not_required'
            ),
            'attempted': attempted_cleanup,
            'evidence_event_ids': cleanup_ids,
            'verification_event_ids': confirmed_cleanup_verifications,
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
            checkpoints=self._checkpoints,
            assertion_evidence=effective_assertion_evidence,
            page_states=self._states,
            replay_event_ids=replay,
            cleanup_event_ids=cleanup_ids,
            cleanup_verification_event_ids=confirmed_cleanup_verifications,
            assertion_event_ids=assertions,
            cleanup=cleanup_summary,
            tool_stats=dict(tool_stats),
            warnings=list(dict.fromkeys([
                *(warnings or []), *self._warnings, *selected_warnings,
            ])),
            termination_reason=termination_reason,
            last_location=self._last_location,
        )
        return build_locator_evidence(trace)


def required_replay_evidence_gaps(
    trace: ExplorationTrace, plan: ScenarioPlan | None = None,
) -> list[dict[str, str]]:
    evidence = {item.event_id: item for item in trace.locator_evidence}
    events = {item.event_id: item for item in trace.events}
    gaps: list[dict[str, str]] = []
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
        covered = {item.assertion_id for item in trace.assertion_evidence}
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
        elif plan.cleanup_expected and not trace.cleanup_verification_event_ids:
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
