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
  generationUserMessage,
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
  assert.match(hint, /已保留草稿和探索证据/)
  assert.deepEqual(generationDraftCompletion({ exploration_snapshot: { artifact: { completion: 'partial', completed_steps: ['打开页面'], remaining_steps: ['补充断言'] } } }), {
    completion: 'partial', isPartial: true, completedSteps: ['打开页面'], remainingSteps: ['补充断言']
  })
})

test('generic generation status boundaries remain mapped', () => {
  assert.equal(generationStatusLabel('exploring'), '正在连续探索页面')
  assert.equal(generationStatusLabel('unexpected'), '状态未知')
  assert.equal(isActiveGeneration('validating'), true)
  assert.equal(isPausedGeneration('needs_credentials'), false)
  assert.equal(isTerminalGeneration('needs_review'), true)
})

test('field validation errors are shown instead of a generic transport error', () => {
  const message = generationApiErrorMessage({
    message: 'Request failed with status code 400',
    response: { data: { error: { details: { description: ['描述中必须包含一个完整 http(s) URL'] } } } }
  }, '创建失败')
  assert.equal(message, '测试描述：描述中必须包含一个完整 http(s) URL')
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
  assert.match(generationResolutionHint({ status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE' }), /已保留草稿和探索证据/)
})

test('workspace activity and revision verification remain explicit', () => {
  const workspace = { verification: { status: 'passed', locked_revision: 4, runtime_assertion_count: 2, assertion_state: { status: 'complete' } }, repair: { status: 'idle' } }
  assert.equal(isCurrentRevisionVerified(workspace, 4), true)
  assert.equal(isCurrentRevisionVerified(workspace, 3), false)
  assert.equal(isCurrentRevisionVerified({ verification: { status: 'passed', locked_revision: 4, runtime_assertion_count: 0, assertion_state: { status: 'complete' } } }, 4), false)
  assert.equal(isCurrentRevisionVerified({ verification: { status: 'passed', locked_revision: 4, runtime_assertion_count: 2, assertion_state: { status: 'incomplete' } } }, 4), false)
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
  assert.equal(generationFailureReason({ error_message: '模型服务异常', exploration_snapshot: { error_message: '可读轨迹错误', final_message: '已停止', termination_reason: 'MODEL_GATEWAY_TIMEOUT' } }), '模型服务异常')
  assert.equal(generationFailureReason({ exploration_snapshot: { error_message: '可读轨迹错误', final_message: '已停止', termination_reason: 'MODEL_GATEWAY_TIMEOUT' } }), '可读轨迹错误')
  assert.equal(generationFailureReason({ exploration_snapshot: { final_message: '已停止', termination_reason: 'MODEL_GATEWAY_TIMEOUT' } }), '模型响应超时，请稍后重试。')
})

test('model free text stays technical while normal warnings are bounded Chinese summaries', () => {
  const english = 'I inspected several possible selectors and considered a long chain of alternatives. '.repeat(12)
  const pending = `待补充断言：${english}`
  const generation = {
    status: 'needs_review', error_code: 'EXPLORATION_EVIDENCE_INCOMPLETE',
    exploration_snapshot: { final_message: english, model_output_raw: english, artifact: { completion: 'partial', remaining_steps: [pending] } }
  }
  const hint = generationResolutionHint(generation)
  assert.equal(generationFailureReason(generation), '探索证据未完整保存。')
  assert.match(hint, /待补充项：/)
  assert.match(hint, /具体原因未以中文记录/)
  assert.doesNotMatch(hint, /I inspected several possible selectors/)
  assert.ok(hint.length < 500)
})

test('mixed diagnostics are capped and unknown English failures receive a Chinese technical hint', () => {
  const longEnglish = ' gateway timeout details '.repeat(30)
  const mixed = generationFailureReason({ status: 'failed', error_message: `模型服务异常：${longEnglish}` })
  assert.match(mixed, /^模型服务异常：/)
  assert.doesNotMatch(mixed, /gateway timeout details/)
  assert.ok(mixed.length <= 181)
  const unknown = generationResolutionHint({ status: 'failed', error_code: 'UNRECOGNIZED_BACKEND_CODE', error_message: longEnglish })
  assert.match(unknown, /原始诊断请查看技术信息/)
  assert.doesNotMatch(unknown, /gateway timeout details/)
  const known = generationFailureReason({ status: 'failed', error_code: 'MODEL_SERVICE_ERROR', error_message: longEnglish })
  assert.equal(known, '模型服务异常，请稍后重试。')
  assert.equal(generationUserMessage(`待补充断言：${longEnglish}`, '请在技术信息查看原始内容。'), '请在技术信息查看原始内容。')
})

test('interrupted drafts retain a Chinese error hint and all summary branches are bounded', () => {
  assert.match(generationFailureReason({ status: 'needs_review', error_code: 'other', error_message: 'Unexpected EOF' }), /未完整结束.*技术信息/)
  const message = generationUserMessage('中文'.repeat(200) + ' detailed message '.repeat(200), '')
  assert.ok(message.length <= 181)
  assert.match(message, /…$/)
})

test('current assertion state supersedes exploration todos after an edited draft is saved', () => {
  const generation = {
    status: 'needs_review',
    exploration_snapshot: { artifact: { completion: 'partial', remaining_steps: ['旧探索未完成的删除验证'] } },
    workspace: { verification: { assertion_state: { status: 'complete', pending: [], confirmed_count: 1, pending_count: 0 } } }
  }
  const hint = generationResolutionHint(generation)
  assert.match(hint, /当前脚本未检测到待补充标记/)
  assert.doesNotMatch(hint, /旧探索未完成的删除验证|调试通过/)
  generation.workspace.verification.assertion_state = {
    status: 'incomplete', confirmed_count: 0, pending_count: 1,
    pending: [{ kind: 'assertion', reason: '补充列表为空的断言', line: 8 }]
  }
  assert.match(generationResolutionHint(generation), /待补充断言：补充列表为空的断言/)
  assert.doesNotMatch(generationResolutionHint(generation), /旧探索未完成的删除验证/)
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

test('description-only input carries test credentials and target URL without legacy controls', () => {
  const inputPanel = readFileSync(new URL('../src/components/webui-generation/GenerationInputPanel.vue', import.meta.url), 'utf8')
  assert.match(inputPanel, /凭据可能出现在生成记录、日志、截图或脚本，请勿使用生产账号/)
  assert.match(inputPanel, /https:\/\/example\.test\/admin\/users/)
  assert.doesNotMatch(inputPanel, /form\.environmentId/)
  assert.doesNotMatch(inputPanel, /form\.startPath/)
  assert.doesNotMatch(inputPanel, /temporary_credentials/)
  assert.doesNotMatch(inputPanel, /<el-input v-model="form\.username"/)
})

test('generation output displays target_url and never uses the retired safe field', () => {
  const resultPanel = readFileSync(new URL('../src/components/webui-generation/GenerationResultPanel.vue', import.meta.url), 'utf8')
  const summary = readFileSync(new URL('../src/components/webui-generation/GenerationScenarioSummary.vue', import.meta.url), 'utf8')
  assert.match(resultPanel, /generation\?\.target_url/)
  assert.match(summary, /targetUrl/)
  assert.doesNotMatch(resultPanel, /target_url_safe/)
  assert.doesNotMatch(summary, /target_url_safe/)
})

test('workspace defers stale pending details after local edits and technical sections retain raw diagnostics', () => {
  const workspace = readFileSync(new URL('../src/components/webui-generation/GenerationWorkspace.vue', import.meta.url), 'utf8')
  const resultPanel = readFileSync(new URL('../src/components/webui-generation/GenerationResultPanel.vue', import.meta.url), 'utf8')
  const evidence = readFileSync(new URL('../src/components/webui-generation/GenerationEvidence.vue', import.meta.url), 'utf8')
  assert.match(workspace, /本地草稿有修改，保存后会重新检查待补充步骤和断言/)
  assert.doesNotMatch(workspace, /form\.script_draft\.includes\('AITS_PENDING_STEP'\)/)
  assert.match(resultPanel, /查看任务技术信息/)
  assert.match(resultPanel, /model_output_raw/)
  assert.match(resultPanel, /generation_error_message/)
  assert.doesNotMatch(evidence, /最新消息：\{\{ trace\.final_message \}\}/)
  assert.match(evidence, /最近保存的草稿仍有待补充项/)
})
