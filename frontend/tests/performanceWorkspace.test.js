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
import { performanceErrorMessage } from '../src/api/performanceError.js'

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('performance workspace exposes only plan, node and target management routes', async () => {
  const [router, layout, tabs] = await Promise.all([
    read('../src/router/index.js'), read('../src/layouts/MainLayout.vue'), read('../src/stores/tabs.js')
  ])
  for (const path of ['/perf-testing/plans', '/perf-testing/nodes', '/perf-testing/targets']) {
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

test('workspace has no execution control and uses virtual-user startup wording', async () => {
  const source = await read('../src/views/perf-testing/PerfWorkspace.vue')
  assert.match(source, /当前没有压测执行能力/)
  assert.match(source, /在线 Agent 不等于 Worker 就绪/)
  assert.match(source, /启动速率表示每秒启动的虚拟用户数，不代表每秒请求数/)
  assert.doesNotMatch(source, /爬升 RPS|\}\} RPS|每秒请求速率/)
  assert.match(source, /clearEnrollmentToken\(\).*tokenDialog\.token = ''/s)
  assert.doesNotMatch(source, /localStorage|router\.push\([^\n]*token|executePerformance|开始压测/)
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
