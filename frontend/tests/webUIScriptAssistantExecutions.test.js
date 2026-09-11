import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { assistantExecutionEntries } from '../src/composables/webUIScriptAssistantPresentation.js'

test('latest verification and its history row expose one execution entry', () => {
  const attempt = { execution_id: 7, execution_status: 'passed', has_screenshot: true }
  assert.deepEqual(assistantExecutionEntries({
    attempts: [attempt], verification: { execution_id: '7', status: 'passed' }
  }), [attempt])
})

test('multiple runs retain order and details while duplicate execution IDs are removed', () => {
  const first = { execution_id: 7, execution_status: 'failed', has_screenshot: true }
  const second = { execution_id: 8, execution_status: 'passed', has_screenshot: false }
  const input = { attempts: [first, { execution_id: '7' }, second], verification: { execution_id: 8, status: 'passed' } }
  const before = structuredClone(input)
  const entries = assistantExecutionEntries(input)
  assert.deepEqual(entries, [first, second])
  assert.deepEqual(input, before)
  assert.notEqual(entries[0], first)
})

test('verification without a matching history entry remains accessible', () => {
  const input = {
    attempts: [{ execution_id: 7, execution_status: 'failed' }],
    verification: { execution_id: 9, status: 'passed' }
  }
  const entries = assistantExecutionEntries(input)
  assert.deepEqual(entries.map(item => item.execution_id), [7, 9])
  assert.equal(entries[1].execution_status, 'passed')
  assert.equal(Boolean(entries[1].has_screenshot), false)
  assert.equal(assistantExecutionEntries({ verification: input.verification }).length, 1)
})

test('empty data and rows without execution IDs do not produce dead buttons', () => {
  for (const input of [null, {}, { attempts: null }, { attempts: [null, {}, { execution_id: 0 }], verification: { status: 'queued' } }]) {
    assert.deepEqual(assistantExecutionEntries(input), [])
  }
})

test('conversation actions sit together with independent accessible help and unchanged script sources', async () => {
  const panel = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  const candidateActions = panel.split('<div class="candidate-actions">')[1].split('</div>')[0]
  const conversation = panel.split('<section v-if="isEdit" class="conversation-section">')[1].split('</section>')[0]
  assert.doesNotMatch(candidateActions, /continueCandidate/)
  assert.match(conversation, /continueCandidate[\s\S]*aria-label="继续调整候选说明"[\s\S]*sendMessage\(false\)[\s\S]*aria-label="发送说明"/)
  assert.match(panel, /:trigger="\['hover', 'focus'\]"/)
  assert.match(panel, /以当前编辑器里的脚本为基础修改/)
  assert.match(panel, /以最新的候选脚本为基础继续修改/)
  assert.match(panel, /需先有候选，并填写具体修改要求/)
  assert.match(panel, /await sendMessage\(true\)/)
  assert.doesNotMatch(panel, /查看调试验证详情/)
  assert.match(panel, /v-for="\(attempt, index\) in executionEntries"/)
})

test('continuing requires an explicit message without affecting candidate adoption', async () => {
  const panel = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(panel, /:disabled="!canContinue \|\| !canSend"[^>]*@click="continueCandidate"/)
  assert.match(panel, /const sendMessage = async useCandidate => \{\s*if \(!messageText.value.trim\(\)\) return ElMessage.warning/)
  assert.match(panel, /const continueCandidate = async \(\) => \{\s*if \(!canContinue.value \|\| !canSend.value\) return/)
  assert.doesNotMatch(panel, /请在保留当前候选目标的前提下|未填写新要求时默认/)
  assert.match(panel, /:disabled="!canContinue \|\| acting" @click="applyToEditor"/)
})
