"""Persistent browser-discovery evidence and handoff helpers.

This module deliberately does not start a browser.  The async agent owns the
MCP session and appends task-scoped JSONL; Django owns validation, redaction,
state and source-asset publication.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    APIEndpoint,
    APISpecification,
    APIWorkspace,
    BrowserDiscoveryHandoff,
    BrowserDiscoveryRecord,
    BrowserDiscoveryTask,
    default_api_workspace_draft,
)
from .workspace_service import WorkspaceConflict, WorkspaceValidationError, can_edit_project, can_execute_project


_PINNED_PLAYWRIGHT_PACKAGE = '@executeautomation/playwright-mcp-server@1.0.12'
_SENSITIVE_KEY = re.compile(r'(?:authorization|cookie|token|secret|password|api[_-]?key|session)', re.I)
_SAFE_HEADER = {'content-type', 'accept'}
_SUPPORTED_REQUEST_TYPES = {'application/json', 'application/x-www-form-urlencoded'}
_STATIC_RESOURCE_TYPES = {'stylesheet', 'image', 'font', 'media', 'manifest'}
_ORIGIN_STATE_MAX_BYTES = 64 * 1024
_ORIGIN_MAX_ITEMS = 32
_PENDING_ORIGIN_MAX_ITEMS = 16


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(getattr(settings, name, default))))
    except (TypeError, ValueError):
        return default


def discovery_limits() -> dict[str, int]:
    """Read local limits only; none of these alter global WebUI limits."""
    return {
        'timeout_seconds': _bounded_int('API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS', 900, 60, 1800),
        'max_model_steps': _bounded_int('API_BROWSER_DISCOVERY_MAX_MODEL_STEPS', 100, 1, 100),
        'max_tool_calls': _bounded_int('API_BROWSER_DISCOVERY_MAX_TOOL_CALLS', 100, 1, 100),
        'max_requests': _bounded_int('API_BROWSER_DISCOVERY_MAX_REQUESTS', 500, 1, 500),
        'max_body_bytes': _bounded_int('API_BROWSER_DISCOVERY_MAX_BODY_BYTES', 256 * 1024, 1024, 256 * 1024),
        'max_total_bytes': _bounded_int('API_BROWSER_DISCOVERY_MAX_TOTAL_BYTES', 20 * 1024 * 1024, 1024, 20 * 1024 * 1024),
    }


def browser_discovery_enabled() -> bool:
    return bool(getattr(settings, 'API_BROWSER_DISCOVERY_ENABLED', False))


def expire_stale_discovery(task: BrowserDiscoveryTask) -> BrowserDiscoveryTask:
    """Make a lost worker visible; evidence remains readable but never looks complete."""
    if task.status not in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING} or not task.heartbeat_at:
        return task
    lease_seconds = _bounded_int('API_BROWSER_DISCOVERY_HEARTBEAT_LEASE_SECONDS', 45, 10, 300)
    if task.heartbeat_at >= timezone.now() - timedelta(seconds=lease_seconds):
        return task
    with transaction.atomic():
        current = BrowserDiscoveryTask.objects.select_for_update().filter(pk=task.pk).first()
        if not current or current.status not in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}:
            return current or task
        if current.heartbeat_at and current.heartbeat_at >= timezone.now() - timedelta(seconds=lease_seconds):
            return current
        records = BrowserDiscoveryRecord.objects.filter(task=current).count()
        current.status = BrowserDiscoveryTask.Status.PARTIAL if records else BrowserDiscoveryTask.Status.FAILED
        current.current_action = 'worker 心跳已过期'
        current.error_code = 'heartbeat_expired'
        current.error_message = '浏览器探索 worker 心跳已过期；已保存的证据仍可查看，但不会继续执行。'
        current.source_version = current.version if records else 0
        current.finished_at = timezone.now()
        current.save()
        return current


def normalize_http_url(value: Any, *, label: str, origin_only: bool = False) -> str:
    from web_testing.target_urls import validate_target_url

    try:
        # This intentionally preserves a SPA entry's path, query and fragment.
        # It also rejects whitespace, userinfo and invalid host/port forms.
        url = validate_target_url(str(value or ''))
    except ValueError as exc:
        raise WorkspaceValidationError(f'{label} {exc}') from exc
    parts = urlsplit(url)
    if origin_only:
        if parts.path not in {'', '/'} or parts.query or parts.fragment:
            raise WorkspaceValidationError('api_origin 必须仅包含协议、主机和可选端口，不能包含路径、查询或凭据。')
        host = parts.hostname.lower().rstrip('.')
        host = f'[{host}]' if ':' in host else host
        port = parts.port
        default_port = 443 if parts.scheme.lower() == 'https' else 80
        return f'{parts.scheme.lower()}://{host}' + (f':{port}' if port and port != default_port else '')
    return url


def task_trace_dir(task: BrowserDiscoveryTask) -> Path:
    return Path(settings.BASE_DIR) / 'logs' / 'api-browser-discovery' / str(task.task_id)


def task_trace_file(task: BrowserDiscoveryTask) -> Path:
    # protocol_version=1 from the Node collector; actions.jsonl remains a
    # separate non-value-bearing action log and is never parsed as evidence.
    return task_trace_dir(task) / 'network.jsonl'


def origin_state_file(task: BrowserDiscoveryTask) -> Path:
    return task_trace_dir(task) / 'origin-state.json'


def origin_control_file(task: BrowserDiscoveryTask) -> Path:
    return task_trace_dir(task) / 'origin-control.json'


def _origin_mode(task: BrowserDiscoveryTask) -> str:
    return 'auto' if isinstance(task.limits, dict) and task.limits.get('origin_mode') == 'auto' else 'manual'


def _safe_origin(value: Any) -> str:
    try:
        return normalize_http_url(value, label='origin', origin_only=True)
    except WorkspaceValidationError:
        return ''


def _safe_pending(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    origin = _safe_origin(value.get('origin'))
    method = str(value.get('method') or '').upper()
    path = str(value.get('path') or '')
    if not origin or not re.fullmatch(r'[A-Z]{1,10}', method) or not path.startswith('/') or '?' in path or '#' in path:
        return None
    return {'origin': origin, 'method': method, 'path': path[:1000]}


def read_origin_state(task: BrowserDiscoveryTask) -> dict[str, Any]:
    """Read only the bounded, credential-free collector state projection."""
    defaults = {'version': 1, 'resolved_origins': [], 'pending': [], 'rejected_origins': []}
    path = origin_state_file(task)
    try:
        if not path.is_file() or path.stat().st_size > _ORIGIN_STATE_MAX_BYTES:
            return defaults
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError):
        return defaults
    if not isinstance(value, dict) or value.get('version') != 1:
        return defaults
    def origins(key: str) -> list[str]:
        result = []
        for item in value.get(key, []) if isinstance(value.get(key), list) else []:
            origin = _safe_origin(item)
            if origin and origin not in result:
                result.append(origin)
            if len(result) >= _ORIGIN_MAX_ITEMS:
                break
        return result
    resolved = origins('resolved_origins')
    rejected = origins('rejected_origins')
    pending = []
    for item in value.get('pending', []) if isinstance(value.get('pending'), list) else []:
        safe = _safe_pending(item)
        if safe and safe['origin'] not in rejected and safe not in pending:
            pending.append(safe)
        if len(pending) >= _PENDING_ORIGIN_MAX_ITEMS:
            break
    return {'version': 1, 'resolved_origins': resolved, 'pending': pending, 'rejected_origins': rejected}


def _read_origin_control(task: BrowserDiscoveryTask) -> dict[str, Any]:
    defaults = {'version': 1, 'approved_origins': [], 'rejected_origins': [], 'cancelled': False}
    path = origin_control_file(task)
    try:
        if not path.is_file() or path.stat().st_size > _ORIGIN_STATE_MAX_BYTES:
            return defaults
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError):
        return defaults
    if not isinstance(value, dict) or value.get('version') != 1:
        return defaults
    for key in ('approved_origins', 'rejected_origins'):
        items = []
        for item in value.get(key, []) if isinstance(value.get(key), list) else []:
            origin = _safe_origin(item)
            if origin and origin not in items:
                items.append(origin)
            if len(items) >= _ORIGIN_MAX_ITEMS:
                break
        defaults[key] = items
    defaults['cancelled'] = bool(value.get('cancelled'))
    return defaults


def write_origin_control(task: BrowserDiscoveryTask, control: dict[str, Any]) -> None:
    """Publish a complete control document with replace semantics for Node polling."""
    payload = {
        'version': 1,
        'approved_origins': list(control.get('approved_origins') or [])[:_ORIGIN_MAX_ITEMS],
        'rejected_origins': list(control.get('rejected_origins') or [])[:_ORIGIN_MAX_ITEMS],
        'cancelled': bool(control.get('cancelled')),
    }
    path = origin_control_file(task)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.origin-control-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_auto_origin(task: BrowserDiscoveryTask, *, save: bool = True) -> dict[str, Any]:
    """Only a sole collector-resolved auto origin becomes the current base URL."""
    state = read_origin_state(task)
    if _origin_mode(task) != 'auto':
        return state
    resolved = state['resolved_origins']
    persisted = _safe_origin((task.limits or {}).get('selected_origin'))
    # A terminal user choice must outlive later checkpoints/ingestion.  It is
    # accepted only through the endpoint after collector resolution, then
    # retained even if the state file is no longer readable during finalizing.
    selected = persisted or (resolved[0] if len(resolved) == 1 else '')
    if task.api_origin != selected:
        task.api_origin = selected
        if save:
            task.save(update_fields=['api_origin', 'updated_at'])
        _refresh_selected_record_eligibility(task)
    return state


def _refresh_selected_record_eligibility(task: BrowserDiscoveryTask) -> None:
    """A terminal source choice changes only this task's redacted eligibility view."""
    records = BrowserDiscoveryRecord.objects.filter(task=task)
    for record in records:
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        eligible = bool(
            task.api_origin and record.origin == task.api_origin and summary.get('source_authorized') is True
            and summary.get('capture_complete') is True and record.method and record.path
            and record.resource_type not in _STATIC_RESOURCE_TYPES
            and (not record.content_type or record.content_type in _SUPPORTED_REQUEST_TYPES)
        )
        exclusion = record.exclusion_reason
        if eligible:
            exclusion = ''
        elif summary.get('source_authorized') is True and record.origin != task.api_origin:
            exclusion = 'origin_not_selected'
        if record.is_eligible != eligible or record.exclusion_reason != exclusion:
            record.is_eligible = eligible
            record.exclusion_reason = exclusion
            record.save(update_fields=['is_eligible', 'exclusion_reason'])


def selectable_origins(task: BrowserDiscoveryTask, *, state: dict[str, Any] | None = None) -> list[str]:
    state = state or read_origin_state(task)
    resolved = set(state['resolved_origins'])
    collected = set()
    for record in BrowserDiscoveryRecord.objects.filter(task=task).only('origin', 'resource_type', 'public_summary'):
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        if (
            summary.get('source_authorized') is True and summary.get('capture_complete') is True
            and record.resource_type not in _STATIC_RESOURCE_TYPES
        ):
            collected.add(record.origin)
    return sorted(resolved & collected)


def origin_resolution(task: BrowserDiscoveryTask, *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or read_origin_state(task)
    active = task.status in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING} and not task.cancellation_requested
    mode = _origin_mode(task)
    pending = state['pending'] if active and mode == 'auto' else []
    selectable = selectable_origins(task, state=state) if mode == 'auto' else []
    if mode == 'manual':
        resolution_state = 'resolved' if task.api_origin else 'detecting'
        origins = [task.api_origin] if task.api_origin else []
    elif pending:
        resolution_state = 'awaiting_confirmation'
        origins = state['resolved_origins']
    elif task.status in {BrowserDiscoveryTask.Status.COMPLETED, BrowserDiscoveryTask.Status.PARTIAL} and not task.api_origin and len(state['resolved_origins']) > 1:
        resolution_state = 'awaiting_selection'
        origins = selectable
    elif task.api_origin:
        resolution_state = 'resolved'
        origins = state['resolved_origins'] or [task.api_origin]
    else:
        resolution_state = 'detecting'
        origins = state['resolved_origins']
    return {
        'mode': mode, 'state': resolution_state, 'origins': origins,
        'pending': pending, 'selected_origin': task.api_origin or None,
        'can_confirm': bool(active and pending),
    }


def _redact(value: Any, *, key: str = '') -> Any:
    if _SENSITIVE_KEY.search(key):
        return '<redacted>'
    if isinstance(value, dict):
        return {str(item)[:120]: _redact(child, key=str(item)) for item, child in value.items()}
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value[:100]]
    if isinstance(value, str):
        return value[:4096]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:4096]


def _body_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value[:4096]
    return _redact(value)


def _header_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _redact(item, key=str(key))
        for key, item in value.items()
        if str(key).lower() in _SAFE_HEADER
    }


def _header(value: Any, name: str) -> str:
    if not isinstance(value, dict):
        return ''
    for key, item in value.items():
        if str(key).lower() == name.lower():
            return str(item)
    return ''


def _auth_hints(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        return [
            {
                'name': str(item.get('name') or '')[:120],
                'scheme': str(item.get('scheme') or '')[:80],
                'value_hash': str(item.get('value_sha256') or '')[:64],
            }
            for item in value[:20] if isinstance(item, dict) and item.get('name')
        ]
    if not isinstance(value, dict):
        return []
    hints = []
    for name, raw in value.items():
        if not _SENSITIVE_KEY.search(str(name)):
            continue
        digest = hashlib.sha256(str(raw).encode()).hexdigest()[:16] if raw not in (None, '') else ''
        hints.append({'name': str(name)[:120], 'value_hash': digest})
    return hints


def _pairs(value: Any, fallback_query: str = '') -> list[dict[str, Any]]:
    """Keep repeated query/form keys observable instead of collapsing to dict."""
    if isinstance(value, dict):
        return [{'name': str(name), 'value': _redact(item, key=str(name))} for name, item in value.items()]
    if isinstance(value, list):
        pairs = []
        for item in value[:100]:
            if isinstance(item, dict) and isinstance(item.get('name'), str):
                pairs.append({'name': item['name'], 'value': _redact(item.get('value'), key=item['name'])})
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append({'name': str(item[0]), 'value': _redact(item[1], key=str(item[0]))})
        return pairs
    if fallback_query:
        return [{'name': name, 'value': _redact(item, key=name)} for name, item in parse_qsl(fallback_query, keep_blank_values=True)]
    return []


def _identifier_field(prefix: str) -> bool:
    """Short values are dependency candidates only for explicit ID fields."""
    field = prefix.rsplit('.', 1)[-1]
    return field == 'id' or field.endswith('_id') or field.endswith('Id')


def _response_scalar_values(value: Any, prefix: str = '') -> list[tuple[str, str]]:
    """Bounded candidates only; an equal value is evidence, never proof of a dependency."""
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            result.extend(_response_scalar_values(item, f'{prefix}.{key}' if prefix else str(key)))
        return result
    if isinstance(value, list):
        return [pair for index, item in enumerate(value[:20]) for pair in _response_scalar_values(item, f'{prefix}[{index}]')]
    if isinstance(value, (str, int, float)) and str(value) not in {'', '<redacted>'}:
        scalar = str(value)
        if len(scalar) >= 3 or _identifier_field(prefix):
            return [(prefix or 'value', scalar)]
    return []


def _request_scalar_fields(summary: dict[str, Any]) -> list[tuple[str, str]]:
    observed = summary.get('observed_request') if isinstance(summary.get('observed_request'), dict) else {}
    values = _response_scalar_values(observed.get('json'), 'json')
    for pair in observed.get('form') or []:
        if isinstance(pair, dict) and isinstance(pair.get('name'), str):
            values.extend(_response_scalar_values(pair.get('value'), f"data.{pair['name']}"))
    values.extend((f"params.{item.get('name')}", str(item.get('value'))) for item in observed.get('query', []) if isinstance(item, dict))
    values.extend((f'path[{index}]', segment) for index, segment in enumerate(str(summary.get('path') or '').split('/')) if segment)
    return [
        (label, value) for label, value in values
        if value not in {'', '<redacted>'} and (len(value) >= 3 or _identifier_field(label) or label.startswith('path['))
    ]


_GENERIC_ROUTE_SEGMENTS = frozenset({'api', 'apis'})


def _static_route(path: str) -> list[str]:
    return [
        segment.lower() for segment in path.split('/')
        if segment and not segment.isdecimal() and segment.lower() not in _GENERIC_ROUTE_SEGMENTS
        and not re.fullmatch(r'v\d+', segment.lower())
    ]


def _shared_resource_route(source_path: str, current_path: str) -> list[str]:
    source_route, current_route = _static_route(source_path), _static_route(current_path)
    shared = []
    for source_segment, current_segment in zip(source_route, current_route):
        if source_segment != current_segment:
            break
        shared.append(source_segment)
    # A route family can differ by one terminal action (list/update/delete),
    # but a deeper divergence is ambiguous namespace/resource traffic.  This
    # derives action position from the observed hierarchy; no action word is
    # hard-coded.
    if not shared or len(source_route) - len(shared) > 1 or len(current_route) - len(shared) > 1:
        return []
    return shared


def _resource_identity_field(field: str, source_path: str, current_path: str) -> bool:
    """Allow only a generic or route-resource primary key as a path source."""
    name = field.rsplit('.', 1)[-1]
    shared = _shared_resource_route(source_path, current_path)
    if not shared:
        return False
    if name == 'id':
        return True
    if not _identifier_field(name):
        return False
    resource = shared[-1]
    singular = resource[:-3] + 'y' if resource.endswith('ies') else resource.rstrip('s')
    normalized = name.replace('_', '').lower()
    return normalized in {f'{resource}id', f'{singular}id'}


def _same_resource_lineage(source_path: str, current_path: str, *, anchors=frozenset()) -> bool:
    """Require an observed non-generic resource route anchor, never global IDs.

    Browser applications commonly route one resource through sibling actions,
    for example ``/menu/list/0`` then ``/menu/update/70``.  Prefix matching
    misses that shape, while matching the numeric value globally would join
    unrelated ``/users/70`` and ``/products/70`` traffic.  Static route
    anchors retain the former evidence without assuming action names.
    """
    source = source_path.rstrip('/') or '/'
    current = current_path.rstrip('/') or '/'
    if source == current or current.startswith(source.rstrip('/') + '/') or source.startswith(current.rstrip('/') + '/'):
        return True
    shared = _shared_resource_route(source, current)
    if not shared:
        return False
    # Sibling names could be actions OR distinct resources. Only a separately
    # observed object URL can establish their shared resource anchor; e.g.
    # /items/42 supports /items/list -> /items/update, but a bare /admin prefix
    # does not equate /admin/users with /admin/products.
    return (
        _static_route(source) == shared or _static_route(current) == shared
        or tuple(shared) in anchors
    )


def _identity_route_anchors(records) -> set[tuple[str, ...]]:
    anchors = set()
    for record in records:
        summary = record.public_summary or {}
        body = (summary.get('observed_response') or {}).get('body')
        for field, value in _response_scalar_values(body):
            if value and record.path.rstrip('/').endswith('/' + value) and field.rsplit('.', 1)[-1] == 'id':
                anchors.add(tuple(_static_route(record.path.rstrip('/')[:-(len(value) + 1)])))
    return anchors


def _has_authentication_response(summary: dict[str, Any]) -> bool:
    from .workspace_verification import _browser_credential_key

    observed = summary.get('observed_response') or {}
    if any(str(hint.get('name', '')).lower() == 'set-cookie' for hint in observed.get('auth_hints') or []):
        return True
    def credential_field(value):
        if isinstance(value, dict):
            return any(
                (_browser_credential_key(key) and item not in (None, '', [], {})) or credential_field(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(credential_field(item) for item in value)
        return False
    return credential_field(observed.get('body'))


def _lookup(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return None


def _record_parts(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    request = raw.get('request') if isinstance(raw.get('request'), dict) else raw
    response = raw.get('response') if isinstance(raw.get('response'), dict) else {}
    return request, response


def _public_record(
    task: BrowserDiscoveryTask, raw: dict[str, Any], *, sequence: int, raw_line: int,
    resolved_origins: set[str] | None = None,
) -> dict[str, Any]:
    request, response = _record_parts(raw)
    request_url = _lookup(request, 'url', 'request_url')
    parts = urlsplit(request_url) if isinstance(request_url, str) else None
    origin = f'{parts.scheme.lower()}://{parts.netloc.lower()}' if parts and parts.scheme and parts.netloc else str(request.get('origin') or '')[:500]
    method = str(_lookup(request, 'method') or '').upper()[:10]
    path = (parts.path or '/')[:1000] if parts else str(request.get('path') or '')[:1000]
    resource_type = str(_lookup(request, 'resource_type', 'resourceType') or '')[:80]
    request_body = raw.get('request_body') if isinstance(raw.get('request_body'), dict) else {}
    request_headers = request.get('request_headers') or request.get('headers')
    request_body_kind = str(request_body.get('body', {}).get('kind') or '').lower() if isinstance(request_body.get('body'), dict) else ''
    kind_content_type = {'json': 'application/json', 'form': 'application/x-www-form-urlencoded', 'urlencoded': 'application/x-www-form-urlencoded'}
    content_type = str(_lookup(request, 'content_type', 'contentType') or _header(request_headers, 'content-type') or kind_content_type.get(request_body_kind, ''))[:200]
    content_type_base = content_type.lower().split(';', 1)[0].strip()
    statuses = [
        str(value) for value in (
            request.get('capture_status'), request_body.get('capture_status'), response.get('capture_status'),
            raw.get('terminal_capture_status'),
        ) if value
    ]
    body_sizes = [
        body.get('bytes') for body in (request_body.get('body'), response.get('body'))
        if isinstance(body, dict) and isinstance(body.get('bytes'), int)
    ]
    incomplete = any(status in {'metadata_only', 'not_captured', 'network_failed', 'network_incomplete'} for status in statuses) or any(
        size > int((task.limits or discovery_limits()).get('max_body_bytes', discovery_limits()['max_body_bytes']))
        for size in body_sizes
    ) or any(bool(_lookup(raw, flag)) for flag in (
        'request_body_missing', 'response_body_missing', 'request_body_truncated', 'response_body_truncated',
        'body_read_error', 'network_failed', 'failed',
    )) or isinstance(raw.get('failure'), dict) or not isinstance(response.get('status'), int) or any(
        bool(body.get('truncated') or body.get('parse_error')) for body in (request_body.get('body'), response.get('body')) if isinstance(body, dict)
    ) or any(request.get(flag) for flag in ('query_truncated', 'url_truncated', 'metadata_truncated'))
    declared_length = _header(request_headers, 'content-length')
    if declared_length.isdigit() and int(declared_length) > 0 and not request_body:
        incomplete = True
    excluded_content = str(response.get('reason') or request.get('reason') or '') in {
        'unsupported_content_type', 'binary_content_type', 'streaming_content_type', 'websocket',
    }
    if resolved_origins is None:
        state = read_origin_state(task)
        resolved_origins = set(state['resolved_origins']) if _origin_mode(task) == 'auto' else {task.api_origin}
    source_authorized = origin in resolved_origins
    selected_origin = bool(task.api_origin and origin == task.api_origin)
    eligible = bool(
        source_authorized and selected_origin and method and path and resource_type not in _STATIC_RESOURCE_TYPES
        and (not content_type_base or content_type_base in _SUPPORTED_REQUEST_TYPES)
        and not incomplete
    )
    exclusion = ''
    if not source_authorized:
        exclusion = 'origin_not_resolved' if _origin_mode(task) == 'auto' else 'origin_not_selected'
    elif not selected_origin:
        exclusion = 'origin_not_selected'
    elif resource_type in _STATIC_RESOURCE_TYPES:
        exclusion = 'static_resource'
    elif excluded_content:
        exclusion = 'unsupported_content_type'
    elif incomplete:
        exclusion = 'capture_incomplete'
    elif content_type_base and content_type_base not in _SUPPORTED_REQUEST_TYPES:
        exclusion = 'unsupported_content_type'
    elif not method or not path:
        exclusion = 'invalid_request_metadata'
    terminal = response if response else (raw.get('failure') if isinstance(raw.get('failure'), dict) else {})
    status_code = _lookup(response, 'status', 'status_code')
    status_code = status_code if isinstance(status_code, int) and 100 <= status_code <= 999 else None
    captured_at = _lookup(request, 'captured_at', 'timestamp', 'time')
    captured_at = parse_datetime(captured_at) if isinstance(captured_at, str) else None
    dependency_ids = _lookup(raw, 'dependency_record_ids', 'dependencyRecordIds') or []
    dependency_ids = [item for item in dependency_ids if isinstance(item, int) and item > 0][:50]
    summary: dict[str, Any] = {
        'sequence': sequence,
        'request_id': str(_lookup(request, 'request_id', 'requestId') or '')[:200],
        'method': method,
        'origin': origin,
        'path': path,
        'resource_type': resource_type,
        'status_code': status_code,
        'content_type': content_type_base,
        'capture_complete': not incomplete,
        'source_authorized': source_authorized,
        'capture_reason': str(terminal.get('reason') or '')[:200],
        'association': _redact(request.get('operation_association') or _lookup(raw, 'association', 'action') or {}),
    }
    # A collector-unresolved origin is intentionally metadata-only.  It must
    # not disclose a query/body sample just because a trace line names it.
    if source_authorized:
        request_body_value = request_body.get('body', {}).get('value') if isinstance(request_body.get('body'), dict) else None
        summary['observed_request'] = {
            'query': _pairs(request.get('query'), parts.query if parts else ''),
            'headers': _header_value(request_headers),
            'auth_hints': _auth_hints(request.get('authentication_headers') or request_headers),
            'json': _body_value(request_body_value if request_body_value is not None else _lookup(request, 'json', 'post_data_json')) if content_type_base == 'application/json' else None,
            'form': _pairs(request_body_value if request_body_value is not None else _lookup(request, 'form', 'data', 'post_data')) if content_type_base == 'application/x-www-form-urlencoded' else None,
        }
        summary['observed_response'] = {
            'headers': _header_value(response.get('response_headers') or response.get('headers')),
            'auth_hints': _auth_hints(response.get('authentication_headers')),
            'body': _body_value(
                response.get('body', {}).get('value') if isinstance(response.get('body'), dict) and 'value' in response['body']
                else _lookup(response, 'json', 'body')
            ),
        }
    return {
        'task': task, 'sequence': sequence, 'request_id': summary['request_id'], 'captured_at': captured_at,
        'origin': origin, 'method': method, 'path': path, 'resource_type': resource_type,
        'status_code': status_code, 'content_type': content_type_base, 'is_eligible': eligible,
        'exclusion_reason': exclusion, 'dependency_record_ids': dependency_ids,
        'public_summary': summary, 'raw_line': raw_line,
    }


def ingest_trace(task: BrowserDiscoveryTask) -> dict[str, int]:
    """Index at most the configured JSONL evidence budget without exposing raw lines."""
    trace_file = task_trace_file(task)
    if not trace_file.exists() or not trace_file.is_file():
        return {'ingested': 0, 'invalid': 0, 'total_lines': 0, 'over_limit': 0, 'pending': 0, 'truncated': 0}
    limits = task.limits if isinstance(task.limits, dict) else discovery_limits()
    defaults = discovery_limits()
    max_bytes = min(int(limits.get('max_total_bytes') or defaults['max_total_bytes']), defaults['max_total_bytes'])
    max_records = min(int(limits.get('max_requests') or defaults['max_requests']), defaults['max_requests'])
    state = sync_auto_origin(task)
    resolved_origins = set(state['resolved_origins']) if _origin_mode(task) == 'auto' else {task.api_origin}
    existing = set(BrowserDiscoveryRecord.objects.filter(task=task).values_list('sequence', flat=True))
    pending: dict[str, dict[str, Any]] = {}
    rows, invalid, total_lines, over_limit, consumed = [], 0, 0, 0, 0
    truncated = int(trace_file.stat().st_size > max_bytes)
    with trace_file.open('rb') as stream:
        raw_line = 0
        while consumed < max_bytes:
            remaining = max_bytes - consumed
            raw_bytes = stream.readline(remaining + 1)
            if not raw_bytes:
                break
            if len(raw_bytes) > remaining:
                truncated = 1
                break
            consumed += len(raw_bytes)
            raw_line += 1
            total_lines += 1
            try:
                payload = json.loads(raw_bytes.decode('utf-8'))
            except (TypeError, UnicodeDecodeError, ValueError):
                invalid += 1
                continue
            if not isinstance(payload, dict):
                invalid += 1
                continue
            if payload.get('protocol_version') != 1:
                invalid += 1
                continue
            event = payload.get('event')
            request_id = payload.get('request_id')
            if event in {'capture_started', 'origin_resolved', 'origin_pending', 'origin_rejected'}:
                continue
            if event == 'capture_limit':
                over_limit += 1
                continue
            if event not in {'request', 'request_body', 'response', 'failure'} or not isinstance(request_id, str) or not request_id:
                invalid += 1
                continue
            if event == 'request':
                pending[request_id] = {'request': payload, 'request_line': raw_line}
                continue
            item = pending.get(request_id)
            if item is None:
                # A checkpoint may see only an append suffix after a process
                # restart. It is safer to wait for its request line than to
                # invent a request from a body/response event.
                continue
            if event == 'request_body':
                item['request_body'] = payload
                continue
            if event == 'response':
                item['response'] = payload
            else:
                item['failure'] = payload
                item['terminal_capture_status'] = payload.get('capture_status') or 'network_failed'
            sequence = item['request'].get('request_sequence')
            if not isinstance(sequence, int) or sequence <= 0:
                invalid += 1
                pending.pop(request_id, None)
                continue
            if sequence in existing:
                pending.pop(request_id, None)
                continue
            if sequence > max_records:
                over_limit += 1
                pending.pop(request_id, None)
                continue
            rows.append(BrowserDiscoveryRecord(**_public_record(
                task, item, sequence=sequence, raw_line=item['request_line'], resolved_origins=resolved_origins,
            )))
            pending.pop(request_id, None)
    if rows:
        BrowserDiscoveryRecord.objects.bulk_create(rows, ignore_conflicts=True)
        _refresh_dependency_candidates(task)
    _refresh_selected_record_eligibility(task)
    count = BrowserDiscoveryRecord.objects.filter(task=task).count()
    BrowserDiscoveryTask.objects.filter(pk=task.pk).update(request_count=count)
    return {
        'ingested': len(rows), 'invalid': invalid, 'total_lines': total_lines,
        'over_limit': over_limit, 'pending': len(pending), 'truncated': truncated,
    }


def evidence_statistics(task: BrowserDiscoveryTask, *, ingest_result: dict[str, int] | None = None) -> dict[str, int]:
    """Separate usable evidence from deliberately excluded/incomplete events."""
    records = list(BrowserDiscoveryRecord.objects.filter(task=task).only('origin', 'is_eligible', 'exclusion_reason', 'public_summary'))
    eligible = sum(1 for record in records if record.is_eligible)
    resolved_records = sum(
        1 for record in records
        if isinstance(record.public_summary, dict) and record.public_summary.get('source_authorized') is True
    )
    def relevant(record: BrowserDiscoveryRecord) -> bool:
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        if summary.get('source_authorized') is True:
            return record.origin == task.api_origin
        # Pre-auto-mode/manual records have no source marker.  Preserve their
        # established completeness semantics rather than treating them as a
        # new cross-origin source.
        return _origin_mode(task) == 'manual'

    incomplete_reasons = {'capture_incomplete', 'invalid_request_metadata'}
    incomplete = sum(
        1 for record in records
        if relevant(record) and record.exclusion_reason in incomplete_reasons
        or (isinstance(record.public_summary, dict) and record.public_summary.get('capture_complete') is False
            and relevant(record)
            and record.exclusion_reason not in {'static_resource', 'unsupported_content_type', 'origin_not_selected'})
    )
    values = ingest_result or {}
    return {
        'records': len(records), 'usable': eligible, 'resolved_records': resolved_records, 'excluded': len(records) - eligible,
        'incomplete': incomplete, 'pending': int(values.get('pending') or 0),
        'invalid_lines': int(values.get('invalid') or 0), 'over_limit': int(values.get('over_limit') or 0),
        'truncated': int(values.get('truncated') or 0),
    }


def _refresh_dependency_candidates(task: BrowserDiscoveryTask) -> None:
    """Persist conservative, observable dependency candidates in sequence order."""
    values_to_records: dict[str, list[tuple[BrowserDiscoveryRecord, str]]] = defaultdict(list)
    records = list(BrowserDiscoveryRecord.objects.filter(task=task, is_eligible=True).order_by('sequence', 'id'))
    anchors = _identity_route_anchors(records)
    authentication_sources = []
    for record in records:
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        candidates = []
        if (summary.get('observed_request') or {}).get('auth_hints'):
            candidates.extend({
                'record_id': source.id,
                'reason': '本请求携带认证信息，前序响应提供认证字段/会话 Cookie；可能的认证入口，需核对',
                'confidence': 'candidate', 'kind': 'authentication',
            } for source in authentication_sources[-5:])
        bindings_by_location: dict[str, list[tuple[BrowserDiscoveryRecord, str]]] = defaultdict(list)
        for location, value in _request_scalar_fields(summary):
            value_in_path = value in {segment for segment in record.path.split('/') if segment}
            sources = [
                item for item in values_to_records.get(value, [])
                if item[0].id != record.id and _same_resource_lineage(item[0].path, record.path, anchors=anchors)
                and (not value_in_path or _resource_identity_field(item[1], item[0].path, record.path))
            ]
            if not sources:
                continue
            # A direct ID from a mutating response is stronger evidence than
            # a later list/read echo of the same value. It remains a candidate.
            def rank(item):
                source, field = item
                return (1 if source.method == 'GET' else 0, 1 if '[' in field else 0, -source.sequence)
            for source, source_path in sorted(sources, key=rank)[:20]:
                candidates.append({
                    'record_id': source.id,
                    'reason': f'响应字段 {source_path} 与同资源路由后续请求值精确匹配',
                    'confidence': 'candidate',
                })
                if (
                    location.startswith(('json.', 'params.', 'data.'))
                    and _resource_identity_field(location, source.path, record.path)
                    and _resource_identity_field(source_path, source.path, record.path)
                ):
                    bindings_by_location[location].append((source, source_path))
        ids = list(dict.fromkeys(item['record_id'] for item in candidates))[:50]
        summary['dependency_candidates'] = candidates[:50]
        summary['dependency_bindings'] = [
            {
                'location': location,
                'sources': [
                    {'record_id': source_id, 'method': source.method, 'path': source.path, 'field': field}
                    for (source_id, field), source in sorted(
                        {(source.id, field): source for source, field in sources}.items(),
                        key=lambda item: (item[1].sequence, item[0][1]),
                    )
                ],
                'reason': '同资源前序主键响应与本请求身份字段精确匹配',
            }
            for location, sources in bindings_by_location.items() if sources
        ][:50]
        if record.dependency_record_ids != ids or record.public_summary != summary:
            record.dependency_record_ids = ids
            record.public_summary = summary
            record.save(update_fields=['dependency_record_ids', 'public_summary'])
        response = summary.get('observed_response') if isinstance(summary.get('observed_response'), dict) else {}
        for path, value in _response_scalar_values(response.get('body')):
            values_to_records[value].append((record, path))
        if _has_authentication_response(summary):
            authentication_sources.append(record)


def serialize_task(task: BrowserDiscoveryTask) -> dict[str, Any]:
    counts = task.records.aggregate(records_count=Count('id'), eligible_records_count=Count('id', filter=Q(is_eligible=True)))
    return {
        'id': str(task.id), 'task_id': task.task_id, 'project_id': task.project_id, 'model_id': task.model_id,
        'target_url': task.target_url, 'description': task.description, 'api_origin': task.api_origin or None,
        'allow_test_data_writes': task.allow_test_data_writes, 'exploration_timeout_seconds': task.exploration_timeout_seconds,
        'limits': deepcopy(task.limits), 'status': task.status, 'version': task.version,
        'cancellation_requested': task.cancellation_requested, 'current_action': task.current_action,
        'tool_calls': task.tool_calls, 'model_calls': task.model_calls, 'request_count': task.request_count,
        'summary': task.summary, 'error_code': task.error_code, 'error_message': task.error_message,
        'evidence_summary': deepcopy(task.evidence_summary) if isinstance(task.evidence_summary, dict) else {},
        'origin_resolution': origin_resolution(task),
        'source_version': task.source_version, 'records_count': counts['records_count'],
        'eligible_records_count': counts['eligible_records_count'],
        'started_at': task.started_at.isoformat() if task.started_at else None,
        'heartbeat_at': task.heartbeat_at.isoformat() if task.heartbeat_at else None,
        'finished_at': task.finished_at.isoformat() if task.finished_at else None,
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'updated_at': task.updated_at.isoformat() if task.updated_at else None,
    }


def _selection(task: BrowserDiscoveryTask, record_ids: list[int]) -> tuple[list[BrowserDiscoveryRecord], str]:
    records = list(BrowserDiscoveryRecord.objects.filter(task=task, id__in=record_ids, is_eligible=True).order_by('sequence'))
    if len(records) != len(record_ids):
        raise WorkspaceValidationError('所选记录必须全部属于当前任务且为完整、已确认 origin 的可生成证据。')
    if any(record.origin and record.origin != task.api_origin for record in records):
        raise WorkspaceValidationError('工作区只能交接已选择主接口来源的记录，不能混合不同 origin。')
    by_id = {record.id: record for record in records}
    pending = list(records)
    while pending:
        item = pending.pop()
        dependency_ids = [value for value in item.dependency_record_ids if isinstance(value, int)]
        missing_ids = [value for value in dependency_ids if value not in by_id]
        if not missing_ids:
            continue
        dependencies = list(BrowserDiscoveryRecord.objects.filter(task=task, id__in=missing_ids, is_eligible=True).order_by('sequence'))
        if len(dependencies) != len(missing_ids):
            raise WorkspaceValidationError('所选记录包含不可用的依赖候选，不能生成可独立运行的工作区。')
        for dependency in dependencies:
            if dependency.origin and dependency.origin != task.api_origin:
                raise WorkspaceValidationError('所选记录依赖其他 origin，当前单 base URL 工作区不能假装为完整跨源流程。')
            if dependency.id not in by_id:
                by_id[dependency.id] = dependency
                pending.append(dependency)
    selected = sorted(by_id.values(), key=lambda item: (item.sequence, item.id))
    groups = {(item.method, item.path) for item in selected}
    if len(groups) > 50:
        raise WorkspaceValidationError('所选接口及其依赖超过 50 个接口组，请缩小范围。')
    selection_hash = hashlib.sha256(','.join(str(item.id) for item in selected).encode()).hexdigest()
    return selected, selection_hash


def _endpoint_contract(samples: list[BrowserDiscoveryRecord]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    query_names: dict[str, dict[str, Any]] = {}
    request_content: dict[str, Any] = {}
    responses: dict[str, Any] = {}
    for sample in samples:
        summary = sample.public_summary if isinstance(sample.public_summary, dict) else {}
        observed = summary.get('observed_request') if isinstance(summary.get('observed_request'), dict) else {}
        for query in observed.get('query') or []:
            name = query.get('name') if isinstance(query, dict) else None
            if isinstance(name, str):
                query_names.setdefault(name, {
                    'name': name, 'in': 'query', 'x-platform-observed': True,
                    'x-platform-required': 'unknown',
                })
        if sample.content_type in _SUPPORTED_REQUEST_TYPES:
            request_content.setdefault(sample.content_type, {'x-platform-observed': True})
        if sample.status_code:
            responses.setdefault(str(sample.status_code), {'description': 'Observed browser response', 'x-platform-observed': True})
    request_body = {}
    if request_content:
        request_body = {'content': request_content, 'x-platform-observed': True, 'x-platform-required': 'unknown'}
    return list(query_names.values()), request_body, responses


def _path_template(record: BrowserDiscoveryRecord, records_by_id: dict[int, BrowserDiscoveryRecord]) -> dict[str, Any] | None:
    """Template only a segment backed by one earlier response value match."""
    segments = record.path.split('/')
    slots = []
    anchors = _identity_route_anchors(records_by_id.values())
    for index, segment in enumerate(segments):
        if not segment:
            continue
        matches = []
        for dependency_id in record.dependency_record_ids:
            source = records_by_id.get(dependency_id)
            if not source or source.sequence >= record.sequence or not _same_resource_lineage(source.path, record.path, anchors=anchors):
                continue
            source_summary = source.public_summary if isinstance(source.public_summary, dict) else {}
            response = source_summary.get('observed_response') if isinstance(source_summary.get('observed_response'), dict) else {}
            for field, value in _response_scalar_values(response.get('body')):
                if value == segment and _resource_identity_field(field, source.path, record.path):
                    matches.append((source, field))
        if not matches:
            continue
        # Several mutations of the same object can echo its ID. Keep every
        # observed equivalent source so a new run may use the original create
        # extraction rather than requiring an artificial re-extract on PATCH.
        by_source = {(source.id, field): source for source, field in matches}
        ordered = sorted(by_source.items(), key=lambda item: (item[1].sequence, item[0][1]))
        field_names = {field.rsplit('.', 1)[-1] for (_, field), _source in ordered}
        if len(field_names) != 1:
            # The same literal may be an object id and a parent/tenant id.
            # Keep it observed, but do not guess which semantic slot is safe.
            continue
        (source_id, field), source = ordered[0]
        safe_field = re.sub(r'[^A-Za-z0-9_]+', '_', field).strip('_') or 'value'
        variable = f'browser_dep_{source.id}_{safe_field}'
        sources = [
            {
                'record_id': candidate.id, 'method': candidate.method, 'path': candidate.path,
                'field': candidate_field,
            }
            for (candidate_id, candidate_field), candidate in ordered
        ]
        slots.append({
            'segment_index': index, 'variable': variable, 'source_record_id': source.id,
            'source_method': source.method, 'source_path': source.path, 'source_field': field,
            'sources': sources,
            'reason': '同资源路由中前序 ID 响应字段与该 path segment 精确匹配',
        })
        segments[index] = '${' + variable + '}'
    if not slots:
        return None
    return {'exact_path': record.path, 'template': '/'.join(segments) or '/', 'slots': slots}


def handoff_to_workspace(*, task: BrowserDiscoveryTask, owner, version: int, record_ids: list[int]) -> tuple[BrowserDiscoveryHandoff, bool]:
    if task.owner_id != owner.id:
        raise LookupError('探索任务不存在。')
    if task.version != version:
        raise WorkspaceConflict('探索任务版本已变化，请刷新后重新选择记录。')
    if task.status not in {BrowserDiscoveryTask.Status.COMPLETED, BrowserDiscoveryTask.Status.PARTIAL}:
        raise WorkspaceValidationError('仅已结束且有证据的探索任务可以交接工作区。')
    if not task.api_origin:
        raise WorkspaceValidationError('未确认 api_origin 的记录不能交接为可执行 API 资产。')
    if origin_resolution(task)['state'] == 'awaiting_selection':
        raise WorkspaceValidationError('多个已采集来源尚未选择本次工作区的主接口来源，不能混合交接。')
    if not can_edit_project(task.project, owner) or not can_execute_project(task.project, owner):
        raise PermissionError('没有权限探索或交接此项目。')
    selected, selection_hash = _selection(task, record_ids)
    source_version = task.source_version or task.version
    with transaction.atomic():
        existing = BrowserDiscoveryHandoff.objects.select_related('spec', 'workspace').filter(
            task=task, source_version=source_version, selection_hash=selection_hash,
        ).first()
        if existing and existing.workspace_id:
            return existing, False
        grouped: dict[tuple[str, str], list[BrowserDiscoveryRecord]] = defaultdict(list)
        for item in selected:
            grouped[(item.method, item.path)].append(item)
        records_by_id = {item.id: item for item in selected}
        observed_samples = []
        for item in selected:
            sample = deepcopy(item.public_summary)
            template = _path_template(item, records_by_id)
            if template:
                sample['path_template'] = template
            observed_samples.append(sample)
        metadata = {
            'servers': [{'url': task.api_origin}],
            'browser_capture': {
                'task_id': task.task_id, 'task_version': task.version, 'source_version': source_version,
                'api_origin': task.api_origin, 'selected_record_ids': [item.id for item in selected],
                'observed_samples': observed_samples,
                'dependency_candidates': {
                    str(item.id): list(item.dependency_record_ids) for item in selected if item.dependency_record_ids
                },
                'inference_notice': '字段必填性、schema、路径参数和权限规则均未知；仅保存已观察样本。',
            },
        }
        if existing:
            spec = existing.spec
        else:
            spec = APISpecification.objects.create(
                project=task.project, created_by=owner, spec_name=f'网页探索发现 {task.task_id[:8]}',
                description='由已确认 API origin 的浏览器观察样本生成；不是完整接口契约。',
                spec_type=APISpecification.SpecType.BROWSER_CAPTURE,
                status=APISpecification.TaskStatus.COMPLETED, metadata=metadata,
                source_task=task, source_version=source_version, source_selection_key=selection_hash,
            )
            endpoints = []
            for index, ((method, path), samples) in enumerate(grouped.items()):
                parameters, request_body, responses = _endpoint_contract(samples)
                endpoints.append(APIEndpoint(
                    spec=spec, method=method, path=path, sort_order=index,
                    summary=f'Observed {method} {path}',
                    description='浏览器观察样本；未知参数必填性、schema 和路径参数语义。',
                    parameters=parameters, request_body=request_body, responses=responses,
                    tags=['browser_capture'], operation_id='',
                ))
            APIEndpoint.objects.bulk_create(endpoints)
        endpoint_ids = list(APIEndpoint.objects.filter(spec=spec).order_by('sort_order', 'id').values_list('id', flat=True))
        draft = default_api_workspace_draft()
        draft['config']['base_url'] = task.api_origin
        workspace = APIWorkspace.objects.create(
            project=task.project, owner=owner, spec=spec, model_id=task.model_id,
            title=f'网页探索工作区 {task.task_id[:8]}', endpoint_ids=endpoint_ids,
            draft=draft,
            messages=[{'role': 'user', 'content': task.description, 'source': 'browser_capture'}],
            generation={'source': {'type': 'browser_capture', 'task_id': task.task_id, 'source_version': source_version,
                                   'selection_key': selection_hash}},
        )
        if existing:
            existing.workspace = workspace
            existing.save(update_fields=['workspace'])
            handoff = existing
        else:
            handoff = BrowserDiscoveryHandoff.objects.create(
                task=task, source_version=source_version, selection_hash=selection_hash,
                selected_record_ids=[item.id for item in selected], spec=spec, workspace=workspace,
            )
    return handoff, True


def browser_capture_context(endpoint: APIEndpoint) -> dict[str, Any] | None:
    """Small generation-only context; raw JSONL and credentials never leave disk."""
    metadata = endpoint.spec.metadata if isinstance(endpoint.spec.metadata, dict) else {}
    source = metadata.get('browser_capture')
    if not isinstance(source, dict):
        return None
    samples = source.get('observed_samples') if isinstance(source.get('observed_samples'), list) else []
    matching = [sample for sample in samples if isinstance(sample, dict) and sample.get('method') == endpoint.method and sample.get('path') == endpoint.path]
    return {
        'source_type': 'browser_capture', 'api_origin': source.get('api_origin'),
        'observed_samples': deepcopy(matching[:20]),
        'dependency_candidates': deepcopy(source.get('dependency_candidates') or {}),
        'inference_notice': source.get('inference_notice', ''),
    }


def resolve_browser_discovery_mcp_config(owner_id: int) -> dict[str, Any]:
    """Return a Playwright-only, pinned stdio config without leaking configuration secrets."""
    from web_testing.generation_preflight import _executeautomation_package_spec, resolve_active_playwright_mcp_config

    selected = resolve_active_playwright_mcp_config(owner_id)
    if not selected:
        raise WorkspaceValidationError('没有可用的 Playwright MCP 配置。')
    _, raw = selected
    playwright = (raw.get('mcpServers') or {}).get('playwright') if isinstance(raw, dict) else None
    if not isinstance(playwright, dict):
        raise WorkspaceValidationError('MCP 配置缺少可用的 Playwright 服务。')
    command, args = playwright.get('command'), playwright.get('args')
    if not isinstance(command, str) or os.path.basename(command) != 'npx':
        raise WorkspaceValidationError('浏览器探索仅支持固定 Playwright MCP 的 npx stdio 配置。')
    try:
        parsed = _executeautomation_package_spec(args)
    except ValueError as exc:
        raise WorkspaceValidationError(str(exc)) from exc
    if not parsed or parsed[1] != _PINNED_PLAYWRIGHT_PACKAGE:
        raise WorkspaceValidationError('浏览器探索要求固定 @executeautomation/playwright-mcp-server@1.0.12。')
    return {'mcpServers': {'playwright': deepcopy(playwright)}}
