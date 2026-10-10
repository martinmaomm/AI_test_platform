import test from 'node:test'
import assert from 'node:assert/strict'
import { buildPlanPayload, planForm, newEndpoint, importEndpoints, createSecurityScope, securityPermissions, newSubmission, isActiveTask, canExportTask, securityError, createRequestId, loadSecurityWorkspaceData, shouldRenewSubmission } from '../src/views/security-testing/securityState.js'

function form() {
  const value = planForm()
  value.name = '资源安全检查'
  value.originsText = 'https://api.example.test'
  value.endpoints[0] = newEndpoint({ name: '订单', url: 'https://api.example.test/orders/1?language=zh-CN', headers: { 'Accept-Language': 'zh-CN' } })
  return value
}

test('save round-trips URL query, language headers and explicit auth ownership declarations', () => {
  const value = form()
  Object.assign(value.endpoints[0], { auth_required: true, owner_only_a: true, proofPath: 'body.data.id', proofExpected: '1' })
  const payload = buildPlanPayload(value)
  assert.equal(payload.config.endpoints[0].url, 'https://api.example.test/orders/1?language=zh-CN')
  assert.deepEqual(payload.config.endpoints[0].headers, { 'Accept-Language': 'zh-CN' })
  assert.equal(payload.config.endpoints[0].owner_only_a, true)
  assert.deepEqual(payload.config.endpoints[0].proof, { path: 'body.data.id', expected: 1 })
})

test('editing an existing plan never reconstructs secret values and omits preserved identities', () => {
  const existing = buildPlanPayload(form())
  const value = planForm({ ...existing, identities: { a: { name: '账号 A', configured: true } } })
  assert.equal(value.identityMode, 'keep')
  assert.equal(value.aHeaders, '')
  assert.equal('identities' in buildPlanPayload(value), false)
  value.identityMode = 'clear'
  assert.deepEqual(buildPlanPayload(value).identities, {})
})

test('replacing accounts is explicit and keeps credentials out of ordinary endpoint headers', () => {
  const value = form()
  value.aHeaders = '{"Authorization":"Bearer synthetic-test-token"}'
  value.bHeaders = '{"Cookie":"sid=synthetic-other-account"}'
  const payload = buildPlanPayload(value)
  assert.deepEqual(Object.keys(payload.identities), ['a', 'b'])
  assert.equal(payload.config.endpoints[0].headers.Authorization, undefined)
  value.endpoints[0].headersText = '{"authorization":"must-use-account"}'
  assert.throws(() => buildPlanPayload(value), /账号配置/)
})

test('empty identity object is not silently treated as a configured account', () => {
  const value = form(); value.aHeaders = '{}'
  assert.throws(() => buildPlanPayload(value), /至少需要一个非空请求头/)
})

test('origin comparison respects protocol and port and does not infer extra scope from endpoint URLs', () => {
  const value = form()
  for (const url of ['https://evil.example.test/orders/1', 'http://api.example.test/orders/1', 'https://api.example.test:9443/orders/1', 'https://u:p@api.example.test/orders/1', 'https://api.example.test/orders/{id}', 'https://api.example.test/orders/1#secret']) {
    value.endpoints[0].url = url
    assert.throws(() => buildPlanPayload(value), /允许来源/)
  }
})

test('origins permit only origins and normalize default ports without discarding query parameters', () => {
  const value = form(); value.originsText = 'https://api.example.test:443/'
  assert.deepEqual(buildPlanPayload(value).config.allowed_origins, ['https://api.example.test'])
  value.originsText = 'https://api.example.test/orders'
  assert.throws(() => buildPlanPayload(value), /协议、域名和端口/)
})

test('resource evidence preserves false and zero but rejects empty null and container values', () => {
  const value = form(); value.endpoints[0].proofPath = 'body.items[0].id'
  for (const expected of ['false', '0', '"item-1"']) {
    value.endpoints[0].proofExpected = expected
    assert.equal(buildPlanPayload(value).config.endpoints[0].proof.expected, JSON.parse(expected))
  }
  for (const expected of ['null', '""', '{}', '[]', '1e999']) {
    value.endpoints[0].proofExpected = expected
    assert.throws(() => buildPlanPayload(value), /预期值/)
  }
})

test('resource evidence requires both fields, a supported selector and a GET response body', () => {
  const value = form(); value.endpoints[0].proofPath = 'body.id'
  assert.throws(() => buildPlanPayload(value), /同时填写/)
  value.endpoints[0].proofExpected = '1'; value.endpoints[0].proofPath = '$.id'
  assert.throws(() => buildPlanPayload(value), /路径格式无效/)
  value.endpoints[0].proofPath = 'body.id'; value.endpoints[0].method = 'HEAD'
  assert.throws(() => buildPlanPayload(value), /HEAD/)
})

test('import uses only explicit selections and refuses to silently truncate to twenty', () => {
  const source = Array.from({ length: 25 }, (_, i) => ({ name: `接口${i}`, url: `https://api.example.test/${i}`, headers: {} }))
  const chosen = [source[4], source[22]]
  const imported = importEndpoints([newEndpoint()], chosen)
  assert.equal(imported.length, 2)
  assert.equal(imported[1].url, source[22].url)
  assert.throws(() => importEndpoints(imported, source.slice(0, 19)), /最多 20/)
  assert.throws(() => importEndpoints(imported, []), /勾选/)
  assert.equal(imported.length, 2)
})

test('numeric limits reject disabled budgets and nonfinite rates before submission', () => {
  for (const [key, value] of [['max_requests', 0], ['max_requests', 101], ['max_requests', 2.5], ['timeout_seconds', 31], ['rate_per_second', Infinity], ['rate_per_second', 0.09]]) {
    const input = form(); input[key] = value
    assert.throws(() => buildPlanPayload(input), /范围/)
  }
})

test('route and project scope invalidates late results, including returning to the same project', () => {
  const scope = createSecurityScope()
  const first = scope.reset(1, '/security-testing/plans')
  assert.equal(scope.current(first), true)
  scope.reset(2, '/security-testing/plans'); assert.equal(scope.current(first), false)
  scope.reset(1, '/security-testing/plans'); assert.equal(scope.current(first), false)
  const next = scope.capture(); scope.reset(1, '/security-testing/tasks'); assert.equal(scope.current(next), false)
  const last = scope.capture(); scope.dispose(); assert.equal(scope.current(last), false)
})

test('unknown submission outcome retries with the same request id but changed plan or case selection gets a fresh id', () => {
  const first = newSubmission(null, 'plan1:revision1:execution', { case_ids: ['case-1'] })
  assert.equal(newSubmission(first, 'plan1:revision1:execution', { case_ids: ['case-1'] }).requestId, first.requestId)
  assert.notEqual(newSubmission(first, 'plan1:revision1:execution', { case_ids: ['case-2'] }).requestId, first.requestId)
  assert.notEqual(newSubmission(first, 'plan1:revision2:execution', { case_ids: ['case-1'] }).requestId, first.requestId)
  assert.match(createRequestId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})

test('permissions are separate for plan editing, execution and reports', () => {
  const user = { username: 'reader' }
  const project = { members: [{ username: 'reader', can_edit: false, can_execute_tests: false, can_view_reports: true }] }
  assert.deepEqual(securityPermissions(user, project), { read: true, edit: false, execute: false, report: true })
  assert.deepEqual(securityPermissions({ username: 'other' }, project), { read: false, edit: false, execute: false, report: false })
  assert.deepEqual(securityPermissions({ is_staff: true }, null), { read: true, edit: true, execute: true, report: true })
})

test('only terminal execution tasks export reports and both active statuses can be stopped', () => {
  assert.equal(canExportTask({ kind: 'planning', status: 'completed' }), false)
  assert.equal(canExportTask({ kind: 'execution', status: 'running' }), false)
  assert.equal(canExportTask({ kind: 'execution', status: 'failed' }), true)
  assert.equal(isActiveTask({ status: 'queued' }), true)
  assert.equal(isActiveTask({ status: 'cancelled' }), false)
})

test('DRF field errors and structured API failures remain actionable', () => {
  assert.equal(securityError({ response: { data: { error: { code: 'plan_changed', message: '计划已修改，请重新生成' } } } }), '计划已修改，请重新生成')
  assert.match(securityError({ response: { data: { config: ['目标不在允许来源'] } } }), /config：目标不在允许来源/)
})

test('completed planning is followed by a fresh plan read before polling can stop', async () => {
  let backendCases = []; let release
  const pendingTasks = new Promise((resolve) => { release = () => { backendCases = [{ id: 'case-1' }]; resolve({ data: { items: [{ status: 'completed' }] } }) } })
  const reads = []
  const resultPromise = loadSecurityWorkspaceData(() => pendingTasks, async () => { reads.push('plans'); return { data: { items: [{ cases: [...backendCases] }] } } })
  assert.deepEqual(reads, [])
  release()
  const result = await resultPromise
  assert.equal(result.tasks[0].status, 'completed')
  assert.deepEqual(result.plans[0].cases, [{ id: 'case-1' }])
})

test('scope invalidation prevents the dependent plan read for an old project', async () => {
  let current = true; let release; let reads = 0
  const tasks = new Promise((resolve) => { release = resolve })
  const result = loadSecurityWorkspaceData(() => tasks, async () => { reads++; return [] }, () => current)
  current = false; release([])
  assert.equal(await result, null)
  assert.equal(reads, 0)
})

test('queue dispatch failure is known safe to resubmit while an unknown timeout keeps its request id', () => {
  assert.equal(shouldRenewSubmission({ response: { status: 503, data: { error: { code: 'queue_unavailable' } } } }), true)
  assert.equal(shouldRenewSubmission({ response: { status: 503, data: {} } }), false)
  assert.equal(shouldRenewSubmission(new Error('timeout')), false)
})
