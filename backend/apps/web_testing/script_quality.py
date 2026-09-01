"""Static quality and provenance gate for deterministic v3 replay scripts."""

from __future__ import annotations

import ast
import builtins
import re
from typing import Any

from .exploration_trace import ExplorationTrace
from .generation_contracts import GoalPlan
from .generation_security import find_suspected_credentials
from .replay_plan import PythonReplayCompiler, ReplayPlan, ReplayPlanner
from .script_contract import ScriptContractError, normalize_for_storage

_ACTION_METHODS = {
    'goto', 'click', 'fill', 'select_option', 'press', 'check', 'uncheck', 'hover',
}
_BROWSER_LIFECYCLE_METHODS = {
    'launch', 'launch_persistent_context', 'new_context', 'new_page', 'close',
}
_SENSITIVE_NAME_RE = re.compile(
    r'(?i)(?:password|passwd|pwd|token|secret|api[_-]?key|authorization|credential)',
)
_ABSOLUTE_URL_RE = re.compile(r'(?i)https?://')
_STEP_COMMENT_RE = re.compile(r'^\s*#\s*步骤\s+(\d+)\s*[:：]')
_ASSERTION_COMMENT_RE = re.compile(r'^\s*#\s*断言\s+(\d+)\s*[:：]')


def _issue(level: str, code: str, message: str, line: int | None = None) -> dict[str, Any]:
    return {'level': level, 'code': code, 'message': message, 'line': line}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ''


def _is_expect_assertion(node: ast.Call) -> bool:
    return bool(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == 'expect'
        and node.func.attr.startswith(('to_', 'not_to_'))
    )


def _ordered_calls(tree: ast.AST, predicate) -> list[ast.Call]:
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Call) and predicate(node)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _action_calls(tree: ast.AST) -> list[ast.Call]:
    return _ordered_calls(tree, lambda node: _call_name(node) in _ACTION_METHODS)


def _assertion_calls(tree: ast.AST) -> list[ast.Call]:
    return _ordered_calls(tree, _is_expect_assertion)


def _call_shapes(calls: list[ast.Call]) -> list[str]:
    return [ast.dump(node, annotate_fields=True, include_attributes=False) for node in calls]


def _nearest_comment(lines: list[str], lineno: int) -> str:
    index = lineno - 2
    skipped_blank = False
    while index >= 0:
        value = lines[index].strip()
        if not value and not skipped_blank:
            skipped_blank = True
            index -= 1
            continue
        if value.startswith('#'):
            return value
        break
    return ''


def _undefined_names(tree: ast.Module, run: ast.AsyncFunctionDef) -> set[str]:
    defined = set(dir(builtins)) | {'page', 'variables'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            defined.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            defined.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if hasattr(node, 'name'):
                defined.add(node.name)
            defined.update(
                item.arg for item in [
                    *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                ]
            )
            if node.args.vararg:
                defined.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined.add(node.args.kwarg.arg)
    return {
        node.id for node in ast.walk(run)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in defined
    }


def _plain_nonempty_string(node: ast.AST | None) -> bool:
    return bool(isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value)


def _has_sensitive_literal_assignment(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if _plain_nonempty_string(node.value) and any(
                isinstance(target, ast.Name) and _SENSITIVE_NAME_RE.search(target.id)
                for target in node.targets
            ):
                return True
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            if (
                isinstance(node.target, ast.Name)
                and _SENSITIVE_NAME_RE.search(node.target.id)
                and _plain_nonempty_string(node.value)
            ):
                return True
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and _SENSITIVE_NAME_RE.search(key.value)
                    and _plain_nonempty_string(value)
                ):
                    return True
        elif isinstance(node, ast.Call):
            if any(
                keyword.arg and _SENSITIVE_NAME_RE.search(keyword.arg)
                and _plain_nonempty_string(keyword.value)
                for keyword in node.keywords
            ):
                return True
    return False


def _has_unresolved_placeholder(tree: ast.AST, lines: list[str]) -> bool:
    if any(isinstance(node, ast.Pass) for node in ast.walk(tree)):
        return True
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == 'NotImplementedError'
        ):
            return True
    return any(re.search(r'(?i)\bTODO\b|待补充|待确认|占位', line) for line in lines)


def _finally_action_shapes(run: ast.AsyncFunctionDef) -> list[str]:
    calls: list[ast.Call] = []
    for node in ast.walk(run):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for statement in node.finalbody:
            calls.extend(_action_calls(statement))
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return _call_shapes(calls)


def _append_once(target: list[dict[str, Any]], issue: dict[str, Any]) -> None:
    identity = (issue['level'], issue['code'], issue.get('line'))
    if not any((item['level'], item['code'], item.get('line')) == identity for item in target):
        target.append(issue)


def evaluate_script(source: str, *, plan: GoalPlan, trace: ExplorationTrace, replay_plan: ReplayPlan | None = None) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source = str(source or '')
    try:
        tree = ast.parse(source, filename='generated_webui_script.py')
    except SyntaxError as exc:
        blockers.append(_issue('blocker', 'SYNTAX_ERROR', 'Python 语法无效。', exc.lineno))
        return _report(blockers, warnings)

    try:
        normalize_for_storage(source)
    except ScriptContractError:
        blockers.append(_issue('blocker', 'SCRIPT_CONTRACT_INVALID', '脚本不符合统一存储契约。'))

    run_nodes = [
        item for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == 'run'
    ]
    if (
        len(run_nodes) != 1
        or [arg.arg for arg in run_nodes[0].args.args] != ['page', 'variables']
        or run_nodes[0].args.posonlyargs
        or run_nodes[0].args.defaults
        or run_nodes[0].args.kwonlyargs
        or run_nodes[0].args.vararg
        or run_nodes[0].args.kwarg
    ):
        blockers.append(_issue('blocker', 'RUN_SIGNATURE_INVALID', 'v3 脚本必须严格定义 async def run(page, variables)。'))
        return _report(blockers, warnings)
    run = run_nodes[0]
    lines = source.splitlines()
    module_docstring = ast.get_docstring(tree) or ''
    if '场景：' not in module_docstring or '目标：' not in module_docstring:
        blockers.append(_issue('blocker', 'DOCSTRING_MISSING', '文件顶部必须包含“场景”和“目标”说明。'))
    if _ABSOLUTE_URL_RE.search(source):
        blockers.append(_issue('blocker', 'ABSOLUTE_URL_FORBIDDEN', '脚本只能使用相对路径，不能包含完整 URL。'))
    if _has_sensitive_literal_assignment(tree) or any(
        find_suspected_credentials(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and ('=' in node.value or ':' in node.value)
    ):
        blockers.append(_issue('blocker', 'SENSITIVE_LITERAL', '脚本不能包含明文账号、密码、Token 或密钥。'))
    if _has_unresolved_placeholder(tree, lines):
        blockers.append(_issue('blocker', 'UNRESOLVED_PLACEHOLDER', '脚本包含 TODO、pass 或未实现占位。'))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _BROWSER_LIFECYCLE_METHODS:
                _append_once(blockers, _issue('blocker', 'BROWSER_LIFECYCLE_FORBIDDEN', '脚本不能自行创建或关闭浏览器、上下文或页面。', node.lineno))
            if name == 'wait_for_timeout' or (
                name == 'sleep' and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {'time', 'asyncio'}
            ):
                _append_once(blockers, _issue('blocker', 'FIXED_WAIT_FORBIDDEN', '脚本不能使用固定等待，应依赖页面状态或 expect。', node.lineno))
        if isinstance(node, ast.Name) and node.id in {'sync_playwright', 'async_playwright'}:
            _append_once(blockers, _issue('blocker', 'PLAYWRIGHT_LIFECYCLE_FORBIDDEN', '脚本由统一执行器管理 Playwright 生命周期。', getattr(node, 'lineno', None)))

    undefined = sorted(_undefined_names(tree, run))
    if undefined:
        blockers.append(_issue('blocker', 'UNDEFINED_NAME', f'脚本引用了未定义名称：{", ".join(undefined[:5])}。'))

    try:
        plan_value = replay_plan or ReplayPlanner.build(plan, trace)
        expected_source = PythonReplayCompiler.compile(plan, trace, plan_value)
        expected_tree = ast.parse(expected_source)
    except Exception:
        blockers.append(_issue('blocker', 'REPLAY_PLAN_INVALID', '选中事件无法构建可回放计划。'))
        return _report(blockers, warnings)

    action_calls = _action_calls(tree)
    assertion_calls = _assertion_calls(tree)
    expected_action_calls = _action_calls(expected_tree)
    expected_assertion_calls = _assertion_calls(expected_tree)
    if _call_shapes(action_calls) != _call_shapes(expected_action_calls):
        blockers.append(_issue('blocker', 'ACTION_NOT_FROM_REPLAY_PLAN', '脚本动作或定位器与确定性 ReplayPlan 不一致。'))
    if _call_shapes(assertion_calls) != _call_shapes(expected_assertion_calls):
        blockers.append(_issue('blocker', 'ASSERTION_NOT_FROM_REPLAY_PLAN', '脚本断言与 callback verification 证据不一致。'))
    if not assertion_calls:
        blockers.append(_issue('blocker', 'EXPECT_MISSING', '脚本至少需要一个由探索证据支持的 expect 断言。'))

    events = {item.event_id: item for item in trace.events}
    action_refs = [(item.goal_id, item.event_id) for item in plan_value.actions]
    assertion_refs = [
        (events[event_id].goal_id, event_id)
        for event_id in plan_value.assertion_event_ids
        if event_id in events
    ]
    missing_action_refs = [
        event_id for goal_id, event_id in action_refs
        if source.count(f'[{goal_id}/{event_id}]') != 1
    ]
    if missing_action_refs:
        blockers.append(_issue('blocker', 'ACTION_EVIDENCE_REFERENCE_MISSING', f'缺少或重复动作证据引用：{", ".join(missing_action_refs)}。'))
    missing_assertion_refs = [
        event_id for goal_id, event_id in assertion_refs
        if source.count(f'[{goal_id}/{event_id}]') != 1
    ]
    if missing_assertion_refs:
        blockers.append(_issue('blocker', 'ASSERTION_EVIDENCE_REFERENCE_MISSING', f'缺少或重复断言证据引用：{", ".join(missing_assertion_refs)}。'))

    step_numbers = [int(match.group(1)) for line in lines if (match := _STEP_COMMENT_RE.match(line))]
    if step_numbers != list(range(1, len(plan_value.actions) + 1)):
        blockers.append(_issue('blocker', 'ACTION_COMMENT_MISSING', '每个回放动作必须按顺序提供“步骤 N：...”中文备注。'))
    assertion_numbers = [int(match.group(1)) for line in lines if (match := _ASSERTION_COMMENT_RE.match(line))]
    if assertion_numbers != list(range(1, len(plan_value.assertion_event_ids) + 1)):
        blockers.append(_issue('blocker', 'ASSERTION_COMMENT_MISSING', '每个断言必须按顺序提供“断言 N：...”中文备注。'))
    for call, (goal_id, event_id) in zip(action_calls, action_refs):
        comment = _nearest_comment(lines, call.lineno)
        if not _STEP_COMMENT_RE.match(comment) or f'[{goal_id}/{event_id}]' not in comment:
            _append_once(blockers, _issue('blocker', 'ACTION_COMMENT_MISSING', '业务动作缺少可读步骤备注和证据引用。', call.lineno))
    for call, (goal_id, event_id) in zip(assertion_calls, assertion_refs):
        comment = _nearest_comment(lines, call.lineno)
        if not _ASSERTION_COMMENT_RE.match(comment) or f'[{goal_id}/{event_id}]' not in comment:
            _append_once(blockers, _issue('blocker', 'ASSERTION_COMMENT_MISSING', '断言缺少可读备注和证据引用。', call.lineno))

    evidence_by_event = {item.event_id: item for item in trace.locator_evidence}
    for action in plan_value.actions:
        evidence = evidence_by_event.get(action.event_id)
        if evidence is None:
            blockers.append(_issue('blocker', 'LOCATOR_EVIDENCE_MISSING', f'{action.event_id} 没有 LocatorEvidence。'))
        elif evidence.validation in {'fragile', 'rejected'}:
            blockers.append(_issue('blocker', 'LOCATOR_EVIDENCE_FRAGILE', f'{action.event_id} 的定位器不够稳定。'))
        elif evidence.validation == 'acceptable':
            warnings.append(_issue('warning', 'LOCATOR_COUNT_UNVERIFIED', f'{action.event_id} 已由真实动作验证，但 MCP 无法独立证明定位器唯一。'))
    for event_id in plan_value.assertion_event_ids:
        evidence = evidence_by_event.get(event_id)
        if evidence is None or evidence.validation in {'fragile', 'rejected'}:
            blockers.append(_issue('blocker', 'ASSERTION_EVIDENCE_MISSING', f'{event_id} 缺少可编译的断言定位证据。'))

    cleanup_actions = [item for item in plan_value.actions if item.cleanup]
    if cleanup_actions:
        expected_run = next(
            item for item in expected_tree.body
            if isinstance(item, ast.AsyncFunctionDef) and item.name == 'run'
        )
        if _finally_action_shapes(run) != _finally_action_shapes(expected_run):
            blockers.append(_issue('blocker', 'CLEANUP_FINALLY_MISSING', 'cleanup Goal 的全部动作必须位于 try/finally 的 finally 中。'))
    return _report(blockers, warnings)


def _report(blockers: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for item in [*blockers, *warnings]:
        _append_once(checks, item)
    blockers = [item for item in checks if item['level'] == 'blocker']
    warnings = [item for item in checks if item['level'] == 'warning']
    status = 'needs_review' if blockers else ('ready_with_warnings' if warnings else 'ready')
    return {
        'status': status,
        'blockers': blockers,
        'warnings': warnings,
        'checks': checks,
        'summary': {
            'passed': 0 if blockers else 1,
            'warning': len(warnings),
            'blocker': len(blockers),
            'message': '脚本需要人工检查。' if blockers else '脚本已通过静态质量检查。',
        },
    }


def blocker_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get('blockers') or [])


def has_missing_evidence(report: dict[str, Any]) -> bool:
    return any(item.get('code', '').endswith('EVIDENCE_MISSING') for item in report.get('blockers') or [])
