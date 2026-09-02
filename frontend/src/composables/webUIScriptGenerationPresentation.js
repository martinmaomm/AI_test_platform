/** Pure, presentation-only helpers for the V4 continuous-exploration page. */

export const GENERATION_STAGES = [
  ['normalizing', '理解测试目标'],
  ['exploring', '连续探索页面'],
  ['planning', '整理可回放路径'],
  ['generating', '生成 Python'],
  ['validating', '检查脚本'],
  ['completed', '完成']
]

export const ACTIVE_GENERATION_STATUSES = new Set([
  'created', 'normalizing', 'preflighting', 'exploring', 'generating', 'validating', 'repairing'
])

export const PAUSED_GENERATION_STATUSES = new Set([
  'needs_input', 'needs_confirmation', 'needs_credentials'
])

export const TERMINAL_GENERATION_STATUSES = new Set([
  'ready', 'ready_with_warnings', 'needs_review', 'failed', 'cancelled'
])

const STATUS_LABELS = {
  created: '等待任务启动',
  normalizing: '正在理解测试场景',
  preflighting: '正在确认目标范围与登录条件',
  exploring: '正在连续探索页面',
  generating: '正在整理回放路径并生成 Python',
  validating: '正在检查脚本',
  repairing: '正在自动修复脚本',
  needs_input: '需要补充场景信息',
  needs_confirmation: '需要确认目标范围',
  needs_credentials: '需要本轮测试登录信息',
  needs_review: '需要人工检查',
  ready: '脚本已生成',
  ready_with_warnings: '脚本已生成（有警告）',
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
  `aits:webui-script-generation:v4:${String(userId || 'anonymous')}:${String(projectId || 'none')}`
)

export const isActiveGeneration = (status) => ACTIVE_GENERATION_STATUSES.has(status)
export const isPausedGeneration = (status) => PAUSED_GENERATION_STATUSES.has(status)
export const isTerminalGeneration = (status) => TERMINAL_GENERATION_STATUSES.has(status)

const WORKSPACE_ACTIVITY_STATUSES = new Set(['pending', 'running'])
export const workspaceVerificationLabel = (status) => ({
  unverified: '尚未实际调试', pending: '等待调试', running: '正在真实调试',
  passed: '本版调试通过', failed: '本版调试失败', error: '调试异常'
})[status] || '调试状态未知'
export const workspaceVerificationTagType = (status) => ({
  unverified: 'info', pending: 'warning', running: 'warning', passed: 'success', failed: 'danger', error: 'danger'
})[status] || 'info'
export const isWorkspaceActive = (workspace) => (
  WORKSPACE_ACTIVITY_STATUSES.has(workspace?.verification?.status) ||
  WORKSPACE_ACTIVITY_STATUSES.has(workspace?.repair?.status)
)
export const isCurrentRevisionVerified = (workspace, revision, environmentId = undefined) => {
  const verification = workspace?.verification || {}
  const verifiedRevision = verification.locked_revision ?? verification.revision ?? verification.verified_revision
  return verification.status === 'passed' && Number(verifiedRevision) === Number(revision) && (
    environmentId === undefined || Number(verification.environment_id) === Number(environmentId)
  )
}

const GENERATION_FIELD_LABELS = {
  description: '测试描述',
  environment_id: 'WebUI 测试环境',
  module_id: '业务模块',
  start_path: '起始相对路径',
  model_config_id: '本次使用模型',
  temporary_credentials: '本次探索登录信息',
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

const CLEANUP_STATUS_PRESENTATION = {
  not_required: { label: '无需清理', type: 'info' },
  completed: { label: '清理已验证', type: 'success' },
  attempted: { label: '已尝试，未验证', type: 'warning' },
  missing: { label: '缺少清理证据', type: 'danger' },
  unknown: { label: '清理结果未知', type: 'warning' }
}

/** Keep cleanup evidence conservative: an absent v4 record is not a success result. */
export const explorationCleanupPresentation = (snapshot) => {
  const report = snapshot?.cleanup
  if (!report || typeof report !== 'object' || Array.isArray(report)) {
    return { hasRecord: false, status: '', label: '尚无清理记录', type: 'info', attempted: false, residuals: [], reason: '' }
  }
  const status = CLEANUP_STATUS_PRESENTATION[report.status] ? report.status : 'unknown'
  const presentation = CLEANUP_STATUS_PRESENTATION[status]
  return {
    hasRecord: true,
    status,
    label: presentation.label,
    type: presentation.type,
    attempted: Boolean(report.attempted),
    residuals: Array.isArray(report.residuals) ? report.residuals.filter(Boolean).map(String) : [],
    reason: String(report.reason || '')
  }
}

export const generationActionRequired = (generation) => {
  const status = generation?.status
  if (!isPausedGeneration(status)) return null
  const errorCode = generation?.error_code || ''
  const remainingAttempts = Math.max(0, 3 - Number(generation?.resume_count || 0))

  if (status === 'needs_credentials') {
    return {
      kind: 'credentials', title: '需要本轮测试登录信息',
      description: generation?.error_message || '登录信息缺失或已过期，请重新提供后继续。',
      questions: [], primaryLabel: '提交登录信息并继续', remainingAttempts
    }
  }
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
    created: 0, normalizing: 0, preflighting: 0, exploring: 1,
    planning: 2, replay_planning: 2, generating: 3,
    validating: 4, repairing: 4, completed: 5
  }
  const currentIndex = stageIndex[currentStage] ?? 0
  const terminal = isTerminalGeneration(generation?.status)

  return GENERATION_STAGES.map(([stage, label], index) => {
    let state = 'wait'
    if (stage === 'completed' && terminal) {
      state = generation.status === 'cancelled'
        ? 'wait'
        : ['failed', 'needs_review'].includes(generation.status) ? 'error' : 'success'
    } else if (index === currentIndex) {
      state = generation.status === 'failed' ? 'error' : 'process'
    } else if (index < currentIndex) {
      state = 'success'
    }
    return { stage, label, state }
  })
}

export const generationResolutionHint = (generation) => {
  const status = generation?.status
  if (status === 'needs_input') return '请补充明确的测试目标、操作步骤和至少一个可验证结果后重新分析。页面元素和平台默认清理策略不需要填写。'
  if (status === 'needs_confirmation') return '请确认本次测试目标范围。平台会在一个连续会话中自行探索页面元素；额外高风险操作仍需单独调整目标。'
  if (status === 'needs_credentials') return '请在“本轮测试登录信息”中填写临时账号和密码后重新发起。'
  if (status === 'needs_review') {
    const incomplete = generation?.error_code === 'EXPLORATION_EVIDENCE_INCOMPLETE'
    return `${generation?.error_message ? `${generation.error_message} ` : ''}${incomplete ? '这不是系统失败：连续探索证据不完整，但草稿和页面证据已保留。' : '本次结果需要人工处理。'} 请确认清理失败或残留数据后再决定是否新建任务。`
  }
  if (status === 'failed' || status === 'cancelled') {
    const message = status === 'failed'
      ? generation?.error_message || '请检查模型、Playwright MCP、登录信息或页面可访问性后重试。'
      : '本次生成已停止。'
    const cleanupStatus = generation?.exploration_snapshot?.cleanup?.status
    return ['unknown', 'attempted', 'missing'].includes(cleanupStatus)
      ? `${message} 重新发起前，请先检查“探索轨迹”中的本轮数据和清理结果，避免重复操作。`
      : message
  }
  if (status === 'ready_with_warnings') return '脚本可保存，但建议先查看定位器和探索轨迹警告。'
  return ''
}
