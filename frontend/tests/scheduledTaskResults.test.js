import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('incomplete reports are not overwritten by the aggregate failed status', () => {
  for (const [file, fnName, row, expected] of [
    ['index.vue', 'getLastReportResult', { last_execution_status: 'failed', last_report_status: 'incomplete' }, '未完成'],
    ['logs.vue', 'getLogResult', { status: 'failed', report_status: 'incomplete' }, '未完成'],
    ['logs.vue', 'getLogResult', { status: 'failed', report_status: 'error', total_cases: 0 }, '测试未通过'],
    ['logs.vue', 'getLogResult', { status: 'failed', report_status: 'skipped' }, '已跳过'],
  ]) {
    const source = read(`../src/views/scheduledTasks/${file}`)
    const body = source.match(new RegExp(`const ${fnName} = \\(row\\) => \\{([\\s\\S]*?)\\n\\}`))[1]
    assert.equal(new Function('row', body)(row).label, expected)
  }
})

test('task list uses report status instead of treating zero failed cases as a pass', () => {
  const source = read('../src/views/scheduledTasks/index.vue')

  assert.match(source, /const getLastReportResult = \(row\)/)
  assert.match(source, /const reportStatus = row\.last_report_status/)
  assert.match(source, /return \{ label: '未完成', type: 'warning' \}/)
  assert.match(source, /return \{ label: '已跳过', type: 'info' \}/)
  assert.match(source, /executionStatus === 'failed'/)
  assert.doesNotMatch(source, /row\.last_failed_cases > 0 \? '未通过' : '通过'/)
})

test('task and log pages remove type filters and disabled or App options', () => {
  const taskList = read('../src/views/scheduledTasks/index.vue')
  const logs = read('../src/views/scheduledTasks/logs.vue')

  assert.doesNotMatch(taskList, /filters\.suite_type/)
  assert.doesNotMatch(logs, /filters\.suite_type/)
  assert.doesNotMatch(taskList, /value="disabled"/)
  assert.doesNotMatch(logs, /app-testing\/test-runs/)
})

test('manual runs remain available to paused tasks and show a clear duplicate-run conflict', () => {
  const source = read('../src/views/scheduledTasks/index.vue')
  const api = read('../src/api/scheduledTasks.js')

  assert.doesNotMatch(source, /:disabled="row\.status !== 'active'"/)
  assert.match(source, /error\?\.response\?\.status === 409/)
  assert.match(source, /任务正在执行，请等待当前执行完成后再试/)
  assert.match(api, /updateScheduledTaskStatus = async \(projectId, id, status\)/)
  assert.match(api, /api\.patch\([^\n]+status\/`, \{ status \}\)/)
  assert.match(source, /const changeTaskStatus = async \(task\)/)
  assert.match(source, /const nextStatus = task\.status === 'active' \? 'paused' : 'active'/)
})

test('logs render running, skipped, and zero-case failure states without false passes', () => {
  const source = read('../src/views/scheduledTasks/logs.vue')

  assert.match(source, /const getLogResult = \(row\)/)
  assert.match(source, /row\.status === 'running' \|\| row\.status === 'pending'/)
  assert.match(source, /row\.status === 'cancelled'/)
  assert.match(source, /row\.status === 'failed'/)
  assert.match(source, /Number\(row\.failed_cases\) > 0/)
})

test('scheduled-task submit surfaces structured backend field validation messages', () => {
  const source = read('../src/components/scheduledTasks/TaskEditDialog.vue')

  assert.match(source, /const getSubmitErrorMessage = \(error\)/)
  assert.match(source, /const details = payload\?\.error\?\.details \?\? payload\?\.details/)
  assert.match(source, /suite_ids: '测试套件'/)
  assert.match(source, /cron_expression: 'Cron表达式'/)
  assert.match(source, /environment: '执行环境'/)
  assert.match(source, /ElMessage\.error\(getSubmitErrorMessage\(error\)/)
})
