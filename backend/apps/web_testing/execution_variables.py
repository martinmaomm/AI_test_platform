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
    'CURL_CA_BUNDLE', 'NODE_OPTIONS',
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
