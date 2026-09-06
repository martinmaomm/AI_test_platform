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
_EXPECT_MATCHER_PREFIXES = ('to_', 'not_to_')


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
        # Historical unit fixtures used ``locator``; live recorder events use
        # ``locator_input``.  Both are callback-owned MCP evidence only.
        locator = event.get('locator') or event.get('locator_input') or {}
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


def _locator_signatures(tree: ast.AST) -> set[str]:
    """Return every concrete locator expression, including ones under expect().

    Assertion comparison intentionally normalizes locator identity to protect
    matcher semantics.  Targeted repair must make the opposite check first:
    every newly introduced locator has to originate from a successful callback.
    """
    return {
        _signature(node)
        for node in ast.walk(tree)
        if _is_locator(node)
    }


def _is_expect_matcher(node: ast.AST) -> bool:
    """Return whether *node* is the awaited Playwright expect matcher call."""
    return bool(
        isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr.startswith(_EXPECT_MATCHER_PREFIXES)
        and isinstance(node.value.func.value, ast.Call)
        and isinstance(node.value.func.value.func, ast.Name)
        and node.value.func.value.func.id == 'expect'
    )


class _AssertionNormalizer(ast.NodeTransformer):
    """Ignore locator identities and matcher timeout values while comparing proof."""

    def visit(self, node):
        if isinstance(node, ast.AST) and _is_locator(node):
            return ast.Name(id='__aits_locator__', ctx=ast.Load())
        return super().visit(node)

    def visit_Call(self, node: ast.Call):
        is_matcher = bool(
            isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith(_EXPECT_MATCHER_PREFIXES)
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == 'expect'
        )
        node = self.generic_visit(node)
        if is_matcher:
            # Playwright matcher timeouts only affect waiting, not the asserted
            # condition.  The keyword itself may be added, removed or tuned.
            node.keywords = [keyword for keyword in node.keywords if keyword.arg != 'timeout']
        return node


def _normalized_signature(node: ast.AST) -> str:
    normalized = _AssertionNormalizer().visit(copy.deepcopy(node))
    return _signature(ast.fix_missing_locations(normalized))


def _assertion_statement_signature(statement: ast.stmt) -> tuple[str, str] | None:
    if isinstance(statement, ast.Assert):
        return ('assert', _normalized_signature(statement.test))
    if isinstance(statement, ast.Expr) and _is_expect_matcher(statement.value):
        matcher = statement.value.value
        return (f'expect:{matcher.func.attr}', _normalized_signature(matcher))
    return None


def _names_loaded(node: ast.AST) -> set[str]:
    return {
        child.id for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _assignment_bindings(tree: ast.Module, run: ast.AsyncFunctionDef) -> dict[str, list[tuple[str, set[str]]]]:
    """Describe values which can change an assertion without changing its AST.

    Locator values are deliberately omitted: locator repairs are the one useful
    kind of assignment change this repair path is expected to make.
    """
    module_bindings: dict[str, list[tuple[str, set[str]]]] = {}
    run_bindings: dict[str, list[tuple[str, set[str]]]] = {}

    class Collector(ast.NodeVisitor):
        def __init__(self, target_run: ast.AsyncFunctionDef | None, destination: dict[str, list[tuple[str, set[str]]]]):
            self.target_run = target_run
            self.destination = destination

        def visit_FunctionDef(self, node):
            return None

        def visit_AsyncFunctionDef(self, node):
            if node is self.target_run:
                self.generic_visit(node)
            return None

        def visit_ClassDef(self, node):
            return None

        def visit_Assign(self, node):
            self._record(node.targets, node.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self._record([node.target], node.value)
            self.generic_visit(node)

        def visit_NamedExpr(self, node):
            self._record([node.target], node.value)
            self.generic_visit(node)

        @staticmethod
        def _target_names(targets: list[ast.AST]) -> set[str]:
            return {
                child.id for target in targets for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }

        def _record(self, targets: list[ast.AST], value: ast.AST | None):
            if value is None or _is_locator(value):
                return
            value_signature = _normalized_signature(value)
            value_names = _names_loaded(value)
            for name in self._target_names(targets):
                self.destination.setdefault(name, []).append((value_signature, value_names))

    # A local binding shadows a module-level value.  Keep module constants only
    # for names not assigned inside run, so global expected values are protected
    # without locking unrelated globals when a local has the same name.
    Collector(None, module_bindings).visit(tree)
    Collector(run, run_bindings).visit(run)
    bindings = dict(module_bindings)
    bindings.update(run_bindings)
    return bindings


def _dependency_signature(statement: ast.stmt, bindings: dict[str, list[tuple[str, set[str]]]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    assertion = _assertion_statement_signature(statement)
    if assertion is None:
        return ()
    pending = _names_loaded(statement)
    pending.discard('expect')
    # ``page`` is a run parameter, never an in-script expected value binding.
    pending.discard('page')
    visited: set[str] = set()
    result: dict[str, tuple[str, ...]] = {}
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        values = bindings.get(name)
        if not values:
            continue
        result[name] = tuple(value for value, _names in values)
        for _value, names in values:
            pending.update(names - visited - {'expect', 'page'})
    return tuple(sorted(result.items()))


def _loop_control_signature(statement: ast.For | ast.AsyncFor | ast.While) -> str:
    if isinstance(statement, ast.While):
        return f'while:{_normalized_signature(statement.test)}'
    return repr((
        type(statement).__name__, _normalized_signature(statement.target),
        _normalized_signature(statement.iter),
    ))


def _with_control_signature(statement: ast.With | ast.AsyncWith) -> str:
    return repr((
        type(statement).__name__,
        tuple((
            _normalized_signature(item.context_expr),
            _normalized_signature(item.optional_vars) if item.optional_vars is not None else None,
        ) for item in statement.items),
    ))


def _except_control_signature(handler: ast.ExceptHandler) -> str:
    return repr((
        _normalized_signature(handler.type) if handler.type is not None else None,
        handler.name,
        tuple(_normalized_signature(statement) for statement in handler.body),
    ))


def _may_terminate(statement: ast.stmt) -> bool:
    """A newly-added early exit can bypass all following proof statements."""
    if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.If):
        return any(_may_terminate(item) for item in statement.body + statement.orelse)
    if isinstance(statement, (ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
        return any(
            _may_terminate(item)
            for body in (statement.body, getattr(statement, 'orelse', []), getattr(statement, 'finalbody', []))
            for item in body
        )
    return False


def _assertion_records(tree: ast.Module, run: ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    """Return runtime assertions with the control flow required to reach them."""
    records: list[dict[str, Any]] = []
    bindings = _assignment_bindings(tree, run)

    def walk(statements: list[ast.stmt], controls: tuple[tuple[str, str], ...]):
        terminating_before = False
        for statement in statements:
            signature = _assertion_statement_signature(statement)
            if signature is not None:
                records.append({
                    'signature': signature,
                    'control': controls,
                    'terminating_before': terminating_before,
                    'dependencies': _dependency_signature(statement, bindings),
                })
            if isinstance(statement, ast.If):
                condition = _normalized_signature(statement.test)
                walk(statement.body, controls + (('if-body', condition),))
                walk(statement.orelse, controls + (('if-else', condition),))
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                loop = _loop_control_signature(statement)
                walk(statement.body, controls + (('loop-body', loop),))
                walk(statement.orelse, controls + (('loop-else', loop),))
            elif isinstance(statement, ast.Try):
                handlers = tuple(_except_control_signature(handler) for handler in statement.handlers)
                signature_value = repr((handlers, bool(statement.orelse), bool(statement.finalbody)))
                walk(statement.body, controls + (('try-body', signature_value),))
                walk(statement.orelse, controls + (('try-else', signature_value),))
                walk(statement.finalbody, controls + (('try-finally', signature_value),))
                for handler in statement.handlers:
                    walk(handler.body, controls + (('try-except', _except_control_signature(handler)),))
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                walk(statement.body, controls + (('with', _with_control_signature(statement)),))
            if _may_terminate(statement):
                terminating_before = True

    walk(run.body, ())
    return records


def _run_function(tree: ast.Module) -> ast.AsyncFunctionDef | None:
    return next((node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run'), None)


def validate_assertion_preservation(original_script: str, candidate_script: str) -> list[dict[str, str]]:
    """Block candidates that remove, weaken or bypass an original runtime proof.

    This intentionally compares only existing ``assert`` and awaited Playwright
    ``expect`` checks plus the variables and control flow that make those checks
    meaningful.  It permits added checks, comments, locator fixes and wait/
    timeout tuning; it is not a whole-script equivalence checker or sandbox.
    """
    try:
        original = ast.parse(original_script)
        candidate = ast.parse(candidate_script)
    except (SyntaxError, TypeError, ValueError):
        return [{'code': 'ASSERTION_COMPARISON_INVALID', 'message': '无法安全比较原断言与候选脚本，候选不得自动执行。'}]
    original_run = _run_function(original)
    candidate_run = _run_function(candidate)
    if original_run is None or candidate_run is None:
        return [{'code': 'ASSERTION_EXECUTION_CHANGED', 'message': '候选缺少可比较的 run 执行入口，候选不得自动执行。'}]
    original_records = _assertion_records(original, original_run)
    candidate_records = _assertion_records(candidate, candidate_run)
    last_index = -1
    blockers: list[dict[str, str]] = []
    for original_record in original_records:
        matching = [
            (index, record) for index, record in enumerate(candidate_records)
            if index > last_index and record['signature'] == original_record['signature']
        ]
        if not matching:
            blockers.append({
                'code': 'ASSERTION_SEMANTICS_CHANGED',
                'message': '候选删除或改变了原有断言的方法、运算符、预期值、否定或容差。',
            })
            continue
        # Added assertions may share a matcher. Prefer the record which retains
        # all original dependencies and execution context, then keep the order
        # of duplicated original assertions stable.
        index, candidate_record = next(
            (
                item for item in matching
                if item[1]['control'] == original_record['control']
                and item[1]['terminating_before'] == original_record['terminating_before']
                and item[1]['dependencies'] == original_record['dependencies']
            ),
            matching[0],
        )
        last_index = index
        if candidate_record['control'] != original_record['control'] or (
            candidate_record['terminating_before'] != original_record['terminating_before']
        ):
            blockers.append({
                'code': 'ASSERTION_EXECUTION_CHANGED',
                'message': '候选改变了原断言的控制流或在其前加入了终止执行的语句。',
            })
        elif candidate_record['dependencies'] != original_record['dependencies']:
            blockers.append({
                'code': 'ASSERTION_EXPECTED_VALUE_CHANGED',
                'message': '候选改变了原断言依赖的预期值变量，候选不得自动执行。',
            })
    return blockers


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
    # Do this before normalizing assertion structure.  In particular,
    # ``expect(page.locator(...))`` must not inherit a pass merely because the
    # assertion normalizer deliberately ignores locator identity.
    evidenced = _evidenced_locators(snapshot)
    new_locators = _locator_signatures(after) - _locator_signatures(before)
    if any(locator not in evidenced for locator in new_locators):
        return [blocker]
    before = _RemoveDocstrings().visit(copy.deepcopy(before))
    after = _RemoveDocstrings().visit(copy.deepcopy(after))
    before.body = [node for node in before.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    after.body = [node for node in after.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    return [] if _same_business_structure(before, after, evidenced) else [blocker]
