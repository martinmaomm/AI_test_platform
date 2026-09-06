"""HTTP boundary for the persistent API workspace.

Legacy API-testing views remain untouched.  All responses use the project's
standard envelope; the documented workspace fields live in ``data``.
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.api import response
from projects.models import Environment, Project
from .models import APITestCase, APIWorkspace, default_api_workspace_draft
from .workspace_service import (
    WorkspaceConflict, WorkspaceValidationError, _UNSET, append_message,
    can_edit_project, can_execute_project, create_or_update_case, endpoint_specs, expire_stalled_workspace,
    normalize_draft, require_executable_draft, validate_model_id,
    owned_workspace, require_revision, serialize_workspace, update_workspace_draft,
)
from .workspace_tasks import debug_api_workspace, generate_api_workspace_candidate


def _problem(exc: Exception, status_code: int = 400):
    return response(kind='error', message=str(exc), status_code=status_code)


def _project_or_denied(project_id: int, user) -> Project:
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_project(project, user):
        raise PermissionError('没有权限编辑此项目的 API 工作区。')
    return project


def _queue_generation(workspace: APIWorkspace, *, revision: int, mode: str,
                      endpoint_snapshot: list[dict[str, Any]], failure_evidence: dict[str, Any] | None):
    task_id = str(uuid.uuid4())
    workspace.status = APIWorkspace.Status.GENERATING
    workspace.error = ''
    workspace.candidate = None
    workspace.task_id = task_id
    workspace.save()

    def dispatch():
        try:
            generate_api_workspace_candidate.apply_async(
                args=(workspace.id, revision, task_id, mode, endpoint_snapshot, failure_evidence), task_id=task_id,
            )
        except Exception as exc:  # broker errors must not strand the workspace in generating
            from .workspace_tasks import _finish_candidate
            _finish_candidate(
                workspace_id=workspace.id, revision=revision, task_id=task_id, mode=mode,
                candidate=None, summary='候选任务提交失败。', error=str(exc) or '候选任务提交失败。',
            )

    transaction.on_commit(dispatch)


def _queue_debug(workspace: APIWorkspace, *, revision: int, environment: dict[str, Any], variables: dict[str, Any]):
    task_id = str(uuid.uuid4())
    workspace.status = APIWorkspace.Status.DEBUGGING
    workspace.error = ''
    workspace.task_id = task_id
    workspace.debug_revision = revision
    workspace.debug_result = {'status': 'queued'}
    workspace.debug_snapshot = {
        'revision': revision, 'draft': deepcopy(workspace.draft),
        'environment': deepcopy(environment), 'variables': deepcopy(variables),
    }
    workspace.save()

    def dispatch():
        try:
            debug_api_workspace.apply_async(args=(workspace.id, revision, task_id), task_id=task_id)
        except Exception as exc:  # broker errors must still reach a terminal state
            from .workspace_tasks import _finish_debug
            _finish_debug(
                workspace_id=workspace.id, revision=revision, task_id=task_id,
                result=None, error=str(exc) or '调试任务提交失败。',
            )

    transaction.on_commit(dispatch)


class APIWorkspaceCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        try:
            _project_or_denied(project_id, request.user)
        except PermissionError as exc:
            return _problem(exc, 403)
        workspaces = [expire_stalled_workspace(item) for item in APIWorkspace.objects.filter(project_id=project_id, owner=request.user)]
        return response(kind='success', data=[serialize_workspace(item) for item in workspaces], message='获取工作区成功')

    def post(self, request, project_id):
        try:
            project = _project_or_denied(project_id, request.user)
            case = None
            case_id = request.data.get('case_id')
            if case_id is not None:
                if not isinstance(case_id, int):
                    raise WorkspaceValidationError('case_id 必须是整数。')
                case = APITestCase.objects.get(pk=case_id, project=project)
            endpoint_ids = request.data.get('endpoint_ids', [])
            if not isinstance(endpoint_ids, list):
                raise WorkspaceValidationError('endpoint_ids 必须是数组。')
            endpoint_specs(project.id, endpoint_ids)
            model_id = validate_model_id(request.data.get('model_id'))
            if case and case.script_content:
                try:
                    draft = normalize_draft(json.loads(case.script_content))
                except (ValueError, TypeError, json.JSONDecodeError):
                    raise WorkspaceValidationError('关联用例的现有脚本无法解析为 requests 用例契约，请重新生成草稿。')
            else:
                draft = default_api_workspace_draft()
            workspace = APIWorkspace.objects.create(
                project=project, owner=request.user, saved_case=case,
                saved_case_updated_at=case.updated_at if case else None,
                title=str(request.data.get('title') or (case.title if case else ''))[:200],
                model_id=model_id, endpoint_ids=endpoint_ids, draft=draft,
            )
            return response(kind='success', data=serialize_workspace(workspace), message='创建工作区成功', status_code=201)
        except APITestCase.DoesNotExist:
            return _problem(WorkspaceValidationError('关联用例不存在或不属于当前项目。'), 404)
        except PermissionError as exc:
            return _problem(exc, 403)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user)
            return response(kind='success', data=serialize_workspace(expire_stalled_workspace(workspace)), message='获取工作区详情成功')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)

    def patch(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            if 'revision' not in request.data:
                raise WorkspaceValidationError('PATCH 必须携带 revision。')
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                workspace = update_workspace_draft(
                    workspace, revision=request.data['revision'],
                    draft=request.data['draft'] if 'draft' in request.data else _UNSET,
                    model_id=request.data['model_id'] if 'model_id' in request.data else _UNSET,
                    endpoint_ids=request.data['endpoint_ids'] if 'endpoint_ids' in request.data else _UNSET,
                )
            return response(kind='success', data=serialize_workspace(workspace), message='工作区草稿已更新')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            message = request.data.get('message')
            mode = request.data.get('mode', 'generate')
            if not isinstance(message, str) or not message.strip():
                raise WorkspaceValidationError('message 不能为空。')
            if mode not in {'generate', 'repair'}:
                raise WorkspaceValidationError('mode 仅支持 generate 或 repair。')
            if 'revision' not in request.data:
                raise WorkspaceValidationError('messages 必须携带 revision。')
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                revision = require_revision(workspace, request.data['revision'])
                if workspace.status in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
                    raise WorkspaceConflict('当前工作区任务尚未结束。')
                validate_model_id(workspace.model_id)
                failure_evidence = None
                if mode == 'repair':
                    if workspace.debug_revision != revision or not isinstance(workspace.debug_result, dict) or not workspace.debug_result:
                        raise WorkspaceValidationError('修复需要当前 revision 的调试失败证据。')
                    if workspace.debug_result.get('success') is True:
                        raise WorkspaceValidationError('当前调试没有失败证据，不能生成修复候选。')
                    failure_evidence = deepcopy(workspace.debug_result)
                append_message(workspace, role='user', content=message.strip(), mode=mode)
                _queue_generation(
                    workspace, revision=revision, mode=mode,
                    endpoint_snapshot=endpoint_specs(project_id, workspace.endpoint_ids),
                    failure_evidence=failure_evidence,
                )
            return response(kind='success', data=serialize_workspace(workspace), message='候选生成已排队', status_code=202)
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceDebugView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, workspace_id):
        try:
            project = _project_or_denied(project_id, request.user)
            if not can_execute_project(project, request.user):
                raise PermissionError('没有权限执行此项目的 API 调试。')
            if 'revision' not in request.data:
                raise WorkspaceValidationError('debug 必须携带 revision。')
            variables = request.data.get('variables', {})
            if not isinstance(variables, dict):
                raise WorkspaceValidationError('variables 必须是对象。')
            environment = {}
            environment_id = request.data.get('environment_id')
            if environment_id is not None:
                environment_obj = Environment.objects.get(pk=environment_id, project_id=project_id, category='api', is_active=True)
                environment = environment_obj.get_api_config() or {}
            environment_variables = environment.get('variables') if isinstance(environment.get('variables'), dict) else {}
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                revision = require_revision(workspace, request.data['revision'])
                if workspace.status in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
                    raise WorkspaceConflict('当前工作区任务尚未结束。')
                executable_draft = require_executable_draft(workspace.draft)
                draft_variables = executable_draft['config'].get('variables', {})
                if not isinstance(draft_variables, dict):
                    raise WorkspaceValidationError('draft.config.variables 必须是对象。')
                # Environment defaults < persisted draft variables < this debug invocation.
                frozen_variables = {**environment_variables, **draft_variables, **variables}
                _queue_debug(workspace, revision=revision, environment=environment, variables=frozen_variables)
            return response(kind='success', data=serialize_workspace(workspace), message='调试已排队', status_code=202)
        except Environment.DoesNotExist:
            return _problem(WorkspaceValidationError('API 环境不存在或不属于当前项目。'), 404)
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceSaveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            if 'revision' not in request.data:
                raise WorkspaceValidationError('save 必须携带 revision。')
            workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user)
            workspace = create_or_update_case(
                workspace, revision=request.data['revision'], title=request.data.get('title'),
                description=request.data.get('description'),
            )
            return response(kind='success', data=serialize_workspace(workspace), message='工作区已保存为 API 用例')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspacePythonView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user)
            # Implemented by the requests-core owner; no HttpRunner fallback is permitted.
            from .case_contract import export_python
            code = export_python(normalize_draft(workspace.draft))
            filename = f"api_workspace_{workspace.id}_r{workspace.revision}.py"
            return response(kind='success', data={'code': code, 'filename': filename, 'revision': workspace.revision}, message='Python 导出成功')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except ImportError:
            return _problem(WorkspaceValidationError('requests 导出核心尚未可用。'), 503)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)
