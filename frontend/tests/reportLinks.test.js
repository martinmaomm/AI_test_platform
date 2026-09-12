import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { reportPath, reportUrl, safeInternalRedirect } from '../src/utils/reportLinks.js'

test('login report return paths cannot navigate outside this application', () => {
  assert.equal(safeInternalRedirect('/reports/web/1/2'), '/reports/web/1/2')
  for (const value of ['//elsewhere.example', '/\\elsewhere.example', 'https://elsewhere.example', null, ['/reports/web/1/2'], '/\ninvalid']) {
    assert.equal(safeInternalRedirect(value), '/dashboard')
  }
})

test('native report links keep project and execution ids encoded', () => {
  assert.equal(reportPath('web', 12, 34), '/reports/web/12/34')
  assert.equal(reportPath('api', 'project id', 'run/id'), '/reports/api/project%20id/run%2Fid')
  assert.equal(reportUrl('web', 12, 34, 'https://automation.example'), 'https://automation.example/reports/web/12/34')
})

test('API native reports distinguish unknown outcomes from assertion failures and warn before replay', async () => {
  const source = await readFile(new URL('../src/components/APITestCaseExecutionDetail.vue', import.meta.url), 'utf8')
  assert.match(source, /unknown: '结果未知，请核对测试数据'/)
  assert.match(source, /replay_safety\?\.safe_to_retry === false/)
  assert.match(source, /本轮可能已产生测试数据，请先核对结果再重新运行/)
  assert.match(source, /'stopped': '已停止'/)
})

test('report routes are authenticated and old static-report proxy is absent', async () => {
  const [router, vite, api, login] = await Promise.all([
    readFile(new URL('../src/router/index.js', import.meta.url), 'utf8'),
    readFile(new URL('../vite.config.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/api/index.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/views/Login.vue', import.meta.url), 'utf8')
  ])
  assert.match(router, /path: '\/reports\/web\/:projectId\/:executionId'[\s\S]*requiresAuth: true/)
  assert.match(router, /path: '\/reports\/api\/:projectId\/:executionId'[\s\S]*requiresAuth: true/)
  assert.match(router, /path: '\/reports\/detail\/:id'[\s\S]*requiresAuth: true/)
  assert.match(router, /query: \{ redirect: to\.fullPath \}/)
  assert.doesNotMatch(vite, /playwright-reports/)
  assert.doesNotMatch(api, /reports\/detail/)
  assert.match(login, /route\.query\.redirect/)
})

test('frontend has no runtime Allure report reference', async () => {
  const files = [
    '../src/components/WebUITestSuiteExecutionDetail.vue',
    '../src/components/APITestSuiteExecutionDetai.vue',
    '../src/views/reports/TestReportDetail.vue'
  ]
  for (const file of files) {
    const source = await readFile(new URL(file, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /allure/i)
  }
})

test('independent report page calls the direct authenticated report endpoint without list scanning', async () => {
  const source = await readFile(new URL('../src/views/reports/ExecutionReportPage.vue', import.meta.url), 'utf8')
  assert.match(source, /getWebUITestExecutionReport/)
  assert.match(source, /getAPITestExecutionReport/)
  assert.match(source, /const executionId = computed/)
  assert.match(source, /let requestVersion = 0/)
  assert.match(source, /onBeforeUnmount\(\(\) => \{ requestVersion \+= 1 \}\)/)
  assert.match(source, /status === 404 \|\| status === 403/)
  assert.doesNotMatch(source, /getTestExecutions|getAPITestExecutions|findExecution/)
})

test('scheduled report renders native status counts and reloads safely when its id changes', async () => {
  const source = await readFile(new URL('../src/views/reports/TestReportDetail.vue', import.meta.url), 'utf8')
  assert.match(source, /log\.report_status \|\| log\.status/)
  assert.match(source, /log\.incomplete_cases/)
  assert.match(source, /log\.error_cases/)
  assert.match(source, /execution_errors/)
  assert.match(source, /watch\(id, fetchDetail, \{ immediate: true \}\)/)
  assert.match(source, /onBeforeUnmount\(\(\) => \{ requestVersion \+= 1 \}\)/)
  assert.match(source, /<router-link/)
  assert.doesNotMatch(source, /exportPdf|window\.print/)
})

test('API suite report reuses the complete case-detail renderer for every returned member', async () => {
  const source = await readFile(new URL('../src/components/APITestSuiteExecutionDetai.vue', import.meta.url), 'utf8')
  assert.match(source, /import APITestCaseExecutionDetail/)
  assert.match(source, /<APITestCaseExecutionDetail :result="item"/)
  assert.match(source, /该次套件执行未返回子用例结果/)
  assert.doesNotMatch(source, /caseLog\(/)
})

test('API native report reads the persisted case log even when the runner result has no log', async () => {
  const source = await readFile(new URL('../src/components/APITestCaseExecutionDetail.vue', import.meta.url), 'utf8')
  const body = source.match(/const getHttpRunnerLog = \(\) => \{([\s\S]*?)\n\}/)?.[1]
  assert.ok(body)
  const readLog = new Function('getHttpRunnerRawResult', 'result', body)
  assert.equal(readLog(() => ({}), { value: { log: '验证 状态码 通过' } }), '验证 状态码 通过')
  assert.equal(readLog(() => ({ log: 'runner output' }), { value: {} }), 'runner output')
  assert.equal(readLog(() => ({}), { value: { error_message: '准备失败' } }), '准备失败')
})
