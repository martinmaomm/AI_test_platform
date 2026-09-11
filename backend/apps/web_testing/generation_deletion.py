"""Shared deletion policy for generation history and its DELETE endpoint."""

from .generation_workspace import BUSY_REPAIR_STATUSES, BUSY_VERIFICATION_STATUSES
from .models import WebUIScriptGeneration


DELETABLE_GENERATION_STATUSES = frozenset({
    WebUIScriptGeneration.Status.NEEDS_INPUT,
    WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
    WebUIScriptGeneration.Status.NEEDS_REVIEW,
    WebUIScriptGeneration.Status.READY,
    WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
    WebUIScriptGeneration.Status.CANCELLED,
    WebUIScriptGeneration.Status.FAILED,
})


def _workspace_status(generation, field):
    # History projects only these tiny JSON fields; never fetch the deferred
    # workspace (which may contain a large script/trace) for each list row.
    annotation = f'delete_{field}_status'
    if annotation in generation.__dict__:
        status = generation.__dict__[annotation]
    else:
        workspace = generation.__dict__.get('workspace')
        workspace = workspace if isinstance(workspace, dict) else {}
        state = workspace.get(field)
        status = state.get('status') if isinstance(state, dict) else None
    return status if isinstance(status, str) else None


def generation_delete_block_reason(generation):
    if generation.status not in DELETABLE_GENERATION_STATUSES:
        return '生成任务尚未结束，请先取消或等待完成后再删除。'
    if _workspace_status(generation, 'verification') in BUSY_VERIFICATION_STATUSES:
        return '脚本正在调试，请等待调试结束后再删除。'
    if _workspace_status(generation, 'repair') in BUSY_REPAIR_STATUSES:
        return '脚本正在修复，请等待修复结束后再删除。'
    return ''
