import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'
import { nextTick as vueNextTick, reactive as vueReactive } from 'vue'

const dataModule = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let harnessCounter = 0

const extractScriptSetup = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const parsed = parse(source)
  if (!parsed.descriptor.scriptSetup?.content) {
    throw new Error(`No <script setup> found in ${path}`)
  }
  return parsed.descriptor.scriptSetup.content
}

const generationRecord = (scriptDraft = 'rev1', revision = 1) => ({
  id: 'g1',
  status: 'ready',
  workspace: {
    revision,
    variables: [],
    verification: { status: 'unverified' },
    repair: { status: 'idle', count: 0 }
  },
  script_draft: scriptDraft
})

const draftRecord = (revision, scriptDraft, variables = [], dirty = false) => ({
  generationId: 'g1',
  revision,
  script_draft: scriptDraft,
  variables,
  dirty
})

const createMonacoHarness = async (initialValue = 'rev1') => {
  const token = `monaco-sync-h${++harnessCounter}`
  const emitted = []
  const props = vueReactive({
    value: initialValue,
    language: 'python',
    theme: 'vs-dark',
    readOnly: false,
    height: '220px',
    completionVariables: []
  })
  globalThis[token] = { emitted, props }

  const vueModule = dataModule(`
    import { ref as vueRef, watch as vueWatch } from ${JSON.stringify(import.meta.resolve('vue'))}
    export const ref = (initial) => vueRef(initial === undefined || initial === null ? {} : initial)
    export { vueWatch as watch }
    export const onMounted = (callback) => callback()
    export const onBeforeUnmount = () => {}
  `)
  const monacoModule = dataModule(`
    const token = ${JSON.stringify(token)}
    const state = globalThis[token]
    const CompletionItemKind = { Property: 1, Method: 2, Module: 3, Class: 4, Variable: 5 }
    const CompletionItemInsertTextRule = { InsertAsSnippet: 1 }
    class Range {
      constructor(startLineNumber, startColumn, endLineNumber, endColumn) {
        this.startLineNumber = startLineNumber
        this.startColumn = startColumn
        this.endLineNumber = endLineNumber
        this.endColumn = endColumn
      }
    }
    class FakeEditor {
      constructor(value = '') {
        this._value = value
        this._changeHandlers = []
      }
      onDidChangeModelContent(callback) {
        this._changeHandlers.push(callback)
        return { dispose: () => {} }
      }
      _emit() {
        this._changeHandlers.forEach(callback => callback())
      }
      setValue(value) {
        this._value = value || ''
        this._emit()
      }
      getValue() {
        return this._value
      }
      getModel() { return {} }
      updateOptions() {}
      layout() {}
      focus() {}
      dispose() {}
    }
    export const languages = {
      registerCompletionItemProvider: () => ({ dispose: () => {} }),
      CompletionItemKind,
      CompletionItemInsertTextRule
    }
    export const editor = {
      create: (_container, options = {}) => {
        const instance = new FakeEditor(options.value || '')
        state.editorInstance = instance
        return instance
      },
      setModelLanguage: () => {},
      setTheme: () => {}
    }
    export { Range }
  `)
  const scriptSource = await extractScriptSetup('../src/components/MonacoEditor.vue')
  const source = `
    const token = ${JSON.stringify(token)}
    const emitted = globalThis[token].emitted
    const defineExpose = (value) => { globalThis[token].exposed = value }
    const defineProps = () => globalThis[token].props
    const defineEmits = () => (...value) => emitted.push(value)
    ${scriptSource}
    export const __testHooks = {
      emitted,
      setValue: (value) => globalThis[token].exposed?.setValue(value),
      setValueFromParent: (value) => { globalThis[token].props.value = value },
      triggerUserInput: (value) => globalThis[token].editorInstance?.setValue(value),
      getEditor: () => globalThis[token].editorInstance
    }
  `
    .replace("from 'vue'", `from '${vueModule}'`)
    .replace("from 'monaco-editor'", `from '${monacoModule}'`)

  const module = await import(dataModule(source))
  return module.__testHooks
}

const createWorkspaceHarness = async ({
  generation = generationRecord('rev1', 1),
  draft = draftRecord(1, 'rev1')
} = {}) => {
  const token = `workspace-sync-h${++harnessCounter}`
  const emitted = []
  const props = vueReactive({
    generation,
    draft,
    busy: false,
    draftSaving: false,
    debugging: false,
    debugExecution: null,
    debugExecutionLoading: false
  })
  globalThis[token] = { emitted, props }

  const vueModule = dataModule(`
    import { computed as vueComputed, reactive as vueReactive, watch as vueWatch } from ${JSON.stringify(import.meta.resolve('vue'))}
    export { vueComputed as computed, vueReactive as reactive, vueWatch as watch }
  `)
  const elementModule = dataModule(`
    export const ElMessage = { success: () => {}, warning: () => {}, error: () => {} }
    export const ElMessageBox = { confirm: async () => true }
  `)
  const monacoModule = dataModule('export default {}')
  const executionModule = dataModule('export default {}')
  const scriptSource = await extractScriptSetup('../src/components/webui-generation/GenerationWorkspace.vue')
  const presentationModule = JSON.stringify(new URL('../src/composables/webUIScriptGenerationPresentation.js', import.meta.url).href)
  const source = `
    const token = ${JSON.stringify(token)}
    const emitted = globalThis[token].emitted
    const defineProps = () => globalThis[token].props
    const defineEmits = () => (...value) => emitted.push(value)
    ${scriptSource}
    export const __testHooks = {
      runtimeVariables: () => runtimeVariables,
      getFormScript: () => form.script_draft,
      emitted,
      setDraft: (value) => { globalThis[token].props.draft = value },
      updateScript: (value) => updateScript(value),
      getDraft: () => globalThis[token].props.draft
    }
  `
    .replace("from 'vue'", `from '${vueModule}'`)
    .replace("from '@/components/MonacoEditor.vue'", `from '${monacoModule}'`)
    .replace("from '@/components/WebUITestCaseExecutionDetail.vue'", `from '${executionModule}'`)
    .replace("from '@/composables/webUIScriptGenerationPresentation'", `from ${presentationModule}`)
    .replace("from 'element-plus'", `from '${elementModule}'`)

  const module = await import(dataModule(source))
  return module.__testHooks
}

test('MonacoEditor 自动同步值不触发 update:value/change，用户输入会触发', async () => {
  const hooks = await createMonacoHarness('rev-1')
  hooks.setValueFromParent('rev-2')
  await vueNextTick()
  assert.equal(hooks.emitted.length, 0)
  hooks.triggerUserInput('user typed')
  assert.equal(hooks.emitted.length, 2)
  assert.equal(hooks.emitted[0][0], 'update:value')
  assert.equal(hooks.emitted[1][0], 'change')
  assert.equal(hooks.emitted[1][1], 'user typed')
})

test('MonacoEditor defineExpose setValue 保留原始语义', async () => {
  const hooks = await createMonacoHarness('rev')
  hooks.setValue('exposed-value')
  assert.equal(hooks.emitted.length, 2)
  hooks.setValue('silent-value')
  assert.equal(hooks.emitted.length, 4)
})

test('GenerationWorkspace 连续 rev1→rev2→rev3 同步且同 revision 内容变更也会同步，不会产生 update-draft', async () => {
  const hooks = await createWorkspaceHarness({
    generation: generationRecord('rev-1', 1),
    draft: draftRecord(1, 'rev-1', [
      { name: 'RUN', value: '', is_secret: false, required: false, description: '' }
    ])
  })
  const runtimeVars = hooks.runtimeVariables()
  runtimeVars[0].value = 'runtime-override'
  assert.equal(hooks.getFormScript(), 'rev-1')
  assert.equal(hooks.emitted.length, 0)

  hooks.setDraft(draftRecord(2, 'rev-2', [{ name: 'RUN', value: '', is_secret: false, required: false, description: '' }], false))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'rev-2')
  await vueNextTick()
  assert.equal(hooks.runtimeVariables()[0].value, 'runtime-override')

  hooks.setDraft(draftRecord(3, 'rev-3', [{ name: 'RUN', value: '', is_secret: false, required: false, description: '' }], false))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'rev-3')
  assert.equal(hooks.runtimeVariables()[0].value, 'runtime-override')

  hooks.setDraft(draftRecord(3, 'rev-3-fresh', [{ name: 'RUN', value: '', is_secret: false, required: false, description: '' }], false))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'rev-3-fresh')
  assert.equal(hooks.runtimeVariables()[0].value, 'runtime-override')
  assert.equal(hooks.emitted.length, 0)
})

test('GenerationWorkspace 真实输入会 emit update-draft；dirty 时不会被后端脏更新覆盖', async () => {
  const hooks = await createWorkspaceHarness({
    generation: generationRecord('rev-1', 1),
    draft: draftRecord(1, 'rev-1')
  })
  hooks.updateScript('local user script')
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'local user script')
  assert.equal(hooks.emitted.at(-1)[0], 'update-draft')

  const emitCountAfterInput = hooks.emitted.length
  hooks.updateScript('local user script')
  assert.equal(hooks.emitted.length, emitCountAfterInput)

  hooks.setDraft(draftRecord(2, 'backend-update', [], true))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'local user script')

  hooks.setDraft(draftRecord(2, 'backend-clean-update', [], false))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'backend-clean-update')
})

test('GenerationWorkspace 首次挂载已有本地编辑时显示草稿，不把它当作待拒绝的更新', async () => {
  const hooks = await createWorkspaceHarness({
    draft: draftRecord(1, 'existing local edits', [], true)
  })
  assert.equal(hooks.getFormScript(), 'existing local edits')
  assert.equal(hooks.emitted.length, 0)
  hooks.setDraft(draftRecord(2, 'incoming server text', [], true))
  await vueNextTick()
  assert.equal(hooks.getFormScript(), 'existing local edits')
})

test('GenerationWorkspace 同一 generation 下同步新 script 内容保留本次覆盖变量值，generation 切换时清空覆盖值', async () => {
  const hooks = await createWorkspaceHarness({
    generation: generationRecord('rev-1', 1),
    draft: draftRecord(1, 'rev-1', [
      { name: 'RUN', value: '', is_secret: false, required: false, description: '' }
    ])
  })

  const runtimeVars = hooks.runtimeVariables()
  runtimeVars[0].value = 'runtime-override'
  await vueNextTick()

  hooks.setDraft(draftRecord(1, 'rev-1-fresh', [{ name: 'RUN', value: '', is_secret: false, required: false, description: '' }], false))
  await vueNextTick()
  assert.equal(hooks.runtimeVariables()[0].value, 'runtime-override')

  hooks.setDraft({ ...draftRecord(2, 'rev-2', [{ name: 'RUN', value: '', is_secret: false, required: false, description: '' }], false), generationId: 'g2' })
  await vueNextTick()
  assert.equal(hooks.runtimeVariables()[0].value, '')
})
