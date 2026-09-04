import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const dialogSource = () => readFileSync(
  new URL('../src/components/scheduledTasks/TaskEditDialog.vue', import.meta.url),
  'utf8'
)

test('WebUI scheduled tasks omit the environment control, lookup, validation, and payload field', () => {
  const source = dialogSource()
  assert.match(source, /const requiresEnvironment = computed\(\(\) => form\.suite_type !== 'web'\)/)
  assert.match(source, /<el-form-item v-if="requiresEnvironment" label="执行环境" prop="environment">/)
  assert.match(source, /if \(!requiresEnvironment\.value\) \{\s*environments\.value = \[\]\s*form\.environment = null/s)
  assert.match(source, /\{ environment, \.\.\.taskFields \} = form/)
  assert.match(source, /\.\.\.\(requiresEnvironment\.value \? \{ environment \} : \{\}\)/)
})

test('non-WebUI scheduled tasks retain the environment validation and category lookup', () => {
  const source = dialogSource()
  assert.match(source, /if \(requiresEnvironment\.value && !value\) callback\(new Error\('请选择执行环境'\)\)/)
  assert.match(source, /const category = form\.suite_type/)
  assert.match(source, /getProjectEnvironments\(projectStore\.currentProjectId, \{\s*category: category\s*\}\)/s)
})

test('WebUI scheduled task details do not show a blank environment', () => {
  const source = readFileSync(new URL('../src/components/scheduledTasks/TaskDetailDialog.vue', import.meta.url), 'utf8')
  assert.match(source, /<el-descriptions-item v-if="task\.suite_type !== 'web'" label="执行环境">/)
})
