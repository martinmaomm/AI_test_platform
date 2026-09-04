import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  assertionStateLabel,
  assertionStateTagType,
  buildGenerationTimeline,
  canRetryScriptFromTrace,
  canSaveGeneratedDraft,
  generationDraftCompletion,
  generationFailureReason,
  generationActionRequired,
  generationApiErrorMessage,
  generationResolutionHint,
  generationStatusLabel,
  generationStorageKey,
  isActiveGeneration,
  isCurrentRevisionVerified,
  isPausedGeneration,
  isTerminalGeneration,
  isWorkspaceActive,
  matchesGenerationWebSocketEvent,
  modelConfigurationLabel,
  modelInfoLabel,
  workspaceVerificationLabel,
  workspaceVerificationTagType
} from '../src/composables/webUIScriptGenerationPresentation.js'

test('v5 storage state is isolated from old generation state', () => {
  assert.match(generationStorageKey(2, 3), /v5/)
  assert.doesNotMatch(generationStorageKey(2, 3), /v4/)
})

test('timeline presents the simplified exploration-to-draft flow', () => {
  const timeline = buildGenerationTimeline({ current_stage: 'exploring', status: 'exploring' })
  assert.deepEqual(timeline.map(item => item.label), ['理解测试目标', '连续探索并编写脚本', '静态检查', '草稿就绪（非测试通过）'])
  assert.equal(timeline[1].state, 'process')
})

test('partial output is presented as editable evidence, not a completed test', () => {
  const hint = generationResolutionHint({
    status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE',
    error_message: '探索未完整结束，但证据已保留。',
    exploration_snapshot: { schema_version: 5, artifact: { completion: 'partial', completed_steps: ['打开页面'], remaining_steps: ['补充断言'] } }
  })
  assert.match(hint, /探索未完整结束，但证据已保留/)
  assert.match(hint, /未完成草稿和探索证据/)
  assert.deepEqual(generationDraftCompletion({ exploration_snapshot: { artifact: { completion: 'partial', completed_steps: ['打开页面'], remaining_steps: ['补充断言'] } } }), {
    completion: 'partial', isPartial: true, completedSteps: ['打开页面'], remainingSteps: ['补充断言']
  })
})

test('generic generation status boundaries remain mapped', () => {
  assert.equal(generationStatusLabel('exploring'), '正在连续探索页面')
  assert.equal(generationStatusLabel('unexpected'), '状态未知')
  assert.equal(isActiveGeneration('validating'), true)
  assert.equal(isPausedGeneration('needs_credentials'), true)
  assert.equal(isTerminalGeneration('needs_review'), true)
})

test('field validation errors are shown instead of a generic transport error', () => {
  const message = generationApiErrorMessage({
    message: 'Request failed with status code 400',
    response: { data: { error: { details: { description: ['请补充测试目标'], start_path: ['必须是相对路径'] } } } }
  }, '创建失败')
  assert.equal(message, '测试描述：请补充测试目标；起始相对路径：必须是相对路径')
})

test('paused states expose generic actions without Goal boundaries', () => {
  const action = generationActionRequired({ status: 'needs_confirmation', current_stage: 'preflighting', resume_count: 1 })
  assert.equal(action.kind, 'target_scope')
  assert.equal(action.remainingAttempts, 2)
  assert.doesNotMatch(action.description, /Goal|goal_id/)
})

test('websocket messages only match an explicitly identified generation', () => {
  const generation = { id: 'generation-1', celery_task_id: 'task-1' }
  assert.equal(matchesGenerationWebSocketEvent({ result: { generation_id: 'generation-1' } }, generation), true)
  assert.equal(matchesGenerationWebSocketEvent({ task_id: 'task-1' }, generation), true)
  assert.equal(matchesGenerationWebSocketEvent({ result: { generation_id: 'other' } }, generation), false)
  assert.equal(matchesGenerationWebSocketEvent({ status: 'completed' }, generation), false)
})

test('failed status is visibly distinct from incomplete exploration evidence', () => {
  assert.equal(generationStatusLabel('failed'), '生成失败')
  assert.match(generationResolutionHint({ status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE' }), /已保留当前探索证据/)
})

test('workspace activity and revision verification remain explicit', () => {
  const workspace = { verification: { status: 'passed', locked_revision: 4, environment_id: 12, runtime_assertion_count: 2, assertion_state: { status: 'complete' } }, repair: { status: 'idle' } }
  assert.equal(isCurrentRevisionVerified(workspace, 4, 12), true)
  assert.equal(isCurrentRevisionVerified(workspace, 4, 13), false)
  assert.equal(isCurrentRevisionVerified({ verification: { status: 'passed', locked_revision: 4, environment_id: 12, runtime_assertion_count: 0, assertion_state: { status: 'complete' } } }, 4, 12), false)
  assert.equal(isCurrentRevisionVerified({ verification: { status: 'passed', locked_revision: 4, environment_id: 12, runtime_assertion_count: 2, assertion_state: { status: 'incomplete' } } }, 4, 12), false)
  assert.equal(isWorkspaceActive({ verification: { status: 'running' }, repair: { status: 'idle' } }), true)
})

test('incomplete verification and assertion-state rows never use a passed presentation', () => {
  assert.equal(workspaceVerificationLabel('incomplete'), '验证未完成')
  assert.equal(workspaceVerificationTagType('incomplete'), 'warning')
  assert.deepEqual(
    [assertionStateTagType({ status: 'complete', pending_count: 0 }), assertionStateLabel({ status: 'complete', pending_count: 0 })],
    ['success', '断言已补齐']
  )
  assert.deepEqual(
    [assertionStateTagType({ status: 'incomplete', pending_count: 2 }), assertionStateLabel({ status: 'incomplete', pending_count: 2 })],
    ['warning', '2 项待补充']
  )
  assert.deepEqual(
    [assertionStateTagType({ status: 'incomplete', pending_count: 0, confirmed_count: 0 }), assertionStateLabel({ status: 'incomplete', pending_count: 0, confirmed_count: 0 })],
    ['warning', '缺少有效断言']
  )
  assert.deepEqual([assertionStateTagType(), assertionStateLabel()], ['info', '未检查'])
})

test('model labels prefer display names and retain provider fallback', () => {
  assert.equal(modelConfigurationLabel({ provider_name: '内部模型', model_name: 'current' }), '内部模型 · current')
  assert.equal(modelConfigurationLabel({ provider: 'openai', model_name: 'legacy' }), 'openai · legacy')
  assert.equal(modelInfoLabel({ provider: 'openai', model_name: 'legacy' }), 'openai · legacy')
  assert.equal(modelInfoLabel({ provider: 'openai' }), '—')
})

test('preflight scope explains one continuous browser session without fixed business wording', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'preflighting',
    error_code: 'EXPLORATION_WRITE_CONFIRMATION_REQUIRED'
  })
  assert.equal(action.kind, 'target_scope')
  assert.equal(action.primaryLabel, '确认目标范围并继续')
  assert.match(action.description, /一个浏览器会话中连续探索完整场景/)
  assert.match(action.description, /不依赖固定按钮文案/)
  assert.doesNotMatch(action.description, /CRUD|Goal|goal_id/)
})

test('needs review preserves the actual reason and never implies success', () => {
  const hint = generationResolutionHint({
    status: 'needs_review', error_message: '浏览器会话已断开，无法补齐最后一步。'
  })
  assert.match(hint, /浏览器会话已断开/)
  assert.doesNotMatch(hint, /测试通过/)
})

test('extra high-risk rejection exposes description editing rather than blind continuation', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'preflighting',
    error_code: 'EXPLORATION_EXTRA_RISK_BLOCKED', error_message: '请移除超出已确认范围的操作'
  })
  assert.equal(action.kind, 'description')
  assert.equal(action.description, '请移除超出已确认范围的操作')
  assert.equal(action.primaryLabel, '修订后继续')
})

test('model gateway errors remain byte-for-byte user visible', () => {
  const message = '模型服务暂时不可用：上游网关超时，请稍后重试。'
  assert.equal(generationResolutionHint({ status: 'failed', error_message: message }), message)
})

test('needs input guidance requests only user-owned scenario information', () => {
  const hint = generationResolutionHint({ status: 'needs_input' })
  assert.match(hint, /测试目标/)
  assert.match(hint, /操作步骤/)
  assert.match(hint, /可验证结果/)
  assert.match(hint, /默认清理策略不需要填写/)
  assert.doesNotMatch(hint, /填写.*页面元素/)
})

test('actual generation error takes priority over trace termination text', () => {
  assert.equal(generationFailureReason({ error_message: '模型服务异常', exploration_snapshot: { error_message: '可读轨迹错误', final_message: '已停止', termination_reason: 'MODEL_TIMEOUT' } }), '模型服务异常')
  assert.equal(generationFailureReason({ exploration_snapshot: { error_message: '可读轨迹错误', final_message: '已停止', termination_reason: 'MODEL_TIMEOUT' } }), '可读轨迹错误')
  assert.equal(generationFailureReason({ exploration_snapshot: { final_message: '已停止', termination_reason: 'MODEL_TIMEOUT' } }), '已停止')
})

test('a schema-v5 trace can retry script organization without reopening a browser', () => {
  const generation = { status: 'needs_review', exploration_snapshot: { schema_version: 5, events: [{ event_id: 'evt-1', status: 'succeeded' }], artifact: { completion: 'partial' } } }
  assert.equal(canRetryScriptFromTrace(generation), true)
  assert.equal(canRetryScriptFromTrace({ ...generation, status: 'cancelled' }), true)
  assert.equal(canRetryScriptFromTrace({ ...generation, status: 'ready' }), false)
  assert.equal(canRetryScriptFromTrace({ status: 'failed', script_draft: 'partial script', exploration_snapshot: { schema_version: 5 } }), true)
  assert.equal(canRetryScriptFromTrace(generation, true), false)
})

test('a non-busy partial draft can be saved without a completion gate, but not with static blockers', () => {
  const generation = { status: 'needs_review', script_draft: 'async def run(page):\n    pass', exploration_snapshot: { schema_version: 5, artifact: { completion: 'partial' } }, quality_report: { checks: [] } }
  assert.equal(canSaveGeneratedDraft(generation, null, false), true)
  assert.equal(canSaveGeneratedDraft({ ...generation, quality_report: { checks: [{ level: 'blocker' }] } }, null, false), false)
  assert.doesNotMatch(readFileSync(new URL('../src/components/webui-generation/GenerationResultPanel.vue', import.meta.url), 'utf8'), /finalization/)
})

test('a terminal failure marks its current stage only and never marks all stages successful', () => {
  const timeline = buildGenerationTimeline({ status: 'failed', current_stage: 'generating' })
  assert.equal(timeline[0].state, 'success')
  assert.equal(timeline[1].state, 'error')
  assert.equal(timeline[2].state, 'wait')
  assert.equal(timeline[3].state, 'wait')
})

test('credential entry warns about retained test-environment artifacts', () => {
  const inputPanel = readFileSync(new URL('../src/components/webui-generation/GenerationInputPanel.vue', import.meta.url), 'utf8')
  assert.match(inputPanel, /凭据可能出现在生成记录、日志、截图或脚本，请勿使用生产账号/)
  assert.doesNotMatch(inputPanel, /不会写入脚本、生成记录或本地存储/)
  assert.doesNotMatch(inputPanel, /登录密码不得写入脚本、日志、截图或报告/)
})
