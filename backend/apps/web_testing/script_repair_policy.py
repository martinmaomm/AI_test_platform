"""Conservative runtime repair gate: keep business semantics and assertions.

This is intentionally narrower than general AI refactoring.  A debug repair may
add a small standard import or change an evidenced locator, not rewrite the test
to make a failure disappear.  Rejected candidates never replace the draft.
"""

from __future__ import annotations

import ast
import copy
from typing import Any

from .exploration_trace import coerce_trace

_LOCATOR_METHODS = frozenset({
    'locator', 'get_by_role', 'get_by_text', 'get_by_label', 'get_by_placeholder',
    'get_by_test_id', 'get_by_alt_text', 'get_by_title', 'filter', 'nth',
})
_SAFE_NEW_IMPORTS = frozenset({'os', 'time', 're', 'uuid', 'datetime'})


def _signature(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _is_locator(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in {'first', 'last'}:
        return _is_locator(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr not in _LOCATOR_METHODS:
            return False
        parent = node.func.value
        return (isinstance(parent, ast.Name) and parent.id == 'page') or _is_locator(parent)
    return False


def _evidenced_locators(trace: Any) -> set[str]:
    data = coerce_trace(trace).model_dump(mode='json')
    result: set[str] = set()
    for event in data.get('events', []):
        if event.get('status') != 'succeeded':
            continue
        locator = event.get('locator') or {}
        for value in locator.values():
            try:
                expression = ast.parse(value, mode='eval').body
            except (SyntaxError, ValueError, TypeError):
                expression = None
            if expression is not None and _is_locator(expression):
                result.add(_signature(expression))
            elif isinstance(value, str) and value.strip() and not value.startswith('page.'):
                # Some MCP snapshots contain a CSS selector instead of a Python locator.
                result.add(_signature(ast.parse(f'page.locator({value!r})', mode='eval').body))
    return result


class _RemoveDocstrings(ast.NodeTransformer):
    def generic_visit(self, node):
        node = super().generic_visit(node)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    node.body = node.body[1:]
        return node


def _same_business_structure(before: Any, after: Any, allowed: set[str]) -> bool:
    if isinstance(before, ast.AST) and isinstance(after, ast.AST):
        if _is_locator(before) and _is_locator(after):
            return _signature(before) == _signature(after) or _signature(after) in allowed
        if type(before) is not type(after):
            return False
        return all(
            _same_business_structure(getattr(before, field), getattr(after, field), allowed)
            for field in before._fields
        )
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            _same_business_structure(left, right, allowed)
            for left, right in zip(before, after)
        )
    return before == after


def validate_targeted_repair(original_script: str, candidate_script: str, snapshot: Any) -> list[dict]:
    """Return safe blockers; an empty list allows the separate static gate to run.

    Assertion arguments, business input, navigation, cleanup, exception handling
    and all control flow must remain unchanged.  Locator changes require evidence.
    No code is executed here.  This gate is not an arbitrary-Python sandbox.
    """
    blocker = {
        'severity': 'blocker', 'code': 'REPAIR_SCOPE_CHANGED',
        'message': '修复改变了业务步骤、断言、清理逻辑或使用了未确认的定位器；已保留原稿，请人工检查。',
    }
    try:
        before = ast.parse(original_script)
        after = ast.parse(candidate_script)
    except (SyntaxError, TypeError, ValueError):
        return [{**blocker, 'code': 'REPAIR_SYNTAX_INVALID', 'message': '无法安全比较修复脚本，已保留原稿。'}]
    old_imports = [_signature(node) for node in before.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    new_imports = [_signature(node) for node in after.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    if any(signature not in new_imports for signature in old_imports):
        return [blocker]
    for node in after.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or _signature(node) in old_imports:
            continue
        if not isinstance(node, ast.Import) or any(
            alias.asname or alias.name not in _SAFE_NEW_IMPORTS for alias in node.names
        ):
            return [blocker]
    before = _RemoveDocstrings().visit(copy.deepcopy(before))
    after = _RemoveDocstrings().visit(copy.deepcopy(after))
    before.body = [node for node in before.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    after.body = [node for node in after.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    return [] if _same_business_structure(before, after, _evidenced_locators(snapshot)) else [blocker]
