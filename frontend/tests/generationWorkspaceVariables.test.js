import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { nextTick, reactive } from 'vue'
import { parse } from '@vue/compiler-sfc'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let harnessId = 0

test('生成工作区仅一张变量表，用例编辑和工作区均移除开关且普通值不再被密码化', async () => {
  const workspace = await readFile(new URL('../src/components/webui-generation/GenerationWorkspace.vue', import.meta.url), 'utf8')
  const caseEditor = await readFile(new URL('../src/components/WebUICaseEditDetail.vue', import.meta.url), 'utf8')
  assert.equal((workspace.match(/<el-table\s/g) || []).length, 1)
  for (const source of [workspace, caseEditor]) {
    assert.doesNotMatch(source, /<el-switch|label="必填"|label="敏感"|敏感值可标记/)
    assert.match(source, /os\.getenv\("VARIABLE_NAME"\)/)
    assert.match(source, /:show-password="row\.is_secret"/)
    assert.doesNotMatch(source, /\s+show-password(?:\s|\/>)/)
  }
})

const draft = (generationId = 'g1', revision = 1, variables = []) => ({
  generationId, revision, script_draft: 'async def run(page):\n    pass', variables, dirty: false
})

async function createWorkspaceHarness(initialDraft = draft()) {
  const token = `workspace-variables-${++harnessId}`
  const emitted = []
  globalThis[token] = {
    emitted,
    props: reactive({
      generation: { id: initialDraft.generationId, workspace: { revision: initialDraft.revision, verification: {}, repair: {} } },
      draft: initialDraft,
      busy: false,
      draftSaving: false,
      debugging: false,
      debugExecution: null,
      debugExecutionLoading: false
    })
  }

  const vueModule = dataModule(`
    export { computed, reactive, watch } from ${JSON.stringify(import.meta.resolve('vue'))}
  `)
  const elementModule = dataModule(`
    export const ElMessage = { success: () => {}, warning: () => {}, error: () => {} }
    export const ElMessageBox = { confirm: async () => true }
  `)
  const source = await readFile(new URL('../src/components/webui-generation/GenerationWorkspace.vue', import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content
  const presentationModule = JSON.stringify(new URL('../src/composables/webUIScriptGenerationPresentation.js', import.meta.url).href)
  const componentModule = dataModule('export default {}')
  const moduleSource = `
    const token = ${JSON.stringify(token)}
    const emitted = globalThis[token].emitted
    const defineProps = () => globalThis[token].props
    const defineEmits = () => (...value) => emitted.push(value)
    ${script}
    export const hooks = {
      variables: () => form.variables,
      runtimeOverrides: () => runtimeOverrides,
      emitted,
      emitDraft: () => emitDraft(),
      requestDebug: () => requestDebug(),
      setDraft: value => { globalThis[token].props.draft = value }
    }
  `
    .replace("from 'vue'", `from '${vueModule}'`)
    .replace("from 'element-plus'", `from '${elementModule}'`)
    .replace("from '@/components/MonacoEditor.vue'", `from '${componentModule}'`)
    .replace("from '@/components/WebUITestCaseExecutionDetail.vue'", `from '${componentModule}'`)
    .replace("from '@/composables/webUIScriptGenerationPresentation'", `from ${presentationModule}`)
  const module = await import(dataModule(moduleSource))
  return module.hooks
}

test('同一任务跨 revision 保留同名覆盖值；改名、删除和切换任务不会串值', async () => {
  const hooks = await createWorkspaceHarness(draft('g1', 1, [
    { name: 'ACCOUNT', value: 'saved-default', is_secret: false, required: false, description: '' }
  ]))
  hooks.runtimeOverrides().ACCOUNT = 'one-time'

  hooks.setDraft(draft('g1', 2, [{ name: 'ACCOUNT', value: 'saved-default', is_secret: false, required: false, description: '' }]))
  await nextTick()
  assert.equal(hooks.runtimeOverrides().ACCOUNT, 'one-time')

  hooks.variables()[0].name = 'RENAMED_ACCOUNT'
  await nextTick()
  assert.equal(hooks.runtimeOverrides().ACCOUNT, undefined)
  assert.equal(hooks.runtimeOverrides().RENAMED_ACCOUNT, '')

  hooks.variables().splice(0, 1)
  await nextTick()
  assert.deepEqual({ ...hooks.runtimeOverrides() }, {})

  hooks.setDraft(draft('g2', 1, [{ name: 'ACCOUNT', value: 'saved-default', is_secret: false, required: false, description: '' }]))
  await nextTick()
  assert.equal(hooks.runtimeOverrides().ACCOUNT, '')
})

test('默认值进入草稿 payload；本次覆盖进入 debug payload 后立即清空，secret metadata 保留', async () => {
  const hooks = await createWorkspaceHarness(draft('g1', 1, [
    { name: 'ACCOUNT', value: 'saved-default', is_secret: false, required: false, description: '普通变量' },
    { name: 'UI_TEST_PASSWORD', value: '', is_secret: true, required: true, description: '自动识别的密码变量' }
  ]))
  hooks.variables()[0].value = 'new-default'
  hooks.emitDraft()
  const draftPayload = hooks.emitted.at(-1)[1]
  assert.equal(draftPayload.variables[0].value, 'new-default')
  assert.deepEqual(draftPayload.variables[1], {
    name: 'UI_TEST_PASSWORD', value: '', is_secret: true, required: true, description: '自动识别的密码变量'
  })

  hooks.runtimeOverrides().ACCOUNT = 'one-time-account'
  hooks.runtimeOverrides().UI_TEST_PASSWORD = 'one-time-password'
  await hooks.requestDebug()
  const debugPayload = hooks.emitted.find(([event]) => event === 'debug')[1]
  assert.deepEqual(debugPayload, [
    { name: 'ACCOUNT', value: 'one-time-account' },
    { name: 'UI_TEST_PASSWORD', value: 'one-time-password' }
  ])
  assert.deepEqual({ ...hooks.runtimeOverrides() }, { ACCOUNT: '', UI_TEST_PASSWORD: '' })
})

test('带空格的变量名使用同一原始覆盖键，提交时归一且不会发送 undefined 覆盖值', async () => {
  const hooks = await createWorkspaceHarness(draft('g1', 1, [
    { name: '  ACCOUNT  ', value: 'saved-default', is_secret: false, required: false, description: '' },
    { name: 'UNSET', value: 'saved-default', is_secret: false, required: false, description: '' }
  ]))
  hooks.runtimeOverrides()['  ACCOUNT  '] = 'one-time-account'
  hooks.runtimeOverrides().UNSET = undefined

  await hooks.requestDebug()
  const debugPayload = hooks.emitted.find(([event]) => event === 'debug')[1]
  assert.deepEqual(debugPayload, [{ name: 'ACCOUNT', value: 'one-time-account' }])
  assert.deepEqual({ ...hooks.runtimeOverrides() }, { '  ACCOUNT  ': '', UNSET: '' })
})
