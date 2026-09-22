"""Performance-project browser discovery reuse and deterministic draft compilation.

The browser/MCP runner and evidence tables remain owned by ``api_testing``.
This module adds only the performance-project claim boundary and a rule-based
compiler; it never publishes API specifications or workspaces.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from celery import shared_task
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from api_testing.browser_discovery import (
    _path_template,
    _request_scalar_fields,
    _response_scalar_values,
    normalize_http_url,
    origin_resolution,
    resolve_browser_discovery_mcp_config,
    serialize_task,
    task_trace_dir,
    task_trace_file,
)
from api_testing.models import BrowserDiscoveryRecord, BrowserDiscoveryTask
from api_testing.workspace_service import WorkspaceConflict, WorkspaceValidationError, require_generation_model_id
from projects.access import EXECUTE, get_project_for_user


logger = logging.getLogger(__name__)

_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SELECTOR = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])*$'
)
_VARIABLE_PART = re.compile(r'[^A-Za-z0-9_]+')
_REDACTED = '<redacted>'


def retry_decision(task: BrowserDiscoveryTask) -> dict[str, Any]:
    """Expose only retries that cannot replay already-observed browser work."""
    if isinstance(task.limits, dict) and task.limits.get('retry_task_id'):
        return {'available': False, 'reason': '该失败任务已经提交过重试，请查看新的探索任务。'}
    if task.status not in {BrowserDiscoveryTask.Status.FAILED, BrowserDiscoveryTask.Status.PARTIAL}:
        return {'available': False, 'reason': '仅失败或部分完成的探索任务可能重试。'}
    if task.error_code == 'queue_unavailable':
        return {'available': True, 'reason': '任务未进入执行队列，可以安全重试。'}
    diagnostic = task.evidence_summary.get('diagnostic') if isinstance(task.evidence_summary, dict) else None
    if (
        isinstance(diagnostic, dict)
        and diagnostic.get('retryable') is True
        and diagnostic.get('stage') == 'initial_model'
        and task.tool_calls == 0
        and task.request_count == 0
    ):
        return {'available': True, 'reason': '模型在首次浏览器操作前失败，可以安全重试。'}
    if isinstance(diagnostic, dict) and diagnostic.get('retryable') is True:
        return {'available': False, 'reason': '失败发生在探索过程中，可能已产生页面操作，不能自动重放。'}
    return {'available': False, 'reason': '当前诊断不属于可安全自动重试的原因。'}


def serialize_performance_task(task: BrowserDiscoveryTask) -> dict[str, Any]:
    data = serialize_task(task)
    data.pop('handoffs', None)
    data['retry'] = retry_decision(task)
    return data


def dispatch_performance_discovery(task: BrowserDiscoveryTask) -> bool:
    try:
        run_performance_browser_discovery_async.apply_async(
            args=(str(task.id), task.version, task.task_id), task_id=task.task_id,
        )
        return True
    except Exception as exc:
        logger.error(
            'Performance browser discovery queue submission failed: task=%s error_type=%s',
            task.id, type(exc).__name__,
        )
        now = timezone.now()
        BrowserDiscoveryTask.objects.filter(pk=task.pk).update(
            status=BrowserDiscoveryTask.Status.FAILED,
            error_code='queue_unavailable',
            error_message='浏览器探索任务暂时无法进入队列，请稍后重试。',
            current_action='任务提交失败', finished_at=now, heartbeat_at=now,
        )
        task.refresh_from_db()
        return False


def clone_retry_task(task: BrowserDiscoveryTask) -> BrowserDiscoveryTask:
    retried = BrowserDiscoveryTask.objects.create(
        project=task.project,
        owner=task.owner,
        model_id=task.model_id,
        target_url=task.target_url,
        description=task.description,
        api_origin=task.api_origin,
        allow_test_data_writes=task.allow_test_data_writes,
        exploration_timeout_seconds=task.exploration_timeout_seconds,
        limits=deepcopy(task.limits),
        task_id=str(uuid.uuid4()),
    )
    task.limits = {
        **(task.limits if isinstance(task.limits, dict) else {}),
        'retry_task_id': str(retried.id),
    }
    task.save(update_fields=['limits', 'updated_at'])
    return retried


def _claim_performance_discovery(discovery_id: str, version: int, task_id: str):
    from api_testing.browser_discovery import browser_discovery_enabled

    with transaction.atomic():
        task = (
            BrowserDiscoveryTask.objects.select_for_update()
            .select_related('project', 'owner')
            .filter(pk=discovery_id)
            .first()
        )
        if (
            not task or task.version != version or task.task_id != task_id
            or task.status != BrowserDiscoveryTask.Status.QUEUED
            or task.cancellation_requested
        ):
            return None
        failure = ''
        if not browser_discovery_enabled():
            failure = 'feature_disabled'
        elif task.project.project_type != 'perf':
            failure = 'project_not_perf'
        else:
            try:
                get_project_for_user(
                    task.project_id, task.owner, EXECUTE, expected_project_type='perf',
                )
            except (Http404, PermissionDenied):
                failure = 'permission_revoked'
        if failure:
            now = timezone.now()
            task.status = BrowserDiscoveryTask.Status.FAILED
            task.error_code = failure
            task.error_message = '任务开始前的功能、项目类型或所有者权限校验未通过。'
            task.current_action = '启动前校验未通过'
            task.finished_at = task.heartbeat_at = now
            task.save()
            return None
        task.status = BrowserDiscoveryTask.Status.RUNNING
        task.current_action = '正在启动浏览器探索'
        task.started_at = task.heartbeat_at = timezone.now()
        task.save(update_fields=['status', 'current_action', 'started_at', 'heartbeat_at', 'updated_at'])
        return task


@shared_task(bind=True, name='performance_testing.discovery.run_performance_browser_discovery_async')
def run_performance_browser_discovery_async(self, discovery_id: str, version: int, task_id: str):
    """Performance claim boundary around the shared browser runner/lifecycle."""
    from ai_core.model_manager import get_llm_manager
    from api_testing.browser_discovery_agent import run_browser_discovery
    from api_testing.tasks import (
        _browser_discovery_cancelled,
        _browser_discovery_checkpoint,
        _browser_discovery_origin_pending,
        _finish_browser_discovery,
    )

    task = _claim_performance_discovery(discovery_id, version, task_id)
    if task is None:
        return {'status': 'ignored'}
    result: dict[str, Any] = {}
    try:
        require_generation_model_id(task.model_id, owner=task.owner)
        if not task.allow_test_data_writes:
            raise ValueError('浏览器探索缺少测试数据写入确认。')
        manager = get_llm_manager(task.model_id)
        llm_model = manager.current_llm
        if llm_model is None:
            raise ValueError('所选 LLM 未初始化。')
        mcp_config = resolve_browser_discovery_mcp_config(task.owner_id)
        trace_dir = task_trace_dir(task)
        trace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        capture_limits = {
            'capture_all_headers': True,
            'max_requests': task.limits['max_requests'],
            'max_body_bytes': task.limits['max_body_bytes'],
            'max_total_body_bytes': task.limits['max_total_bytes'],
        }
        result = asyncio.run(run_browser_discovery(
            llm_model=llm_model,
            mcp_config=mcp_config,
            task_id=task.task_id,
            target_url=task.target_url,
            description=task.description,
            api_origin=task.api_origin,
            trace_file=str(task_trace_file(task)),
            timeout_seconds=task.exploration_timeout_seconds,
            max_steps=task.limits['max_model_steps'],
            max_tool_calls=task.limits['max_tool_calls'],
            auto_approve_origins=task.limits.get('auto_approve_origins') is True,
            capture_limits=capture_limits,
            checkpoint=lambda payload: _browser_discovery_checkpoint(discovery_id, version, task_id, payload),
            is_cancelled=lambda: _browser_discovery_cancelled(discovery_id, version, task_id),
            origin_pending=lambda: _browser_discovery_origin_pending(discovery_id, version, task_id),
        ))
        if not isinstance(result, dict):
            raise ValueError('浏览器探索 runner 返回格式无效。')
        _finish_browser_discovery(discovery_id, version, task_id, result)
    except Exception as exc:
        logger.exception('Performance browser discovery failed for %s', discovery_id)
        _finish_browser_discovery(discovery_id, version, task_id, result, exception=exc)
    return {'status': 'finished'}


class _PlaceholderRegistry:
    def __init__(self):
        self.items: list[dict[str, Any]] = []
        self._names: set[str] = set()

    def add(self, record_id: int, location: str, reason: str) -> str:
        base = _VARIABLE_PART.sub('_', f'record_{record_id}_{location}').strip('_').lower()
        if not base or base[0].isdigit():
            base = f'value_{base}'
        name = base[:64]
        suffix = 2
        while name in self._names:
            tail = f'_{suffix}'
            name = f'{base[:64 - len(tail)]}{tail}'
            suffix += 1
        self._names.add(name)
        self.items.append({
            'name': name, 'record_id': record_id, 'location': location,
            'reason': reason,
        })
        return '${' + name + '}'


def _replace_redacted(value: Any, *, record_id: int, location: str, registry: _PlaceholderRegistry) -> Any:
    if value == _REDACTED:
        return registry.add(record_id, location, '采集证据已脱敏，必须由用户填写真实值。')
    if isinstance(value, dict):
        return {
            str(key): _replace_redacted(
                child, record_id=record_id, location=f'{location}.{key}', registry=registry,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_redacted(
                child, record_id=record_id, location=f'{location}[{index}]', registry=registry,
            )
            for index, child in enumerate(value)
        ]
    return deepcopy(value)


def _pairs_to_object(
    pairs: Any, *, record_id: int, location: str, registry: _PlaceholderRegistry,
    warnings: list[str],
) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    for item in pairs if isinstance(pairs, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        name = item['name']
        grouped.setdefault(name, []).append(_replace_redacted(
            item.get('value'), record_id=record_id,
            location=f'{location}.{name}', registry=registry,
        ))
    result = {}
    for name, values in grouped.items():
        result[name] = values[0] if len(values) == 1 else values
        if len(values) > 1:
            warnings.append(
                f'记录 {record_id} 的 {location} 参数 {name} 重复出现，草稿以数组保留全部值；请确认执行器编码符合目标接口。'
            )
    return result


def _json_type(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'list'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, int):
        return 'number'
    if isinstance(value, float):
        return 'number'
    return ''


def _selector_for_response_field(field: str) -> str | None:
    if not isinstance(field, str) or not field or not _SELECTOR.fullmatch(field):
        return None
    return f'body.{field}'


def _set_nested(root: Any, path: str, value: Any) -> bool:
    """Set a simple dotted/list location already proven by persisted evidence."""
    tokens: list[str | int] = []
    for name, index in re.findall(r'([^\.\[\]]+)|\[(\d+)\]', path):
        tokens.append(int(index) if index else name)
    if not tokens:
        return False
    current = root
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False
            current = current[token]
    tail = tokens[-1]
    if isinstance(tail, int):
        if not isinstance(current, list) or tail >= len(current):
            return False
        current[tail] = value
        return True
    if not isinstance(current, dict) or tail not in current:
        return False
    current[tail] = value
    return True


def _request_from_record(
    record: BrowserDiscoveryRecord, registry: _PlaceholderRegistry, warnings: list[str],
) -> dict[str, Any]:
    summary = record.public_summary if isinstance(record.public_summary, dict) else {}
    observed = summary.get('observed_request') if isinstance(summary.get('observed_request'), dict) else {}
    query = _pairs_to_object(
        observed.get('query'), record_id=record.id, location='query', registry=registry, warnings=warnings,
    )
    headers = deepcopy(observed.get('headers')) if isinstance(observed.get('headers'), dict) else {}
    from .serializers import _FORBIDDEN_HEADERS
    # Preserve every observed header in evidence, but let the HTTP client build
    # framing and negotiate its supported compression for the rendered request.
    managed = _FORBIDDEN_HEADERS | {'accept-encoding', 'keep-alive', 'te', 'trailer', 'upgrade'}
    connection = next((str(value) for name, value in headers.items() if name.lower() == 'connection'), '')
    managed |= {name.strip().lower() for name in connection.split(',') if name.strip()}
    omitted = [name for name in headers if name.lower() in managed or name.startswith(':')]
    for name in omitted:
        del headers[name]
    if omitted:
        warnings.append(
            f'记录 {record.id} 的 {", ".join(omitted)} 已完整保留在样本中；'
            '这些传输请求头在执行时由 HTTP 客户端按实际目标、正文和压缩能力生成，不复制进草稿。'
        )
    headers = _replace_redacted(headers, record_id=record.id, location='headers', registry=registry)
    for hint in observed.get('auth_hints') if isinstance(observed.get('auth_hints'), list) else []:
        name = hint.get('name') if isinstance(hint, dict) else None
        if not isinstance(name, str) or not _HTTP_HEADER_NAME.fullmatch(name) or name.lower() in managed:
            continue
        if not any(str(existing).lower() == name.lower() for existing in headers):
            headers[name] = registry.add(
                record.id, f'headers.{name}',
                '采集证据只确认该认证请求头存在，未保留其值或认证方案。',
            )
    content_type = str(record.content_type or '').split(';', 1)[0].lower()
    if content_type == 'application/json':
        body_type = 'json'
        body = _replace_redacted(
            observed.get('json'), record_id=record.id, location='body', registry=registry,
        )
    elif content_type == 'application/x-www-form-urlencoded':
        body_type = 'form'
        body = _pairs_to_object(
            observed.get('form'), record_id=record.id, location='body', registry=registry, warnings=warnings,
        )
    else:
        body_type, body = 'none', None
    return {'query': query, 'headers': headers, 'body_type': body_type, 'body': body}


def _target_origin_and_prefix(base_url: str) -> tuple[str, str]:
    normalized = normalize_http_url(base_url, label='target.base_url')
    parts = urlsplit(normalized)
    if parts.query or parts.fragment:
        raise WorkspaceValidationError('性能目标地址不能包含查询或片段。')
    origin = normalize_http_url(
        f'{parts.scheme}://{parts.netloc}', label='target.origin', origin_only=True,
    )
    prefix = parts.path.rstrip('/')
    return origin, '' if prefix == '' else prefix


def _path_below_target_prefix(path: str, prefix: str) -> str:
    if not prefix:
        return path
    if path == prefix:
        return '/'
    if path.startswith(prefix + '/'):
        return path[len(prefix):]
    raise WorkspaceValidationError(
        f'探索记录路径 {path} 不在性能目标路径前缀 {prefix}/ 内，不能重放到该目标。'
    )


def _exact_selection(
    task: BrowserDiscoveryTask, record_ids: list[int],
) -> tuple[list[BrowserDiscoveryRecord], str, set[int]]:
    """Close only dependencies backed by an exact persisted binding/path match.

    ``dependency_record_ids`` also contains deliberately conservative auth and
    value-match candidates. Those remain visible to the UI, but are not turned
    into executable setup traffic without a precise source selector/location.
    """
    records = list(
        BrowserDiscoveryRecord.objects.filter(task=task, is_eligible=True)
        .order_by('sequence', 'id')
    )
    by_id = {record.id: record for record in records}
    roots = [by_id.get(record_id) for record_id in record_ids]
    if any(record is None for record in roots):
        raise WorkspaceValidationError(
            '所选记录必须全部属于当前任务且为完整、已确认 origin 的可生成证据。'
        )
    if any(record.origin != task.api_origin for record in roots):
        raise WorkspaceValidationError('压测草稿不能混合不同 origin 的探索记录。')

    selected = {record.id: record for record in roots}
    pending = list(roots)
    uncertain_ids: set[int] = set()
    while pending:
        record = pending.pop()
        exact_ids: set[int] = set()
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        request_values = dict(_request_scalar_fields(summary))
        bindings = summary.get('dependency_bindings')
        for binding in bindings if isinstance(bindings, list) else []:
            location = binding.get('location') if isinstance(binding, dict) else None
            if not isinstance(location, str) or location not in request_values:
                continue
            sources = binding.get('sources') if isinstance(binding, dict) else None
            valid_sources = []
            for source_item in sources if isinstance(sources, list) else []:
                source_id = source_item.get('record_id') if isinstance(source_item, dict) else None
                if not isinstance(source_id, int):
                    continue
                source = by_id.get(source_id)
                if source is None:
                    raise WorkspaceValidationError('所选记录引用了其他任务或不可见的依赖来源。')
                if source.origin != task.api_origin:
                    raise WorkspaceValidationError('所选记录依赖其他 origin，不能生成单 origin 压测草稿。')
                if source.sequence >= record.sequence:
                    raise WorkspaceValidationError('探索依赖必须来自同任务、同 origin 的前序可见样本。')
                field = source_item.get('field')
                source_summary = source.public_summary if isinstance(source.public_summary, dict) else {}
                source_response = source_summary.get('observed_response')
                source_body = source_response.get('body') if isinstance(source_response, dict) else None
                source_values = dict(_response_scalar_values(source_body))
                if (
                    _selector_for_response_field(field)
                    and field in source_values
                    and source_values[field] == request_values[location]
                ):
                    valid_sources.append(source)
            if valid_sources:
                source = min(valid_sources, key=lambda item: (item.sequence, item.id))
                exact_ids.add(source.id)

        candidate_map = {candidate_id: by_id[candidate_id] for candidate_id in record.dependency_record_ids if candidate_id in by_id}
        candidate_map[record.id] = record
        template = _path_template(record, candidate_map)
        if template:
            for slot in template.get('slots', []):
                source_id = slot.get('source_record_id')
                source = by_id.get(source_id)
                if not source or source.origin != task.api_origin or source.sequence >= record.sequence:
                    raise WorkspaceValidationError('路径依赖不是同任务、同 origin 的前序可见样本。')
                exact_ids.add(source.id)

        uncertain_ids.update(
            dependency_id for dependency_id in record.dependency_record_ids
            if dependency_id not in exact_ids and dependency_id not in selected
        )
        for dependency_id in exact_ids:
            if dependency_id not in selected:
                selected[dependency_id] = by_id[dependency_id]
                pending.append(by_id[dependency_id])

    ordered = sorted(selected.values(), key=lambda item: (item.sequence, item.id))
    groups = {(record.method, record.path) for record in ordered}
    if len(groups) > 50:
        raise WorkspaceValidationError('所选接口及其确切依赖超过 50 个接口组，请缩小范围。')
    selection_hash = hashlib.sha256(
        ','.join(str(record.id) for record in ordered).encode()
    ).hexdigest()
    return ordered, selection_hash, uncertain_ids


def compile_performance_draft(
    *, task: BrowserDiscoveryTask, version: int, record_ids: list[int], target,
) -> dict[str, Any]:
    """Compile only observed values; unknown credentials remain undefined variables."""
    if task.version != version:
        raise WorkspaceConflict('探索任务版本已变化，请刷新后重新选择记录。')
    if task.status not in {BrowserDiscoveryTask.Status.COMPLETED, BrowserDiscoveryTask.Status.PARTIAL}:
        raise WorkspaceValidationError('仅已结束且有可见证据的探索任务可以生成草稿。')
    if not task.api_origin:
        raise WorkspaceValidationError('探索任务尚未确认 origin，不能生成可执行草稿。')
    if origin_resolution(task)['state'] == 'awaiting_selection':
        raise WorkspaceValidationError('多个已采集 origin 尚未选择，不能生成单 origin 压测草稿。')
    task_origin = normalize_http_url(task.api_origin, label='api_origin', origin_only=True)
    target_origin, target_prefix = _target_origin_and_prefix(target.base_url)
    if task_origin != target_origin:
        raise WorkspaceValidationError('所选性能目标 origin 与探索任务已确认 origin 不一致。')

    requested_ids = set(record_ids)
    selected, _selection_hash, uncertain_dependency_ids = _exact_selection(task, record_ids)
    if any(not record.method or record.status_code is None for record in selected):
        raise WorkspaceValidationError('所选记录缺少真实请求方法或响应状态，不能生成可执行草稿。')
    allowed_methods = {str(item).upper() for item in (target.allowed_methods or [])}
    rejected_methods = sorted({record.method for record in selected if record.method not in allowed_methods})
    if rejected_methods:
        raise WorkspaceValidationError(
            f'所选性能目标未批准以下请求方法：{", ".join(rejected_methods)}。'
        )

    records_by_id = {record.id: record for record in selected}
    for record in selected:
        for dependency_id in record.dependency_record_ids:
            dependency = records_by_id.get(dependency_id)
            if dependency and dependency.sequence >= record.sequence:
                raise WorkspaceValidationError('探索依赖必须来自同任务、同 origin 的前序可见样本。')

    phase_by_id = {
        record.id: ('main' if record.id in requested_ids else 'setup')
        for record in selected
    }
    # An auto-included dependency that itself consumes a user-selected main
    # sample cannot run in setup. Promote it (and its downstream chain) to main
    # rather than producing an invalid setup-after-main data flow.
    for record in selected:
        if phase_by_id[record.id] == 'setup' and any(
            phase_by_id.get(dependency_id) == 'main'
            for dependency_id in record.dependency_record_ids
        ):
            phase_by_id[record.id] = 'main'

    registry = _PlaceholderRegistry()
    warnings: list[str] = []
    if task.status == BrowserDiscoveryTask.Status.PARTIAL:
        warnings.append('探索任务仅部分完成；草稿只使用当前完整、可见且已确认 origin 的样本。')
    if uncertain_dependency_ids:
        warnings.append(
            '部分采集器依赖仅为候选且缺少确切变量绑定，未自动加入可执行步骤；请在样本界面人工确认。'
        )

    steps: list[dict[str, Any]] = []
    step_by_record: dict[int, dict[str, Any]] = {}
    record_id_by_step_identity: dict[int, int] = {}
    sequence_by_record: dict[int, int] = {}
    source_rows: list[dict[str, Any]] = []
    duplicate_names: dict[str, int] = {}
    for record in selected:
        request = _request_from_record(record, registry, warnings)
        base_name = f'{record.method} {record.path}'
        duplicate_names[base_name] = duplicate_names.get(base_name, 0) + 1
        name = base_name if duplicate_names[base_name] == 1 else f'{base_name} 样本 {duplicate_names[base_name]}'
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        observed_response = summary.get('observed_response') if isinstance(summary.get('observed_response'), dict) else {}
        assertions = [{
            'check': 'status_code', 'comparator': 'eq', 'expected': record.status_code,
        }]
        if 'body' in observed_response:
            response_type = _json_type(observed_response.get('body'))
            if response_type:
                assertions.append({'check': 'body', 'comparator': 'type', 'expected': response_type})
        step = {
            'name': name[:200],
            'phase': phase_by_id[record.id],
            'method': record.method,
            'path': _path_below_target_prefix(record.path, target_prefix),
            'query': request['query'],
            'headers': request['headers'],
            'body_type': request['body_type'],
            'body': request['body'],
            'extract': [],
            'assertions': assertions,
        }
        steps.append(step)
        step_by_record[record.id] = step
        record_id_by_step_identity[id(step)] = record.id
        sequence_by_record[record.id] = record.sequence
        source_rows.append({
            'record_id': record.id,
            'sequence': record.sequence,
            'method': record.method,
            'path': record.path,
            'phase': step['phase'],
            'requested': record.id in requested_ids,
        })

    bound_dependencies: set[tuple[int, int]] = set()

    def attach(source: BrowserDiscoveryRecord, field: str, destination: BrowserDiscoveryRecord, location: str) -> str | None:
        selector = _selector_for_response_field(field)
        if not selector:
            warnings.append(
                f'记录 {destination.id} 的依赖来源字段 {field!r} 不能转换为确定性选择器，已保留固定样本值。'
            )
            return None
        variable = _VARIABLE_PART.sub('_', f'discovery_{source.id}_{field}').strip('_').lower()[:64]
        if not variable or variable[0].isdigit():
            variable = f'value_{variable}'
        extraction = {'name': variable, 'check': selector}
        if extraction not in step_by_record[source.id]['extract']:
            step_by_record[source.id]['extract'].append(extraction)
        bound_dependencies.add((source.id, destination.id))
        return '${' + variable + '}'

    for record in selected:
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        for binding in summary.get('dependency_bindings') if isinstance(summary.get('dependency_bindings'), list) else []:
            if not isinstance(binding, dict) or not isinstance(binding.get('location'), str):
                continue
            candidates = []
            for item in binding.get('sources') if isinstance(binding.get('sources'), list) else []:
                source = records_by_id.get(item.get('record_id')) if isinstance(item, dict) else None
                field = item.get('field') if isinstance(item, dict) else None
                if source and source.sequence < record.sequence and source.origin == record.origin and isinstance(field, str):
                    candidates.append((source, field))
            if not candidates:
                continue
            source, field = sorted(candidates, key=lambda item: (item[0].sequence, item[0].id))[0]
            placeholder = attach(source, field, record, binding['location'])
            if not placeholder:
                continue
            prefix, _, nested = binding['location'].partition('.')
            target_value = step_by_record[record.id].get({'params': 'query', 'json': 'body', 'data': 'body'}.get(prefix, ''))
            if not nested or not _set_nested(target_value, nested, placeholder):
                warnings.append(
                    f'记录 {record.id} 的依赖位置 {binding["location"]!r} 与当前可见样本不一致，已保留固定样本值。'
                )

        template = _path_template(record, records_by_id)
        if template:
            rendered_path = record.path
            valid = True
            for slot in template.get('slots', []):
                source = records_by_id.get(slot.get('source_record_id'))
                field = slot.get('source_field')
                if not source or source.origin != record.origin or source.sequence >= record.sequence:
                    valid = False
                    break
                placeholder = attach(source, field, record, f'path[{slot.get("segment_index")}]')
                if not placeholder:
                    valid = False
                    break
                segments = rendered_path.split('/')
                index = slot.get('segment_index')
                if not isinstance(index, int) or index >= len(segments):
                    valid = False
                    break
                segments[index] = placeholder
                rendered_path = '/'.join(segments)
            if valid:
                step_by_record[record.id]['path'] = _path_below_target_prefix(
                    rendered_path, target_prefix,
                )

    for record in selected:
        if record.id in requested_ids:
            continue
        consumers = [
            child for child in selected
            if record.id in child.dependency_record_ids
        ]
        if consumers and not any((record.id, child.id) in bound_dependencies for child in consumers):
            warnings.append(
                f'记录 {record.id} 被采集器标为候选前置依赖，但缺少可确定绑定；已作为 setup 草稿保留，请人工确认是否必要。'
            )

    for item in registry.items:
        warnings.append(
            f'变量 {item["name"]} 未赋值：{item["reason"]} 位置 {item["location"]}。'
        )
    warnings.append('状态码和响应类型断言来自所选真实样本，仅为建议；业务成功语义仍需人工确认。')

    steps.sort(key=lambda step: (
        0 if step['phase'] == 'setup' else 1,
        sequence_by_record[record_id_by_step_identity[id(step)]],
    ))

    from .serializers import PlanStepSerializer
    step_serializer = PlanStepSerializer(data=steps, many=True)
    if not step_serializer.is_valid():
        raise WorkspaceValidationError(
            f'所选样本无法转换为当前压测步骤契约：{step_serializer.errors}'
        )

    draft = {
        'name': f'网页探索草稿 {task.task_id[:8]}',
        'description': task.description,
        'target_id': target.id,
        'users': 1,
        'spawn_rate': 1,
        'duration_seconds': 30,
        'wait_seconds': 1,
        # Required/redacted values are intentionally absent. Undefined ${name}
        # references make execution validation fail until the user supplies them.
        'variables': {},
        'unique_variables': [],
        'steps': steps,
    }
    source = {
        'type': 'browser_discovery',
        'task_id': str(task.id),
        'task_version': task.version,
        'source_version': task.source_version or task.version,
        'api_origin': task_origin,
        'target_path_prefix': target_prefix or '/',
        'requested_record_ids': record_ids,
        'included_record_ids': [record.id for record in selected],
        'records': source_rows,
        'required_variables': registry.items,
        'partial': task.status == BrowserDiscoveryTask.Status.PARTIAL,
    }
    return {'draft': draft, 'warnings': list(dict.fromkeys(warnings)), 'source': source}
