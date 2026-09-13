import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'

const view = async () => {
  const source = await readFile(new URL('../src/views/api-testing/TestExecutions.vue', import.meta.url), 'utf8')
  const parsed = parse(source)
  assert.deepEqual(parsed.errors, [])
  return parsed
}

const extractFunction = (script, name) => {
  const match = script.search(new RegExp(`const ${name} = async \\([^)]*\\) => \\{`))
  if (match < 0) throw new Error(`${name} function declaration not found`)
  const open = script.indexOf('{', match)
  let depth = 0
  for (let index = open; index < script.length; index += 1) {
    if (script[index] === '{') depth += 1
    if (script[index] === '}') depth -= 1
    if (depth === 0) return script.slice(match, index + 1)
  }
  throw new Error(`${name} function body not found`)
}

const deferred = () => {
  let resolve
  const promise = new Promise(value => { resolve = value })
  return { promise, resolve }
}

test('API execution list exposes source labels and sends the source filter', async () => {
  const { descriptor } = await view()
  const template = descriptor.template.content
  const script = descriptor.scriptSetup.content

  assert.match(template, /placeholder="执行来源"/)
  assert.match(template, /@change="handleExecutionSourceChange"/)
  assert.match(template, /label="AI自动验证" value="workspace_generation"/)
  assert.match(template, /label="工作区调试" value="workspace_debug"/)
  assert.match(template, /label="正式执行" value="formal"/)
  assert.match(template, /getExecutionSourceText\(row\.execution_source, row\.execution_source_display\)/)
  assert.match(script, /execution_source: ''/)
  assert.match(script, /workspace_generation: 'AI自动验证'/)
  assert.match(script, /workspace_debug: '工作区调试'/)
  assert.match(script, /formal: '正式执行'/)
  assert.match(script, /execution_source: item\.execution_source/)
  assert.doesNotMatch(script, /input_snapshot/)
})

test('source changes reset pagination, refetch, and discard stale list responses', async () => {
  const { descriptor } = await view()
  const script = descriptor.scriptSetup.content
  const loadSource = extractFunction(script, 'loadTestRuns')
  const changeSource = extractFunction(script, 'handleExecutionSourceChange')
  const pending = []
  const calls = []
  const loading = { value: false }
  const currentPage = { value: 3 }
  const pageSize = { value: 20 }
  const filters = { exec_type: '', status: '', trigger_type: '', execution_source: '', dateRange: null }
  const testRuns = { value: [] }
  const totalRuns = { value: 0 }
  const state = new Function(
    'loading', 'currentPage', 'pageSize', 'filters', 'getAPITestExecutions', 'projectStore',
    'testRuns', 'totalRuns', 'handleError',
    `let testRunsRequestSequence = 0; ${loadSource}; ${changeSource}; return { loadTestRuns, handleExecutionSourceChange }`,
  )(
    loading, currentPage, pageSize, filters,
    async (_projectId, params) => {
      calls.push({ ...params })
      const next = deferred()
      pending.push(next)
      return next.promise
    },
    { currentProjectId: 9 }, testRuns, totalRuns, () => {},
  )

  const staleRequest = state.loadTestRuns()
  filters.execution_source = 'workspace_debug'
  const latestRequest = state.handleExecutionSourceChange()
  assert.deepEqual(calls, [
    { page: 3, page_size: 20 },
    { page: 1, page_size: 20, execution_source: 'workspace_debug' },
  ])

  pending[1].resolve({ success: true, data: { items: [{ id: 2, execution_source: 'workspace_debug' }], pagination: { total: 1 } } })
  await latestRequest
  pending[0].resolve({ success: true, data: { items: [{ id: 1, execution_source: 'formal' }], pagination: { total: 1 } } })
  await staleRequest
  assert.equal(currentPage.value, 1)
  assert.deepEqual(testRuns.value.map(item => item.id), [2])
  assert.equal(totalRuns.value, 1)
  assert.equal(loading.value, false)

  currentPage.value = 4
  filters.execution_source = ''
  const clearedRequest = state.handleExecutionSourceChange()
  assert.deepEqual(calls.at(-1), { page: 1, page_size: 20 })
  pending[2].resolve({ success: true, data: { items: [{ id: 3, execution_source: 'formal' }], pagination: { total: 1 } } })
  await clearedRequest
  assert.deepEqual(testRuns.value.map(item => item.id), [3])
})
