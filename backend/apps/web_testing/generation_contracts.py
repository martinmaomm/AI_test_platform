"""Strict v4 contracts for one continuous WebUI exploration."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .execution_variables import ExecutionVariableError, validate_variable_name
from .generation_security import REDACTED_VALUE, find_suspected_credentials, redact_text

SCENARIO_PLAN_SCHEMA_VERSION = 4


class GenerationContractError(ValueError):
    """A model or persisted artifact is not a safe v4 contract."""
    def __init__(self, message: str = 'contract_invalid', *, diagnostics: tuple[dict[str, str], ...] = ()):
        super().__init__(message)
        self.diagnostics = diagnostics


class ScenarioInputInsufficientError(GenerationContractError):
    """Only blank or genuinely target-free descriptions use this error."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @model_validator(mode='after')
    def _reject_sensitive_text(self):
        _validate_safe_value(self.model_dump(mode='json'), self.__class__.__name__)
        return self


def _validate_safe_value(value: Any, field_name: str, *, reject_absolute_url: bool = False) -> None:
    if isinstance(value, str):
        if REDACTED_VALUE in value or find_suspected_credentials(value) or redact_text(value) != value:
            raise ValueError(f'{field_name} 不能包含敏感信息')
        if reject_absolute_url and re.search(r'(?i)https?://', value):
            raise ValueError(f'{field_name} 不能包含完整 URL')
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_safe_value(str(key), field_name, reject_absolute_url=reject_absolute_url)
            _validate_safe_value(item, field_name, reject_absolute_url=reject_absolute_url)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_value(item, field_name, reject_absolute_url=reject_absolute_url)


def _safe_diagnostics(error: ValidationError) -> tuple[dict[str, str], ...]:
    return tuple({
        'path': '.'.join(str(part) if isinstance(part, str) else '[item]' for part in item.get('loc', ()))[:160] or '<contract>',
        'type': str(item.get('type') or 'validation_error')[:80],
        'stage': 'contract_validation',
    } for item in error.errors(include_input=False, include_context=False, include_url=False)[:3])


class InputSpec(_StrictContract):
    """One explicit runtime input; its value never belongs in persisted JSON."""
    name: str = Field(min_length=1, max_length=128)
    source: str = Field(pattern=r'^(?:generated|runtime|credential)$')
    credential_slot: str = Field(default='', pattern=r'^(?:|username|password)$')

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
    input_refs: list[InputSpec] = Field(default_factory=list, max_length=20)
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


def parse_scenario_plan_json(raw_text: str, *, format_repair: Callable[[str, str], str] | None = None) -> ScenarioPlan:
    candidate = str(raw_text or '')
    for attempt in range(2):
        try:
            return ScenarioPlan.model_validate(json.loads(candidate))
        except (ValueError, TypeError, ValidationError) as exc:
            diagnostics = _safe_diagnostics(exc) if isinstance(exc, ValidationError) else ()
            if attempt == 0 and format_repair is not None:
                candidate = format_repair(candidate, str(exc))
                continue
            raise GenerationContractError('scenario_plan_invalid', diagnostics=diagnostics) from exc
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
