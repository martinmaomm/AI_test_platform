"""Strict v3 contracts for goal-scoped WebUI generation.

The persisted JSON in this module is deliberately v3-only. It does not read
or project the previous CRUD-step schema: callers must create a fresh
generation after the development data cleanup.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .execution_variables import ExecutionVariableError, validate_variable_name
from .generation_security import REDACTED_VALUE, find_suspected_credentials, redact_text

GOAL_PLAN_SCHEMA_VERSION = 3


class GenerationContractError(ValueError):
    """A model or persisted artifact is not a safe v3 contract."""

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
    """One explicit runtime input; source must never be inferred from its name."""
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
            expected_name = {
                'username': 'UI_TEST_USERNAME',
                'password': 'UI_TEST_PASSWORD',
            }[self.credential_slot]
            if self.name != expected_name:
                raise ValueError(f'{self.credential_slot} credential ref 必须命名为 {expected_name}')
        return self


class VerificationContract(_StrictContract):
    """Goal-owned assertion semantics, independent of site wording or locators."""
    mode: str = Field(pattern=r'^(?:visible|contains_ref|not_contains_ref)$')
    input_ref: str = Field(default='', max_length=128)

    @field_validator('input_ref', mode='before')
    @classmethod
    def _optional_execution_variable_name(cls, value):
        if value in (None, ''):
            return ''
        try:
            return validate_variable_name(value)
        except ExecutionVariableError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode='after')
    def _mode_shape(self):
        if self.mode == 'visible' and self.input_ref:
            raise ValueError('visible verification 不应声明 input_ref')
        if self.mode != 'visible' and not self.input_ref:
            raise ValueError('contains_ref/not_contains_ref verification 必须声明 input_ref')
        return self


class Goal(_StrictContract):
    id: str = Field(pattern=r'^G[1-9][0-9]*$')
    kind: str = Field(pattern=r'^(?:setup|exercise|verify|cleanup)$')
    objective: str = Field(min_length=2, max_length=1000)
    completion_criteria: str = Field(min_length=2, max_length=1000)
    input_refs: list[InputSpec] = Field(default_factory=list, max_length=20)
    verification: VerificationContract | None = None
    side_effect: str = Field(default='none', pattern=r'^(?:none|test_data|external|unknown)$')
    cleanup_for_goal_ids: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('input_refs', 'cleanup_for_goal_ids', mode='before')
    @classmethod
    def _list_only(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('字段必须是数组')
        return value

    @model_validator(mode='after')
    def _input_refs_unique(self):
        names = [item.name for item in self.input_refs]
        if len(names) != len(set(names)):
            raise ValueError('同一 Goal 的 input ref 不可重复')
        if self.kind == 'verify' and self.verification is None:
            raise ValueError('verify Goal 必须声明 verification contract')
        if self.kind == 'cleanup' and self.cleanup_for_goal_ids and self.verification is None:
            raise ValueError('清理测试数据的 cleanup Goal 必须声明 verification contract')
        if self.kind not in {'verify', 'cleanup'} and self.verification is not None:
            raise ValueError('仅 verify/cleanup Goal 可以声明 verification contract')
        if self.verification and self.verification.input_ref and self.verification.input_ref not in names:
            raise ValueError('verification input_ref 必须属于当前 Goal')
        return self


class GoalPlan(_StrictContract):
    schema_version: int = Field(default=GOAL_PLAN_SCHEMA_VERSION, frozen=True)
    title: str = Field(min_length=2, max_length=200)
    objective: str = Field(min_length=2, max_length=1000)
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    goals: list[Goal] = Field(min_length=1, max_length=30)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=30)
    credentials_required: bool = False
    discovery_notes: list[str] = Field(default_factory=list, max_length=30)
    ambiguities: list[str] = Field(default_factory=list, max_length=20)
    risk_level: str = Field(default='low', pattern=r'^(?:low|medium|high)$')

    @field_validator('schema_version')
    @classmethod
    def _only_v3(cls, value):
        if value != GOAL_PLAN_SCHEMA_VERSION:
            raise ValueError('仅支持 schema_version=3')
        return value

    @field_validator('preconditions', 'forbidden_actions', 'discovery_notes', 'ambiguities', mode='before')
    @classmethod
    def _text_list(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('字段必须是数组')
        return [str(item) for item in value]

    @model_validator(mode='after')
    def _validate_goal_graph(self):
        ids = [goal.id for goal in self.goals]
        if len(ids) != len(set(ids)):
            raise ValueError('Goal ID 不可重复')
        known_ids = set(ids)
        cleanup_references: set[str] = set()
        for goal in self.goals:
            if goal.kind != 'cleanup' and goal.cleanup_for_goal_ids:
                raise ValueError('仅 cleanup Goal 可以声明 cleanup_for_goal_ids')
            if goal.kind == 'cleanup':
                unknown = set(goal.cleanup_for_goal_ids) - known_ids
                if unknown or goal.id in goal.cleanup_for_goal_ids:
                    raise ValueError('cleanup_for_goal_ids 必须引用其他已有 Goal')
                cleanup_references.update(goal.cleanup_for_goal_ids)
        required_cleanup = {goal.id for goal in self.goals if goal.side_effect == 'test_data'}
        if required_cleanup - cleanup_references:
            raise ValueError('写入测试数据的 Goal 必须有 cleanup Goal 引用')
        input_specs: dict[str, tuple[str, str]] = {}
        credential_slots: set[str] = set()
        for goal in self.goals:
            for item in goal.input_refs:
                shape = (item.source, item.credential_slot)
                if item.name in input_specs and input_specs[item.name] != shape:
                    raise ValueError(f'执行变量 {item.name} 的来源或凭据槽定义冲突')
                input_specs[item.name] = shape
                if item.source == 'credential':
                    credential_slots.add(item.credential_slot)
        if self.credentials_required and credential_slots != {'username', 'password'}:
            raise ValueError('credentials_required=true 时必须声明 username 和 password 凭据变量')
        if not self.credentials_required and credential_slots:
            raise ValueError('声明 credential input ref 时 credentials_required 必须为 true')
        if not any(goal.verification is not None for goal in self.goals):
            raise ValueError('GoalPlan 至少需要一个可编译的 verification contract')
        _validate_safe_value(
            self.model_dump(mode='json'), 'GoalPlan', reject_absolute_url=True,
        )
        self.input_sources()
        return self

    def input_sources(self) -> dict[str, str]:
        """Return the explicit source for every execution variable in the plan."""
        result: dict[str, str] = {}
        for goal in self.goals:
            for item in goal.input_refs:
                existing = result.get(item.name)
                if existing is not None and existing != item.source:
                    raise GenerationContractError('input_ref_source_conflict')
                result[item.name] = item.source
        return result


def parse_goal_plan_json(raw_text: str, *, format_repair: Callable[[str, str], str] | None = None) -> GoalPlan:
    """Parse one model response, allowing one JSON-format-only repair."""
    candidate = str(raw_text or '')
    for attempt in range(2):
        try:
            return GoalPlan.model_validate(json.loads(candidate))
        except (ValueError, TypeError, ValidationError) as exc:
            diagnostics = _safe_diagnostics(exc) if isinstance(exc, ValidationError) else ()
            if attempt == 0 and format_repair is not None:
                candidate = format_repair(candidate, str(exc))
                continue
            raise GenerationContractError('goal_plan_invalid', diagnostics=diagnostics) from exc
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
