import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('scheduled-task editor derives suite type from the current project and only requires API environments', () => {
  const source = read('../src/components/scheduledTasks/TaskEditDialog.vue')

  assert.doesNotMatch(source, /label="测试类型"/)
  assert.doesNotMatch(source, /suite_type:/)
  assert.match(source, /const projectType = computed\(\(\) => selectedProject\.value\?\.project_type\)/)
  assert.match(source, /const requiresEnvironment = computed\(\(\) => projectType\.value === 'api'\)/)
  assert.match(source, /getProjectEnvironments\(projectId, \{\s*category: 'api'\s*\}\)/s)
  assert.match(source, /\.\.\.\(requiresEnvironment\.value \? \{ environment \} : \{\}\)/)
})

test('suite choices are project scoped, guarded against stale responses, and expose retry and ordering controls', () => {
  const dialog = read('../src/components/scheduledTasks/TaskEditDialog.vue')
  const api = read('../src/api/scheduledTasks.js')

  assert.match(api, /getSuiteChoices = async \(projectId\)/)
  assert.doesNotMatch(api, /suite_type/)
  assert.match(dialog, /const requestId = \+\+suiteRequestId/)
  assert.match(dialog, /projectId !== projectStore\.currentProjectId/)
  assert.match(dialog, /if \(!Array\.isArray\(data\)\) throw new TypeError\('测试套件响应格式无效'\)/)
  assert.match(dialog, /加载测试套件失败/)
  assert.match(dialog, /@click="loadSuites">重试<\/el-button>/)
  assert.match(dialog, /当前项目暂无测试套件/)
  assert.match(dialog, /当前项目的测试套件均不可执行/)
  assert.match(dialog, /const isSuiteDisabled = \(suite\)/)
  assert.match(dialog, /当前项目的测试套件均不可执行，请启用套件并至少配置一个用例后重试/)
  assert.match(dialog, /执行顺序（从上到下）/)
  assert.match(dialog, /const moveSuite = \(index, offset\)/)
})

test('scheduled-task detail hides redundant type and empty Web environment fields', () => {
  const source = read('../src/components/scheduledTasks/TaskDetailDialog.vue')

  assert.doesNotMatch(source, /label="测试类型"/)
  assert.match(source, /v-if="task\.environment_name" label="执行环境"/)
  assert.match(source, /getTaskExecutionLogs\(projectId, taskId, \{ page_size: 5 \}\)/)
})

test('project changes invalidate every editor lookup and close an edit instead of reusing its task id', () => {
  const source = read('../src/components/scheduledTasks/TaskEditDialog.vue')

  assert.match(source, /let environmentRequestId = 0/)
  assert.match(source, /let channelRequestId = 0/)
  assert.match(source, /requestId !== environmentRequestId \|\| projectId !== projectStore\.currentProjectId/)
  assert.match(source, /requestId !== channelRequestId \|\| projectId !== projectStore\.currentProjectId/)
  assert.match(source, /form\.notice_targets = \[\]/)
  assert.match(source, /if \(isEdit\.value\) \{\s*visible\.value = false/s)
  assert.match(source, /suite_ids: Array\.isArray\(props\.task\.suite_ids\) \? props\.task\.suite_ids : \[\]/)
})
