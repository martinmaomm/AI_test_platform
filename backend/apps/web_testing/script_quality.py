"""Deterministic static quality gate for generated Python Playwright scripts."""

from __future__ import annotations

import ast
import builtins
import io
import re
import tokenize
from typing import Any

from .generation_contracts import ExplorationSnapshot, ScenarioSpec
from .generation_security import SENSITIVE_KEY_RE, find_suspected_credentials, redact_metadata
from .script_contract import ScriptContractError, normalize_for_storage


ACTION_NAMES = {
    'goto', 'click', 'dblclick', 'fill', 'type', 'check', 'uncheck',
    'select_option', 'press', 'hover', 'focus', 'clear', 'tap', 'drag_to',
}
MUTATION_WORDS = ('create', 'update', 'delete', '新增', '编辑', '删除', '保存', '提交')
SENSITIVE_LITERAL_RE = re.compile(r'(?i)(password|passwd|token|secret|api[_ -]?key)\s*[:=]\s*[\'\"]')
ABSOLUTE_URL_RE = re.compile(r'(?i)https?://')


def _issue(level: str, code: str, message: str, *, line: int | None = None) -> dict[str, Any]:
    return {'level': level, 'code': code, 'message': message, 'line': line}


def _comment_lines(source: str) -> dict[int, str]:
    comments: dict[int, str] = {}
    try:
        for item in tokenize.generate_tokens(io.StringIO(source).readline):
            if item.type == tokenize.COMMENT:
                comments[item.start[0]] = item.string.lstrip('#').strip()
    except tokenize.TokenError:
        pass
    return comments


def _nearest_comment(comments: dict[int, str], line: int) -> str:
    for current in range(line - 1, max(0, line - 5), -1):
        if current in comments:
            return comments[current]
    return ''


def _consume_comment(
    comments: dict[int, str],
    used_lines: set[int],
    line: int,
    prefix: str,
) -> bool:
    """Consume one nearby matching comment so it cannot cover two calls."""
    for current in range(line - 1, max(0, line - 5), -1):
        if current in used_lines:
            continue
        comment = comments.get(current, '')
        if re.match(prefix, comment):
            used_lines.add(current)
            return True
    return False


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else ''


def _is_expect_assertion(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == 'expect'
        and node.func.attr.startswith('to_')
    )


def _undefined_names(tree: ast.Module, run_node: ast.AsyncFunctionDef) -> set[str]:
    """Conservative local-name check for generated business scripts."""
    defined = set(dir(builtins)) | {'page', 'expect'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            defined.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            defined.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            defined.update(arg.arg for arg in [*node.args.args, *node.args.posonlyargs, *node.args.kwonlyargs])
    return {
        node.id for node in ast.walk(run_node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined
    }


def _has_finally(run_node: ast.AsyncFunctionDef) -> bool:
    return any(isinstance(item, ast.Try) and bool(item.finalbody) for item in ast.walk(run_node))


def _script_has_mutation(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(word in node.value.lower() for word in MUTATION_WORDS):
                return True
    return False


def _is_sensitive_name(value: str | None) -> bool:
    return bool(value and SENSITIVE_KEY_RE.search(value))


def _is_plain_string(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _has_sensitive_literal_assignment(tree: ast.AST) -> bool:
    """Reject literal values bound to sensitive names while allowing os.environ."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _is_plain_string(value) and any(
                isinstance(target, ast.Name) and _is_sensitive_name(target.id)
                for target in targets
            ):
                return True
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _is_plain_string(key) and _is_sensitive_name(str(key.value)) and _is_plain_string(value):
                    return True
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if _is_sensitive_name(keyword.arg) and _is_plain_string(keyword.value):
                    return True
    return False


def _has_unresolved_placeholder(tree: ast.AST, comments: dict[int, str]) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Pass):
            return True
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'NotImplementedError':
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r'(?i)\bTODO\b|待补充|待确认|占位', node.value):
                return True
    return any(re.search(r'(?i)\bTODO\b|待补充|待确认|占位', value) for value in comments.values())


def _exact_text_locator(node: ast.Call) -> bool:
    return any(
        keyword.arg == 'exact'
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def _core_pass_checks(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocker_codes = {item['code'] for item in blockers}
    checks = [
        ('SYNTAX_VALID', '语法检查通过。', {'SYNTAX_ERROR'}),
        ('SCRIPT_CONTRACT_VALID', '脚本入口和严格存储契约通过。', {'SCRIPT_CONTRACT_INVALID', 'RUN_SIGNATURE_INVALID'}),
        ('DOCSTRING_VALID', '场景说明完整且已脱敏。', {'DOCSTRING_MISSING', 'SENSITIVE_LITERAL', 'ABSOLUTE_URL_FORBIDDEN'}),
        ('RELATIVE_URL_VALID', '脚本只使用相对路径。', {'ABSOLUTE_URL_FORBIDDEN'}),
        ('SENSITIVE_INFORMATION_VALID', '未发现明文敏感信息。', {'SENSITIVE_LITERAL'}),
        ('EXPECT_VALID', '包含 Playwright expect 断言。', {'EXPECT_MISSING'}),
        ('ACTION_COMMENT_VALID', '业务动作备注覆盖完整。', {'ACTION_COMMENT_MISSING', 'ACTION_COMMENT_COVERAGE_ZERO'}),
        ('ASSERTION_COMMENT_VALID', '断言备注覆盖完整。', {'ASSERTION_COMMENT_MISSING'}),
        ('CLEANUP_VALID', '清理策略满足当前场景要求。', {'CLEANUP_FINALLY_MISSING', 'CLEANUP_COMMENT_MISSING'}),
        ('NAMES_IMPORTS_VALID', '名称和导入检查通过。', {'UNDEFINED_NAME', 'IMPORT_MISSING'}),
    ]
    return [
        _issue('pass', code, message)
        for code, message, failed_by in checks
        if not (blocker_codes & failed_by)
    ]


def evaluate_script(
    script: str,
    *,
    scenario: ScenarioSpec,
    snapshot: ExplorationSnapshot,
) -> dict[str, Any]:
    """Return only safe, structured and user-readable static findings."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source = str(script or '')
    try:
        tree = ast.parse(source, filename='generated_webui_script.py')
    except SyntaxError as exc:
        blockers.append(_issue('blocker', 'SYNTAX_ERROR', '脚本语法错误，无法执行。', line=exc.lineno))
        return _report(blockers, warnings, passes=_core_pass_checks(blockers))

    # The quality gate and the final save operation must enforce the same
    # storage contract.  Keep the user-facing finding intentionally generic:
    # contract exceptions may include script fragments that must not leak into
    # an API response or persisted report.
    try:
        normalize_for_storage(source)
    except ScriptContractError:
        blockers.append(_issue(
            'blocker',
            'SCRIPT_CONTRACT_INVALID',
            '脚本不符合统一存储契约，不能保存或执行。',
        ))

    run_nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run']
    if len(run_nodes) != 1 or len(run_nodes[0].args.args) != 1 or run_nodes[0].args.args[0].arg != 'page':
        blockers.append(_issue('blocker', 'RUN_SIGNATURE_INVALID', '脚本必须仅提供 async def run(page) 入口。'))
        return _report(blockers, warnings, passes=_core_pass_checks(blockers))
    run_node = run_nodes[0]
    comments = _comment_lines(source)
    module_docstring = ast.get_docstring(tree) or ''
    if not all(label in module_docstring for label in ('场景', '目标', '前置条件', '清理策略')):
        blockers.append(_issue('blocker', 'DOCSTRING_MISSING', '文件顶部必须有包含场景、目标、前置条件和清理策略的已脱敏说明。'))
    imports = {alias.name.split('.')[0] for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in getattr(node, 'names', [])}
    expect_count = 0
    action_count = 0
    used_action_comments: set[int] = set()
    used_assertion_comments: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in {'launch', 'new_context', 'new_page', 'close', 'launch_persistent_context'}:
                blockers.append(_issue('blocker', 'BROWSER_LIFECYCLE_FORBIDDEN', '脚本不能自行创建或关闭浏览器、上下文或页面。', line=node.lineno))
            if name in ACTION_NAMES:
                action_count += 1
                if not _consume_comment(comments, used_action_comments, node.lineno, r'步骤\s*\d+\s*[:：]'):
                    blockers.append(_issue('blocker', 'ACTION_COMMENT_MISSING', '每个业务动作前必须有“步骤 N：中文名称”注释。', line=node.lineno))
            if name == 'wait_for_timeout':
                warnings.append(_issue('warning', 'FIXED_WAIT', '脚本包含固定等待，建议替换为可观察的页面断言。', line=node.lineno))
            if name == 'get_by_text' and not _exact_text_locator(node):
                warnings.append(_issue('warning', 'AMBIGUOUS_TEXT_LOCATOR', '文本定位器可能匹配多个元素，建议结合 role 或稳定属性。', line=node.lineno))
            if _is_expect_assertion(node):
                expect_count += 1
                if not _consume_comment(comments, used_assertion_comments, node.lineno, r'断言\s*\d+\s*[:：]'):
                    blockers.append(_issue('blocker', 'ASSERTION_COMMENT_MISSING', '每个 expect 前必须有“断言 N：中文名称”注释。', line=node.lineno))
                if name in {'to_be_visible', 'to_be_enabled'}:
                    warnings.append(_issue('warning', 'WEAK_ASSERTION', '仅验证可见或可用较弱，建议同时验证业务结果。', line=node.lineno))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'time' and node.attr == 'sleep':
            blockers.append(_issue('blocker', 'TIME_SLEEP_FORBIDDEN', '异步 Playwright 脚本不能使用 time.sleep。', line=node.lineno))
        if isinstance(node, ast.Name) and node.id == 'sync_playwright':
            blockers.append(_issue('blocker', 'SYNC_PLAYWRIGHT_FORBIDDEN', '脚本必须使用异步 Playwright，不能使用 sync_playwright。', line=node.lineno))
        if isinstance(node, ast.Attribute) and node.attr == 'nth':
            warnings.append(_issue('warning', 'FRAGILE_LOCATOR', '定位器依赖序号 nth，页面结构变化时容易失效。', line=node.lineno))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if ABSOLUTE_URL_RE.search(value):
                blockers.append(_issue('blocker', 'ABSOLUTE_URL_FORBIDDEN', '脚本只能使用相对路径，不能写完整 URL。', line=node.lineno))
            if find_suspected_credentials(value) or SENSITIVE_LITERAL_RE.search(value):
                blockers.append(_issue('blocker', 'SENSITIVE_LITERAL', '脚本不能包含明文账号、密码或密钥。', line=node.lineno))
            if 'xpath=' in value.lower() or value.strip().startswith('//'):
                warnings.append(_issue('warning', 'FRAGILE_XPATH', '定位器使用 XPath，页面变动时容易失效。', line=node.lineno))
            if re.search(r'\bnth\s*\(', value, re.I) or re.search(r'\s>\s.*\s>', value):
                warnings.append(_issue('warning', 'FRAGILE_LOCATOR', '定位器过于依赖层级或序号，建议使用 role 或稳定属性。', line=node.lineno))

    if not expect_count:
        blockers.append(_issue('blocker', 'EXPECT_MISSING', '脚本至少需要一个 expect 断言。'))
    if _has_sensitive_literal_assignment(tree):
        blockers.append(_issue('blocker', 'SENSITIVE_LITERAL', '脚本不能包含明文账号、密码或密钥。'))
    if _has_unresolved_placeholder(tree, comments):
        blockers.append(_issue('blocker', 'UNRESOLVED_PLACEHOLDER', '脚本包含 TODO、pass 或未实现占位，不能作为可保存脚本。'))
    scenario_mutates = any(step.mutates_data or step.intent in {'create', 'update', 'delete'} for step in scenario.steps)
    if (_script_has_mutation(tree) or scenario_mutates) and not _has_finally(run_node):
        blockers.append(_issue('blocker', 'CLEANUP_FINALLY_MISSING', '包含写操作的脚本必须使用 finally 清理本轮新增数据。'))
    if scenario_mutates and not any(
        comment.startswith('清理：') or comment.startswith('清理:') for comment in comments.values()
    ):
        blockers.append(_issue('blocker', 'CLEANUP_COMMENT_MISSING', '写操作脚本必须提供“清理：...”注释。'))
    if 'os.environ' in source and 'os' not in imports:
        blockers.append(_issue('blocker', 'IMPORT_MISSING', '脚本读取环境变量时必须导入 os。'))
    if 'time.' in source and 'time' not in imports:
        blockers.append(_issue('blocker', 'IMPORT_MISSING', '脚本使用 time 时必须导入 time。'))
    if 'expect(' in source and 'expect' not in imports:
        blockers.append(_issue('blocker', 'IMPORT_MISSING', '脚本使用 expect 时必须从 playwright.async_api 导入 expect。'))
    if _undefined_names(tree, run_node):
        blockers.append(_issue('blocker', 'UNDEFINED_NAME', '脚本引用了未定义变量或缺失导入，无法安全执行。'))
    if action_count and len([item for item in blockers if item['code'] == 'ACTION_COMMENT_MISSING']) == action_count:
        blockers.append(_issue('blocker', 'ACTION_COMMENT_COVERAGE_ZERO', '业务动作没有可读步骤注释。'))

    unresolved = set(snapshot.unresolved_steps)
    if unresolved:
        warnings.append(_issue('warning', 'MISSING_EVIDENCE', '部分场景步骤缺少页面探索证据，需要定向补充探索或人工检查。'))
    evidence_ids = set(snapshot.step_evidence)
    missing_evidence = {step.id for step in scenario.steps} - evidence_ids
    if missing_evidence:
        warnings.append(_issue('warning', 'MISSING_EVIDENCE', '部分场景步骤没有对应探索证据，需要定向补充探索或人工检查。'))
    step_comments = {
        int(match.group(1)) for comment in comments.values()
        if (match := re.match(r'步骤\s*(\d+)\s*[:：]', comment))
    }
    expected_step_numbers = set(range(1, len(scenario.steps) + 1))
    if step_comments != expected_step_numbers:
        warnings.append(_issue('warning', 'SCENARIO_STEP_MAPPING_INCOMPLETE', '脚本步骤备注与场景步骤未完全对应，请人工核对。'))
    return _report(blockers, warnings, passes=_core_pass_checks(blockers))


def _report(
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    passes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    for item in [*(passes or []), *blockers, *warnings]:
        key = (item['level'], item['code'], item.get('line'))
        if key not in seen:
            seen.add(key)
            checks.append(item)
    passes = [item for item in checks if item['level'] == 'pass']
    blockers = [item for item in checks if item['level'] == 'blocker']
    warnings = [item for item in checks if item['level'] == 'warning']
    status = 'needs_review' if blockers else ('ready_with_warnings' if warnings else 'ready')
    message = '脚本需要人工检查后再保存。' if blockers else ('脚本可保存，但建议先处理警告。' if warnings else '脚本通过静态检查，可以保存并执行。')
    return redact_metadata({
        'status': status,
        'blockers': blockers,
        'warnings': warnings,
        'checks': checks,
        'summary': {
            'passed': len(passes),
            'warning': len(warnings),
            'blocker': len(blockers),
            'message': message,
        },
    })


def has_missing_evidence(report: dict[str, Any]) -> bool:
    return any(item.get('code') == 'MISSING_EVIDENCE' for item in report.get('warnings', []))


def blocker_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get('blockers') or [])
