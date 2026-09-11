import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { effectScope, nextTick, reactive } from 'vue'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
let moduleId = 0

async function harness(t, initial = {}) {
  const context = reactive({ projectId: 1, generationId: 2, eventId: 'evt-1', capturedAt: '2026-09-11T10:00:00Z', screenshotStatus: 'captured', ...initial })
  const calls = [], created = [], revoked = []
  const api = { fetch: async () => new Blob(['fixture'], { type: 'image/png' }) }
  const key = `__automationFailureScreenshotTest${++moduleId}`
  globalThis[key] = async (...args) => { calls.push(args); return api.fetch(...args) }
  const apiModule = dataModule(`export const getWebUIScriptGenerationFailureScreenshot = globalThis[${JSON.stringify(key)}]`)
  const source = (await readFile(new URL('../src/composables/useGenerationFailureScreenshot.js', import.meta.url), 'utf8'))
    .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
    .replace("from '@/api/webTesting'", `from '${apiModule}'`)
  const { useGenerationFailureScreenshot } = await import(dataModule(source))
  delete globalThis[key]
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL
  URL.createObjectURL = blob => { const url = `blob:failure-${created.length + 1}`; created.push({ blob, url }); return url }
  URL.revokeObjectURL = url => revoked.push(url)
  const scope = effectScope()
  const state = scope.run(() => useGenerationFailureScreenshot(() => context))
  t.after(() => { scope.stop(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke })
  await flush()
  return { context, calls, created, revoked, api, scope, state }
}

test('captured stop screenshot uses the authenticated generation endpoint once', async t => {
  const { calls, state } = await harness(t)
  assert.equal(state.canLoad.value, true)
  assert.equal(state.screenshotUrl.value, 'blob:failure-1')
  assert.deepEqual(calls, [[1, 2, 'evt-1', '2026-09-11T10:00:00Z']])
})

test('unavailable or unrequested screenshots do not make a request', async t => {
  const { calls, state } = await harness(t, { screenshotStatus: 'unavailable' })
  assert.equal(state.canLoad.value, false)
  assert.equal(state.screenshotUrl.value, '')
  assert.deepEqual(calls, [])
})

test('late screenshot responses never leak across a generation, event, or captured-at switch', async t => {
  const { context, api, created, state } = await harness(t, { screenshotStatus: 'not_requested' })
  let finishOld
  api.fetch = () => new Promise(resolve => { finishOld = resolve })
  context.screenshotStatus = 'captured'
  await flush()
  const newBlob = new Blob(['new'])
  api.fetch = async () => newBlob
  context.generationId = 3
  context.eventId = 'evt-2'
  context.capturedAt = '2026-09-11T10:01:00Z'
  await flush()
  finishOld(new Blob(['old']))
  await flush()
  assert.equal(created.length, 1)
  assert.equal(created[0].blob, newBlob)
  assert.equal(state.loading.value, false)
})

test('object URLs are released and 403 or 404 remain a safe terminal display error', async t => {
  const { context, api, revoked, scope, state } = await harness(t)
  api.fetch = async () => { throw { response: { status: 404 } } }
  context.eventId = 'evt-2'
  await flush()
  assert.deepEqual(revoked, ['blob:failure-1'])
  assert.equal(state.error.value, '截图当前不可用，不影响已保存的停止现场信息。')
  assert.equal(state.loading.value, false)
  scope.stop()
})
