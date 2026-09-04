import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { effectScope, nextTick, ref } from 'vue'

// Load the real composable with a tiny import seam, without adding a test-only
// dependency or contacting a real API. Every test gets isolated API/window state.
const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let moduleId = 0
const methods = [
  'cancelWebUIScriptGeneration', 'createWebUIScriptGeneration', 'debugWebUIScriptGeneration',
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
    storage.set('aits:webui-script-generation:v5:1:1', options.storedGenerationId)
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
  const token = `__aitsWorkspaceTest${++moduleId}`
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
  assert.deepEqual([...storage.keys()], ['aits:webui-script-generation:v5:1:1'])
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
  assert.equal(storage.get('aits:webui-script-generation:v5:1:1'), 'test-generation')
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
  assert.equal(storage.get('aits:webui-script-generation:v5:1:2'), 'replacement')
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
  assert.equal(storage.get('aits:webui-script-generation:v5:2:1'), 'replacement-by-user')
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
  assert.equal(storage.get('aits:webui-script-generation:v5:1:2'), 'replacement')
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
