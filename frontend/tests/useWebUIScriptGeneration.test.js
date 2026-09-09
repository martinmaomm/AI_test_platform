import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { effectScope, nextTick, ref } from 'vue'

// Load the real composable with a tiny import seam, without adding a test-only
// dependency or contacting a real API. Every test gets isolated API/window state.
const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let moduleId = 0
const methods = [
  'applyWebUIScriptGenerationRepair',
  'cancelWebUIScriptGeneration', 'createWebUIScriptGeneration', 'debugWebUIScriptGeneration',
  'discardWebUIScriptGenerationRepair',
  'getWebUIScriptGeneration', 'getWebUITestCaseExecution', 'repairWebUIScriptGeneration',
  'resolveWebUIScriptGeneration', 'retryWebUIScriptGenerationFromTrace', 'saveWebUIScriptGeneration', 'updateWebUIScriptGenerationDraft'
]
const record = (workspace = {}) => ({
  id: 'test-generation', status: 'ready', target_url: 'https://example.test/', script_draft: 'async def run(page):\n    pass',
  workspace: { revision: 0, variables: [], verification: { status: 'unverified' }, repair: { status: 'idle' }, ...workspace }
})
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function harness(t, initial = record(), options = {}) {
  const storage = new Map()
  if (options.storedGenerationId) {
    storage.set('automation:webui-script-generation:v5:1:1', options.storedGenerationId)
  }
  const timers = new Map()
  const oldWindow = globalThis.window
  let timerId = 0
  globalThis.window = {
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    setInterval: fn => { timers.set(++timerId, fn); return timerId },
    clearInterval: id => timers.delete(id)
  }
  const calls = []
  const handlers = {
    createWebUIScriptGeneration: async () => ({ success: true, data: structuredClone(initial) }),
    getWebUIScriptGeneration: async () => ({ success: true, data: structuredClone(initial) }),
    getWebUITestCaseExecution: async (_project, id) => ({ success: true, data: { id } })
  }
  const api = Object.fromEntries(methods.map(name => [name, async (...args) => {
    calls.push({ name, args })
    if (!handlers[name]) throw new Error(`Unexpected mock API call: ${name}`)
    return handlers[name](...args)
  }]))
  const token = `__automationWorkspaceTest${++moduleId}`
  globalThis[token] = api
  const apiModule = dataModule(`const api = globalThis[${JSON.stringify(token)}];\n${methods.map(name => `export const ${name} = (...args) => api.${name}(...args);`).join('\n')}`)
  const vueModule = dataModule(`export {computed,ref,unref,watch} from ${JSON.stringify(import.meta.resolve('vue'))}; export const onUnmounted = () => {};`)
  const original = await readFile(new URL('../src/composables/useWebUIScriptGeneration.js', import.meta.url), 'utf8')
  const source = original.replace("from 'vue'", `from '${vueModule}'`)
    .replace("from '@/api/webTesting'", `from '${apiModule}'`)
    .replace("from './webUIScriptGenerationPresentation'", `from '${new URL('../src/composables/webUIScriptGenerationPresentation.js', import.meta.url).href}'`)
  const { useWebUIScriptGeneration } = await import(dataModule(source))
  delete globalThis[token]
  const scope = effectScope()
  const projectId = ref(1)
  const userId = ref(1)
  const state = scope.run(() => useWebUIScriptGeneration({ projectId, userId }))
  t.after(() => { state.stopPolling(); scope.stop(); globalThis.window = oldWindow })
  if (options.create !== false) await state.create({ description: 'offline test' })
  return { state, projectId, userId, calls, handlers, storage, timers }
}

test('restore reads only the scoped v5 generation id from localStorage', async t => {
  const { state, calls, storage } = await harness(t, record(), {
    storedGenerationId: 'test-generation', create: false
  })
  await state.restore()
  const reads = calls.filter(call => call.name === 'getWebUIScriptGeneration')
  assert.equal(reads.at(-1).args[1], 'test-generation')
  assert.equal(state.generation.value.id, 'test-generation')
  assert.deepEqual([...storage.keys()], ['automation:webui-script-generation:v5:1:1'])
})

test('polling preserves unsaved local code when a server revision changes', async t => {
  const { state, handlers, storage } = await harness(t)
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'local unfinished code' })
  handlers.getWebUIScriptGeneration = async () => ({ data: { ...record({ revision: 1 }), script_draft: 'remote code' } })
  await state.refresh()
  assert.equal(state.localDraft.value.script_draft, 'local unfinished code')
  assert.equal(state.localDraft.value.revision, 0)
  assert.equal(state.hasUnsavedDraft.value, true)
  assert.deepEqual([...storage.values()], ['test-generation'])
})

test('same-revision incremental script text syncs when clean and never overwrites dirty code', async t => {
  const { state, handlers } = await harness(t)
  handlers.getWebUIScriptGeneration = async () => ({ data: { ...record(), script_draft: 'server checkpoint one' } })
  await state.refresh()
  assert.equal(state.localDraft.value.script_draft, 'server checkpoint one')
  assert.equal(state.localDraft.value.dirty, false)

  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'local user edit' })
  handlers.getWebUIScriptGeneration = async () => ({ data: { ...record(), script_draft: 'server checkpoint two', tool_stats: { total_tool_calls: 2 } } })
  await state.refresh()
  assert.equal(state.generation.value.script_draft, 'server checkpoint two')
  assert.equal(state.localDraft.value.script_draft, 'local user edit')
  assert.equal(state.localDraft.value.dirty, true)
})

test('active incremental records update evidence without overwriting a dirty local draft', async t => {
  const { state, handlers } = await harness(t, { ...record(), status: 'exploring', current_stage: 'exploring' })
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'local editable partial script' })
  handlers.getWebUIScriptGeneration = async () => ({ data: {
    ...record({ revision: 3 }), status: 'generating', current_stage: 'generating', script_draft: 'server incremental script',
    tool_stats: { total_tool_calls: 8 }, exploration_snapshot: { schema_version: 5, events: [{ event_id: 'evt-1' }], artifact: { revision: 3, completion: 'partial' } }
  } })
  await state.refresh()
  assert.equal(state.generation.value.script_draft, 'server incremental script')
  assert.equal(state.generation.value.workspace.revision, 3)
  assert.equal(state.generation.value.tool_stats.total_tool_calls, 8)
  assert.equal(state.localDraft.value.script_draft, 'local editable partial script')
  assert.equal(state.localDraft.value.revision, 0)
  assert.equal(state.hasUnsavedDraft.value, true)
})

test('workspace debug keeps polling after generation is terminal, then stops', async t => {
  const { state, timers, handlers } = await harness(t, record({ verification: { status: 'running' } }))
  assert.equal(state.isTerminal.value, true)
  assert.equal(timers.size, 1)
  handlers.getWebUIScriptGeneration = async () => ({ data: record({ verification: { status: 'passed', locked_revision: 0 } }) })
  await state.refresh()
  assert.equal(timers.size, 0)
})

test('an obsolete response cannot reappear after a project switch', async t => {
  const { state, projectId, handlers } = await harness(t)
  const request = deferred()
  handlers.getWebUIScriptGeneration = () => request.promise
  const pending = state.refresh()
  projectId.value = 2
  await nextTick()
  request.resolve({ data: record() })
  await pending
  assert.equal(state.generation.value, null)
  assert.equal(state.localDraft.value, null)
})

test('409 on saving preserves the editable draft and exposes conflict', async t => {
  const { state, handlers } = await harness(t)
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'user edits must survive' })
  handlers.updateWebUIScriptGenerationDraft = async () => { throw { response: { status: 409, data: { message: 'stale' } } } }
  await assert.rejects(state.saveDraft())
  assert.equal(state.draftConflict.value, true)
  assert.equal(state.localDraft.value.script_draft, 'user edits must survive')
})

test('no execution is started until the explicit debug action', async t => {
  const { state, handlers, calls } = await harness(t)
  assert.equal(calls.some(call => call.name === 'debugWebUIScriptGeneration'), false)
  handlers.debugWebUIScriptGeneration = async () => ({ data: record({ verification: { status: 'pending' } }) })
  await state.debug([{ name: 'TEST_LABEL', value: 'fixture-only' }])
  const debugCall = calls.find(call => call.name === 'debugWebUIScriptGeneration')
  assert.equal(debugCall.args[2].confirm_execution, true)
  assert.equal(debugCall.args[2].expected_revision, 0)
  assert.equal(state.isWorkspaceBusy.value, true)
})

test('debug saves unified variable defaults first and sends only one-time overrides', async t => {
  const { state, handlers, calls } = await harness(t)
  const variables = [{ name: 'TEST_LABEL', value: 'saved-default', description: '测试名称', required: false, is_secret: false }]
  state.updateLocalDraft({ ...state.localDraft.value, variables })
  handlers.updateWebUIScriptGenerationDraft = async (_project, _id, payload) => ({
    data: { ...record({ revision: 1, variables: payload.variables }), script_draft: payload.script_draft }
  })
  handlers.debugWebUIScriptGeneration = async () => ({ data: record({ revision: 1, variables, verification: { status: 'pending' } }) })

  await state.debug([{ name: 'TEST_LABEL', value: 'only-this-debug' }])

  const writes = calls.filter(call => ['updateWebUIScriptGenerationDraft', 'debugWebUIScriptGeneration'].includes(call.name))
  assert.deepEqual(writes.map(call => call.name), ['updateWebUIScriptGenerationDraft', 'debugWebUIScriptGeneration'])
  assert.equal(writes[0].args[2].variables[0].value, 'saved-default')
  assert.deepEqual(writes[1].args[2].runtime_variables, [{ name: 'TEST_LABEL', value: 'only-this-debug', is_secret: false }])
  assert.equal(writes[1].args[2].expected_revision, 1)
  assert.equal(state.localDraft.value.variables[0].value, 'saved-default')
})

test('AI repair only starts from a clean failed revision with diagnostics and forwards merged one-time variables', async t => {
  const variables = [{ name: 'UI_TEST_PASSWORD', value: '', description: '测试密码', required: true, is_secret: true }]
  const failed = record({
    revision: 3,
    variables,
    verification: { status: 'failed', locked_revision: 3, diagnostics: [{ code: 'ASSERTION_FAILURE', message: '登录后页面未出现' }] }
  })
  const { state, handlers, calls, storage } = await harness(t, failed)
  assert.equal(state.canStartRepair.value, true)
  handlers.repairWebUIScriptGeneration = async () => ({ data: record({
    revision: 3,
    variables,
    verification: failed.workspace.verification,
    repair: { status: 'pending', phase: 'collecting', attempt_count: 0 }
  }) })

  await state.repair([{ name: 'UI_TEST_PASSWORD', value: 'fixture-only-password' }])

  const repairCall = calls.find(call => call.name === 'repairWebUIScriptGeneration')
  assert.deepEqual(repairCall.args, [1, 'test-generation', {
    expected_revision: 3,
    confirm_execution: true,
    runtime_variables: [{ name: 'UI_TEST_PASSWORD', value: 'fixture-only-password', is_secret: true }]
  }])
  assert.equal(state.workspace.value.repair.status, 'pending')
  assert.equal(state.isWorkspaceBusy.value, true)
  assert.equal(state.canStartRepair.value, false)
  assert.deepEqual([...storage.values()], ['test-generation'])
  assert.doesNotMatch([...storage.values()].join(','), /fixture-only-password/)

  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'local edit must block repair' })
  assert.equal(state.hasUnsavedDraft.value, true)
  assert.equal(state.canStartRepair.value, false)
})

test('an active generation cannot start repair even if a stale failure diagnostic is present', async t => {
  const activeFailure = {
    ...record({ verification: { status: 'failed', diagnostics: [{ code: 'ASSERTION_FAILURE', message: 'stale diagnostic' }] } }),
    status: 'generating'
  }
  const { state, calls } = await harness(t, activeFailure)
  assert.equal(state.isActive.value, true)
  assert.equal(state.canStartRepair.value, false)
  assert.equal(await state.repair([]), null)
  assert.equal(calls.some(call => call.name === 'repairWebUIScriptGeneration'), false)
})

test('applying a repair candidate uses its hash and synchronizes the returned revision only after success', async t => {
  const candidate = 'async def run(page):\n    await page.goto("https://example.test/fixed")'
  const source = record({
    revision: 3,
    verification: { status: 'failed', diagnostics: [{ code: 'ASSERTION_FAILURE', message: 'failed' }] },
    repair: { status: 'candidate_passed', candidate_hash: 'sha256-candidate', candidate_script: candidate, attempt_count: 1 }
  })
  const { state, handlers, calls } = await harness(t, source)
  assert.equal(state.localDraft.value.script_draft, source.script_draft)
  assert.equal(state.hasRepairCandidate.value, true)
  assert.equal(await state.save('候选未采用前不得保存'), null)
  assert.equal(await state.debug([]), null)
  assert.equal(calls.some(call => ['saveWebUIScriptGeneration', 'debugWebUIScriptGeneration'].includes(call.name)), false)
  handlers.applyWebUIScriptGenerationRepair = async () => ({ data: {
    ...record({ revision: 4, repair: { status: 'idle' }, verification: { status: 'passed', locked_revision: 4 } }),
    script_draft: candidate
  } })

  await state.applyRepairCandidate('sha256-candidate')

  const applyCall = calls.find(call => call.name === 'applyWebUIScriptGenerationRepair')
  assert.deepEqual(applyCall.args, [1, 'test-generation', { expected_revision: 3, candidate_hash: 'sha256-candidate' }])
  assert.equal(state.workspace.value.revision, 4)
  assert.equal(state.localDraft.value.revision, 4)
  assert.equal(state.localDraft.value.script_draft, candidate)
  assert.equal(state.localDraft.value.dirty, false)
  assert.equal(state.hasRepairCandidate.value, false)
})

test('a candidate without script cannot be applied, and a 409 keeps the original local draft intact', async t => {
  const originalScript = 'async def run(page):\n    await page.goto("https://example.test/original")'
  const noScriptCandidate = { ...record({ revision: 3, repair: { status: 'candidate_ready', candidate_hash: 'sha256-missing' } }), script_draft: originalScript }
  const first = await harness(t, noScriptCandidate)
  assert.equal(first.state.canApplyRepairCandidate.value, false)
  await first.state.refresh()
  assert.equal(first.state.localDraft.value.script_draft, originalScript)
  assert.equal(await first.state.applyRepairCandidate('sha256-missing'), null)
  assert.equal(first.calls.some(call => call.name === 'applyWebUIScriptGenerationRepair'), false)

  const candidate = { ...record({
    revision: 3,
    repair: { status: 'candidate_ready', candidate_hash: 'sha256-candidate', candidate_script: 'candidate script' }
  }), script_draft: originalScript }
  const second = await harness(t, candidate)
  second.handlers.applyWebUIScriptGenerationRepair = async () => {
    throw { response: { status: 409, data: { data: { ...candidate, script_draft: 'must never replace local draft' } } } }
  }
  await assert.rejects(second.state.applyRepairCandidate('sha256-candidate'))
  assert.equal(second.state.draftConflict.value, true)
  assert.equal(second.state.localDraft.value.script_draft, originalScript)
})

test('discarding a candidate preserves the original script and revision, while a 409 preserves the local draft', async t => {
  const originalScript = 'async def run(page):\n    await page.goto("https://example.test/original")'
  const source = {
    ...record({
      revision: 3,
      verification: { status: 'failed', diagnostics: [{ code: 'ASSERTION_FAILURE', message: '原脚本失败' }] },
      repair: { status: 'candidate_ready', candidate_hash: 'sha256-candidate', candidate_script: 'candidate script' }
    }),
    script_draft: originalScript
  }
  const { state, handlers, calls } = await harness(t, source)
  handlers.discardWebUIScriptGenerationRepair = async () => ({ data: {
    ...record({ revision: 3, verification: source.workspace.verification, repair: { status: 'idle' } }),
    script_draft: originalScript
  } })

  await state.discardRepairCandidate('sha256-candidate')

  const discardCall = calls.find(call => call.name === 'discardWebUIScriptGenerationRepair')
  assert.deepEqual(discardCall.args, [1, 'test-generation', { expected_revision: 3, candidate_hash: 'sha256-candidate' }])
  assert.equal(state.localDraft.value.script_draft, originalScript)
  assert.equal(state.localDraft.value.revision, 3)
  assert.equal(state.hasRepairCandidate.value, false)
  assert.equal(state.canStartRepair.value, true)

  const conflict = await harness(t, source)
  conflict.handlers.discardWebUIScriptGenerationRepair = async () => {
    throw { response: { status: 409, data: { data: { ...source, script_draft: 'must not replace original draft' } } } }
  }
  await assert.rejects(conflict.state.discardRepairCandidate('sha256-candidate'))
  assert.equal(conflict.state.draftConflict.value, true)
  assert.equal(conflict.state.localDraft.value.script_draft, originalScript)
  assert.equal(conflict.state.localDraft.value.revision, 3)
})

test('repair execution details default to the latest attempt and can switch between both rounds', async t => {
  const withAttempt = record({
    repair: {
      status: 'candidate_passed',
      attempt_count: 2,
      attempts: [
        { execution_id: 31, execution_status: 'failed', failure_summary: '首轮定位器失败' },
        { execution_id: 32, execution_status: 'passed' }
      ]
    }
  })
  const { state, handlers, calls } = await harness(t, withAttempt)
  const details = {
    31: { id: 91, execution: 31, project_id: 1, status: 'failed', log: 'first candidate run log', screenshot_path: 'candidate-first.png' },
    32: { id: 92, execution: 32, project_id: 1, status: 'passed', log: 'second candidate run log', screenshot_path: 'candidate-second.png' }
  }
  handlers.getWebUIScriptGeneration = async () => ({ success: true, data: withAttempt })
  handlers.getWebUITestCaseExecution = async (_projectId, executionId) => ({ success: true, data: details[executionId] })

  await state.refresh()
  await new Promise(resolve => setImmediate(resolve))

  assert.equal(state.selectedRepairExecutionId.value, 32)
  assert.deepEqual(state.repairExecution.value, details[32])
  assert.deepEqual(calls.filter(call => call.name === 'getWebUITestCaseExecution').at(-1).args, [1, 32])

  await state.loadRepairExecution(31)
  assert.equal(state.selectedRepairExecutionId.value, 31)
  assert.deepEqual(state.repairExecution.value, details[31])
  assert.deepEqual(calls.filter(call => call.name === 'getWebUITestCaseExecution').at(-1).args, [1, 31])

  await state.loadRepairExecution('32')
  assert.equal(state.selectedRepairExecutionId.value, 32)
  assert.deepEqual(state.repairExecution.value, details[32])
})

test('a late first-round response cannot replace the second round selected by the user', async t => {
  const withAttempts = record({
    repair: {
      status: 'candidate_passed',
      attempts: [
        { execution_id: 31, execution_status: 'failed' },
        { execution_id: 32, execution_status: 'passed' }
      ]
    }
  })
  const { state, handlers } = await harness(t, withAttempts)
  const delayedFirstRound = deferred()
  const secondRoundDetail = { id: 92, execution: 32, project_id: 1, status: 'passed', screenshot_path: 'second-round.png' }
  handlers.getWebUITestCaseExecution = async (_projectId, executionId) => (
    executionId === 31 ? delayedFirstRound.promise : { success: true, data: secondRoundDetail }
  )

  const firstRequest = state.loadRepairExecution(31)
  await state.loadRepairExecution(32)
  delayedFirstRound.resolve({ success: true, data: { id: 91, execution: 31, project_id: 1, status: 'failed', screenshot_path: 'late-first-round.png' } })
  await firstRequest

  assert.equal(state.selectedRepairExecutionId.value, 32)
  assert.deepEqual(state.repairExecution.value, secondRoundDetail)
  assert.equal(state.repairExecutionLoading.value, false)
})

test('repair execution loading rejects ids outside current attempts and late responses from another generation', async t => {
  const oldGeneration = record({
    repair: { status: 'candidate_ready', attempts: [{ execution_id: 31, execution_status: 'failed' }] }
  })
  const { state, handlers, calls } = await harness(t, oldGeneration)
  const oldResponse = deferred()
  let executionRequestCount = 0
  handlers.getWebUITestCaseExecution = async () => {
    executionRequestCount += 1
    if (executionRequestCount === 1) return oldResponse.promise
    return { success: true, data: { id: 193, execution: 31, project_id: 1, status: 'passed', screenshot_path: 'new-generation.png' } }
  }

  const invalidResult = await state.loadRepairExecution(999)
  assert.equal(invalidResult, null)
  assert.equal(calls.some(call => call.name === 'getWebUITestCaseExecution'), false)

  const pendingOldRequest = state.loadRepairExecution(31)
  handlers.createWebUIScriptGeneration = async () => ({ success: true, data: {
    ...oldGeneration,
    id: 'next-generation',
    workspace: { ...oldGeneration.workspace, repair: { status: 'candidate_passed', attempts: [{ execution_id: 31, execution_status: 'passed' }] } }
  } })
  await state.create({ description: 'next task' })
  oldResponse.resolve({ success: true, data: { id: 93, execution: 31, project_id: 1, status: 'failed', screenshot_path: 'stale-generation.png' } })
  await pendingOldRequest

  assert.equal(state.generation.value.id, 'next-generation')
  assert.equal(state.selectedRepairExecutionId.value, 31)
  assert.equal(state.repairExecution.value, null)

  await state.loadRepairExecution(31)
  assert.equal(state.repairExecution.value.screenshot_path, 'new-generation.png')
})

test('candidate_ready also loads the last failed execution detail through the standard execution_status field', async t => {
  const candidateReady = record({
    repair: {
      status: 'candidate_ready', candidate_hash: 'sha256-candidate', candidate_script: 'candidate',
      attempts: [{ execution_id: 44, execution_status: 'failed', summary: '候选定位器失败' }]
    }
  })
  const { state, handlers } = await harness(t, candidateReady)
  const detail = { id: 144, execution: 44, project_id: 1, status: 'failed', screenshot_path: 'candidate-failed.png' }
  handlers.getWebUIScriptGeneration = async () => ({ success: true, data: candidateReady })
  handlers.getWebUITestCaseExecution = async () => ({ success: true, data: detail })
  await state.refresh()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(state.selectedRepairExecutionId.value, 44)
  assert.deepEqual(state.repairExecution.value, detail)
})

test('unified variable table can supply inferred password via transient debug override', async t => {
  const variables = [{ name: 'TEST_PASSWORD', value: '', description: '', required: true, is_secret: true }]
  const { state, handlers, calls, storage } = await harness(t, record({ variables }))
  handlers.debugWebUIScriptGeneration = async () => ({ data: record({ variables, verification: { status: 'pending' } }) })

  await state.debug([{ name: 'TEST_PASSWORD', value: 'fixture-password' }])

  const debugCall = calls.find(call => call.name === 'debugWebUIScriptGeneration')
  assert.deepEqual(debugCall.args[2].runtime_variables, [{ name: 'TEST_PASSWORD', value: 'fixture-password', is_secret: true }])
  assert.equal(calls.some(call => call.name === 'updateWebUIScriptGenerationDraft'), false)
  assert.equal(state.localDraft.value.variables[0].value, '')
  assert.deepEqual([...storage.values()], ['test-generation'])
})

test('saving an unexecuted script sends draft mode, not verified mode', async t => {
  const { state, handlers, calls } = await harness(t)
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 10 } })
  const result = await state.save('测试草稿')
  assert.equal(result.test_case_id, 10)
  assert.equal(state.generation.value, null)
  assert.equal(state.localDraft.value, null)
  const saved = calls.find(call => call.name === 'saveWebUIScriptGeneration')
  assert.equal(saved.args[2].mode, 'draft')
  assert.equal(saved.args[2].expected_revision, 0)
})

test('successful save clears generation workspace state and removes local pointer', async t => {
  const { state, handlers, storage } = await harness(t, record({ verification: { status: 'incomplete', execution_id: 14 } }))
  state.debugExecution.value = { id: 9, execution: 14, project_id: 1, status: 'incomplete', log: 'old debug detail' }
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 20 } })
  const result = await state.save('测试草稿')
  assert.equal(result.test_case_id, 20)
  assert.equal(state.generation.value, null)
  assert.equal(state.localDraft.value, null)
  assert.equal(state.debugExecution.value, null)
  assert.equal(state.draftConflict.value, false)
  assert.equal(state.lastError.value, '')
  assert.equal(storage.size, 0)
})

test('save without test_case_id treats response as malformed and keeps workspace', async t => {
  const { state, handlers } = await harness(t)
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record() } })
  await assert.rejects(state.save('测试草稿'), /保存响应缺少测试用例标识/)
  assert.equal(state.generation.value.id, 'test-generation')
  assert.equal(state.localDraft.value !== null, true)
})

test('save failure keeps workspace and draft state', async t => {
  const { state, handlers } = await harness(t)
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'keep these edits' })
  handlers.updateWebUIScriptGenerationDraft = async (_project, _generationId, payload) => ({ data: { ...record(), script_draft: payload.script_draft } })
  handlers.saveWebUIScriptGeneration = async () => ({ success: false, message: '保存失败' })
  await assert.rejects(state.save('测试草稿'))
  assert.equal(state.generation.value.id, 'test-generation')
  assert.equal(state.localDraft.value.script_draft, 'keep these edits')
  assert.equal(state.hasUnsavedDraft.value, false)
  assert.equal(state.localDraft.value.dirty, false)
  assert.equal(state.draftConflict.value, false)
})

test('invalid saved case identifiers never clear the draft or restore pointer', async t => {
  const { state, handlers, storage } = await harness(t)
  for (const testCaseId of [null, '', ' ', NaN, Infinity, 0, -1, 1.5, 'invalid', {}, true]) {
    handlers.saveWebUIScriptGeneration = async () => ({ success: true, data: { test_case_id: testCaseId } })
    await assert.rejects(state.save('测试草稿'), /无法确认保存结果/)
    assert.equal(state.localDraft.value.generationId, 'test-generation')
    assert.equal(storage.get(state.storageKey.value), 'test-generation')
    assert.equal(state.saving.value, false)
  }
})

test('failed creation keeps the previous editable draft', async t => {
  const { state, handlers } = await harness(t)
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'keep these edits' })
  handlers.createWebUIScriptGeneration = async () => { throw new Error('offline create failure') }
  await assert.rejects(state.create({ description: 'another generation' }))
  assert.equal(state.localDraft.value.script_draft, 'keep these edits')
  assert.equal(state.hasUnsavedDraft.value, true)
})

test('creation forwards only the environment-free generation contract', async t => {
  const { state, calls } = await harness(t, record(), { create: false })
  await state.create({
    description: '目标网址：https://example.test/login\\n测试账号：demo / demo-password',
    module_id: 8,
    model_config_id: 12,
    exploration_timeout_seconds: 600
  })
  const created = calls.find(call => call.name === 'createWebUIScriptGeneration')
  assert.deepEqual(created.args, [1, {
    description: '目标网址：https://example.test/login\\n测试账号：demo / demo-password',
    module_id: 8,
    model_config_id: 12,
    exploration_timeout_seconds: 600
  }])
})

test('old polling cannot resume while a replacement generation is being created', async t => {
  const { state, handlers, calls } = await harness(t)
  const request = deferred()
  handlers.createWebUIScriptGeneration = () => request.promise
  const pending = state.create({ description: 'replacement' })
  const count = calls.length
  await state.refresh()
  assert.equal(calls.length, count)
  request.resolve({ data: { ...record(), id: 'replacement' } })
  await pending
  assert.equal(state.generation.value.id, 'replacement')
})

test('a delayed poll response cannot restore state after save success', async t => {
  const { state, handlers } = await harness(t, { ...record(), status: 'exploring' })
  const pollRequest = deferred()
  handlers.getWebUIScriptGeneration = () => pollRequest.promise
  const poll = state.refresh()
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 21 } })
  await state.save('测试草稿')
  pollRequest.resolve({ data: record({ status: 'failed', script_draft: 'stale poll content' }) })
  await poll
  assert.equal(state.generation.value, null)
  assert.equal(state.localDraft.value, null)
})

test('a delayed debug detail response cannot restore debugExecution after save success', async t => {
  const { state, handlers } = await harness(t, { ...record(), status: 'exploring', verification: { status: 'incomplete', execution_id: 14 } })
  const detailRequest = deferred()
  handlers.getWebUIScriptGeneration = async () => ({
    data: {
      ...record(),
      workspace: {
        verification: { status: 'incomplete', execution_id: 14 }
      }
    }
  })
  handlers.getWebUITestCaseExecution = () => detailRequest.promise
  await state.refresh()
  await new Promise(resolve => setImmediate(resolve))
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 22 } })
  await state.save('测试草稿')
  detailRequest.resolve({ data: { execution: 14, status: 'incomplete', log: 'stale debug detail' } })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(state.debugExecution.value, null)
})

test('saveDraft success preserves generation workspace and localStorage', async t => {
  const { state, handlers, storage } = await harness(t)
  state.updateLocalDraft({ ...state.localDraft.value, script_draft: 'draft draft' })
  handlers.updateWebUIScriptGenerationDraft = async (_project, _generationId, payload) => ({
    data: { ...record(), script_draft: payload.script_draft }
  })
  const result = await state.saveDraft()
  assert.equal(state.generation.value.id, 'test-generation')
  assert.equal(state.localDraft.value?.generationId, 'test-generation')
  assert.equal(state.localDraft.value?.script_draft, 'draft draft')
  assert.equal(state.localDraft.value?.dirty, false)
  assert.equal(storage.get('automation:webui-script-generation:v5:1:1'), 'test-generation')
  assert.equal(result.workspace?.verification?.status, 'unverified')
})

test('successful clear workspace prevents later restore from issuing generation GET', async t => {
  const { state, handlers, calls, storage } = await harness(t)
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 20 } })
  await state.save('测试草稿')
  calls.length = 0
  await state.restore()
  assert.equal(calls.some(call => call.name === 'getWebUIScriptGeneration'), false)
  assert.equal(storage.size, 0)
  assert.equal(state.generation.value, null)
})

test('a stale websocket event after save success cannot rehydrate workspace state', async t => {
  const { state, calls, handlers } = await harness(t)
  handlers.saveWebUIScriptGeneration = async () => ({ data: { generation: record(), test_case_id: 24 } })
  await state.save('测试草稿')
  const handled = state.handleWebSocketEvent({ generation_id: 'test-generation' })
  assert.equal(handled, false)
  assert.equal(state.generation.value, null)
  assert.equal(state.localDraft.value, null)
  assert.equal(calls.some(call => call.name === 'getWebUIScriptGeneration'), false)
})

test('old save completion should not clear a new save in progress', async t => {
  const { state, projectId, handlers, storage } = await harness(t)
  const oldSave = deferred()
  handlers.saveWebUIScriptGeneration = () => oldSave.promise
  const savingOld = state.save('旧任务待清理')
  projectId.value = 2
  await nextTick()
  handlers.createWebUIScriptGeneration = async () => ({ data: { ...record(), id: 'replacement' } })
  await state.create({ description: 'switch project and new task' })
  const newSave = deferred()
  handlers.saveWebUIScriptGeneration = () => newSave.promise
  const savingNew = state.save('新任务保存')
  assert.equal(state.saving.value, true)
  oldSave.resolve({ data: { test_case_id: 30 } })
  await savingOld
  assert.equal(state.generation.value.id, 'replacement')
  assert.equal(storage.get('automation:webui-script-generation:v5:1:2'), 'replacement')
  assert.equal(state.saving.value, true)
  newSave.resolve({ data: { test_case_id: 31 } })
  await savingNew
  assert.equal(state.saving.value, false)
  assert.equal(state.generation.value, null)
})

test('old save completion should not clear a new save after user switch', async t => {
  const { state, userId, handlers, storage } = await harness(t)
  const oldSave = deferred()
  handlers.saveWebUIScriptGeneration = () => oldSave.promise
  const savingOld = state.save('旧任务待清理')

  userId.value = 2
  await nextTick()

  handlers.createWebUIScriptGeneration = async () => ({ data: { ...record(), id: 'replacement-by-user' } })
  await state.create({ description: 'switch user and new task' })

  const newSave = deferred()
  handlers.saveWebUIScriptGeneration = () => newSave.promise
  const savingNew = state.save('新任务保存')
  assert.equal(state.saving.value, true)

  oldSave.resolve({ data: { test_case_id: 30 } })
  await savingOld
  assert.equal(state.generation.value.id, 'replacement-by-user')
  assert.equal(storage.get('automation:webui-script-generation:v5:2:1'), 'replacement-by-user')
  assert.equal(state.saving.value, true)

  newSave.resolve({ data: { test_case_id: 31 } })
  await savingNew
  assert.equal(state.saving.value, false)
  assert.equal(state.generation.value, null)
})

test('old save result cannot clear generation after project switch and recreate', async t => {
  const { state, projectId, handlers, storage } = await harness(t)
  const saveRequest = deferred()
  handlers.saveWebUIScriptGeneration = () => saveRequest.promise
  const saving = state.save('待清理旧任务')
  projectId.value = 2
  await nextTick()
  handlers.createWebUIScriptGeneration = async () => ({ data: { ...record(), id: 'replacement' } })
  await state.create({ description: 'switch project and new task' })
  saveRequest.resolve({ data: { test_case_id: 23 } })
  await saving
  assert.equal(state.generation.value.id, 'replacement')
  assert.equal(state.localDraft.value?.generationId, 'replacement')
  assert.equal(storage.get('automation:webui-script-generation:v5:1:2'), 'replacement')
})

test('incomplete debug runs load their real logs and screenshot on refresh and restore', async t => {
  const { state, handlers, calls } = await harness(t)
  const detail = { id: 41, execution: 14, project_id: 1, status: 'incomplete', log: 'pytest: 3 assertions passed', screenshot_path: 'webui_failure_screenshots/execution_14/generation_draft.png' }
  handlers.getWebUIScriptGeneration = async () => ({ success: true, data: record({ verification: { status: 'incomplete', execution_id: 14 } }) })
  handlers.getWebUITestCaseExecution = async () => ({ success: true, data: detail })
  await state.refresh()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(state.debugExecution.value, detail)
  assert.equal(state.workspace.value.verification.status, 'incomplete')
  await state.restore()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(state.debugExecution.value, detail)
  assert.deepEqual(calls.filter(call => call.name === 'getWebUITestCaseExecution').map(call => call.args), [[1, 14], [1, 14]])
})

test('all completed debug outcomes load details but in-progress runs do not', async t => {
  const { state, handlers, calls } = await harness(t)
  for (const status of ['pending', 'running', 'passed', 'failed', 'error']) {
    const before = calls.filter(call => call.name === 'getWebUITestCaseExecution').length
    handlers.getWebUIScriptGeneration = async () => ({ success: true, data: record({ verification: { status, execution_id: 14 } }) })
    handlers.getWebUITestCaseExecution = async () => ({ success: true, data: { execution: 14, status, log: status } })
    await state.refresh()
    await new Promise(resolve => setImmediate(resolve))
    const after = calls.filter(call => call.name === 'getWebUITestCaseExecution').length
    assert.equal(after - before, ['pending', 'running'].includes(status) ? 0 : 1)
  }
})
