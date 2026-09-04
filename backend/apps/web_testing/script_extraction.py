"""Deterministic metadata extraction for canonical Python Playwright scripts."""

from __future__ import annotations

import ast
import io
import tokenize
from typing import Any

from .assertion_state import analyze_assertion_state


EXTRACTION_VERSION = 'webui-playwright-ast-v1'
ACTION_METHODS = {
    'goto', 'click', 'dblclick', 'fill', 'type', 'check', 'uncheck',
    'select_option', 'press', 'hover', 'focus', 'clear', 'tap', 'drag_to',
}
INPUT_ACTIONS = {'fill', 'type', 'select_option', 'press'}
ASSERTION_METHODS = {
    'to_be_visible', 'to_be_hidden', 'to_be_enabled', 'to_be_disabled',
    'to_be_checked', 'to_be_editable', 'to_have_text', 'to_contain_text',
    'to_have_value', 'to_have_url', 'to_have_title', 'to_have_count',
}

def _safe_expression(node: ast.AST) -> str:
    try:
        expression = ast.unparse(node)
    except Exception:
        expression = '<expression>'
    return expression


def _is_page_receiver(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == 'page'


def _is_expect_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'expect'
    )


def _assertion_expected(method: str, call: ast.Call) -> str:
    labels = {
        'to_be_visible': '目标元素应可见',
        'to_be_hidden': '目标元素应隐藏',
        'to_be_enabled': '目标元素应可用',
        'to_be_disabled': '目标元素应禁用',
        'to_be_checked': '目标元素应选中',
        'to_be_editable': '目标元素应可编辑',
        'to_have_text': '页面应包含指定文本',
        'to_contain_text': '页面应包含指定文本',
        'to_have_value': '目标元素应有指定值',
        'to_have_url': '页面应到达指定地址',
        'to_have_title': '页面应有指定标题',
        'to_have_count': '目标元素数量应符合预期',
    }
    label = labels.get(method, f'断言 {method} 成功')
    if call.args:
        expected = _safe_expression(call.args[0])
        if method in {'to_have_text', 'to_contain_text', 'to_have_value', 'to_have_url', 'to_have_title'}:
            return f'{label}：{expected}'
    return label


def _comment_lines(content: str) -> dict[int, str]:
    comments: dict[int, str] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(content).readline):
            if token.type == tokenize.COMMENT:
                comments[token.start[0]] = token.string.lstrip('#').strip()
    except tokenize.TokenError:
        return comments
    return comments


def _nearest_readable_comment(comments: dict[int, str], line: int, prefixes: tuple[str, ...]) -> str | None:
    for current in range(line - 1, max(0, line - 5), -1):
        comment = comments.get(current, '')
        if comment.startswith(prefixes):
            return comment
    return None


def extract_playwright_metadata(content: str, description: str = '') -> dict[str, Any]:
    """Extract steps and assertions without executing or inferring business data."""

    tree = ast.parse(content, filename='webui_test_script.py')
    run = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run'
    )

    extracted_steps: list[dict[str, Any]] = []
    locator_candidates: list[dict[str, Any]] = []
    assertion_candidates: list[dict[str, Any]] = []
    seen_locators: set[str] = set()
    comments = _comment_lines(content)

    awaited_nodes = sorted(
        (
            node for node in ast.walk(run)
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in awaited_nodes:
        call = node.value
        if not isinstance(call.func, ast.Attribute):
            continue
        method = call.func.attr
        receiver = call.func.value

        if method in ACTION_METHODS:
            locator = None if _is_page_receiver(receiver) and method == 'goto' else _safe_expression(receiver)
            step: dict[str, Any] = {
                'step_id': len(extracted_steps) + 1,
                'action': method,
                'target': locator or 'page',
            }
            readable_name = _nearest_readable_comment(comments, node.lineno, ('步骤', '清理'))
            if readable_name:
                step['readable_name'] = readable_name
            if locator and locator not in seen_locators:
                locator_candidates.append({'expression': locator, 'source': 'ast'})
                seen_locators.add(locator)
            if method == 'goto' and call.args:
                step['url'] = _safe_expression(call.args[0])
            elif method in INPUT_ACTIONS:
                step['value'] = _safe_expression(call.args[0]) if call.args else ''
            extracted_steps.append(step)
            continue

        if method in ASSERTION_METHODS and _is_expect_call(receiver):
            target_call = receiver
            target = _safe_expression(target_call.args[0]) if target_call.args else '<target>'
            assertion_candidates.append({
                'assertion': method,
                'target': target,
                'expected': _assertion_expected(method, call),
            })
            readable_name = _nearest_readable_comment(comments, node.lineno, ('断言',))
            if readable_name:
                assertion_candidates[-1]['readable_name'] = readable_name

    if assertion_candidates:
        expected_result = '；'.join(item['expected'] for item in assertion_candidates)
    else:
        safe_description = str(description).strip()
        expected_result = (
            f'完成测试流程：{safe_description[:120]}'
            if safe_description else '测试流程执行完成并满足页面验证条件'
        )

    return {
        'extracted_steps': extracted_steps,
        'locator_candidates': locator_candidates,
        'assertion_candidates': assertion_candidates,
        # This is always derived from the supplied source, never from an older
        # generation record.  Runtime proof is added by the execution service.
        'assertion_state': analyze_assertion_state(content),
        'extraction_version': EXTRACTION_VERSION,
        'expected_result': expected_result,
    }
