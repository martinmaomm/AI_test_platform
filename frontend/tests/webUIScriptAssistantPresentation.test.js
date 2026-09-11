import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer } from 'vite'
import { nextTick, ref } from 'vue'
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
  verifyActionState,
  verificationTagType,
  verificationLabel
} from '../src/composables/webUIScriptAssistantPresentation.js'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise })
  return { promise, resolve, reject }
}

const flush = async () => {
  await Promise.resolve()
  await Promise.resolve()
}

const withAssistantComposable = async callback => {
  const originalWarn = console.warn
  const previous = {
    document: globalThis.document,
    location: globalThis.location,
    window: globalThis.window
  }
  const location = { protocol: 'http:', host: 'localhost', pathname: '/', search: '', hash: '', href: 'http://localhost/', assign () {}, replace () {} }
  globalThis.location = location
  globalThis.window = { navigator: { userAgent: 'node' }, history: { state: null, replaceState () {}, pushState () {} }, location, addEventListener () {}, removeEventListener () {} }
  globalThis.document = { addEventListener () {}, removeEventListener () {}, querySelector () { return null }, createElement () { return { style: {}, setAttribute () {}, appendChild () {}, removeChild () {} } }, body: {} }
  const server = await createServer({ root: new URL('..', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom' })
  try {
    console.warn = (...args) => {
      if (String(args[0]).includes('onBeforeUnmount is called when there is no active component instance')) return
      originalWarn(...args)
    }
    const { useWebUIScriptAssistant } = await server.ssrLoadModule('/src/composables/useWebUIScriptAssistant.js')
    return await callback(useWebUIScriptAssistant)
  } finally {
    await server.close()
    console.warn = originalWarn
    globalThis.document = previous.document
    globalThis.location = previous.location
    globalThis.window = previous.window
  }
}

test('verification labels distinguish execution results and interrupted pending work', () => {
  assert.equal(verificationLabel({ status: 'incomplete' }), '已实际运行但验证不完整')
  assert.equal(verificationLabel({ status: 'stopped' }), '实际验证已停止')
  assert.equal(verificationLabel({ status: 'queued' }), '已排队等待实际验证')
  assert.equal(verificationLabel({ status: 'running' }, 'running'), '正在实际验证')
  assert.equal(verificationLabel({ status: 'running' }, 'cancelled'), '本次验证已取消')
  assert.equal(verificationLabel({ status: 'queued' }, 'failed'), '本次验证已中止')
  assert.equal(verificationTagType({ status: 'running' }, 'cancelled'), 'info')
  assert.equal(verificationTagType({ status: 'running' }, 'failed'), 'danger')
  assert.equal(verificationLabel({ status: 'failed' }, 'failed'), '实际验证失败')
  assert.equal(verificationLabel({ status: 'passed' }, 'applied'), '实际验证通过')
})

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
  assert.match(source, /@click="refreshRepairStatus">刷新修复状态/)
})

test('assistant envelopes and lifecycle never call an unverified candidate passed', () => {
  assert.deepEqual(assistantList({ data: { results: [{ id: 'a' }] } }), [{ id: 'a' }])
  assert.equal(isAssistantActive({ status: 'running' }), true)
  assert.equal(isAssistantActive({ status: 'candidate_ready' }), false)
  assert.equal(verificationLabel({ status: 'unverified' }), '尚未实际验证')
  assert.equal(verificationLabel({ status: 'passed' }), '实际验证通过')
  assert.equal(verificationLabel({ status: 'queued' }), '已排队等待实际验证')
  assert.equal(verificationLabel({ status: 'incomplete' }), '已实际运行但验证不完整')
  assert.equal(verificationLabel({ status: 'stopped' }), '实际验证已停止')
})

test('assistant recovery stays in its mode and candidate controls enforce active, blocker and applied boundaries', () => {
  const edit = { id: 'edit', mode: 'edit' }
  const repair = { id: 'repair', mode: 'repair' }
  assert.deepEqual(assistantsForContext([edit, repair], { mode: 'edit' }), [edit])
  assert.deepEqual(assistantListParams({ mode: 'repair', executionId: 4 }), { mode: 'repair', execution_id: 4 })

  const readyRepair = { mode: 'repair', status: 'candidate_ready', candidate_hash: 'hash', candidate_script: 'async def run(page): pass', blockers: [], adoption: { kind: 'automatic', can_apply: true, requires_acknowledge_review: false }, verify_action: { can_verify: true, requires_acknowledge_review: false } }
  assert.equal(canContinueCandidate(readyRepair), true)
  assert.equal(canVerifyCandidate(readyRepair), true)
  assert.equal(canApplyRepairCandidate(readyRepair), true)
  assert.equal(canContinueCandidate({ ...readyRepair, status: 'running' }), false)
  assert.equal(canVerifyCandidate({ ...readyRepair, blockers: ['unsafe'], verify_action: { can_verify: false, requires_acknowledge_review: false } }), false)
  assert.equal(canApplyRepairCandidate({ ...readyRepair, status: 'applied' }), false)
  assert.equal(canVerifyCandidate({ ...readyRepair, status: 'failed' }), false)
  assert.equal(canApplyRepairCandidate({ ...readyRepair, status: 'cancelled' }), false)
})

test('scope-only repair can be manually saved and run only with server-required review', () => {
  const manualRepair = {
    mode: 'repair', status: 'candidate_ready', candidate_hash: 'hash',
    candidate_script: 'async def run(page): pass',
    blockers: [{ code: 'REPAIR_SCOPE_CHANGED' }],
    adoption: { kind: 'manual_review', can_apply: true, requires_acknowledge_review: true },
    verify_action: { can_verify: true, requires_acknowledge_review: true }
  }
  assert.equal(canVerifyCandidate(manualRepair), true)
  assert.equal(canApplyRepairCandidate(manualRepair), true)
  assert.deepEqual(repairAdoptionState(manualRepair), manualRepair.adoption)
  assert.deepEqual(verifyActionState(manualRepair), manualRepair.verify_action)
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
  assert.match(source, /运行验证/)
  assert.match(source, /确认风险并运行/)
  assert.match(source, /acknowledge_review: frozen\.acknowledgeReview/)
  assert.match(source, /clearAttempt\(\)\s+await verify/)
  assert.match(source, /已实际验证通过，但仍未通过自动修复范围检查/)
  assert.match(source, /已实际运行但未通过或验证不完整/)
  assert.match(source, /manualSaveToast\(frozen\.verificationState\)/)
})

test('assistant panel supports the first edit message and keeps repair out of the edit-only message path', async () => {
  const source = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  assert.match(source, /isEdit && !assistant/)
  assert.match(source, /placeholder="说明你希望如何修改脚本…"/)
  assert.match(source, /sendMessage\(true\)/)
  assert.match(source, /<section v-if="isEdit" class="conversation-section">/)
})

test('repair records are preserved separately from edit reset, which keeps the next-send model and rejects stale recovery', async () => {
  const panel = await readFile(new URL('../src/components/WebUIScriptAssistantPanel.vue', import.meta.url), 'utf8')
  const composable = await readFile(new URL('../src/composables/useWebUIScriptAssistant.js', import.meta.url), 'utf8')
  const api = await readFile(new URL('../src/api/webTesting.js', import.meta.url), 'utf8')
  assert.match(panel, /执行失败 AI 修复/)
  assert.match(panel, /刷新修复状态/)
  assert.match(panel, /placeholder="修复记录"/)
  assert.match(panel, /重新分析本次失败/)
  assert.match(panel, /当前候选尚未采用。重新分析会保留该修复记录和候选，不会删除历史记录/)
  assert.match(panel, /selectAssistant\(null\)\s+clearConversationView\(\)/)
  assert.match(panel, /isRepair && readyToStartRepair/)
  assert.doesNotMatch(panel, /最近会话/)
  assert.match(panel, /confirmButtonText: '清空并新建'/)
  assert.match(panel, /clearEditConversation\(\)/)
  assert.match(panel, /await reset\(\)/)
  assert.match(panel, /showSessionStatus = computed\(\(\) => !isEdit\.value \|\| assistant\.value\?\.status !== 'idle'\)/)
  assert.match(panel, /模型切换从下一次发送生效。/)
  assert.match(panel, /本轮\/上次模型/)
  assert.match(panel, /expected_edit_version: props\.editContext\.editVersion, model_config_id: modelConfigId\.value/)
  assert.match(panel, /watch\(assistant, value => \{[\s\S]*if \(isEdit\.value\) \{[\s\S]*initializeEditModel\(\)/)
  assert.match(panel, /v-if="isEdit && refreshRequired" class="refresh-state"/)
  assert.match(panel, /!canVerify \|\| loading \|\| refreshRequired \|\| acting/)
  assert.match(panel, /!canApplyRepair \|\| loading \|\| refreshRequired \|\| acting/)
  assert.match(composable, /const clearEditConversation = \(\) => \{[\s\S]*requestVersion \+= 1/)
  assert.match(composable, /const reset = async \(\) => \{[\s\S]*const request = \+\+requestVersion[\s\S]*requests\.reset/)
  assert.match(composable, /request !== requestVersion/)
  assert.match(composable, /会话自动刷新失败，请重新获取状态。/)
  assert.match(composable, /修复状态自动刷新失败，请点击“刷新修复状态”恢复。/)
  assert.match(composable, /isEditRequest \? '创建 AI 会话失败' : '启动 AI 修复失败'/)
  assert.match(composable, /currentContext\(\)\.mode === 'repair' \? '启动运行验证失败' : '启动调试验证失败'/)
  assert.match(panel, /isRepair \? '修复状态已恢复。' : '会话状态已恢复。'/)
  assert.match(panel, /isRepair \? '运行超时（秒）' : '调试超时（秒）'/)
  assert.match(api, /resetWebUIScriptAssistant[\s\S]*\/reset\//)
})

test('edit composable ignores late list, poll, and message responses around create and reset', async () => {
  await withAssistantComposable(async useWebUIScriptAssistant => {
    const initialList = deferred()
    const latePoll = deferred()
    const lateMessagePoll = deferred()
    const original = { id: 'assistant-1', mode: 'edit', revision: 3, status: 'candidate_ready', messages: [{ role: 'user', content: '旧消息' }], candidate_hash: 'old-hash', candidate_script: 'old script' }
    const created = { id: 'assistant-2', mode: 'edit', revision: 1, status: 'candidate_ready', messages: [{ role: 'user', content: '新消息' }], candidate_hash: 'new-hash', candidate_script: 'new script' }
    const reset = { id: 'assistant-2', mode: 'edit', revision: 2, status: 'idle', model_info: { config_id: 9 }, messages: [], attempts: [], verification: null }
    const messaged = { id: 'assistant-2', mode: 'edit', revision: 3, status: 'candidate_ready', messages: [{ role: 'user', content: '下一条消息' }], candidate_hash: 'next-hash', candidate_script: 'next script' }
    let getCalls = 0
    const requests = {
      apply: async () => { throw new Error('not used') },
      cancel: async () => { throw new Error('not used') },
      create: async () => ({ data: created }),
      get: async () => {
        getCalls += 1
        return getCalls === 1 ? latePoll.promise : lateMessagePoll.promise
      },
      list: async () => initialList.promise,
      message: async () => ({ data: messaged }),
      reset: async () => ({ data: reset }),
      verify: async () => { throw new Error('not used') }
    }
    const state = useWebUIScriptAssistant({ projectId: ref(1), context: ref({ mode: 'edit', testCaseId: 7 }), requests, loadModelConfigs: async () => [] })
    await flush()

    await state.create({ mode: 'edit', model_config_id: 9 })
    initialList.resolve({ data: { results: [original] } })
    await flush()
    assert.equal(state.assistant.value.id, created.id)

    const staleBeforeReset = state.loadAssistant(created.id, { quiet: true })
    await flush()
    await state.reset()
    latePoll.resolve({ data: created })
    await staleBeforeReset
    assert.equal(state.assistant.value.status, 'idle')
    assert.deepEqual(state.assistant.value.messages, [])
    assert.equal(state.assistant.value.candidate_script, undefined)

    const staleBeforeMessage = state.loadAssistant(created.id, { quiet: true })
    await flush()
    await state.message({ expected_revision: 2, expected_edit_version: 5, model_config_id: 11, message: '下一条消息', use_candidate: false })
    lateMessagePoll.resolve({ data: reset })
    await staleBeforeMessage
    assert.equal(state.assistant.value.revision, 3)
    assert.equal(state.assistant.value.candidate_hash, 'next-hash')
  })
})

test('edit composable exposes a retry after initial recovery failure and clears it after an empty list succeeds', async () => {
  await withAssistantComposable(async useWebUIScriptAssistant => {
    let failList = true
    const requests = {
      apply: async () => { throw new Error('not used') },
      cancel: async () => { throw new Error('not used') },
      create: async () => { throw new Error('not used') },
      get: async () => { throw new Error('not used') },
      list: async () => {
        if (failList) throw new Error('offline')
        return { data: { results: [] } }
      },
      message: async () => { throw new Error('not used') },
      reset: async () => { throw new Error('not used') },
      verify: async () => { throw new Error('not used') }
    }
    const state = useWebUIScriptAssistant({ projectId: ref(1), context: ref({ mode: 'edit', testCaseId: 7 }), requests, loadModelConfigs: async () => [] })
    await flush()
    assert.equal(state.assistant.value, null)
    assert.equal(state.refreshRequired.value, true)
    assert.equal(state.lastError.value, '加载 AI 会话失败，请重新获取状态。')

    state.acting.value = true
    await state.loadRecent()
    assert.equal(state.refreshRequired.value, true)
    state.acting.value = false

    failList = false
    await state.loadRecent()
    assert.deepEqual(state.assistants.value, [])
    assert.equal(state.refreshRequired.value, false)
    assert.equal(state.lastError.value, '')
  })
})

test('repair recovery blocks new work until its scoped records are refreshed and exposes manual recovery after polling fails', async () => {
  await withAssistantComposable(async useWebUIScriptAssistant => {
    let failList = true
    let failGet = false
    const requests = {
      apply: async () => { throw new Error('not used') },
      cancel: async () => { throw new Error('not used') },
      create: async () => { throw new Error('not used') },
      get: async () => {
        if (failGet) throw new Error('offline')
        return { data: { id: 'repair-1', mode: 'repair', status: 'running' } }
      },
      list: async () => {
        if (failList) throw new Error('offline')
        return { data: { results: [] } }
      },
      message: async () => { throw new Error('not used') },
      reset: async () => { throw new Error('not used') },
      verify: async () => { throw new Error('not used') }
    }
    const state = useWebUIScriptAssistant({ projectId: ref(1), context: ref({ mode: 'repair', executionId: 9, suiteCaseId: 4 }), requests, loadModelConfigs: async () => [] })
    await flush()
    assert.equal(state.refreshRequired.value, true)
    assert.equal(state.lastError.value, 'offline')

    failList = false
    await state.loadRecent()
    assert.deepEqual(state.assistants.value, [])
    assert.equal(state.refreshRequired.value, false)

    state.selectAssistant({ id: 'repair-1', mode: 'repair', status: 'running' })
    failGet = true
    await state.loadAssistant('repair-1', { quiet: true })
    await state.loadAssistant('repair-1', { quiet: true })
    await state.loadAssistant('repair-1', { quiet: true })
    state.selectAssistant(null)
    assert.equal(state.refreshRequired.value, true)
    assert.equal(state.lastError.value, '修复状态自动刷新失败，请点击“刷新修复状态”恢复。')
  })
})

test('repair detail load failures require a refresh before another action can begin', async () => {
  await withAssistantComposable(async useWebUIScriptAssistant => {
    let failDetail = false
    const record = { id: 'repair-1', mode: 'repair', status: 'candidate_ready', candidate_hash: 'candidate', candidate_script: 'async def run(page): pass' }
    const requests = {
      apply: async () => { throw new Error('not used') },
      cancel: async () => { throw new Error('not used') },
      create: async () => { throw new Error('not used') },
      get: async () => {
        if (failDetail) throw new Error('offline')
        return { data: record }
      },
      list: async () => ({ data: { results: [record] } }),
      message: async () => { throw new Error('not used') },
      reset: async () => { throw new Error('not used') },
      verify: async () => { throw new Error('not used') }
    }
    const state = useWebUIScriptAssistant({ projectId: ref(1), context: ref({ mode: 'repair', executionId: 9 }), requests, loadModelConfigs: async () => [] })
    await flush()
    assert.equal(state.assistant.value?.id, record.id)

    failDetail = true
    await state.loadAssistant(record.id)
    assert.equal(state.loading.value, false)
    assert.equal(state.refreshRequired.value, true)
    assert.equal(state.lastError.value, 'offline')
  })
})

test('switching execution while repair history loads does not lock the new scope or restore stale records', async () => {
  await withAssistantComposable(async useWebUIScriptAssistant => {
    const first = deferred()
    const second = deferred()
    const context = ref({ mode: 'repair', executionId: 1 })
    const calls = []
    const state = useWebUIScriptAssistant({
      projectId: ref(1), context, loadModelConfigs: async () => [],
      requests: {
        list: async (project, params) => {
          calls.push(params.execution_id)
          return params.execution_id === 1 ? first.promise : second.promise
        },
        get: async (project, id) => ({ data: { id, mode: 'repair', status: 'candidate_ready' } })
      }
    })
    assert.equal(state.loading.value, true)
    context.value = { mode: 'repair', executionId: 2 }
    await nextTick()
    assert.deepEqual(calls, [1, 2])
    second.resolve({ data: { results: [{ id: 'new', mode: 'repair', status: 'candidate_ready' }] } })
    await flush()
    await flush()
    assert.equal(state.loading.value, false)
    assert.equal(state.assistant.value?.id, 'new')
    first.resolve({ data: { results: [{ id: 'old', mode: 'repair', status: 'candidate_ready' }] } })
    await flush()
    assert.equal(state.assistant.value?.id, 'new')
    assert.equal(state.loading.value, false)
    state.selectAssistant(null)
  })
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
