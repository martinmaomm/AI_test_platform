import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const filename = '../src/components/webui-generation/GenerationInputPanel.vue'
const original = await readFile(new URL(filename, import.meta.url), 'utf8')
// Expose handlers only in the compiled test copy. Render the actual SFC template.
const { descriptor } = parse(original.replace('</script>', 'defineExpose({ form, submit, handleCancel })\n</script>'))
const source = compileScript(descriptor, { id: 'generation-input-test', inlineTemplate: true }).content
  .replaceAll("from 'vue'", `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('from "vue"', `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace("from 'element-plus'", `from ${JSON.stringify(dataModule('export const ElMessage = {warning: () => {}}'))}`)
  .replace("from '@/composables/webUIScriptGenerationPresentation'", `from ${JSON.stringify(new URL('../src/composables/webUIScriptGenerationPresentation.js', import.meta.url).href)}`)
  .replace("from '@/composables/webuiExplorationTimeout'", `from ${JSON.stringify(new URL('../src/composables/webuiExplorationTimeout.js', import.meta.url).href)}`)
const { default: Panel } = await import(dataModule(source))

const createHarness = async (props = {}) => {
  const emitted = []
  let hooks
  const component = {
    ...Panel,
    setup(props, ctx) {
      const render = Panel.setup(props, { ...ctx, expose: value => { hooks = value } })
      hooks.form.description = '离线测试描述'
      return render
    }
  }
  const app = createSSRApp(component, {
    modelConfigs: [{ id: 1, model_name: 'fixture' }],
    ...props,
    onSubmit: payload => emitted.push(['submit', payload]),
    onCancel: () => emitted.push(['cancel'])
  })
  app.component('el-button', {
    props: { disabled: Boolean, loading: Boolean, nativeType: String },
    setup: (props, { slots }) => () => h('button', {
      disabled: Boolean(props.disabled || props.loading), type: props.nativeType || 'button'
    }, slots.default?.())
  })
  for (const name of ['el-alert', 'el-select', 'el-form-item', 'el-input', 'el-input-number', 'el-form', 'el-collapse', 'el-collapse-item', 'el-option']) {
    app.component(name, { setup: (_props, { slots }) => () => h('div', slots.default?.()) })
  }
  const html = await renderToString(app)
  return { html, hooks, emitted }
}

test('空闲时可分析生成，但不能触发取消', async () => {
  const { html, hooks, emitted } = await createHarness()
  assert.match(html, /<button(?![^>]*disabled)[^>]*>分析并生成脚本<\/button>/)
  assert.doesNotMatch(html, /取消生成/)
  hooks.handleCancel()
  assert.equal(emitted.length, 0)
  hooks.submit()
  assert.equal(emitted[0][0], 'submit')
})

test('真正生成期间保留可点击取消按钮，阻止重复提交', async () => {
  const { html, hooks, emitted } = await createHarness({ generationActive: true })
  assert.match(html, /<button(?![^>]*disabled)[^>]*>取消生成<\/button>/)
  assert.doesNotMatch(html, /分析并生成脚本/)
  hooks.submit()
  assert.equal(emitted.length, 0)
  hooks.handleCancel()
  assert.deepEqual(emitted, [['cancel']])
})

for (const [label, props] of [
  ['调试或保存期间', { busy: true }],
  ['创建请求期间', { submitting: true }],
  ['取消请求期间', { cancelling: true }]
]) {
  test(`${label}保持禁用的分析并生成脚本按钮，回车或直接调用不能绕过`, async () => {
    const { html, hooks, emitted } = await createHarness(props)
    assert.match(html, /<button[^>]*disabled[^>]*>分析并生成脚本<\/button>/)
    assert.doesNotMatch(html, /取消生成/)
    hooks.submit()
    hooks.handleCancel()
    assert.equal(emitted.length, 0)
  })
}

test('暂停任务保持原暂停提示，生成取消中禁止重复取消', async () => {
  const paused = await createHarness({ paused: true })
  assert.match(paused.html, /<button[^>]*disabled[^>]*>请先处理当前暂停任务<\/button>/)
  paused.hooks.submit()
  paused.hooks.handleCancel()
  assert.equal(paused.emitted.length, 0)
  const cancelling = await createHarness({ generationActive: true, cancelling: true })
  assert.match(cancelling.html, /<button[^>]*disabled[^>]*>取消生成<\/button>/)
  cancelling.hooks.handleCancel()
  assert.equal(cancelling.emitted.length, 0)
})
