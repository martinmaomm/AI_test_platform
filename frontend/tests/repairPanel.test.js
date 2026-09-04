import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { nextTick, reactive } from 'vue'
import { parse } from '@vue/compiler-sfc'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

async function createRepairHarness(repair) {
  const token = `repair-panel-${Math.random()}`
  const emitted = []
  globalThis[token] = { emitted, props: reactive({ repair, busy: false, applying: false, discarding: false, repairExecution: null, repairExecutionLoading: false, selectedExecutionId: null }) }
  const vueModule = dataModule(`export { computed } from ${JSON.stringify(import.meta.resolve('vue'))}`)
  const elementModule = dataModule(`export const ElMessage = { error: () => {} }; export const ElMessageBox = { confirm: async () => true }`)
  const componentModule = dataModule('export default {}')
  const source = await readFile(new URL('../src/components/webui-generation/RepairPanel.vue', import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content
  const module = await import(dataModule(`
    const token = ${JSON.stringify(token)}
    const emitted = globalThis[token].emitted
    const defineProps = () => globalThis[token].props
    const defineEmits = () => (...value) => emitted.push(value)
    ${script}
    export const hooks = {
      phaseLabel: () => phaseLabel.value,
      hasCandidate: () => hasCandidate.value,
      hasDiscardableCandidate: () => hasDiscardableCandidate.value,
      attemptStatus: value => attemptExecutionStatus(value),
      attemptLabel: value => executionStatusLabel(attemptExecutionStatus(value)),
      attemptType: value => attemptTagType(attemptExecutionStatus(value)),
      attemptFailure,
      hasExecutionId,
      isSelectedAttempt,
      selectedExecutionTitle: () => selectedExecutionTitle.value,
      requestExecutionDetails,
      requestApply: () => requestApply(),
      requestDiscard: () => requestDiscard(),
      blockerText,
      setRepair: value => { globalThis[token].props.repair = value },
      setSelectedExecutionId: value => { globalThis[token].props.selectedExecutionId = value },
      emitted
    }
  `.replace("from 'vue'", `from '${vueModule}'`)
    .replace("from 'element-plus'", `from '${elementModule}'`)
    .replace("from '@/components/WebUITestCaseExecutionDetail.vue'", `from '${componentModule}'`)))
  return module.hooks
}

test('RepairPanel maps backend repair fields to Chinese using execution_status only', async () => {
  const hooks = await createRepairHarness({ phase: 'analyzing' })
  assert.equal(hooks.phaseLabel(), '分析失败原因')
  assert.equal(hooks.attemptStatus({ execution_status: 'passed' }), 'passed')
  assert.equal(hooks.attemptLabel({ execution_status: 'passed' }), '执行通过')
  assert.equal(hooks.attemptStatus({ execution_status: 'not_run' }), 'not_run')
  assert.equal(hooks.attemptLabel({ execution_status: 'not_run' }), '未执行浏览器验证')
  assert.equal(hooks.attemptType({ execution_status: 'not_run' }), 'warning')
  assert.equal(hooks.attemptFailure({ summary: '候选登录断言失败' }), '候选登录断言失败')
  assert.equal(hooks.blockerText({ message: '目标站点暂不可访问' }), '目标站点暂不可访问')
  assert.equal(hooks.blockerText('账号无权限'), '账号无权限')

  hooks.setRepair({ phase: 'validating' })
  await nextTick()
  assert.equal(hooks.phaseLabel(), '验证候选脚本')
})

test('RepairPanel only emits apply after a complete candidate is present and confirmed', async () => {
  const hooks = await createRepairHarness({ status: 'candidate_ready', candidate_hash: 'sha256-missing', candidate_script: '' })
  assert.equal(hooks.hasCandidate(), false)
  await hooks.requestApply()
  assert.deepEqual(hooks.emitted, [])

  hooks.setRepair({ status: 'candidate_ready', candidate_hash: 'sha256-candidate', candidate_script: 'async def run(page):\n    pass' })
  await nextTick()
  assert.equal(hooks.hasCandidate(), true)
  await hooks.requestApply()
  assert.deepEqual(hooks.emitted, [['apply', 'sha256-candidate']])
})

test('RepairPanel permits discarding a hash-only candidate so the original draft is never locked indefinitely', async () => {
  const hooks = await createRepairHarness({ status: 'candidate_ready', candidate_hash: 'sha256-candidate', candidate_script: '' })
  assert.equal(hooks.hasCandidate(), false)
  assert.equal(hooks.hasDiscardableCandidate(), true)
  await hooks.requestDiscard()
  assert.deepEqual(hooks.emitted, [['discard', 'sha256-candidate']])
})

test('RepairPanel exposes execution details only for attempts that have an execution_id', async () => {
  const first = { execution_id: 31, execution_status: 'failed' }
  const second = { execution_id: 32, execution_status: 'passed' }
  const hooks = await createRepairHarness({ status: 'candidate_passed', attempts: [first, second] })

  assert.equal(hooks.hasExecutionId(first), true)
  assert.equal(hooks.hasExecutionId({ execution_status: 'error' }), false)
  hooks.requestExecutionDetails({ execution_status: 'error' })
  hooks.requestExecutionDetails(first)
  hooks.requestExecutionDetails(second)
  assert.deepEqual(hooks.emitted, [['view-execution', 31], ['view-execution', 32]])

  hooks.setSelectedExecutionId(31)
  await nextTick()
  assert.equal(hooks.isSelectedAttempt(first), true)
  assert.equal(hooks.isSelectedAttempt(second), false)
  assert.equal(hooks.selectedExecutionTitle(), '第 1 轮执行详情')

  hooks.setSelectedExecutionId('32')
  await nextTick()
  assert.equal(hooks.isSelectedAttempt(second), true)
  assert.equal(hooks.selectedExecutionTitle(), '第 2 轮执行详情')
})
