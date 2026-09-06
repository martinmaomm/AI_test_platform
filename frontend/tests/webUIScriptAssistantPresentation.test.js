import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  assistantList,
  assistantListParams,
  assistantAttemptStatusLabel,
  assistantPanelContext,
  assistantsForContext,
  canApplyRepairCandidate,
  canContinueCandidate,
  canVerifyCandidate,
  expandedAssistantRowIds,
  isAssistantActive,
  repairAdoptionState,
  verificationLabel
} from '../src/composables/webUIScriptAssistantPresentation.js'

test('assistant list uses only the approved context identifiers', () => {
  assert.deepEqual(assistantListParams({ testCaseId: 7 }), { test_case_id: 7 })
  assert.deepEqual(assistantListParams({ executionId: 11, suiteCaseId: 19, testCaseId: 7 }), { test_case_id: 7, execution_id: 11, suite_case_id: 19 })
  assert.equal(Object.hasOwn(assistantListParams({ executionId: 11, suiteCaseId: 19 }), 'test_case_id'), false)
})

test('assistant panel passes an explicit mode for both component entry contexts', async () => {
  assert.deepEqual(assistantPanelContext({ testCaseId: 7 }, null), { mode: 'edit', testCaseId: 7 })
  assert.deepEqual(assistantPanelContext(null, { executionId: 11, suiteCaseId: 19 }), { mode: 'repair', executionId: 11, suiteCaseId: 19 })
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /assistantPanelContext\(props\.editContext, props\.repairContext\)/)
  assert.match(source, /@click="\(\) => loadRecent\(\)"/)
})

test('assistant envelopes and lifecycle never call an unverified candidate passed', () => {
  assert.deepEqual(assistantList({ data: { results: [{ id: 'a' }] } }), [{ id: 'a' }])
  assert.equal(isAssistantActive({ status: 'running' }), true)
  assert.equal(isAssistantActive({ status: 'candidate_ready' }), false)
  assert.equal(verificationLabel({ status: 'unverified' }), '尚未实际验证')
  assert.equal(verificationLabel({ status: 'passed' }), '实际验证通过')
})

test('assistant recovery stays in its mode and candidate controls enforce active, blocker and applied boundaries', () => {
  const edit = { id: 'edit', mode: 'edit' }
  const repair = { id: 'repair', mode: 'repair' }
  assert.deepEqual(assistantsForContext([edit, repair], { mode: 'edit' }), [edit])
  assert.deepEqual(assistantListParams({ mode: 'repair', executionId: 4 }), { mode: 'repair', execution_id: 4 })

  const readyRepair = { mode: 'repair', status: 'candidate_ready', candidate_hash: 'hash', candidate_script: 'async def run(page): pass', blockers: [], adoption: { kind: 'automatic', can_apply: true, requires_acknowledge_review: false } }
  assert.equal(canContinueCandidate(readyRepair), true)
  assert.equal(canVerifyCandidate(readyRepair), true)
  assert.equal(canApplyRepairCandidate(readyRepair), true)
  assert.equal(canContinueCandidate({ ...readyRepair, status: 'running' }), false)
  assert.equal(canVerifyCandidate({ ...readyRepair, blockers: ['unsafe'] }), false)
  assert.equal(canApplyRepairCandidate({ ...readyRepair, status: 'applied' }), false)
  assert.equal(canVerifyCandidate({ ...readyRepair, status: 'failed' }), false)
  assert.equal(canApplyRepairCandidate({ ...readyRepair, status: 'cancelled' }), false)
})

test('scope-only repair can be manually saved but never automatically verified', () => {
  const manualRepair = {
    mode: 'repair', status: 'candidate_ready', candidate_hash: 'hash',
    candidate_script: 'async def run(page): pass',
    blockers: [{ code: 'REPAIR_SCOPE_CHANGED' }],
    adoption: { kind: 'manual_review', can_apply: true, requires_acknowledge_review: true }
  }
  assert.equal(canVerifyCandidate(manualRepair), false)
  assert.equal(canApplyRepairCandidate(manualRepair), true)
  assert.deepEqual(repairAdoptionState(manualRepair), manualRepair.adoption)
  assert.equal(canApplyRepairCandidate({ ...manualRepair, adoption: { kind: 'unavailable', can_apply: false, requires_acknowledge_review: false } }), false)
})

test('opening a suite repair row preserves existing expanded rows and makes the chosen row visible', () => {
  assert.deepEqual(expandedAssistantRowIds(['3'], 9, true), ['3', '9'])
  assert.deepEqual(expandedAssistantRowIds(['3', '9'], 9, true), ['3', '9'])
  assert.deepEqual(expandedAssistantRowIds(['3', '9'], 9, false), ['3'])
})

test('assistant attempt statuses remain Chinese while the full candidate stays separately accessible', async () => {
  assert.equal(assistantAttemptStatusLabel('passed'), '执行通过')
  assert.equal(assistantAttemptStatusLabel('failed'), '执行失败')
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /候选修改差异/)
  assert.match(source, /完整候选代码/)
  assert.match(source, /candidateView === 'full' \? assistant\.candidate_script/)
  assert.match(source, /人工确认并保存/)
  assert.match(source, /保存不会运行/)
  assert.match(source, /acknowledge_review: frozen\.acknowledgeReview/)
  assert.match(source, /已人工确认保存/)
  assert.match(source, /const frozen = \{[\s\S]*candidateHash: assistant\.value\.candidate_hash/)
  assert.match(source, /候选已变化，请刷新后重新确认。/)
})

test('assistant panel supports the first edit message and keeps repair out of the edit-only message path', async () => {
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /isEdit && !assistant/)
  assert.match(source, /placeholder="说明你希望如何修改脚本…"/)
  assert.match(source, /sendMessage\(true\)/)
  assert.match(source, /<section v-if="isEdit" class="conversation-section">/)
})

test('assistant attempt details use their own clipped, scrollable viewport', async () => {
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /\.attempt-section :deep\(\.test-report-container\) \{ block-size:min\(480px, 52vh\); min-height:0; max-block-size:min\(480px, 52vh\);[^}]*overflow:hidden/)
  assert.match(source, /\.attempt-section :deep\(\.report-content\) \{ block-size:100%; min-height:0; overflow:hidden; \}/)
  assert.match(source, /\.attempt-section :deep\(\.main-content\) \{ block-size:100%; min-height:0; max-height:none; overflow-x:hidden; overflow-y:auto; \}/)
})

test('attempt detail requests are guarded when switching session or unmounting', async () => {
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /onBeforeUnmount\(clearAttempt\)/)
  assert.match(source, /assistant\.value\?\.id\], clearAttempt, \{ flush: 'sync' \}/)
  assert.match(source, /if \(!current\(\)\) return\s+const detail/)
  assert.match(source, /version === attemptRequestVersion/)
})
