/** Pure, presentation-only helpers for the V2 WebUI generation page. */

export const GENERATION_STAGES = [
  ['normalizing', '理解测试场景'],
  ['preflighting', '检查风险与登录条件'],
  ['exploring', '探索页面'],
  ['generating', '生成 Python 脚本'],
  ['validating', '检查脚本质量'],
  ['repairing', '自动修复脚本'],
  ['completed', '整理生成结果']
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
  preflighting: '正在检查风险与登录条件',
  exploring: '正在只读探索页面',
  generating: '正在生成 Python 脚本',
  validating: '正在检查脚本质量',
  repairing: '正在自动修复脚本',
  needs_input: '需要补充场景信息',
  needs_confirmation: '需要确认探索风险',
  needs_credentials: '需要本次探索登录信息',
  needs_review: '需要人工检查',
  ready: '脚本已生成',
  ready_with_warnings: '脚本已生成（有警告）',
  failed: '生成失败',
  cancelled: '已取消'
}

export const generationStatusLabel = (status) => STATUS_LABELS[status] || '状态未知'

export const generationStorageKey = (userId, projectId) => (
  `aits:webui-script-generation:v2:${String(userId || 'anonymous')}:${String(projectId || 'none')}`
)

export const isActiveGeneration = (status) => ACTIVE_GENERATION_STATUSES.has(status)
export const isPausedGeneration = (status) => PAUSED_GENERATION_STATUSES.has(status)
export const isTerminalGeneration = (status) => TERMINAL_GENERATION_STATUSES.has(status)

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
  const currentIndex = GENERATION_STAGES.findIndex(([stage]) => stage === currentStage)
  const terminal = isTerminalGeneration(generation?.status)
  const repairTriggered = Number(generation?.repair_count || 0) > 0 || currentStage === 'repairing'

  return GENERATION_STAGES.map(([stage, label], index) => {
    let state = 'wait'
    let displayLabel = label
    if (stage === 'repairing' && !repairTriggered) {
      displayLabel = `${label}（未触发）`
    } else if (stage === 'completed' && terminal) {
      state = generation.status === 'cancelled'
        ? 'wait'
        : ['failed', 'needs_review'].includes(generation.status) ? 'error' : 'success'
    } else if (stage === currentStage) {
      state = generation.status === 'failed' ? 'error' : 'process'
    } else if (index < currentIndex && (stage !== 'repairing' || repairTriggered)) {
      state = 'success'
    }
    return { stage, label: displayLabel, state }
  })
}

export const generationResolutionHint = (generation) => {
  const status = generation?.status
  if (status === 'needs_input') return '请补充可验证的操作步骤、成功标准和清理要求后重新发起。'
  if (status === 'needs_confirmation') return '场景要求探索阶段执行写操作或存在高风险约束，请调整描述后重新发起。'
  if (status === 'needs_credentials') return '请在“本次探索登录信息”中填写临时账号和密码后重新发起。'
  if (status === 'needs_review') return '脚本和证据已保留，请根据阻断项或未确认项人工调整后再创建新的生成任务。'
  if (status === 'failed') return generation?.error_message || '请检查模型、Playwright MCP、登录信息或页面可访问性后重试。'
  if (status === 'cancelled') return '本次生成已停止；可以修改输入后重新发起。'
  if (status === 'ready_with_warnings') return '脚本可保存，但建议先查看定位器和探索证据警告。'
  return ''
}
