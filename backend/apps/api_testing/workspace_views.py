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
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.api import response
from projects.models import Environment, Project
from .models import APITestCase, APIWorkspace, APISpecification, default_api_workspace_draft
from .workspace_service import (
    WorkspaceConflict, WorkspaceValidationError, _UNSET, append_message,
    can_edit_project, can_execute_project, create_or_update_case, debug_timeout_seconds,
    endpoint_specs, expire_stalled_workspace,
    generation_budget, generation_endpoint_specs, infer_spec_id, validate_spec_id,
    generation_repair_seed, generation_retry_metadata,
    normalize_draft, require_executable_draft, require_generation_model_id, validate_model_id,
    owned_workspace, require_revision, serialize_workspace, update_workspace_draft,
    workspace_is_busy, scenario_authenticated_endpoint_ids, validate_target_draft,
    validate_target_endpoint_id,
)
from .workspace_evidence import attach_execution_histories, stop_workspace_executions
from .workspace_tasks import debug_api_workspace, generate_and_verify_api_workspace
from .workspace_verification import require_target_url


def _problem(exc: Exception, status_code: int = 400):
    return response(kind='error', message=str(exc), status_code=status_code)


def _workspace_payload(workspace: APIWorkspace) -> dict[str, Any]:
    family = [workspace]
    if workspace.parent_id is None:
        prefetched = getattr(workspace, '_prefetched_objects_cache', {})
        if 'scenarios' in prefetched:
            family.extend(list(workspace.scenarios.all()))
        else:
            children = list(workspace.scenarios.select_related('saved_case').order_by('scenario_order', 'id'))
            workspace._prefetched_objects_cache = {**prefetched, 'scenarios': children}
            family.extend(children)
    attach_execution_histories(family)
    return serialize_workspace(workspace)


def _draft_endpoint_ids(draft: dict[str, Any]) -> list[int]:
    """Keep a saved case's ordered endpoint scope without trusting arbitrary metadata."""
    endpoint_ids: list[int] = []
    for step in draft.get('teststeps', []):
        endpoint_id = step.get('endpoint_id') if isinstance(step, dict) else None
        if isinstance(endpoint_id, int) and not isinstance(endpoint_id, bool) and endpoint_id > 0:
            if endpoint_id not in endpoint_ids:
                endpoint_ids.append(endpoint_id)
    return endpoint_ids


def _project_or_denied(project_id: int, user) -> Project:
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_project(project, user):
        raise PermissionError('没有权限编辑此项目的 API 工作区。')
    return project


def _queue_debug(workspace: APIWorkspace, *, revision: int, environment: dict[str, Any], variables: dict[str, Any]):
    task_id = str(uuid.uuid4())
    budget = generation_budget(model_id=workspace.model_id, owner=workspace.owner)
    budget['timeouts']['execution_seconds'] = debug_timeout_seconds()
    budget['timeouts']['batch_seconds'] = None
    budget['timeouts']['llm_seconds'] = None
    workspace.status = APIWorkspace.Status.DEBUGGING
    workspace.error = ''
    workspace.task_id = task_id
    workspace.debug_revision = revision
    workspace.debug_result = {
        'status': 'queued',
        **{key: deepcopy(budget[key]) for key in ('queued_at', 'claimed_at', 'started_at', 'finished_at', 'timeouts', 'deadlines')},
    }
    workspace.debug_snapshot = {
        'revision': revision, 'draft': deepcopy(workspace.draft),
        'environment': deepcopy(environment), 'variables': deepcopy(variables),
        'task_id': task_id, 'budget': deepcopy(budget),
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
                    scenario_workflow: bool = False,
                    retry_snapshot: dict[str, Any] | None = None,
                    retry_previous: dict[str, Any] | None = None,
                    preserve_candidate: bool = False):
    task_id = str(uuid.uuid4())
    budget = generation_budget(model_id=workspace.model_id, owner=workspace.owner)
    workspace.status = APIWorkspace.Status.GENERATING
    workspace.error = ''
    if not preserve_candidate:
        workspace.candidate = None
    workspace.task_id = task_id
    if retry_snapshot is not None:
        snapshot = deepcopy(retry_snapshot)
        for key in ('claimed_at', 'started_at', 'finished_at', 'parent_task_id', 'parent_revision',
                    'queue_managed_by_parent'):
            snapshot.pop(key, None)
        snapshot.update({
            'revision': revision, 'task_id': task_id, 'mode': mode,
            'draft': deepcopy(prompt_draft if prompt_draft is not None else workspace.draft),
            'model_id': workspace.model_id, 'spec_id': workspace.spec_id,
            'target_endpoint_id': workspace.target_endpoint_id,
            'target_url': target_url, 'variables': deepcopy(variables),
            'failure_evidence': deepcopy(failure_evidence),
        })
        scenario_workflow = snapshot.get('workflow') == 'scenarios'
    else:
        snapshot = {
            'revision': revision, 'task_id': task_id, 'mode': mode,
            'draft': deepcopy(prompt_draft if prompt_draft is not None else workspace.draft),
            'user_draft': deepcopy(workspace.draft),
            'model_id': workspace.model_id, 'spec_id': workspace.spec_id,
            'target_endpoint_id': workspace.target_endpoint_id, 'endpoints': deepcopy(endpoints),
            'target_url': target_url, 'variables': deepcopy(variables), 'messages': deepcopy(workspace.messages),
            'failure_evidence': deepcopy(failure_evidence),
        }
    snapshot.update(deepcopy(budget))
    if scenario_workflow and retry_snapshot is None:
        snapshot.update({'workflow': 'scenarios', 'scope_endpoint_ids': [item['id'] for item in endpoints]})
    elif workspace.parent_id and retry_snapshot is None:
        previous_generation = workspace.generation if isinstance(workspace.generation, dict) else {}
        previous_snapshot = previous_generation.get('_snapshot') if isinstance(previous_generation.get('_snapshot'), dict) else {}
        previous_scenario = previous_snapshot.get('scenario') if isinstance(previous_snapshot.get('scenario'), dict) else {}
        shared_constraints = previous_snapshot.get('shared_constraints')
        if not isinstance(shared_constraints, list) or any(not isinstance(item, str) for item in shared_constraints):
            shared_constraints = []
        local_messages = list(workspace.messages) if isinstance(workspace.messages, list) else []
        frozen_scope = previous_snapshot.get('scope_endpoint_ids')
        if not isinstance(frozen_scope, list) or not frozen_scope:
            raise WorkspaceValidationError('子场景没有冻结根依赖范围，不能生成或修复。')
        target_ids = previous_scenario.get('target_endpoint_ids', workspace.endpoint_ids)
        if not isinstance(target_ids, list) or not target_ids:
            raise WorkspaceValidationError('子场景没有冻结业务目标，不能生成或修复。')
        if {item['id'] for item in endpoints} != set(frozen_scope):
            raise WorkspaceValidationError('子场景依赖范围与冻结快照不一致，不能继续执行。')
        authenticated_ids = scenario_authenticated_endpoint_ids(previous_scenario, target_endpoint_ids=target_ids)
        dependency_ids = previous_scenario.get('dependency_endpoint_ids') or []
        detailed_ids = set(target_ids) | set(dependency_ids)
        scope_catalog = [
            {'id': item['id'], 'method': item['method'], 'path': item['path'], 'name': item.get('summary') or ''}
            for item in endpoints
        ]
        frozen_scope_specs = deepcopy(endpoints)
        endpoints = [item for item in endpoints if item['id'] in detailed_ids]
        snapshot.update({
            'scope_endpoint_ids': deepcopy(frozen_scope),
            'scope_catalog': scope_catalog,
            'frozen_scope_specs': frozen_scope_specs,
            'endpoints': deepcopy(endpoints),
            'messages': deepcopy(local_messages),
            'shared_constraints': deepcopy(shared_constraints),
            'scenario': {
                'title': workspace.title, 'description': workspace.scenario_description,
                'endpoint_ids': deepcopy(target_ids), 'target_endpoint_ids': deepcopy(target_ids),
                'available_endpoint_ids': deepcopy(frozen_scope),
                'dependency_endpoint_ids': deepcopy(dependency_ids),
                'dependency_evidence': str(previous_scenario.get('dependency_evidence') or ''),
                'authenticated_endpoint_ids': authenticated_ids,
                'requires_authenticated_context': bool(authenticated_ids),
                **({'test_type': previous_scenario['test_type']} if previous_scenario.get('test_type') else {}),
            },
        })
    archived: dict[str, Any] | None = None
    if isinstance(retry_previous, dict):
        prior_archive = retry_previous.get('_retry_previous') if isinstance(retry_previous.get('_retry_previous'), dict) else {}
        prior_rounds = retry_previous.get('rounds')
        if not isinstance(prior_rounds, list) or not prior_rounds:
            prior_rounds = prior_archive.get('rounds') if isinstance(prior_archive.get('rounds'), list) else []
        archived = {
            'status': retry_previous.get('status'), 'phase': retry_previous.get('phase'),
            'summary': retry_previous.get('summary'), 'source_revision': retry_previous.get('source_revision'),
            'model_failure': deepcopy(retry_previous.get('model_failure')),
            'rounds': deepcopy(prior_rounds),
        }
    workspace.generation = {
        'status': 'queued', 'phase': 'queued', 'attempt': 0, 'max_attempts': 3,
        'source_revision': revision, 'target_url': target_url, 'summary': '', 'rounds': [],
        'adopted_revision': None,
        '_snapshot': snapshot,
        **{key: deepcopy(budget[key]) for key in ('queued_at', 'claimed_at', 'started_at', 'finished_at', 'timeouts', 'deadlines')},
    }
    if archived is not None:
        workspace.generation['_retry_previous'] = archived
    if workspace.parent_id:
        workspace.generation['scenario_context'] = deepcopy(snapshot['scenario'])
        workspace.generation['shared_constraints'] = deepcopy(snapshot.get('shared_constraints') or [])
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
    require_revision(workspace, revision)
    return generation_repair_seed(workspace)


def _validate_retry_snapshot(workspace: APIWorkspace, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Revalidate current authority/scope while retaining old frozen content."""
    if not isinstance(snapshot.get('target_url'), str) or not isinstance(snapshot.get('variables'), dict):
        raise WorkspaceValidationError('失败任务缺少合法的服务器冻结执行参数。')
    require_target_url(snapshot['target_url'])
    frozen_endpoints = snapshot.get('endpoints')
    if not isinstance(frozen_endpoints, list) or not frozen_endpoints:
        raise WorkspaceValidationError('失败任务缺少冻结接口范围，不能安全重试。')
    current = generation_endpoint_specs(workspace)
    frozen_scope = snapshot.get('scope_endpoint_ids')
    expected_ids = frozen_scope if isinstance(frozen_scope, list) and frozen_scope else [
        item.get('id') for item in frozen_endpoints if isinstance(item, dict)
    ]
    if len(current) != len(expected_ids) or {item['id'] for item in current} != set(expected_ids):
        raise WorkspaceValidationError('当前接口范围与失败任务的冻结范围不一致，不能安全重试。')
    return frozen_endpoints


class APIWorkspaceCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        try:
            _project_or_denied(project_id, request.user)
            source_type = request.query_params.get('source_type')
            if source_type not in {None, 'document', 'browser_capture'}:
                raise WorkspaceValidationError('source_type 仅支持 document 或 browser_capture。')
        except PermissionError as exc:
            return _problem(exc, 403)
        except WorkspaceValidationError as exc:
            return _problem(exc)
        workspaces = APIWorkspace.objects.filter(
            project_id=project_id, owner=request.user, parent__isnull=True,
        )
        if source_type == 'browser_capture':
            workspaces = workspaces.filter(spec__spec_type=APISpecification.SpecType.BROWSER_CAPTURE)
        elif source_type == 'document':
            workspaces = workspaces.filter(
                Q(spec__isnull=True) | ~Q(spec__spec_type=APISpecification.SpecType.BROWSER_CAPTURE),
            )
        workspaces = [
            expire_stalled_workspace(item)
            for item in workspaces.select_related('saved_case', 'spec').prefetch_related(
                Prefetch(
                    'scenarios',
                    queryset=APIWorkspace.objects.select_related('saved_case').order_by('scenario_order', 'id'),
                ),
            )
        ]
        family = [item for root in workspaces for item in [root, *list(root.scenarios.all())]]
        attach_execution_histories(family)
        return response(kind='success', data=[serialize_workspace(item) for item in workspaces], message='获取工作区成功')

    def post(self, request, project_id):
        try:
            project = _project_or_denied(project_id, request.user)
            case = None
            case_id = request.data.get('case_id')
            if case_id is not None:
                if not isinstance(case_id, int) or isinstance(case_id, bool) or case_id <= 0:
                    raise WorkspaceValidationError('case_id 必须是正整数。')
                case = APITestCase.objects.select_related('endpoint__spec').get(pk=case_id, project=project)
                if case.endpoint_id and case.endpoint.spec.project_id != project.id:
                    raise WorkspaceValidationError('关联用例的端点不属于当前项目。')
            if case and case.script_content:
                try:
                    draft = normalize_draft(json.loads(case.script_content))
                except (ValueError, TypeError, json.JSONDecodeError):
                    raise WorkspaceValidationError('关联用例的现有脚本无法解析为 requests 用例契约，请重新生成草稿。')
            else:
                draft = default_api_workspace_draft()

            target_endpoint_id = request.data.get('target_endpoint_id')
            if case and case.test_case_type == 'endpoint':
                if not case.endpoint_id:
                    raise WorkspaceValidationError('关联端点用例缺少有效目标端点。')
                if target_endpoint_id is not None and target_endpoint_id != case.endpoint_id:
                    raise WorkspaceValidationError('target_endpoint_id 必须与关联端点用例一致。')
                target_endpoint_id = case.endpoint_id
            elif case and target_endpoint_id is not None:
                raise WorkspaceValidationError('场景用例不能作为显式端点工作区打开。')
            target_endpoint = validate_target_endpoint_id(
                project.id, target_endpoint_id, owner=request.user,
            )

            endpoint_ids_provided = 'endpoint_ids' in request.data
            endpoint_ids = request.data.get('endpoint_ids', [])
            if not isinstance(endpoint_ids, list):
                raise WorkspaceValidationError('endpoint_ids 必须是数组。')
            if not endpoint_ids_provided and case:
                endpoint_ids = _draft_endpoint_ids(draft)
                if not endpoint_ids and case.endpoint_id:
                    endpoint_ids = [case.endpoint_id]
            if target_endpoint_id is not None and not endpoint_ids_provided and target_endpoint_id not in endpoint_ids:
                endpoint_ids.append(target_endpoint_id)
            inferred_spec_id = request.data.get('spec_id')
            if inferred_spec_id is None:
                inferred_spec_id = infer_spec_id(project.id, endpoint_ids)
                if inferred_spec_id is None and target_endpoint is not None:
                    inferred_spec_id = target_endpoint.spec_id
            if endpoint_ids and inferred_spec_id is None:
                raise WorkspaceValidationError('所选端点必须来自同一可用 API 规范，请明确选择 spec_id。')
            spec = validate_spec_id(project.id, inferred_spec_id, owner=request.user)
            endpoint_specs(project.id, endpoint_ids, spec_id=spec.id if spec else None, owner=request.user)
            if target_endpoint_id is not None:
                validate_target_endpoint_id(
                    project.id, target_endpoint_id, spec_id=spec.id if spec else None,
                    endpoint_ids=endpoint_ids, owner=request.user,
                )
                validate_target_draft(
                    draft, project_id=project.id, spec_id=spec.id,
                    endpoint_ids=endpoint_ids, target_endpoint_id=target_endpoint_id,
                    owner=request.user, require_target=bool(draft.get('teststeps')),
                )
            model_id = validate_model_id(request.data.get('model_id'), owner=request.user)
            workspace = APIWorkspace.objects.create(
                project=project, owner=request.user, saved_case=case,
                saved_case_updated_at=case.updated_at if case else None,
                title=str(request.data.get('title') or (case.title if case else ''))[:200],
                model_id=model_id, spec=spec, target_endpoint_id=target_endpoint_id,
                endpoint_ids=endpoint_ids, draft=draft,
            )
            return response(kind='success', data=_workspace_payload(workspace), message='创建工作区成功', status_code=201)
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
            return response(kind='success', data=_workspace_payload(expire_stalled_workspace(workspace)), message='获取工作区详情成功')
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)

    def patch(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            if 'revision' not in request.data:
                raise WorkspaceValidationError('PATCH 必须携带 revision。')
            if 'target_endpoint_id' in request.data:
                raise WorkspaceValidationError('target_endpoint_id 创建后不可通过 PATCH 修改。')
            with transaction.atomic():
                workspace = owned_workspace(project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True)
                workspace = update_workspace_draft(
                    workspace, revision=request.data['revision'],
                    draft=request.data['draft'] if 'draft' in request.data else _UNSET,
                    model_id=request.data['model_id'] if 'model_id' in request.data else _UNSET,
                    endpoint_ids=request.data['endpoint_ids'] if 'endpoint_ids' in request.data else _UNSET,
                    spec_id=request.data['spec_id'] if 'spec_id' in request.data else _UNSET,
                    title=request.data['title'] if 'title' in request.data else _UNSET,
                    assertion_review_ack=request.data['assertion_review_ack'] if 'assertion_review_ack' in request.data else _UNSET,
                )
            return response(kind='success', data=_workspace_payload(workspace), message='工作区草稿已更新')
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
                # A child retains its business target ids for coverage, while
                # this frozen root-scoped projection exposes only selected
                # Swagger dependencies for its self-contained login/setup.
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
            return response(kind='success', data=_workspace_payload(workspace), message='生成并试运行已排队', status_code=202)
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceRetryGenerationView(APIView):
    """Explicitly retry one failed provider call from server-frozen input."""

    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, workspace_id):
        try:
            project = _project_or_denied(project_id, request.user)
            if not can_execute_project(project, request.user):
                raise PermissionError('没有权限执行此项目的 API 试运行。')
            unexpected = set(request.data) - {'revision', 'execution_confirmed'}
            if unexpected:
                raise WorkspaceValidationError('retry-generation 仅接受 revision 与 execution_confirmed。')
            if 'revision' not in request.data:
                raise WorkspaceValidationError('retry-generation 必须携带 revision。')
            if request.data.get('execution_confirmed') is not True:
                raise WorkspaceValidationError('重试生成并试运行必须明确传递 execution_confirmed:true。')
            with transaction.atomic():
                workspace = owned_workspace(
                    project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True,
                )
                previous_revision = require_revision(workspace, request.data['revision'])
                if workspace_is_busy(workspace):
                    raise WorkspaceConflict('当前工作区任务尚未结束。')
                _model_failure, retry = generation_retry_metadata(workspace)
                if retry.get('available') is not True or retry.get('mode') not in {'generate', 'repair'}:
                    raise WorkspaceValidationError(str(retry.get('reason') or '当前失败不能安全重试。'))
                require_generation_model_id(workspace.model_id, owner=workspace.owner)
                previous_generation = deepcopy(workspace.generation)
                previous_snapshot = previous_generation.get('_snapshot')
                if not isinstance(previous_snapshot, dict):
                    raise WorkspaceValidationError('失败任务缺少服务器冻结参数，不能安全重试。')
                _validate_retry_snapshot(workspace, previous_snapshot)
                mode = retry['mode']
                if mode == 'repair':
                    prompt_draft, failure_evidence = _repair_seed(workspace, previous_revision)
                else:
                    prompt_draft = normalize_draft(
                        previous_snapshot.get('user_draft', previous_snapshot.get('draft')),
                    )
                    failure_evidence = None

                # A retry is a new asynchronous lease even though it does not
                # edit the draft. Advancing the revision makes the accepted
                # request idempotent against a second same-revision POST even
                # when the first task finishes before that request acquires the lock.
                workspace.revision += 1
                if isinstance(workspace.candidate, dict) and workspace.candidate.get('source_revision') == previous_revision:
                    workspace.candidate = {**deepcopy(workspace.candidate), 'source_revision': workspace.revision}
                if workspace.debug_revision == previous_revision:
                    workspace.debug_revision = workspace.revision
                workspace.save(update_fields=['revision', 'candidate', 'debug_revision', 'updated_at'])
                _queue_pipeline(
                    workspace, revision=workspace.revision, mode=mode,
                    target_url=previous_snapshot['target_url'], variables=previous_snapshot['variables'],
                    endpoints=previous_snapshot['endpoints'], prompt_draft=prompt_draft,
                    failure_evidence=failure_evidence,
                    retry_snapshot=previous_snapshot, retry_previous=previous_generation,
                    preserve_candidate=True,
                )
            return response(
                kind='success', data=_workspace_payload(workspace),
                message='当前失败范围的生成并试运行已重新排队', status_code=202,
            )
        except PermissionError as exc:
            return _problem(exc, 403)
        except LookupError as exc:
            return _problem(exc, 404)
        except WorkspaceConflict as exc:
            return _problem(exc, 409)
        except (WorkspaceValidationError, ValueError) as exc:
            return _problem(exc)


class APIWorkspaceCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, workspace_id):
        try:
            _project_or_denied(project_id, request.user)
            requested_revision = request.data.get('revision')
            if not isinstance(requested_revision, int) or isinstance(requested_revision, bool):
                raise WorkspaceValidationError('cancel 必须携带整数 revision。')
            cancelled_ids: list[int] = []
            cancelled_leases: dict[int, str] = {}
            now = timezone.now()
            with transaction.atomic():
                workspace = owned_workspace(
                    project_id=project_id, workspace_id=workspace_id, user=request.user, lock=True,
                )
                generation = deepcopy(workspace.generation) if isinstance(workspace.generation, dict) else {}
                already_cancelled = (
                    generation.get('status') == 'cancelled'
                    and generation.get('cancelled_source_revision') == requested_revision
                )
                if not already_cancelled:
                    require_revision(workspace, requested_revision)
                if workspace.parent_id is None:
                    family = list(APIWorkspace.objects.select_for_update().filter(
                        Q(pk=workspace.id) | Q(parent_id=workspace.id), owner=request.user,
                    ).order_by('parent_id', 'scenario_order', 'id'))
                else:
                    family = [workspace]
                if not already_cancelled:
                    for item in family:
                        if item.id != workspace.id and item.status not in {
                            APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING,
                        }:
                            continue
                        item_generation = deepcopy(item.generation) if isinstance(item.generation, dict) else {}
                        old_revision = item.revision
                        old_task_id = item.task_id
                        item_generation.update({
                            'status': 'cancelled', 'phase': 'finished',
                            'summary': '用户已取消工作区任务。', 'finished_at': now.isoformat(),
                            'cancelled_source_revision': old_revision,
                        })
                        item.generation = item_generation
                        candidate = deepcopy(item.candidate) if isinstance(item.candidate, dict) else None
                        if candidate and candidate.get('source_revision') == old_revision:
                            candidate['source_revision'] = old_revision + 1
                            item_generation['source_revision'] = old_revision + 1
                            item.candidate = candidate
                        if item.status == APIWorkspace.Status.DEBUGGING:
                            debug_result = deepcopy(item.debug_result) if isinstance(item.debug_result, dict) else {}
                            debug_result.update({
                                'status': 'cancelled', 'error_type': 'Cancelled',
                                'error': '用户已取消工作区任务。', 'finished_at': now.isoformat(),
                            })
                            item.debug_result = debug_result
                            item.debug_snapshot = {}
                        item.status = APIWorkspace.Status.READY
                        item.error = ''
                        item.task_id = ''
                        item.revision += 1
                        item.save()
                        cancelled_ids.append(item.id)
                        if old_task_id:
                            cancelled_leases[item.id] = old_task_id
                    stop_workspace_executions(cancelled_leases, message='用户已取消工作区任务。')
                else:
                    cancelled_ids = [item.id for item in family]
            workspace = owned_workspace(
                project_id=project_id, workspace_id=workspace_id, user=request.user,
            )
            return response(kind='success', data=_workspace_payload(workspace), message='工作区任务已取消')
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
            return response(kind='success', data=_workspace_payload(workspace), message='调试已排队', status_code=202)
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
                test_type=request.data['test_type'] if 'test_type' in request.data else _UNSET,
            )
            return response(kind='success', data=_workspace_payload(workspace), message='工作区已保存为 API 用例')
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
