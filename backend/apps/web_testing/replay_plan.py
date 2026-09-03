"""Deterministic v4 replay from explicitly owned successful callbacks."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exploration_trace import AssertionEvidence, ExplorationTrace, effective_scenario_plan
from .generation_contracts import GenerationContractError, ScenarioPlan

_TEMPLATE_REF_RE = re.compile(r'\{\{([A-Z_][A-Z0-9_]*)\}\}')


class ReplayAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    step_type: Literal['action'] = 'action'
    sequence: int = Field(ge=1)
    event_id: str
    evidence_id: str
    action: str
    relative_path: str
    input_refs: list[str] = Field(default_factory=list)
    input_source: str = Field(default='', pattern=r'^(?:|generated|runtime|credential)$')
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    step_name: str = Field(default='', max_length=80)


class ReplayAssertion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    step_type: Literal['assertion'] = 'assertion'
    sequence: int = Field(ge=1)
    assertion_id: str = Field(pattern=r'^A[1-9][0-9]*$')
    criterion_index: int = Field(ge=0, le=19)
    phase: str = Field(pattern=r'^(?:main|cleanup)$')
    event_id: str = Field(pattern=r'^E[0-9]{6}$')
    evidence_id: str = Field(pattern=r'^L[0-9]{6}$')
    kind: str = Field(
        pattern=r'^(?:visible|contains_ref|not_contains_ref|contains_literal|not_contains_literal)$'
    )
    input_ref: str = ''
    literal: str = ''
    criterion: str = Field(default='', max_length=500)


ReplayStep = Annotated[
    ReplayAction | ReplayAssertion,
    Field(discriminator='step_type'),
]


class ReplayPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = 4
    actions: list[ReplayAction] = Field(default_factory=list)
    cleanup_actions: list[ReplayAction] = Field(default_factory=list)
    assertions: list[ReplayAssertion] = Field(default_factory=list)
    cleanup_assertions: list[ReplayAssertion] = Field(default_factory=list)
    main_steps: list[ReplayStep] = Field(default_factory=list)
    cleanup_steps: list[ReplayStep] = Field(default_factory=list)
    assertion_event_ids: list[str] = Field(default_factory=list)
    input_sources: dict[str, str] = Field(default_factory=dict)
    input_value_kinds: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _ordered_steps_match_views(self):
        expected_main = sorted(
            [*self.actions, *self.assertions], key=lambda item: item.sequence,
        )
        expected_cleanup = sorted(
            [*self.cleanup_actions, *self.cleanup_assertions],
            key=lambda item: item.sequence,
        )
        if self.main_steps != expected_main or self.cleanup_steps != expected_cleanup:
            raise ValueError('ReplayPlan steps 必须精确保持 callback sequence 顺序')
        seen_cleanup_action = False
        for step in self.cleanup_steps:
            if isinstance(step, ReplayAction):
                seen_cleanup_action = True
            elif not seen_cleanup_action:
                raise ValueError('cleanup assertion 前必须已有成功 cleanup action')
        return self


def _template_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_TEMPLATE_REF_RE.findall(value))
    if isinstance(value, Mapping):
        refs: set[str] = set()
        for key, item in value.items():
            refs.update(_template_refs(key))
            refs.update(_template_refs(item))
        return refs
    if isinstance(value, (list, tuple)):
        refs: set[str] = set()
        for item in value:
            refs.update(_template_refs(item))
        return refs
    return set()


def _has_sensitive_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return '<runtime_sensitive_data>' in value
    if isinstance(value, Mapping):
        return any(
            _has_sensitive_placeholder(key) or _has_sensitive_placeholder(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_sensitive_placeholder(item) for item in value)
    return False


class ReplayPlanner:
    @staticmethod
    def _action(
        *,
        event_id: str,
        plan: ScenarioPlan,
        trace: ExplorationTrace,
    ) -> ReplayAction:
        events = {event.event_id: event for event in trace.events}
        evidence = {item.event_id: item for item in trace.locator_evidence}
        event = events.get(event_id)
        item = evidence.get(event_id)
        if event is None or event.status != 'succeeded':
            raise GenerationContractError('replay_plan_event_not_callback_success')
        if item is None or item.validation in {'fragile', 'rejected'}:
            raise GenerationContractError('replay_plan_selected_action_locator_invalid')
        owned_values = (item.value, item.kwargs, event.action_arguments)
        if any(_has_sensitive_placeholder(value) for value in owned_values):
            raise GenerationContractError('replay_plan_sensitive_locator_value')
        refs: set[str] = set()
        for value in owned_values:
            refs.update(_template_refs(value))
        if not refs <= set(plan.input_sources()):
            raise GenerationContractError('replay_plan_locator_template_ref_unknown')
        if event.action in {'fill', 'select'}:
            if len(event.input_refs) != 1 or event.input_source not in {
                'generated', 'runtime', 'credential',
            }:
                raise GenerationContractError('replay_plan_selected_action_input_mapping_invalid')
            if plan.input_sources().get(event.input_refs[0]) != event.input_source:
                raise GenerationContractError('replay_plan_input_source_mismatch')
        if event.action == 'press' and not str(event.action_arguments.get('key') or '').strip():
            raise GenerationContractError('replay_plan_selected_press_key_missing')
        return ReplayAction(
            sequence=event.sequence,
            event_id=event_id,
            evidence_id=item.evidence_id,
            action=event.action,
            # The first successful navigate proves entry; the executor owns base_url
            # and always replays the configured relative start path.
            relative_path=trace.start_path if event.action == 'navigate' else event.relative_path,
            input_refs=event.input_refs,
            input_source=event.input_source,
            action_arguments=event.action_arguments,
            step_name=next((
                item.step_name for item in [
                    *trace.finalization.main_actions, *trace.finalization.cleanup_actions,
                ] if item.event_id == event_id
            ), '进入起始页面' if event.action == 'navigate' else ''),
        )

    @staticmethod
    def _assertion(
        *,
        assertion: AssertionEvidence,
        plan: ScenarioPlan,
        trace: ExplorationTrace,
    ) -> ReplayAssertion:
        requirements = {item.assertion_id: item for item in plan.assertion_requirements}
        requirement = requirements.get(assertion.assertion_id)
        if requirement is None or any((
            assertion.criterion_index != requirement.criterion_index,
            assertion.phase != requirement.phase,
            assertion.kind != requirement.kind,
            assertion.input_ref != requirement.input_ref,
            assertion.literal != requirement.literal,
        )):
            raise GenerationContractError('assertion_evidence_plan_mismatch')
        events = {event.event_id: event for event in trace.events}
        evidence = {item.event_id: item for item in trace.locator_evidence}
        event = events.get(assertion.event_id)
        item = evidence.get(assertion.event_id)
        if not event or event.status != 'succeeded' or event.action != 'observe':
            raise GenerationContractError('assertion_event_not_callback_success')
        if item is None or item.validation in {'fragile', 'rejected'}:
            raise GenerationContractError('replay_plan_selected_assertion_locator_invalid')
        if _has_sensitive_placeholder(item.value) or _has_sensitive_placeholder(item.kwargs):
            raise GenerationContractError('replay_plan_sensitive_assertion_locator')
        refs = _template_refs(item.value) | _template_refs(item.kwargs)
        if not refs <= set(plan.input_sources()):
            raise GenerationContractError('replay_plan_assertion_template_ref_unknown')
        return ReplayAssertion(
            sequence=event.sequence,
            assertion_id=assertion.assertion_id,
            criterion_index=assertion.criterion_index,
            phase=assertion.phase,
            event_id=assertion.event_id,
            evidence_id=item.evidence_id,
            kind=assertion.kind,
            input_ref=assertion.input_ref,
            literal=assertion.literal,
            criterion=plan.success_criteria[assertion.criterion_index],
        )

    @staticmethod
    def build(plan: ScenarioPlan, trace: ExplorationTrace) -> ReplayPlan:
        plan = effective_scenario_plan(plan, trace)
        warnings = list(trace.warnings)
        actions = [
            ReplayPlanner._action(event_id=event_id, plan=plan, trace=trace)
            for event_id in trace.replay_event_ids
        ]
        cleanup_actions = [
            ReplayPlanner._action(event_id=event_id, plan=plan, trace=trace)
            for event_id in trace.cleanup_event_ids
        ]
        events = {event.event_id: event for event in trace.events}
        compiled_assertions = [
            ReplayPlanner._assertion(assertion=assertion, plan=plan, trace=trace)
            for assertion in sorted(
                trace.assertion_evidence,
                key=lambda value: events[value.event_id].sequence,
            )
        ]
        assertions = [item for item in compiled_assertions if item.phase == 'main']
        cleanup_assertions = [item for item in compiled_assertions if item.phase == 'cleanup']
        covered = {item.assertion_id for item in compiled_assertions}
        for requirement in plan.assertion_requirements:
            if requirement.assertion_id not in covered:
                warnings.append(
                    f'{requirement.assertion_id} 未被成功 callback assertion evidence 覆盖。'
                )
        if plan.cleanup_expected and not cleanup_actions:
            warnings.append('计划要求清理，但没有可编译的成功 cleanup action callback。')
        if plan.cleanup_expected and not cleanup_assertions:
            warnings.append('清理动作没有后续可编译的 cleanup verification callback。')
        main_steps = sorted(
            [*actions, *assertions], key=lambda item: item.sequence,
        )
        cleanup_steps = sorted(
            [*cleanup_actions, *cleanup_assertions], key=lambda item: item.sequence,
        )
        return ReplayPlan(
            actions=actions,
            cleanup_actions=cleanup_actions,
            assertions=assertions,
            cleanup_assertions=cleanup_assertions,
            main_steps=main_steps,
            cleanup_steps=cleanup_steps,
            assertion_event_ids=[item.event_id for item in compiled_assertions],
            input_sources=plan.input_sources(),
            input_value_kinds={item.name: item.value_kind for item in plan.input_refs},
            warnings=list(dict.fromkeys(warnings)),
        )


class PythonReplayCompiler:
    @staticmethod
    def compile(plan: ScenarioPlan, trace: ExplorationTrace, replay_plan: ReplayPlan) -> str:
        evidence = {
            item.evidence_id: item
            for item in trace.locator_evidence
        }
        required_evidence_ids = {
            *(item.evidence_id for item in replay_plan.actions),
            *(item.evidence_id for item in replay_plan.cleanup_actions),
            *(item.evidence_id for item in replay_plan.assertions),
            *(item.evidence_id for item in replay_plan.cleanup_assertions),
        }
        if not required_evidence_ids <= set(evidence):
            raise GenerationContractError('replay_plan_locator_evidence_missing')
        lines = [
            json.dumps(
                f'场景：{plan.title}\n目标：{plan.objective}\n'
                '生成方式：动作、定位器、断言和清理仅来自成功最终路径定稿的 callback。',
                ensure_ascii=False,
            ),
            '',
            'import hashlib',
            'import re',
            'import secrets',
            'import time',
            'from playwright.async_api import expect',
            '',
            'async def run(page, variables):',
            '    variables = variables or {}',
            '    resolved_values = {}',
            '    def _value_for(ref, source, value_kind):',
            '        if ref in resolved_values:',
            '            return resolved_values[ref]',
            '        supplied = variables.get(ref)',
            '        if supplied not in (None, ""):',
            '            resolved_values[ref] = str(supplied)',
            '            return resolved_values[ref]',
            '        if source in {"runtime", "credential"}:',
            '            raise RuntimeError(f"required runtime variable {ref} is missing")',
            '        scope = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:8]',
            '        token = secrets.token_hex(6)',
            '        if value_kind == "email":',
            '            resolved_values[ref] = f"aits-{scope}-{token}@example.com"',
            '        elif value_kind == "password":',
            '            resolved_values[ref] = f"Aits!{token}9"',
            '        elif value_kind == "integer":',
            '            resolved_values[ref] = str((time.time_ns() % 900000) + 100000)',
            '        else:',
            '            resolved_values[ref] = f"aits-{scope}-{token}"',
            '        return resolved_values[ref]',
            '    def _source_for(ref):',
        ]
        for ref, source in sorted(replay_plan.input_sources.items()):
            lines.extend([
                f'        if ref == {ref!r}:',
                f'            return {source!r}',
            ])
        lines.extend([
            '        raise RuntimeError(f"unknown runtime variable {ref}")',
            '    def _value_kind_for(ref):',
        ])
        for ref, value_kind in sorted(replay_plan.input_value_kinds.items()):
            lines.extend([
                f'        if ref == {ref!r}:',
                f'            return {value_kind!r}',
            ])
        lines.extend([
            '        raise RuntimeError(f"unknown runtime variable {ref}")',
            '    def _resolve_template(value):',
            '        if isinstance(value, str):',
            '            full = re.fullmatch(r"\\{\\{([A-Z_][A-Z0-9_]*)\\}\\}", value)',
            '            if full:',
            '                ref = full.group(1)',
            '                return _value_for(ref, _source_for(ref), _value_kind_for(ref))',
            '            return re.sub(',
            '                r"\\{\\{([A-Z_][A-Z0-9_]*)\\}\\}",',
            '                lambda match: _value_for(match.group(1), _source_for(match.group(1)), _value_kind_for(match.group(1))),',
            '                value,',
            '            )',
            '        if isinstance(value, dict):',
            '            return {_resolve_template(key): _resolve_template(item) for key, item in value.items()}',
            '        if isinstance(value, list):',
            '            return [_resolve_template(item) for item in value]',
            '        if isinstance(value, tuple):',
            '            return tuple(_resolve_template(item) for item in value)',
            '        return value',
        ])
        body_indent = '    '
        if replay_plan.cleanup_actions:
            lines.append('    try:')
            body_indent = '        '
        action_number = 0
        assertion_number = 0
        for step in replay_plan.main_steps:
            if isinstance(step, ReplayAction):
                action_number += 1
                lines.extend(PythonReplayCompiler._action_lines(
                    step, evidence[step.evidence_id], action_number,
                    replay_plan.input_sources, replay_plan.input_value_kinds, body_indent, label='步骤',
                ))
            else:
                assertion_number += 1
                lines.extend(PythonReplayCompiler._assertion_lines(
                    step, evidence[step.evidence_id], assertion_number,
                    replay_plan.input_sources, replay_plan.input_value_kinds, body_indent,
                ))
        if replay_plan.cleanup_actions:
            if not replay_plan.main_steps:
                lines.append(f'{body_indent}return')
            lines.append('    finally:')
            cleanup_action_number = 0
            cleanup_assertion_number = 0
            for step in replay_plan.cleanup_steps:
                if isinstance(step, ReplayAction):
                    cleanup_action_number += 1
                    lines.extend(PythonReplayCompiler._action_lines(
                        step, evidence[step.evidence_id], cleanup_action_number,
                        replay_plan.input_sources, replay_plan.input_value_kinds, '        ', label='清理',
                    ))
                else:
                    cleanup_assertion_number += 1
                    lines.extend(PythonReplayCompiler._assertion_lines(
                        step, evidence[step.evidence_id], cleanup_assertion_number,
                        replay_plan.input_sources, replay_plan.input_value_kinds, '        ', label='清理验证',
                    ))
        return '\n'.join(lines) + '\n'

    @staticmethod
    def _locator(item) -> str:
        value = f'_resolve_template({item.value!r})'
        kwargs = f', **_resolve_template({item.kwargs!r})' if item.kwargs else ''
        if item.strategy == 'testid':
            if item.kwargs:
                raise GenerationContractError('testid_locator_kwargs_not_compilable')
            return f'page.get_by_test_id({value})'
        if item.strategy == 'role':
            return f'page.get_by_role({value}{kwargs})'
        if item.strategy == 'label':
            return f'page.get_by_label({value}{kwargs})'
        if item.strategy == 'placeholder':
            return f'page.get_by_placeholder({value}{kwargs})'
        if item.strategy == 'text':
            return f'page.get_by_text({value}{kwargs})'
        if item.strategy == 'css':
            return f'page.locator({value}{kwargs})'
        raise GenerationContractError('replay_plan_locator_not_compilable')

    @staticmethod
    def _kwargs(arguments: Mapping[str, Any]) -> str:
        return f', **_resolve_template({dict(arguments)!r})' if arguments else ''

    @staticmethod
    def _action_lines(
        action: ReplayAction,
        item,
        number: int,
        input_sources: dict[str, str],
        input_value_kinds: dict[str, str],
        indent: str,
        *,
        label: str,
    ) -> list[str]:
        marker = f'[{action.event_id}]'
        lines = [
                f'{indent}# {label} {number}：{marker} {action.step_name or action.action}',
        ]
        if action.action == 'navigate':
            lines.append(f'{indent}await page.goto({action.relative_path!r})')
            return lines
        locator = PythonReplayCompiler._locator(item)
        arguments = dict(action.action_arguments)
        if action.action in {'fill', 'select'}:
            ref = action.input_refs[0]
            method = 'fill' if action.action == 'fill' else 'select_option'
            lines.append(
                f'{indent}await {locator}.{method}('
                f'_value_for({ref!r}, {input_sources[ref]!r}, {input_value_kinds[ref]!r})'
                f'{PythonReplayCompiler._kwargs(arguments)})'
            )
        elif action.action == 'press':
            key = arguments.pop('key')
            lines.append(
                f'{indent}await {locator}.press({key!r}'
                f'{PythonReplayCompiler._kwargs(arguments)})'
            )
        elif action.action in {'click', 'check', 'uncheck', 'hover'}:
            lines.append(
                f'{indent}await {locator}.{action.action}('
                f'{PythonReplayCompiler._kwargs(arguments).removeprefix(", ")})'
            )
        else:
            raise GenerationContractError('replay_plan_action_not_compilable')
        return lines

    @staticmethod
    def _assertion_lines(
        assertion: ReplayAssertion,
        item,
        number: int,
        input_sources: dict[str, str],
        input_value_kinds: dict[str, str],
        indent: str,
        *,
        label: str = '断言',
    ) -> list[str]:
        marker = f'[{assertion.assertion_id}/{assertion.event_id}]'
        locator = PythonReplayCompiler._locator(item)
        lines = [
            f'{indent}# {label} {number}：{marker} {assertion.criterion or assertion.kind}',
        ]
        if assertion.kind == 'visible':
            lines.append(f'{indent}await expect({locator}).to_be_visible()')
            return lines
        if assertion.kind in {'contains_ref', 'not_contains_ref'}:
            expected = (
                f'_value_for({assertion.input_ref!r}, '
                f'{input_sources[assertion.input_ref]!r}, '
                f'{input_value_kinds[assertion.input_ref]!r})'
            )
        else:
            expected = repr(assertion.literal)
        method = (
            'to_contain_text'
            if assertion.kind in {'contains_ref', 'contains_literal'}
            else 'not_to_contain_text'
        )
        lines.append(f'{indent}await expect({locator}).{method}({expected})')
        return lines
