"""Deterministic v3 replay planning and Python Playwright compilation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .generation_contracts import GenerationContractError, Goal, GoalPlan
from .exploration_trace import ExplorationTrace, LocatorEvidence

_TEMPLATE_REF_RE = re.compile(r'\{\{([A-Z_][A-Z0-9_]*)\}\}')


class ReplayAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal_id: str
    event_id: str
    evidence_id: str
    action: str
    relative_path: str
    input_refs: list[str] = Field(default_factory=list)
    input_source: str = Field(default='', pattern=r'^(?:|generated|runtime|credential)$')
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    cleanup: bool = False


class ReplayPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = 3
    actions: list[ReplayAction] = Field(default_factory=list)
    assertion_event_ids: list[str] = Field(default_factory=list)
    cleanup_goal_ids: list[str] = Field(default_factory=list)
    input_sources: dict[str, str] = Field(default_factory=dict)


def _template_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_TEMPLATE_REF_RE.findall(value))
    if isinstance(value, Mapping):
        return set().union(*(_template_refs(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_template_refs(item) for item in value)) if value else set()
    return set()


def _has_sensitive_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return '<runtime_sensitive_data>' in value
    if isinstance(value, Mapping):
        return any(_has_sensitive_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_sensitive_placeholder(item) for item in value)
    return False


class ReplayPlanner:
    """Consumes only selected successful callback events, sorted by callback sequence."""
    @staticmethod
    def build(plan: GoalPlan, trace: ExplorationTrace) -> ReplayPlan:
        events = {event.event_id: event for event in trace.events}
        evidence = {item.event_id: item for item in trace.locator_evidence}
        runs = {run.goal_id: run for run in trace.goal_runs}
        input_sources = plan.input_sources()
        actions: list[ReplayAction] = []
        assertions: list[str] = []
        cleanup_goal_ids = {goal.id for goal in plan.goals if goal.kind == 'cleanup'}
        for goal in plan.goals:
            run = runs.get(goal.id)
            if run is None or run.status != 'completed':
                raise GenerationContractError(f'goal_{goal.id}_not_completed')
            assertion_ids = sorted(set(run.assertion_event_ids), key=lambda event_id: events[event_id].sequence if event_id in events else -1)
            if goal.verification and not assertion_ids:
                raise GenerationContractError('replay_plan_verification_assertion_missing')
            for event_id in assertion_ids:
                event = events.get(event_id)
                item = evidence.get(event_id)
                contract = goal.verification
                if event is None or event.goal_id != goal.id or event.status != 'succeeded' or item is None or item.strategy != 'css' or item.validation in {'fragile', 'rejected'} or contract is None or item.assertion_mode != contract.mode or item.assertion_input_ref != contract.input_ref:
                    raise GenerationContractError('replay_plan_assertion_not_callback_owned')
                if _has_sensitive_placeholder(item.value) or _has_sensitive_placeholder(item.kwargs):
                    raise GenerationContractError('replay_plan_sensitive_locator_value')
                if not _template_refs(item.value) <= set(input_sources):
                    raise GenerationContractError('replay_plan_locator_template_ref_unknown')
                assertions.append(event_id)
            for event_id in sorted(set(run.selected_event_ids), key=lambda item: events[item].sequence if item in events else -1):
                event = events.get(event_id)
                if event is None or event.goal_id != goal.id or event.status != 'succeeded':
                    raise GenerationContractError('replay_plan_event_not_selected_success')
                if event.action in {'observe', 'screenshot'}:
                    continue
                item = evidence.get(event_id)
                if item is None:
                    raise GenerationContractError('replay_plan_locator_evidence_missing')
                if item.validation in {'fragile', 'rejected'} and goal.kind != 'verify':
                    raise GenerationContractError('replay_plan_fragile_required_locator')
                if _has_sensitive_placeholder(item.value) or _has_sensitive_placeholder(item.kwargs):
                    raise GenerationContractError('replay_plan_sensitive_locator_value')
                if not _template_refs(item.value) <= set(input_sources):
                    raise GenerationContractError('replay_plan_locator_template_ref_unknown')
                if event.action in {'fill', 'select'}:
                    if len(event.input_refs) != 1 or event.input_source not in {'generated', 'runtime', 'credential'}:
                        raise GenerationContractError('replay_plan_runtime_input_unmapped')
                    if input_sources.get(event.input_refs[0]) != event.input_source:
                        raise GenerationContractError('replay_plan_input_source_mismatch')
                if event.action == 'press' and not isinstance(event.action_arguments.get('key'), str):
                    raise GenerationContractError('replay_plan_press_key_missing')
                actions.append(ReplayAction(goal_id=goal.id, event_id=event_id, evidence_id=item.evidence_id, action=event.action, relative_path=event.relative_path, input_refs=event.input_refs, input_source=event.input_source, action_arguments=event.action_arguments, cleanup=goal.id in cleanup_goal_ids))
        actions.sort(key=lambda item: events[item.event_id].sequence)
        return ReplayPlan(actions=actions, assertion_event_ids=assertions, cleanup_goal_ids=sorted(cleanup_goal_ids), input_sources=input_sources)


class PythonReplayCompiler:
    """Produces source from callback evidence only; locators retain runtime templates."""
    @staticmethod
    def compile(plan: GoalPlan, trace: ExplorationTrace, replay_plan: ReplayPlan) -> str:
        evidence = {item.evidence_id: item for item in trace.locator_evidence}
        events = {item.event_id: item for item in trace.events}
        actions_by_goal = {goal.id: [item for item in replay_plan.actions if item.goal_id == goal.id] for goal in plan.goals}
        assertions_by_goal = {goal.id: [event_id for event_id in replay_plan.assertion_event_ids if events.get(event_id) and events[event_id].goal_id == goal.id] for goal in plan.goals}
        action_numbers = {
            action.event_id: index
            for index, action in enumerate(replay_plan.actions, start=1)
        }
        assertion_numbers = {
            event_id: index
            for index, event_id in enumerate(replay_plan.assertion_event_ids, start=1)
        }
        lines = [
            json.dumps(
                f'场景：{plan.title}\n目标：{plan.objective}\n'
                '生成方式：所有业务动作和断言均来自 Playwright MCP 的成功 callback 证据。',
                ensure_ascii=False,
            ),
            '',
            'import time',
            'from playwright.async_api import expect',
            '',
            'async def run(page, variables):',
            '    variables = variables or {}',
            '    resolved_values = {}',
            '    def _value_for(ref, source):',
            '        if ref in resolved_values:',
            '            return resolved_values[ref]',
            '        supplied = variables.get(ref)',
            '        if supplied not in (None, ""):',
            '            resolved_values[ref] = str(supplied)',
            '            return resolved_values[ref]',
            '        if source in {"runtime", "credential"}:',
            '            raise RuntimeError(f"required runtime variable {ref} is missing")',
            '        resolved_values[ref] = f"aits-{ref.lower()}-{time.time_ns()}"',
            '        return resolved_values[ref]',
        ]
        primary_goals = [goal for goal in plan.goals if goal.kind != 'cleanup']
        cleanup_goals = [goal for goal in plan.goals if goal.kind == 'cleanup']
        if cleanup_goals:
            lines.append('    try:')
            for goal in primary_goals:
                lines.extend(PythonReplayCompiler._goal_block(
                    goal, actions_by_goal[goal.id], assertions_by_goal[goal.id],
                    evidence, replay_plan.input_sources, action_numbers,
                    assertion_numbers, indent='        ',
                ))
            lines.append('    finally:')
            for goal in cleanup_goals:
                lines.extend(PythonReplayCompiler._goal_block(
                    goal, actions_by_goal[goal.id], assertions_by_goal[goal.id],
                    evidence, replay_plan.input_sources, action_numbers,
                    assertion_numbers, indent='        ',
                ))
        else:
            for goal in primary_goals:
                lines.extend(PythonReplayCompiler._goal_block(
                    goal, actions_by_goal[goal.id], assertions_by_goal[goal.id],
                    evidence, replay_plan.input_sources, action_numbers,
                    assertion_numbers, indent='    ',
                ))
        return '\n'.join(lines) + '\n'

    @staticmethod
    def _comment(value: str) -> str:
        return re.sub(r'[\r\n]+', ' ', str(value)).replace('#', '＃').strip()[:500]

    @staticmethod
    def _value_expression(value: str, input_sources: Mapping[str, str]) -> str:
        parts: list[str] = []
        position = 0
        for match in _TEMPLATE_REF_RE.finditer(value):
            if match.start() > position:
                parts.append(json.dumps(value[position:match.start()], ensure_ascii=False))
            ref = match.group(1)
            source = input_sources.get(ref)
            if source not in {'generated', 'runtime', 'credential'}:
                raise GenerationContractError('replay_plan_locator_template_ref_unknown')
            parts.append(f'_value_for({json.dumps(ref)}, {json.dumps(source)})')
            position = match.end()
        if position < len(value) or not parts:
            parts.append(json.dumps(value[position:], ensure_ascii=False))
        return ' + '.join(parts)

    @staticmethod
    def _locator(item: LocatorEvidence, input_sources: Mapping[str, str]) -> str:
        value = PythonReplayCompiler._value_expression(item.value, input_sources)
        if item.strategy == 'testid':
            return f'page.get_by_test_id({value})'
        if item.strategy == 'role':
            kwargs = ', '.join(f'{key}={PythonReplayCompiler._value_expression(str(value), input_sources)}' for key, value in item.kwargs.items())
            return f'page.get_by_role({value}{", " + kwargs if kwargs else ""})'
        if item.strategy == 'label':
            return f'page.get_by_label({value})'
        if item.strategy == 'placeholder':
            return f'page.get_by_placeholder({value})'
        if item.strategy == 'text':
            exact = ', exact=True' if item.kwargs.get('exact') else ''
            return f'page.get_by_text({value}{exact})'
        if item.strategy == 'css':
            return f'page.locator({value})'
        raise GenerationContractError('unsupported_locator_strategy')

    @staticmethod
    def _goal_block(goal: Goal, actions: list[ReplayAction], assertion_ids: list[str], evidence: dict[str, LocatorEvidence], input_sources: Mapping[str, str], action_numbers: Mapping[str, int], assertion_numbers: Mapping[str, int], *, indent: str) -> list[str]:
        lines = [f'{indent}# Goal {goal.id}: {PythonReplayCompiler._comment(goal.objective)} | 完成标准：{PythonReplayCompiler._comment(goal.completion_criteria)}']
        lines.extend(PythonReplayCompiler._actions(
            actions, evidence, input_sources, action_numbers, indent=indent,
        ))
        lines.extend(PythonReplayCompiler._assertions(
            goal, assertion_ids, evidence, input_sources, assertion_numbers,
            indent=indent,
        ))
        return lines

    @staticmethod
    def _actions(actions: list[ReplayAction], evidence: dict[str, LocatorEvidence], input_sources: Mapping[str, str], action_numbers: Mapping[str, int], *, indent: str) -> list[str]:
        lines: list[str] = []
        for action in actions:
            item = evidence[action.evidence_id]
            number = action_numbers[action.event_id]
            if action.action == 'navigate':
                lines.append(
                    f'{indent}# 步骤 {number}：打开目标路径 '
                    f'{PythonReplayCompiler._comment(action.relative_path)} '
                    f'[{action.goal_id}/{action.event_id}]',
                )
                lines.append(f'{indent}await page.goto({json.dumps(action.relative_path, ensure_ascii=False)})')
                continue
            locator = PythonReplayCompiler._locator(item, input_sources)
            if action.action in {'fill', 'select'}:
                ref = action.input_refs[0]
                verb = '填写' if action.action == 'fill' else '选择'
                lines.append(
                    f'{indent}# 步骤 {number}：在已探索元素中{verb}变量 {ref} '
                    f'[{action.goal_id}/{action.event_id}]',
                )
                value = f'_value_for({json.dumps(ref)}, {json.dumps(action.input_source)})'
                method = 'fill' if action.action == 'fill' else 'select_option'
                lines.append(f'{indent}await {locator}.{method}({value})')
            elif action.action == 'press':
                key = action.action_arguments.get('key')
                if not isinstance(key, str) or not key:
                    raise GenerationContractError('replay_plan_press_key_missing')
                lines.append(
                    f'{indent}# 步骤 {number}：在已探索元素上按下 '
                    f'{PythonReplayCompiler._comment(key)} '
                    f'[{action.goal_id}/{action.event_id}]',
                )
                lines.append(f'{indent}await {locator}.press({json.dumps(key, ensure_ascii=False)})')
            elif action.action in {'click', 'check', 'uncheck', 'hover'}:
                labels = {
                    'click': '点击已探索元素', 'check': '勾选已探索元素',
                    'uncheck': '取消勾选已探索元素', 'hover': '悬停到已探索元素',
                }
                lines.append(
                    f'{indent}# 步骤 {number}：{labels[action.action]} '
                    f'[{action.goal_id}/{action.event_id}]',
                )
                lines.append(f'{indent}await {locator}.{action.action}()')
            else:
                raise GenerationContractError('unsupported_replay_action')
        return lines

    @staticmethod
    def _assertions(goal: Goal, assertion_ids: list[str], evidence: dict[str, LocatorEvidence], input_sources: Mapping[str, str], assertion_numbers: Mapping[str, int], *, indent: str) -> list[str]:
        lines: list[str] = []
        for event_id in assertion_ids:
            item = next((candidate for candidate in evidence.values() if candidate.event_id == event_id), None)
            if item is None or goal.verification is None or item.strategy != 'css' or item.assertion_mode != goal.verification.mode or item.assertion_input_ref != goal.verification.input_ref:
                raise GenerationContractError('replay_plan_assertion_not_callback_owned')
            locator = PythonReplayCompiler._locator(item, input_sources)
            number = assertion_numbers[event_id]
            if goal.verification.mode == 'visible':
                lines.append(
                    f'{indent}# 断言 {number}：确认目标区域可见 '
                    f'[{goal.id}/{event_id}]',
                )
                lines.append(f'{indent}await expect({locator}).to_be_visible()')
            elif goal.verification.mode == 'contains_ref':
                ref = goal.verification.input_ref
                lines.append(
                    f'{indent}# 断言 {number}：确认目标区域包含变量 {ref} '
                    f'[{goal.id}/{event_id}]',
                )
                lines.append(f'{indent}await expect({locator}).to_contain_text(_value_for({json.dumps(ref)}, {json.dumps(input_sources[ref])}))')
            elif goal.verification.mode == 'not_contains_ref':
                ref = goal.verification.input_ref
                lines.append(
                    f'{indent}# 断言 {number}：确认目标区域不包含变量 {ref} '
                    f'[{goal.id}/{event_id}]',
                )
                lines.append(f'{indent}await expect({locator}).not_to_contain_text(_value_for({json.dumps(ref)}, {json.dumps(input_sources[ref])}))')
            else:
                raise GenerationContractError('unsupported_verification_mode')
        return lines
