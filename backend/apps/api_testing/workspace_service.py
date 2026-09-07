"""Small, explicit state transitions for API workspaces.

The editor draft is the only mutable case contract.  Candidates and queued debug
input are deliberately separate so an asynchronous result can never overwrite a
newer editor revision.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import APIEndpoint, APIWorkspace, APITestCase, default_api_workspace_draft
from projects.models import Project


class WorkspaceConflict(ValueError):
    pass


class WorkspaceValidationError(ValueError):
    pass


def can_edit_project(project: Project, user) -> bool:
    return (
        project.created_by_id == user.id
        or project.owner_id == user.id
        or project.members.filter(user=user, can_edit=True).exists()
    )


def can_execute_project(project: Project, user) -> bool:
    return (
        project.created_by_id == user.id
        or project.owner_id == user.id
        or project.members.filter(user=user, can_execute_tests=True).exists()
    )


def validate_model_id(model_id: Any, *, owner) -> int | None:
    """Validate a persisted model binding without allowing cross-user reuse."""
    if model_id is None:
        return None
    if not isinstance(model_id, int) or isinstance(model_id, bool) or model_id <= 0:
        raise WorkspaceValidationError('model_id 必须是正整数或 null。')
    from ai_core.models import LLMConfiguration, ModelType
    if not LLMConfiguration.objects.filter(
        pk=model_id, created_by=owner, model_type=ModelType.LLM, is_active=True,
    ).exists():
        raise WorkspaceValidationError('model_id 不存在、不属于当前工作区所有者、不是 LLM 或已被禁用。')
    return model_id


def require_generation_model_id(model_id: Any, *, owner) -> int:
    """Generation must use an explicit, currently available owner-scoped model."""
    validated_model_id = validate_model_id(model_id, owner=owner)
    if validated_model_id is None:
        raise WorkspaceValidationError('发起 AI 对话前必须选择当前工作区所有者的启用 LLM 模型。')
    return validated_model_id


def normalize_draft(value: Any) -> dict[str, Any]:
    """Use the exact requests runtime contract at every persistence boundary."""
    try:
        from .case_contract import CaseContractError, normalize_case
        return normalize_case(value)
    except (CaseContractError, TypeError, ValueError) as exc:
        raise WorkspaceValidationError(str(exc)) from exc


def require_executable_draft(value: Any) -> dict[str, Any]:
    """An empty editor draft is valid, but cannot be saved or sent to requests."""
    draft = normalize_draft(value)
    if not draft['teststeps']:
        raise WorkspaceValidationError('草稿尚无测试步骤；可继续编辑，但不能生成候选、保存或调试。')
    return draft


def endpoint_specs(project_id: int, endpoint_ids: list[int]) -> list[dict[str, Any]]:
    if len(endpoint_ids) > 50:
        raise WorkspaceValidationError('一次最多选择 50 个相关接口，请缩小本次生成范围。')
    ids = [value for value in endpoint_ids if isinstance(value, int) and not isinstance(value, bool) and value > 0]
    if len(ids) != len(set(ids)) or len(ids) != len(endpoint_ids):
        raise WorkspaceValidationError('endpoint_ids 必须是互不重复的正整数。')
    endpoints = list(APIEndpoint.objects.filter(id__in=ids, spec__project_id=project_id).select_related('spec'))
    if len(endpoints) != len(ids):
        raise WorkspaceValidationError('存在不属于当前项目的接口端点。')
    by_id = {endpoint.id: endpoint for endpoint in endpoints}
    return [{
        'id': endpoint.id, 'method': endpoint.method, 'path': endpoint.path,
        'summary': endpoint.summary, 'description': endpoint.description,
        'parameters': endpoint.parameters, 'request_body': endpoint.request_body,
        'responses': endpoint.responses, 'operation_id': endpoint.operation_id,
        'document_context': _document_context(endpoint),
    } for endpoint in (by_id[item] for item in ids)]


def _document_context(endpoint):
    """Only copy addressing/auth metadata needed for this endpoint, not the entire spec."""
    metadata = endpoint.spec.metadata if isinstance(endpoint.spec.metadata, dict) else {}
    paths = metadata.get('paths', {})
    paths = paths if isinstance(paths, dict) else {}
    path_item = paths.get(endpoint.path, {})
    path_item = path_item if isinstance(path_item, dict) else {}
    operation = path_item.get(endpoint.method.lower(), {})
    operation = operation if isinstance(operation, dict) else {}
    components = metadata.get('components', {})
    components = components if isinstance(components, dict) else {}
    return {
        'spec_id': endpoint.spec_id, 'spec_name': endpoint.spec.spec_name,
        'servers': deepcopy(operation.get('servers', path_item.get('servers', metadata.get('servers', [])))),
        'host': metadata.get('host', ''), 'basePath': metadata.get('basePath', ''),
        'schemes': metadata.get('schemes', []),
        'security': deepcopy(operation.get('security', metadata.get('security', []))),
        'security_schemes': deepcopy(components.get('securitySchemes', metadata.get('securityDefinitions', {}))),
    }


def serialize_workspace(workspace: APIWorkspace) -> dict[str, Any]:
    saved_case = workspace.saved_case if workspace.saved_case_id else None
    return {
        'id': workspace.id,
        'title': workspace.title,
        'model_id': workspace.model_id,
        'endpoint_ids': workspace.endpoint_ids if isinstance(workspace.endpoint_ids, list) else [],
        'draft': workspace.draft if isinstance(workspace.draft, dict) else default_api_workspace_draft(),
        'revision': workspace.revision,
        'status': workspace.status,
        'error': workspace.error,
        'messages': workspace.messages if isinstance(workspace.messages, list) else [],
        'candidate': workspace.candidate,
        'debug_result': workspace.debug_result if isinstance(workspace.debug_result, dict) else {},
        'debug_revision': workspace.debug_revision,
        'saved_case_id': workspace.saved_case_id,
        'saved_case_description': str(saved_case.description or '') if saved_case else '',
        'task_id': workspace.task_id or None,
        'updated_at': workspace.updated_at.isoformat() if workspace.updated_at else None,
    }


def owned_workspace(*, project_id: int, workspace_id: int, user, lock: bool = False) -> APIWorkspace:
    queryset = APIWorkspace.objects.filter(
        project_id=project_id, id=workspace_id, owner=user,
    )
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related('saved_case')
    try:
        return queryset.get()
    except APIWorkspace.DoesNotExist as exc:
        raise LookupError('工作区不存在，或不属于当前用户。') from exc


def require_revision(workspace: APIWorkspace, revision: Any) -> int:
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise WorkspaceValidationError('revision 必须是整数。')
    if workspace.revision != revision:
        raise WorkspaceConflict('工作区已被修改，请刷新后重试。')
    return revision


_UNSET = object()


def update_workspace_draft(workspace: APIWorkspace, *, revision: int, draft: Any = _UNSET,
                           model_id: Any = _UNSET, endpoint_ids: Any = _UNSET) -> APIWorkspace:
    require_revision(workspace, revision)
    if workspace.status in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
        raise WorkspaceConflict('当前工作区任务尚未结束，不能修改草稿。')
    changed = False
    if draft is not _UNSET:
        workspace.draft = normalize_draft(draft)
        workspace.candidate = None
        workspace.debug_result = {}
        workspace.debug_snapshot = {}
        workspace.debug_revision = None
        changed = True
    if model_id is not _UNSET:
        workspace.model_id = validate_model_id(model_id, owner=workspace.owner)
        changed = True
    if endpoint_ids is not _UNSET:
        if not isinstance(endpoint_ids, list):
            raise WorkspaceValidationError('endpoint_ids 必须是数组。')
        endpoint_specs(workspace.project_id, endpoint_ids)
        workspace.endpoint_ids = endpoint_ids
        changed = True
    if changed:
        workspace.revision += 1
        workspace.status = APIWorkspace.Status.IDLE
        workspace.error = ''
        workspace.task_id = ''
        workspace.save()
    return workspace


def expire_stalled_workspace(workspace: APIWorkspace, *, now=None) -> APIWorkspace:
    """Mark an orphaned queued task failed during polling; never retry it here."""
    if workspace.status not in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
        return workspace
    now = now or timezone.now()
    limit_seconds = debug_timeout_seconds() if workspace.status == APIWorkspace.Status.DEBUGGING else generation_timeout_seconds()
    if (now - workspace.updated_at).total_seconds() <= limit_seconds:
        return workspace
    with transaction.atomic():
        current = APIWorkspace.objects.select_for_update().get(pk=workspace.pk)
        if current.status != workspace.status or current.task_id != workspace.task_id:
            return current
        if (now - current.updated_at).total_seconds() <= limit_seconds:
            return current
        was_debugging = current.status == APIWorkspace.Status.DEBUGGING
        current.status = APIWorkspace.Status.FAILED
        current.error = '异步任务未在限定时间内完成，已标记失败；未自动重试。'
        if was_debugging:
            current.debug_snapshot = {}
            current.debug_result = {'status': 'error', 'error': current.error}
        current.save()
        return current


def generation_timeout_seconds() -> int:
    """Follow the configured model-manager request timeout, including long requests."""
    from ai_core import model_manager
    reader = getattr(model_manager, 'get_llm_request_timeout', None)
    if not callable(reader):
        reader = model_manager._get_default_llm_timeout
    return max(1, int(reader())) * 2 + 60


def debug_timeout_seconds() -> int:
    """requests_runtime enforces this total timeout in its subprocess parent."""
    from .requests_runtime import MAX_TOTAL_TIMEOUT_SECONDS
    return int(MAX_TOTAL_TIMEOUT_SECONDS) + 60


def append_message(workspace: APIWorkspace, *, role: str, content: str, mode: str | None = None) -> None:
    messages = list(workspace.messages) if isinstance(workspace.messages, list) else []
    item = {'role': role, 'content': content}
    if mode:
        item['mode'] = mode
    messages.append(item)
    workspace.messages = messages[-40:]


def classify_case_draft(draft: dict[str, Any], *, project_id: int) -> tuple[str, APIEndpoint | None]:
    """Keep single-endpoint cases visible in their endpoint list without dropping references."""
    steps = draft['teststeps']
    referenced_ids: list[int] = []
    for index, step in enumerate(steps):
        endpoint_id = step.get('endpoint_id')
        if endpoint_id is None:
            continue
        if not isinstance(endpoint_id, int) or isinstance(endpoint_id, bool) or endpoint_id <= 0:
            raise WorkspaceValidationError(f'teststeps[{index}].endpoint_id 必须是当前项目的正整数。')
        referenced_ids.append(endpoint_id)
    endpoints = {
        endpoint.id: endpoint
        for endpoint in APIEndpoint.objects.filter(id__in=set(referenced_ids), spec__project_id=project_id)
    }
    if len(endpoints) != len(set(referenced_ids)):
        raise WorkspaceValidationError('草稿引用了不属于当前项目的接口端点，不能保存。')
    if len(steps) == 1 and referenced_ids:
        return 'endpoint', endpoints[referenced_ids[0]]
    return 'scenario', None


def create_or_update_case(workspace: APIWorkspace, *, revision: int, title: str | None,
                          description: Any = _UNSET) -> APIWorkspace:
    with transaction.atomic():
        workspace = APIWorkspace.objects.select_for_update().get(pk=workspace.pk)
        require_revision(workspace, revision)
        if workspace.status in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
            raise WorkspaceConflict('当前工作区任务尚未结束，不能保存。')
        draft = require_executable_draft(workspace.draft)
        test_case_type, endpoint = classify_case_draft(draft, project_id=workspace.project_id)
        case = None
        if workspace.saved_case_id:
            case = APITestCase.objects.select_for_update().get(
                pk=workspace.saved_case_id, project_id=workspace.project_id,
            )
            if workspace.saved_case_updated_at and case.updated_at != workspace.saved_case_updated_at:
                raise WorkspaceConflict('关联用例已被其他人修改，请先处理冲突。')
        case_title = title if isinstance(title, str) and title.strip() else (workspace.title or draft['config']['name'] or '未命名 API 用例')
        if description is _UNSET or description is None:
            case_description = case.description if case else ''
        elif isinstance(description, str):
            case_description = description
        else:
            raise WorkspaceValidationError('description 必须是字符串、null，或省略。')
        if case is None:
            case = APITestCase.objects.create(
                project_id=workspace.project_id, created_by=workspace.owner,
                title=case_title[:200], description=case_description,
                test_case_type=test_case_type, endpoint=endpoint,
                script_content=__import__('json').dumps(draft, ensure_ascii=False),
            )
            workspace.saved_case = case
        else:
            case.title = case_title[:200]
            case.description = case_description
            case.script_content = __import__('json').dumps(draft, ensure_ascii=False)
            case.test_case_type = test_case_type
            case.endpoint = endpoint
            case.save(update_fields=['title', 'description', 'script_content', 'test_case_type', 'endpoint', 'updated_at'])
            workspace.saved_case = case
        workspace.title = case.title
        workspace.saved_case_updated_at = case.updated_at
        workspace.save(update_fields=['saved_case', 'title', 'saved_case_updated_at', 'updated_at'])
    return workspace
