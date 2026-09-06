export const ASSISTANT_ACTIVE_STATUSES = new Set(['queued', 'running'])

export const unwrapAssistantResponse = response => response?.data ?? response ?? {}

export const assistantList = response => {
  const body = unwrapAssistantResponse(response)
  if (Array.isArray(body)) return body
  if (Array.isArray(body.items)) return body.items
  if (Array.isArray(body.results)) return body.results
  if (Array.isArray(body.data)) return body.data
  if (Array.isArray(body.data?.items)) return body.data.items
  if (Array.isArray(body.data?.results)) return body.data.results
  return []
}

export const assistantErrorMessage = (error, fallback) => (
  error?.response?.data?.message || error?.response?.data?.detail || error?.message || fallback
)

export const isAssistantActive = assistant => ASSISTANT_ACTIVE_STATUSES.has(assistant?.status)

export const assistantListParams = context => {
  const params = {}
  if (context?.mode) params.mode = context.mode
  if (context?.testCaseId) params.test_case_id = context.testCaseId
  if (context?.executionId) params.execution_id = context.executionId
  if (context?.suiteCaseId) params.suite_case_id = context.suiteCaseId
  return params
}

export const assistantPanelContext = (editContext, repairContext) => (
  editContext
    ? { mode: 'edit', testCaseId: editContext.testCaseId }
    : { mode: 'repair', executionId: repairContext?.executionId, suiteCaseId: repairContext?.suiteCaseId }
)

export const assistantsForContext = (items, context) => (
  (Array.isArray(items) ? items : []).filter(item => !context?.mode || item?.mode === context.mode)
)

export const hasAssistantBlockers = assistant => Array.isArray(assistant?.blockers) && assistant.blockers.length > 0

export const canContinueCandidate = assistant => (
  Boolean(assistant?.candidate_hash && assistant?.candidate_script)
  && !isAssistantActive(assistant)
  && assistant.status !== 'applied'
)

export const verifyActionState = assistant => {
  const state = assistant?.verify_action
  if (!state || typeof state !== 'object') return { can_verify: false, requires_acknowledge_review: false }
  return {
    can_verify: state.can_verify === true,
    requires_acknowledge_review: state.requires_acknowledge_review === true
  }
}

export const canVerifyCandidate = assistant => (
  canContinueCandidate(assistant)
  && ['candidate_ready', 'candidate_passed'].includes(assistant.status)
  && verifyActionState(assistant).can_verify
)

export const repairAdoptionState = assistant => {
  const state = assistant?.adoption
  if (!state || typeof state !== 'object') return { kind: 'unavailable', can_apply: false, requires_acknowledge_review: false }
  return {
    kind: state.kind || 'unavailable',
    can_apply: state.can_apply === true,
    requires_acknowledge_review: state.requires_acknowledge_review === true
  }
}

// Manual saving is deliberately separate from automatic verification.  The
// server-computed adoption state remains the authority for both paths.
export const canApplyRepairCandidate = assistant => (
  assistant?.mode === 'repair'
  && ['candidate_ready', 'candidate_passed'].includes(assistant?.status)
  && repairAdoptionState(assistant).can_apply
)

export const expandedAssistantRowIds = (expandedIds, rowId, open) => {
  const id = String(rowId)
  const values = Array.isArray(expandedIds) ? expandedIds.map(String) : []
  return open ? [...new Set([...values, id])] : values.filter(value => value !== id)
}

export const assistantModelLabel = model => `${model?.provider_name || model?.provider || 'LLM'} · ${model?.model_name || '未命名模型'}`

const isVerificationInterrupted = (verification, sessionStatus) => (
  ['queued', 'pending', 'running'].includes(verification?.status)
  && ['cancelled', 'failed'].includes(sessionStatus)
)

export const verificationLabel = (verification, sessionStatus) => isVerificationInterrupted(verification, sessionStatus)
  ? (sessionStatus === 'cancelled' ? '本次验证已取消' : '本次验证已中止')
  : ({
      passed: '实际验证通过',
      pending: '已排队等待实际验证',
      queued: '已排队等待实际验证',
      running: '正在实际验证',
      failed: '实际验证失败',
      error: '实际验证异常',
      incomplete: '已实际运行但验证不完整',
      stopped: '实际验证已停止',
      unverified: '尚未实际验证'
    })[verification?.status] || '尚未实际验证'

export const verificationTagType = (verification, sessionStatus) => isVerificationInterrupted(verification, sessionStatus)
  ? (sessionStatus === 'cancelled' ? 'info' : 'danger')
  : ({
      passed: 'success', pending: 'warning', queued: 'warning', running: 'warning', failed: 'danger', error: 'danger', incomplete: 'warning', stopped: 'warning', unverified: 'info'
    })[verification?.status] || 'info'

export const assistantAttemptStatusLabel = status => ({
  passed: '执行通过',
  failed: '执行失败',
  error: '执行异常',
  incomplete: '验证未完成',
  pending: '等待执行',
  running: '正在执行',
  not_run: '未实际执行'
})[status] || '未验证'
