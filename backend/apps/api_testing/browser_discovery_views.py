"""Authenticated HTTP boundary for browser-captured API discovery."""
from __future__ import annotations

import uuid
import logging

from django.db import transaction
from django.db.models import prefetch_related_objects
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ai_core.config_access import usable_llm_configurations
from common.api import response
from projects.models import Project
from .browser_discovery import (
    browser_discovery_enabled,
    browser_discovery_delete_state,
    discovery_limits,
    evidence_statistics,
    expire_stale_discovery,
    handoff_to_workspace,
    normalize_http_url,
    origin_resolution,
    serialize_task,
    task_handoff_prefetch,
    selectable_origins,
    sync_auto_origin,
    write_origin_control,
    _read_origin_control,
    _refresh_selected_record_eligibility,
)
from .models import APISpecification, BrowserDiscoveryTask
from .serializers import (
    BrowserDiscoveryCreateSerializer, BrowserDiscoveryHandoffSerializer,
    BrowserDiscoveryOriginDecisionSerializer, BrowserDiscoveryRecordSerializer,
)
from .workspace_service import WorkspaceConflict, WorkspaceValidationError, can_edit_project, can_execute_project, validate_model_id, workspace_source

logger = logging.getLogger(__name__)


def _problem(exc: Exception, status_code: int = 400):
    return response(kind='error', message=str(exc), status_code=status_code)


def _project(project_id: int) -> Project:
    return get_object_or_404(Project, pk=project_id)


def _editable_project(project_id: int, user) -> Project:
    project = _project(project_id)
    if not can_edit_project(project, user):
        raise PermissionError('没有权限访问此项目的浏览器探索任务。')
    return project


def _owned_task(project_id: int, task_id, user, *, lock: bool = False) -> BrowserDiscoveryTask:
    query = BrowserDiscoveryTask.objects.select_related('project')
    if lock:
        query = query.select_for_update()
    try:
        task = query.get(pk=task_id, project_id=project_id, owner=user)
    except BrowserDiscoveryTask.DoesNotExist as exc:
        raise LookupError('探索任务不存在。') from exc
    if not can_edit_project(task.project, user):
        raise PermissionError('没有权限访问此项目的浏览器探索任务。')
    return task


class BrowserDiscoveryConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        try:
            _editable_project(project_id, request.user)
            models = usable_llm_configurations().order_by('-created_at')
            return response(kind='success', data={
                'enabled': browser_discovery_enabled(),
                'limits': discovery_limits(),
                'models': [
                    {
                        'id': item.id, 'model_name': item.model_name, 'provider': item.provider,
                        'provider_name': item.provider_name or item.provider,
                    }
                    for item in models
                ],
            }, message='获取浏览器探索能力成功')
        except PermissionError as exc:
            return _problem(exc, 403)


class BrowserDiscoveryCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        try:
            _editable_project(project_id, request.user)
            tasks = BrowserDiscoveryTask.objects.filter(
                project_id=project_id, owner=request.user,
            ).prefetch_related(task_handoff_prefetch()).order_by('-created_at', '-id')
            return response(kind='success', data=[serialize_task(expire_stale_discovery(item)) for item in tasks], message='获取浏览器探索任务成功')
        except PermissionError as exc:
            return _problem(exc, 403)

    def post(self, request, project_id):
        try:
            project = _editable_project(project_id, request.user)
            if not browser_discovery_enabled():
                raise PermissionError('浏览器探索功能当前未启用。')
            if project.project_type != 'api':
                raise WorkspaceValidationError('仅 API 类型项目可以创建浏览器探索任务。')
            if not can_execute_project(project, request.user):
                raise PermissionError('没有权限执行此项目的浏览器探索。')
            serializer = BrowserDiscoveryCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data
            if values['allow_test_data_writes'] is not True:
                raise WorkspaceValidationError('开始浏览器探索必须明确传递 allow_test_data_writes:true。')
            target_url = normalize_http_url(values['target_url'], label='target_url')
            api_origin_value = values.get('api_origin')
            api_origin = normalize_http_url(api_origin_value, label='api_origin', origin_only=True) if api_origin_value else ''
            model_id = validate_model_id(values['model_id'], owner=request.user)
            limits = {**discovery_limits(), 'origin_mode': 'manual' if api_origin else 'auto'}
            requested_timeout = values.get('exploration_timeout_seconds', limits['timeout_seconds'])
            if not 60 <= requested_timeout <= limits['timeout_seconds']:
                raise WorkspaceValidationError(f'exploration_timeout_seconds 必须在 60 到 {limits["timeout_seconds"]} 秒之间。')
            # This validates the exact, pinned Playwright capability before a
            # job is accepted.  It neither starts MCP nor exposes config data.
            from .browser_discovery import resolve_browser_discovery_mcp_config
            resolve_browser_discovery_mcp_config(request.user.id)
            celery_task_id = str(uuid.uuid4())
            task = BrowserDiscoveryTask.objects.create(
                project=project, owner=request.user, model_id=model_id, target_url=target_url,
                description=values['description'], api_origin=api_origin,
                allow_test_data_writes=True, exploration_timeout_seconds=requested_timeout,
                limits=limits, task_id=celery_task_id,
            )
            try:
                from .tasks import run_browser_discovery_async
                run_browser_discovery_async.apply_async(
                    args=(str(task.id), task.version, task.task_id), task_id=task.task_id,
                )
            except Exception as exc:
                logger.error('Browser discovery queue submission failed: task=%s error_type=%s', task.id, type(exc).__name__)
                task.status = BrowserDiscoveryTask.Status.FAILED
                task.error_code = 'queue_unavailable'
                task.error_message = '浏览器探索任务暂时无法进入队列，请稍后重试。'
                task.finished_at = task.updated_at
                task.save(update_fields=['status', 'error_code', 'error_message', 'finished_at', 'updated_at'])
                return _problem(WorkspaceValidationError('探索任务提交失败。'), 503)
            return response(kind='success', data=serialize_task(task), message='浏览器探索任务已创建', status_code=202)
        except PermissionError as exc:
            return _problem(exc, 403)
        except WorkspaceValidationError as exc:
            return _problem(exc)


class BrowserDiscoveryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, task_id):
        try:
            task = _owned_task(project_id, task_id, request.user)
            prefetch_related_objects([task], task_handoff_prefetch())
            return response(kind='success', data=serialize_task(expire_stale_discovery(task)), message='获取浏览器探索任务成功')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)

    def delete(self, request, project_id, task_id):
        try:
            with transaction.atomic():
                task = _owned_task(project_id, task_id, request.user, lock=True)
                deletion = browser_discovery_delete_state(task)
                if not deletion['can_delete']:
                    raise WorkspaceConflict(deletion['reason'])
                deleted_task_id = str(task.id)
                # Task-owned records cascade in the database.  Trace JSONL and
                # screenshots deliberately remain on disk for diagnostics.
                task.delete()
            return response(
                kind='success', data={'id': deleted_task_id},
                message='浏览器探索任务及数据库采样记录已删除；排障日志和截图已保留。',
            )
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except ProtectedError:
            return _problem(WorkspaceConflict('任务已被交接或发布来源引用，暂不能删除。'), 409)


class BrowserDiscoveryRecordsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, task_id):
        try:
            task = _owned_task(project_id, task_id, request.user)
            try:
                limit = min(100, max(1, int(request.query_params.get('limit', 50))))
                after = max(0, int(request.query_params.get('after', 0)))
            except (TypeError, ValueError):
                raise WorkspaceValidationError('limit 和 after 必须是整数。')
            records = task.records.filter(id__gt=after).order_by('id')[:limit]
            items = BrowserDiscoveryRecordSerializer(records, many=True).data
            return response(kind='success', data={
                'items': items, 'next_after': items[-1]['id'] if items else None,
                'evidence_path': None,
            }, message='获取浏览器探索证据摘要成功')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceValidationError as exc:
            return _problem(exc)


class BrowserDiscoveryCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, task_id):
        try:
            with transaction.atomic():
                task = _owned_task(project_id, task_id, request.user, lock=True)
                if not can_execute_project(task.project, request.user):
                    raise PermissionError('没有权限取消此项目的浏览器探索。')
                control = _read_origin_control(task)
                control['cancelled'] = True
                write_origin_control(task, control)
                if task.status == BrowserDiscoveryTask.Status.QUEUED:
                    task.status = BrowserDiscoveryTask.Status.CANCELLED
                    task.cancellation_requested = True
                    task.current_action = '已在队列中取消'
                    task.finished_at = task.heartbeat_at = task.updated_at
                elif task.status in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}:
                    task.cancellation_requested = True
                    task.current_action = '取消请求已记录，正在等待探索进程安全结束'
                task.save(update_fields=['status', 'cancellation_requested', 'current_action', 'finished_at', 'heartbeat_at', 'updated_at'])
            return response(kind='success', data=serialize_task(task), message='浏览器探索取消状态已更新')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)


class BrowserDiscoveryOriginsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, task_id):
        try:
            serializer = BrowserDiscoveryOriginDecisionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data
            origin = normalize_http_url(values['origin'], label='origin', origin_only=True)
            with transaction.atomic():
                task = _owned_task(project_id, task_id, request.user, lock=True)
                if not can_execute_project(task.project, request.user):
                    raise PermissionError('没有权限确认此项目的浏览器探索来源。')
                if task.version != values['version']:
                    raise WorkspaceConflict('探索任务版本已变化，请刷新后重试。')
                state = sync_auto_origin(task)
                resolution = origin_resolution(task, state=state)
                decision = values['decision']
                active = task.status in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING} and not task.cancellation_requested
                if decision in {'approve', 'reject'}:
                    if resolution['mode'] != 'auto':
                        raise WorkspaceValidationError('手动接口地址模式不接受自动来源确认。')
                    if not active:
                        raise WorkspaceValidationError('仅仍在运行中的探索任务可以确认或拒绝来源。')
                    pending = state['pending']
                    if not any(item['origin'] == origin for item in pending):
                        raise WorkspaceValidationError('来源不是当前等待确认的候选，不能写入控制指令。')
                    control = _read_origin_control(task)
                    approved = set(control['approved_origins'])
                    rejected = set(control['rejected_origins'])
                    if decision == 'approve':
                        approved.add(origin)
                        rejected.discard(origin)
                    else:
                        rejected.add(origin)
                        approved.discard(origin)
                    control.update({
                        'approved_origins': sorted(approved), 'rejected_origins': sorted(rejected), 'cancelled': False,
                    })
                    write_origin_control(task, control)
                    task.current_action = '已发送来源确认，正在等待浏览器继续当前请求'
                    task.save(update_fields=['current_action', 'updated_at'])
                else:
                    if task.status not in {BrowserDiscoveryTask.Status.COMPLETED, BrowserDiscoveryTask.Status.PARTIAL}:
                        raise WorkspaceValidationError('仅已结束的探索任务可以选择交接来源。')
                    # The task version protects trace mutations, but terminal
                    # source selection is a separate one-way decision.  Do
                    # not let an older page holding the same task version
                    # replace the source already used to derive the handoff.
                    selected_origin = normalize_http_url(
                        (task.limits or {}).get('selected_origin') or task.api_origin,
                        label='selected_origin', origin_only=True,
                    ) if ((task.limits or {}).get('selected_origin') or task.api_origin) else ''
                    if selected_origin and selected_origin != origin:
                        raise WorkspaceConflict('交接来源已选择为其他 origin，不能使用旧页面改选；请刷新任务状态。')
                    if not selected_origin:
                        if origin not in selectable_origins(task, state=state):
                            raise WorkspaceValidationError('只能选择已解析且已采集的来源作为工作区主接口来源。')
                        task.api_origin = origin
                        task.current_action = '已结束，已选择交接来源'
                        task.limits = {**(task.limits or {}), 'selected_origin': origin}
                        _refresh_selected_record_eligibility(task, force_dependencies=True)
                        # The terminal collector metrics belong to the completed
                        # run, while usable/excluded depend on the source selected
                        # now.  Recompute the latter without erasing final trace
                        # diagnostics that are no longer available from JSONL.
                        prior_evidence = task.evidence_summary if isinstance(task.evidence_summary, dict) else {}
                        evidence = evidence_statistics(task)
                        for key in ('pending', 'invalid_lines', 'over_limit', 'truncated'):
                            evidence[key] = int(prior_evidence.get(key, evidence[key]) or 0)
                        task.evidence_summary = evidence
                        task.save(update_fields=['api_origin', 'limits', 'current_action', 'evidence_summary', 'updated_at'])
            return response(kind='success', data=serialize_task(task), message='浏览器探索来源状态已更新')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except WorkspaceValidationError as exc:
            return _problem(exc)


class BrowserDiscoveryHandoffView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, task_id):
        try:
            serializer = BrowserDiscoveryHandoffSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            with transaction.atomic():
                task = _owned_task(project_id, task_id, request.user, lock=True)
                if not can_execute_project(task.project, request.user):
                    raise PermissionError('没有权限交接此项目的浏览器探索。')
                handoff, created = handoff_to_workspace(task=task, owner=request.user, **serializer.validated_data)
            source_type, source_name, source_task_id = workspace_source(handoff.workspace)
            data = {
                'spec': {'id': handoff.spec_id, 'spec_type': APISpecification.SpecType.BROWSER_CAPTURE},
                'endpoint_ids': handoff.workspace.endpoint_ids,
                'workspace': {
                    'id': handoff.workspace_id, 'status': handoff.workspace.status,
                    'source_type': source_type, 'source_name': source_name,
                    'source_task_id': source_task_id,
                },
                'selection_key': handoff.selection_hash,
            }
            return response(kind='success', data=data, message='浏览器探索来源已交接工作区', status_code=201 if created else 200)
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except WorkspaceValidationError as exc:
            return _problem(exc)
