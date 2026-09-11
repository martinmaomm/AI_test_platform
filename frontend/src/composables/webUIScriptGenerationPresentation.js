/** Pure, presentation-only helpers for the schema-v5 exploration workspace. */

export const GENERATION_STAGES = [
  ['normalizing', '理解测试目标'],
  ['exploring', '连续探索并编写脚本'],
  ['validating', '静态检查'],
  ['completed', '草稿就绪（非测试通过）']
]

export const ACTIVE_GENERATION_STATUSES = new Set([
  'created', 'normalizing', 'preflighting', 'exploring', 'generating', 'validating', 'repairing'
])

export const PAUSED_GENERATION_STATUSES = new Set([
  'needs_input', 'needs_confirmation'
])

export const TERMINAL_GENERATION_STATUSES = new Set([
  'ready', 'ready_with_warnings', 'needs_review', 'failed', 'cancelled'
])

const STATUS_LABELS = {
  created: '等待任务启动',
  normalizing: '正在理解测试场景',
  preflighting: '正在确认目标范围与登录条件',
  exploring: '正在连续探索页面',
  generating: '正在连续探索并编写脚本',
  validating: '正在静态检查脚本',
  repairing: '正在整理脚本草稿',
  needs_input: '需要补充场景信息',
  needs_confirmation: '需要确认目标范围',
  needs_review: '需要人工检查',
  ready: '草稿已就绪（未代表测试通过）',
  ready_with_warnings: '草稿已就绪（有警告，未代表测试通过）',
  failed: '生成失败',
  cancelled: '已取消'
}

export const generationStatusLabel = (status) => STATUS_LABELS[status] || '状态未知'

/** Prefer the optional display name while keeping historic records readable. */
export const modelProviderLabel = (model, fallback = 'LLM') => (
  model?.provider_name || model?.provider || fallback
)

export const modelConfigurationLabel = (model) => (
  `${modelProviderLabel(model)} · ${model?.model_name || '未命名模型'}`
)

export const modelInfoLabel = (model, emptyLabel = '—') => (
  model?.model_name ? `${modelProviderLabel(model)} · ${model.model_name}` : emptyLabel
)

export const generationStorageKey = (userId, projectId) => (
  `automation:webui-script-generation:v5:${String(userId || 'anonymous')}:${String(projectId || 'none')}`
)

export const isActiveGeneration = (status) => ACTIVE_GENERATION_STATUSES.has(status)
export const isPausedGeneration = (status) => PAUSED_GENERATION_STATUSES.has(status)
export const isTerminalGeneration = (status) => TERMINAL_GENERATION_STATUSES.has(status)
export const shouldShowGenerationStopContext = (status) => ['failed', 'needs_review', 'cancelled'].includes(status)

export const generationLifecycleStateLabel = (state) => ({
  queued: '等待恢复任务启动',
  running: '探索任务进行中',
  interrupted: '探索已中断',
  idle: '未启动恢复探索'
})[state] || '生命周期状态未知'

export const formatGenerationLifecycleTime = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

export const canResumeInterruptedExploration = (generation, {
  busy = false,
  draftDirty = false,
  hasRepairCandidate = false
} = {}) => (
  (generation?.lifecycle?.state === 'interrupted' || (
    generation?.lifecycle?.state === 'idle' && ['failed', 'needs_review', 'cancelled'].includes(generation?.status)
  ))
  && generation?.lifecycle?.can_resume === true
  && !busy
  && !draftDirty
  && !hasRepairCandidate
)

const WORKSPACE_ACTIVITY_STATUSES = new Set(['pending', 'running'])
export const workspaceVerificationLabel = (status) => ({
  unverified: '尚未实际调试', pending: '等待调试', running: '正在真实调试',
  passed: '本版调试通过', incomplete: '验证未完成', failed: '本版调试失败', error: '调试异常'
})[status] || '调试状态未知'
export const workspaceVerificationTagType = (status) => ({
  unverified: 'info', pending: 'warning', running: 'warning', passed: 'success', incomplete: 'warning', failed: 'danger', error: 'danger'
})[status] || 'info'
export const assertionStateTagType = (state) => {
  if (!state) return 'info'
  return state.status === 'complete' ? 'success' : 'warning'
}
export const assertionStateLabel = (state) => {
  if (!state) return '未检查'
  if (state.status === 'complete') return '断言已补齐'
  if (Number(state.pending_count || 0) > 0) return `${state.pending_count} 项待补充`
  return '缺少有效断言'
}
export const isWorkspaceActive = (workspace) => (
  WORKSPACE_ACTIVITY_STATUSES.has(workspace?.verification?.status) ||
  WORKSPACE_ACTIVITY_STATUSES.has(workspace?.repair?.status)
)
export const isCurrentRevisionVerified = (workspace, revision) => {
  const verification = workspace?.verification || {}
  const verifiedRevision = verification.locked_revision ?? verification.revision ?? verification.verified_revision
  return verification.status === 'passed'
    && Number(verification.runtime_assertion_count || 0) > 0
    && verification.assertion_state?.status === 'complete'
    && Number(verifiedRevision) === Number(revision)
}

export const generationHasStaticBlockers = (generation) => {
  const report = generation?.quality_report || {}
  return Array.isArray(report.blockers) && report.blockers.length > 0
    ? true
    : (report.checks || []).some(item => item?.level === 'blocker')
}

export const canSaveGeneratedDraft = (generation, draft, busy = false) => (
  !busy
  && Boolean((draft?.script_draft || generation?.script_draft || '').trim())
  && !generationHasStaticBlockers(generation)
)

export const generationDraftCompletion = (generation) => {
  const artifact = generation?.exploration_snapshot?.artifact || {}
  const completion = artifact.completion || 'unknown'
  return {
    completion,
    isPartial: completion === 'partial',
    completedSteps: Array.isArray(artifact.completed_steps) ? artifact.completed_steps : [],
    remainingSteps: Array.isArray(artifact.remaining_steps) ? artifact.remaining_steps : []
  }
}

const KNOWN_GENERATION_ERROR_MESSAGES = {
  TASK_CANCELLED: '任务已取消。',
  INVALID_TARGET_URL: '目标网址无效，请填写完整的 HTTP(S) 地址。',
  NO_SCRIPT_DRAFT: '未保存可用的 Python 草稿。',
  CHECKPOINT_FAILED: '草稿保存失败，已停止本次生成；请以最后一次成功保存的版本为准。',
  EXPLORATION_EVIDENCE_INCOMPLETE: '探索证据未完整保存。',
  INPUT_AMBIGUOUS: '测试目标仍需确认。',
  EXPLORATION_WRITE_CONFIRMATION_REQUIRED: '需要确认允许的测试数据操作范围。',
  EXPLORATION_EXTRA_RISK_BLOCKED: '测试目标包含当前不允许的额外高风险操作。',
  MODEL_SERVICE_ERROR: '模型服务异常，请稍后重试。',
  MODEL_GATEWAY_TIMEOUT: '模型响应超时，请稍后重试。',
  MODEL_AUTHENTICATION_FAILED: '模型认证或权限校验失败，请检查模型配置。',
  MODEL_RATE_LIMITED: '模型服务触发限流，请稍后重试。',
  exploration_timeout: '页面探索达到总时限，已保留当前证据。',
  repeated_interaction: '相同操作在当前页面状态下重复执行，已达到纠错上限；请查看操作记录和页面结果。',
  interaction_failure: '浏览器定位或交互连续失败，已停止探索；请查看定位器和页面状态。',
  external_domain_blocked: '探索尝试访问目标站点以外的地址，已停止。',
  transient: '连接暂时中断，已保留当前草稿和探索证据。'
}

const USER_MESSAGE_LIMIT = 180
const isChineseMessage = value => /[\u3400-\u9fff]/.test(String(value || ''))
export const generationUserMessage = (value, fallback) => {
  let text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!isChineseMessage(text)) return fallback
  const chineseCount = (text.match(/[\u3400-\u9fff]/g) || []).length
  const englishCount = (text.match(/[A-Za-z]/g) || []).length
  if (englishCount > chineseCount * 2) {
    if (fallback) return fallback
    text = (text.match(/^[\u3400-\u9fff，。；：、（）()0-9\s]+/) || [''])[0].trim()
  }
  return text.length > USER_MESSAGE_LIMIT ? `${text.slice(0, USER_MESSAGE_LIMIT)}…` : text
}

/** Only backend diagnostics may become a normal warning; model final text stays technical. */
export const generationFailureReason = (generation) => {
  const snapshot = generation?.exploration_snapshot || {}
  const diagnostic = generation?.error_message || snapshot.error_message || ''
  const errorCode = generation?.error_code || snapshot.error_code || snapshot.termination_reason || ''
  const compactDiagnostic = generationUserMessage(diagnostic, '')
  if (compactDiagnostic) return compactDiagnostic
  if (KNOWN_GENERATION_ERROR_MESSAGES[errorCode]) return KNOWN_GENERATION_ERROR_MESSAGES[errorCode]
  if (diagnostic || errorCode) return generation?.status === 'failed'
    ? '本次生成失败，原始诊断请查看技术信息。'
    : '本次生成未完整结束，原始诊断请查看技术信息。'
  return ''
}

const asEvidenceObject = value => value && typeof value === 'object' && !Array.isArray(value) ? value : null

/**
 * Stop-location evidence is intentionally only read from failure_context.
 * In particular, target_url and later page states must not be used to invent
 * the page where an earlier failure happened.
 */
export const generationFailureContext = (generation) => {
  const context = asEvidenceObject(generation?.exploration_snapshot?.failure_context)
  return context && Object.keys(context).length ? context : null
}

export const failureEvidenceText = (value) => (
  value === null || value === undefined || String(value).trim() === '' ? '未采集' : String(value)
)

export const failureEvidenceBreadcrumbs = (value) => {
  if (!Array.isArray(value) || !value.length) return '未采集'
  const items = value.map(item => failureEvidenceText(item)).filter(item => item !== '未采集')
  return items.length ? items.join(' / ') : '未采集'
}

const GENERIC_FAILURE_ACTIONS = {
  goto: '打开页面', navigate: '打开页面', click: '触发页面操作', dblclick: '触发页面操作',
  fill: '输入内容', type: '输入内容', press: '按键操作', select: '选择内容',
  select_option: '选择内容', check: '切换选项状态', uncheck: '切换选项状态',
  hover: '悬停查看页面状态', wait_for: '等待页面状态', wait_for_selector: '等待页面状态',
  screenshot: '获取页面截图', evaluate: '读取页面信息'
}

/** Convert tool verbs only; do not maintain a site-specific button vocabulary. */
export const failureActionText = (value) => {
  const raw = String(value || '').trim()
  if (!raw) return '未采集'
  const normalized = raw.toLowerCase().replace(/[\s-]+/g, '_')
  if (GENERIC_FAILURE_ACTIONS[normalized]) return GENERIC_FAILURE_ACTIONS[normalized]
  return generationUserMessage(raw, '页面操作（具体动作未采集）')
}

export const generationFailureContextLocationLabel = (context) => ({
  failure_event: '停止位置',
  last_observed: '最后记录的页面（不代表失败现场）'
})[context?.location_source] || '位置来源未采集'

export const generationFailureContextReason = (context, generation = null) => {
  const generationReason = generationFailureReason(generation)
  const code = String(generation?.error_code || context?.reason_code || '')
  // A last observation is context only. Do not turn it into an interaction
  // failure when the actual terminal reason was an LLM or infrastructure error.
  if (context?.location_source === 'last_observed') return generationReason || '本次停止原因未采集。'
  if (code.startsWith('MODEL_')) return generationReason || '模型服务异常，具体原因未采集。'
  const message = generationUserMessage(context?.message, '')
  if (message) return message
  return generationReason || generationFailureReason({ status: 'failed', error_code: context?.reason_code }) || '探索已停止，具体原因未采集。'
}

/** Failed-event location must only use the evidence captured by that event. */
export const failedEventPageEvidence = (event) => {
  const page = asEvidenceObject(event?.page_context)
  if (!page) return '未采集'
  const title = failureEvidenceText(page.page_title)
  const url = failureEvidenceText(page.page_url)
  if (title === '未采集' && url === '未采集') return '未采集'
  return [title === '未采集' ? '' : title, url === '未采集' ? '' : url].filter(Boolean).join(' · ')
}

export const failedEventTargetEvidence = (event) => {
  const page = asEvidenceObject(event?.page_context)
  if (!page) return '未采集'
  const element = failureEvidenceText(page.element_label)
  const container = failureEvidenceText(page.container_label)
  if (element === '未采集' && container === '未采集') return '未采集'
  return [element === '未采集' ? '' : element, container === '未采集' ? '' : `所属：${container}`].filter(Boolean).join('；')
}

const generationPendingSummary = (generation) => {
  const completion = generationDraftCompletion(generation)
  // The response recomputes this state from the current script. Exploration
  // remaining_steps belong to the original artifact, not later human edits.
  const state = generation?.workspace?.verification?.assertion_state || generation?.quality_report?.assertion_state || {}
  const pending = Array.isArray(state.pending) ? state.pending : []
  const pendingSteps = pending.filter(item => item?.kind === 'step')
  const pendingAssertions = pending.filter(item => item?.kind === 'assertion')
  if (pendingSteps.length) return `待补充步骤：${generationUserMessage(pendingSteps[0].reason, '具体原因未以中文记录，请在技术信息查看原始内容。')}`
  if (pendingAssertions.length) return `待补充断言：${generationUserMessage(pendingAssertions[0].reason, '具体原因未以中文记录，请在技术信息查看原始内容。')}`
  if (state.status === 'incomplete' && Number(state.confirmed_count || 0) === 0) return '草稿缺少真实断言，需补充可验证结果。'
  if (state.status === 'complete') return '当前脚本未检测到待补充标记；请以本版实际调试结果为准。'
  if (completion.remainingSteps.length) return `待补充项：${generationUserMessage(completion.remainingSteps[0], '具体原因未以中文记录，请在技术信息查看原始内容。')}`
  return completion.isPartial ? '草稿未完成，尚未记录具体待补充项。' : ''
}

export const canRetryScriptFromTrace = (generation, busy = false) => {
  const snapshot = generation?.exploration_snapshot || {}
  const hasTrace = (Array.isArray(snapshot.events) && snapshot.events.length > 0)
    || (Array.isArray(snapshot.page_states) && snapshot.page_states.length > 0)
  const hasTraceOrDraft = hasTrace || Boolean(generation?.script_draft?.trim())
  return !busy
    && snapshot.schema_version === 5
    && hasTraceOrDraft
    && ['failed', 'needs_review', 'cancelled'].includes(generation?.status)
}

const GENERATION_FIELD_LABELS = {
  description: '测试描述',
  module_id: '业务模块',
  model_config_id: '本次使用模型',
  exploration_timeout_seconds: '页面探索总超时时间',
  non_field_errors: '生成配置'
}

const collectErrorDetails = (value, field = '') => {
  if (Array.isArray(value)) {
    return value.flatMap(item => collectErrorDetails(item, field))
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => collectErrorDetails(item, key))
  }
  if (value === null || value === undefined || value === '') return []
  const label = GENERATION_FIELD_LABELS[field] || field
  return [`${label ? `${label}：` : ''}${String(value)}`]
}

/** Prefer actionable field validation over Axios' generic HTTP error text. */
export const generationApiErrorMessage = (error, fallback) => {
  const body = error?.response?.data
  const details = body?.error?.details ?? body?.errors
  const detailMessages = collectErrorDetails(details)
  if (detailMessages.length) return detailMessages.slice(0, 3).join('；')
  return body?.message || body?.error?.message || error?.message || fallback
}

export const generationActionRequired = (generation) => {
  const status = generation?.status
  if (!isPausedGeneration(status)) return null
  const errorCode = generation?.error_code || ''
  const remainingAttempts = Math.max(0, 3 - Number(generation?.resume_count || 0))

  if (status === 'needs_input') {
    return {
      kind: 'description', title: '场景信息不足',
      description: generation?.error_message || '请补充完整步骤、成功标准、目标数据范围和清理约束。',
      questions: [], primaryLabel: '重新分析并继续', remainingAttempts
    }
  }
  if (status === 'needs_confirmation' && generation?.current_stage === 'preflighting' && errorCode !== 'EXPLORATION_EXTRA_RISK_BLOCKED') {
    return {
      kind: 'target_scope', title: '需要确认本次目标范围',
      description: '平台会在一个浏览器会话中连续探索完整场景，不依赖固定按钮文案。仅允许操作本轮测试数据并记录清理风险；页面元素、DOM 和定位器不需要你填写。',
      questions: [], primaryLabel: '确认目标范围并继续', remainingAttempts
    }
  }
  if (errorCode === 'EXPLORATION_EVIDENCE_INCOMPLETE') {
    return {
      kind: 'exploration_issue', title: '页面探索未完成',
      description: generation?.error_message || '当前页面轨迹不足，不能要求你填写 DOM 或定位器。请查看探索轨迹并修订业务目标后重新发起。',
      questions: [], primaryLabel: '', remainingAttempts
    }
  }
  return {
    kind: 'description', title: '需要调整探索约束',
    description: generation?.error_message || '请修订描述，明确目标范围、允许的操作和清理约束。',
    questions: [], primaryLabel: '修订后继续', remainingAttempts
  }
}

/** A WebSocket event can only wake polling when it explicitly identifies this generation. */
export const matchesGenerationWebSocketEvent = (message, generation) => {
  if (!generation) return false
  const result = message?.result || {}
  const messageGenerationId = result.generation_id || message?.generation_id
  const messageTaskId = message?.task_id || result.task_id
  const generationMatches = Boolean(messageGenerationId && generation.id && String(messageGenerationId) === String(generation.id))
  const taskMatches = Boolean(messageTaskId && generation.celery_task_id && String(messageTaskId) === String(generation.celery_task_id))
  return generationMatches || taskMatches
}

export const buildGenerationTimeline = (generation) => {
  const currentStage = generation?.current_stage || 'created'
  const stageIndex = {
    created: 0, normalizing: 0, preflighting: 0,
    exploring: 1, generating: 1,
    validating: 2, repairing: 2,
    completed: 3
  }
  const currentIndex = stageIndex[currentStage] ?? 0
  const terminal = isTerminalGeneration(generation?.status)
  const draftReady = ['ready', 'ready_with_warnings'].includes(generation?.status)

  return GENERATION_STAGES.map(([stage, label], index) => {
    let state = 'wait'
    if (draftReady) {
      state = 'success'
    } else if (terminal) {
      if (index < currentIndex) state = 'success'
      else if (index === currentIndex) state = ['failed', 'needs_review'].includes(generation.status) ? 'error' : 'wait'
    } else if (index === currentIndex) {
      state = 'process'
    } else if (index < currentIndex) {
      state = 'success'
    }
    return { stage, label, state }
  })
}

export const generationResolutionHint = (generation, { draftDirty = false } = {}) => {
  const status = generation?.status
  if (generation?.lifecycle?.state === 'interrupted') {
    return draftDirty
      ? '探索已中断，草稿和轨迹已保留。请先保存本地草稿，再补充现场说明并继续；也可查看历史记录。'
      : '探索已中断，草稿和轨迹已保留。可查看历史记录，或补充现场说明并继续；将启动新的浏览器，智能体会按说明继续探索，请先核对已有数据。'
  }
  if (status === 'needs_input') return '请补充明确的测试目标、操作步骤和至少一个可验证结果后重新分析。页面元素和平台默认清理策略不需要填写。'
  if (status === 'needs_confirmation') return '请确认本次测试目标范围。平台会在一个连续会话中自行探索页面元素；额外高风险操作仍需单独调整目标。'
  if (status === 'needs_review') {
    const reason = generationFailureReason(generation)
    const pending = draftDirty ? '本地草稿有修改，保存后会重新检查待补充步骤和断言。' : generationPendingSummary(generation)
    return `${reason ? `${reason} ` : ''}${pending ? `${pending} ` : ''}已保留草稿和探索证据，可继续编辑，或仅基于现有轨迹整理脚本。`
  }
  if (status === 'failed' || status === 'cancelled') {
    const message = generationFailureReason(generation) || (status === 'failed' ? '本次生成失败，详情请查看技术信息。' : '本次生成已取消。')
    const completion = generationDraftCompletion(generation)
    return `${message}${completion.isPartial && !draftDirty ? ` ${generationPendingSummary(generation)} 未完成草稿仍可编辑，也可仅基于已保存证据整理脚本。` : draftDirty ? ' 本地草稿有修改，保存后会重新检查。' : ''}`
  }
  if (status === 'ready_with_warnings') return '脚本可保存，但建议先查看定位器和探索轨迹警告。'
  return ''
}
