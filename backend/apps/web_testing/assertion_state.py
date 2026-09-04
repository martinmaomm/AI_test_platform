"""Current-script assertion state and runtime verification helpers.

The saved Playwright business script remains untouched.  Static analysis only
describes whether a draft still needs an assertion; execution status additionally
requires the managed runner to report at least one assertion that completed.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import tokenize
from typing import Any, Iterator


PENDING_ASSERTION_PREFIX = 'AITS_PENDING_ASSERTION:'
PENDING_STEP_PREFIX = 'AITS_PENDING_STEP:'
RUNTIME_ASSERTION_COUNT_KEY = 'runtime_assertion_count'
_EXPECT_METHOD_PREFIXES = ('to_', 'not_to_')
_PROGRESS_COMMENT_RE = re.compile(r'^\s*验证\s*[:：]\s*(?P<label>\S.*)$')


def _run_function(tree: ast.Module) -> ast.AsyncFunctionDef | None:
    return next(
        (
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run'
        ),
        None,
    )


def _runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Walk the run body without crediting assertions inside helper definitions."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from _runtime_nodes(child)


def _is_literal_expression(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, MemoryError, RecursionError):
        pass
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _is_literal_expression(key)) and _is_literal_expression(value)
            for key, value in zip(node.keys, node.values)
        )
    if isinstance(node, ast.UnaryOp):
        return _is_literal_expression(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_literal_expression(node.left) and _is_literal_expression(node.right)
    if isinstance(node, ast.BoolOp):
        return all(_is_literal_expression(item) for item in node.values)
    if isinstance(node, ast.Compare):
        return _is_literal_expression(node.left) and all(
            _is_literal_expression(item) for item in node.comparators
        )
    return False


def _is_awaited_expect(node: ast.AST) -> bool:
    if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
        return False
    matcher = node.value
    return bool(
        isinstance(matcher.func, ast.Attribute)
        and matcher.func.attr.startswith(_EXPECT_METHOD_PREFIXES)
        and isinstance(matcher.func.value, ast.Call)
        and isinstance(matcher.func.value.func, ast.Name)
        and matcher.func.value.func.id == 'expect'
    )


def _pending_markers(script: str) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(script).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            comment = token.string[1:].strip()
            prefix = next((value for value in (PENDING_ASSERTION_PREFIX, PENDING_STEP_PREFIX) if comment.startswith(value)), None)
            if prefix is None:
                continue
            payload = comment[len(prefix):].strip()
            try:
                value = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                value = {}
            assertion_id = str(value.get('assertion_id') or ('step' if prefix == PENDING_STEP_PREFIX else 'unknown')).strip() if isinstance(value, dict) else 'unknown'
            criterion = str(value.get('criterion') or '').strip() if isinstance(value, dict) else ''
            reason = str(value.get('reason') or '').strip() if isinstance(value, dict) else ''
            if not criterion or not reason:
                reason = reason or '仍有步骤或断言待补充，请完成后移除对应标记。'
            pending.append({
                'assertion_id': assertion_id,
                'criterion': criterion,
                'reason': reason,
                'line': token.start[0],
                'kind': 'step' if prefix == PENDING_STEP_PREFIX else 'assertion',
            })
    except (tokenize.TokenError, IndentationError):
        # Syntax validation remains the script contract's job.  Markers parsed
        # before the broken token are still useful to the editor.
        pass
    return pending


def analyze_assertion_state(script: str | None) -> dict[str, Any]:
    """Recompute deferred and real assertion state from the current script.

    Only tokenizer COMMENT tokens can become deferred assertions, so a marker
    inside a string literal never affects verification.  A source-level real
    assertion is deliberately not itself a pass result; the runner must later
    report a successful runtime count.
    """
    source = str(script or '')
    pending = _pending_markers(source)
    confirmed_count = 0
    try:
        tree = ast.parse(source, filename='webui_test_script.py')
        run = _run_function(tree)
        if run is not None:
            for node in _runtime_nodes(run):
                if isinstance(node, ast.Assert) and not _is_literal_expression(node.test):
                    confirmed_count += 1
                elif _is_awaited_expect(node):
                    confirmed_count += 1
    except SyntaxError:
        # Drafts may be invalid while being edited.  Save/execute endpoints
        # retain their existing syntax checks, while this view stays safe.
        pass
    pending_count = len(pending)
    return {
        'status': 'complete' if confirmed_count > 0 and pending_count == 0 else 'incomplete',
        'pending': pending,
        'confirmed_count': confirmed_count,
        'pending_count': pending_count,
    }


def evaluation_status(
    script: str | None,
    *,
    operation_success: bool,
    runtime_assertion_count: int | None,
) -> tuple[str, dict[str, Any], int]:
    """Return failed/passed/incomplete without conflating operations and proof."""
    state = analyze_assertion_state(script)
    try:
        runtime_count = max(0, int(runtime_assertion_count or 0))
    except (TypeError, ValueError):
        runtime_count = 0
    if not operation_success:
        return 'failed', state, runtime_count
    if state['status'] == 'complete' and runtime_count > 0:
        return 'passed', state, runtime_count
    return 'incomplete', state, runtime_count


def read_runtime_assertion_count(path: str | os.PathLike[str] | None) -> int:
    """Read the runner-owned sidecar without trusting user-provided content."""
    if not path:
        return 0
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return 0
        return max(0, int(payload.get(RUNTIME_ASSERTION_COUNT_KEY, 0)))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0


def _progress_comments_by_line(source: str) -> dict[int, str]:
    """Return valid ``# 验证：...`` labels keyed by their comment line."""
    labels: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            match = _PROGRESS_COMMENT_RE.match(token.string[1:])
            if match:
                labels[token.start[0]] = match.group('label').strip()
    except (tokenize.TokenError, IndentationError):
        pass
    return labels


def _progress_label(labels: dict[int, str], line: int, assertion_type: str) -> str:
    """Use an adjacent model-written label, with a generic source fallback."""
    return labels.get(line - 1) or f'第 {line} 行的{assertion_type}'


def instrument_runtime_assertions(source: str) -> str:
    """Instrument temporary code with assertion progress events only.

    Each source assertion is evaluated exactly once.  Successful assertions
    record their adjacent ``# 验证：...`` label (or a generic line fallback),
    while a caught failure is remembered so a later success cannot claim that
    the complete case finished normally.
    """
    tree = ast.parse(source, filename='webui_test_script.py')
    run = _run_function(tree)
    if run is None:
        return source
    labels = _progress_comments_by_line(source)

    class _RunAssertInstrumenter(ast.NodeTransformer):
        def __init__(self, target: ast.AsyncFunctionDef):
            self.target = target

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            if node is not self.target:
                return node
            return self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef):
            return node

        def visit_ClassDef(self, node: ast.ClassDef):
            return node

        def visit_Lambda(self, node: ast.Lambda):
            return node

        def visit_Assert(self, node: ast.Assert):
            node = self.generic_visit(node)
            success: list[ast.stmt] = []
            if not _is_literal_expression(node.test):
                label = _progress_label(labels, node.lineno, '条件断言')
                success.append(ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id='_aits_record_assertion', ctx=ast.Load()),
                        args=[ast.Constant(value=label)], keywords=[],
                    )
                ))
            failure = ast.Expr(
                value=ast.Call(
                    func=ast.Name(id='_aits_record_assertion_failure', ctx=ast.Load()),
                    args=[], keywords=[],
                )
            )
            wrapped = ast.Try(
                body=[node],
                handlers=[ast.ExceptHandler(
                    type=ast.Name(id='BaseException', ctx=ast.Load()),
                    name=None,
                    body=[failure, ast.Raise(exc=None, cause=None)],
                )],
                orelse=success,
                finalbody=[],
            )
            return ast.copy_location(wrapped, node)

        def visit_Await(self, node: ast.Await):
            node = self.generic_visit(node)
            if not _is_awaited_expect(node):
                return node
            matcher = node.value
            label = _progress_label(labels, node.lineno, '页面断言')
            matcher.func.value = ast.Call(
                func=ast.Name(id='_aits_wrap_expect', ctx=ast.Load()),
                args=[matcher.func.value, ast.Constant(value=label)],
                keywords=[],
            )
            return node

    tree = _RunAssertInstrumenter(run).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)
