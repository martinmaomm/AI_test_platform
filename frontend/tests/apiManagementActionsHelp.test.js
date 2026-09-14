import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('API management views describe available actions with shared accessible help', async () => {
  const [specs, detail, suites, executions, tooltip] = await Promise.all([
    read('../src/views/api-testing/ApiSpecManage.vue'),
    read('../src/views/api-testing/APISpecDetail.vue'),
    read('../src/views/api-testing/TestSuites.vue'),
    read('../src/views/api-testing/TestExecutions.vue'),
    read('../src/components/ActionHelpTooltip.vue'),
  ])

  for (const source of [specs, detail, suites, executions]) {
    assert.match(source, /import ActionHelpTooltip from ['"]@\/components\/ActionHelpTooltip\.vue['"]/, 'view imports the shared help control')
  }

  for (const text of [
    '上传并解析规范',
    '规范操作',
    '查看端点详情',
    '生成端点用例',
    '端点测试用例操作',
  ]) {
    assert.ok(`${specs}${detail}`.includes(text), `missing specification action help: ${text}`)
  }

  for (const text of [
    '新建套件',
    '套件操作',
    '套件用例关联',
    '批量删除套件',
    '更新套件',
    '确认执行套件',
  ]) {
    assert.ok(suites.includes(text), `missing suite action help: ${text}`)
  }

  for (const text of [
    '执行记录列表',
    '执行记录操作',
    '批量删除执行记录',
    '查看完整报告会进入该记录的报告页',
  ]) {
    assert.ok(executions.includes(text), `missing execution action help: ${text}`)
  }

  assert.match(specs, /删除会移除该规范、其端点及这些端点的测试用例/)
  assert.match(suites, /不删除测试用例/)
  assert.match(executions, /测试用例和测试套件不会被删除/)
  assert.match(suites, /可能增删改测试数据/)
  assert.match(suites, /套件变量高于用例变量，用例变量高于环境变量/)
  assert.doesNotMatch(suites, /本次变量/)
  assert.match(suites, /\.dialog-primary-action,[\s\S]*\.table-action-header \{/)
  assert.match(executions, /\.table-action-header \{/)
  assert.match(suites, /<el-table-column label="操作" width="340" fixed="right">/)
  assert.match(executions, /<el-table-column label="操作" width="250" fixed="right" align="center">/)
  assert.match(specs, /content="[^"]*无法从页面恢复/)
  assert.match(suites, /content="[^"]*无法从页面恢复/)
  assert.match(executions, /content="[^"]*无法从页面恢复/)
  assert.match(tooltip, /:aria-label="`\$\{label\}说明`"/)
  assert.match(tooltip, /:trigger="\['hover', 'focus', 'click'\]"/)
  assert.match(tooltip, /@click\.stop/)
})
