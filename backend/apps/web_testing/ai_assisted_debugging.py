"""Pure helpers for the bounded WebUI AI-assisted debugging workflow.

The functions in this module intentionally do not persist evidence or runtime
values.  The caller owns the short-lived cache entry and must only store the
safe summaries returned here.
"""
from __future__ import annotations

import ast
import re

from typing import Any

from .execution_diagnostics import diagnose_failure, friendly_failure_summary


PAGE_RELATED_CATEGORIES = frozenset({'action_timeout', 'strict_mode', 'assertion_failure'})
NON_CODE_CATEGORIES = frozenset({'navigation_error', 'target_closed'})
MAX_REPAIR_LOG_CHARS = 12000
MAX_CANDIDATE_SCRIPT_CHARS = 200000
MAX_CANDIDATE_DIFF_CHARS = 20000
NON_CODE_MARKERS = (
    'err_connection_', 'err_name_not_resolved', 'err_internet_disconnected',
    'connection refused', 'network is unreachable', 'authentication failed',
    'invalid credentials', '账号或密码错误', '用户名或密码错误',
    'invalid username or password', 'incorrect username or password',
    'executable doesn\'t exist', 'browser executable', 'playwright install',
    'task_cancelled', '用户已取消',
)
DIFF_TRUNCATION_MARKER = '\n… 差异过长，已省略；请查看完整候选脚本。\n'


def bounded_text(value: Any, limit: int = MAX_REPAIR_LOG_CHARS) -> str:
    return str(value or '')[:max(0, limit)]


def redact_runtime_values(value: Any, runtime_variables: list[dict[str, Any]]) -> str:
    """Remove one-time overrides from persisted/model diagnostics.

    Short word-like values use token boundaries.  A username such as ``test``
    is therefore hidden when it appears as a value, without corrupting useful
    words such as ``pytest`` or ``contest`` in the same log.
    """
    text = str(value or '')
    for item in runtime_variables:
        secret = str(item.get('value') or '')
        if secret:
            if len(secret) < _RUNTIME_VALUE_SUBSTRING_LIMIT and re.fullmatch(
                r'[\w]+', secret,
            ):
                text = re.sub(
                    rf'(?<!\w){re.escape(secret)}(?!\w)',
                    '[运行变量已隐藏]',
                    text,
                )
            else:
                text = text.replace(secret, '[运行变量已隐藏]')
    return text


def failure_evidence(*, stdout: str = '', stderr: str = '', log: str = '', fallback: str = '', runtime_variables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    runtime_variables = runtime_variables or []
    stdout = bounded_text(redact_runtime_values(stdout, runtime_variables))
    stderr = bounded_text(redact_runtime_values(stderr, runtime_variables))
    log = bounded_text(redact_runtime_values(log, runtime_variables))
    fallback = bounded_text(redact_runtime_values(fallback, runtime_variables), 2000)
    diagnostic = diagnose_failure(stdout, stderr or log)
    return {
        'category': diagnostic.category,
        'summary': friendly_failure_summary(stdout, stderr or log, fallback),
        'technical_message': redact_runtime_values(diagnostic.technical_message, runtime_variables),
        'stdout': stdout,
        'stderr': stderr,
        'log': log,
    }


def requires_directed_mcp(evidence: dict[str, Any]) -> bool:
    return str(evidence.get('category') or '') in PAGE_RELATED_CATEGORIES


def is_non_code_failure(evidence: dict[str, Any]) -> bool:
    if str(evidence.get('category') or '') in NON_CODE_CATEGORIES:
        return True
    text = ' '.join(str(evidence.get(key) or '') for key in ('summary', 'technical_message', 'stdout', 'stderr', 'log')).lower()
    return any(marker in text for marker in NON_CODE_MARKERS)
def bounded_candidate_diff(value: Any) -> str:
    """Limit a display-only diff without discarding its complete candidate."""
    text = str(value or '')
    if len(text) <= MAX_CANDIDATE_DIFF_CHARS:
        return text
    prefix_limit = MAX_CANDIDATE_DIFF_CHARS - len(DIFF_TRUNCATION_MARKER)
    return text[:max(0, prefix_limit)] + DIFF_TRUNCATION_MARKER


_RUNTIME_VALUE_SUBSTRING_LIMIT = 16


def _string_literals(source: str) -> list[str]:
    try:
        tree = ast.parse(str(source or ''))
    except (SyntaxError, TypeError, ValueError):
        return []
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _runtime_literal_count(
    literals: list[str], value: str, *, allow_substring: bool = False,
) -> int:
    if not value:
        return 0
    if not allow_substring and len(value) < _RUNTIME_VALUE_SUBSTRING_LIMIT:
        return sum(literal == value for literal in literals)
    return sum(literal.count(value) for literal in literals)


def candidate_leaks_runtime_values(
    candidate: str,
    runtime_variables: list[dict[str, Any]],
    *,
    baseline_script: str = '',
) -> bool:
    """Reject runtime values newly added to Python string literals.

    Short values such as ``test`` are only rejected when an entire literal is
    that value.  Longer values are also rejected when embedded in a literal.
    A literal already present in the original test-environment draft is allowed
    to remain unchanged; only a newly introduced or increased occurrence is
    rejected.  Invalid Python deliberately returns ``False`` here: the shared
    static gate owns syntax diagnostics and reports those errors to reviewers.
    """
    literals = _string_literals(candidate)
    baseline_literals = _string_literals(baseline_script)
    for item in runtime_variables:
        value = str(item.get('value') or '')
        allow_substring = bool(item.get('is_secret'))
        if _runtime_literal_count(
            literals, value, allow_substring=allow_substring,
        ) > _runtime_literal_count(
            baseline_literals, value, allow_substring=allow_substring,
        ):
            return True
    return False
