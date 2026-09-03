"""Durable editing and verification state for generated scripts."""
from __future__ import annotations

import hashlib
import json
import ast
import re
from copy import deepcopy
from typing import Any

from django.db import transaction

from .execution_variables import normalize_variable_definitions, validate_variable_name
from .models import WebUIScriptGeneration


ACTIVE_GENERATION_STATUSES = frozenset({
    WebUIScriptGeneration.Status.CREATED,
    WebUIScriptGeneration.Status.NORMALIZING,
    WebUIScriptGeneration.Status.PREFLIGHTING,
    WebUIScriptGeneration.Status.EXPLORING,
    WebUIScriptGeneration.Status.GENERATING,
    WebUIScriptGeneration.Status.VALIDATING,
    WebUIScriptGeneration.Status.REPAIRING,
})
BUSY_VERIFICATION_STATUSES = frozenset({'pending', 'running'})
BUSY_REPAIR_STATUSES = frozenset({'pending', 'running'})
MAX_WORKSPACE_REPAIRS = 2
_SECRET_VARIABLE_RE = re.compile(r'(?i)(password|passwd|token|secret|api[_-]?key|credential)')


class WorkspaceConflict(ValueError):
    """Raised when a client or task attempts to mutate an obsolete workspace."""

    def __init__(self, message: str, generation: WebUIScriptGeneration):
        super().__init__(message)
        self.generation = generation


def script_hash(script: str | None) -> str:
    # Keep the user's draft byte-for-byte, but match ScriptContract's storage canonicalization.
    return hashlib.sha256((script or '').strip().encode('utf-8')).hexdigest()


def _verification(*, status: str = 'unverified', script: str = '', **values: Any) -> dict[str, Any]:
    result = {
        'status': status,
        'script_hash': script_hash(script) if script else '',
        'environment_id': None,
        'execution_id': None,
        'task_id': '',
        'locked_revision': None,
        'environment_fingerprint': '',
        'base_url_fingerprint': '',
        'message': '',
        'error_message': '',
        'runtime_variables_present': False,
        'diagnostics': [],
    }
    result.update(values)
    return result


def _repair(**values: Any) -> dict[str, Any]:
    result = {
        'status': 'idle',
        'count': 0,
        'task_id': '',
        'source_revision': None,
        'script_hash': '',
        'message': '',
        'blockers': [],
    }
    result.update(values)
    return result


def normalize_workspace(value: Any, *, script: str = '') -> dict[str, Any]:
    """Return a forward-compatible workspace without retaining secret values."""
    raw = value if isinstance(value, dict) else {}
    try:
        revision = max(0, int(raw.get('revision', 0)))
    except (TypeError, ValueError):
        revision = 0
    try:
        variables = normalize_variable_definitions(raw.get('variables') or [])
    except ValueError:
        variables = []
    variables = _without_persisted_secret_values(variables)
    verification = _verification(script=script)
    if isinstance(raw.get('verification'), dict):
        verification.update({
            key: deepcopy(value) for key, value in raw['verification'].items()
            if key in verification or key in {'message', 'error_message', 'completed_at', 'started_at', 'locked_revision', 'environment_fingerprint', 'base_url_fingerprint', 'runtime_variables_present'}
        })
    repair = _repair()
    if isinstance(raw.get('repair'), dict):
        repair.update({
            key: deepcopy(value) for key, value in raw['repair'].items()
            if key in repair or key in {'completed_at', 'started_at', 'candidate_hash'}
        })
    if 'variables' not in raw:
        variables = infer_script_variables(script)
    return {'revision': revision, 'variables': variables, 'verification': verification, 'repair': repair}


def _without_persisted_secret_values(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**item, 'value': '' if item.get('is_secret') else str(item.get('value') or '')}
        for item in variables
    ]


def workspace_for_generation(generation: WebUIScriptGeneration) -> dict[str, Any]:
    return normalize_workspace(generation.workspace, script=generation.script_draft)


def variable_definitions_for_scenario_plan(plan: Any) -> list[dict[str, Any]]:
    """Build the editable variable table from explicit v4 InputSpec objects."""
    definitions: dict[str, dict[str, Any]] = {}
    for spec in getattr(plan, 'input_refs', ()):
        name = validate_variable_name(getattr(spec, 'name', ''))
        source = str(getattr(spec, 'source', ''))
        value_kind = str(getattr(spec, 'value_kind', 'text'))
        credential_slot = str(getattr(spec, 'credential_slot', ''))
        current = definitions.get(name)
        if current is not None:
            if current['_source'] != source:
                raise ValueError(f'变量 {name} 的来源定义冲突')
            continue
        descriptions = {
            'generated': '脚本每次运行时自动生成唯一值；也可以在执行时覆盖。',
            'runtime': '执行前必须提供的业务变量。',
            'credential': '执行前必须提供的登录信息。',
        }
        if source not in descriptions:
            raise ValueError(f'变量 {name} 的来源无效')
        definitions[name] = {
            'name': name,
            'value': '',
            'is_secret': value_kind == 'password' or (
                source == 'credential' and credential_slot == 'password'
            ),
            'required': source in {'runtime', 'credential'},
            'description': descriptions[source],
            '_source': source,
        }
    return [
        {key: value for key, value in definitions[name].items() if key != '_source'}
        for name in sorted(definitions)
    ]


def workspace_for_response(generation: WebUIScriptGeneration) -> dict[str, Any]:
    """Expose a passed badge only while its script and environment still match."""
    workspace = workspace_for_generation(generation)
    verification = workspace['verification']
    if verification.get('status') == 'passed' and (
        verification.get('locked_revision') != workspace['revision']
        or verification.get('script_hash') != script_hash(generation.script_draft)
        or verification.get('environment_id') != generation.environment_id
        or verification.get('environment_fingerprint') != environment_fingerprint(generation.environment.config)
        or not generation.environment.is_active
    ):
        verification['status'] = 'unverified'
        verification['message'] = '脚本版本或环境配置已变化，旧调试结果不能代表当前配置。'
    return workspace


def _set_workspace(generation: WebUIScriptGeneration, workspace: dict[str, Any]) -> None:
    generation.workspace = workspace
    generation.save(update_fields=['workspace', 'updated_at'])


def environment_fingerprint(config: Any) -> str:
    """Stable configuration identity without persisting environment values themselves."""
    try:
        payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    except (TypeError, ValueError):
        payload = '{}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def base_url_fingerprint(config: Any) -> str:
    base_url = ((config or {}).get('base_url') or '').rstrip('/') if isinstance(config, dict) else ''
    return hashlib.sha256(base_url.encode('utf-8')).hexdigest()


def infer_script_variables(script: str) -> list[dict[str, Any]]:
    """Offer environment variables referenced by a generated draft without values."""
    try:
        module = ast.parse(script or '')
    except SyntaxError:
        return []
    discovered: dict[str, bool] = {}
    defaults: dict[str, str] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name) and node.value.value.id == 'os' and node.value.attr == 'environ':
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                name = key.value.strip().upper()
                if re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', name):
                    discovered[name] = True
            continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not node.args:
            continue
        is_environ = isinstance(node.func.value, ast.Attribute) and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == 'os' and node.func.value.attr == 'environ'
        is_getenv = isinstance(node.func.value, ast.Name) and node.func.value.id == 'os' and node.func.attr == 'getenv'
        if not (is_environ or is_getenv) or node.func.attr not in {'get', 'getenv'}:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        name = first.value.strip().upper()
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', name):
            continue
        # getenv/get with a default is intentionally optional.
        required = len(node.args) < 2 and not node.keywords
        discovered[name] = discovered.get(name, False) or required
        default = node.args[1] if len(node.args) > 1 else next(
            (item.value for item in node.keywords if item.arg == 'default'), None,
        )
        if isinstance(default, ast.Constant) and isinstance(default.value, (str, int, float)):
            defaults.setdefault(name, str(default.value))
    variables = []
    for name, required in sorted(discovered.items()):
        try:
            validate_variable_name(name)
        except ValueError:
            # Runner-owned variables such as PLAYWRIGHT_BASE_URL must not be
            # offered as editable overrides (nor shadowed with empty strings).
            continue
        secret = bool(_SECRET_VARIABLE_RE.search(name))
        variables.append({
            'name': name, 'value': '' if secret else defaults.get(name, ''),
            'is_secret': secret, 'required': required, 'description': '',
        })
    return variables


def update_draft(generation_id: Any, *, expected_revision: int, script_draft: str, variables: list[dict[str, Any]]):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status in ACTIVE_GENERATION_STATUSES:
            raise WorkspaceConflict('生成任务仍在处理中，暂不能编辑脚本。', generation)
        if not (generation.script_draft or '').strip():
            raise WorkspaceConflict('当前生成记录没有可编辑的脚本草稿。', generation)
        workspace = workspace_for_generation(generation)
        if workspace['revision'] != expected_revision:
            raise WorkspaceConflict('工作区版本已变化，请刷新后重试。', generation)
        if workspace['verification']['status'] in BUSY_VERIFICATION_STATUSES:
            raise WorkspaceConflict('调试正在执行，不能同时编辑脚本。', generation)
        if workspace['repair']['status'] in BUSY_REPAIR_STATUSES:
            raise WorkspaceConflict('修复正在生成，不能同时编辑脚本。', generation)
        persisted_variables = _without_persisted_secret_values(variables)
        if generation.script_draft == script_draft and workspace['variables'] == persisted_variables:
            return generation
        generation.script_draft = script_draft
        workspace['revision'] += 1
        workspace['variables'] = persisted_variables
        workspace['verification'] = _verification(script=script_draft)
        workspace['repair'] = _repair(count=int(workspace['repair'].get('count') or 0))
        generation.workspace = workspace
        generation.quality_report = {'status': 'stale', 'message': '草稿已修改，原检查结果已失效。'}
        generation.save(update_fields=['script_draft', 'workspace', 'quality_report', 'updated_at'])
    return generation


def prepare_debug(generation_id: Any, *, expected_revision: int, execution_id: int, runtime_variables_present: bool = False):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().select_related('environment').get(pk=generation_id)
        if generation.status in ACTIVE_GENERATION_STATUSES:
            raise WorkspaceConflict('生成任务仍在处理中，暂不能调试草稿。', generation)
        workspace = workspace_for_generation(generation)
        if workspace['revision'] != expected_revision:
            raise WorkspaceConflict('工作区版本已变化，请刷新后重试。', generation)
        if workspace['verification']['status'] in BUSY_VERIFICATION_STATUSES:
            raise WorkspaceConflict('当前草稿正在调试。', generation)
        if workspace['repair']['status'] in BUSY_REPAIR_STATUSES:
            raise WorkspaceConflict('修复正在生成，暂不能调试。', generation)
        current_hash = script_hash(generation.script_draft)
        workspace['verification'] = _verification(
            status='pending', script=generation.script_draft,
            environment_id=generation.environment_id, execution_id=execution_id,
            locked_revision=expected_revision,
            environment_fingerprint=environment_fingerprint(generation.environment.config),
            base_url_fingerprint=base_url_fingerprint(generation.environment.config),
            runtime_variables_present=bool(runtime_variables_present),
        )
        _set_workspace(generation, workspace)
    return generation, current_hash


def attach_debug_task(generation_id: Any, *, execution_id: int, locked_revision: int, locked_hash: str, task_id: str):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        verification = workspace['verification']
        if _matches(verification, execution_id, locked_revision, locked_hash):
            verification['task_id'] = str(task_id)
            _set_workspace(generation, workspace)
    return generation


def mark_debug_running(generation_id: Any, *, execution_id: int, locked_revision: int, locked_hash: str, task_id: str | None = None):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        verification = workspace['verification']
        if (
            not _matches(verification, execution_id, locked_revision, locked_hash)
            or verification.get('status') != 'pending'
            or not _task_matches(verification, task_id)
        ):
            return None
        verification['status'] = 'running'
        _set_workspace(generation, workspace)
        return generation


def finish_debug(generation_id: Any, *, execution_id: int, locked_revision: int, locked_hash: str, status: str, diagnostics: list[dict[str, Any]], task_id: str | None = None):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        verification = workspace['verification']
        if (
            not _matches(verification, execution_id, locked_revision, locked_hash)
            or verification.get('status') not in {'pending', 'running'}
            or not _task_matches(verification, task_id)
        ):
            return False
        verification['status'] = status
        verification['diagnostics'] = diagnostics
        message = '' if status == 'passed' else str((diagnostics or [{}])[0].get('message') or '')
        verification['message'] = message
        verification['error_message'] = message
        _set_workspace(generation, workspace)
        return True


def prepare_repair(generation_id: Any, *, expected_revision: int):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status in ACTIVE_GENERATION_STATUSES:
            raise WorkspaceConflict('生成任务仍在处理中，暂不能修复草稿。', generation)
        workspace = workspace_for_generation(generation)
        verification = workspace['verification']
        repair = workspace['repair']
        if workspace['revision'] != expected_revision:
            raise WorkspaceConflict('工作区版本已变化，请刷新后重试。', generation)
        if verification['status'] in BUSY_VERIFICATION_STATUSES:
            raise WorkspaceConflict('调试正在执行，完成后才能请求修复。', generation)
        if repair['status'] in BUSY_REPAIR_STATUSES:
            raise WorkspaceConflict('修复任务已经在执行。', generation)
        if repair['count'] >= MAX_WORKSPACE_REPAIRS:
            raise WorkspaceConflict('同一草稿最多只能请求 2 次修复，请人工审核。', generation)
        if verification['status'] not in {'failed', 'error'} or not verification.get('diagnostics'):
            raise WorkspaceConflict('缺少本草稿的失败诊断，不能凭空生成修复。', generation)
        locked_hash = script_hash(generation.script_draft)
        workspace['repair'] = _repair(
            status='pending', count=repair['count'], source_revision=expected_revision,
            script_hash=locked_hash,
        )
        _set_workspace(generation, workspace)
    return generation, locked_hash


def attach_repair_task(generation_id: Any, *, locked_revision: int, locked_hash: str, task_id: str):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        if repair.get('source_revision') == locked_revision and repair.get('script_hash') == locked_hash:
            repair['task_id'] = str(task_id)
            _set_workspace(generation, workspace)
    return generation


def mark_repair_running(generation_id: Any, *, locked_revision: int, locked_hash: str, task_id: str | None = None):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        if (
            repair.get('source_revision') != locked_revision
            or repair.get('script_hash') != locked_hash
            or repair.get('status') != 'pending'
            or not _task_matches(repair, task_id)
        ):
            return None
        repair['status'] = 'running'
        _set_workspace(generation, workspace)
        return generation


def finish_repair_failure(generation_id: Any, *, locked_revision: int, locked_hash: str, message: str, blockers: list[dict[str, Any]] | None = None, task_id: str | None = None):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        if (
            repair.get('source_revision') != locked_revision
            or repair.get('script_hash') != locked_hash
            or workspace.get('revision') != locked_revision
            or script_hash(generation.script_draft) != locked_hash
            or repair.get('status') not in {'pending', 'running'}
            or not _task_matches(repair, task_id)
        ):
            return False
        workspace['repair'] = _repair(
            status='failed', count=min(MAX_WORKSPACE_REPAIRS, int(repair.get('count') or 0) + 1),
            source_revision=locked_revision, script_hash=locked_hash, task_id=repair.get('task_id', ''),
            message=message, blockers=blockers or [],
        )
        _set_workspace(generation, workspace)
        return True


def accept_repair_candidate(generation_id: Any, *, locked_revision: int, locked_hash: str, candidate_script: str, task_id: str | None = None):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        if (
            repair.get('source_revision') != locked_revision
            or repair.get('script_hash') != locked_hash
            or repair.get('status') != 'running'
            or not _task_matches(repair, task_id)
        ):
            return False
        if workspace['revision'] != locked_revision or script_hash(generation.script_draft) != locked_hash:
            return False
        generation.script_draft = candidate_script
        generation.quality_report = {'status': 'stale', 'message': '修复候选已生成，原检查结果已失效，需人工审核。'}
        workspace['revision'] += 1
        workspace['verification'] = _verification(script=candidate_script)
        workspace['repair'] = _repair(
            status='ready', count=min(MAX_WORKSPACE_REPAIRS, int(repair.get('count') or 0) + 1),
            source_revision=locked_revision, script_hash=locked_hash, task_id=repair.get('task_id', ''),
            candidate_hash=script_hash(candidate_script), message='修复草稿已生成，需人工审核后再调试。',
        )
        generation.workspace = workspace
        generation.save(update_fields=['script_draft', 'workspace', 'quality_report', 'updated_at'])
        return True


def _matches(verification: dict[str, Any], execution_id: int, revision: int, digest: str) -> bool:
    return (
        verification.get('execution_id') == execution_id
        and verification.get('locked_revision') == revision
        and verification.get('script_hash') == digest
    )


def _task_matches(state: dict[str, Any], task_id: str | None) -> bool:
    """A retry may re-enter a worker, but never impersonate a newer dispatch."""
    expected = str(state.get('task_id') or '')
    return not expected or task_id is None or expected == str(task_id)
