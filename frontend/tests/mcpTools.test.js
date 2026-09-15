import test from 'node:test'
import assert from 'node:assert/strict'
import {
  getMCPRequestError,
  getMCPToolsError,
  getMCPToolsRefreshNotAppliedMessage,
  getMCPToolsStatusPresentation,
  isMCPConfigurationBusy,
  isMCPToolsRefreshSuccessful,
  normalizeMCPConfiguration,
  unwrapMCPConfigurationResponse,
} from '../src/utils/mcpTools.js'

test('未检测、空工具成功和检测失败使用不同的工具状态文案', () => {
  assert.deepEqual(getMCPToolsStatusPresentation({ tools_status: 'unchecked' }), {
    type: 'info', text: '尚未检测',
  })
  assert.deepEqual(getMCPToolsStatusPresentation({ tools_status: 'ready', tools_count: 2 }), {
    type: 'success', text: '已发现 2 个工具',
  })
  assert.deepEqual(getMCPToolsStatusPresentation({ tools_status: 'ready', tools_count: 0 }), {
    type: 'success', text: '已发现 0 个工具',
  })
  assert.deepEqual(getMCPToolsStatusPresentation({ tools_status: 'error' }), {
    type: 'danger', text: '检测失败',
  })
})

test('未知工具状态回退为尚未检测，缺失数量从工具列表推导', () => {
  const configuration = normalizeMCPConfiguration({
    tools_status: 'unexpected',
    tools: [{ name: 'search' }, { name: 'read' }],
  })

  assert.equal(configuration.tools_status, 'unchecked')
  assert.equal(configuration.tools_count, 2)
})

test('仅 ready 可作为刷新完成结果，行级操作在任一请求中互斥', () => {
  assert.equal(isMCPToolsRefreshSuccessful('ready'), true)
  assert.equal(isMCPToolsRefreshSuccessful('unchecked'), false)
  assert.equal(isMCPToolsRefreshSuccessful('error'), false)
  assert.equal(isMCPToolsRefreshSuccessful('unexpected'), false)
  assert.equal(isMCPConfigurationBusy({}), false)
  assert.equal(isMCPConfigurationBusy({ statusLoading: true }), true)
  assert.equal(isMCPConfigurationBusy({ toolsRefreshing: true }), true)
})

test('并发刷新结果未应用时优先显示响应消息，并阻止成功提示', () => {
  assert.equal(getMCPToolsRefreshNotAppliedMessage({
    tools_refresh_applied: false,
    message: '配置已被其他请求更新',
  }), '配置已被其他请求更新')
  assert.equal(getMCPToolsRefreshNotAppliedMessage({ tools_refresh_applied: false }), '配置已变化，本次检测结果未采用')
  assert.equal(getMCPToolsRefreshNotAppliedMessage({ tools_refresh_applied: true, message: '忽略' }), '')
  assert.equal(getMCPToolsRefreshNotAppliedMessage({ tools_status: 'ready' }), '')
})

test('刷新失败响应没有 tools 字段时可保留调用方已有的工具清单', () => {
  const existing = normalizeMCPConfiguration({
    tools_status: 'ready',
    tools_count: 1,
    tools: [{ name: 'cached_tool' }],
  })
  const refreshResult = {
    tools_status: 'error',
    tools_error: 'MCP server timed out',
  }
  const merged = normalizeMCPConfiguration({
    ...existing,
    ...refreshResult,
    tools: Array.isArray(refreshResult.tools) ? refreshResult.tools : existing.tools,
  })

  assert.equal(merged.tools_status, 'error')
  assert.equal(merged.tools_error, 'MCP server timed out')
  assert.deepEqual(merged.tools, [{ name: 'cached_tool' }])
  assert.equal(merged.tools_count, 1)
})

test('错误原因优先使用当前本地失败原因，避免旧后端错误掩盖本次请求错误', () => {
  assert.equal(getMCPToolsError({ tools_error: '后端超时', local_tools_error: '网络中断' }), '网络中断')
  assert.equal(getMCPToolsError({ local_tools_error: '网络中断' }), '网络中断')
})

test('仅接受 success:true 信封或具备配置字段的直接对象响应', () => {
  const data = { id: 3, is_active: false }
  assert.deepEqual(unwrapMCPConfigurationResponse({ data: { success: true, data } }), data)
  assert.deepEqual(unwrapMCPConfigurationResponse({ data }), data)
  assert.equal(unwrapMCPConfigurationResponse({ data: { success: false, data } }), null)
  assert.equal(unwrapMCPConfigurationResponse({ data: { success: true, data: {} } }), null)
  assert.equal(unwrapMCPConfigurationResponse({ data: { message: '没有配置字段' } }), null)
  assert.equal(unwrapMCPConfigurationResponse({ data: [] }), null)
})

test('请求错误支持嵌套 error.message，且不会将错误对象渲染为字符串', () => {
  assert.equal(getMCPRequestError({
    response: { data: { error: { message: 'stdio MCP 已断开' } } },
  }), 'stdio MCP 已断开')
  assert.equal(getMCPRequestError({
    response: { data: { error: { code: 'timeout' } } },
    message: { code: 'network' },
  }, '检测请求失败'), '检测请求失败')
})
