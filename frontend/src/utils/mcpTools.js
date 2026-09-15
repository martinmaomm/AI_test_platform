export const MCP_TOOLS_STATUS = Object.freeze({
  UNCHECKED: 'unchecked',
  READY: 'ready',
  ERROR: 'error',
})

export const MCP_SINGLETON_PAGE_STATE = Object.freeze({
  LOADING: 'loading',
  FAILED: 'failed',
  EMPTY: 'empty',
  READY: 'ready',
  CONFLICT: 'conflict',
})

export const DEFAULT_PLAYWRIGHT_MCP_RAW_CONFIG = JSON.stringify({
  mcpServers: {
    playwright: {
      command: 'npx',
      args: ['-y', '@executeautomation/playwright-mcp-server@1.0.12'],
    },
  },
}, null, 2)

export function getMCPSingletonPageState({ loading = false, loadError = '', configurations } = {}) {
  if (loading) return MCP_SINGLETON_PAGE_STATE.LOADING
  if (loadError || !Array.isArray(configurations)) return MCP_SINGLETON_PAGE_STATE.FAILED
  if (configurations.length === 0) return MCP_SINGLETON_PAGE_STATE.EMPTY
  if (configurations.length === 1) return MCP_SINGLETON_PAGE_STATE.READY
  return MCP_SINGLETON_PAGE_STATE.CONFLICT
}

export function validatePlaywrightMCPRawConfig(rawConfig) {
  let config
  try {
    config = JSON.parse(rawConfig)
  } catch {
    return { valid: false, message: 'JSON格式不正确' }
  }
  const servers = config?.mcpServers
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
    return { valid: false, message: '请提供包含mcpServers对象的配置' }
  }
  const names = Object.keys(servers)
  if (names.length !== 1 || names[0] !== 'playwright') {
    return { valid: false, message: '全局MCP配置只能包含一个 playwright 服务器' }
  }
  if (
    !servers.playwright
    || typeof servers.playwright !== 'object'
    || Array.isArray(servers.playwright)
    || typeof servers.playwright.command !== 'string'
    || !servers.playwright.command.trim()
  ) {
    return { valid: false, message: 'playwright服务器必须包含command字段' }
  }
  return { valid: true }
}

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
