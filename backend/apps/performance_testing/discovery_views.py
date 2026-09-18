"""Performance-project HTTP boundary for the shared browser discovery engine."""
from __future__ import annotations

import uuid
from copy import deepcopy

from django.db import transaction
from django.db.models import prefetch_related_objects
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_core.config_access import usable_llm_configurations
from api_testing.browser_discovery import (
    _read_origin_control,
    _refresh_selected_record_eligibility,
    browser_discovery_delete_state,
    browser_discovery_enabled,
    discovery_limits,
    evidence_statistics,
    expire_stale_discovery,
    normalize_http_url,
    origin_resolution,
    resolve_browser_discovery_mcp_config,
    selectable_origins,
    sync_auto_origin,
    task_handoff_prefetch,
    write_origin_control,
)
from api_testing.models import BrowserDiscoveryTask
from api_testing.serializers import (
    BrowserDiscoveryRecordSerializer,
)
from api_testing.workspace_service import WorkspaceConflict, WorkspaceValidationError, validate_model_id
from projects.access import DELETE, EXECUTE, READ, get_project_for_user

from .discovery import (
    clone_retry_task,
    compile_performance_draft,
    dispatch_performance_discovery,
    retry_decision,
    serialize_performance_task,
)
from .discovery_serializers import (
    PerformanceDiscoveryCreateSerializer,
    PerformanceDiscoveryDraftSerializer,
    PerformanceDiscoveryOriginSerializer,
    PerformanceDiscoveryRetrySerializer,
)
from .models import PerformanceTarget
from .parsers import LimitedJSONParser


def _ok(data, code=status.HTTP_200_OK):
    return Response({'success': True, 'data': data}, status=code)


def _problem(message, code=status.HTTP_400_BAD_REQUEST, error_code='invalid_request'):
    return Response({
        'success': False,
        'error': {'code': error_code, 'message': str(message)},
    }, status=code)


def _project(request, project_id, capability=READ):
    return get_project_for_user(
        project_id, request.user, capability, expected_project_type='perf',
    )


def _owned_task(request, project_id, task_id, *, capability=READ, lock=False):
    project = _project(request, project_id, capability)
    query = BrowserDiscoveryTask.objects.select_related('project')
    if lock:
        query = query.select_for_update()
    return get_object_or_404(
        query,
        pk=task_id,
        project=project,
        owner=request.user,
        project__project_type='perf',
    )


class PerformanceDiscoveryAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (LimitedJSONParser,)


class PerformanceDiscoveryConfigView(PerformanceDiscoveryAPIView):
    def get(self, request, project_id):
        _project(request, project_id, READ)
        models = usable_llm_configurations().order_by('-created_at')
        return _ok({
            'enabled': browser_discovery_enabled(),
            'limits': discovery_limits(),
            'models': [{
                'id': item.id,
                'model_name': item.model_name,
                'provider': item.provider,
                'provider_name': item.provider_name or item.provider,
            } for item in models],
        })


class PerformanceDiscoveryCollectionView(PerformanceDiscoveryAPIView):
    def get(self, request, project_id):
        project = _project(request, project_id, READ)
        tasks = (
            BrowserDiscoveryTask.objects.filter(project=project, owner=request.user)
            .prefetch_related(task_handoff_prefetch())
            .order_by('-created_at', '-id')
        )
        return _ok({'items': [
            serialize_performance_task(expire_stale_discovery(item)) for item in tasks
        ]})

    def post(self, request, project_id):
        project = _project(request, project_id, EXECUTE)
        if not browser_discovery_enabled():
            return _problem('浏览器探索功能当前未启用。', status.HTTP_403_FORBIDDEN, 'feature_disabled')
        serializer = PerformanceDiscoveryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if values['allow_test_data_writes'] is not True:
            return _problem('开始浏览器探索必须明确传递 allow_test_data_writes:true。')
        try:
            target_url = normalize_http_url(values['target_url'], label='target_url')
            api_origin_value = values.get('api_origin')
            api_origin = normalize_http_url(
                api_origin_value, label='api_origin', origin_only=True,
            ) if api_origin_value else ''
            model_id = validate_model_id(values['model_id'], owner=request.user)
            limits = {
                **discovery_limits(),
                'origin_mode': 'manual' if api_origin else 'auto',
                'auto_approve_origins': values['auto_approve_origins'],
            }
            requested_timeout = values.get('exploration_timeout_seconds', min(900, limits['timeout_seconds']))
            if not 60 <= requested_timeout <= limits['timeout_seconds']:
                raise WorkspaceValidationError(
                    f'exploration_timeout_seconds 必须在 60 到 {limits["timeout_seconds"]} 秒之间。'
                )
            resolve_browser_discovery_mcp_config(request.user.id)
        except WorkspaceValidationError as exc:
            return _problem(exc)
        task = BrowserDiscoveryTask.objects.create(
            project=project,
            owner=request.user,
            model_id=model_id,
            target_url=target_url,
            description=values['description'],
            api_origin=api_origin,
            allow_test_data_writes=True,
            exploration_timeout_seconds=requested_timeout,
            limits=limits,
            task_id=str(uuid.uuid4()),
        )
        if not dispatch_performance_discovery(task):
            return _problem('探索任务提交失败。', status.HTTP_503_SERVICE_UNAVAILABLE, 'queue_unavailable')
        return _ok(serialize_performance_task(task), status.HTTP_202_ACCEPTED)


class PerformanceDiscoveryDetailView(PerformanceDiscoveryAPIView):
    def get(self, request, project_id, task_id):
        task = _owned_task(request, project_id, task_id)
        prefetch_related_objects([task], task_handoff_prefetch())
        return _ok(serialize_performance_task(expire_stale_discovery(task)))

    def delete(self, request, project_id, task_id):
        try:
            with transaction.atomic():
                task = _owned_task(
                    request, project_id, task_id, capability=DELETE, lock=True,
                )
                deletion = browser_discovery_delete_state(task)
                if not deletion['can_delete']:
                    raise WorkspaceConflict(deletion['reason'])
                deleted_task_id = str(task.id)
                task.delete()
            return _ok({'id': deleted_task_id})
        except WorkspaceConflict as exc:
            return _problem(exc, status.HTTP_409_CONFLICT, 'conflict')
        except ProtectedError:
            return _problem(
                '任务已被来源引用，暂不能删除。', status.HTTP_409_CONFLICT, 'conflict',
            )


class PerformanceDiscoveryRecordsView(PerformanceDiscoveryAPIView):
    def get(self, request, project_id, task_id):
        task = _owned_task(request, project_id, task_id)
        try:
            limit = min(100, max(1, int(request.query_params.get('limit', 50))))
            after = max(0, int(request.query_params.get('after', 0)))
        except (TypeError, ValueError):
            return _problem('limit 和 after 必须是整数。')
        records = task.records.filter(id__gt=after).order_by('id')[:limit]
        items = BrowserDiscoveryRecordSerializer(records, many=True).data
        return _ok({
            'items': items,
            'next_after': items[-1]['id'] if items else None,
            'evidence_path': None,
        })


class PerformanceDiscoveryCancelView(PerformanceDiscoveryAPIView):
    def post(self, request, project_id, task_id):
        with transaction.atomic():
            task = _owned_task(
                request, project_id, task_id, capability=EXECUTE, lock=True,
            )
            control = _read_origin_control(task)
            control['cancelled'] = True
            write_origin_control(task, control)
            now = timezone.now()
            if task.status == BrowserDiscoveryTask.Status.QUEUED:
                task.status = BrowserDiscoveryTask.Status.CANCELLED
                task.cancellation_requested = True
                task.current_action = '已在队列中取消'
                task.finished_at = task.heartbeat_at = now
            elif task.status in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}:
                task.cancellation_requested = True
                task.current_action = '取消请求已记录，正在等待探索进程安全结束'
            task.save(update_fields=[
                'status', 'cancellation_requested', 'current_action',
                'finished_at', 'heartbeat_at', 'updated_at',
            ])
        return _ok(serialize_performance_task(task))


class PerformanceDiscoveryOriginView(PerformanceDiscoveryAPIView):
    def post(self, request, project_id, task_id):
        serializer = PerformanceDiscoveryOriginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            origin = normalize_http_url(values['origin'], label='origin', origin_only=True)
            with transaction.atomic():
                task = _owned_task(
                    request, project_id, task_id, capability=EXECUTE, lock=True,
                )
                if task.version != values['version']:
                    raise WorkspaceConflict('探索任务版本已变化，请刷新后重试。')
                state = sync_auto_origin(task)
                resolution = origin_resolution(task, state=state)
                decision = values['decision']
                active = (
                    task.status in {BrowserDiscoveryTask.Status.RUNNING, BrowserDiscoveryTask.Status.FINALIZING}
                    and not task.cancellation_requested
                )
                if decision in {'approve', 'reject'}:
                    if resolution['mode'] != 'auto':
                        raise WorkspaceValidationError('手动接口地址模式不接受自动来源确认。')
                    if not active:
                        raise WorkspaceValidationError('仅仍在运行中的探索任务可以确认或拒绝来源。')
                    if not any(item['origin'] == origin for item in state['pending']):
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
                        'approved_origins': sorted(approved),
                        'rejected_origins': sorted(rejected),
                        'cancelled': False,
                    })
                    write_origin_control(task, control)
                    task.current_action = '已发送来源确认，正在等待浏览器继续当前请求'
                    task.save(update_fields=['current_action', 'updated_at'])
                else:
                    if task.status not in {BrowserDiscoveryTask.Status.COMPLETED, BrowserDiscoveryTask.Status.PARTIAL}:
                        raise WorkspaceValidationError('仅已结束的探索任务可以选择草稿来源。')
                    selected_origin = normalize_http_url(
                        (task.limits or {}).get('selected_origin') or task.api_origin,
                        label='selected_origin', origin_only=True,
                    ) if ((task.limits or {}).get('selected_origin') or task.api_origin) else ''
                    if selected_origin and selected_origin != origin:
                        raise WorkspaceConflict('草稿来源已选择为其他 origin，不能使用旧页面改选；请刷新任务状态。')
                    if not selected_origin:
                        if origin not in selectable_origins(task, state=state):
                            raise WorkspaceValidationError('只能选择已解析且已采集的来源作为压测草稿来源。')
                        task.api_origin = origin
                        task.current_action = '已结束，已选择草稿来源'
                        task.limits = {**(task.limits or {}), 'selected_origin': origin}
                        _refresh_selected_record_eligibility(task, force_dependencies=True)
                        prior = task.evidence_summary if isinstance(task.evidence_summary, dict) else {}
                        evidence = evidence_statistics(task)
                        for key in ('pending', 'invalid_lines', 'over_limit', 'truncated'):
                            evidence[key] = int(prior.get(key, evidence[key]) or 0)
                        task.evidence_summary = evidence
                        task.save(update_fields=[
                            'api_origin', 'limits', 'current_action', 'evidence_summary', 'updated_at',
                        ])
            return _ok(serialize_performance_task(task))
        except WorkspaceConflict as exc:
            return _problem(exc, status.HTTP_409_CONFLICT, 'conflict')
        except WorkspaceValidationError as exc:
            return _problem(exc)


class PerformanceDiscoveryDraftView(PerformanceDiscoveryAPIView):
    def post(self, request, project_id, task_id):
        serializer = PerformanceDiscoveryDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        project = _project(request, project_id, EXECUTE)
        task = get_object_or_404(
            BrowserDiscoveryTask.objects.select_related('project'),
            pk=task_id, project=project, owner=request.user, project__project_type='perf',
        )
        target = get_object_or_404(
            PerformanceTarget, pk=values['target_id'], project=project,
        )
        try:
            payload = compile_performance_draft(
                task=task,
                version=values['version'],
                record_ids=values['record_ids'],
                target=target,
            )
            return _ok(payload)
        except WorkspaceConflict as exc:
            return _problem(exc, status.HTTP_409_CONFLICT, 'conflict')
        except WorkspaceValidationError as exc:
            return _problem(exc)


class PerformanceDiscoveryRetryView(PerformanceDiscoveryAPIView):
    def post(self, request, project_id, task_id):
        serializer = PerformanceDiscoveryRetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                task = _owned_task(
                    request, project_id, task_id, capability=EXECUTE, lock=True,
                )
                if task.version != serializer.validated_data['version']:
                    raise WorkspaceConflict('探索任务版本已变化，请刷新后重试。')
                decision = retry_decision(task)
                if not decision['available']:
                    raise WorkspaceConflict(decision['reason'])
                # Retry revalidates current model/MCP availability but preserves
                # the original task and evidence as immutable diagnostics.
                validate_model_id(task.model_id, owner=request.user)
                resolve_browser_discovery_mcp_config(request.user.id)
                retried = clone_retry_task(task)
            if not dispatch_performance_discovery(retried):
                return _problem('探索任务重试提交失败。', status.HTTP_503_SERVICE_UNAVAILABLE, 'queue_unavailable')
            return _ok({
                'task': serialize_performance_task(retried),
                'retry_of': str(task.id),
            }, status.HTTP_202_ACCEPTED)
        except WorkspaceConflict as exc:
            return _problem(exc, status.HTTP_409_CONFLICT, 'conflict')
        except WorkspaceValidationError as exc:
            return _problem(exc)
