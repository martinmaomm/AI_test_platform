export const PERFORMANCE_ACTIVE_STATUSES = new Set(['queued', 'preparing', 'running', 'stopping'])
export const PERFORMANCE_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'incomplete'])

const RUN_STATUS_LABELS = {
  queued: '排队中', preparing: '准备中', running: '执行中', stopping: '停止中',
  completed: '已完成', failed: '执行失败', cancelled: '已取消', incomplete: '执行不完整',
}

export const performanceRunStatusLabel = (status) => RUN_STATUS_LABELS[status] || status || '-'
export const isPerformanceRunActive = (status) => PERFORMANCE_ACTIVE_STATUSES.has(status)
export const canStopPerformanceRun = (status) => ['queued', 'preparing', 'running'].includes(status)

export const performanceExecutionPermissions = (user, member) => {
  const isAdmin = user?.is_staff === true || user?.is_superuser === true
  return {
    canExecute: isAdmin || member?.can_execute_tests === true,
    canReport: isAdmin || member?.can_view_reports === true,
  }
}

export const createPerformanceRequestId = (cryptoApi = globalThis.crypto) => {
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()
  if (typeof cryptoApi?.getRandomValues !== 'function') throw new Error('当前浏览器不支持安全随机数，无法创建执行请求')
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export const executionUnavailableMessage = (config = {}) => (
  config.execution_unavailable_reason || '性能执行控制器未启动，暂不能创建运行。'
)

export const completedWithFailures = (run) => (
  run?.status === 'completed' && Number(run?.latest_metrics?.failures || 0) > 0
)

export const runSummaryText = (run) => completedWithFailures(run)
  ? '执行完成，存在失败请求'
  : performanceRunStatusLabel(run?.status)

export const metricEntries = (metrics = {}) => Array.isArray(metrics.entries) ? metrics.entries : []
export const metricSamples = (samples) => Array.isArray(samples) ? samples.slice(-400) : []
export const sampleMetrics = (sample = {}) => sample?.metrics && typeof sample.metrics === 'object' ? sample.metrics : {}
export const formatErrorRate = (value) => {
  const rate = Number(value)
  return Number.isFinite(rate) ? `${(Math.max(0, Math.min(1, rate)) * 100).toFixed(2)}%` : '-'
}
export const formatMetric = (value) => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(2) : '-'
}

export const samePerformanceRunScope = (captured, current) => (
  captured.scopeEpoch === current.scopeEpoch
  && String(captured.projectId) === String(current.projectId)
  && String(captured.runId) === String(current.runId)
)
