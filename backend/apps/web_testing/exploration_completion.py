"""Evidence-first completion assessment for read-only MCP exploration."""

from __future__ import annotations

import re

from .generation_contracts import (
    ExplorationCompletion,
    ExplorationMissingTarget,
    ExplorationSnapshot,
    ScenarioSpec,
)


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


def assess_exploration_completion(
    scenario: ScenarioSpec,
    snapshot: ExplorationSnapshot,
    *,
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

    for step in scenario.steps:
        item = evidence.get(step.id)
        traceable = has_real_observation and _has_traceable_step_evidence(step, item, snapshot)
        if not traceable:
            if item:
                item['status'] = 'unresolved'
                item['reason'] = '缺少与目标关联的真实只读页面观测。'
            else:
                evidence[step.id] = {
                    'status': 'unresolved', 'paths': [], 'element_names': [],
                    'reason': '缺少真实只读页面观测。',
                }
            missing.append(ExplorationMissingTarget(
                target=step.target_hint,
                kind='observable',
                step_ids=[step.id],
                reason='页面目标尚未由实际工具观测确认。',
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
                reason='未验证为业务、权限或数据范围决策，继续只读探索。',
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
    if observable_missing:
        status = 'blocked' if budget_exhausted else 'needs_targeted_exploration'
    elif user_questions:
        status = 'needs_user_decision'
    else:
        status = 'complete'

    payload = snapshot.model_dump(mode='json')
    payload['step_evidence'] = evidence
    payload['unresolved_steps'] = sorted(
        step_id for step_id, item in evidence.items() if item.get('status') == 'unresolved'
    )
    if any(step.mutates_data or step.intent in {'create', 'update', 'delete'} for step in scenario.steps):
        payload['warnings'] = list(dict.fromkeys([
            *payload.get('warnings', []),
            '只读探索仅确认 CRUD 相关 UI 与定位证据；提交结果须在脚本运行期验证。',
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
