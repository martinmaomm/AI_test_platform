"""Callback-owned v3 event ledger for goal-scoped browser exploration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .generation_contracts import GenerationContractError, Goal, GoalPlan
from .generation_security import redact_text

TRACE_SCHEMA_VERSION = 3
_MAX_EVENTS = 120
_MAX_EXCERPT = 1200
_SECRET_KEY_RE = re.compile(r'(?i)(password|passwd|token|secret|cookie|authorization|api[_-]?key)')
_ABSOLUTE_URL_RE = re.compile(r'https?://[^\s\'"<>]+', re.I)
_OBSERVATION_TOOLS = frozenset({'playwright_get_visible_text', 'playwright_get_visible_html', 'playwright_snapshot'})
_INTERACTION_ACTIONS = ('click', 'fill', 'select', 'press', 'check', 'uncheck', 'hover')
_RUNTIME_VALUE_FIELDS = ('value', 'text', 'option', 'option_value', 'selected_value')
_ACTION_ARGUMENT_FIELDS = {
    'press': ('key',),
    'click': ('button', 'click_count', 'delay', 'force', 'modifiers'),
    'check': ('force',),
    'uncheck': ('force',),
    'hover': ('force',),
}


def _relative_path(value: Any) -> str:
    text = str(value or '').strip()
    if text.startswith(('http://', 'https://')):
        return urlsplit(text).path or '/'
    if text.startswith('/'):
        return text.split('?', 1)[0].split('#', 1)[0] or '/'
    return ''


def _safe_text(value: Any, *, limit: int, sensitive_values: tuple[str, ...] = (), runtime_values: Mapping[str, str] | None = None, credential_refs: frozenset[str] = frozenset(), redact_inline: bool = True) -> str:
    text = str(value or '')
    runtime_values = runtime_values or {}
    replacements: dict[str, str] = {}
    for index, (ref, runtime_value) in enumerate(sorted(runtime_values.items(), key=lambda item: len(str(item[1])), reverse=True)):
        if runtime_value:
            marker = f'__AITS_RUNTIME_{index}__'
            replacements[marker] = '<runtime_sensitive_data>' if ref in credential_refs else f'{{{{{ref}}}}}'
            text = text.replace(str(runtime_value), marker)
    for secret in sensitive_values:
        text = text.replace(secret, '<runtime_sensitive_data>')
    if redact_inline:
        text = redact_text(text)
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or '<url>', text)
    for marker, replacement in replacements.items():
        text = text.replace(marker, replacement)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _as_mapping(value: Any, fallback: str = '') -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(fallback) if fallback else {}
    except (TypeError, ValueError):
        parsed = {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _output_text(value: Any) -> str:
    if hasattr(value, 'content'):
        return _output_text(value.content)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        return ' '.join(_output_text(item) for item in value)
    return str(value or '')


def _tool_failed(output: Any) -> bool:
    if isinstance(output, Mapping) and (output.get('error') or output.get('isError') or output.get('is_error')):
        return True
    if getattr(output, 'isError', False) or getattr(output, 'is_error', False):
        return True
    return bool(re.search(r'operation failed|error executing tool|timeout .* exceeded|failed to|invalid .*selector|tool[ _-]?error', _output_text(output), re.I))


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


def _locator_input(inputs: Mapping[str, Any], action: str, sensitive_values: tuple[str, ...], runtime_values: Mapping[str, str], credential_refs: frozenset[str]) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    for key, value in inputs.items():
        name = str(key).lower()
        if _SECRET_KEY_RE.search(name):
            continue
        if name in {'selector', 'locator', 'role', 'name', 'text', 'label', 'placeholder', 'testid', 'test_id', 'exact'}:
            if name == 'exact' and isinstance(value, bool):
                locator[name] = value
                continue
            normalized = _safe_text(value, limit=301, sensitive_values=sensitive_values, runtime_values=runtime_values, credential_refs=credential_refs, redact_inline=False)
            if len(normalized) > 300:
                # Never compile a silently truncated selector. Missing evidence
                # sends the Goal to review instead of replaying a different DOM target.
                locator = {}
                break
            locator[name] = normalized
    if action == 'fill':
        locator['input_value'] = '<runtime_test_data>'
    return {key: value for key, value in locator.items() if value not in ('', None)}


def _runtime_input_values(inputs: Mapping[str, Any]) -> set[str]:
    """Read only protocol value fields; never infer a ref from locator text."""
    values: set[str] = set()
    for key in _RUNTIME_VALUE_FIELDS:
        value = inputs.get(key)
        if isinstance(value, (str, int, float, bool)):
            values.add(str(value))
        elif isinstance(value, Mapping):
            nested = value.get('value')
            if isinstance(nested, (str, int, float, bool)):
                values.add(str(nested))
    return values


def _matched_runtime_refs(inputs: Mapping[str, Any], runtime_values: Mapping[str, str], action: str) -> list[str]:
    if action not in {'fill', 'select'}:
        return []
    actual_values = _runtime_input_values(inputs)
    matches = [ref for ref, value in runtime_values.items() if str(value) in actual_values]
    return [matches[0]] if len(matches) == 1 else []


def _action_arguments(inputs: Mapping[str, Any], action: str, sensitive_values: tuple[str, ...], runtime_values: Mapping[str, str], credential_refs: frozenset[str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for key in _ACTION_ARGUMENT_FIELDS.get(action, ()):
        value = inputs.get(key)
        if isinstance(value, (str, int, float, bool)):
            arguments[key] = _safe_text(value, limit=120, sensitive_values=sensitive_values, runtime_values=runtime_values, credential_refs=credential_refs, redact_inline=False) if isinstance(value, str) else value
        elif key == 'modifiers' and isinstance(value, list) and all(isinstance(item, str) for item in value):
            arguments[key] = [_safe_text(item, limit=40, sensitive_values=sensitive_values, runtime_values=runtime_values, credential_refs=credential_refs, redact_inline=False) for item in value]
    return arguments


def _verification_result(tool_name: str, status: str, locator: Mapping[str, Any], verification: Mapping[str, str], runtime_values: Mapping[str, str], raw_output: str) -> 'AssertionResult | None':
    """Check the raw callback output in memory, persisting only a boolean result."""
    mode = str(verification.get('mode') or '')
    input_ref = str(verification.get('input_ref') or '')
    if status != 'succeeded' or tool_name != 'playwright_get_visible_html' or not locator.get('selector') or mode not in {'visible', 'contains_ref', 'not_contains_ref'}:
        return None
    if mode == 'visible':
        return AssertionResult(mode=mode, matched=True)
    value = runtime_values.get(input_ref)
    if not value:
        return AssertionResult(mode=mode, input_ref=input_ref, matched=False)
    present = str(value) in raw_output
    return AssertionResult(mode=mode, input_ref=input_ref, matched=present if mode == 'contains_ref' else not present)


class PageState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_id: str = Field(pattern=r'^P[0-9a-f]{16}$')
    relative_path: str = Field(pattern=r'^/')
    fingerprint: str = Field(min_length=16, max_length=16)
    excerpt: str = Field(default='', max_length=1200)


class ExplorationEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(pattern=r'^E\d{6}$')
    sequence: int = Field(ge=1)
    goal_id: str = Field(pattern=r'^G[1-9][0-9]*$')
    tool_name: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=40)
    status: str = Field(pattern=r'^(?:succeeded|failed|blocked)$')
    relative_path: str = Field(default='/', pattern=r'^/')
    locator_input: dict[str, Any] = Field(default_factory=dict)
    input_refs: list[str] = Field(default_factory=list, max_length=20)
    input_source: str = Field(default='', pattern=r'^(?:|generated|runtime|credential)$')
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    assertion_result: 'AssertionResult | None' = None
    before_state_id: str = Field(default='', max_length=20)
    after_state_id: str = Field(default='', max_length=20)
    result_excerpt: str = Field(default='', max_length=1200)
    screenshot_path: str = Field(default='', max_length=500)


class GoalRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal_id: str = Field(pattern=r'^G[1-9][0-9]*$')
    status: str = Field(pattern=r'^(?:completed|blocked|uncertain)$')
    selected_event_ids: list[str] = Field(default_factory=list, max_length=120)
    assertion_event_ids: list[str] = Field(default_factory=list, max_length=120)
    reason: str = Field(default='', max_length=500)


class LocatorEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str = Field(pattern=r'^L\d{6}$')
    event_id: str = Field(pattern=r'^E\d{6}$')
    goal_id: str = Field(pattern=r'^G[1-9][0-9]*$')
    action: str = Field(min_length=1, max_length=40)
    relative_path: str = Field(pattern=r'^/')
    strategy: str = Field(pattern=r'^(?:path|testid|role|label|placeholder|css|text)$')
    value: str = Field(min_length=1, max_length=300)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    validation: str = Field(pattern=r'^(?:succeeded|acceptable|fragile|rejected)$')
    validation_reasons: list[str] = Field(default_factory=list, max_length=10)
    state_fingerprint: str = Field(default='', max_length=16)
    assertion_mode: str = Field(default='', pattern=r'^(?:|visible|contains_ref|not_contains_ref)$')
    assertion_input_ref: str = Field(default='', max_length=128)


class AssertionResult(BaseModel):
    """Persist only the verification outcome, never the inspected runtime value."""
    model_config = ConfigDict(extra='forbid')
    mode: str = Field(pattern=r'^(?:visible|contains_ref|not_contains_ref)$')
    input_ref: str = Field(default='', max_length=128)
    matched: bool


class ExplorationTrace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = TRACE_SCHEMA_VERSION
    start_path: str = Field(default='/', pattern=r'^/')
    goal_runs: list[GoalRun] = Field(default_factory=list, max_length=30)
    events: list[ExplorationEvent] = Field(default_factory=list, max_length=_MAX_EVENTS)
    page_states: list[PageState] = Field(default_factory=list, max_length=80)
    locator_evidence: list[LocatorEvidence] = Field(default_factory=list, max_length=_MAX_EVENTS)
    cleanup: dict[str, Any] = Field(default_factory=lambda: {'status': 'not_required', 'attempted': False, 'residuals': [], 'reason': ''})
    tool_stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    termination_reason: str = Field(default='', max_length=120)
    last_location: str = Field(default='/', pattern=r'^/')

    @model_validator(mode='after')
    def _only_selected_successes(self):
        events = {event.event_id: event for event in self.events}
        for run in self.goal_runs:
            for event_id in [*run.selected_event_ids, *run.assertion_event_ids]:
                event = events.get(event_id)
                if event is None or event.goal_id != run.goal_id:
                    raise ValueError('Goal 评估只能引用当前 Goal 的 callback event_id')
                if event.status != 'succeeded':
                    raise ValueError('失败或阻断事件不能进入 Goal 选择')
        return self


class GoalEvaluation(BaseModel):
    """Strict, retryable evaluator response. It cannot contain selectors."""
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern=r'^(?:completed|blocked|uncertain)$')
    reason: str = Field(default='', max_length=500)
    selected_event_ids: list[str] = Field(default_factory=list, max_length=120)
    assertion_event_ids: list[str] = Field(default_factory=list, max_length=120)


def evaluate_goal_events(goal: Goal, events: list[ExplorationEvent], response: Mapping[str, Any] | None = None) -> GoalRun:
    """Validate a selector-free evaluator response or use a conservative fallback.

    The fallback never infers intent from text: it only accepts callback events
    already bound to this goal and requires evidence appropriate to Goal kind.
    """
    candidates = [event for event in events if event.goal_id == goal.id and event.status == 'succeeded']
    allowed = {event.event_id for event in candidates}
    if response is not None:
        try:
            evaluation = GoalEvaluation.model_validate(response)
        except Exception as exc:
            raise GenerationContractError('goal_evaluation_invalid') from exc
        selected = list(dict.fromkeys(evaluation.selected_event_ids))
        assertions = list(dict.fromkeys(evaluation.assertion_event_ids))
        if not set([*selected, *assertions]) <= allowed:
            raise GenerationContractError('goal_evaluation_event_not_callback_owned')
        if evaluation.status == 'completed':
            reason = _completion_shape_error(goal, candidates, selected, assertions)
            if reason:
                raise GenerationContractError(reason)
        return GoalRun(goal_id=goal.id, status=evaluation.status, selected_event_ids=selected, assertion_event_ids=assertions, reason=evaluation.reason)
    return _conservative_fallback(goal, candidates)


def _assertion_capable(event: ExplorationEvent, goal: Goal) -> bool:
    contract = goal.verification
    result = event.assertion_result
    return bool(
        contract
        and result
        and result.matched
        and result.mode == contract.mode
        and result.input_ref == contract.input_ref
        and event.tool_name == 'playwright_get_visible_html'
        and event.locator_input.get('selector')
    )


def _later_observation(events: list[ExplorationEvent], after: ExplorationEvent) -> ExplorationEvent | None:
    return next((event for event in reversed(events) if event.sequence > after.sequence and event.action == 'observe'), None)


def _completion_shape_error(goal: Goal, candidates: list[ExplorationEvent], selected_ids: list[str], assertion_ids: list[str]) -> str:
    selected = [event for event in candidates if event.event_id in selected_ids]
    assertions = [event for event in candidates if event.event_id in assertion_ids]
    if goal.kind == 'setup':
        return '' if any(event.action in {'navigate', 'observe'} for event in selected) else 'goal_setup_requires_navigation_or_observation'
    if goal.verification:
        if not assertions:
            return 'goal_verification_requires_assertion_evidence'
        if not all(_assertion_capable(event, goal) for event in assertions):
            return 'goal_verification_assertion_not_compilable'
    if goal.kind == 'verify':
        return ''
    interactions = [event for event in selected if event.action in _INTERACTION_ACTIONS]
    if interactions and any(_later_observation(candidates, event) for event in interactions):
        return ''
    if goal.kind == 'cleanup' and goal.side_effect == 'none' and any(event.action == 'observe' for event in selected):
        return ''
    return f'goal_{goal.kind}_requires_interaction_and_observation'


def _conservative_fallback(goal: Goal, candidates: list[ExplorationEvent]) -> GoalRun:
    navigations = [event for event in candidates if event.action == 'navigate']
    observations = [event for event in candidates if event.action == 'observe']
    if goal.kind == 'setup':
        interactions = [event for event in candidates if event.action in _INTERACTION_ACTIONS]
        if interactions:
            final_observation = _later_observation(candidates, interactions[-1])
            if final_observation:
                selected = [
                    event for event in candidates
                    if event.sequence <= final_observation.sequence
                    and event.action in {'navigate', *_INTERACTION_ACTIONS}
                ]
                selected.append(final_observation)
                return GoalRun(
                    goal_id=goal.id, status='completed',
                    selected_event_ids=[event.event_id for event in selected],
                    reason='保留当前 Goal 中到最终页面观察为止的全部成功 callback 动作。',
                )
        elif observations:
            selected = [*navigations, observations[-1]]
            return GoalRun(
                goal_id=goal.id, status='completed',
                selected_event_ids=list(dict.fromkeys(event.event_id for event in selected)),
                reason='使用当前 Goal 的导航和最终页面观察 callback 完成 setup。',
            )
    elif goal.kind == 'verify':
        assertions = [event for event in candidates if _assertion_capable(event, goal)]
        if assertions:
            return GoalRun(goal_id=goal.id, status='completed', assertion_event_ids=[assertions[-1].event_id], reason='使用 locator-backed callback observation 生成 verify 断言。')
    else:
        interactions = [event for event in candidates if event.action in _INTERACTION_ACTIONS]
        if interactions:
            final_interaction = interactions[-1]
            observation = _later_observation(candidates, final_interaction)
            assertions = [
                event for event in candidates
                if event.sequence > final_interaction.sequence and _assertion_capable(event, goal)
            ] if goal.verification else []
            if observation and (not goal.verification or assertions):
                terminal_sequence = max(
                    observation.sequence,
                    assertions[-1].sequence if assertions else observation.sequence,
                )
                selected = [
                    event for event in candidates
                    if event.sequence <= terminal_sequence
                    and event.action in {'navigate', *_INTERACTION_ACTIONS}
                ]
                selected.append(observation)
                return GoalRun(
                    goal_id=goal.id, status='completed',
                    selected_event_ids=list(dict.fromkeys(event.event_id for event in selected)),
                    assertion_event_ids=[assertions[-1].event_id] if assertions else [],
                    reason='保留当前 Goal 中到最终验证为止的全部成功 callback 动作。',
                )
        if goal.kind == 'cleanup' and goal.side_effect == 'none' and observations:
            assertions = [event for event in candidates if _assertion_capable(event, goal)] if goal.verification else []
            if not goal.verification or assertions:
                return GoalRun(
                    goal_id=goal.id, status='completed',
                    selected_event_ids=[observations[-1].event_id],
                    assertion_event_ids=[assertions[-1].event_id] if assertions else [],
                    reason='目标数据已不存在，使用 callback 页面验证完成 cleanup。',
                )
    return GoalRun(goal_id=goal.id, status='uncertain', reason='当前 Goal 缺少与其类型相称的成功 callback 证据，保守等待评估重试。')


class ExplorationTraceRecorder:
    """Binds events to ``active_goal_id`` at callback time, never afterwards."""
    def __init__(self, start_path: str = '/', *, sensitive_values: tuple[str, ...] = (), trace_file: str | Path | None = None, runtime_namespace: str = ''):
        self.start_path = _relative_path(start_path) or '/'
        self._sensitive_values = tuple(str(item) for item in sensitive_values if item)
        self._trace_file = Path(trace_file).resolve() if trace_file else None
        self._runtime_namespace = runtime_namespace
        self._events: list[ExplorationEvent] = []
        self._states: list[PageState] = []
        self._goal_runs: list[GoalRun] = []
        self._active: dict[Any, tuple[int, str, dict[str, Any], str, str, dict[str, str], dict[str, str], dict[str, str]]] = {}
        self._next_sequence = 1
        self._active_goal_id = ''
        self._active_goal_runtime_values: dict[str, str] = {}
        self._active_goal_input_sources: dict[str, str] = {}
        self._active_goal_verification: dict[str, str] = {}
        self._runtime_values: dict[str, str] = {}
        self._runtime_sources: dict[str, str] = {}
        self._credential_runtime_refs: frozenset[str] = frozenset()
        self._last_location = self.start_path
        self._last_state_id = ''
        if self._trace_file:
            self._trace_file.parent.mkdir(parents=True, exist_ok=True)
            self._trace_file.unlink(missing_ok=True)

    @property
    def events(self) -> list[ExplorationEvent]:
        return list(self._events)

    def set_active_goal(self, goal_id: str, runtime_values: Mapping[str, str] | None = None, input_sources: Mapping[str, str] | None = None, verification: Mapping[str, str] | None = None) -> None:
        if not re.fullmatch(r'G[1-9][0-9]*', goal_id):
            raise ValueError('active_goal_id 无效')
        self._active_goal_id = goal_id
        self._active_goal_runtime_values = {
            str(ref): str(value)
            for ref, value in dict(runtime_values or {}).items()
            if str(ref) and str(value)
        }
        self._active_goal_input_sources = {
            str(ref): str(source)
            for ref, source in dict(input_sources or {}).items()
            if str(ref) and str(source) in {'generated', 'runtime', 'credential'}
        }
        raw_verification = dict(verification or {})
        self._active_goal_verification = {
            'mode': str(raw_verification.get('mode') or ''),
            'input_ref': str(raw_verification.get('input_ref') or ''),
        } if raw_verification.get('mode') in {'visible', 'contains_ref', 'not_contains_ref'} else {}
        self._runtime_values.update(self._active_goal_runtime_values)
        self._runtime_sources.update(self._active_goal_input_sources)
        self._credential_runtime_refs = frozenset(ref for ref, source in self._runtime_sources.items() if source == 'credential')
        self._sensitive_values = tuple(dict.fromkeys([*self._sensitive_values, *self._active_goal_runtime_values.values()]))

    def record_goal_run(self, goal_run: GoalRun) -> None:
        self._goal_runs = [item for item in self._goal_runs if item.goal_id != goal_run.goal_id]
        self._goal_runs.append(goal_run)

    def on_tool_start(self, serialized: Mapping[str, Any] | None, input_str: str, *, run_id: Any = None, inputs: Any = None) -> None:
        name = str((serialized or {}).get('name') or 'browser_tool').lower()
        if not name.startswith('playwright_') and name != 'browser_console_logs':
            return
        if not self._active_goal_id:
            raise RuntimeError('浏览器 callback 在 active_goal_id 设置前到达')
        sequence = self._next_sequence
        self._next_sequence += 1
        self._active[run_id] = (sequence, name, _as_mapping(inputs, input_str), self._active_goal_id, self._last_state_id, dict(self._active_goal_runtime_values), dict(self._active_goal_input_sources), dict(self._active_goal_verification))

    def _complete(self, output: Any, *, run_id: Any, status: str) -> None:
        active = self._active.pop(run_id, None)
        if active is None or len(self._events) >= _MAX_EVENTS:
            return
        sequence, tool_name, inputs, goal_id, before_state_id, runtime_values, input_sources, verification = active
        action = _action(tool_name)
        path = next((_relative_path(inputs.get(key)) for key in ('url', 'path', 'target', 'href') if _relative_path(inputs.get(key))), '') or self._last_location
        raw_output = _output_text(output)
        excerpt = _safe_text(raw_output, limit=_MAX_EXCERPT, sensitive_values=self._sensitive_values, runtime_values=self._runtime_values, credential_refs=self._credential_runtime_refs)
        state_id = ''
        if action == 'observe' and status == 'succeeded' and excerpt:
            fingerprint = hashlib.sha256(f'{path}|{excerpt}'.encode()).hexdigest()[:16]
            state_id = f'P{fingerprint}'
            if not any(item.state_id == state_id for item in self._states):
                self._states.append(PageState(state_id=state_id, relative_path=path or '/', fingerprint=fingerprint, excerpt=excerpt))
            self._last_state_id = state_id
        screenshot_path = ''
        if action == 'screenshot':
            match = re.search(r'(?:[\w.-]+/)*[\w.-]+\.png\b', _output_text(output))
            screenshot_path = match.group(0) if match else ''
        input_refs = _matched_runtime_refs(inputs, runtime_values, action)
        locator = _locator_input(inputs, action, self._sensitive_values, self._runtime_values, self._credential_runtime_refs)
        assertion_result = _verification_result(tool_name, status, locator, verification, runtime_values, raw_output)
        event = ExplorationEvent(
            event_id=f'E{sequence:06d}', sequence=sequence, goal_id=goal_id, tool_name=tool_name,
            action=action, status=status, relative_path=path or '/',
            locator_input=locator,
            input_refs=input_refs, input_source=input_sources.get(input_refs[0], '') if input_refs else '',
            action_arguments=_action_arguments(inputs, action, self._sensitive_values, self._runtime_values, self._credential_runtime_refs), before_state_id=before_state_id,
            after_state_id=state_id or self._last_state_id, result_excerpt=excerpt, screenshot_path=screenshot_path, assertion_result=assertion_result,
        )
        self._events.append(event)
        self._last_location = path or self._last_location
        if self._trace_file:
            with self._trace_file.open('a', encoding='utf-8') as stream:
                stream.write(event.model_dump_json() + '\n')

    def on_tool_end(self, output: Any, *, run_id: Any = None) -> None:
        self._complete(output, run_id=run_id, status='failed' if _tool_failed(output) else 'succeeded')

    def on_tool_error(self, error: BaseException, *, run_id: Any = None) -> None:
        self._complete(error, run_id=run_id, status='failed')

    def mark_blocked(self, serialized, input_str, *, run_id=None, inputs=None, error=None) -> None:
        if run_id not in self._active:
            self.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        self._complete(error or 'blocked by safety policy', run_id=run_id, status='blocked')

    def build(self, *, tool_stats: Mapping[str, Any], termination_reason: str = '', cleanup: Mapping[str, Any] | None = None, warnings: list[str] | None = None) -> ExplorationTrace:
        trace = ExplorationTrace(start_path=self.start_path, goal_runs=self._goal_runs, events=self._events, page_states=self._states, cleanup=dict(cleanup or {}), tool_stats=dict(tool_stats), warnings=list(dict.fromkeys(warnings or [])), termination_reason=termination_reason, last_location=self._last_location)
        return build_locator_evidence(trace)


def _evidence_locator(event: ExplorationEvent) -> tuple[str, str, dict[str, Any], str, list[str]] | None:
    if event.action == 'navigate':
        return 'path', event.relative_path, {}, 'succeeded', ['relative navigation callback succeeded']
    locator = event.locator_input
    if 'testid' in locator or 'test_id' in locator:
        return 'testid', str(locator.get('testid') or locator.get('test_id')), {}, 'succeeded', ['callback succeeded; count not independently queried']
    if 'role' in locator:
        kwargs = {'name': locator['name']} if locator.get('name') else {}
        return 'role', str(locator['role']), kwargs, 'acceptable', ['callback succeeded; MCP cannot prove locator count == 1']
    for name, strategy in (('label', 'label'), ('placeholder', 'placeholder'), ('selector', 'css'), ('locator', 'css'), ('text', 'text'), ('name', 'text')):
        if locator.get(name):
            value = str(locator[name])
            quality = 'fragile' if re.search(r'(?:nth\(|:nth-|#[\w-]*\d{4,}|:visible$)', value, re.I) else 'acceptable'
            reasons = ['callback succeeded; MCP cannot prove locator count == 1']
            if quality == 'fragile':
                reasons.append('dynamic or positional locator')
            return strategy, value, {'exact': bool(locator.get('exact'))} if 'exact' in locator else {}, quality, reasons
    return None


def build_locator_evidence(trace: ExplorationTrace) -> ExplorationTrace:
    selected = {event_id for run in trace.goal_runs for event_id in [*run.selected_event_ids, *run.assertion_event_ids]}
    evidence: list[LocatorEvidence] = []
    for event in trace.events:
        if event.event_id not in selected or event.status != 'succeeded':
            continue
        item = _evidence_locator(event)
        if item is None:
            continue
        strategy, value, kwargs, validation, reasons = item
        fingerprint = next((state.fingerprint for state in trace.page_states if state.state_id == (event.after_state_id or event.before_state_id)), '')
        assertion = event.assertion_result
        evidence.append(LocatorEvidence(evidence_id=f'L{event.sequence:06d}', event_id=event.event_id, goal_id=event.goal_id, action=event.action, relative_path=event.relative_path, strategy=strategy, value=value, kwargs=kwargs, validation=validation, validation_reasons=reasons, state_fingerprint=fingerprint, assertion_mode=assertion.mode if assertion and assertion.matched else '', assertion_input_ref=assertion.input_ref if assertion and assertion.matched else ''))
    return trace.model_copy(update={'locator_evidence': evidence})


def evaluate_trace_goal_runs(plan: GoalPlan, trace: ExplorationTrace) -> ExplorationTrace:
    runs = [evaluate_goal_events(goal, trace.events) for goal in plan.goals]
    return build_locator_evidence(trace.model_copy(update={'goal_runs': runs}))


def required_goal_evidence_gaps(plan: GoalPlan, trace: ExplorationTrace) -> list[dict[str, str]]:
    runs = {run.goal_id: run for run in trace.goal_runs}
    evidence_by_event = {item.event_id: item for item in trace.locator_evidence}
    gaps = []
    for goal in plan.goals:
        run = runs.get(goal.id)
        if run is None or run.status != 'completed':
            gaps.append({'goal_id': goal.id, 'reason': run.reason if run else '缺少 Goal 评估结果'})
            continue
        selected_events = [event for event in trace.events if event.event_id in run.selected_event_ids]
        required_events = [event.event_id for event in selected_events if event.action not in {'observe', 'screenshot'}]
        if any(event_id not in evidence_by_event for event_id in required_events):
            gaps.append({'goal_id': goal.id, 'reason': '选中动作缺少 LocatorEvidence'})
        if any(evidence_by_event[event_id].validation in {'fragile', 'rejected'} for event_id in required_events if event_id in evidence_by_event):
            gaps.append({'goal_id': goal.id, 'reason': '必要业务动作定位器不够稳定'})
        if any(event.action in {'fill', 'select'} and len(event.input_refs) != 1 for event in selected_events):
            gaps.append({'goal_id': goal.id, 'reason': '输入动作未能精确映射到单一 runtime ref'})
        assertion_evidence = [evidence_by_event.get(event_id) for event_id in run.assertion_event_ids]
        if goal.verification and not run.assertion_event_ids:
            gaps.append({'goal_id': goal.id, 'reason': 'Goal 缺少 callback-owned verification 断言证据'})
        if goal.verification and any(item is None or item.strategy != 'css' or item.validation in {'fragile', 'rejected'} or item.assertion_mode != goal.verification.mode or item.assertion_input_ref != goal.verification.input_ref for item in assertion_evidence):
            gaps.append({'goal_id': goal.id, 'reason': '断言事件不是可定位的 callback HTML observation，或未满足 verification contract'})
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
