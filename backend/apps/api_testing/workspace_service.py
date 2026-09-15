"""Small, explicit state transitions for API workspaces.

The editor draft is the only mutable case contract.  Candidates and queued debug
input are deliberately separate so an asynchronous result can never overwrite a
newer editor revision.
"""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import timedelta
from typing import Any

from django.db import models, transaction
from django.http import Http404
from rest_framework.exceptions import PermissionDenied
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import APIEndpoint, APIWorkspace, APITestCase, APISpecification, default_api_workspace_draft
from projects.access import EDIT, EXECUTE, get_project_for_user
from projects.models import Project


class WorkspaceConflict(ValueError):
    pass


class WorkspaceValidationError(ValueError):
    pass


def scenario_authenticated_endpoint_ids(scenario: dict[str, Any], *, target_endpoint_ids: list[int] | set[int]) -> list[int]:
    """Read a frozen authentication subset, conservatively handling older plans."""
    legacy_required = scenario.get('requires_authenticated_context', True)
    if not isinstance(legacy_required, bool):
        raise WorkspaceValidationError('requires_authenticated_context 必须是布尔值。')
    if 'authenticated_endpoint_ids' not in scenario:
        # Only an explicit legacy false declares an unauthenticated scenario.
        return list(dict.fromkeys(target_endpoint_ids)) if legacy_required else []
    ids = scenario['authenticated_endpoint_ids']
    if not isinstance(ids, list) or any(not isinstance(item, int) or isinstance(item, bool) for item in ids):
        raise WorkspaceValidationError('authenticated_endpoint_ids 必须是整数数组。')
    if not set(ids).issubset(target_endpoint_ids):
        raise WorkspaceValidationError('authenticated_endpoint_ids 必须是本场景业务目标端点的子集。')
    if 'requires_authenticated_context' in scenario and legacy_required != bool(ids):
        raise WorkspaceValidationError('requires_authenticated_context 必须与 authenticated_endpoint_ids 是否非空一致。')
    return list(dict.fromkeys(ids))


def can_edit_project(project: Project, user) -> bool:
    try:
        get_project_for_user(project.pk, user, EDIT)
    except (Http404, PermissionDenied):
        return False
    return True


def can_execute_project(project: Project, user) -> bool:
    try:
        get_project_for_user(project.pk, user, EXECUTE)
    except (Http404, PermissionDenied):
        return False
    return True


def validate_model_id(model_id: Any, *, owner) -> int | None:
    """Validate a persisted binding against platform-usable model configs."""
    if model_id is None:
        return None
    if not isinstance(model_id, int) or isinstance(model_id, bool) or model_id <= 0:
        raise WorkspaceValidationError('model_id 必须是正整数或 null。')
    from ai_core.config_access import usable_llm_configurations
    if not usable_llm_configurations().filter(pk=model_id).exists():
        raise WorkspaceValidationError('model_id 不存在、不是 LLM 或已被禁用。')
    return model_id


def require_generation_model_id(model_id: Any, *, owner) -> int:
    """Generation must use an explicit, currently platform-available model."""
    validated_model_id = validate_model_id(model_id, owner=owner)
    if validated_model_id is None:
        raise WorkspaceValidationError('发起 AI 对话前必须选择启用的 LLM 模型。')
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


def _require_browser_capture_source_owner(spec: APISpecification, *, owner) -> None:
    """Validate published capture provenance without exposing the raw task.

    ``owner`` remains for call compatibility. Published specifications and
    endpoints are project assets; BrowserDiscoveryTask records and workspaces
    remain owner-private at their own HTTP boundaries.
    """
    if spec.spec_type != APISpecification.SpecType.BROWSER_CAPTURE:
        return
    if not spec.source_task_id or spec.source_task.project_id != spec.project_id:
        raise WorkspaceValidationError('浏览器探索来源不存在或与当前项目不一致。')


def validate_spec_id(project_id: int, spec_id: Any, *, owner=None) -> APISpecification | None:
    if spec_id is None:
        return None
    if not isinstance(spec_id, int) or isinstance(spec_id, bool) or spec_id <= 0:
        raise WorkspaceValidationError('spec_id 必须是正整数或 null。')
    try:
        spec = APISpecification.objects.select_related('source_task').get(pk=spec_id, project_id=project_id)
    except APISpecification.DoesNotExist as exc:
        raise WorkspaceValidationError('API 规范不存在或不属于当前项目。') from exc
    if spec.status != APISpecification.TaskStatus.COMPLETED:
        raise WorkspaceValidationError('API 规范尚未可用，不能用于生成。')
    _require_browser_capture_source_owner(spec, owner=owner)
    return spec


def infer_spec_id(project_id: int, endpoint_ids: list[int]) -> int | None:
    if not endpoint_ids:
        return None
    spec_ids = list(APIEndpoint.objects.filter(
        id__in=endpoint_ids, spec__project_id=project_id,
    ).order_by().values_list('spec_id', flat=True).distinct())
    return spec_ids[0] if len(spec_ids) == 1 else None


def endpoint_specs(project_id: int, endpoint_ids: list[int], *, spec_id: int | None = None, owner=None) -> list[dict[str, Any]]:
    if len(endpoint_ids) > 50:
        raise WorkspaceValidationError('一次最多选择 50 个相关接口，请缩小本次生成范围。')
    ids = [value for value in endpoint_ids if isinstance(value, int) and not isinstance(value, bool) and value > 0]
    if len(ids) != len(set(ids)) or len(ids) != len(endpoint_ids):
        raise WorkspaceValidationError('endpoint_ids 必须是互不重复的正整数。')
    query = APIEndpoint.objects.filter(id__in=ids, spec__project_id=project_id)
    if spec_id is not None:
        query = query.filter(spec_id=spec_id)
    endpoints = list(query.select_related('spec', 'spec__source_task'))
    if len(endpoints) != len(ids):
        raise WorkspaceValidationError('存在不属于当前项目的接口端点。')
    for endpoint in endpoints:
        _require_browser_capture_source_owner(endpoint.spec, owner=owner)
    by_id = {endpoint.id: endpoint for endpoint in endpoints}
    return [{
        'id': endpoint.id, 'method': endpoint.method, 'path': endpoint.path,
        'summary': endpoint.summary, 'description': endpoint.description,
        'parameters': _resolve_endpoint_refs(endpoint.parameters, endpoint.spec.metadata),
        'request_body': _resolve_endpoint_refs(endpoint.request_body, endpoint.spec.metadata),
        'responses': _resolve_endpoint_refs(endpoint.responses, endpoint.spec.metadata), 'operation_id': endpoint.operation_id,
        'document_context': _document_context(endpoint),
    } for endpoint in (by_id[item] for item in ids)]


def validate_target_endpoint_id(project_id: int, target_endpoint_id: Any, *,
                                spec_id: int | None = None,
                                endpoint_ids: list[int] | None = None,
                                owner=None) -> APIEndpoint | None:
    """Resolve an explicit endpoint target without creating a deletion FK.

    API specifications own their endpoints through CASCADE.  The workspace keeps
    only the numeric id so a later specification deletion leaves an auditable,
    invalid target instead of changing workspace mode.
    """
    if target_endpoint_id is None:
        return None
    if (not isinstance(target_endpoint_id, int) or isinstance(target_endpoint_id, bool)
            or target_endpoint_id <= 0):
        raise WorkspaceValidationError('target_endpoint_id 必须是正整数或 null。')
    try:
        endpoint = APIEndpoint.objects.select_related('spec', 'spec__source_task').get(
            pk=target_endpoint_id, spec__project_id=project_id,
        )
    except APIEndpoint.DoesNotExist as exc:
        raise WorkspaceValidationError('目标端点不存在或不属于当前项目。') from exc
    if spec_id is not None and endpoint.spec_id != spec_id:
        raise WorkspaceValidationError('目标端点与工作区 API 规范不一致。')
    _require_browser_capture_source_owner(endpoint.spec, owner=owner)
    if endpoint_ids is not None and target_endpoint_id not in endpoint_ids:
        raise WorkspaceValidationError('endpoint_ids 不能移除显式目标端点。')
    return endpoint


def validate_target_draft(draft: dict[str, Any], *, project_id: int, spec_id: int,
                          endpoint_ids: list[int], target_endpoint_id: int,
                          owner=None, require_target: bool = True) -> list[int]:
    """Keep every explicit-target draft reference inside its frozen scope."""
    referenced_ids: list[int] = []
    for index, step in enumerate(draft.get('teststeps', [])):
        endpoint_id = step.get('endpoint_id') if isinstance(step, dict) else None
        if endpoint_id is None:
            continue
        if not isinstance(endpoint_id, int) or isinstance(endpoint_id, bool) or endpoint_id <= 0:
            raise WorkspaceValidationError(f'teststeps[{index}].endpoint_id 必须是当前项目的正整数。')
        if endpoint_id not in referenced_ids:
            referenced_ids.append(endpoint_id)
    if referenced_ids:
        endpoint_specs(project_id, referenced_ids, spec_id=spec_id, owner=owner)
    outside_scope = [endpoint_id for endpoint_id in referenced_ids if endpoint_id not in endpoint_ids]
    if outside_scope:
        raise WorkspaceValidationError(f'草稿引用了工作区可用范围之外的端点：{outside_scope}。')
    if require_target and target_endpoint_id not in referenced_ids:
        raise WorkspaceValidationError('显式端点用例草稿必须实际引用目标端点。')
    return referenced_ids


_MAX_LOCAL_REF_DEPTH = 24


def _resolve_endpoint_refs(value: Any, document: Any, resolving: frozenset[str] = frozenset(), depth: int = 0) -> Any:
    """Boundedly expand local refs while preserving unexpandable schema evidence."""
    if isinstance(value, list):
        return [_resolve_endpoint_refs(item, document, resolving, depth) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    reference = value.get('$ref')
    if reference is not None:
        # APIParserService persists locally-expanded schemas together with the
        # source pointer.  Prefer that checked expansion when the original
        # document is not retained in ``metadata``.
        if value.get('x-platform-ref-status') == 'resolved':
            return {
                key: _resolve_endpoint_refs(item, document, resolving, depth)
                for key, item in value.items() if key not in {'$ref', 'x-platform-ref-status'}
            }
        copied = {key: _resolve_endpoint_refs(item, document, resolving, depth) for key, item in value.items()}
        if not isinstance(reference, str) or not reference.startswith('#/'):
            copied['x-platform-ref-status'] = 'external'
            return copied
        if reference in resolving:
            copied['x-platform-ref-status'] = 'circular'
            return copied
        if depth >= _MAX_LOCAL_REF_DEPTH:
            copied['x-platform-ref-status'] = 'truncated'
            return copied
        current: Any = document
        try:
            for part in reference[2:].split('/'):
                current = current[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, IndexError, TypeError, ValueError):
            copied['x-platform-ref-status'] = 'unresolved'
            return copied
        resolved = _resolve_endpoint_refs(current, document, resolving | {reference}, depth + 1)
        if not isinstance(resolved, dict):
            copied['x-platform-ref-status'] = 'unresolved'
            return copied
        resolved.update(copied)
        resolved['x-platform-ref-status'] = 'resolved'
        return resolved
    return {key: _resolve_endpoint_refs(item, document, resolving, depth) for key, item in value.items()}


def generation_endpoint_specs(workspace: APIWorkspace) -> list[dict[str, Any]]:
    """Return the frozen root scope when a child needs local prerequisites.

    A child keeps ``endpoint_ids`` as its business targets for coverage.  Its
    generated draft may additionally need an authentication or data-setup
    endpoint selected by the root, so using the child target list here would
    make a self-contained scenario impossible.  The root scope remains the
    only source of such dependencies; this never widens to an entire spec.
    """
    if workspace.parent_id:
        root = APIWorkspace.objects.filter(pk=workspace.parent_id).first()
        if not root or root.project_id != workspace.project_id or root.owner_id != workspace.owner_id:
            raise WorkspaceValidationError('子场景的根工作区归属不一致，不能读取依赖端点。')
        if root.spec_id != workspace.spec_id:
            raise WorkspaceValidationError('根工作区 API 规范已变化，子场景的依赖范围已失效。')
        if workspace.target_endpoint_id is not None:
            if root.target_endpoint_id != workspace.target_endpoint_id:
                raise WorkspaceValidationError('子场景与根工作区的显式目标端点不一致。')
            validate_target_endpoint_id(
                workspace.project_id, workspace.target_endpoint_id, spec_id=workspace.spec_id,
                endpoint_ids=workspace.endpoint_ids, owner=workspace.owner,
            )
        child_generation = workspace.generation if isinstance(workspace.generation, dict) else {}
        child_snapshot = child_generation.get('_snapshot') if isinstance(child_generation.get('_snapshot'), dict) else {}
        frozen_ids = child_snapshot.get('scope_endpoint_ids')
        if not isinstance(frozen_ids, list) or not frozen_ids:
            raise WorkspaceValidationError('子场景没有冻结根依赖范围，不能生成或修复。')
        root_generation = root.generation if isinstance(root.generation, dict) else {}
        if root_generation.get('status') == 'stale':
            raise WorkspaceValidationError('根工作区选择范围已变化，子场景必须从新的根范围重新生成。')
        root_current_ids = root.endpoint_ids if isinstance(root.endpoint_ids, list) else []
        if not root_current_ids:
            root_current_ids = list(APIEndpoint.objects.filter(spec_id=root.spec_id).values_list('id', flat=True))
        if not set(frozen_ids).issubset(set(root_current_ids)):
            raise WorkspaceValidationError('根工作区已缩小或变更选择范围，子场景的依赖范围已失效。')
        return endpoint_specs(workspace.project_id, frozen_ids, spec_id=workspace.spec_id, owner=workspace.owner)
    if not workspace.spec_id:
        raise WorkspaceValidationError('请先选择可用 API 规范后再生成。')
    validate_spec_id(workspace.project_id, workspace.spec_id, owner=workspace.owner)
    endpoint_ids = workspace.endpoint_ids if isinstance(workspace.endpoint_ids, list) else []
    if workspace.target_endpoint_id is not None:
        validate_target_endpoint_id(
            workspace.project_id, workspace.target_endpoint_id, spec_id=workspace.spec_id,
            endpoint_ids=endpoint_ids, owner=workspace.owner,
        )
    if not endpoint_ids:
        endpoint_ids = list(APIEndpoint.objects.filter(spec_id=workspace.spec_id).values_list('id', flat=True)[:51])
        if len(endpoint_ids) > 50:
            raise WorkspaceValidationError('该 API 规范超过 50 个端点，请明确缩小本次生成范围。')
    if not endpoint_ids:
        raise WorkspaceValidationError('所选 API 规范没有可用端点，不能生成。')
    return endpoint_specs(workspace.project_id, endpoint_ids, spec_id=workspace.spec_id, owner=workspace.owner)


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
    context = {
        'spec_id': endpoint.spec_id, 'spec_name': endpoint.spec.spec_name,
        'servers': deepcopy(operation.get('servers', path_item.get('servers', metadata.get('servers', [])))),
        'host': metadata.get('host', ''), 'basePath': metadata.get('basePath', ''),
        'schemes': metadata.get('schemes', []),
        'security': deepcopy(operation.get('security', metadata.get('security', []))),
        'security_schemes': deepcopy(components.get('securitySchemes', metadata.get('securityDefinitions', {}))),
    }
    # Browser capture remains a source context, not an invented OpenAPI schema.
    # Import lazily because the source helper also reuses workspace permissions.
    from .browser_discovery import browser_capture_context
    source_context = browser_capture_context(endpoint)
    if source_context is not None:
        context['browser_capture'] = source_context
    return context


def workspace_source(workspace: APIWorkspace) -> tuple[str, str, str | None]:
    """Return the immutable workspace origin from its root specification.

    Generation metadata is deliberately transient: retries and terminal-state
    cleanup replace it.  A child scenario also inherits the root selection, so
    its own (possibly legacy) ``spec`` must never alter the displayed origin.
    """
    root = workspace.parent if workspace.parent_id else workspace
    spec = root.spec if root.spec_id else None
    if not spec or spec.spec_type != APISpecification.SpecType.BROWSER_CAPTURE:
        return 'document', str(spec.spec_name or '') if spec else '', None
    return (
        'browser_capture',
        str(spec.spec_name or ''),
        str(spec.source_task_id) if spec.source_task_id else None,
    )


def serialize_workspace(workspace: APIWorkspace, *, include_scenarios: bool = True,
                        source: tuple[str, str, str | None] | None = None) -> dict[str, Any]:
    saved_case = workspace.saved_case if workspace.saved_case_id else None
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    scenario_context = generation.get('scenario_context') if isinstance(generation.get('scenario_context'), dict) else {}
    suggested_test_type = scenario_context.get('test_type')
    valid_test_types = {value for value, _label in APITestCase.TEST_TYPE_CHOICES}
    if suggested_test_type not in valid_test_types:
        suggested_test_type = (
            saved_case.test_type
            if saved_case and saved_case.test_case_type == 'endpoint' and saved_case.test_type in valid_test_types
            else None
        )
    resolved_source = source or workspace_source(workspace)
    source_type, source_name, source_task_id = resolved_source
    children: list[APIWorkspace] | None = None
    if workspace.parent_id is None and include_scenarios:
        prefetched = getattr(workspace, '_prefetched_objects_cache', {})
        if 'scenarios' in prefetched:
            children = list(workspace.scenarios.all())
        else:
            children = list(workspace.scenarios.all().select_related('saved_case').order_by('scenario_order', 'id'))
    model_failure, retry = generation_retry_metadata(workspace, known_children=children)
    data = {
        'id': workspace.id,
        'title': workspace.title,
        'parent_id': workspace.parent_id,
        'scenario_order': workspace.scenario_order,
        'scenario_description': workspace.scenario_description,
        'model_id': workspace.model_id,
        'target_endpoint_id': workspace.target_endpoint_id,
        'spec_id': workspace.spec_id,
        'source_type': source_type,
        'source_name': source_name,
        'source_task_id': source_task_id,
        'endpoint_ids': workspace.endpoint_ids if isinstance(workspace.endpoint_ids, list) else [],
        'draft': workspace.draft if isinstance(workspace.draft, dict) else default_api_workspace_draft(),
        'revision': workspace.revision,
        'status': workspace.status,
        'error': workspace.error,
        'messages': workspace.messages if isinstance(workspace.messages, list) else [],
        'candidate': workspace.candidate,
        'generation': public_generation(workspace.generation),
        'model_failure': model_failure,
        'retry': retry,
        'execution_history': deepcopy(getattr(workspace, '_execution_history', [])),
        'debug_result': workspace.debug_result if isinstance(workspace.debug_result, dict) else {},
        'debug_revision': workspace.debug_revision,
        'saved_case_id': workspace.saved_case_id,
        'saved_case_title': str(saved_case.title or '') if saved_case else '',
        'saved_case_description': str(saved_case.description or '') if saved_case else '',
        'suggested_test_type': suggested_test_type,
        'task_id': workspace.task_id or None,
        'updated_at': workspace.updated_at.isoformat() if workspace.updated_at else None,
    }
    if workspace.parent_id is None and include_scenarios:
        children = children or []
        data['scenarios'] = [
            serialize_workspace(child, include_scenarios=False, source=resolved_source)
            for child in children
        ]
        data['coverage'] = workspace_coverage(workspace, children)
    return data


def workspace_coverage(root: APIWorkspace, children: list[APIWorkspace] | None = None) -> dict[str, Any]:
    children = children if children is not None else list(root.scenarios.all())
    generation = root.generation if isinstance(root.generation, dict) else {}
    snapshot = generation.get('_snapshot') if isinstance(generation.get('_snapshot'), dict) else {}
    frozen_scope = snapshot.get('scope_endpoint_ids')
    if root.target_endpoint_id is not None:
        # Explicit endpoint mode separates its single business coverage target
        # from the wider frozen scope used for login/setup/query/cleanup steps.
        total = {root.target_endpoint_id}
    else:
        total = {
            endpoint_id for endpoint_id in (
                frozen_scope if isinstance(frozen_scope, list) else root.endpoint_ids or []
            ) if isinstance(endpoint_id, int)
        }
    # A real root scope/model/spec edit marks prior evidence stale.  It remains
    # visible in child history but must not be reported as coverage for the new
    # scope.  Title-only updates deliberately do not set this state.
    if generation.get('status') == 'stale':
        if root.target_endpoint_id is None:
            current_ids = root.endpoint_ids if isinstance(root.endpoint_ids, list) else []
            if current_ids:
                total = {item for item in current_ids if isinstance(item, int)}
            elif root.spec_id:
                total = set(APIEndpoint.objects.filter(spec_id=root.spec_id).values_list('id', flat=True))
        return {
            'total': len(total), 'planned': 0, 'generated': 0, 'verified': 0,
            'uncovered_endpoint_ids': sorted(total), 'planned_endpoint_ids': [],
            'generated_endpoint_ids': [], 'verified_endpoint_ids': [],
        }
    scoped_ids = generation.get('scenario_ids') if isinstance(generation.get('scenario_ids'), list) else [item.id for item in children]
    scoped = [item for item in children if item.id in scoped_ids]
    planned = {endpoint_id for item in scoped for endpoint_id in (item.endpoint_ids or []) if isinstance(endpoint_id, int)}
    generated: set[int] = set()
    verified: set[int] = set()

    def actual_endpoint_ids(draft: Any) -> set[int]:
        if not isinstance(draft, dict):
            return set()
        return {
            step.get('endpoint_id') for step in draft.get('teststeps', [])
            if isinstance(step, dict) and isinstance(step.get('endpoint_id'), int)
        }

    def successful_round(item: APIWorkspace, candidate: dict[str, Any]) -> bool:
        candidate_hash = candidate.get('draft_hash')
        rounds = item.generation.get('rounds') if isinstance(item.generation, dict) else []
        rounds = rounds if isinstance(rounds, list) else []
        return any(
            isinstance(round_, dict) and round_.get('draft_hash') == candidate_hash
            and isinstance(round_.get('result'), dict) and round_['result'].get('success') is True
            for round_ in rounds
        )

    for item in scoped:
        candidate = item.candidate if isinstance(item.candidate, dict) else {}
        candidate_draft = candidate.get('draft') if isinstance(candidate.get('draft'), dict) else None
        if candidate_draft is not None:
            actual = actual_endpoint_ids(candidate_draft)
            generated.update(actual)
            if (candidate.get('verification_status') == 'passed' and candidate.get('source_revision') == item.revision
                    and successful_round(item, candidate)):
                verified.update(actual)
        if item.debug_revision == item.revision and isinstance(item.debug_result, dict) and item.debug_result.get('success'):
            actual = actual_endpoint_ids(item.draft)
            generated.update(actual)
            verified.update(actual)
    planned.intersection_update(total)
    generated.intersection_update(total)
    verified.intersection_update(total)
    return {
        'total': len(total), 'planned': len(planned), 'generated': len(generated), 'verified': len(verified),
        'uncovered_endpoint_ids': sorted(total - planned), 'planned_endpoint_ids': sorted(planned),
        'generated_endpoint_ids': sorted(generated), 'verified_endpoint_ids': sorted(verified),
    }


def public_generation(value: Any) -> dict[str, Any]:
    generation = deepcopy(value) if isinstance(value, dict) else {}
    for internal_key in list(generation):
        if internal_key.startswith('_') or internal_key == 'task_id':
            generation.pop(internal_key, None)
    return generation


def _retry_stage(workspace: APIWorkspace, generation: dict[str, Any], snapshot: dict[str, Any]) -> str:
    stored = generation.get('model_failure') if isinstance(generation.get('model_failure'), dict) else {}
    stage = stored.get('stage')
    if stage in {'planning', 'generating', 'repairing'}:
        return stage
    if snapshot.get('workflow') == 'scenarios' and workspace.parent_id is None:
        return 'planning'
    if snapshot.get('mode') == 'repair' or generation.get('attempt', 0) > 1:
        return 'repairing'
    return 'generating'


def _public_model_failure(workspace: APIWorkspace, generation: dict[str, Any],
                          snapshot: dict[str, Any]) -> dict[str, Any] | None:
    if workspace.status != APIWorkspace.Status.FAILED or generation.get('status') != 'failed':
        return None
    stage = _retry_stage(workspace, generation, snapshot)
    from ai_core.provider_errors import classify_provider_error, public_model_failure
    stored = generation.get('model_failure') if isinstance(generation.get('model_failure'), dict) else None
    public = public_model_failure(stored, stage=stage)
    if public:
        return public

    # A small number of pre-contract failures are already persisted.  Only an
    # explicit stream wrapper is eligible for this read-only compatibility
    # classification; arbitrary workspace/HTTP/validation errors stay outside
    # the provider namespace.
    summary = generation.get('summary') or workspace.error
    if not isinstance(summary, str) or '流式LLM调用失败' not in summary:
        return None
    try:
        classified = classify_provider_error(RuntimeError(summary), model_context=False)
    except (ImportError, TypeError, ValueError):
        classified = None
    return public_model_failure(classified, stage=stage)


def _generation_rounds(generation: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = generation.get('rounds')
    if isinstance(rounds, list) and rounds:
        return [item for item in rounds if isinstance(item, dict)]
    previous = generation.get('_retry_previous') if isinstance(generation.get('_retry_previous'), dict) else {}
    rounds = previous.get('rounds')
    return [item for item in rounds if isinstance(item, dict)] if isinstance(rounds, list) else []


def result_has_http_evidence(value: Any) -> bool:
    """Recognize persisted requests-runner responses, not generic failures."""
    if not isinstance(value, dict):
        return False
    step_datas = value.get('step_datas')
    if not isinstance(step_datas, list):
        return False
    for step in step_datas:
        data = step.get('data') if isinstance(step, dict) else None
        req_resps = data.get('req_resps') if isinstance(data, dict) else None
        if not isinstance(req_resps, list):
            continue
        for exchange in req_resps:
            response = exchange.get('response') if isinstance(exchange, dict) else None
            status_code = response.get('status_code') if isinstance(response, dict) else None
            if isinstance(status_code, int) and not isinstance(status_code, bool):
                return True
    return False


def generation_repair_seed(workspace: APIWorkspace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a current, server-held candidate/debug failure safe to replay."""
    candidate = workspace.candidate if isinstance(workspace.candidate, dict) else {}
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    if candidate.get('source_revision') == workspace.revision and isinstance(candidate.get('draft'), dict):
        candidate_hash = candidate.get('draft_hash')
        for item in reversed(_generation_rounds(generation)):
            if item.get('draft_hash') != candidate_hash:
                continue
            result = item.get('result')
            if isinstance(result, dict) and result.get('success') is not True:
                return candidate['draft'], result
    if workspace.debug_revision == workspace.revision and isinstance(workspace.debug_result, dict):
        if workspace.debug_result and workspace.debug_result.get('success') is not True:
            return workspace.draft, workspace.debug_result
    raise WorkspaceValidationError('现有失败证据不足以安全修复；请使用常规生成入口重新确认参数。')


def generation_retry_metadata(workspace: APIWorkspace, *, known_children: list[APIWorkspace] | None = None,
                              ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Build the public, side-effect-free retry contract for one workspace."""
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    snapshot = generation.get('_snapshot') if isinstance(generation.get('_snapshot'), dict) else {}
    model_failure = _public_model_failure(workspace, generation, snapshot)
    if workspace.parent_id:
        scope = 'scenario'
    elif snapshot.get('workflow') == 'scenarios':
        scope = 'planning'
    else:
        scope = 'case'
    retry = {
        'available': False, 'reason': '当前记录没有可重试的模型服务失败。',
        'mode': None, 'scope': scope, 'requires_execution_confirmation': True,
    }

    prefetched = getattr(workspace, '_prefetched_objects_cache', {})
    if not model_failure:
        prefetched_children = known_children
        if prefetched_children is None:
            prefetched_children = (
                list(workspace.scenarios.all())
                if workspace.parent_id is None and 'scenarios' in prefetched else []
            )
        if prefetched_children:
            retry['scope'] = 'scenario'
            retry['reason'] = '根工作区已包含子场景，请选择具体失败子场景重试；不会重跑已通过的子场景。'
        return None, retry
    children_exist = bool(known_children) if known_children is not None else (
        bool(list(workspace.scenarios.all())) if workspace.parent_id is None and 'scenarios' in prefetched
        else workspace.parent_id is None and workspace.scenarios.exists()
    )
    if children_exist:
        retry['scope'] = 'scenario'
        retry['reason'] = '根工作区已包含子场景，请选择具体失败子场景重试；不会重跑已通过的子场景。'
        return model_failure, retry
    if workspace_is_busy(workspace):
        retry['reason'] = '当前工作区族仍有任务运行，不能重复提交重试。'
        return model_failure, retry
    if generation.get('status') == 'stale' or generation.get('source_revision') != workspace.revision:
        retry['reason'] = '失败证据已过期，请使用常规生成入口重新确认当前配置。'
        return model_failure, retry
    if snapshot.get('task_id') != workspace.task_id:
        retry['reason'] = '失败任务租约已被后续操作替换，不能重放旧模型失败。'
        return model_failure, retry
    if not model_failure['retryable']:
        retry['reason'] = model_failure['message']
        return model_failure, retry
    if not snapshot:
        retry['reason'] = '失败任务缺少服务器冻结参数，不能安全重试。'
        return model_failure, retry
    if snapshot.get('model_id') != workspace.model_id:
        retry['reason'] = '所选模型已变化，不能用旧失败快照重试；请使用常规生成入口。'
        return model_failure, retry
    if snapshot.get('spec_id') != workspace.spec_id or snapshot.get('target_endpoint_id') != workspace.target_endpoint_id:
        retry['reason'] = 'API 规范或目标范围已变化，不能用旧失败快照重试。'
        return model_failure, retry
    try:
        require_generation_model_id(workspace.model_id, owner=workspace.owner)
    except WorkspaceValidationError as exc:
        retry['reason'] = str(exc)
        return model_failure, retry

    rounds = _generation_rounds(generation)
    has_http = any(result_has_http_evidence(item.get('result')) for item in rounds)
    if (workspace.debug_revision == workspace.revision
            and result_has_http_evidence(workspace.debug_result)):
        has_http = True
    candidate = workspace.candidate if isinstance(workspace.candidate, dict) else {}
    has_current_candidate = (
        candidate.get('source_revision') == workspace.revision
        and isinstance(candidate.get('draft'), dict)
    )
    if model_failure['stage'] == 'planning':
        retry.update({
            'available': True, 'mode': 'generate',
            'reason': '将复用服务器冻结的规划描述、模型、接口范围、目标地址和变量，仅重试规划。',
        })
        return model_failure, retry
    if has_http or has_current_candidate or model_failure['stage'] == 'repairing':
        try:
            repair_draft, repair_result = generation_repair_seed(workspace)
        except WorkspaceValidationError as exc:
            retry['reason'] = str(exc)
            return model_failure, retry
        from .workspace_verification import classify_result
        _status, safety_reason, disposition = classify_result(repair_result, repair_draft)
        if disposition != 'repair':
            retry['reason'] = f'现有运行证据不可安全重放：{safety_reason}'
            return model_failure, retry
        retry.update({
            'available': True, 'mode': 'repair',
            'reason': (
                '已有目标接口运行证据；将复用服务器冻结参数和现有候选，仅修复并重新试运行当前失败范围，'
                '可能再次向冻结目标发送请求；不会重跑已通过的同级场景。'
            ),
        })
        return model_failure, retry
    retry.update({
        'available': True, 'mode': 'generate',
        'reason': '失败前未发送目标 HTTP；将复用服务器冻结参数，仅重新生成并试运行当前失败范围。',
    })
    return model_failure, retry


def owned_workspace(*, project_id: int, workspace_id: int, user, lock: bool = False) -> APIWorkspace:
    queryset = APIWorkspace.objects.filter(
        project_id=project_id, id=workspace_id, owner=user,
    )
    if lock:
        # Do not join nullable relations into the locking query: PostgreSQL
        # rejects that shape and MySQL may widen the locked relation set.
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related('saved_case', 'spec', 'parent__spec').prefetch_related(
            models.Prefetch(
                'scenarios',
                queryset=APIWorkspace.objects.select_related('saved_case').order_by('scenario_order', 'id'),
            ),
        )
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
                           model_id: Any = _UNSET, endpoint_ids: Any = _UNSET,
                           spec_id: Any = _UNSET, title: Any = _UNSET,
                           assertion_review_ack: Any = _UNSET) -> APIWorkspace:
    require_revision(workspace, revision)
    if workspace_is_busy(workspace):
        raise WorkspaceConflict('当前工作区任务尚未结束，不能修改草稿。')
    changed = False
    title_changed = False
    next_spec_id = workspace.spec_id
    if spec_id is not _UNSET:
        spec = validate_spec_id(workspace.project_id, spec_id, owner=workspace.owner)
        next_spec_id = spec.id if spec else None
    next_endpoint_ids = workspace.endpoint_ids if isinstance(workspace.endpoint_ids, list) else []
    if endpoint_ids is not _UNSET:
        if not isinstance(endpoint_ids, list):
            raise WorkspaceValidationError('endpoint_ids 必须是数组。')
        next_endpoint_ids = endpoint_ids
    if next_endpoint_ids and next_spec_id is None:
        inferred_spec_id = infer_spec_id(workspace.project_id, next_endpoint_ids)
        if inferred_spec_id is None:
            raise WorkspaceValidationError('所选端点必须来自同一可用 API 规范，请明确选择 spec_id。')
        next_spec_id = validate_spec_id(workspace.project_id, inferred_spec_id, owner=workspace.owner).id
    if next_endpoint_ids:
        endpoint_specs(workspace.project_id, next_endpoint_ids, spec_id=next_spec_id, owner=workspace.owner)
    proposed_draft = normalize_draft(draft) if draft is not _UNSET else workspace.draft
    if workspace.target_endpoint_id is not None:
        if next_spec_id is None:
            raise WorkspaceValidationError('显式目标工作区必须保留 API 规范。')
        if workspace.parent_id and endpoint_ids is not _UNSET and next_endpoint_ids != [workspace.target_endpoint_id]:
            raise WorkspaceValidationError('显式端点子场景的业务目标范围必须保持为唯一目标端点。')
        validate_target_endpoint_id(
            workspace.project_id, workspace.target_endpoint_id, spec_id=next_spec_id,
            endpoint_ids=next_endpoint_ids, owner=workspace.owner,
        )
        available_endpoint_ids = next_endpoint_ids
        if workspace.parent_id:
            available_endpoint_ids = [item['id'] for item in generation_endpoint_specs(workspace)]
        validate_target_draft(
            proposed_draft, project_id=workspace.project_id, spec_id=next_spec_id,
            endpoint_ids=available_endpoint_ids, target_endpoint_id=workspace.target_endpoint_id,
            owner=workspace.owner, require_target=bool(proposed_draft.get('teststeps')),
        )
    candidate = workspace.candidate if isinstance(workspace.candidate, dict) else {}
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    previous_revision = workspace.revision
    child_model_only = (
        workspace.parent_id is not None and model_id is not _UNSET
        and draft is _UNSET and endpoint_ids is _UNSET and spec_id is _UNSET
    )

    def current_source(value: dict[str, Any], key: str = 'source_revision') -> bool:
        return not value or value.get(key) == previous_revision

    # Model-only selection changes no HTTP script or verification context.  It
    # may carry current failed evidence into the next revision, but never
    # revive already-stale/mismatched proof or rewrite its historical snapshot.
    preserve_child_evidence = (
        child_model_only
        and generation.get('status') != 'stale'
        and current_source(generation)
        and current_source(candidate)
        and workspace.debug_revision in {None, previous_revision}
        and generation.get('adopted_revision') in {None, previous_revision}
    )
    candidate_draft = candidate.get('draft') if isinstance(candidate.get('draft'), dict) else None
    review = candidate.get('review') if isinstance(candidate.get('review'), dict) else {}
    review_adoption = bool(
        draft is not _UNSET and candidate_draft is not None
        and review.get('requires_confirmation') is True
        and proposed_draft == normalize_draft(candidate_draft)
    )
    if review_adoption and assertion_review_ack != candidate.get('draft_hash'):
        raise WorkspaceConflict('候选修改了受保护断言，assertion_review_ack 缺失或已过期。')
    exact_adoption = False
    if draft is not _UNSET and candidate_draft is not None:
        from .workspace_verification import draft_hash
        rounds = generation.get('rounds') if isinstance(generation.get('rounds'), list) else []
        final_round = rounds[-1] if rounds and isinstance(rounds[-1], dict) else {}
        exact_adoption = (
            model_id is _UNSET and endpoint_ids is _UNSET and spec_id is _UNSET
            and
            generation.get('status') == 'passed'
            and candidate.get('source_revision') == workspace.revision
            and generation.get('source_revision') == workspace.revision
            and candidate.get('draft_hash') == draft_hash(candidate_draft)
            and final_round.get('draft_hash') == candidate.get('draft_hash')
            and proposed_draft == candidate_draft
            and isinstance(final_round.get('result'), dict)
        )
    if draft is not _UNSET:
        workspace.draft = proposed_draft
        if not exact_adoption:
            workspace.candidate = None
            workspace.debug_result = {}
            workspace.debug_snapshot = {}
            workspace.debug_revision = None
        changed = True
    if title is not _UNSET:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise WorkspaceValidationError('title 必须是 1 到 200 个字符。')
        workspace.title = title.strip()
        title_changed = True
    if model_id is not _UNSET:
        workspace.model_id = validate_model_id(model_id, owner=workspace.owner)
        changed = True
    if endpoint_ids is not _UNSET:
        workspace.endpoint_ids = next_endpoint_ids
        changed = True
    if spec_id is not _UNSET:
        workspace.spec_id = next_spec_id
        changed = True
    if changed:
        workspace.revision += 1
        workspace.status = APIWorkspace.Status.IDLE
        workspace.error = ''
        workspace.task_id = ''
        if exact_adoption:
            workspace.debug_result = deepcopy(final_round['result'])
            workspace.debug_snapshot = {}
            workspace.debug_revision = workspace.revision
            workspace.generation = {**generation, 'adopted_revision': workspace.revision}
            workspace.candidate = None
        elif review_adoption:
            workspace.candidate = None
            workspace.debug_result = {}
            workspace.debug_snapshot = {}
            workspace.debug_revision = None
            workspace.generation = {**generation, 'adopted_revision': workspace.revision}
        elif preserve_child_evidence:
            rebound_generation = deepcopy(generation)
            if rebound_generation.get('source_revision') == previous_revision:
                rebound_generation['source_revision'] = workspace.revision
            if rebound_generation.get('adopted_revision') == previous_revision:
                rebound_generation['adopted_revision'] = workspace.revision
            workspace.generation = rebound_generation
            if candidate.get('source_revision') == previous_revision:
                workspace.candidate = {**deepcopy(candidate), 'source_revision': workspace.revision}
            if workspace.debug_revision == previous_revision:
                workspace.debug_revision = workspace.revision
        elif workspace.generation:
            workspace.generation = {**workspace.generation, 'status': 'stale', 'phase': 'finished'}
        workspace.save()
    elif title_changed:
        workspace.save(update_fields=['title', 'updated_at'])
    return workspace


def workspace_is_busy(workspace: APIWorkspace) -> bool:
    active = {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}
    root_id = workspace.parent_id or workspace.id
    return APIWorkspace.objects.filter(
        models.Q(pk=root_id) | models.Q(parent_id=root_id), status__in=active,
    ).exists()


def expire_stalled_workspace(workspace: APIWorkspace, *, now=None) -> APIWorkspace:
    """Mark an orphaned queued task failed during polling; never retry it here."""
    if workspace.status not in {APIWorkspace.Status.GENERATING, APIWorkspace.Status.DEBUGGING}:
        return workspace
    now = now or timezone.now()
    def deadline(value: APIWorkspace):
        if value.status == APIWorkspace.Status.DEBUGGING:
            snapshot = value.debug_snapshot if isinstance(value.debug_snapshot, dict) else {}
            budget = snapshot.get('budget') if isinstance(snapshot.get('budget'), dict) else {}
            deadlines = budget.get('deadlines') if isinstance(budget.get('deadlines'), dict) else {}
            key = 'execution_at' if budget.get('claimed_at') else 'queue_at'
            parsed = parse_datetime(str(deadlines.get(key) or ''))
            if parsed is not None and timezone.is_aware(parsed):
                return parsed, key
            return None, 'invalid'
        generation = value.generation if isinstance(value.generation, dict) else {}
        snapshot = generation.get('_snapshot') if isinstance(generation.get('_snapshot'), dict) else {}
        deadlines = snapshot.get('deadlines') if isinstance(snapshot.get('deadlines'), dict) else {}
        if not snapshot.get('claimed_at'):
            waiting_key = 'batch_at' if snapshot.get('queue_managed_by_parent') is True else 'queue_at'
            parsed = parse_datetime(str(deadlines.get(waiting_key) or ''))
            if parsed is not None and timezone.is_aware(parsed):
                return parsed, waiting_key
            return None, 'invalid'
        active = [
            parse_datetime(str(deadlines.get(key) or ''))
            for key in ('execution_at', 'batch_at')
        ]
        active = [item for item in active if item is not None and timezone.is_aware(item)]
        if active:
            return min(active), 'active'
        return None, 'invalid'

    current_deadline, deadline_kind = deadline(workspace)
    if current_deadline is not None and now <= current_deadline:
        return workspace
    with transaction.atomic():
        current = APIWorkspace.objects.select_for_update().get(pk=workspace.pk)
        if current.status != workspace.status or current.task_id != workspace.task_id:
            return current
        locked_deadline, locked_kind = deadline(current)
        if locked_deadline is not None and now <= locked_deadline:
            return current
        was_debugging = current.status == APIWorkspace.Status.DEBUGGING
        current.status = APIWorkspace.Status.FAILED
        current.error = (
            '异步任务缺少合法冻结预算，已标记失败；请重新发起。'
            if locked_kind == 'invalid'
            else
            '异步任务排队超过独立等待时限，已标记失败；未消耗执行预算且未自动重试。'
            if locked_kind == 'queue_at'
            else '异步任务未在限定执行时间内完成，已标记失败；未自动重试。'
        )
        if was_debugging:
            current.debug_snapshot = {}
            current.debug_result = {'status': 'error', 'error': current.error}
        else:
            generation = deepcopy(current.generation) if isinstance(current.generation, dict) else {}
            generation.update({
                'status': 'failed', 'phase': 'finished', 'summary': current.error,
                'finished_at': now.isoformat(),
            })
            current.generation = generation
        current.save()
        # A root owns its child execution lease.  Once it expires, queued or
        # running children must not leave the whole family permanently busy.
        if current.parent_id is None and not was_debugging:
            for child in APIWorkspace.objects.select_for_update().filter(
                parent_id=current.id, status=APIWorkspace.Status.GENERATING,
            ):
                child_generation = deepcopy(child.generation) if isinstance(child.generation, dict) else {}
                child_generation.update({
                    'status': 'failed', 'phase': 'finished', 'summary': current.error,
                    'finished_at': now.isoformat(),
                })
                child.generation = child_generation
                child.status = APIWorkspace.Status.FAILED
                child.error = current.error
                child.save()
        return current


def generation_timeout_seconds() -> int:
    """Bound active work for one generated scenario, including its retries."""
    return _configured_timeout('API_GENERATION_TIMEOUT_SECONDS', 1800)


def generation_batch_timeout_seconds() -> int:
    """Bound active root planning plus its ordered scenario batch."""
    return _configured_timeout('API_GENERATION_BATCH_TIMEOUT_SECONDS', 7200)


def generation_queue_timeout_seconds() -> int:
    """Bound queue waiting independently from active execution budgets."""
    return _configured_timeout('API_GENERATION_QUEUE_TIMEOUT_SECONDS', 1800)


def _configured_timeout(name: str, default: int) -> int:
    configured = os.environ.get(name, str(default))
    try:
        return max(60, int(configured))
    except (TypeError, ValueError):
        return default


def generation_budget(*, model_id: int | None, owner, queued_at=None) -> dict[str, Any]:
    """Freeze queue/active/batch/LLM budgets when the request is accepted."""
    queued_at = queued_at or timezone.now()
    from ai_core.model_manager import DEFAULT_LLM_TIMEOUT
    from ai_core.config_access import usable_llm_configurations
    llm_seconds = DEFAULT_LLM_TIMEOUT
    model = usable_llm_configurations().filter(pk=model_id).only('provider', 'extra_config').first()
    if model:
        default_llm = 30 if model.provider == 'ollama' else DEFAULT_LLM_TIMEOUT
        configured = model.extra_config.get('timeout', default_llm) if isinstance(model.extra_config, dict) else default_llm
        try:
            llm_seconds = max(1, int(configured))
        except (TypeError, ValueError):
            llm_seconds = default_llm
    queue_seconds = generation_queue_timeout_seconds()
    return {
        'queued_at': queued_at.isoformat(),
        'claimed_at': None,
        'started_at': None,
        'finished_at': None,
        'timeouts': {
            'queue_seconds': queue_seconds,
            'execution_seconds': generation_timeout_seconds(),
            'batch_seconds': generation_batch_timeout_seconds(),
            'llm_seconds': llm_seconds,
        },
        'deadlines': {
            'queue_at': (queued_at + timedelta(seconds=queue_seconds)).isoformat(),
            'execution_at': None,
            'batch_at': None,
        },
    }


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


def workspace_has_ai_creation_evidence(workspace: APIWorkspace) -> bool:
    """Return whether this unsaved workspace has adopted a server-recorded AI candidate."""
    generation = workspace.generation if isinstance(workspace.generation, dict) else {}
    adopted_revision = generation.get('adopted_revision')
    return (
        isinstance(adopted_revision, int)
        and not isinstance(adopted_revision, bool)
        and 0 < adopted_revision <= workspace.revision
    )


def create_or_update_case(workspace: APIWorkspace, *, revision: int, title: str | None,
                          description: Any = _UNSET, test_type: Any = _UNSET) -> APIWorkspace:
    with transaction.atomic():
        workspace = APIWorkspace.objects.select_for_update().get(pk=workspace.pk)
        require_revision(workspace, revision)
        if workspace_is_busy(workspace):
            raise WorkspaceConflict('当前工作区任务尚未结束，不能保存。')
        draft = require_executable_draft(workspace.draft)
        if workspace.target_endpoint_id is not None:
            if workspace.spec_id is None:
                raise WorkspaceValidationError('显式目标工作区缺少 API 规范，不能保存。')
            endpoint = validate_target_endpoint_id(
                workspace.project_id, workspace.target_endpoint_id, spec_id=workspace.spec_id,
                endpoint_ids=workspace.endpoint_ids, owner=workspace.owner,
            )
            available_endpoint_ids = workspace.endpoint_ids
            if workspace.parent_id:
                available_endpoint_ids = [item['id'] for item in generation_endpoint_specs(workspace)]
            validate_target_draft(
                draft, project_id=workspace.project_id, spec_id=workspace.spec_id,
                endpoint_ids=available_endpoint_ids, target_endpoint_id=workspace.target_endpoint_id,
                owner=workspace.owner,
            )
            test_case_type = 'endpoint'
        else:
            test_case_type, endpoint = classify_case_draft(draft, project_id=workspace.project_id)
        case = None
        if workspace.saved_case_id:
            case = APITestCase.objects.select_for_update().get(
                pk=workspace.saved_case_id, project_id=workspace.project_id,
            )
            if workspace.target_endpoint_id is not None and (
                    case.test_case_type != 'endpoint' or case.endpoint_id != workspace.target_endpoint_id):
                raise WorkspaceValidationError('关联用例与工作区显式目标端点不一致。')
            if workspace.saved_case_updated_at and case.updated_at != workspace.saved_case_updated_at:
                raise WorkspaceConflict('关联用例已被其他人修改，请先处理冲突。')
        if test_case_type == 'endpoint':
            if test_type is _UNSET:
                case_test_type = case.test_type if case else 'positive'
            else:
                valid_test_types = {value for value, _label in APITestCase.TEST_TYPE_CHOICES}
                if not isinstance(test_type, str) or test_type not in valid_test_types:
                    raise WorkspaceValidationError('test_type 仅支持 positive、negative、boundary 或 security。')
                case_test_type = test_type
        else:
            if test_type is not _UNSET:
                raise WorkspaceValidationError('场景用例不接受 test_type。')
            case_test_type = case.test_type if case else 'positive'
        case_title = (
            title if isinstance(title, str) and title.strip()
            else (case.title if case else (draft['config']['name'] or workspace.title or '未命名 API 用例'))
        )
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
                test_case_type=test_case_type, endpoint=endpoint, test_type=case_test_type,
                script_content=__import__('json').dumps(draft, ensure_ascii=False),
                creation_source=(
                    'ai' if workspace_has_ai_creation_evidence(workspace) else 'manual'
                ),
            )
            workspace.saved_case = case
        else:
            case.title = case_title[:200]
            case.description = case_description
            case.script_content = __import__('json').dumps(draft, ensure_ascii=False)
            case.test_case_type = test_case_type
            case.endpoint = endpoint
            case.test_type = case_test_type
            case.save(update_fields=[
                'title', 'description', 'script_content', 'test_case_type', 'endpoint', 'test_type', 'updated_at',
            ])
            workspace.saved_case = case
        workspace.saved_case_updated_at = case.updated_at
        workspace.save(update_fields=['saved_case', 'saved_case_updated_at', 'updated_at'])
    return workspace
