import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildGenerationTimeline,
  explorationCleanupPresentation,
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
  modelInfoLabel
} from '../src/composables/webUIScriptGenerationPresentation.js'

test('v4 storage state is isolated from old generation state', () => {
  assert.match(generationStorageKey(2, 3), /v4/)
  assert.doesNotMatch(generationStorageKey(2, 3), /v3/)
})

test('timeline presents one continuous exploration before replay and script checks', () => {
  const timeline = buildGenerationTimeline({ current_stage: 'exploring', status: 'exploring' })
  assert.deepEqual(timeline.map(item => item.label), ['理解测试目标', '连续探索页面', '最终路径定稿', '生成 Python', '检查脚本', '完成'])
  assert.equal(timeline[1].state, 'process')
})

test('incomplete exploration is not presented as a system failure', () => {
  const hint = generationResolutionHint({
    status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE',
    error_message: '探索未完整结束，但证据已保留。'
  })
  assert.match(hint, /不是系统失败/)
  assert.match(hint, /页面证据已保留，未生成草稿/)
})

test('cleanup presentation remains conservative for v4 traces', () => {
  assert.equal(explorationCleanupPresentation({}).hasRecord, false)
  assert.deepEqual(
    explorationCleanupPresentation({ schema_version: 4, cleanup: { status: 'completed', attempted: true } }),
    { hasRecord: true, status: 'completed', label: '清理已验证', type: 'success', attempted: true, residuals: [], reason: '' }
  )
  assert.equal(explorationCleanupPresentation({ cleanup: { status: 'attempted' } }).label, '已尝试，未验证')
  assert.equal(explorationCleanupPresentation({ cleanup: { status: 'missing' } }).type, 'danger')
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

test('paused states expose generic v4 actions without Goal boundaries', () => {
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
  assert.match(generationResolutionHint({ status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE' }), /不是系统失败/)
})

test('workspace activity and revision verification remain explicit', () => {
  const workspace = { verification: { status: 'passed', locked_revision: 4, environment_id: 12 }, repair: { status: 'idle' } }
  assert.equal(isCurrentRevisionVerified(workspace, 4, 12), true)
  assert.equal(isCurrentRevisionVerified(workspace, 4, 13), false)
  assert.equal(isWorkspaceActive({ verification: { status: 'running' }, repair: { status: 'idle' } }), true)
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

test('needs review preserves cleanup evidence and never implies success', () => {
  const hint = generationResolutionHint({
    status: 'needs_review', error_message: '清理动作已执行，但缺少后续确认证据。'
  })
  assert.match(hint, /缺少后续确认证据/)
  assert.match(hint, /人工处理/)
  assert.doesNotMatch(hint, /已完成|已清理/)
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

test('failed and cancelled runs with unknown cleanup warn before a replacement task', () => {
  for (const status of ['failed', 'cancelled']) {
    const hint = generationResolutionHint({
      status, error_message: '模型服务异常（HTTP 500）',
      exploration_snapshot: { schema_version: 4, cleanup: { status: 'unknown', attempted: false } }
    })
    assert.match(hint, /重新发起前/)
    assert.match(hint, /避免重复操作/)
  }
})
