"""Evidence-first completion assessment for target-driven MCP exploration."""

from __future__ import annotations

import re

from .generation_contracts import (
    ExplorationCompletion,
    ExplorationMissingTarget,
    ExplorationSnapshot,
    ScenarioSpec,
)
from .exploration_policy import CRUD_OPERATIONS, ExplorationPolicy


USER_DECISION_KINDS = frozenset({'business_decision', 'permission_scope', 'data_scope'})
_TECHNICAL_QUESTION_MARKERS = (
    'dom', 'locator', 'selector', 'xpath', 'css', '按钮', '菜单', '字段', 'input',
    'html', '元素', '定位', '入口', 'page.', 'get_by_',
)


def _normalize(value: str) -> str:
    return re.sub(r'\s+', '', str(value or '')).lower()


def _matches_target(target: str, observed: set[str]) -> bool:
    needle = _normalize(target)
    if not needle:
        return False
    return any(needle in item or item in needle for item in observed if item)


def _has_traceable_step_evidence(step, item: dict | None, snapshot: ExplorationSnapshot) -> bool:
    """Require a target, path, page state, element and locator to agree."""
    if not item or item.get('status') == 'unresolved':
        return False
    evidence_paths = {_normalize(path) for path in item.get('paths') or []}
    visited_paths = {_normalize(path) for path in snapshot.visited_paths}
    state_paths = {_normalize(page.path) for page in snapshot.page_states}
    traceable_paths = evidence_paths & visited_paths & state_paths
    if not traceable_paths:
        return False
    target = _normalize(step.target_hint)
    observed_pages = [
        page for page in snapshot.page_states
        if _normalize(page.path) in traceable_paths
    ]
    target_page_states = [
        page for page in observed_pages
        if _matches_target(target, {
            _normalize(page.name), *(_normalize(region) for region in page.key_regions),
        })
    ]
    # A sidebar/menu item on the landing page is an observable navigation aid,
    # not proof that the target page was reached.
    if step.intent == 'navigate' and not target_page_states:
        return False
    evidence_names = {_normalize(name) for name in item.get('element_names') or []}
    relevant_pages = target_page_states if step.intent == 'navigate' else observed_pages
    # A navigation step is evidenced by the destination page and its controls;
    # those controls need not repeat the page name. For an action, the target
    # may instead be a button/field on a differently named page.
    return any(
        _normalize(element.visible_name) in evidence_names
        and bool(element.candidate_locators)
        and any(
            _normalize(element.page_name) == _normalize(page.name)
            for page in relevant_pages
        )
        and (
            bool(target_page_states)
            or _matches_target(target, {_normalize(element.visible_name)})
        )
        for element in snapshot.elements
    )


def _discovery_target_covered(target: str, snapshot: ExplorationSnapshot) -> bool:
    """Track discovery target coverage separately from step completion.

    A menu entry may cover an "入口" discovery target, but it cannot satisfy
    ``_has_traceable_step_evidence`` above because no target page state exists.
    """
    visited = {_normalize(path) for path in snapshot.visited_paths}
    matching_pages = [
        page for page in snapshot.page_states
        if _normalize(page.path) in visited
        and _matches_target(target, {
            _normalize(page.name), *(_normalize(region) for region in page.key_regions),
        })
    ]
    if matching_pages:
        return True
    return any(
        _matches_target(target, {_normalize(element.visible_name)})
        and bool(element.candidate_locators)
        and any(_normalize(element.page_name) == _normalize(page.name) for page in snapshot.page_states)
        for element in snapshot.elements
    )


def _is_allowed_user_decision(item: ExplorationMissingTarget, scenario: ScenarioSpec) -> bool:
    if item.kind not in USER_DECISION_KINDS:
        return False
    question = item.user_question or item.target
    normalized = _normalize(question)
    if not normalized or any(marker in normalized for marker in _TECHNICAL_QUESTION_MARKERS):
        return False
    if item.kind == 'business_decision':
        return any(_normalize(question) == _normalize(value) for value in scenario.ambiguities)
    return True


def _observed_action_for_step(step, snapshot: ExplorationSnapshot) -> bool:
    return any(
        action.step_id == step.id
        and action.status == 'observed'
        and (step.intent not in {'create', 'update', 'delete'} or action.operation == step.intent)
        for action in snapshot.exploration_actions
    )


def _unknown_action_for_step(step, snapshot: ExplorationSnapshot) -> bool:
    return any(
        action.step_id == step.id
        and action.status == 'unknown'
        and (step.intent not in {'create', 'update', 'delete'} or action.operation == step.intent)
        for action in snapshot.exploration_actions
    )


def _effective_allowed_operations(
    snapshot: ExplorationSnapshot,
    policy: ExplorationPolicy | None,
) -> frozenset[str]:
    if policy is not None:
        return policy.allowed_operations
    if snapshot.exploration_policy_applied:
        return frozenset(snapshot.exploration_allowed_operations)
    # Older snapshots predate policy metadata. Preserve their historical gate.
    return CRUD_OPERATIONS


def assess_exploration_completion(
    scenario: ScenarioSpec,
    snapshot: ExplorationSnapshot,
    *,
    policy: ExplorationPolicy | None = None,
    targeted_rounds: int | None = None,
    budget_exhausted: bool = False,
    supplement_round_limit_reached: bool = False,
) -> ExplorationSnapshot:
    """Derive completion from real tool observations instead of model assertions.

    ``step_evidence.status=confirmed`` alone is not enough: at least one actual
    browser call and a target-aligned observable are required before generation.
    """
    if targeted_rounds is None:
        targeted_rounds = snapshot.completion.targeted_rounds
    # Older persisted snapshots predate checkpoints. Their non-zero tool count
    # remains compatible; newly collected snapshots additionally carry a
    # checkpoint for every completed operation.
    has_real_observation = snapshot.tool_stats.total_tool_calls > snapshot.tool_stats.failed_tool_calls
    evidence = snapshot.model_dump(mode='json')['step_evidence']
    missing: list[ExplorationMissingTarget] = []
    submission_unknown = False
    allowed_operations = _effective_allowed_operations(snapshot, policy)
    has_mutating_step = any(
        step.mutates_data or step.intent in {'create', 'update', 'delete'}
        for step in scenario.steps
    )

    for step in scenario.steps:
        item = evidence.get(step.id)
        traceable = has_real_observation and _has_traceable_step_evidence(step, item, snapshot)
        if not traceable:
            if item:
                item['status'] = 'unresolved'
                item['reason'] = '缺少与目标关联的真实页面观测。'
            else:
                evidence[step.id] = {
                    'status': 'unresolved', 'paths': [], 'element_names': [],
                    'reason': '缺少真实页面观测。',
                }
            missing.append(ExplorationMissingTarget(
                target=step.target_hint,
                kind='observable',
                step_ids=[step.id],
                reason='页面目标尚未由实际工具观测确认。',
            ))
        operation = step.intent if step.intent in CRUD_OPERATIONS else None
        if operation in allowed_operations:
            if _unknown_action_for_step(step, snapshot):
                submission_unknown = True
                missing.append(ExplorationMissingTarget(
                    target=step.target_hint,
                    kind='observable',
                    step_ids=[step.id],
                    reason='提交结果未知；为避免重复写入，本轮不会重试该操作。',
                ))
            elif not _observed_action_for_step(step, snapshot):
                missing.append(ExplorationMissingTarget(
                    target=step.target_hint,
                    kind='observable',
                    step_ids=[step.id],
                    reason='CRUD 目标尚未获得提交后的可观察结果。',
                ))

    for target in scenario.discovery_targets:
        if not _discovery_target_covered(target, snapshot):
            missing.append(ExplorationMissingTarget(
                target=target,
                kind='observable',
                reason='discovery target 尚无可追溯页面观察。',
            ))

    # New structured targets may request a human decision. Observable targets
    # always win so login-only or incomplete navigation continues in-browser.
    for item in snapshot.completion.missing_targets:
        if item.kind == 'observable':
            missing.append(item)
        elif _is_allowed_user_decision(item, scenario):
            missing.append(item)
        else:
            missing.append(ExplorationMissingTarget(
                target=item.target,
                kind='observable',
                step_ids=item.step_ids,
                reason='未验证为业务、权限或数据范围决策，继续页面探索。',
            ))

    structured_questions = [
        item.user_question or item.target
        for item in missing if item.kind in USER_DECISION_KINDS
    ]
    # Legacy snapshots only have strings. Treat them as human decisions solely
    # when they match a normalizer-provided ambiguity; never infer this from
    # interrogative wording.
    legacy_questions = [
        question for question in snapshot.unresolved_questions
        if question in scenario.ambiguities
        and not any(marker in _normalize(question) for marker in _TECHNICAL_QUESTION_MARKERS)
    ]
    user_questions = list(dict.fromkeys([*structured_questions, *legacy_questions]))
    observable_missing = [item for item in missing if item.kind == 'observable']
    observed_actions = any(action.status == 'observed' for action in snapshot.exploration_actions)
    cleanup_status = snapshot.cleanup_report.status
    cleanup_missing = observed_actions and cleanup_status in {'not_required', 'not_attempted'}
    cleanup_unknown = cleanup_status == 'unknown'
    cleanup_pending = (
        observed_actions and cleanup_missing
    )
    if cleanup_pending:
        missing.append(ExplorationMissingTarget(
            target='本轮测试数据清理',
            kind='observable',
            reason='CRUD 操作已完成，但尚未获得清理尝试结果。',
        ))
        observable_missing = [item for item in missing if item.kind == 'observable']
    if observable_missing or cleanup_unknown:
        status = 'blocked' if budget_exhausted or submission_unknown or cleanup_unknown else 'needs_targeted_exploration'
    elif user_questions:
        status = 'needs_user_decision'
    else:
        status = 'complete'

    payload = snapshot.model_dump(mode='json')
    payload['step_evidence'] = evidence
    payload['unresolved_steps'] = sorted(
        step_id for step_id, item in evidence.items() if item.get('status') == 'unresolved'
    )
    if cleanup_missing:
        payload['cleanup_report'] = {
            'status': 'not_attempted',
            'attempted': False,
            'reason': '已观察到本轮数据操作，但模型未报告清理结果。',
        }
    if has_mutating_step and not allowed_operations:
        payload['warnings'] = list(dict.fromkeys([
            *payload.get('warnings', []),
            '本轮探索策略未允许 CRUD 提交，仅确认 UI 与定位证据，未将其标记为提交验证。',
        ]))
    elif has_mutating_step:
        payload['warnings'] = list(dict.fromkeys([
            *payload.get('warnings', []),
            'CRUD 探索只允许当前 scope 内的数据；exploration_actions 记录提交后的实际观察结果。',
        ]))
    if snapshot.cleanup_report.status == 'residual':
        payload['warnings'] = list(dict.fromkeys([
            *payload.get('warnings', []),
            *[f'本轮清理残留：{item}' for item in snapshot.cleanup_report.residuals],
        ]))
    elif cleanup_unknown:
        payload['warnings'] = list(dict.fromkeys([
            *payload.get('warnings', []),
            '本轮 cleanup 状态未知；不得将其视为已完成。',
        ]))
    deduplicated_missing = []
    seen_missing: set[tuple] = set()
    for item in missing:
        key = (item.target, item.kind, tuple(item.step_ids), item.reason, item.user_question)
        if key not in seen_missing:
            seen_missing.add(key)
            deduplicated_missing.append(item)
    payload['completion'] = ExplorationCompletion(
        status=status,
        missing_targets=deduplicated_missing,
        user_questions=user_questions,
        targeted_rounds=targeted_rounds,
        budget_exhausted=budget_exhausted,
        supplement_round_limit_reached=supplement_round_limit_reached,
    ).model_dump(mode='json')
    return ExplorationSnapshot.model_validate(payload)


def can_request_user_decision(snapshot: ExplorationSnapshot) -> bool:
    return (
        snapshot.completion.status == 'needs_user_decision'
        and bool(snapshot.completion.user_questions)
        and not any(item.kind == 'observable' for item in snapshot.completion.missing_targets)
    )
