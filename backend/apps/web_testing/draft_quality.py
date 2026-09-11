"""Checks for editable agent-authored Python, independent of replay ASTs.

These checks catch common generation mistakes; they are not a Python sandbox
and never claim that business operations or assertions have actually passed.
"""

from __future__ import annotations

import ast
import builtins
import re
import symtable
from typing import Any

from .assertion_state import analyze_assertion_state
from .script_contract import ScriptContractError, normalize_for_storage
from .target_urls import target_origin, validate_target_url


_ALLOWED_IMPORTS = frozenset({
    'playwright.async_api', 'time', 'datetime', 'uuid', 'secrets',
    'random', 're', 'json', 'math', 'decimal', 'string', 'typing', 'asyncio',
    'collections', 'itertools', 'os',
})
_FORBIDDEN_CALLS = frozenset({
    'eval', 'exec', 'compile', '__import__', 'open', 'input', 'globals', 'locals',
    'getattr', 'setattr', 'delattr',
})
_LIFECYCLE_CALLS = frozenset({
    'launch', 'launch_persistent_context', 'new_context', 'new_page', 'close',
    'connect', 'connect_over_cdp', 'async_playwright', 'sync_playwright',
})
_UNMANAGED_CALLS = frozenset({'evaluate', 'evaluate_handle', 'add_script_tag', 'add_init_script', 'set_input_files'})


def _issue(code: str, message: str, *, level: str = 'blocker', line: int | None = None) -> dict:
    return {'level': level, 'code': code, 'message': message, 'line': line}


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return f'{parent}.{node.attr}' if parent else node.attr
    return ''


def _undefined_globals(source: str) -> list[str]:
    root = symtable.symtable(source, '<draft>', 'exec')
    defined = {item.get_name() for item in root.get_symbols() if item.is_assigned() or item.is_imported() or item.is_namespace()}
    known = defined | set(dir(builtins))
    missing: set[str] = set()

    def visit(table):
        for item in table.get_symbols():
            if item.is_referenced() and item.is_global() and item.get_name() not in known:
                missing.add(item.get_name())
        for child in table.get_children():
            visit(child)

    visit(root)
    return sorted(missing)


def _simple_statement_call(node: ast.stmt) -> ast.Call | None:
    """Return a call whose execution order is explicit in one simple statement."""
    value: ast.AST | None = None
    if isinstance(node, ast.Expr):
        value = node.value
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value
    elif isinstance(node, ast.Return):
        value = node.value
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _static_entry_goto(tree: ast.Module) -> tuple[str, ast.Call | ast.stmt | None]:
    """Find a statically ordered entry goto without interpreting Python flow.

    Direct sequential statements in ``run`` and directly-called local helpers
    are reliable. Branches, loops, dynamic goto values, and recursive helpers
    stay explicitly unconfirmed.
    """
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    run = functions.get('run')
    if run is None:
        return 'missing', None

    def inspect(function: ast.FunctionDef | ast.AsyncFunctionDef, stack: frozenset[str]):
        if function.name in stack:
            return 'unconfirmed', function
        stack = stack | {function.name}
        for statement in function.body:
            if not isinstance(statement, (
                ast.Expr, ast.Assign, ast.AnnAssign, ast.Return, ast.Pass,
                ast.Import, ast.ImportFrom,
            )):
                # Do not guess which branch/helper a compound statement runs.
                return 'unconfirmed', statement
            call = _simple_statement_call(statement)
            if any(
                isinstance(child, ast.Name) and child.id in functions
                and not (call is not None and child is call.func)
                for child in ast.walk(statement)
            ):
                # Passing or aliasing a helper makes its execution order unknown.
                return 'unconfirmed', statement
            if call is not None:
                awaited = isinstance(getattr(statement, 'value', None), ast.Await)
                short = call.func.attr if isinstance(call.func, ast.Attribute) else ''
                if short == 'goto':
                    if not awaited:
                        return 'unconfirmed', call
                    target = call.args[0] if call.args else next(
                        (kw.value for kw in call.keywords if kw.arg == 'url'), None,
                    )
                    if isinstance(target, ast.Constant) and isinstance(target.value, str):
                        return 'literal', call
                    return 'unconfirmed', call
                if isinstance(call.func, ast.Name) and call.func.id in functions:
                    if isinstance(functions[call.func.id], ast.AsyncFunctionDef) and not awaited:
                        return 'unconfirmed', call
                    result = inspect(functions[call.func.id], stack)
                    if result[0] != 'missing':
                        return result
            # A goto hidden in control flow cannot be ordered reliably here.
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == 'goto'
                for child in ast.walk(statement)
            ):
                return 'unconfirmed', statement
            if isinstance(statement, ast.Return):
                return 'missing', None
        return 'missing', None

    return inspect(run, frozenset())


def evaluate_draft(script: str, *, target_url: str = '', snapshot: dict | None = None) -> dict[str, Any]:
    source = str(script or '').strip()
    state = analyze_assertion_state(source)
    blockers: list[dict] = []
    warnings: list[dict] = []

    def report():
        return {
            'status': 'needs_review' if blockers else 'ready_with_warnings' if warnings else 'ready',
            'source': 'agent_draft', 'blockers': blockers, 'warnings': warnings,
            'assertion_state': state,
            'completion': 'partial' if blockers or state['status'] != 'complete' else 'complete',
        }

    try:
        tree = ast.parse(source, filename='webui_test_script.py')
        compile(tree, 'webui_test_script.py', 'exec')  # Syntax only; never execute a draft.
        normalize_for_storage(source)
    except (SyntaxError, ScriptContractError, ValueError) as exc:
        blockers.append(_issue('SCRIPT_CONTRACT_INVALID', str(exc), line=getattr(exc, 'lineno', None)))
        return report()

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name
                if item.name not in _ALLOWED_IMPORTS:
                    blockers.append(_issue('IMPORT_NOT_ALLOWED', f'生成草稿不允许导入 {item.name}，请使用页面工具和标准测试辅助库。', line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in _ALLOWED_IMPORTS or any(item.name == '*' for item in node.names):
                blockers.append(_issue('IMPORT_NOT_ALLOWED', '生成草稿包含未允许的模块或通配符导入。', line=node.lineno))
            for item in node.names:
                aliases[item.asname or item.name] = f'{node.module}.{item.name}'

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            blockers.append(_issue('UNSAFE_ATTRIBUTE', '生成草稿不允许访问 Python 内部双下划线属性。', line=node.lineno))
        if not isinstance(node, ast.Call):
            continue
        name = _qualified_name(node.func, aliases)
        short = name.rsplit('.', 1)[-1]
        if name in _FORBIDDEN_CALLS:
            blockers.append(_issue('UNSAFE_CALL', f'生成草稿不能调用 {name}。', line=node.lineno))
        if short in _LIFECYCLE_CALLS:
            blockers.append(_issue('BROWSER_LIFECYCLE_FORBIDDEN', '浏览器和页面生命周期由平台执行器管理。', line=node.lineno))
        if short in _UNMANAGED_CALLS:
            blockers.append(_issue('UNMANAGED_BROWSER_OPERATION', '生成草稿不能执行页面 JavaScript 或上传文件。', line=node.lineno))
        if name.startswith('os.') and name not in {'os.getenv', 'os.environ.get'}:
            blockers.append(_issue('UNSAFE_OS_OPERATION', '生成草稿只能读取配置变量，不能调用操作系统命令或修改环境。', line=node.lineno))
        if short == 'goto':
            target = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'url'), None)
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                try:
                    validate_target_url(target.value)
                    if target_url and target_origin(target.value) != target_origin(target_url):
                        blockers.append(_issue('NAVIGATION_OUTSIDE_TARGET', '脚本导航地址不属于描述中的目标站点，请确认测试范围。', line=node.lineno))
                except ValueError:
                    blockers.append(_issue('ABSOLUTE_URL_REQUIRED', '脚本导航必须填写完整 HTTP(S) 网址，不能依赖相对路径或环境地址。', line=node.lineno))
            else:
                warnings.append(_issue('DYNAMIC_NAVIGATION_REVIEW', '动态导航地址需要人工确认最终为完整 HTTP(S) 网址，并属于本次测试目标。', level='warning', line=node.lineno))
        if short in {'wait_for_timeout', 'sleep'}:
            warnings.append(_issue('FIXED_WAIT', '建议使用页面状态或 expect 等待，减少固定等待。', level='warning', line=node.lineno))
        if short == 'screenshot':
            for kw in node.keywords:
                if kw.arg == 'path' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    path = kw.value.value.replace('\\', '/')
                    if path.startswith('/') or '..' in path.split('/') or re.match(r'^[A-Za-z]:', path):
                        blockers.append(_issue('SCREENSHOT_PATH_INVALID', '截图不能写入绝对路径或上级目录。', line=node.lineno))

    # Top-level execution must not perform work merely by importing the script.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            try:
                ast.literal_eval(node.value)
                continue
            except (ValueError, TypeError):
                pass
        blockers.append(_issue('TOP_LEVEL_EXECUTION', '请把动态数据生成和页面操作放在 run 函数内。', line=getattr(node, 'lineno', None)))

    entry_status, entry = _static_entry_goto(tree)
    if entry_status == 'missing':
        blockers.append(_issue('ENTRY_NAVIGATION_MISSING', '脚本缺少打开目标页面的 page.goto 操作。'))
    elif entry_status == 'unconfirmed':
        warnings.append(_issue(
            'ENTRY_NAVIGATION_UNCONFIRMED',
            '无法静态确认脚本首个 page.goto 的完整入口网址；草稿可继续编辑和调试，但不能标记为已验证。',
            level='warning', line=getattr(entry, 'lineno', None),
        ))
    elif target_url and isinstance(entry, ast.Call):
        entry_target = entry.args[0] if entry.args else next(
            (kw.value for kw in entry.keywords if kw.arg == 'url'), None,
        )
        if (
            isinstance(entry_target, ast.Constant)
            and isinstance(entry_target.value, str)
            and target_origin(entry_target.value) == target_origin(target_url)
            and entry_target.value != target_url
        ):
            blockers.append(_issue(
                'TARGET_URL_CHANGED',
                '脚本入口与描述中的完整目标网址不同，请保留路径、参数和 # 路由。',
                line=entry.lineno,
            ))

    missing = _undefined_globals(source)
    if missing:
        blockers.append(_issue('UNDEFINED_NAME', f'脚本引用了未定义名称：{", ".join(missing[:8])}。'))
    leading_comments = source.splitlines()[:tree.body[0].lineno - 1]
    if not ast.get_docstring(tree) and not any(line.lstrip().startswith('#') for line in leading_comments):
        warnings.append(_issue('SCENARIO_DESCRIPTION_MISSING', '建议在脚本顶部补充本次场景描述。', level='warning'))
    if not any(line.lstrip().startswith('#') for line in source.splitlines()):
        warnings.append(_issue('STEP_COMMENTS_MISSING', '建议给主要操作步骤添加中文注释。', level='warning'))
    if state['pending_count']:
        pending_steps = sum(item.get('kind') == 'step' for item in state['pending'])
        pending_assertions = state['pending_count'] - pending_steps
        kinds = []
        if pending_steps:
            kinds.append(f'{pending_steps} 项步骤')
        if pending_assertions:
            kinds.append(f'{pending_assertions} 项断言')
        warnings.append(_issue('PENDING_WORK', f"仍有{'、'.join(kinds)}待补充；草稿可以编辑，不能视为测试通过。", level='warning'))
    elif state['confirmed_count'] == 0:
        warnings.append(_issue('NO_REAL_ASSERTION', '脚本没有真实断言，执行完成也只能显示验证未完成。', level='warning'))
    # Successful tool calls support review, but do not make a locator/business
    # result verified. Missing evidence is advisory, not a code-generation gate.
    if snapshot and not snapshot.get('events'):
        warnings.append(_issue('NO_BROWSER_EVIDENCE', '尚无真实浏览器操作证据，请人工检查并调试该草稿。', level='warning'))
    return report()
