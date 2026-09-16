import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import {
  buildPerformanceTargetPayload,
  isPerformancePlatformAdmin,
  performanceNetworkModeLabel,
  performanceNodeStatusLabel,
  performancePlanPermissions,
  samePerformanceScope,
} from '../src/views/perf-testing/performanceWorkspaceState.js'
import {
  canRegenerateInstallation,
  installationArchitectureText,
  installationCommandExpired,
  performanceInstallationStage,
} from '../src/utils/performanceInstallation.js'
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

test('performance API keeps management responses in response.data and installation commands explicit', async () => {
  const source = await read('../src/api/performance.js')
  assert.match(source, /const get = async .*\.data/)
  assert.match(source, /createPerformanceNode[\s\S]*response\?\.data \?\? response/)
  assert.match(source, /resetPerformanceNodeEnrollment[\s\S]*response\?\.data \?\? response/)
  assert.match(source, /getPerformanceNodeInstallation[\s\S]*nodes\/\$\{id\}\/installation/)
  assert.match(source, /performanceErrorMessage/)
})

test('workspace creates one-node execution requests and explains virtual-user startup wording', async () => {
  const source = await read('../src/views/perf-testing/PerfWorkspace.vue')
  assert.match(source, /在线 Agent 不等于 Worker 就绪/)
  assert.match(source, /启动速率表示每秒启动的虚拟用户数，不代表每秒请求数/)
  assert.doesNotMatch(source, /爬升 RPS|\}\} RPS|每秒请求速率/)
  assert.match(source, /节点安装向导/)
  assert.match(source, /getPerformanceNodeInstallation/)
  assert.match(source, /copyText\(command\)/)
  assert.match(source, /v-if="canManageNodes" label="操作"/)
  assert.match(source, /installationRequestNonce/)
  assert.match(source, /installationClock\.value = Date\.now\(\)/)
  assert.match(source, /installationAvailable && canRegenerateInstallation/)
  assert.match(source, /安装命令已过期，请重新生成/)
  assert.match(source, /需要 Linux 主机、Docker 和 root 权限/)
  assert.match(source, /重新生成会立即使旧注册凭证和旧长期身份失效/)
  assert.match(source, /不要默认重装或重置身份/)
  assert.match(source, /window\.setInterval\(\(\) => \{ installationClock\.value = Date\.now\(\); return Promise\.all\(\[loadConfig\(requestProjectId, requestEpoch\), loadList\('nodes'/)
  assert.match(source, /Promise\.all\(\[loadAccess\(requestProjectId, requestEpoch\), loadConfig\(requestProjectId, requestEpoch\), loadList\('targets', requestProjectId, requestEpoch\), loadList\('plans', requestProjectId, requestEpoch\), loadList\('nodes', requestProjectId, requestEpoch\)\]\)/)
  assert.match(source, /await ElMessageBox\.confirm\([\s\S]*?if \(!scopeIsCurrent\(scope\) \|\| !installationDialog\.visible \|\| installationRequestNonce !== dialogNonce\) return[\s\S]*?resetPerformanceNodeEnrollment/)
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

test('installation presentation follows actual node state without retaining a command', () => {
  assert.deepEqual(performanceInstallationStage({ status: 'pending' }, { command: 'docker compose up' }), { key: 'installing', text: '请在节点终端执行下方命令，等待节点注册并发送心跳。' })
  assert.deepEqual(performanceInstallationStage({ status: 'pending', registered_at: '2026-09-16T00:00:00Z' }, { command: 'docker compose up' }), { key: 'registered', text: '节点已注册，等待首次心跳。' })
  assert.deepEqual(performanceInstallationStage({ status: 'offline', registered_at: '2026-09-16T00:00:00Z' }, null), { key: 'offline', text: '节点曾注册但当前离线，请先检查 Docker 容器、网络和平台地址。' })
  assert.deepEqual(performanceInstallationStage({ status: 'online' }, null), { key: 'online', text: '节点已在线，可保留此页查看安装条件。' })
  assert.deepEqual(performanceInstallationStage({ status: 'revoked' }, null), { key: 'revoked', text: '节点已吊销，不能安装或重新注册；如需恢复，请新建节点。' })
  assert.equal(canRegenerateInstallation({ status: 'pending' }, { command: null }), true)
  assert.equal(canRegenerateInstallation({ status: 'pending', registered_at: '2026-09-16T00:00:00Z' }, { command: null }), false)
  assert.equal(canRegenerateInstallation({ status: 'offline', registered_at: 'yes' }, { command: null }), false)
  assert.equal(installationCommandExpired({ expires_at: '2026-09-16T00:00:00Z' }, Date.parse('2026-09-16T00:00:01Z')), true)
  assert.equal(canRegenerateInstallation({ status: 'pending' }, { command: 'old command' }, true), true)
  assert.equal(installationArchitectureText(['amd64', 'arm64']), 'x86_64（amd64）、ARM64（arm64）')
  assert.equal(installationArchitectureText([]), '未配置或未发布')
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
