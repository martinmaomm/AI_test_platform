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
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.api import response
from projects.models import Environment, Project
from .models import APITestCase, APIWorkspace, default_api_workspace_draft
from .workspace_service import (
    WorkspaceConflict, WorkspaceValidationError, _UNSET, append_message,
    can_edit_project, can_execute_project, create_or_update_case, endpoint_specs, expire_stalled_workspace,
    generation_endpoint_specs, infer_spec_id, validate_spec_id,
    normalize_draft, require_executable_draft, require_generation_model_id, validate_model_id,
    owned_workspace, require_revision, serialize_workspace, update_workspace_draft,
    workspace_is_busy,
)
from .workspace_tasks import debug_api_workspace, generate_and_verify_api_workspace
from .workspace_verification import require_target_url


def _problem(exc: Exception, status_code: int = 400):
    return response(kind='error', message=str(exc), status_code=status_code)


def _project_or_denied(project_id: int, user) -> Project:
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_project(project, user):
        raise PermissionError('没有权限编辑此项目的 API 工作区。')
    return project


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


def _queue_pipeline(workspace: APIWorkspace, *, revision: int, mode: str, target_url: str,
                    variables: dict[str, Any], endpoints: list[dict[str, Any]],
                    prompt_draft: dict[str, Any] | None = None, failure_evidence: dict[str, Any] | None = None,
                    scenario_workflow: bool = False):
    task_id = str(uuid.uuid4())
    workspace.status = APIWorkspace.Status.GENERATING
    workspace.error = ''
    workspace.candidate = None
    workspace.task_id = task_id
    snapshot = {
        'revision': revision, 'task_id': task_id, 'mode': mode,
        'draft': deepcopy(prompt_draft if prompt_draft is not None else workspace.draft),
        'model_id': workspace.model_id, 'spec_id': workspace.spec_id, 'endpoints': deepcopy(endpoints),
        'target_url': target_url, 'variables': deepcopy(variables), 'messages': deepcopy(workspace.messages),
        'failure_evidence': deepcopy(failure_evidence), 'queued_at': timezone.now().isoformat(),
    }
    if scenario_workflow:
        snapshot.update({'workflow': 'scenarios', 'scope_endpoint_ids': [item['id'] for item in endpoints]})
    elif workspace.parent_id:
        snapshot['scenario'] = {
            'title': workspace.title,
            'description': workspace.scenario_description,
            'endpoint_ids': list(workspace.endpoint_ids or []),
        }
    workspace.generation = {
        'status': 'queued', 'phase': 'queued', 'attempt': 0, 'max_attempts': 3,
        'source_revision': revision, 'target_url': target_url, 'summary': '', 'rounds': [],
        'adopted_revision': None,
        '_snapshot': snapshot,
    }
    if scenario_workflow:
        workspace.generation.update({
            'phase': 'planning', 'plan': {}, 'scenario_ids': [], 'active_scenario_id': None,
            'current_scenario': 0, 'total_scenarios': 0,
        })
    workspace.save()

    def dispatch():
        try:
            generate_and_verify_api_workspace.apply_async(args=(workspace.id, revision, task_id), task_id=task_id)
        except Exception as exc:
            from .workspace_tasks import _finish_pipeline
            _finish_pipeline(workspace.id, revision, task_id, 'failed', str(exc) or '生成并试运行任务提交失败。')

    transaction.on_commit(dispatch)


def _repair_seed(workspace: APIWorkspace, revision: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze only server-held, current-revision failed evidence for a repair run."""
    candidate = workspace.candidate if isinstance(workspace.candidate, dict) else {}
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    rounds = generation.get('rounds') if isinstance(generation.get('rounds'), list) else []
    if candidate.get('source_revision') == revision and isinstance(candidate.get('draft'), dict):
        candidate_hash = candidate.get('draft_hash')
        for item in reversed(rounds):
            if not isinstance(item, dict) or item.get('draft_hash') != candidate_hash:
                continue
            result = item.get('result')
            if isinstance(result, dict) and result.get('success') is not True:
                return candidate['draft'], result
    if workspace.debug_revision == revision and isinstance(workspace.debug_result, dict) and workspace.debug_result:
        if workspace.debug_result.get('success') is not True:
            return workspace.draft, workspace.debug_result
    raise WorkspaceValidationError('修复需要当前 revision 的失败调试或候选运行证据。')


class APIWorkspaceCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        try:
            _project_or_denied(project_id, request.user)
        except PermissionError as exc:
            return _problem(exc, 403)
        workspaces = [
            expire_stalled_workspace(item)
            for item in APIWorkspace.objects.filter(project_id=project_id, owner=request.user, parent__isnull=True).select_related('saved_case')
        ]
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
            inferred_spec_id = request.data.get('spec_id')
            if inferred_spec_id is None:
                inferred_spec_id = infer_spec_id(project.id, endpoint_ids)
                if case and case.endpoint_id:
                    inferred_spec_id = case.endpoint.spec_id
            spec = validate_spec_id(project.id, inferred_spec_id)
            endpoint_specs(project.id, endpoint_ids, spec_id=spec.id if spec else None)
            model_id = validate_model_id(request.data.get('model_id'), owner=request.user)
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
                model_id=model_id, spec=spec, endpoint_ids=endpoint_ids, draft=draft,
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
                    spec_id=request.data['spec_id'] if 'spec_id' in request.data else _UNSET,
                    title=request.data['title'] if 'title' in request.data else _UNSET,
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

    def delete(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            if request.data.get('confirmed') is not True:
                raise WorkspaceValidationError('删除工作区必须明确传递 confirmed:true。')
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                require_revision(workspace, request.data.get('revision'))
                if workspace.parent_id:
                    raise WorkspaceValidationError('子场景不能单独删除，请删除根工作区。')
                if workspace_is_busy(workspace):
                    raise WorkspaceConflict('根工作区或子场景正在运行，不能删除。')
                workspace.delete()
            return response(kind='success', data={'id': workspace_id}, message='工作区已删除')
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
            project = _project_or_denied(project_id, request.user)
            if not can_execute_project(project, request.user):
                raise PermissionError('没有权限执行此项目的 API 试运行。')
            message = request.data.get('message')
            mode = request.data.get('mode', 'generate')
            if not isinstance(message, str) or not message.strip():
                raise WorkspaceValidationError('message 不能为空。')
            if mode not in {'generate', 'repair'}:
                raise WorkspaceValidationError('mode 仅支持 generate 或 repair。')
            if 'revision' not in request.data:
                raise WorkspaceValidationError('messages 必须携带 revision。')
            if request.data.get('execution_confirmed') is not True:
                raise WorkspaceValidationError('生成并试运行必须明确传递 execution_confirmed:true。')
            target_url = require_target_url(request.data.get('base_url'))
            variables = request.data.get('variables', {})
            if not isinstance(variables, dict):
                raise WorkspaceValidationError('variables 必须是对象。')
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                revision = require_revision(workspace, request.data['revision'])
                if workspace_is_busy(workspace):
                    raise WorkspaceConflict('当前工作区任务尚未结束。')
                require_generation_model_id(workspace.model_id, owner=workspace.owner)
                endpoints = generation_endpoint_specs(workspace)
                prompt_draft, failure_evidence = (workspace.draft, None)
                if mode == 'repair':
                    prompt_draft, failure_evidence = _repair_seed(workspace, revision)
                append_message(workspace, role='user', content=message.strip(), mode=mode)
                scenario_workflow = (
                    mode == 'generate' and workspace.parent_id is None and workspace.saved_case_id is None
                )
                _queue_pipeline(
                    workspace, revision=revision, mode=mode, target_url=target_url, variables=variables,
                    endpoints=endpoints, prompt_draft=prompt_draft, failure_evidence=failure_evidence,
                    scenario_workflow=scenario_workflow,
                )
            return response(kind='success', data=serialize_workspace(workspace), message='生成并试运行已排队', status_code=202)
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
                if workspace_is_busy(workspace):
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
                description=request.data['description'] if 'description' in request.data else _UNSET,
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
