import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  clonePlanStep, createPlanStep, formatJsonText, joinPathAndQuery, jsonErrorMessage,
  parsePathAndQuery, previewStepUrl, serializePlanSteps,
} from '../src/views/perf-testing/performancePlanEditorState.js'

test('query rows retain duplicate, empty and Chinese values without a double question mark', () => {
  const parsed = parsePathAndQuery('/搜索?q=%E4%B8%AD%E6%96%87&q=&q=%E4%B8%AD%E6%96%87')
  assert.deepEqual(parsed.query.map(({ key, value }) => [key, value]), [['q', '中文'], ['q', ''], ['q', '中文']])
  assert.equal(joinPathAndQuery(parsed.path, parsed.query), '/搜索?q=%E4%B8%AD%E6%96%87&q=&q=%E4%B8%AD%E6%96%87')
  assert.match(serializePlanSteps([{ ...createPlanStep({ name: 'x', path: '/' }), query: [{ key: '', value: 'orphan' }] }], ['GET']).errors[0].query, /参数名不能为空/)
})

test('serialized steps contain only the backend contract and preserve JSON scalar bodies', () => {
  const step = createPlanStep({ name: '读取', method: 'GET', path: '/health?a=1&a=2', expected_status: 204, headers: { Accept: 'application/json' }, body: false })
  const result = serializePlanSteps([step], ['GET'])
  assert.deepEqual(result.value, [{ name: '读取', method: 'GET', path: '/health?a=1&a=2', expected_status: 204, headers: { Accept: 'application/json' }, body: false }])
  assert.deepEqual(Object.keys(result.value[0]).sort(), ['body', 'expected_status', 'headers', 'method', 'name', 'path'])
})

test('step validation covers method, raw path, status and safe HTTP headers', () => {
  const step = createPlanStep({ name: '', method: 'POST', path: '/bad path#hash', expected_status: 0 })
  step.headers = [{ id: 'a', key: 'Bad Header', value: 'x' }]
  const errors = serializePlanSteps([step], ['GET']).errors[0]
  assert.match(errors.name, /步骤名称/)
  assert.match(errors.method, /未获/)
  assert.match(errors.path, /空格、#/)
  assert.match(errors.status, /100 到 599/)
  assert.match(errors.headers, /HTTP token/)
  step.headers = [{ id: 'a', key: 'X-Test', value: 'first\r\nsecond' }]
  assert.match(serializePlanSteps([step], ['GET']).errors[0].headers, /换行/)
})

test('body modes preserve drafts and JSON errors include a parse reason with location when supplied by runtime', () => {
  const step = createPlanStep({ name: 'draft', path: '/' }); step.bodyMode = 'json'; step.bodyText = '{\n  "x":\n}'
  assert.equal(clonePlanStep(step).bodyText, step.bodyText)
  assert.match(serializePlanSteps([step], ['GET']).errors[0].body, /JSON/)
  assert.equal(formatJsonText('null'), 'null')
  assert.equal(formatJsonText('0'), '0')
  assert.match(jsonErrorMessage('{\n}', new SyntaxError('Unexpected token } in JSON at position 2')), /第 2 行，第 1 列/)
  assert.match(jsonErrorMessage('{\n  "x":', new SyntaxError('Unexpected end of JSON input')), /第 2 行，第 7 列/)
  assert.equal(previewStepUrl({ base_url: 'https://api.example.test/' }, createPlanStep({ path: '/v1?a=中文' })), 'https://api.example.test/v1?a=%E4%B8%AD%E6%96%87')
})

test('editor exposes stable browser selectors and dirty close contract', async () => {
  const source = await readFile(new URL('../src/views/perf-testing/PerformancePlanEditor/PerformancePlanEditor.vue', import.meta.url), 'utf8')
  for (const id of ['performance-plan-editor', 'plan-name', 'plan-target', 'plan-target-refresh', 'plan-target-error', 'plan-target-empty', 'plan-step-list', 'step-name', 'step-method', 'step-path', 'step-query-rows', 'step-header-rows', 'step-body-json', 'step-expected-status', 'step-url-preview', 'plan-save']) assert.match(source, new RegExp(`data-testid="${id}"`))
  assert.match(source, /emit\('request-close', snapshot\(\) !== initialSnapshot\.value\)/)
  assert.match(source, /props\.saving\s*\|\|\s*props\.targetsLoading\s*\|\|\s*Boolean\(props\.targetError\)\s*\|\|\s*!selectedTarget\.value/)
})

test('empty query rows and numeric JSON overflow never silently change requests', () => {
  assert.equal(joinPathAndQuery('/', [{ key: '', value: '' }]), '/')
  const step = createPlanStep({ name: '验证', path: '/' })
  step.bodyMode = 'json'
  for (const text of ['null', 'false', '0', '[1,2]', '{"value":0}']) {
    step.bodyText = text
    assert.deepEqual(serializePlanSteps([step], ['GET']).value[0].body, JSON.parse(text))
  }
  step.bodyText = '1e999'
  assert.match(serializePlanSteps([step], ['GET']).errors[0].body, /有限数值/)
  assert.throws(() => formatJsonText('1e999'), /有限数值/)
  assert.match(jsonErrorMessage('{\n', new SyntaxError('Unexpected end of JSON input')), /第 2 行，第 1 列/)
})

test('headers retain all literal keys and match node restrictions', () => {
  const step = createPlanStep({ name: '请求头', path: '/' })
  step.headers = [{ key: '__proto__', value: 'literal' }]
  assert.equal(JSON.parse(JSON.stringify(serializePlanSteps([step], ['GET']).value[0].headers)).__proto__, 'literal')
  step.headers = [{ key: 'X-A', value: '1' }, { key: 'x-a', value: '2' }]
  assert.match(serializePlanSteps([step], ['GET']).errors[0].headers, /重复/)
  step.headers = [{ key: 'Host', value: 'example.test' }]
  assert.match(serializePlanSteps([step], ['GET']).errors[0].headers, /请求客户端控制/)
})
