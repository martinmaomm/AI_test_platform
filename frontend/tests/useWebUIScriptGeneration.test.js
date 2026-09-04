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
  await state.save('测试草稿')
  const saved = calls.find(call => call.name === 'saveWebUIScriptGeneration')
  assert.equal(saved.args[2].mode, 'draft')
  assert.equal(saved.args[2].expected_revision, 0)
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
