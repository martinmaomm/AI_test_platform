import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { effectScope, nextTick, reactive } from 'vue'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
let moduleId = 0

async function harness(t, initial = {}) {
  const props = reactive({ projectId: 1, executionId: 2, caseExecutionId: null, screenshotPath: '', status: 'running', ...initial })
  const calls = [], created = [], revoked = []
  const api = { fetch: async () => new Blob(['fixture'], { type: 'image/png' }) }
  const key = `__aitsScreenshotTest${++moduleId}`
  globalThis[key] = async (...args) => { calls.push(args); return api.fetch(...args) }
  const apiModule = dataModule(`export const getWebUITestExecutionScreenshot = globalThis[${JSON.stringify(key)}]`)
  const source = (await readFile(new URL('../src/composables/useWebUIExecutionScreenshot.js', import.meta.url), 'utf8'))
    .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
    .replace("from '@/api/webTesting'", `from '${apiModule}'`)
  const { useWebUIExecutionScreenshot } = await import(dataModule(source))
  delete globalThis[key]
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL
  URL.createObjectURL = blob => { const url = `blob:fixture-${created.length + 1}`; created.push({ blob, url }); return url }
  URL.revokeObjectURL = url => revoked.push(url)
  const scope = effectScope()
  const state = scope.run(() => useWebUIExecutionScreenshot(() => props))
  t.after(() => { scope.stop(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke })
  await flush()
  return { props, calls, created, revoked, api, state, scope }
}

test('successful runs fetch an authenticated screenshot without an error message', async t => {
  const { state, calls } = await harness(t, { status: 'passed', screenshotPath: 'webui_failure_screenshots/execution_2/single_case.png' })
  assert.equal(state.showScreenshot.value, true)
  assert.equal(state.title.value, '执行完成截图')
  assert.equal(state.screenshotUrl.value, 'blob:fixture-1')
  assert.deepEqual(calls, [[1, 2, null]])
})

test('failed suite members and incomplete runs show screenshots with accurate labels', async t => {
  const { props, state, calls } = await harness(t, { status: 'failed', caseExecutionId: 9, screenshotPath: 'fixture.png' })
  assert.equal(state.title.value, '异常结束截图')
  assert.deepEqual(calls[0], [1, 2, 9])
  props.status = 'incomplete'
  await flush()
  assert.equal(state.title.value, '执行结束截图（验证未完成）')
  assert.ok(state.screenshotUrl.value)
})

test('polling adds a screenshot to the same execution without reopening its detail', async t => {
  const { props, state, calls } = await harness(t)
  assert.equal(state.showScreenshot.value, false)
  assert.equal(calls.length, 0)
  props.status = 'passed'
  props.screenshotPath = 'fixture.png'
  await flush()
  assert.equal(calls.length, 1)
  assert.ok(state.screenshotUrl.value)
})

test('missing screenshots do not trigger downloads or pretend to be available', async t => {
  const { props, state, calls } = await harness(t, { status: 'error' })
  assert.equal(state.showScreenshot.value, true)
  assert.equal(state.screenshotUrl.value, '')
  assert.equal(state.loading.value, false)
  assert.equal(calls.length, 0)
  props.status = 'skipped'
  await flush()
  assert.equal(state.showScreenshot.value, false)
})

test('late responses never display another execution screenshot', async t => {
  const { props, api, state, created } = await harness(t)
  let finishOld
  api.fetch = () => new Promise(resolve => { finishOld = resolve })
  props.screenshotPath = 'old.png'
  await flush()
  const newBlob = new Blob(['new'])
  api.fetch = async () => newBlob
  props.executionId = 3
  props.screenshotPath = 'new.png'
  await flush()
  finishOld(new Blob(['old']))
  await flush()
  assert.equal(created.length, 1)
  assert.equal(created[0].blob, newBlob)
  assert.equal(state.loading.value, false)
})

test('object URLs are released on replacement and pending loads ignored on disposal', async t => {
  const { props, api, state, created, revoked, scope } = await harness(t, { screenshotPath: 'old.png' })
  let finish
  api.fetch = () => new Promise(resolve => { finish = resolve })
  props.executionId = 3
  await flush()
  assert.deepEqual(revoked, ['blob:fixture-1'])
  assert.equal(state.screenshotUrl.value, '')
  scope.stop()
  finish(new Blob(['disposed']))
  await flush()
  assert.equal(created.length, 1)
})

test('download failures can be retried without changing execution status', async t => {
  const { props, api, state } = await harness(t, { status: 'passed' })
  api.fetch = async () => { throw new Error('offline') }
  props.screenshotPath = 'fixture.png'
  await flush()
  assert.equal(state.error.value, '截图加载失败，请重试。')
  assert.equal(state.loading.value, false)
  assert.equal(props.status, 'passed')
  api.fetch = async () => new Blob(['retry'])
  await state.reload()
  assert.equal(state.error.value, '')
  assert.ok(state.screenshotUrl.value)
})
