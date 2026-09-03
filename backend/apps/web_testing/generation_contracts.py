"""Strict v4 contracts for one continuous WebUI exploration."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .execution_variables import ExecutionVariableError, validate_variable_name

SCENARIO_PLAN_SCHEMA_VERSION = 4
_SAFE_DIAGNOSTIC_PATH_SEGMENTS = frozenset({
    'schema_version', 'title', 'objective', 'instructions', 'success_criteria',
    'assertion_requirements', 'assertion_id', 'criterion_index', 'phase', 'kind',
    'input_ref', 'literal', 'input_refs', 'name', 'source', 'value_kind', 'credential_slot',
    'preconditions', 'forbidden_actions', 'credentials_required',
    'allow_test_data_writes', 'cleanup_expected', 'discovery_notes', 'risk_level',
})
_SAFE_CUSTOM_ERROR_TYPES = (
    ('不能包含完整 URL', 'absolute_url_forbidden'),
    ('仅支持 schema_version=4', 'schema_version_mismatch'),
    ('ref 断言必须且只能声明 input_ref', 'assertion_ref_shape'),
    ('literal 断言必须且只能声明 literal', 'assertion_literal_shape'),
    ('visible 断言不能声明 ref 或 literal', 'assertion_visible_shape'),
    ('input ref 不可重复', 'duplicate_input_ref'),
    ('assertion_id 不可重复', 'duplicate_assertion_id'),
    ('每条 success criterion 只能声明一个机器断言', 'duplicate_criterion_assertion'),
    ('每条 success criterion 都必须有机器可编译的 assertion requirement', 'criterion_assertion_missing'),
    ('assertion requirement 引用了未声明的 input ref', 'unknown_assertion_input_ref'),
    ('credentials_required=true 时必须声明 username 和 password 凭据变量', 'credential_refs_incomplete'),
    ('声明 credential input ref 时 credentials_required 必须为 true', 'credential_flag_missing'),
    ('cleanup_expected 仅适用于允许测试数据写入的场景', 'cleanup_without_write_scope'),
    ('场景必须至少有一条 main assertion requirement', 'main_assertion_missing'),
    ('cleanup_expected 必须声明 cleanup assertion requirement', 'cleanup_assertion_missing'),
    ('仅 cleanup_expected 场景可以声明 cleanup assertion requirement', 'unexpected_cleanup_assertion'),
    ('cleanup assertion 不能以仍包含目标值证明清理完成', 'cleanup_assertion_positive'),
    ('credential input ref 必须声明 credential_slot', 'credential_slot_missing'),
    ('仅 credential input ref 可以声明 credential_slot', 'unexpected_credential_slot'),
    ('credential ref 必须命名为', 'credential_ref_name_invalid'),
    ('credential ref 的 value_kind 必须为', 'credential_value_kind_invalid'),
    ('字段必须是数组', 'list_field_required'),
    ('input_refs 必须是数组', 'input_refs_list_required'),
)


class GenerationContractError(ValueError):
    """A model or persisted artifact is not a safe v4 contract."""
    def __init__(self, message: str = 'contract_invalid', *, diagnostics: tuple[dict[str, str], ...] = ()):
        super().__init__(message)
        self.diagnostics = diagnostics


class ScenarioInputInsufficientError(GenerationContractError):
    """Only blank or genuinely target-free descriptions use this error."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def _validate_safe_value(value: Any, field_name: str, *, reject_absolute_url: bool = False) -> None:
    if isinstance(value, str):
        if reject_absolute_url and re.search(r'(?i)https?://', value):
            raise ValueError(f'{field_name} 不能包含完整 URL')
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_safe_value(str(key), field_name, reject_absolute_url=reject_absolute_url)
            _validate_safe_value(item, field_name, reject_absolute_url=reject_absolute_url)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_value(item, field_name, reject_absolute_url=reject_absolute_url)


def _safe_diagnostic_path(location: Any) -> str:
    parts = []
    for part in location:
        if isinstance(part, str):
            parts.append(part if part in _SAFE_DIAGNOSTIC_PATH_SEGMENTS else '<field>')
        else:
            parts.append('[item]')
    return '.'.join(parts)[:160] or '<contract>'


def _safe_diagnostics(error: ValidationError) -> tuple[dict[str, str], ...]:
    return tuple({
        'path': _safe_diagnostic_path(item.get('loc', ())),
        'type': _safe_diagnostic_type(item),
        'stage': 'contract_validation',
    } for item in error.errors(include_input=False, include_context=True, include_url=False)[:3])


def _safe_diagnostic_type(item: dict[str, Any]) -> str:
    """Map known validators to actionable codes without exposing their input."""
    message = str((item.get('ctx') or {}).get('error') or '')
    for fragment, error_type in _SAFE_CUSTOM_ERROR_TYPES:
        if fragment in message:
            return error_type
    return str(item.get('type') or 'validation_error')[:80]


class InputSpec(_StrictContract):
    """One explicit runtime input; its value never belongs in persisted JSON."""
    name: str = Field(min_length=1, max_length=128)
    source: str = Field(pattern=r'^(?:generated|runtime|credential)$')
    value_kind: str = Field(default='text', pattern=r'^(?:text|email|password|integer)$')
    credential_slot: str = Field(default='', pattern=r'^(?:|username|password)$')

    @model_validator(mode='before')
    @classmethod
    def _default_credential_value_kind(cls, value):
        if not isinstance(value, dict):
            return value
        if (
            value.get('source') == 'credential'
            and value.get('credential_slot') == 'password'
            and 'value_kind' not in value
        ):
            return {**value, 'value_kind': 'password'}
        return value

    @field_validator('name', mode='before')
    @classmethod
    def _execution_variable_name(cls, value):
        try:
            return validate_variable_name(value)
        except ExecutionVariableError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode='after')
    def _credential_shape(self):
        if self.source == 'credential' and not self.credential_slot:
            raise ValueError('credential input ref 必须声明 credential_slot')
        if self.source != 'credential' and self.credential_slot:
            raise ValueError('仅 credential input ref 可以声明 credential_slot')
        if self.source == 'credential':
            expected = {'username': 'UI_TEST_USERNAME', 'password': 'UI_TEST_PASSWORD'}[self.credential_slot]
            if self.name != expected:
                raise ValueError(f'{self.credential_slot} credential ref 必须命名为 {expected}')
            expected_value_kind = 'password' if self.credential_slot == 'password' else 'text'
            if self.value_kind != expected_value_kind:
                raise ValueError(f'{self.credential_slot} credential ref 的 value_kind 必须为 {expected_value_kind}')
        return self


class AssertionRequirement(_StrictContract):
    """Machine-compilable meaning for one user success criterion."""

    assertion_id: str = Field(pattern=r'^A[1-9][0-9]*$')
    criterion_index: int = Field(ge=0, le=19)
    phase: str = Field(default='main', pattern=r'^(?:main|cleanup)$')
    kind: str = Field(
        pattern=r'^(?:visible|contains_ref|not_contains_ref|contains_literal|not_contains_literal)$'
    )
    input_ref: str = Field(default='', max_length=128)
    literal: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def _semantic_shape(self):
        uses_ref = self.kind in {'contains_ref', 'not_contains_ref'}
        uses_literal = self.kind in {'contains_literal', 'not_contains_literal'}
        if uses_ref != bool(self.input_ref):
            raise ValueError('ref 断言必须且只能声明 input_ref')
        if uses_literal != bool(self.literal):
            raise ValueError('literal 断言必须且只能声明 literal')
        if self.kind == 'visible' and (self.input_ref or self.literal):
            raise ValueError('visible 断言不能声明 ref 或 literal')
        return self


class ScenarioPlan(_StrictContract):
    """A complete plan given to one agent; instructions are never run boundaries."""
    schema_version: int = Field(default=SCENARIO_PLAN_SCHEMA_VERSION, frozen=True)
    title: str = Field(min_length=2, max_length=200)
    objective: str = Field(min_length=2, max_length=1000)
    instructions: list[str] = Field(min_length=1, max_length=40)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    assertion_requirements: list[AssertionRequirement] = Field(min_length=1, max_length=20)
    input_refs: list[InputSpec] = Field(default_factory=list, max_length=40)
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=30)
    credentials_required: bool = False
    allow_test_data_writes: bool = False
    cleanup_expected: bool = False
    discovery_notes: list[str] = Field(default_factory=list, max_length=30)
    risk_level: str = Field(default='low', pattern=r'^(?:low|medium|high)$')

    @field_validator('schema_version')
    @classmethod
    def _only_v4(cls, value):
        if value != SCENARIO_PLAN_SCHEMA_VERSION:
            raise ValueError('仅支持 schema_version=4')
        return value

    @field_validator('instructions', 'success_criteria', 'preconditions', 'forbidden_actions', 'discovery_notes', mode='before')
    @classmethod
    def _text_list(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('字段必须是数组')
        return [str(item) for item in value]

    @field_validator('input_refs', 'assertion_requirements', mode='before')
    @classmethod
    def _input_list(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('input_refs 必须是数组')
        return value

    @model_validator(mode='after')
    def _validate_plan(self):
        names = [item.name for item in self.input_refs]
        if len(names) != len(set(names)):
            raise ValueError('input ref 不可重复')
        assertion_ids = [item.assertion_id for item in self.assertion_requirements]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError('assertion_id 不可重复')
        criterion_indexes = [item.criterion_index for item in self.assertion_requirements]
        if len(criterion_indexes) != len(set(criterion_indexes)):
            raise ValueError('每条 success criterion 只能声明一个机器断言')
        if set(criterion_indexes) != set(range(len(self.success_criteria))):
            raise ValueError('每条 success criterion 都必须有机器可编译的 assertion requirement')
        unknown_assertion_refs = {
            item.input_ref for item in self.assertion_requirements
            if item.input_ref and item.input_ref not in set(names)
        }
        if unknown_assertion_refs:
            raise ValueError('assertion requirement 引用了未声明的 input ref')
        credential_slots = {item.credential_slot for item in self.input_refs if item.source == 'credential'}
        if self.credentials_required and credential_slots != {'username', 'password'}:
            raise ValueError('credentials_required=true 时必须声明 username 和 password 凭据变量')
        if not self.credentials_required and credential_slots:
            raise ValueError('声明 credential input ref 时 credentials_required 必须为 true')
        if self.cleanup_expected and not self.allow_test_data_writes:
            raise ValueError('cleanup_expected 仅适用于允许测试数据写入的场景')
        cleanup_assertions = [
            item for item in self.assertion_requirements if item.phase == 'cleanup'
        ]
        if not any(item.phase == 'main' for item in self.assertion_requirements):
            raise ValueError('场景必须至少有一条 main assertion requirement')
        if self.cleanup_expected and not cleanup_assertions:
            raise ValueError('cleanup_expected 必须声明 cleanup assertion requirement')
        if not self.cleanup_expected and cleanup_assertions:
            raise ValueError('仅 cleanup_expected 场景可以声明 cleanup assertion requirement')
        if any(
            item.kind in {'contains_ref', 'contains_literal'}
            for item in cleanup_assertions
        ):
            raise ValueError('cleanup assertion 不能以仍包含目标值证明清理完成')
        _validate_safe_value(self.model_dump(mode='json'), 'ScenarioPlan', reject_absolute_url=True)
        return self

    def input_sources(self) -> dict[str, str]:
        return {item.name: item.source for item in self.input_refs}


def _extract_single_json_object(raw_value: Any) -> str:
    """Return one embedded JSON object when the surrounding text is unambiguous.

    Model responses commonly add a Markdown fence or a short explanation.  This
    is deliberately a local, deterministic cleanup: it never guesses between
    multiple JSON objects and therefore cannot change ScenarioPlan semantics.
    """
    if isinstance(raw_value, BaseModel):
        return json.dumps(raw_value.model_dump(mode='json'), ensure_ascii=False)
    if isinstance(raw_value, (dict, list)):
        return json.dumps(raw_value, ensure_ascii=False)

    candidate = str(raw_value or '').strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(\{.*\})\s*```', candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        json.loads(candidate)
        return candidate
    except (TypeError, ValueError):
        pass

    decoder = json.JSONDecoder()
    objects: list[Any] = []
    position = 0
    while True:
        start = candidate.find('{', position)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(candidate, start)
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        position = end
    if len(objects) == 1:
        return json.dumps(objects[0], ensure_ascii=False)
    return candidate


def _parse_scenario_plan_candidate(candidate: str) -> ScenarioPlan:
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        diagnostics = ({
            'path': '<json>',
            'type': 'json_decode_error',
            'stage': 'json_decode',
        },)
        raise GenerationContractError('scenario_plan_invalid', diagnostics=diagnostics) from exc
    try:
        return ScenarioPlan.model_validate(_normalize_assertion_requirement_shapes(payload))
    except ValidationError as exc:
        raise GenerationContractError(
            'scenario_plan_invalid', diagnostics=_safe_diagnostics(exc),
        ) from exc


def _normalize_assertion_requirement_shapes(payload: Any) -> Any:
    """Mechanically repair mutually-exclusive assertion fields before validation.

    This deliberately only resolves contradictory ``kind``/field polarity.  It
    never invents a missing value or turns an otherwise ambiguous assertion
    into a valid one, so the strict contract and its one model repair remain
    the semantic authority.
    """
    if not isinstance(payload, dict):
        return payload
    requirements = payload.get('assertion_requirements')
    if not isinstance(requirements, list):
        return payload
    normalized = dict(payload)
    items: list[Any] = []
    for raw in requirements:
        if not isinstance(raw, dict):
            items.append(raw)
            continue
        item = dict(raw)
        kind = item.get('kind')
        input_ref = str(item.get('input_ref') or '')
        literal = str(item.get('literal') or '')
        if kind == 'visible':
            item['input_ref'] = ''
            item['literal'] = ''
        elif kind in {'contains_ref', 'not_contains_ref'}:
            item['literal'] = ''
            if not input_ref and literal:
                item['kind'] = kind.replace('_ref', '_literal')
                item['literal'] = literal
        elif kind in {'contains_literal', 'not_contains_literal'}:
            item['input_ref'] = ''
            if not literal and input_ref:
                item['kind'] = kind.replace('_literal', '_ref')
                item['input_ref'] = input_ref
        items.append(item)
    normalized['assertion_requirements'] = items
    return normalized


def parse_scenario_plan_json(
    raw_value: Any,
    *,
    format_repair: Callable[[str, tuple[dict[str, str], ...]], str] | None = None,
) -> ScenarioPlan:
    """Parse a v4 plan after deterministic cleanup and at most one semantic repair."""
    candidate = _extract_single_json_object(raw_value)
    for attempt in range(2):
        try:
            return _parse_scenario_plan_candidate(candidate)
        except GenerationContractError as exc:
            if attempt == 0 and format_repair is not None:
                candidate = _extract_single_json_object(
                    format_repair(candidate, exc.diagnostics),
                )
                continue
            raise
    raise AssertionError('unreachable')


def is_terminal_status(status: str) -> bool:
    return str(status) in {'ready', 'ready_with_warnings', 'needs_review', 'failed', 'cancelled'}


class GenerationTransitionError(ValueError):
    """A durable generation state transition would skip required ownership."""


_STATUS_STAGE = {
    'created': 'created', 'normalizing': 'normalizing', 'preflighting': 'preflighting',
    'exploring': 'exploring', 'generating': 'generating', 'validating': 'validating',
    'repairing': 'repairing', 'needs_input': 'normalizing', 'needs_confirmation': 'preflighting',
    'needs_credentials': 'preflighting', 'needs_review': 'completed', 'ready': 'completed',
    'ready_with_warnings': 'completed', 'failed': 'completed', 'cancelled': 'completed',
}
_NEXT_STATUS = {
    'created': {'created', 'normalizing', 'preflighting', 'failed', 'cancelled'},
    'normalizing': {'normalizing', 'preflighting', 'needs_input', 'failed', 'cancelled'},
    'preflighting': {'preflighting', 'exploring', 'needs_confirmation', 'needs_credentials', 'failed', 'cancelled'},
    'exploring': {'exploring', 'generating', 'needs_review', 'failed', 'cancelled'},
    'generating': {'generating', 'validating', 'needs_review', 'ready', 'ready_with_warnings', 'failed', 'cancelled'},
    'validating': {'validating', 'needs_review', 'ready', 'ready_with_warnings', 'failed', 'cancelled'},
    'repairing': {'repairing', 'needs_review', 'ready', 'ready_with_warnings', 'failed', 'cancelled'},
    'needs_input': {'normalizing', 'needs_review', 'cancelled'},
    'needs_confirmation': {'normalizing', 'preflighting', 'needs_review', 'cancelled'},
    'needs_credentials': {'preflighting', 'needs_review', 'cancelled'},
    'failed': {'generating'},
    'needs_review': set(), 'ready': set(), 'ready_with_warnings': set(), 'cancelled': set(),
}


def stage_for_status(status: str) -> str:
    try:
        return _STATUS_STAGE[str(status)]
    except KeyError as exc:
        raise GenerationTransitionError('未知生成状态') from exc


def validate_transition(current_status: str, target_status: str) -> None:
    if target_status not in _NEXT_STATUS.get(str(current_status), set()):
        raise GenerationTransitionError(f'不允许的生成状态迁移：{current_status} -> {target_status}')
