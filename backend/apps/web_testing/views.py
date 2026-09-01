"""HTTP API for Web UI script generation, assets, suites, and execution history."""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import response
from projects.models import Environment

from .constants import WEBUI_BROWSER_ENGINE, normalize_webui_execution_options
from .execution_diagnostics import safe_screenshot_relative_path
from .execution_variables import (
    ExecutionVariableError,
    normalize_variable_definitions,
    pop_runtime_variables,
    store_runtime_variables,
)
from .exploration_timeout import (
    EXPLORATION_TIMEOUT_MAX_SECONDS,
    EXPLORATION_TIMEOUT_MIN_SECONDS,
    exploration_total_timeout_seconds,
)
from .generation_events import publish_terminal
from .generation_repository import (
    GenerationResolutionConflict,
    attach_celery_task,
    cancel_generation,
    get_generation_for_project,
    prepare_trace_generation_retry,
    prepare_generation_resolution,
    transition_generation,
)
from .generation_save_state import generation_reference, is_generation_saved
from .generation_security import store_temporary_credentials
from .generation_workspace import (
    ACTIVE_GENERATION_STATUSES,
    BUSY_REPAIR_STATUSES,
    BUSY_VERIFICATION_STATUSES,
    base_url_fingerprint,
    environment_fingerprint,
    WorkspaceConflict,
    attach_debug_task,
    attach_repair_task,
    prepare_debug,
    prepare_repair,
    script_hash,
    update_draft,
    workspace_for_generation,
)
from .models import (
    MidSceneScript,
    WebUIScriptGeneration,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestModule,
    WebUITestSuite,
    WebUITestSuiteCase,
    WebUITestSuiteCaseExecution,
    WebUITestSuiteExecutionDetail,
)
from .project_access import (
    DELETE,
    EDIT,
    EXECUTE,
    READ,
    REPORT,
    get_project_for_user,
    payload_project_mismatch,
    project_access_required,
    validate_related_project,
)
from .script_contract import ScriptContractError, normalize_for_storage, store_script_content
from .serializers import (
    WebUIScriptGenerationCreateSerializer,
    WebUIScriptGenerationDebugSerializer,
    WebUIScriptGenerationDraftSerializer,
    WebUIScriptGenerationRepairSerializer,
    WebUIScriptGenerationRetrySerializer,
    WebUIScriptGenerationResolveSerializer,
    WebUIScriptGenerationSaveSerializer,
    WebUIScriptGenerationSerializer,
    WebUITestCaseCreateSerializer,
    WebUITestCaseDetailSerializer,
    WebUITestCaseExecutionDetailSerializer,
    WebUITestCaseSerializer,
    WebUITestExecutionListSerializer,
    WebUITestModuleSerializer,
    WebUITestSuiteAddTestCaseSerializer,
    WebUITestSuiteCreateSerializer,
    WebUITestSuiteExecutionDetailSerializer,
    WebUITestSuiteSerializer,
    WebUITestSuiteUpdateSerializer,
)
from .tasks import (
    _remove_failure_screenshots,
    cancel_task,
    debug_webui_script_generation_task,
    execute_webui_test_suite_task,
    generate_midscene_script_task,
    generate_webui_script_generation_v2_task,
    repair_webui_script_generation_task,
    retry_webui_script_generation_from_trace_task,
)

logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


def _is_generation_owner(project, generation, user) -> bool:
    return (
        generation.user_id == user.id
        or project.owner_id == user.id
        or project.created_by_id == user.id
    )


class WebUIScriptGenerationCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        serializer = WebUIScriptGenerationCreateSerializer(
            data=request.data,
            context={'request': request, 'project': project},
        )
        serializer.is_valid(raise_exception=True)
        generation = serializer.save()
        try:
            task = generate_webui_script_generation_v2_task.delay(str(generation.pk))
            generation = attach_celery_task(generation.pk, task.id)
        except Exception:
            logger.exception('WebUI 脚本生成任务调度失败: generation_id=%s', generation.pk)
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.FAILED,
                error_code='TRANSIENT_SERVICE_ERROR',
                error_message='脚本生成任务暂时无法调度，请稍后重试。',
            )
            return Response(
                {
                    'success': False,
                    'message': generation.error_message,
                    'data': WebUIScriptGenerationSerializer(generation).data,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'success': True,
                'message': '脚本生成任务已启动。',
                'data': WebUIScriptGenerationSerializer(generation).data,
            },
            status=status.HTTP_201_CREATED,
        )


class WebUIScriptGenerationSettingsView(APIView):
    """Return server-side defaults that are safe for a project member to use."""

    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id):
        return Response({
            'success': True,
            'data': {
                'exploration_timeout_seconds': int(exploration_total_timeout_seconds()),
                'min_exploration_timeout_seconds': EXPLORATION_TIMEOUT_MIN_SECONDS,
                'max_exploration_timeout_seconds': EXPLORATION_TIMEOUT_MAX_SECONDS,
            },
        })


class WebUIScriptGenerationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id, generation_id):
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        return Response({'success': True, 'data': WebUIScriptGenerationSerializer(generation).data})


class WebUIScriptGenerationCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能取消自己创建的生成记录')
        celery_task_id = generation.celery_task_id
        generation = cancel_generation(generation.pk)
        publish_terminal(generation)
        if celery_task_id:
            cancel_task(celery_task_id)
        return Response(
            {
                'success': True,
                'message': '脚本生成任务已取消',
                'data': WebUIScriptGenerationSerializer(generation).data,
            }
        )


class WebUIScriptGenerationResolveView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能处理自己创建的生成记录')

        serializer = WebUIScriptGenerationResolveSerializer(
            data=request.data or {},
            context={'generation': generation},
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            generation, should_schedule = prepare_generation_resolution(
                generation.pk,
                expected_status=values['expected_status'],
                expected_revision=values['expected_revision'],
                user_id=request.user.id,
                description_safe=values.get('safe_description'),
                clarification_answers=values.get('safe_answers'),
                credentials_provided=bool(values.get('temporary_credentials')),
            )
        except GenerationResolutionConflict as exc:
            return Response(
                {
                    'success': False,
                    'message': str(exc),
                    'data': WebUIScriptGenerationSerializer(exc.generation).data,
                },
                status=status.HTTP_409_CONFLICT,
            )
        if not should_schedule:
            publish_terminal(generation)
            return Response(
                {
                    'success': False,
                    'message': generation.error_message,
                    'data': WebUIScriptGenerationSerializer(generation).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            if values.get('temporary_credentials'):
                store_temporary_credentials(generation.pk, values['temporary_credentials'])
            task = generate_webui_script_generation_v2_task.delay(str(generation.pk))
            generation = attach_celery_task(generation.pk, task.id)
        except Exception:
            logger.exception('WebUI 暂停任务恢复失败: generation_id=%s', generation.pk)
            generation = transition_generation(
                generation.pk,
                WebUIScriptGeneration.Status.FAILED,
                error_code='TRANSIENT_SERVICE_ERROR',
                error_message='脚本生成任务暂时无法恢复，请稍后重试。',
            )
            publish_terminal(generation)
            return Response(
                {
                    'success': False,
                    'message': generation.error_message,
                    'data': WebUIScriptGenerationSerializer(generation).data,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'success': True,
                'message': '补充信息已提交，正在继续生成。',
                'data': WebUIScriptGenerationSerializer(generation).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class WebUIScriptGenerationRetryView(APIView):
    """Retry only the LLM generation stage from the saved callback trace."""

    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能重试自己创建的生成记录')
        serializer = WebUIScriptGenerationRetrySerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            generation = prepare_trace_generation_retry(
                generation.pk, expected_revision=serializer.validated_data['expected_revision'],
            )
            task = retry_webui_script_generation_from_trace_task.delay(str(generation.pk))
            generation = attach_celery_task(generation.pk, task.id)
        except GenerationResolutionConflict as exc:
            return Response({'success': False, 'message': str(exc), 'data': WebUIScriptGenerationSerializer(exc.generation).data}, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception('仅重试脚本生成任务调度失败: generation_id=%s', generation_id)
            generation = transition_generation(
                generation_id, WebUIScriptGeneration.Status.FAILED,
                error_code='TRANSIENT_SERVICE_ERROR', error_message='脚本生成重试任务暂时无法调度，请稍后重试。',
            )
            publish_terminal(generation)
            return Response({'success': False, 'message': generation.error_message, 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'success': True, 'message': '正在基于已保存的探索轨迹重新生成脚本。', 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_202_ACCEPTED)


class WebUIScriptGenerationDraftView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def patch(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能编辑自己创建的生成记录')
        serializer = WebUIScriptGenerationDraftSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            generation = update_draft(
                generation.pk,
                expected_revision=serializer.validated_data['expected_revision'],
                script_draft=serializer.validated_data['script_draft'],
                variables=serializer.validated_data['variables'],
            )
        except WorkspaceConflict as exc:
            return Response(
                {'success': False, 'message': str(exc), 'data': WebUIScriptGenerationSerializer(exc.generation).data},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({'success': True, 'data': WebUIScriptGenerationSerializer(generation).data})


class WebUIScriptGenerationDebugView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能调试自己创建的生成记录')
        serializer = WebUIScriptGenerationDebugSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            # Invalid work-in-progress is durable, but never reaches the executor.
            normalize_for_storage(generation.script_draft)
        except ScriptContractError as exc:
            return Response({'success': False, 'message': str(exc), 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_400_BAD_REQUEST)
        environment = Environment.objects.filter(
            id=generation.environment_id,
            project_id=project_id,
            category=Environment.EnvironmentCategory.WEB,
            is_active=True,
        ).first()
        if environment is None or not ((environment.config or {}).get('base_url') or '').strip():
            return Response({'success': False, 'message': '所选 WebUI 环境不可用或未配置 Base URL。', 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                execution = WebUITestExecution.objects.create(
                    exec_type='case', name='生成草稿调试', description=generation.description_safe,
                    executor=request.user, project=project, environment=environment,
                    browser=WEBUI_BROWSER_ENGINE, status='pending', trigger_type='manual',
                )
                WebUITestCaseExecutionDetail.objects.create(execution=execution, test_case=None, status='pending')
                generation, locked_hash = prepare_debug(
                    generation.pk,
                    expected_revision=serializer.validated_data['expected_revision'],
                    execution_id=execution.id,
                    runtime_variables_present=bool(serializer.validated_data['runtime_variables']),
                )
        except WorkspaceConflict as exc:
            return Response({'success': False, 'message': str(exc), 'data': WebUIScriptGenerationSerializer(exc.generation).data}, status=status.HTTP_409_CONFLICT)

        # Only this cache entry carries runtime values; Celery receives IDs and a digest only.
        try:
            store_runtime_variables(execution.id, serializer.validated_data['runtime_variables'])
            task = debug_webui_script_generation_task.delay(
                str(generation.pk), execution.id, serializer.validated_data['expected_revision'], locked_hash,
            )
            execution.task_id = task.id
            execution.save(update_fields=['task_id', 'updated_at'])
            generation = attach_debug_task(
                generation.pk, execution_id=execution.id,
                locked_revision=serializer.validated_data['expected_revision'], locked_hash=locked_hash,
                task_id=task.id,
            )
        except Exception:
            logger.exception('生成草稿调试任务调度失败: generation_id=%s execution_id=%s', generation.pk, execution.id)
            try:
                pop_runtime_variables(execution.id)
            except Exception:
                logger.warning('无法清理调试临时变量，将等待缓存过期: execution_id=%s', execution.id)
            execution.status = 'error'
            execution.error_message = '调试任务暂时无法调度，请稍后重试。'
            execution.end_time = timezone.now()
            execution.save(update_fields=['status', 'error_message', 'end_time', 'updated_at'])
            detail = execution.case_execution_detail
            detail.status = 'error'
            detail.error_message = execution.error_message
            detail.end_time = execution.end_time
            detail.save(update_fields=['status', 'error_message', 'end_time'])
            from .generation_workspace import finish_debug
            finish_debug(
                generation.pk, execution_id=execution.id,
                locked_revision=serializer.validated_data['expected_revision'], locked_hash=locked_hash,
                status='error', diagnostics=[{'code': 'DISPATCH_FAILED', 'message': execution.error_message}],
            )
            generation.refresh_from_db()
            return Response({'success': False, 'message': execution.error_message, 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'success': True, 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_202_ACCEPTED)


class WebUIScriptGenerationRepairView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        try:
            generation = get_generation_for_project(generation_id, project_id)
        except WebUIScriptGeneration.DoesNotExist as exc:
            raise Http404('生成记录不存在') from exc
        if not _is_generation_owner(project, generation, request.user):
            raise PermissionDenied('只能修复自己创建的生成记录')
        serializer = WebUIScriptGenerationRepairSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            generation, locked_hash = prepare_repair(
                generation.pk, expected_revision=serializer.validated_data['expected_revision'],
            )
        except WorkspaceConflict as exc:
            return Response({'success': False, 'message': str(exc), 'data': WebUIScriptGenerationSerializer(exc.generation).data}, status=status.HTTP_409_CONFLICT)
        try:
            task = repair_webui_script_generation_task.delay(
                str(generation.pk), serializer.validated_data['expected_revision'], locked_hash,
            )
            generation = attach_repair_task(
                generation.pk, locked_revision=serializer.validated_data['expected_revision'],
                locked_hash=locked_hash, task_id=task.id,
            )
        except Exception:
            logger.exception('生成草稿修复任务调度失败: generation_id=%s', generation.pk)
            from .generation_workspace import finish_repair_failure
            finish_repair_failure(
                generation.pk, locked_revision=serializer.validated_data['expected_revision'], locked_hash=locked_hash,
                message='修复任务暂时无法调度，请稍后重试。',
                blockers=[{'severity': 'blocker', 'code': 'DISPATCH_FAILED', 'message': '修复任务暂时无法调度。'}],
            )
            generation.refresh_from_db()
            return Response({'success': False, 'message': '修复任务暂时无法调度，请稍后重试。', 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'success': True, 'data': WebUIScriptGenerationSerializer(generation).data}, status=status.HTTP_202_ACCEPTED)


class WebUIScriptGenerationSaveView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, generation_id):
        project = get_project_for_user(project_id, request.user, EDIT)
        serializer = WebUIScriptGenerationSaveSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        generation_ref = generation_reference(generation_id)
        try:
            with transaction.atomic():
                try:
                    generation = WebUIScriptGeneration.objects.select_for_update().select_related(
                        'test_case', 'project', 'user', 'module'
                    ).get(pk=generation_id, project_id=project_id)
                except WebUIScriptGeneration.DoesNotExist as exc:
                    raise Http404('生成记录不存在') from exc
                if not _is_generation_owner(project, generation, request.user):
                    raise PermissionDenied('只能保存自己创建的生成记录')
                requested_mode = serializer.validated_data.get('mode')
                legacy_save = requested_mode is None
                workspace = workspace_for_generation(generation)
                if requested_mode and workspace['revision'] != serializer.validated_data['expected_revision']:
                    return Response(
                        {'success': False, 'message': '工作区版本已变化，请刷新后重试。', 'data': WebUIScriptGenerationSerializer(generation).data},
                        status=status.HTTP_409_CONFLICT,
                    )
                if (
                    generation.status in ACTIVE_GENERATION_STATUSES
                    or workspace['verification'].get('status') in BUSY_VERIFICATION_STATUSES
                    or workspace['repair'].get('status') in BUSY_REPAIR_STATUSES
                ):
                    return Response(
                        {'success': False, 'message': '生成、调试或修复进行中，不能保存工作区草稿。', 'data': WebUIScriptGenerationSerializer(generation).data},
                        status=status.HTTP_409_CONFLICT,
                    )
                if legacy_save and workspace['revision'] > 0:
                    return Response(
                        {'success': False, 'message': '该草稿已经编辑，请使用当前工作区版本保存。'},
                        status=status.HTTP_409_CONFLICT,
                    )
                if legacy_save and generation.status not in {
                    WebUIScriptGeneration.Status.READY,
                    WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
                }:
                    return Response({'success': False, 'message': '当前脚本尚未通过质量检查，不能保存。'}, status=status.HTTP_409_CONFLICT)
                normalized_script = normalize_for_storage(generation.script_draft)
                verification = workspace['verification']
                if requested_mode == 'verified' and not (
                    verification.get('status') == 'passed'
                    and generation.environment.is_active
                    and verification.get('script_hash') == script_hash(normalized_script)
                    and verification.get('environment_id') == generation.environment_id
                    and verification.get('locked_revision') == workspace['revision']
                    and verification.get('environment_fingerprint') == environment_fingerprint(generation.environment.config)
                    and verification.get('base_url_fingerprint') == base_url_fingerprint(generation.environment.config)
                ):
                    return Response(
                        {'success': False, 'message': '当前脚本尚无同版本、同环境的实际调试通过记录。', 'data': WebUIScriptGenerationSerializer(generation).data},
                        status=status.HTTP_409_CONFLICT,
                    )
                test_case = generation.test_case
                created = False
                if test_case is not None:
                    # Lock the case before inspecting provenance so a concurrent case-page edit cannot be overwritten.
                    test_case = WebUITestCase.objects.select_for_update().get(pk=test_case.pk)
                    metadata = test_case.generation_metadata if isinstance(test_case.generation_metadata, dict) else {}
                    stored_fingerprint = metadata.get('content_fingerprint')
                    stored_variables_fingerprint = metadata.get('variables_fingerprint')
                    if metadata.get('generation_ref') != generation_ref or not stored_fingerprint or not stored_variables_fingerprint:
                        return Response(
                            {'success': False, 'message': '关联用例缺少可确认的生成来源或旧版指纹，已拒绝覆盖；请先人工确认。'},
                            status=status.HTTP_409_CONFLICT,
                        )
                    if stored_fingerprint != script_hash(test_case.test_script_content):
                        return Response(
                            {'success': False, 'message': '关联用例脚本已在用例页独立修改，已拒绝覆盖。'},
                            status=status.HTTP_409_CONFLICT,
                        )
                    current_variables_fingerprint = script_hash(__import__('json').dumps(
                        test_case.variables or [], ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                    ))
                    if stored_variables_fingerprint != current_variables_fingerprint:
                        return Response(
                            {'success': False, 'message': '关联用例变量已在用例页独立修改，已拒绝覆盖。'},
                            status=status.HTTP_409_CONFLICT,
                        )
                if test_case is None:
                    scenario = generation.scenario_spec or {}
                    test_case = WebUITestCase.objects.create(
                        title=(
                            serializer.validated_data.get('title')
                            or scenario.get('title')
                            or 'AI 生成的 WebUI 用例'
                        ),
                        description=generation.description_safe,
                        module=generation.module or WebUITestModule.ensure_default(project.id),
                        variables=[],
                        user=generation.user,
                        project=project,
                    )
                    generation.test_case = test_case
                    generation.save(update_fields=['test_case', 'updated_at'])
                    created = True
                if serializer.validated_data.get('title'):
                    test_case.title = serializer.validated_data['title']
                    test_case.save(update_fields=['title', 'updated_at'])
                metadata = {
                    'generation_ref': generation_ref,
                    'content_fingerprint': script_hash(normalized_script),
                    'workspace_revision': workspace['revision'],
                    'variables_fingerprint': script_hash(__import__('json').dumps(workspace['variables'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))),
                    'model': {
                        'provider': (generation.model_info or {}).get('provider', ''),
                        'provider_name': (generation.model_info or {}).get('provider_name', ''),
                        'model_name': (generation.model_info or {}).get('model_name', ''),
                    },
                    'quality_status': (generation.quality_report or {}).get('status', ''),
                    'repair_count': (workspace['repair'] or {}).get('count', 0),
                    'verification': {
                        'status': 'passed' if requested_mode == 'verified' else 'unverified',
                        'script_hash': script_hash(normalized_script),
                        'environment_id': generation.environment_id if requested_mode == 'verified' else None,
                        'execution_id': verification.get('execution_id') if requested_mode == 'verified' else None,
                    },
                    'unresolved_step_count': len((generation.exploration_snapshot or {}).get('unresolved_steps') or []),
                }
                store_script_content(test_case, normalized_script, source='mcp_exploration', generation_metadata=metadata)
                test_case.variables = workspace['variables']
                test_case.save(update_fields=['variables', 'updated_at'])
        except ScriptContractError as exc:
            return Response(
                {'success': False, 'message': f'{exc}；无效草稿仍保留在生成工作区，不能保存为测试用例。'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': '脚本已保存到测试用例。',
                'data': {
                    'generation': WebUIScriptGenerationSerializer(generation).data,
                    'test_case_id': test_case.pk,
                    'created': created,
                },
            }
        )


class GenerateMidSceneScriptView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT, expected_project_type='app')
    def post(self, request, project_id):
        description = str(request.data.get('description') or '').strip()
        screenshot_b64 = str(request.data.get('screenshot_b64') or '').strip()
        if not description:
            return response(kind='error', message='请输入测试场景描述', status_code=400)
        project = get_project_for_user(
            project_id,
            request.user,
            EDIT,
            expected_project_type='app',
        )
        script = MidSceneScript.objects.create(
            name=f"MidScene脚本_{timezone.now().strftime('%Y%m%d_%H%M%S')}",
            description=description,
            natural_language=description,
            screenshot_b64=screenshot_b64,
            created_by=request.user,
            project=project,
            status='pending',
        )
        task = generate_midscene_script_task.delay(
            script_id=script.id,
            user_id=request.user.id,
            project_id=project.id,
        )
        return response(
            kind='success',
            data={'script_id': script.id, 'task_id': task.id, 'status': 'pending'},
            message='MidScene 脚本生成任务已启动',
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT, expected_project_type='app')
def get_midscene_script(request, project_id, script_id):
    script = get_object_or_404(MidSceneScript, id=script_id, project_id=project_id)
    return response(
        kind='success',
        data={
            'id': script.id,
            'name': script.name,
            'description': script.description,
            'test_script_content': script.script_content,
            'natural_language': script.natural_language,
            'screenshot_b64': script.screenshot_b64,
            'is_executed': script.is_executed,
            'execution_result': script.execution_result,
            'execution_logs': script.execution_logs,
            'status': script.status,
            'task_id': script.task_id,
            'created_at': script.created_at.isoformat(),
            'updated_at': script.updated_at.isoformat(),
        },
        message='获取 MidScene 脚本成功',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(READ, expected_project_type='app')
def list_midscene_scripts(request, project_id):
    queryset = MidSceneScript.objects.filter(project_id=project_id)
    if request.GET.get('status'):
        queryset = queryset.filter(status=request.GET['status'])
    page = max(int(request.GET.get('page', 1)), 1)
    page_size = min(max(int(request.GET.get('page_size', 10)), 1), 100)
    total = queryset.count()
    scripts = queryset[(page - 1) * page_size:page * page_size]
    return response(
        kind='success',
        data={
            'scripts': [
                {
                    'id': item.id,
                    'name': item.name,
                    'description': item.description,
                    'status': item.status,
                    'is_executed': item.is_executed,
                    'created_at': item.created_at.isoformat(),
                    'updated_at': item.updated_at.isoformat(),
                }
                for item in scripts
            ],
            'total': total,
            'page': page,
            'page_size': page_size,
        },
        message='获取 MidScene 脚本列表成功',
    )


class TaskStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id, task_id: str):
        from common.task import get_celery_task_status

        result = get_celery_task_status(task_id)
        if not result:
            return response(kind='not_found', message=f'任务 {task_id} 不存在')
        task_status = str(result.get('status', 'unknown')).lower()
        data = {
            'task_id': task_id,
            'status': task_status,
            'progress': result.get('progress', 0),
            'message': result.get('message', ''),
            'result': result,
        }
        if result.get('error'):
            data['error'] = result['error']
        return response(kind='success', data=data, message='任务状态查询成功')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT)
def get_webui_test_execution_statistics(request, project_id):
    queryset = WebUITestExecution.objects.filter(project_id=project_id)
    stats = {'total': queryset.count()}
    for state in ('pending', 'running', 'passed', 'failed', 'error', 'stopped'):
        stats[state] = queryset.filter(status=state).count()
    completed = stats['passed'] + stats['failed'] + stats['error']
    stats['success_rate'] = round(stats['passed'] / completed * 100, 2) if completed else 0
    return response(kind='success', data=stats, message='获取执行统计成功')


class WebUITestModuleListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WebUITestModuleSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestModule.objects.filter(project_id=project_id).order_by('order', 'id')

    def list(self, request, *args, **kwargs):
        WebUITestModule.ensure_default(kwargs['project_id'])
        roots = self.get_queryset().filter(parent__isnull=True)
        return response(
            kind='success',
            data=self.get_serializer(roots, many=True).data,
            message='获取模块树成功',
        )

    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        project_id = kwargs['project_id']
        if payload_project_mismatch(request.data, project_id):
            return response(
                kind='error',
                message='请求中的 project 必须与 URL project_id 一致',
                status_code=400,
            )
        data = request.data.copy()
        data['project'] = project_id
        validate_related_project(WebUITestModule, data.get('parent'), project_id, 'parent')
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response(kind='created', data=serializer.data, message='模块创建成功')


class WebUITestModuleRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WebUITestModuleSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestModule.objects.filter(project_id=project_id)

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return response(kind='success', data=serializer.data, message='获取模块详情成功')

    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        project_id = kwargs['project_id']
        if payload_project_mismatch(request.data, project_id):
            return response(kind='error', message='project 与 URL 不一致', status_code=400)
        validate_related_project(WebUITestModule, request.data.get('parent'), project_id, 'parent')
        serializer = self.get_serializer(
            self.get_object(),
            data=request.data,
            partial=kwargs.get('partial', False),
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response(kind='success', data=serializer.data, message='模块更新成功')

    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        module = self.get_object()
        if module.is_default:
            return response(kind='error', message='默认模块不能删除', status_code=400)
        if module.children.exists():
            return response(kind='error', message='请先删除或移动子模块', status_code=400)
        default_module = WebUITestModule.ensure_default(kwargs['project_id'])
        with transaction.atomic():
            module.test_cases.update(module=default_module)
            module.script_generations.update(module=default_module)
            name = module.name
            module.delete()
        return response(kind='success', message=f"模块 '{name}' 删除成功")


class WebUITestCaseBatchDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(DELETE)
    def post(self, request, project_id):
        case_ids = request.data.get('case_ids') or []
        if not isinstance(case_ids, list) or not case_ids:
            return response(kind='error', message='case_ids 不能为空', status_code=400)
        queryset = WebUITestCase.objects.filter(id__in=case_ids, project_id=project_id)
        count = queryset.count()
        queryset.delete()
        return response(
            kind='success',
            data={'deleted_count': count},
            message=f'成功删除 {count} 个测试用例',
        )


class WebUITestCaseBatchUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id):
        case_ids = request.data.get('case_ids') or []
        update_data = request.data.get('update_data') or {}
        if not isinstance(case_ids, list) or not case_ids:
            return response(kind='error', message='case_ids 不能为空', status_code=400)
        module_id = update_data.get('module_id') or WebUITestModule.ensure_default(project_id).id
        validate_related_project(WebUITestModule, module_id, project_id, 'module_id')
        count = WebUITestCase.objects.filter(
            id__in=case_ids,
            project_id=project_id,
        ).update(module_id=module_id)
        return response(
            kind='success',
            data={'updated_count': count},
            message=f'成功更新 {count} 个测试用例',
        )


class WebUITestCaseListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        return WebUITestCaseCreateSerializer if self.request.method == 'POST' else WebUITestCaseSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        queryset = WebUITestCase.objects.filter(project_id=project_id).select_related('module', 'user')
        if self.request.query_params.get('module_id'):
            queryset = queryset.filter(module_id=self.request.query_params['module_id'])
        if self.request.query_params.get('search'):
            search = self.request.query_params['search']
            queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search))
        if self.request.query_params.get('script_status'):
            queryset = queryset.filter(script_status=self.request.query_params['script_status'])
        return queryset.order_by('-created_at')

    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        project_id = kwargs['project_id']
        if payload_project_mismatch(request.data, project_id):
            return response(kind='error', message='project 与 URL 不一致', status_code=400)
        data = request.data.copy()
        data['project'] = project_id
        module_id = data.get('module') or data.get('module_id')
        module_id = module_id or WebUITestModule.ensure_default(project_id).id
        validate_related_project(WebUITestModule, module_id, project_id, 'module')
        data['module'] = module_id
        serializer = self.get_serializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        test_case = serializer.save()
        return response(
            kind='created',
            data=WebUITestCaseDetailSerializer(test_case).data,
            message='测试用例创建成功',
        )


class WebUITestCaseRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WebUITestCaseDetailSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestCase.objects.filter(project_id=project_id).select_related('module', 'user')

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return response(kind='success', data=serializer.data, message='获取测试用例详情成功')

    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        project_id = kwargs['project_id']
        if payload_project_mismatch(request.data, project_id):
            return response(kind='error', message='project 与 URL 不一致', status_code=400)
        data = request.data.copy()
        if 'module' in data or 'module_id' in data:
            module_id = data.get('module') or data.get('module_id')
            module_id = module_id or WebUITestModule.ensure_default(project_id).id
            validate_related_project(WebUITestModule, module_id, project_id, 'module_id')
            data['module_id'] = module_id
            data.pop('module', None)
        serializer = self.get_serializer(
            self.get_object(),
            data=data,
            partial=kwargs.get('partial', False),
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response(kind='success', data=serializer.data, message='测试用例更新成功')

    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        test_case = self.get_object()
        title = test_case.title
        test_case.delete()
        return response(kind='success', message=f"测试用例 '{title}' 删除成功")


class ExecuteWebUITestCaseView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EXECUTE)
    def post(self, request, project_id, pk):
        from .tasks import execute_webui_test_case_task

        test_case = get_object_or_404(WebUITestCase, pk=pk, project_id=project_id)
        if not test_case.test_script_content:
            return response(kind='error', message='测试用例没有可执行脚本', status_code=400)
        environment_id = request.data.get('environment_id')
        environment = get_object_or_404(
            Environment,
            id=environment_id,
            project_id=project_id,
            category=Environment.EnvironmentCategory.WEB,
            is_active=True,
        )
        base_url = ((environment.config or {}).get('base_url') or '').rstrip('/')
        if not base_url:
            return response(kind='error', message='WebUI 测试环境缺少基础 URL', status_code=400)
        try:
            options = normalize_webui_execution_options(request.data.get('options'))
            runtime_variables = normalize_variable_definitions(
                request.data.get('runtime_variables') or []
            )
        except (ValueError, ExecutionVariableError) as exc:
            return response(kind='error', message=str(exc), status_code=400)
        execution = WebUITestExecution.objects.create(
            exec_type='case',
            name=test_case.title,
            description=test_case.description,
            executor=request.user,
            project=test_case.project,
            environment=environment,
            browser=WEBUI_BROWSER_ENGINE,
            status='pending',
            trigger_type='manual',
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=execution,
            test_case=test_case,
            status='pending',
        )
        store_runtime_variables(execution.id, runtime_variables)
        task = execute_webui_test_case_task.delay(
            execution.id,
            options,
            test_case.test_script_content,
            base_url,
        )
        execution.task_id = task.id
        execution.save(update_fields=['task_id', 'updated_at'])
        return response(
            kind='success',
            data={'execution_id': execution.id, 'task_id': task.id, 'status': execution.status},
            message='测试用例执行已启动',
        )


class WebUITestSuiteListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return WebUITestSuiteCreateSerializer if self.request.method == 'POST' else WebUITestSuiteSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestSuite.objects.filter(project_id=project_id).prefetch_related(
            'case_memberships__test_case'
        )

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        if request.GET.get('status'):
            queryset = queryset.filter(status=request.GET['status'])
        if request.GET.get('search'):
            search = request.GET['search']
            queryset = queryset.filter(
                models.Q(name__icontains=search) | models.Q(description__icontains=search)
            )
        return response(
            kind='paginated_queryset',
            data=queryset,
            page=max(int(request.query_params.get('page', 1)), 1),
            page_size=min(max(int(request.query_params.get('page_size', 20)), 1), 100),
            serializer_class=WebUITestSuiteSerializer,
            message='获取测试套件列表成功',
        )

    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        project_id = kwargs['project_id']
        if payload_project_mismatch(request.data, project_id):
            return response(kind='error', message='project 与 URL 不一致', status_code=400)
        data = request.data.copy()
        data['project'] = project_id
        serializer = self.get_serializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        suite = serializer.save()
        return response(
            kind='created',
            data=WebUITestSuiteSerializer(suite).data,
            message='测试套件创建成功',
        )


class WebUITestSuiteRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return WebUITestSuiteUpdateSerializer if self.request.method in {'PUT', 'PATCH'} else WebUITestSuiteSerializer

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestSuite.objects.filter(project_id=project_id).prefetch_related(
            'case_memberships__test_case'
        )

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return response(kind='success', data=serializer.data, message='获取测试套件详情成功')

    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        if payload_project_mismatch(request.data, kwargs['project_id']):
            return response(kind='error', message='project 与 URL 不一致', status_code=400)
        serializer = self.get_serializer(
            self.get_object(),
            data=request.data,
            partial=kwargs.get('partial', False),
        )
        serializer.is_valid(raise_exception=True)
        suite = serializer.save()
        return response(
            kind='success',
            data=WebUITestSuiteSerializer(suite).data,
            message='测试套件更新成功',
        )

    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        suite = self.get_object()
        name = suite.name
        suite.delete()
        return response(kind='success', message=f"测试套件 '{name}' 删除成功")


class WebUITestSuiteAddTestCaseView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, pk):
        suite = get_object_or_404(WebUITestSuite, pk=pk, project_id=project_id)
        serializer = WebUITestSuiteAddTestCaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case_ids = serializer.validated_data['test_case_ids']
        cases = {
            item.id: item
            for item in WebUITestCase.objects.filter(id__in=case_ids, project_id=project_id)
        }
        missing = [case_id for case_id in case_ids if case_id not in cases]
        if missing:
            return response(
                kind='error',
                message=f'测试用例不存在或不属于当前项目: {missing}',
                status_code=400,
            )
        added = []
        skipped = []
        with transaction.atomic():
            for case_id in case_ids:
                if suite.case_memberships.filter(test_case_id=case_id).exists():
                    skipped.append(cases[case_id].title)
                    continue
                suite.add_test_case(cases[case_id])
                added.append(cases[case_id].title)
        return response(
            kind='success',
            data={'added_cases': added, 'skipped_cases': skipped},
            message=f'成功添加 {len(added)} 个测试用例',
        )


class WebUITestSuiteRemoveTestCaseView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def delete(self, request, project_id, pk, test_case_id):
        membership = get_object_or_404(
            WebUITestSuiteCase,
            suite_id=pk,
            suite__project_id=project_id,
            test_case_id=test_case_id,
        )
        title = membership.test_case.title
        membership.delete()
        return response(kind='success', message=f"测试用例 '{title}' 已从套件移除")


class WebUITestSuiteReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, pk):
        suite = get_object_or_404(WebUITestSuite, pk=pk, project_id=project_id)
        case_ids = request.data.get('test_case_ids')
        if not isinstance(case_ids, list) or not case_ids:
            return response(
                kind='error',
                message='test_case_ids 必须是包含全部套件用例的有序列表',
                status_code=400,
            )
        try:
            case_ids = [int(case_id) for case_id in case_ids]
        except (TypeError, ValueError):
            return response(kind='error', message='test_case_ids 只能包含整数', status_code=400)
        if len(set(case_ids)) != len(case_ids):
            return response(kind='error', message='test_case_ids 不能重复', status_code=400)
        memberships = list(suite.case_memberships.all())
        by_case = {item.test_case_id: item for item in memberships}
        if set(case_ids) != set(by_case):
            return response(kind='error', message='排序列表必须与套件当前用例完全一致', status_code=400)
        for order, case_id in enumerate(case_ids, start=1):
            by_case[case_id].order = order
        WebUITestSuiteCase.objects.bulk_update(memberships, ['order'])
        return response(
            kind='success',
            data=WebUITestSuiteSerializer(suite).data,
            message='测试套件执行顺序已更新',
        )


class ExecuteWebUITestSuiteView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EXECUTE)
    def post(self, request, project_id, pk):
        suite = get_object_or_404(WebUITestSuite, pk=pk, project_id=project_id)
        total_cases = suite.case_memberships.count()
        if not total_cases:
            return response(kind='error', message='测试套件中没有测试用例', status_code=400)
        environment = get_object_or_404(
            Environment,
            id=request.data.get('environment_id'),
            project_id=project_id,
            category=Environment.EnvironmentCategory.WEB,
            is_active=True,
        )
        try:
            options = normalize_webui_execution_options(request.data.get('options'))
            runtime_variables = normalize_variable_definitions(
                request.data.get('runtime_variables') or []
            )
        except (ValueError, ExecutionVariableError) as exc:
            return response(kind='error', message=str(exc), status_code=400)
        execution = WebUITestExecution.objects.create(
            exec_type='suite',
            name=suite.name,
            description=suite.description,
            executor=request.user,
            project=suite.project,
            environment=environment,
            browser=WEBUI_BROWSER_ENGINE,
            status='pending',
            trigger_type='manual',
        )
        WebUITestSuiteExecutionDetail.objects.create(
            execution=execution,
            test_suite=suite,
            total_cases=total_cases,
        )
        store_runtime_variables(execution.id, runtime_variables)
        task = execute_webui_test_suite_task.delay(execution.id, request.user.id, options)
        execution.task_id = task.id
        execution.save(update_fields=['task_id', 'updated_at'])
        return response(
            kind='success',
            data={
                'execution_id': execution.id,
                'task_id': task.id,
                'test_suite_name': suite.name,
                'total_cases': total_cases,
                'status': execution.status,
            },
            message='测试套件执行任务已启动',
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT)
def get_webui_test_suite_statistics(request, project_id):
    suites = WebUITestSuite.objects.filter(project_id=project_id)
    executions = WebUITestExecution.objects.filter(project_id=project_id, exec_type='suite')
    completed = executions.filter(status__in=['passed', 'failed', 'error']).count()
    passed = executions.filter(status='passed').count()
    return response(
        kind='success',
        data={
            'total_suites': suites.count(),
            'active_suites': suites.filter(status='active').count(),
            'total_suite_executions': executions.count(),
            'passed_suite_executions': passed,
            'failed_suite_executions': executions.filter(status__in=['failed', 'error']).count(),
            'total_suite_cases': WebUITestSuiteCase.objects.filter(suite__project_id=project_id).count(),
            'suite_success_rate': round(passed / completed * 100, 2) if completed else 0,
        },
        message='获取测试套件统计成功',
    )


class TestExecutionListView(generics.ListAPIView):
    serializer_class = WebUITestExecutionListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, REPORT)
        queryset = WebUITestExecution.objects.filter(project_id=project_id).select_related(
            'executor', 'environment', 'project'
        )
        if self.request.GET.get('exec_type') in {'case', 'suite'}:
            queryset = queryset.filter(exec_type=self.request.GET['exec_type'])
        if self.request.GET.get('status') in {'pending', 'running', 'passed', 'failed', 'error', 'stopped'}:
            queryset = queryset.filter(status=self.request.GET['status'])
        if self.request.GET.get('trigger_type'):
            queryset = queryset.filter(trigger_type=self.request.GET['trigger_type'])
        return queryset.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        return response(
            kind='paginated_queryset',
            data=self.get_queryset(),
            page=max(int(request.query_params.get('page', 1)), 1),
            page_size=min(max(int(request.query_params.get('page_size', 20)), 1), 100),
            serializer_class=self.get_serializer_class(),
            message='获取执行记录列表成功',
        )


class TestCaseExecutionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        detail = get_object_or_404(
            WebUITestCaseExecutionDetail.objects.select_related('execution', 'test_case'),
            execution_id=pk,
            execution__project_id=project_id,
            execution__exec_type='case',
        )
        return response(
            kind='success',
            data=WebUITestCaseExecutionDetailSerializer(detail).data,
            message='获取单用例执行详情成功',
        )


class TestSuiteExecutionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        detail = get_object_or_404(
            WebUITestSuiteExecutionDetail.objects.select_related(
                'execution', 'test_suite'
            ).prefetch_related('case_executions__test_case'),
            execution_id=pk,
            execution__project_id=project_id,
            execution__exec_type='suite',
        )
        return response(
            kind='success',
            data=WebUITestSuiteExecutionDetailSerializer(detail).data,
            message='获取套件执行详情成功',
        )


class TestExecutionCasesView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        execution = get_object_or_404(
            WebUITestExecution,
            pk=pk,
            project_id=project_id,
            exec_type='suite',
        )
        detail = get_object_or_404(WebUITestSuiteExecutionDetail, execution=execution)
        cases = detail.case_executions.select_related('test_case__module').order_by('id')
        data = [
            {
                'id': item.id,
                'test_case_id': item.test_case_id,
                'test_case_title': item.test_case.title,
                'test_case_description': item.test_case.description,
                'test_case_module': item.test_case.module.name if item.test_case.module else None,
                'name': item.name,
                'status': item.status,
                'status_display': item.get_status_display(),
                'duration': item.duration,
                'error_message': item.error_message,
                'log': item.log,
                'screenshot_path': safe_screenshot_relative_path(item.screenshot_path),
                'video_path': item.video_path,
                'stdout': item.stdout,
            }
            for item in cases
        ]
        return response(
            kind='success',
            data={
                'suite_summary': {
                    'test_suite_id': detail.test_suite_id,
                    'test_suite_name': detail.test_suite.name,
                    'total_cases': detail.total_cases,
                    'passed_cases': detail.passed_cases,
                    'failed_cases': detail.failed_cases,
                    'skipped_cases': detail.skipped_cases,
                    'pass_rate': detail.pass_rate,
                    'start_time': detail.start_time,
                    'end_time': detail.end_time,
                    'duration': detail.duration,
                },
                'cases': data,
                'total_cases': len(data),
            },
            message='获取套件子用例执行详情成功',
        )


def _resolve_screenshot_file(relative_path, execution_id=None):
    normalized = safe_screenshot_relative_path(relative_path)
    if not normalized:
        raise Http404('截图不存在')
    if execution_id is not None:
        expected_prefix = f'webui_failure_screenshots/execution_{int(execution_id)}/'
        if not normalized.startswith(expected_prefix):
            raise Http404('截图不存在')
    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    candidate = os.path.realpath(os.path.join(media_root, normalized))
    if os.path.commonpath([media_root, candidate]) != media_root or not os.path.isfile(candidate):
        raise Http404('截图不存在')
    return candidate


class TestExecutionScreenshotView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(REPORT)
    def get(self, request, project_id, pk, case_pk=None):
        execution = get_object_or_404(WebUITestExecution, pk=pk, project_id=project_id)
        if execution.exec_type == 'case':
            detail = getattr(execution, 'case_execution_detail', None)
            screenshot_path = detail.screenshot_path if detail else None
        elif case_pk is not None:
            case_execution = get_object_or_404(
                WebUITestSuiteCaseExecution,
                pk=case_pk,
                suite_execution__execution=execution,
            )
            screenshot_path = case_execution.screenshot_path
        else:
            raise Http404('套件执行需要指定子用例截图')
        return FileResponse(
            open(_resolve_screenshot_file(screenshot_path, execution.id), 'rb'),
            content_type='image/png',
        )


class TestExecutionDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(DELETE)
    def delete(self, request, project_id, pk):
        execution = get_object_or_404(WebUITestExecution, pk=pk, project_id=project_id)
        name = execution.name
        _remove_failure_screenshots(execution.id)
        execution.delete()
        return response(kind='success', message=f"执行记录 '{name}' 删除成功")
