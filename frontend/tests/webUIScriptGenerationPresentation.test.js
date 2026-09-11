import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  assertionStateLabel,
  assertionStateTagType,
  buildGenerationTimeline,
  canRetryScriptFromTrace,
  canResumeInterruptedExploration,
  canSaveGeneratedDraft,
  failedEventPageEvidence,
  failedEventTargetEvidence,
  failureActionText,
  failureEvidenceBreadcrumbs,
  failureEvidenceText,
  formatGenerationLifecycleTime,
  generationFailureContext,
  generationFailureContextLocationLabel,
  generationFailureContextReason,
  generationDraftCompletion,
  generationFailureReason,
  generationLifecycleStateLabel,
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
  workspaceVerificationTagType,
  shouldShowGenerationStopContext
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
  assert.equal(shouldShowGenerationStopContext('failed'), true)
  assert.equal(shouldShowGenerationStopContext('needs_review'), true)
  assert.equal(shouldShowGenerationStopContext('cancelled'), true)
  assert.equal(shouldShowGenerationStopContext('preflighting'), false)
  assert.equal(shouldShowGenerationStopContext('exploring'), false)
  assert.equal(shouldShowGenerationStopContext('ready'), false)
})

test('interrupted lifecycle is explicit and never inferred from missing heartbeats', () => {
  const interrupted = { lifecycle: { state: 'interrupted', can_resume: true } }
  assert.equal(generationLifecycleStateLabel('interrupted'), '探索已中断')
  assert.equal(formatGenerationLifecycleTime(null), '—')
  assert.equal(canResumeInterruptedExploration(interrupted), true)
  assert.equal(canResumeInterruptedExploration(interrupted, { draftDirty: true }), false)
  assert.equal(canResumeInterruptedExploration(interrupted, { busy: true }), false)
  assert.equal(canResumeInterruptedExploration({ lifecycle: { state: 'running', can_resume: true } }), false)
  assert.equal(canResumeInterruptedExploration({ status: 'needs_review', lifecycle: { state: 'idle', can_resume: true } }), true)
  assert.equal(canResumeInterruptedExploration({ status: 'cancelled', lifecycle: { state: 'idle', can_resume: true } }), true)
  assert.equal(canResumeInterruptedExploration({ status: 'failed', lifecycle: { state: 'idle', can_resume: false } }), false)
  assert.match(generationResolutionHint({ ...interrupted, status: 'failed' }, { draftDirty: true }), /补充现场说明并继续/)
  assert.match(generationResolutionHint(interrupted), /补充现场说明并继续/)
  assert.match(generationResolutionHint(interrupted), /智能体会按说明继续探索，请先核对已有数据/)
})

test('resume dialog requires notes and cancel stays local without an API action', () => {
  const panel = readFileSync(new URL('../src/components/webui-generation/GenerationResultPanel.vue', import.meta.url), 'utf8')
  assert.match(panel, /补充现场说明并继续/)
  assert.match(panel, /本地草稿尚未保存，请先保存草稿后再补充现场说明并继续/)
  assert.match(panel, /请填写现场恢复说明/)
  assert.match(panel, /不会直接重放旧脚本/)
  assert.match(panel, /智能体会按说明继续探索，请核对已有数据/)
  assert.match(panel, /不是从失败代码行断点续跑/)
  assert.match(panel, /已新增的数据标识/)
  assert.match(panel, /cancelButtonText: '取消恢复'/)
  assert.match(panel, /生成记录已变更，请重新打开恢复窗口并确认现场/)
  assert.match(panel, /if \(error !== 'cancel' && error !== 'close'\)/)
  assert.match(panel, /emit\('resume-exploration', \{/)
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

test('generic interaction stops do not blame authentication or test credentials', () => {
  for (const code of ['repeated_interaction', 'interaction_failure']) {
    const message = generationFailureReason({ error_code: code })
    assert.match(message, /操作|交互/)
    assert.doesNotMatch(message, /登录|密码|账号/)
  }
  const diagnostic = '相同页面下的操作已达到纠错上限，已停止本次探索。'
  assert.equal(generationFailureReason({ error_code: 'repeated_interaction', error_message: diagnostic }), diagnostic)
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

test('stop context uses only the backend failure snapshot and makes missing evidence explicit', () => {
  const generation = {
    target_url: 'https://must-not-be-used.example/',
    exploration_snapshot: {
      failure_context: {
        event_id: 'evt-7', page_url: 'https://captured.example/orders', page_title: '订单页',
        breadcrumbs: ['业务', '订单'], action: '提交', element_label: '确认区域',
        container_label: '订单表单', selector: '[data-testid="submit"]',
        message: '提交后页面未进入下一步。', reason_code: 'interaction_failure', location_source: 'failure_event', screenshot_status: 'captured'
      }
    }
  }
  const context = generationFailureContext(generation)
  assert.equal(context.page_url, 'https://captured.example/orders')
  assert.equal(failureEvidenceText(undefined), '未采集')
  assert.equal(failureEvidenceBreadcrumbs(['业务', '订单']), '业务 / 订单')
  assert.equal(generationFailureContextLocationLabel(context), '停止位置')
  assert.equal(generationFailureContextReason(context), '提交后页面未进入下一步。')
  assert.equal(generationFailureContext({ exploration_snapshot: { failure_context: null } }), null)
  assert.equal(generationFailureContext({ exploration_snapshot: { failure_context: {} } }), null)
  assert.equal(failureActionText('fill'), '输入内容')
  assert.equal(failureActionText('click'), '触发页面操作')
  assert.equal(failureActionText('unexpected_tool'), '页面操作（具体动作未采集）')
  assert.doesNotMatch(JSON.stringify(context), /must-not-be-used/)
})

test('last observed pages are not presented as failure scenes or browser interaction failures', () => {
  const context = { location_source: 'last_observed', message: '点击失败', reason_code: 'interaction_failure' }
  const generation = { status: 'failed', error_code: 'MODEL_GATEWAY_TIMEOUT', error_message: '模型响应超时，请稍后重试。' }
  assert.equal(generationFailureContextLocationLabel(context), '最后记录的页面（不代表失败现场）')
  assert.equal(generationFailureContextReason(context, generation), '模型响应超时，请稍后重试。')
  assert.equal(generationFailureContextLocationLabel({}), '位置来源未采集')
})

test('failed-event page and target evidence never reuse a later page or an unscoped event URL', () => {
  const event = {
    url: 'https://must-not-be-used.example/current', element_label: 'must not use',
    page_context: { page_title: '结算页', page_url: 'https://captured.example/checkout', element_label: '提交订单', container_label: '结算表单' }
  }
  assert.equal(failedEventPageEvidence(event), '结算页 · https://captured.example/checkout')
  assert.equal(failedEventTargetEvidence(event), '提交订单；所属：结算表单')
  assert.equal(failedEventPageEvidence({ url: 'https://must-not-be-used.example/' }), '未采集')
  assert.equal(failedEventTargetEvidence({ element_label: 'must not use' }), '未采集')
})

test('stop context and failure-event UI preserve evidence boundaries and raw diagnostics', () => {
  const panel = readFileSync(new URL('../src/components/webui-generation/GenerationResultPanel.vue', import.meta.url), 'utf8')
  const stopContext = readFileSync(new URL('../src/components/webui-generation/GenerationStopContext.vue', import.meta.url), 'utf8')
  const evidence = readFileSync(new URL('../src/components/webui-generation/GenerationEvidence.vue', import.meta.url), 'utf8')
  const api = readFileSync(new URL('../src/api/webTesting.js', import.meta.url), 'utf8')
  assert.match(panel, /<GenerationStopContext/)
  assert.match(stopContext, /页面标题/)
  assert.match(stopContext, /页面地址/)
  assert.match(stopContext, /查看具体定位器/)
  assert.match(stopContext, /failureActionText\(context\.action\)/)
  assert.match(stopContext, /preview-src-list/)
  assert.match(stopContext, /generationFailureContext\(props\.generation\)/)
  assert.match(stopContext, /location_source/)
  assert.match(evidence, /failedEventPageEvidence/)
  assert.match(evidence, /failedEventTargetEvidence/)
  assert.match(evidence, /查看原始报错/)
  assert.match(api, /failure-screenshot/)
  assert.match(api, /responseType: 'blob'/)
  assert.match(api, /event_id: eventId, captured_at: capturedAt \|\| undefined/)
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
  assert.match(inputPanel, /账号密码可能出现在生成记录、日志、截图或脚本，请勿使用生产账号/)
  assert.match(inputPanel, /http:\/\/192\.168\.31\.188:9990\//)
  assert.doesNotMatch(inputPanel, /form\.environmentId/)
  assert.doesNotMatch(inputPanel, /form\.startPath/)
  assert.doesNotMatch(inputPanel, /temporary_credentials/)
  assert.doesNotMatch(inputPanel, /<el-input v-model="form\.username"/)
})

test('inserted example follows menu exploration and numbered script verification requirements', () => {
  const inputPanel = readFileSync(new URL('../src/components/webui-generation/GenerationInputPanel.vue', import.meta.url), 'utf8')
  const example = inputPanel.match(/const EXAMPLE_DESCRIPTION = `([\s\S]*?)`/)?.[1]
  assert.ok(example && example.length <= 2000)
  assert.ok(example.startsWith('http://192.168.31.188:9990/\n'))
  assert.ok(example.includes('登录账号：test，密码：123456。'))
  assert.ok(example.includes('权限 > 菜单列表'))
  assert.ok(example.includes('探索完成后，生成完整的 Python Playwright 脚本'))
  assert.ok(example.includes('time.time_ns()'))
  assert.deepEqual(example.match(/^\d\./gm), ['1.', '2.', '3.', '4.', '5.'])
  assert.ok(example.includes('5. 查询并验证数据不存在'))
  assert.match(inputPanel, /const insertExample = \(\) => \{ form\.description = EXAMPLE_DESCRIPTION \}/)
  assert.match(inputPanel, /页面元素和定位方式由 Playwright MCP 探索，无需手动提供/)
  assert.match(inputPanel, /可替换为其他网站的模块和页面/)
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
  assert.doesNotMatch(workspace, /form\.script_draft\.includes\('PENDING_STEP'\)/)
  assert.match(resultPanel, /查看任务技术信息/)
  assert.match(resultPanel, /model_output_raw/)
  assert.match(resultPanel, /generation_error_message/)
  assert.doesNotMatch(evidence, /最新消息：\{\{ trace\.final_message \}\}/)
  assert.match(evidence, /最近保存的草稿仍有待补充项/)
})
