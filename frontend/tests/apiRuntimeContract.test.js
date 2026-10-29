import test from 'node:test'
import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { API_DYNAMIC_VARIABLES, apiCompatibilityIssue } from '../src/utils/apiRuntimeContract.js'

test('runtime insertion offers usable values rather than executable functions', () => {
  assert.deepEqual(API_DYNAMIC_VARIABLES.flatMap(group => group.items.map(item => item.value)), ['${timestamp_ns}', '${uuid4}'])
  assert.equal(apiCompatibilityIssue({config: {variables: {name: 'run_${timestamp_ns}_${uuid4}'}}, teststeps: [{request: {url: '/items/${id}'}}]}), '')
})

test('unsupported stored hooks are reported without removing or changing their content', () => {
  for (const value of [
    {config: {setup_hooks: []}},
    {teststeps: [{teardown_hooks: ['${cleanup()}'], request: {url: '/items'}}]},
    {teststeps: [{request: {hooks: ['custom']}}]},
    {functions: {sign: 'custom'}},
  ]) {
    const before = JSON.stringify(value)
    assert.match(apiCompatibilityIssue(value), /旧脚本钩子或函数表达式/)
    assert.equal(JSON.stringify(value), before)
  }
})

test('legacy function templates are detected in nested request and variable values', () => {
  assert.match(apiCompatibilityIssue({config: {variables: {id: '${get_uuid()}'}}}), /不支持/)
  assert.match(apiCompatibilityIssue({teststeps: [{request: {json: {value: '${get_random_int(1, 9)}'}}}]}), /不支持/)
  assert.equal(apiCompatibilityIssue({teststeps: [{request: {json: {functions: 'normal business data', hooks: []}}}]}), '')
})

test('every lazy route import matches the actual file spelling, including on Linux', async () => {
  const router = await readFile(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const imports = [...router.matchAll(/import\(['"]@\/([^'"]+)['"]\)/g)].map(match => match[1])
  assert.ok(imports.length > 20)
  for (const path of imports) {
    let parent = new URL('../src/', import.meta.url)
    for (const segment of path.split('/')) {
      assert.ok((await readdir(parent)).includes(segment), `Route file casing mismatch: ${path}`)
      parent = new URL(segment + '/', parent)
    }
  }
})
