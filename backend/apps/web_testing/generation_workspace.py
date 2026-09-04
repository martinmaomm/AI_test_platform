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
from .assertion_state import analyze_assertion_state
from .script_contract import ScriptContractError, normalize_for_storage
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
REPAIR_CANDIDATE_STATUSES = frozenset({'candidate_ready', 'candidate_passed'})
MAX_ARTIFACT_HISTORY = 8
MAX_REPAIR_CANDIDATE_CHARS = 200000
MAX_REPAIR_DIFF_CHARS = 20000
MAX_REPAIR_SUMMARY_CHARS = 2000
MAX_REPAIR_BLOCKERS = 20
MAX_REPAIR_ERROR_CODE_CHARS = 96
_SECRET_VARIABLE_RE = re.compile(r'(?i)(password|passwd|token|secret|api[_-]?key|credential)')


class WorkspaceConflict(ValueError):
    """Raised when a client or task attempts to mutate an obsolete workspace."""

    def __init__(self, message: str, generation: WebUIScriptGeneration):
        super().__init__(message)
        self.generation = generation


def script_hash(script: str | None) -> str:
    # Keep the user's draft byte-for-byte, but match ScriptContract's storage canonicalization.
    return hashlib.sha256((script or '').strip().encode('utf-8')).hexdigest()


def variables_fingerprint(variables: Any) -> str:
    """Fingerprint the persisted variable definitions without retaining secrets."""
    try:
        payload = json.dumps(
            normalize_variable_definitions(variables),
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        )
    except (TypeError, ValueError):
        payload = '[]'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _verification(*, status: str = 'unverified', script: str = '', **values: Any) -> dict[str, Any]:
    result = {
        'status': status,
        'script_hash': script_hash(script) if script else '',
        'execution_id': None,
        'task_id': '',
        'locked_revision': None,
        'target_url_fingerprint': '',
        'variables_fingerprint': '',
        'message': '',
        'error_message': '',
        'runtime_variables_present': False,
        'runtime_assertion_count': 0,
        'diagnostics': [],
        'assertion_state': analyze_assertion_state(script),
    }
    result.update(values)
    return result


def evaluate_workspace_draft(
    script: str, *, target_url: str, snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the shared static draft gate for generated and manually edited code.

    This deliberately does not compare a hand-edited AST with an old replay
    plan.  The v5 quality contract is the single static authority; execution
    proof remains separately guarded by ``finish_debug``.
    """
    from .draft_quality import evaluate_draft

    report = evaluate_draft(script, target_url=target_url, snapshot=snapshot)
    if not isinstance(report, dict):
        raise ValueError('脚本静态检查未返回有效结果。')
    report = deepcopy(report)
    report['assertion_state'] = analyze_assertion_state(script)
    report.setdefault('blockers', [])
    report.setdefault('warnings', [])
    report.setdefault('completion', 'unknown')
    if report.get('status') not in {'ready', 'ready_with_warnings', 'needs_review'}:
        report['status'] = 'needs_review'
        report['blockers'].append({
            'level': 'blocker', 'code': 'DRAFT_QUALITY_STATUS_INVALID',
            'message': '脚本静态检查返回了未知状态。',
        })
    return report


def _repair(**values: Any) -> dict[str, Any]:
    result = {
        'status': 'idle',
        'phase': '',
        'attempt_count': 0,
        'attempts': [],
        'task_id': '',
        'source_revision': None,
        'script_hash': '',
        'message': '',
        'blockers': [],
        'candidate_script': '',
        'candidate_diff': '',
        'candidate_quality_report': {},
        'candidate_error_code': '',
        'candidate_error_message': '',
        'summary': '',
        'runtime_variables_present': False,
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
            if key in verification or key in {'message', 'error_message', 'completed_at', 'started_at', 'locked_revision', 'runtime_variables_present'}
        })
    repair = _repair()
    if isinstance(raw.get('repair'), dict):
        repair.update({
            key: deepcopy(value) for key, value in raw['repair'].items()
            if key in repair or key in {'completed_at', 'started_at', 'candidate_hash'}
        })
    repair['attempts'] = _sanitize_repair_attempts(repair.get('attempts'))
    repair['attempt_count'] = len(repair['attempts'])
    repair['summary'] = str(repair.get('summary') or '')[:MAX_REPAIR_SUMMARY_CHARS]
    repair['message'] = str(repair.get('message') or '')[:MAX_REPAIR_SUMMARY_CHARS]
    repair['blockers'] = _sanitize_repair_blockers(repair.get('blockers'))
    repair['candidate_script'] = str(repair.get('candidate_script') or '')[:MAX_REPAIR_CANDIDATE_CHARS]
    repair['candidate_diff'] = str(repair.get('candidate_diff') or '')[:MAX_REPAIR_DIFF_CHARS]
    if 'variables' not in raw:
        variables = infer_script_variables(script)
    artifact_history = raw.get('artifact_history') if isinstance(raw.get('artifact_history'), list) else []
    artifact_history = [deepcopy(item) for item in artifact_history[-MAX_ARTIFACT_HISTORY:] if isinstance(item, dict)]
    agent_run = raw.get('_agent_run') if isinstance(raw.get('_agent_run'), dict) else {}
    return {
        'revision': revision, 'variables': variables, 'verification': verification,
        'repair': repair, 'artifact_history': artifact_history,
        '_agent_run': deepcopy(agent_run),
    }


def _sanitize_repair_attempts(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    allowed = {
        'round', 'candidate_hash', 'mcp_checked', 'static_status', 'execution_id',
        'execution_status', 'summary', 'has_screenshot', 'runtime_assertion_count',
    }
    cleaned = []
    for item in items[-2:]:
        if not isinstance(item, dict):
            continue
        entry = {key: deepcopy(item[key]) for key in allowed if key in item}
        entry['round'] = min(2, max(1, int(entry.get('round') or 1)))
        entry['candidate_hash'] = str(entry.get('candidate_hash') or '')[:64]
        entry['mcp_checked'] = bool(entry.get('mcp_checked'))
        entry['static_status'] = str(entry.get('static_status') or '')[:64]
        entry['execution_id'] = entry.get('execution_id') if isinstance(entry.get('execution_id'), int) else None
        entry['execution_status'] = str(entry.get('execution_status') or '')[:32]
        entry['summary'] = str(entry.get('summary') or '')[:MAX_REPAIR_SUMMARY_CHARS]
        entry['has_screenshot'] = bool(entry.get('has_screenshot'))
        entry['runtime_assertion_count'] = max(0, int(entry.get('runtime_assertion_count') or 0))
        cleaned.append(entry)
    return cleaned


def _sanitize_repair_blockers(value: Any) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else []
    return [
        {
            'severity': str(item.get('severity') or '')[:32],
            'code': str(item.get('code') or '')[:96],
            'message': str(item.get('message') or '')[:MAX_REPAIR_SUMMARY_CHARS],
        }
        for item in items[:MAX_REPAIR_BLOCKERS] if isinstance(item, dict)
    ]


def _sanitize_repair_text(value: Any, *, limit: int = MAX_REPAIR_SUMMARY_CHARS) -> str:
    """Constrain task-provided text before it reaches the durable workspace."""
    return str(value or '')[:limit]


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
    """Expose a passed badge only while script, variables, and URL still match."""
    workspace = workspace_for_generation(generation)
    verification = workspace['verification']
    assertion_state = analyze_assertion_state(generation.script_draft)
    verification['assertion_state'] = assertion_state
    if verification.get('status') == 'passed' and (
        assertion_state['status'] != 'complete'
        or int(verification.get('runtime_assertion_count') or 0) == 0
    ):
        verification['status'] = 'incomplete'
        verification['message'] = '当前脚本仍有待补充项，或缺少有效断言及实际执行记录，不能显示为已通过。'
        verification['error_message'] = verification['message']
    if verification.get('status') == 'passed' and (
        verification.get('locked_revision') != workspace['revision']
        or verification.get('script_hash') != script_hash(generation.script_draft)
        or verification.get('variables_fingerprint') != variables_fingerprint(workspace['variables'])
        or verification.get('target_url_fingerprint') != script_hash(generation.target_url)
    ):
        verification['status'] = 'unverified'
        verification['message'] = '脚本版本、变量定义或目标网址已变化，旧调试结果不能代表当前配置。'
    return workspace


def _set_workspace(generation: WebUIScriptGeneration, workspace: dict[str, Any]) -> None:
    generation.workspace = workspace
    generation.save(update_fields=['workspace', 'updated_at'])


def infer_script_variables(script: str) -> list[dict[str, Any]]:
    """Offer script variables referenced by a generated draft without values."""
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
        if workspace['repair']['status'] in REPAIR_CANDIDATE_STATUSES:
            raise WorkspaceConflict('请先采用或放弃当前候选。', generation)
        persisted_variables = _without_persisted_secret_values(variables)
        if generation.script_draft == script_draft and workspace['variables'] == persisted_variables:
            return generation
        generation.script_draft = script_draft
        workspace['revision'] += 1
        workspace['variables'] = persisted_variables
        workspace['verification'] = _verification(script=script_draft)
        workspace['repair'] = _repair()
        generation.workspace = workspace
        generation.quality_report = evaluate_workspace_draft(
            script_draft, target_url=generation.target_url, snapshot=generation.exploration_snapshot,
        )
        generation.save(update_fields=['script_draft', 'workspace', 'quality_report', 'updated_at'])
    return generation


def prepare_debug(generation_id: Any, *, expected_revision: int, execution_id: int, runtime_variables_present: bool = False):
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        if generation.status in ACTIVE_GENERATION_STATUSES:
            raise WorkspaceConflict('生成任务仍在处理中，暂不能调试草稿。', generation)
        workspace = workspace_for_generation(generation)
        if workspace['revision'] != expected_revision:
            raise WorkspaceConflict('工作区版本已变化，请刷新后重试。', generation)
        if workspace['verification']['status'] in BUSY_VERIFICATION_STATUSES:
            raise WorkspaceConflict('当前草稿正在调试。', generation)
        if workspace['repair']['status'] in BUSY_REPAIR_STATUSES:
            raise WorkspaceConflict('修复正在生成，暂不能调试。', generation)
        if workspace['repair']['status'] in REPAIR_CANDIDATE_STATUSES:
            raise WorkspaceConflict('请先采用或放弃当前候选。', generation)
        report = evaluate_workspace_draft(
            generation.script_draft, target_url=generation.target_url, snapshot=generation.exploration_snapshot,
        )
        if report.get('status') == 'needs_review' or report.get('blockers'):
            generation.quality_report = report
            generation.save(update_fields=['quality_report', 'updated_at'])
            raise WorkspaceConflict('当前草稿未通过静态检查，不能执行调试。', generation)
        generation.quality_report = report
        generation.save(update_fields=['quality_report', 'updated_at'])
        current_hash = script_hash(generation.script_draft)
        workspace['verification'] = _verification(
            status='pending', script=generation.script_draft,
            execution_id=execution_id,
            locked_revision=expected_revision,
            target_url_fingerprint=script_hash(generation.target_url),
            variables_fingerprint=variables_fingerprint(workspace['variables']),
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


def finish_debug(generation_id: Any, *, execution_id: int, locked_revision: int, locked_hash: str, status: str, diagnostics: list[dict[str, Any]], runtime_assertion_count: int = 0, task_id: str | None = None):
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
        current_state = analyze_assertion_state(generation.script_draft)
        try:
            verified_runtime_count = max(0, int(runtime_assertion_count or 0))
        except (TypeError, ValueError):
            verified_runtime_count = 0
        if status == 'passed' and (
            current_state['status'] != 'complete' or verified_runtime_count == 0
        ):
            status = 'incomplete'
        verification['status'] = status
        verification['runtime_assertion_count'] = verified_runtime_count
        verification['assertion_state'] = current_state
        verification['diagnostics'] = diagnostics
        message = '' if status == 'passed' else str((diagnostics or [{}])[0].get('message') or '')
        if status == 'incomplete' and not message:
            if current_state['pending_count']:
                message = (
                    f"操作已完成，但仍有 {current_state['pending_count']} 项断言待补充；"
                    '删除对应 marker 并补入真实断言后重跑。'
                )
            elif verified_runtime_count == 0:
                message = '操作已完成，但本次未成功执行任何真实断言，验证未完成。'
            else:
                message = '操作已完成，但当前脚本验证条件未完成。'
        verification['message'] = message
        verification['error_message'] = message
        _set_workspace(generation, workspace)
        return True


def prepare_repair(generation_id: Any, *, expected_revision: int, runtime_variables_present: bool = False):
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
        if repair['status'] in REPAIR_CANDIDATE_STATUSES:
            raise WorkspaceConflict('请先采用或放弃当前候选。', generation)
        if verification['status'] not in {'failed', 'error'} or not verification.get('diagnostics'):
            raise WorkspaceConflict('缺少本草稿的失败诊断，不能凭空生成修复。', generation)
        locked_hash = script_hash(generation.script_draft)
        workspace['repair'] = _repair(
            status='pending', source_revision=expected_revision,
            script_hash=locked_hash, phase='collecting',
            message='正在收集失败证据。',
            runtime_variables_present=bool(runtime_variables_present),
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
        repair['phase'] = 'analyzing'
        repair['message'] = '正在分析失败证据。'
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
            status='failed', phase='completed',
            source_revision=locked_revision, script_hash=locked_hash, task_id=repair.get('task_id', ''),
            message=_sanitize_repair_text(message), blockers=_sanitize_repair_blockers(blockers),
        )
        _set_workspace(generation, workspace)
        return True


def update_repair_state(generation_id: Any, *, locked_revision: int, locked_hash: str,
                        phase: str | None = None, message: str | None = None,
                        attempts: list[dict[str, Any]] | None = None,
                        candidate_script: str | None = None, candidate_diff: str | None = None,
                        candidate_quality_report: dict[str, Any] | None = None,
                        candidate_error_code: str | None = None, candidate_error_message: str | None = None,
                        final_status: str | None = None, summary: str | None = None,
                        task_id: str | None = None) -> bool:
    """Persist only review-safe repair progress while retaining the original draft."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        if (repair.get('source_revision') != locked_revision or repair.get('script_hash') != locked_hash
                or workspace['revision'] != locked_revision or script_hash(generation.script_draft) != locked_hash
                or repair.get('status') not in {'pending', 'running'} or not _task_matches(repair, task_id)):
            return False
        if phase is not None:
            repair['phase'] = phase
        if message is not None:
            repair['message'] = _sanitize_repair_text(message)
        if attempts is not None:
            repair['attempts'] = _sanitize_repair_attempts(attempts)
            repair['attempt_count'] = len(repair['attempts'])
        if candidate_script is not None:
            candidate_script = str(candidate_script)
            if len(candidate_script) > MAX_REPAIR_CANDIDATE_CHARS:
                raise ValueError('修复候选脚本超过允许长度，已拒绝保存。')
            repair['candidate_script'] = candidate_script
            repair['candidate_hash'] = script_hash(candidate_script)
        if candidate_diff is not None:
            candidate_diff = str(candidate_diff)
            if len(candidate_diff) > MAX_REPAIR_DIFF_CHARS:
                raise ValueError('修复候选差异超过允许长度，已拒绝保存。')
            repair['candidate_diff'] = candidate_diff
        if candidate_quality_report is not None:
            repair['candidate_quality_report'] = deepcopy(candidate_quality_report)
        if candidate_error_code is not None:
            repair['candidate_error_code'] = _sanitize_repair_text(
                candidate_error_code, limit=MAX_REPAIR_ERROR_CODE_CHARS,
            )
        if candidate_error_message is not None:
            repair['candidate_error_message'] = _sanitize_repair_text(candidate_error_message)
        if summary is not None:
            repair['summary'] = _sanitize_repair_text(summary)
        if final_status is not None:
            repair['status'] = final_status
            repair['phase'] = 'completed'
        _set_workspace(generation, workspace)
        return True


def apply_repair_candidate(generation_id: Any, *, expected_revision: int, candidate_hash: str):
    """Atomically adopt a reviewed proposal and retain only valid execution proof."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        source_hash = script_hash(generation.script_draft)
        candidate = str(repair.get('candidate_script') or '')
        if (workspace['revision'] != expected_revision or repair.get('source_revision') != expected_revision
                or repair.get('script_hash') != source_hash or repair.get('candidate_hash') != candidate_hash
                or script_hash(candidate) != candidate_hash or repair.get('status') not in {'candidate_ready', 'candidate_passed'}):
            raise WorkspaceConflict('草稿或候选已变化，请刷新后重试。', generation)
        generation.script_draft = candidate
        workspace['revision'] += 1
        if repair.get('status') == 'candidate_passed':
            passed_attempt = next((item for item in reversed(repair.get('attempts') or [])
                                   if item.get('candidate_hash') == candidate_hash and item.get('execution_status') == 'passed'), None)
            candidate_state = analyze_assertion_state(candidate)
            proof_complete = bool(passed_attempt and int(passed_attempt.get('runtime_assertion_count') or 0) > 0 and candidate_state.get('status') == 'complete')
            workspace['verification'] = _verification(
                status='passed' if proof_complete else 'unverified', script=candidate,
                execution_id=(passed_attempt or {}).get('execution_id'),
                locked_revision=workspace['revision'],
                target_url_fingerprint=script_hash(generation.target_url),
                variables_fingerprint=variables_fingerprint(workspace['variables']),
                runtime_assertion_count=int((passed_attempt or {}).get('runtime_assertion_count') or 0),
                message='已继承候选的实际验证证据。' if proof_complete else '候选缺少完整的实际断言证据，采用后需重新调试验证。',
            )
        else:
            workspace['verification'] = _verification(script=candidate)
        workspace['repair'] = _repair(
            message='候选已采用。' if workspace['verification']['status'] == 'passed' else '候选已采用，需重新调试验证。'
        )
        generation.workspace = workspace
        generation.quality_report = deepcopy(repair.get('candidate_quality_report') or {})
        generation.save(update_fields=['script_draft', 'workspace', 'quality_report', 'updated_at'])
        return generation


def discard_repair_candidate(generation_id: Any, *, expected_revision: int, candidate_hash: str):
    """Discard one review proposal without changing the user-owned draft."""
    with transaction.atomic():
        generation = WebUIScriptGeneration.objects.select_for_update().get(pk=generation_id)
        workspace = workspace_for_generation(generation)
        repair = workspace['repair']
        source_hash = script_hash(generation.script_draft)
        if (workspace['revision'] != expected_revision or repair.get('source_revision') != expected_revision
                or repair.get('script_hash') != source_hash or repair.get('candidate_hash') != candidate_hash
                or script_hash(str(repair.get('candidate_script') or '')) != candidate_hash
                or repair.get('status') not in {'candidate_ready', 'candidate_passed'}):
            raise WorkspaceConflict('草稿或候选已变化，请刷新后重试。', generation)
        workspace['repair'] = _repair(message='候选已丢弃。')
        generation.workspace = workspace
        generation.save(update_fields=['workspace', 'updated_at'])
        return generation


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
