import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createBeatStatusMonitor } from '../src/utils/beatStatusMonitor.js'

const flush = () => new Promise((resolve) => setImmediate(resolve))
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const setup = (fetchStatus) => {
  const updates = []
  const timers = new Map()
  let id = 0
  const monitor = createBeatStatusMonitor({
    fetchStatus,
    onUpdate: (state) => updates.push(state),
    setTimer: (callback, delay) => { timers.set(++id, { callback, delay }); return id },
    clearTimer: (timerId) => timers.delete(timerId)
  })
  return { monitor, updates, timers, current: () => updates.at(-1) }
}

test('checks on entry, polls every 15 seconds, and recovers from offline', async () => {
  let status = 'offline'
  const fixture = setup(async () => ({ data: { status } }))
  fixture.monitor.start(2)
  await flush()
  assert.deepEqual(fixture.current(), { status: 'offline', checking: false })
  assert.equal([...fixture.timers.values()][0].delay, 15000)
  status = 'online'
  await [...fixture.timers.values()][0].callback()
  assert.equal(fixture.current().status, 'online')
  assert.equal(fixture.timers.size, 1)
  fixture.monitor.stop()
  assert.equal(fixture.timers.size, 0)
})

test('slow requests never overlap and initial state is not falsely offline', async () => {
  const request = deferred()
  let calls = 0
  const fixture = setup(() => { calls += 1; return request.promise })
  fixture.monitor.start(2)
  fixture.monitor.start(2)
  await fixture.monitor.refresh()
  assert.equal(calls, 1)
  assert.equal(fixture.current().status, 'checking')
  request.resolve({ status: 'online' })
  await flush()
  fixture.monitor.stop()
})

test('network errors and malformed results are unknown, not offline', async () => {
  for (const fetchStatus of [async () => { throw new Error('offline API') }, async () => ({}), async () => ({ data: { status: 'broken' } })]) {
    const fixture = setup(fetchStatus)
    fixture.monitor.start(2)
    await flush()
    assert.equal(fixture.current().status, 'unknown')
    fixture.monitor.stop()
  }
})

test('switching project aborts and discards previous responses', async () => {
  const first = deferred()
  const requests = []
  const fixture = setup((project, signal) => {
    requests.push({ project, signal })
    return project === 2 ? first.promise : Promise.resolve({ status: 'online' })
  })
  fixture.monitor.start(2)
  fixture.monitor.start(3)
  await flush()
  assert.equal(requests[0].signal.aborted, true)
  first.resolve({ status: 'offline' })
  await flush()
  assert.equal(fixture.current().status, 'online')
  assert.equal(fixture.timers.size, 1)
  fixture.monitor.stop()
})

test('leaving stops timers, aborts requests and prevents late updates', async () => {
  const request = deferred()
  let signal
  const fixture = setup((_project, requestSignal) => { signal = requestSignal; return request.promise })
  fixture.monitor.start(2)
  fixture.monitor.stop()
  const updateCount = fixture.updates.length
  request.resolve({ status: 'offline' })
  await flush()
  assert.equal(signal.aborted, true)
  assert.equal(fixture.updates.length, updateCount)
  assert.equal(fixture.timers.size, 0)
})

test('banner handles cached pages, hidden tabs and does not block task buttons', () => {
  const component = readFileSync(new URL('../src/components/scheduledTasks/ScheduledServiceStatus.vue', import.meta.url), 'utf8')
  assert.match(component, /onActivated\(activate\)/)
  assert.match(component, /onDeactivated\(deactivate\)/)
  assert.match(component, /removeEventListener\('visibilitychange'/)
  assert.match(component, /!document.hidden/)
  assert.match(component, /health.status !== 'online'/)
  assert.match(component, /定时任务服务未启动或已离线/)
  assert.match(component, /此提示不代表服务已经停止/)
})
