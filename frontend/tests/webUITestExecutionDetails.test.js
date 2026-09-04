import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'

const extractViewDetailsSource = async () => {
  const source = await readFile(new URL('../src/views/web-testing/TestExecutions.vue', import.meta.url), 'utf8')
  const parsed = parse(source)
  const script = parsed.descriptor.scriptSetup?.content
  if (!script) {
    throw new Error('TestExecutions.vue should contain <script setup>')
  }

  const match = script.search(/const viewDetails = async \(row\) => \{/)
  if (match < 0) {
    throw new Error('viewDetails function declaration not found')
  }
  const open = script.indexOf('{', match)
  let depth = 0
  let close = -1
  for (let i = open; i < script.length; i++) {
    if (script[i] === '{') {
      depth += 1
    } else if (script[i] === '}') {
      depth -= 1
      if (depth === 0) {
        close = i
        break
      }
    }
  }
  if (close < 0) {
    throw new Error('Unable to locate viewDetails function block')
  }
  return script.slice(match, close + 1)
}

const buildViewDetails = async () => {
  const fnSource = await extractViewDetailsSource()
  return (dependencies) => {
    const selectedRun = { value: null }
    const detailDialogVisible = { value: false }
    const messages = []
    const message = {
      error: message => messages.push(message)
    }
    const projectStore = { currentProjectId: dependencies.projectId }
    const runner = new Function(
      'getWebUITestCaseExecution',
      'getWebUITestSuiteExecution',
      'projectStore',
      'selectedRun',
      'detailDialogVisible',
      'ElMessage',
      `${fnSource}\nreturn viewDetails`
    )
    const viewDetails = runner(
      dependencies.getWebUITestCaseExecution,
      dependencies.getWebUITestSuiteExecution,
      projectStore,
      selectedRun,
      detailDialogVisible,
      message
    )
    return { viewDetails, selectedRun, detailDialogVisible, messages }
  }
}

test('case execution detail request merges response.data into the selected row', async t => {
  const row = { id: 101, exec_type: 'case', project_id: 11, log: 'row-log', screenshot_path: 'row.png', name: 'row-case', execution: 'row-exec' }
  const caseApiCalls = []
  const deps = {
    projectId: 77,
    getWebUITestCaseExecution: async (projectId, id) => {
      caseApiCalls.push([projectId, id])
      return {
        success: true,
        data: {
          id,
          project_id: 99,
          log: 'real-case-log',
          screenshot_path: 'real-case.png',
          execution: 'real-case-execution'
        }
      }
    },
    getWebUITestSuiteExecution: async () => {
      throw new Error('should not be called for case')
    }
  }
  const viewDetails = (await buildViewDetails())(deps)
  await viewDetails.viewDetails(row)
  assert.equal(viewDetails.detailDialogVisible.value, true)
  assert.deepEqual(caseApiCalls, [[77, 101]])
  assert.equal(viewDetails.selectedRun.value.id, 101)
  assert.equal(viewDetails.selectedRun.value.project_id, 99)
  assert.equal(viewDetails.selectedRun.value.log, 'real-case-log')
  assert.equal(viewDetails.selectedRun.value.screenshot_path, 'real-case.png')
  assert.equal(viewDetails.selectedRun.value.exec_type, 'case')
  assert.equal(viewDetails.selectedRun.value.execution, 'real-case-execution')
})

test('suite execution detail request merges response.data into the selected row', async t => {
  const row = { id: 202, exec_type: 'suite', project_id: 11, log: 'row-log', screenshot_path: 'row-suite.png' }
  const suiteApiCalls = []
  const deps = {
    projectId: 99,
    getWebUITestCaseExecution: async () => {
      throw new Error('should not be called for suite')
    },
    getWebUITestSuiteExecution: async (projectId, id) => {
      suiteApiCalls.push([projectId, id])
      return {
        success: true,
        data: {
          id,
          project_id: 120,
          log: 'real-suite-log',
          screenshot_path: 'real-suite.png',
          execution: 'real-suite-execution'
        }
      }
    }
  }
  const viewDetails = (await buildViewDetails())(deps)
  await viewDetails.viewDetails(row)
  assert.equal(viewDetails.detailDialogVisible.value, true)
  assert.deepEqual(suiteApiCalls, [[99, 202]])
  assert.equal(viewDetails.selectedRun.value.project_id, 120)
  assert.equal(viewDetails.selectedRun.value.log, 'real-suite-log')
  assert.equal(viewDetails.selectedRun.value.screenshot_path, 'real-suite.png')
  assert.equal(viewDetails.selectedRun.value.exec_type, 'suite')
  assert.equal(viewDetails.selectedRun.value.execution, 'real-suite-execution')
})

test('unsupported exec_type should not show detail dialog and should not call detail APIs', async t => {
  const row = { id: 303, exec_type: 'invalid' }
  const apiCalls = { case: 0, suite: 0 }
  const deps = {
    projectId: 88,
    getWebUITestCaseExecution: async () => {
      apiCalls.case += 1
      return { success: true, data: {} }
    },
    getWebUITestSuiteExecution: async () => {
      apiCalls.suite += 1
      return { success: true, data: {} }
    }
  }
  const viewDetails = (await buildViewDetails())(deps)
  await viewDetails.viewDetails(row)
  assert.equal(viewDetails.detailDialogVisible.value, false)
  assert.equal(apiCalls.case, 0)
  assert.equal(apiCalls.suite, 0)
  assert.equal(viewDetails.messages.at(-1), '未知的执行类型')
  assert.equal(viewDetails.selectedRun.value, null)
})

test('detail API response failure should not open an empty detail dialog', async t => {
  const row = { id: 404, exec_type: 'case', project_id: 11, log: 'row-log', screenshot_path: 'row.png' }
  const deps = {
    projectId: 77,
    getWebUITestCaseExecution: async () => ({ success: false, message: 'backend failed' }),
    getWebUITestSuiteExecution: async () => {
      throw new Error('should not be called')
    }
  }
  const viewDetails = (await buildViewDetails())(deps)
  await viewDetails.viewDetails(row)
  assert.equal(viewDetails.detailDialogVisible.value, false)
  assert.equal(viewDetails.selectedRun.value, null)
  assert.equal(viewDetails.messages.at(-1), 'backend failed')
})

test('invalid detail payloads never masquerade as successful execution details', async () => {
  for (const data of [null, 'invalid', []]) {
    const state = (await buildViewDetails())({
      projectId: 1,
      getWebUITestCaseExecution: async () => ({ success: true, data })
    })
    await state.viewDetails({ id: 14, exec_type: 'case' })
    assert.equal(state.detailDialogVisible.value, false)
    assert.equal(state.selectedRun.value, null)
  }
})
