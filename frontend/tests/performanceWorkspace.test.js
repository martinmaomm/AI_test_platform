import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import {
  buildPerformanceTargetPayload,
  copyEnrollmentTokenToClipboard,
  isPerformancePlatformAdmin,
  performanceNetworkModeLabel,
  performanceNodeStatusLabel,
  performancePlanPermissions,
  samePerformanceScope,
} from '../src/views/perf-testing/performanceWorkspaceState.js'
import {
  canStopPerformanceRun,
  completedWithFailures,
  createPerformanceRequestId,
  formatErrorRate,
  formatMetric,
  metricEntries,
  metricSamples,
  performanceExecutionPermissions,
  performanceRunStatusLabel,
  samePerformanceRunScope,
  sampleMetrics,
} from '../src/views/perf-testing/performanceExecutionState.js'
import { performanceErrorMessage } from '../src/api/performanceError.js'

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('performance workspace exposes plan, run, node and target routes', async () => {
  const [router, layout, tabs] = await Promise.all([
    read('../src/router/index.js'), read('../src/layouts/MainLayout.vue'), read('../src/stores/tabs.js')
  ])
  for (const path of ['/perf-testing/plans', '/perf-testing/runs', '/perf-testing/nodes', '/perf-testing/targets']) {
    assert.ok(`${router}${layout}${tabs}`.includes(path), `missing ${path}`)
  }
  assert.doesNotMatch(router, /PerfScheduledTasks|PerfEnvironments|PerfNotificationReceivers|PerfWorkspacePlaceholder/)
  assert.doesNotMatch(layout, /perf-testing\/(scheduled-tasks|environments|notification-receivers)/)
  assert.equal(existsSync(new URL('../src/views/perf-testing/PerfWorkspacePlaceholder.vue', import.meta.url)), false)
})

test('performance API keeps management responses in response.data and enrollment tokens explicit', async () => {
  const source = await read('../src/api/performance.js')
  assert.match(source, /const get = async .*\.data/)
  assert.match(source, /createPerformanceNode[\s\S]*response\?\.data \?\? response/)
  assert.match(source, /resetPerformanceNodeEnrollment[\s\S]*response\?\.data \?\? response/)
  assert.match(source, /performanceErrorMessage/)
})

test('workspace creates one-node execution requests and explains virtual-user startup wording', async () => {
  const source = await read('../src/views/perf-testing/PerfWorkspace.vue')
  assert.match(source, /在线 Agent 不等于 Worker 就绪/)
  assert.match(source, /启动速率表示每秒启动的虚拟用户数，不代表每秒请求数/)
  assert.doesNotMatch(source, /爬升 RPS|\}\} RPS|每秒请求速率/)
  assert.match(source, /clearEnrollmentToken\(\).*tokenDialog\.token = ''/s)
  assert.doesNotMatch(source, /localStorage|router\.push\([^\n]*token/)
  assert.match(source, /createPerformanceRun\(scope\.projectId, plan\.id, \{ node_id: nodeId, request_id: requestId \}\)/)
  assert.match(source, /execution_unavailable_reason/)
  assert.match(source, /每次仅运行一个节点/)
  assert.match(source, /保存计划不会自动执行，需在计划列表确认后创建运行/)
  assert.match(source, /正在运行的压测会被请求停止，尾统计可能不完整/)
  assert.doesNotMatch(source, /本批仅保存计划，不会执行|本批没有运行任务需要终止/)
  assert.match(source, /requestEpoch === epoch && String\(projectId\.value\) === String\(requestProjectId\)/)
  assert.match(source, /clearInterval\(pollTimer\)/)
  assert.match(source, /Math\.max\(5000, Math\.min\(10000/)
  assert.match(source, /projectStore\.currentProject\?\.project_type !== 'perf'/)
  assert.match(source, /if \(saving\.plan\) return/)
  assert.match(source, /if \(saving\.node\) return/)
  assert.match(source, /Headers 必须是 JSON 对象/)
  assert.match(source, /Body 必须是合法 JSON/)
})

test('only the backend platform-admin definition can manage nodes and targets', () => {
  assert.equal(isPerformancePlatformAdmin({ is_staff: true }), true)
  assert.equal(isPerformancePlatformAdmin({ is_superuser: true }), true)
  assert.equal(isPerformancePlatformAdmin({ role: 'admin', is_staff: false, is_superuser: false }), false)
  assert.equal(isPerformancePlatformAdmin({ owner_username: 'owner' }), false)

  const memberAdmin = { role: 'admin', can_edit: true, can_delete: true }
  assert.deepEqual(performancePlanPermissions({ role: 'user' }, memberAdmin), { canEdit: true, canDelete: true })
  assert.deepEqual(performancePlanPermissions({ role: 'user' }, { can_edit: true, can_delete: false }), { canEdit: true, canDelete: false })
})

test('target mutation payload strips read-only fields and scope guards reject switched projects', () => {
  assert.deepEqual(buildPerformanceTargetPayload({
    id: 7, name: ' target ', base_url: ' https://example.test/ ', allowed_methods: ['GET'], created_at: 'old', updated_at: 'old'
  }), { name: 'target', base_url: 'https://example.test/', allowed_methods: ['GET'] })
  assert.equal(samePerformanceScope({ projectId: 1, epoch: 4 }, { projectId: '1', epoch: 4 }), true)
  assert.equal(samePerformanceScope({ projectId: 1, epoch: 4 }, { projectId: 2, epoch: 4 }), false)
  assert.equal(samePerformanceScope({ projectId: 1, epoch: 4 }, { projectId: 1, epoch: 5 }), false)
})

test('clipboard failure is observable so the token dialog can stay open for manual copying', async () => {
  const copied = []
  await copyEnrollmentTokenToClipboard('one-time-token', async (value) => copied.push(value))
  assert.deepEqual(copied, ['one-time-token'])
  await assert.rejects(copyEnrollmentTokenToClipboard('one-time-token', async () => { throw new Error('blocked') }))
})

test('performance API errors prefer a nested field-specific validation detail', () => {
  assert.equal(performanceErrorMessage({ response: { data: {
    message: '数据验证失败', error: { details: { steps: [{ path: ['必须以单个 / 开头'] }] } }
  } } }), 'steps.path: 必须以单个 / 开头')
  assert.equal(performanceErrorMessage({ response: { data: { error: { details: { name: ['不能为空'] } } } } }), 'name: 不能为空')
  assert.equal(performanceErrorMessage({ response: { data: { message: '普通错误' } } }), '普通错误')
})

test('node statuses and network modes are Chinese display labels without changing backend values', () => {
  assert.equal(performanceNodeStatusLabel('pending'), '待注册')
  assert.equal(performanceNodeStatusLabel('online'), '在线')
  assert.equal(performanceNodeStatusLabel('offline'), '离线')
  assert.equal(performanceNodeStatusLabel('revoked'), '已吊销')
  assert.equal(performanceNetworkModeLabel('lan'), '内网')
  assert.equal(performanceNetworkModeLabel('public'), '公网')
  assert.equal(performanceNodeStatusLabel('future-value'), 'future-value')
})

test('execution permissions remain project-capability based and status text stays Chinese', () => {
  assert.deepEqual(performanceExecutionPermissions({ role: 'admin' }, { can_execute_tests: false, can_view_reports: false }), { canExecute: false, canReport: false })
  assert.deepEqual(performanceExecutionPermissions({ is_staff: true }, { can_execute_tests: false, can_view_reports: false }), { canExecute: true, canReport: true })
  assert.deepEqual(performanceExecutionPermissions({ role: 'user' }, { can_execute_tests: true, can_view_reports: false }), { canExecute: true, canReport: false })
  assert.equal(performanceRunStatusLabel('running'), '执行中')
  assert.equal(performanceRunStatusLabel('incomplete'), '执行不完整')
  assert.equal(canStopPerformanceRun('running'), true)
  assert.equal(canStopPerformanceRun('stopping'), false)
})

test('execution metrics use the fixed nested sample contract and preserve limits', () => {
  const samples = Array.from({ length: 401 }, (_, index) => ({ timestamp: `t${index}`, metrics: { rps: index, failures: index % 2, p95: index + 1 } }))
  assert.equal(metricSamples(samples).length, 400)
  assert.deepEqual(sampleMetrics(metricSamples(samples)[0]), { rps: 1, failures: 1, p95: 2 })
  assert.deepEqual(metricEntries({ entries: [{ name: 'GET /health', method: 'GET', requests: 2, failures: 0, avg_response_time: 3, p95: 4, p99: 5 }] }), [{ name: 'GET /health', method: 'GET', requests: 2, failures: 0, avg_response_time: 3, p95: 4, p99: 5 }])
  assert.equal(formatErrorRate(0.125), '12.50%')
  assert.equal(completedWithFailures({ status: 'completed', latest_metrics: { failures: 1 } }), true)
})

test('request id is generated once by the caller and can be reused after a retry', () => {
  let calls = 0
  const id = createPerformanceRequestId({ randomUUID: () => { calls += 1; return '123e4567-e89b-12d3-a456-426614174000' } })
  assert.equal(id, '123e4567-e89b-12d3-a456-426614174000')
  assert.equal(calls, 1)
})

test('request id uses cryptographic getRandomValues UUIDv4 fallback without Math.random', () => {
  const id = createPerformanceRequestId({
    getRandomValues: (bytes) => { for (let index = 0; index < bytes.length; index += 1) bytes[index] = index; return bytes }
  })
  assert.match(id, /^00010203-0405-4607-8809-0a0b0c0d0e0f$/)
  assert.throws(() => createPerformanceRequestId({}), /安全随机数/)
})

test('run screens poll only active runs, use nested samples, and do not expose private commands', async () => {
  const [list, detail] = await Promise.all([
    read('../src/views/perf-testing/PerfRunList.vue'), read('../src/views/perf-testing/PerfRunDetail.vue')
  ])
  assert.match(list, /getPerformanceRuns/)
  assert.match(list, /v-if="canReport"[\s\S]*PerfRunDetail/)
  assert.match(detail, /isPerformanceRunActive/)
  assert.match(detail, /window\.setTimeout\(loadRun, delay\)/)
  assert.match(detail, /sampleMetrics\(item\)\.rps/)
  assert.match(detail, /await stopPerformanceRun\(scope\.projectId, scope\.runId\)/)
  assert.match(detail, /pollFailures\.value <= 3/)
  assert.match(detail, /yAxisIndex: 2/)
  assert.doesNotMatch(`${list}${detail}`, /node_command|handshake_token|cert_pem|key_pem/)
})

test('execution presentation rounds measurements and rejects stale stop scopes', async () => {
  const workspace = await read('../src/views/perf-testing/PerfWorkspace.vue')
  assert.equal(formatMetric(12.345), '12.35')
  assert.equal(formatMetric('bad'), '-')
  assert.equal(samePerformanceRunScope({ projectId: 1, runId: 'a', scopeEpoch: 2 }, { projectId: '1', runId: 'a', scopeEpoch: 2 }), true)
  assert.equal(samePerformanceRunScope({ projectId: 1, runId: 'a', scopeEpoch: 2 }, { projectId: 1, runId: 'b', scopeEpoch: 2 }), false)
  assert.match(workspace, /@change="resetRunRequestId"/)
  assert.match(workspace, /Promise\.all\(\[loadConfig\(requestProjectId, requestEpoch\), loadList\('nodes'/)
})

test('run detail separates route scope from polling requests so a delayed confirmation can still stop', async () => {
  const detail = await read('../src/views/perf-testing/PerfRunDetail.vue')
  assert.match(detail, /let scopeEpoch = 0; let requestEpoch = 0/)
  assert.match(detail, /const scope = currentScope\(\); const currentRequestEpoch = \+\+requestEpoch/)
  assert.match(detail, /scopeEpoch \+= 1; stopPolling\(\);[\s\S]*stopping\.value = false/)
  assert.match(detail, /if \(scopeIsCurrent\(scope\)\) stopping\.value = false/)
})
