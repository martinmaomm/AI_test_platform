"""State and stage contracts for the WebUI script-generation pipeline."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from types import UnionType
from typing import Any, Callable, Final, Literal, TypeVar, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import WebUIScriptGeneration
from .generation_security import (
    REDACTED_VALUE,
    find_suspected_credentials,
    redact_dom_attributes,
    redact_text,
)


TContract = TypeVar('TContract', bound=BaseModel)


class GenerationContractError(ValueError):
    """Raised when a persisted generation artifact is not valid structured JSON."""

    def __init__(self, message: str = 'contract_invalid', *, diagnostics: tuple[dict[str, str], ...] = ()):
        super().__init__(message)
        self.diagnostics = diagnostics


_ABSOLUTE_URL_RE = re.compile(r'(?i)\bhttps?://')
_ACTION_CALL_RE = re.compile(
    r'(?i)\b(?:click|fill|press|goto|evaluate|select_option|check|uncheck|hover|'
    r'upload_file|download)\s*\(',
)
_LOCATOR_ACTIONS = frozenset({
    'click', 'fill', 'press', 'goto', 'evaluate', 'select_option', 'check',
    'uncheck', 'hover', 'upload_file', 'download',
})
_SAFE_STEP_ID_RE = re.compile(r'^S[1-9][0-9]*$')
_MAX_SAFE_DIAGNOSTICS = 3
_MAX_SAFE_PATH_CHARS = 160
_MAX_SAFE_STEP_ID_CHARS = 12
_SAFE_ERROR_TYPES = frozenset({
    'arguments_type', 'bool_parsing', 'bool_type', 'dict_type', 'extra_forbidden',
    'float_parsing', 'float_type', 'greater_than_equal', 'int_parsing', 'int_type',
    'less_than_equal', 'list_type', 'literal_error', 'missing', 'model_type',
    'string_pattern_mismatch', 'string_too_long', 'string_too_short', 'string_type',
    'too_long', 'too_short', 'value_error',
})


def _ensure_safe_contract_text(value: str, *, field_name: str, reject_absolute_url: bool = False) -> str:
    """Reject values which would leak credentials into persisted artifacts."""
    text = str(value)
    if (
        REDACTED_VALUE in text
        or find_suspected_credentials(text)
        or redact_text(text) != text
    ):
        raise ValueError(f'{field_name} 不能包含敏感信息')
    if reject_absolute_url and _ABSOLUTE_URL_RE.search(text):
        raise ValueError(f'{field_name} 不能包含完整 URL')
    return text


def _validate_nested_text(
    value: Any,
    *,
    field_name: str,
    reject_absolute_url: bool = False,
) -> None:
    if isinstance(value, str):
        _ensure_safe_contract_text(
            value,
            field_name=field_name,
            reject_absolute_url=reject_absolute_url,
        )
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_nested_text(
                str(key),
                field_name=field_name,
                reject_absolute_url=reject_absolute_url,
            )
            _validate_nested_text(
                item,
                field_name=field_name,
                reject_absolute_url=reject_absolute_url,
            )
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_nested_text(
                item,
                field_name=field_name,
                reject_absolute_url=reject_absolute_url,
            )


def _unwrap_optional_contract_type(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin not in (UnionType, Union):
        return annotation
    args = [item for item in get_args(annotation) if item is not type(None)]
    return args[0] if len(args) == 1 else Any


def _safe_validation_path(model_type: type[BaseModel], location: tuple[Any, ...]) -> str:
    """Project a Pydantic error location onto contract-owned, non-secret names."""
    current: Any = model_type
    parts: list[str] = []
    for item in location:
        if isinstance(current, type) and issubclass(current, BaseModel):
            if isinstance(item, str) and item in current.model_fields:
                parts.append(item)
                current = _unwrap_optional_contract_type(current.model_fields[item].annotation)
            else:
                parts.append('<field>')
                current = Any
        elif get_origin(current) is list:
            if isinstance(item, int) and item >= 0:
                parts.append(f'[{min(item, 999)}]')
            else:
                parts.append('[item]')
            current = _unwrap_optional_contract_type(get_args(current)[0]) if get_args(current) else Any
        elif get_origin(current) in (dict, Mapping):
            if isinstance(item, str) and len(item) <= _MAX_SAFE_STEP_ID_CHARS and _SAFE_STEP_ID_RE.fullmatch(item):
                parts.append(item)
            else:
                parts.append('<key>')
            args = get_args(current)
            current = _unwrap_optional_contract_type(args[1]) if len(args) == 2 else Any
        else:
            parts.append('<field>')
            current = Any
    result = ''
    for part in parts:
        result += part if part.startswith('[') or not result else f'.{part}'
    return (result or '<contract>')[:_MAX_SAFE_PATH_CHARS]


def _safe_validation_diagnostics(error: ValidationError, model_type: type[BaseModel]) -> tuple[dict[str, str], ...]:
    diagnostics = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:_MAX_SAFE_DIAGNOSTICS]:
        error_type = str(item.get('type') or 'validation_error')
        diagnostics.append({
            'path': _safe_validation_path(model_type, tuple(item.get('loc') or ())),
            'type': error_type if error_type in _SAFE_ERROR_TYPES else 'validation_error',
            'stage': 'contract_validation',
        })
    return tuple(diagnostics)


def _locator_contains_action_call(locator: str) -> bool:
    """Reject executable action calls while allowing words inside locator literals."""
    try:
        tree = ast.parse(locator, mode='eval')
    except (SyntaxError, ValueError, TypeError):
        literal_free = _without_complete_quoted_literals(locator)
        # A malformed candidate cannot prove that apparent action syntax is text.
        return literal_free is None or bool(_ACTION_CALL_RE.search(literal_free))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else (
            function.id if isinstance(function, ast.Name) else ''
        )
        if name.lower() in _LOCATOR_ACTIONS:
            return True
    return False


def _without_complete_quoted_literals(value: str) -> str | None:
    """Mask complete CSS-style quotes; an incomplete escape or quote is unsafe."""
    result: list[str] = []
    index = 0
    while index < len(value):
        quote = value[index]
        if quote not in {'\'', '"'}:
            result.append(quote)
            index += 1
            continue
        prefix_start = index
        while prefix_start > 0 and value[prefix_start - 1].isalpha():
            prefix_start -= 1
        prefix = value[prefix_start:index].lower()
        has_identifier_boundary = prefix_start == 0 or not (
            value[prefix_start - 1].isalnum() or value[prefix_start - 1] == '_'
        )
        if prefix in {'f', 'fr', 'rf'} and has_identifier_boundary:
            # AST handles valid f-strings. In malformed expression text, do not
            # risk masking an interpolation that invokes a page action.
            return None
        result.append(' ')
        index += 1
        closed = False
        while index < len(value):
            current = value[index]
            if current == '\\':
                if index + 1 >= len(value):
                    return None
                result.extend('  ')
                index += 2
            elif current == quote:
                result.append(' ')
                index += 1
                closed = True
                break
            else:
                result.append(' ')
                index += 1
        if not closed:
            return None
    return ''.join(result)


def _safe_diagnostic_summary(diagnostics: tuple[dict[str, str], ...]) -> str:
    if not diagnostics:
        return 'contract_validation'
    return '; '.join(
        f"{item['stage']}:{item['path']}:{item['type']}" for item in diagnostics
    )[:600]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @model_validator(mode='after')
    def reject_sensitive_text(self):
        _validate_nested_text(self.model_dump(mode='json'), field_name=self.__class__.__name__)
        return self


class ScenarioStep(_StrictContract):
    id: str = Field(pattern=r'^S[1-9][0-9]*$')
    name: str = Field(min_length=2, max_length=120)
    intent: Literal['navigate', 'read', 'create', 'update', 'delete', 'assert', 'cleanup']
    target_hint: str = Field(min_length=1, max_length=300)
    input_refs: list[str] = Field(default_factory=list, max_length=20)
    mutates_data: bool = False
    expected: str = Field(min_length=1, max_length=500)

    @field_validator('input_refs', mode='before')
    @classmethod
    def reject_sensitive_input_references(cls, value):
        values = value or []
        if not isinstance(values, list):
            raise ValueError('input_refs 必须是数组')
        sanitized = [redact_text(str(item)) for item in values]
        if any('<redacted>' in item for item in sanitized):
            raise ValueError('input_refs 不能包含敏感值')
        return sanitized


class ScenarioAssertion(_StrictContract):
    id: str = Field(pattern=r'^A[1-9][0-9]*$')
    name: str = Field(min_length=2, max_length=120)
    target_hint: str = Field(min_length=1, max_length=300)
    expected: str = Field(min_length=1, max_length=500)
    step_id: str | None = Field(default=None, pattern=r'^S[1-9][0-9]*$')


class ScenarioCleanup(_StrictContract):
    id: str = Field(pattern=r'^C[1-9][0-9]*$')
    name: str = Field(min_length=2, max_length=120)
    target_hint: str = Field(min_length=1, max_length=300)
    condition: str = Field(min_length=1, max_length=500)
    step_id: str | None = Field(default=None, pattern=r'^S[1-9][0-9]*$')


class ScenarioSpec(_StrictContract):
    title: str = Field(min_length=2, max_length=200)
    objective: str = Field(min_length=2, max_length=1000)
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    steps: list[ScenarioStep] = Field(min_length=1, max_length=30)
    assertions: list[ScenarioAssertion] = Field(default_factory=list, max_length=30)
    cleanup: list[ScenarioCleanup] = Field(default_factory=list, max_length=20)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=30)
    credentials_required: bool = False
    discovery_targets: list[str] = Field(default_factory=list, max_length=20)
    ambiguities: list[str] = Field(default_factory=list, max_length=20)
    risk_level: Literal['low', 'medium', 'high'] = 'low'

    @field_validator('preconditions', 'forbidden_actions', 'discovery_targets', 'ambiguities', mode='before')
    @classmethod
    def sanitize_text_list(cls, value):
        values = value or []
        if not isinstance(values, list):
            raise ValueError('字段必须是数组')
        sanitized = [redact_text(str(item)) for item in values]
        if any('<redacted>' in item for item in sanitized):
            raise ValueError('场景契约不能包含敏感值')
        return sanitized

    @field_validator('steps')
    @classmethod
    def verify_unique_step_ids(cls, value):
        ids = [step.id for step in value]
        if len(ids) != len(set(ids)):
            raise ValueError('步骤 ID 不可重复')
        return value

    @model_validator(mode='after')
    def validate_step_references_and_cleanup(self):
        step_ids = {step.id for step in self.steps}
        if not self.assertions:
            raise ValueError('场景至少需要一个 assertion')
        invalid_assertion_ids = {
            assertion.step_id for assertion in self.assertions
            if assertion.step_id is not None and assertion.step_id not in step_ids
        }
        if invalid_assertion_ids:
            raise ValueError('assertion.step_id 必须引用已有步骤')
        invalid_cleanup_ids = {
            cleanup.step_id for cleanup in self.cleanup
            if cleanup.step_id is not None and cleanup.step_id not in step_ids
        }
        if invalid_cleanup_ids:
            raise ValueError('cleanup.step_id 必须引用已有步骤')
        mutates_data = any(
            step.mutates_data or step.intent in {'create', 'update', 'delete'}
            for step in self.steps
        )
        if mutates_data and not self.cleanup:
            raise ValueError('涉及新增、编辑或删除时必须明确提供 cleanup，不能由平台编造')
        return self


class ExplorationElement(_StrictContract):
    page_name: str = Field(min_length=1, max_length=160)
    role: str = Field(default='', max_length=80)
    visible_name: str = Field(default='', max_length=300)
    stable_attributes: dict[str, str] = Field(default_factory=dict)
    candidate_locators: list[str] = Field(default_factory=list, max_length=10)

    @field_validator('stable_attributes', mode='before')
    @classmethod
    def sanitize_attributes(cls, value):
        sanitized = redact_dom_attributes(value or {})
        if not isinstance(sanitized, dict):
            raise ValueError('stable_attributes 必须是对象')
        return {str(key): str(item) for key, item in sanitized.items()}

    @field_validator('candidate_locators')
    @classmethod
    def reject_locator_actions_or_sensitive_values(cls, value):
        for locator in value:
            _ensure_safe_contract_text(
                locator,
                field_name='candidate_locator',
                reject_absolute_url=True,
            )
            if _locator_contains_action_call(locator):
                raise ValueError('candidate_locator 只能描述定位器，不能包含动作调用')
        return value


class PageState(_StrictContract):
    name: str = Field(min_length=1, max_length=160)
    title: str = Field(default='', max_length=300)
    path: str = Field(pattern=r'^/')
    key_regions: list[str] = Field(default_factory=list, max_length=30)


class NavigationPath(_StrictContract):
    step_id: str | None = Field(default=None, pattern=r'^S[1-9][0-9]*$')
    action: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=300)
    from_path: str = Field(pattern=r'^/')
    to_path: str = Field(pattern=r'^/')


class StepEvidence(_StrictContract):
    status: Literal['confirmed', 'partially_confirmed', 'unresolved']
    paths: list[str] = Field(default_factory=list, max_length=20)
    element_names: list[str] = Field(default_factory=list, max_length=30)
    reason: str = Field(default='', max_length=500)

    @field_validator('paths')
    @classmethod
    def relative_paths_only(cls, value):
        if any(not path.startswith('/') for path in value):
            raise ValueError('探索路径必须是相对路径')
        return value


class ExplorationToolStats(_StrictContract):
    total_tool_calls: int = Field(ge=0)
    tool_counts: dict[str, int] = Field(default_factory=dict)
    failed_tool_calls: int = Field(default=0, ge=0)
    termination_reason: str | None = Field(default=None, max_length=100)
    duration_seconds: float = Field(default=0, ge=0)


class ExplorationSnapshot(_StrictContract):
    start_url_path: str = Field(pattern=r'^/')
    visited_paths: list[str] = Field(default_factory=list, max_length=100)
    page_states: list[PageState] = Field(default_factory=list, max_length=50)
    elements: list[ExplorationElement] = Field(default_factory=list, max_length=300)
    navigation_paths: list[NavigationPath] = Field(default_factory=list, max_length=100)
    step_evidence: dict[str, StepEvidence] = Field(default_factory=dict)
    unresolved_steps: list[str] = Field(default_factory=list, max_length=30)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    tool_stats: ExplorationToolStats

    @field_validator('visited_paths')
    @classmethod
    def sanitize_visited_paths(cls, value):
        if any(not path.startswith('/') for path in value):
            raise ValueError('visited_paths 只能保存相对路径')
        if any(
            _ABSOLUTE_URL_RE.search(path)
            or re.search(r'(?i)(token|secret|password|session|auth)=', path)
            for path in value
        ):
            raise ValueError('visited_paths 不能包含敏感查询参数')
        return value

    @field_validator('step_evidence')
    @classmethod
    def verify_step_evidence_keys(cls, value):
        if any(not re.fullmatch(r'S[1-9][0-9]*', key) for key in value):
            raise ValueError('step_evidence 的键必须是 ScenarioSpec 步骤 ID')
        return value

    @model_validator(mode='after')
    def reject_urls_from_all_snapshot_text(self):
        _validate_nested_text(
            self.model_dump(mode='json'),
            field_name='ExplorationSnapshot',
            reject_absolute_url=True,
        )
        return self


def validate_snapshot_against_scenario(
    scenario: ScenarioSpec,
    snapshot: ExplorationSnapshot,
) -> None:
    """Require one confirmed, partial, or unresolved evidence item per scenario step."""
    scenario_step_ids = {step.id for step in scenario.steps}
    evidence_step_ids = set(snapshot.step_evidence)
    unknown_step_ids = evidence_step_ids - scenario_step_ids
    if unknown_step_ids:
        raise GenerationContractError('探索证据包含未知场景步骤')
    missing_step_ids = scenario_step_ids - evidence_step_ids
    if missing_step_ids:
        raise GenerationContractError('探索证据缺少场景步骤状态')
    for step_id in snapshot.unresolved_steps:
        if step_id not in scenario_step_ids:
            raise GenerationContractError('未确认步骤必须引用已有场景步骤')
        if snapshot.step_evidence[step_id].status != 'unresolved':
            raise GenerationContractError('未确认步骤必须对应 unresolved 证据状态')


def merge_exploration_snapshots(
    current: ExplorationSnapshot,
    supplemental: ExplorationSnapshot,
    *,
    scenario: ScenarioSpec,
    target_step_ids: set[str],
) -> ExplorationSnapshot:
    """Merge one directed evidence supplement without persisting full URLs.

    Only evidence for the requested missing steps may be replaced; the original
    exploration remains the source of truth for every other scenario step.
    """
    valid_steps = {step.id for step in scenario.steps}
    if not target_step_ids or not target_step_ids <= valid_steps:
        raise GenerationContractError('定向补充探索包含无效步骤')
    payload = current.model_dump(mode='json')
    supplement = supplemental.model_dump(mode='json')
    for name in ('visited_paths', 'page_states', 'elements', 'navigation_paths', 'warnings'):
        seen: set[str] = set()
        merged = []
        for item in [*payload.get(name, []), *supplement.get(name, [])]:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        payload[name] = merged
    evidence = dict(payload.get('step_evidence') or {})
    for step_id in target_step_ids:
        candidate = (supplement.get('step_evidence') or {}).get(step_id)
        if candidate:
            evidence[step_id] = candidate
    payload['step_evidence'] = evidence
    payload['unresolved_steps'] = sorted(
        step_id for step_id, item in evidence.items()
        if item.get('status') == 'unresolved'
    )
    previous_stats = payload.get('tool_stats') or {}
    extra_stats = supplement.get('tool_stats') or {}
    counts = dict(previous_stats.get('tool_counts') or {})
    for name, count in (extra_stats.get('tool_counts') or {}).items():
        counts[name] = counts.get(name, 0) + int(count)
    payload['tool_stats'] = {
        'total_tool_calls': int(previous_stats.get('total_tool_calls', 0)) + int(extra_stats.get('total_tool_calls', 0)),
        'tool_counts': counts,
        'failed_tool_calls': int(previous_stats.get('failed_tool_calls', 0)) + int(extra_stats.get('failed_tool_calls', 0)),
        'termination_reason': extra_stats.get('termination_reason') or previous_stats.get('termination_reason'),
        'duration_seconds': float(previous_stats.get('duration_seconds', 0)) + float(extra_stats.get('duration_seconds', 0)),
    }
    result = ExplorationSnapshot.model_validate(payload)
    validate_snapshot_against_scenario(scenario, result)
    return result


def _extract_json_object(raw_text: str) -> Any:
    text = str(raw_text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise GenerationContractError(f'模型输出不是有效 JSON: {exc}') from exc


def parse_contract_json(
    raw_text: str,
    model_type: type[TContract],
    *,
    format_repair: Callable[[str, str], str] | None = None,
) -> TContract:
    """Parse one strict contract; a caller may perform exactly one format repair."""
    try:
        return model_type.model_validate(_extract_json_object(raw_text))
    except (GenerationContractError, ValidationError) as first_error:
        diagnostics = (
            _safe_validation_diagnostics(first_error, model_type)
            if isinstance(first_error, ValidationError) else first_error.diagnostics
        )
        if format_repair is None:
            raise GenerationContractError('contract_invalid', diagnostics=diagnostics) from first_error
        repaired_text = format_repair(raw_text, _safe_diagnostic_summary(diagnostics))
        try:
            return model_type.model_validate(_extract_json_object(repaired_text))
        except (GenerationContractError, ValidationError) as second_error:
            diagnostics = (
                _safe_validation_diagnostics(second_error, model_type)
                if isinstance(second_error, ValidationError) else second_error.diagnostics
            )
            raise GenerationContractError('contract_invalid_after_repair', diagnostics=diagnostics) from second_error


def parse_scenario_spec_json(raw_text: str, *, format_repair=None) -> ScenarioSpec:
    return parse_contract_json(raw_text, ScenarioSpec, format_repair=format_repair)


def parse_exploration_snapshot_json(raw_text: str, *, format_repair=None) -> ExplorationSnapshot:
    return parse_contract_json(raw_text, ExplorationSnapshot, format_repair=format_repair)


class GenerationTransitionError(ValueError):
    """Raised when a generation state transition would skip pipeline rules."""


TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({
    WebUIScriptGeneration.Status.READY,
    WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
    WebUIScriptGeneration.Status.NEEDS_REVIEW,
    WebUIScriptGeneration.Status.CANCELLED,
    WebUIScriptGeneration.Status.FAILED,
})


ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    WebUIScriptGeneration.Status.CREATED: frozenset({
        WebUIScriptGeneration.Status.NORMALIZING,
        WebUIScriptGeneration.Status.NEEDS_INPUT,
        WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.NORMALIZING: frozenset({
        WebUIScriptGeneration.Status.PREFLIGHTING,
        WebUIScriptGeneration.Status.NEEDS_INPUT,
        WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
        WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.PREFLIGHTING: frozenset({
        WebUIScriptGeneration.Status.EXPLORING,
        WebUIScriptGeneration.Status.NEEDS_INPUT,
        WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
        WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.EXPLORING: frozenset({
        WebUIScriptGeneration.Status.GENERATING,
        WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.GENERATING: frozenset({
        WebUIScriptGeneration.Status.VALIDATING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.VALIDATING: frozenset({
        WebUIScriptGeneration.Status.READY,
        WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
        WebUIScriptGeneration.Status.REPAIRING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.REPAIRING: frozenset({
        WebUIScriptGeneration.Status.VALIDATING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
        WebUIScriptGeneration.Status.FAILED,
    }),
    WebUIScriptGeneration.Status.NEEDS_INPUT: frozenset({
        WebUIScriptGeneration.Status.NORMALIZING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
    }),
    WebUIScriptGeneration.Status.NEEDS_CONFIRMATION: frozenset({
        WebUIScriptGeneration.Status.NORMALIZING,
        WebUIScriptGeneration.Status.PREFLIGHTING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
    }),
    WebUIScriptGeneration.Status.NEEDS_CREDENTIALS: frozenset({
        WebUIScriptGeneration.Status.PREFLIGHTING,
        WebUIScriptGeneration.Status.NEEDS_REVIEW,
        WebUIScriptGeneration.Status.CANCELLED,
    }),
}


STAGE_FOR_STATUS: Final[dict[str, str]] = {
    WebUIScriptGeneration.Status.CREATED: WebUIScriptGeneration.Stage.CREATED,
    WebUIScriptGeneration.Status.NORMALIZING: WebUIScriptGeneration.Stage.NORMALIZING,
    WebUIScriptGeneration.Status.PREFLIGHTING: WebUIScriptGeneration.Stage.PREFLIGHTING,
    WebUIScriptGeneration.Status.EXPLORING: WebUIScriptGeneration.Stage.EXPLORING,
    WebUIScriptGeneration.Status.GENERATING: WebUIScriptGeneration.Stage.GENERATING,
    WebUIScriptGeneration.Status.VALIDATING: WebUIScriptGeneration.Stage.VALIDATING,
    WebUIScriptGeneration.Status.REPAIRING: WebUIScriptGeneration.Stage.REPAIRING,
    WebUIScriptGeneration.Status.NEEDS_INPUT: WebUIScriptGeneration.Stage.NORMALIZING,
    WebUIScriptGeneration.Status.NEEDS_CONFIRMATION: WebUIScriptGeneration.Stage.PREFLIGHTING,
    WebUIScriptGeneration.Status.NEEDS_CREDENTIALS: WebUIScriptGeneration.Stage.PREFLIGHTING,
    WebUIScriptGeneration.Status.NEEDS_REVIEW: WebUIScriptGeneration.Stage.COMPLETED,
    WebUIScriptGeneration.Status.READY: WebUIScriptGeneration.Stage.COMPLETED,
    WebUIScriptGeneration.Status.READY_WITH_WARNINGS: WebUIScriptGeneration.Stage.COMPLETED,
    WebUIScriptGeneration.Status.CANCELLED: WebUIScriptGeneration.Stage.COMPLETED,
    WebUIScriptGeneration.Status.FAILED: WebUIScriptGeneration.Stage.COMPLETED,
}


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES


def validate_transition(current_status: str, target_status: str) -> None:
    """Validate one state change; retries to the same status are idempotent."""
    if current_status == target_status:
        return
    if is_terminal_status(current_status):
        raise GenerationTransitionError(f'终态任务不能再从 {current_status} 切换到 {target_status}')
    if target_status not in ALLOWED_TRANSITIONS.get(current_status, frozenset()):
        raise GenerationTransitionError(f'不允许从 {current_status} 切换到 {target_status}')


def stage_for_status(status: str) -> str:
    try:
        return STAGE_FOR_STATUS[status]
    except KeyError as exc:
        raise GenerationTransitionError(f'未知生成状态: {status}') from exc
