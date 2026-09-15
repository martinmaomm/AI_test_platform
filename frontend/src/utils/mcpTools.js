export const MCP_TOOLS_STATUS = Object.freeze({
  UNCHECKED: 'unchecked',
  READY: 'ready',
  ERROR: 'error',
})

export function normalizeToolsStatus(status) {
  return Object.values(MCP_TOOLS_STATUS).includes(status)
    ? status
    : MCP_TOOLS_STATUS.UNCHECKED
}

export function isMCPToolsRefreshSuccessful(status) {
  return normalizeToolsStatus(status) === MCP_TOOLS_STATUS.READY
}

export function getMCPToolsRefreshNotAppliedMessage(result = {}) {
  if (result.tools_refresh_applied !== false) return ''
  return typeof result.message === 'string' && result.message.trim()
    ? result.message
    : '配置已变化，本次检测结果未采用'
}

export function isMCPConfigurationBusy(configuration = {}) {
  return Boolean(configuration.statusLoading || configuration.toolsRefreshing)
}

export function normalizeMCPConfiguration(configuration = {}) {
  const tools = Array.isArray(configuration.tools) ? configuration.tools : []
  const toolsCount = Number.isFinite(configuration.tools_count)
    ? configuration.tools_count
    : tools.length

  return {
    ...configuration,
    tools,
    tools_count: toolsCount,
    tools_status: normalizeToolsStatus(configuration.tools_status),
    tools_checked_at: configuration.tools_checked_at || null,
    tools_error: configuration.tools_error || '',
  }
}

export function getMCPToolsStatusPresentation(configuration = {}) {
  const status = normalizeToolsStatus(configuration.tools_status)
  const toolsCount = Number.isFinite(configuration.tools_count)
    ? configuration.tools_count
    : 0

  if (status === MCP_TOOLS_STATUS.READY) {
    return { type: 'success', text: `已发现 ${toolsCount} 个工具` }
  }
  if (status === MCP_TOOLS_STATUS.ERROR) {
    return { type: 'danger', text: '检测失败' }
  }
  return { type: 'info', text: '尚未检测' }
}

export function getMCPToolsError(configuration = {}) {
  return configuration.local_tools_error || configuration.tools_error || ''
}

const MCP_CONFIGURATION_FIELDS = ['id', 'name', 'rawConfig', 'is_active', 'tools_count', 'tools_status', 'tools_checked_at', 'tools_error', 'tools']

function hasMCPConfigurationFields(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && MCP_CONFIGURATION_FIELDS.some(field => Object.prototype.hasOwnProperty.call(value, field))
}

export function unwrapMCPConfigurationResponse(response) {
  const payload = response?.data
  if (typeof payload?.success === 'boolean') {
    if (payload.success !== true || !hasMCPConfigurationFields(payload.data)) {
      return null
    }
    return payload.data
  }

  if (hasMCPConfigurationFields(payload)) {
    return payload
  }
  return null
}

export function getMCPRequestError(error, fallback = '请求失败') {
  const responseData = error?.response?.data
  const candidates = [
    responseData?.message,
    responseData?.error?.message,
    responseData?.error,
    error?.message,
  ]
  return candidates.find(message => typeof message === 'string' && message.trim()) || fallback
}
