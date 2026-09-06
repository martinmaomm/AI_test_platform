"""Validation and precedence rules for WebUI execution variables."""

from __future__ import annotations

import re
from typing import Any, Iterable

from django.core.cache import cache


VARIABLE_NAME_RE = re.compile(r'^[A-Z_][A-Z0-9_]{0,127}$')
PROTECTED_PREFIXES = ('PYTHON', 'PLAYWRIGHT_', 'PYTEST_', 'DJANGO_', 'CELERY_', 'LD_')
PROTECTED_NAMES = {
    'PATH', 'HOME', 'SHELL', 'VIRTUAL_ENV', 'PWD', 'TMPDIR',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
    'CURL_CA_BUNDLE', 'NODE_OPTIONS', 'WEBUI_RUNTIME_VARIABLES',
}
RUNTIME_VARIABLE_TTL_SECONDS = 60 * 60


class ExecutionVariableError(ValueError):
    pass


def validate_variable_name(value: Any) -> str:
    name = str(value or '').strip().upper()
    if not VARIABLE_NAME_RE.fullmatch(name):
        raise ExecutionVariableError('变量名只能包含大写字母、数字和下划线，且不能以数字开头')
    if name in PROTECTED_NAMES or name.startswith(PROTECTED_PREFIXES):
        raise ExecutionVariableError(f'变量 {name} 属于系统保留变量，不能覆盖')
    return name


def normalize_variable_definitions(value: Any) -> list[dict[str, Any]]:
    """Normalize editable definitions while preserving secret/display metadata."""
    if value in (None, ''):
        return []
    if isinstance(value, dict):
        items: Iterable[Any] = [
            {'name': key, 'value': item}
            for key, item in value.items()
        ]
    elif isinstance(value, list):
        items = value
    else:
        raise ExecutionVariableError('变量必须是对象或变量列表')

    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ExecutionVariableError('每个变量必须是对象')
        name = validate_variable_name(raw.get('name') or raw.get('key'))
        if name in names:
            raise ExecutionVariableError(f'变量名重复: {name}')
        names.add(name)
        normalized.append({
            'name': name,
            'value': '' if raw.get('value') is None else str(raw.get('value')),
            'is_secret': bool(raw.get('is_secret', False)),
            'required': bool(raw.get('required', False)),
            'description': str(raw.get('description') or '').strip()[:200],
        })
    if len(normalized) > 100:
        raise ExecutionVariableError('变量数量不能超过 100 个')
    return normalized


def variable_values(value: Any, *, require_values: bool = True) -> dict[str, str]:
    definitions = normalize_variable_definitions(value)
    result: dict[str, str] = {}
    for item in definitions:
        if require_values and item['required'] and not item['value']:
            raise ExecutionVariableError(f"必填变量 {item['name']} 未设置值")
        result[item['name']] = item['value']
    return result


def merge_execution_variables(*layers: Any) -> dict[str, str]:
    """Merge low-to-high priority layers into one safe process environment map."""
    merged: dict[str, str] = {}
    required_names: set[str] = set()
    for layer in layers:
        if layer in (None, ''):
            continue
        for item in normalize_variable_definitions(layer):
            merged[item['name']] = item['value']
            if item['required']:
                required_names.add(item['name'])
    missing = sorted(name for name in required_names if not merged.get(name))
    if missing:
        raise ExecutionVariableError(f"必填变量 {', '.join(missing)} 未设置值")
    return merged


def merge_variable_definitions(*layers: Any) -> list[dict[str, Any]]:
    """Return one normalized definition list with later layers taking priority.

    This is deliberately separate from ``merge_execution_variables``.  A
    frozen suite repair source needs to retain definition metadata while still
    applying the normal low-to-high precedence rule.  Concatenating the two
    snapshots would turn an intentional cross-layer override into a duplicate
    definition error on the next normalization.
    """
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for item in normalize_variable_definitions(layer):
            merged[item['name']] = item
    return list(merged.values())


def runtime_variable_names(value: Any) -> list[str]:
    """Persist only safe runtime variable names, never their values."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        names: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = validate_variable_name(item)
            if name in seen:
                raise ExecutionVariableError(f'变量名重复: {name}')
            names.append(name)
            seen.add(name)
        return names
    return [item['name'] for item in normalize_variable_definitions(value)]


def require_runtime_variables(value: Any, required_names: Any) -> list[dict[str, Any]]:
    """Require re-entry of every runtime variable named by an old snapshot."""
    variables = normalize_variable_definitions(value)
    required = {validate_variable_name(name) for name in (required_names or [])}
    supplied = {item['name'] for item in variables if item['value']}
    missing = sorted(required - supplied)
    if missing:
        raise ExecutionVariableError(f"一次性运行变量 {', '.join(missing)} 必须重新输入")
    return variables


def store_runtime_variables(execution_id: int, value: Any) -> None:
    """Keep one-time overrides out of Celery payloads and persistent records."""
    normalized = normalize_variable_definitions(value)
    cache.set(
        f'webui:execution:{int(execution_id)}:runtime_variables',
        normalized,
        timeout=RUNTIME_VARIABLE_TTL_SECONDS,
    )


def pop_runtime_variables(execution_id: int) -> list[dict[str, Any]]:
    key = f'webui:execution:{int(execution_id)}:runtime_variables'
    value = cache.get(key) or []
    cache.delete(key)
    return normalize_variable_definitions(value)


def _repair_runtime_key(generation_id: object, revision: int, digest: str) -> str:
    return f'webui:repair:{generation_id}:{int(revision)}:{digest}:runtime_variables'


def store_repair_runtime_variables(generation_id: object, revision: int, digest: str, value: Any) -> None:
    """One repair request may reuse values across at most two local attempts.

    Values deliberately never enter workspace JSON or Celery task arguments.
    """
    cache.set(_repair_runtime_key(generation_id, revision, digest), normalize_variable_definitions(value), timeout=RUNTIME_VARIABLE_TTL_SECONDS)


def pop_repair_runtime_variables(generation_id: object, revision: int, digest: str) -> list[dict[str, Any]]:
    key = _repair_runtime_key(generation_id, revision, digest)
    value = cache.get(key) or []
    cache.delete(key)
    return normalize_variable_definitions(value)


def get_repair_runtime_variables(generation_id: object, revision: int, digest: str) -> list[dict[str, Any]]:
    """Read one repair request's short-lived values for its bounded two rounds."""
    return normalize_variable_definitions(
        cache.get(_repair_runtime_key(generation_id, revision, digest)) or []
    )
