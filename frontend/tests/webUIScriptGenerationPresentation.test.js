import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildGenerationTimeline,
  generationActionRequired,
  generationResolutionHint,
  explorationCleanupPresentation,
  isCurrentRevisionVerified,
  isWorkspaceActive,
  modelConfigurationLabel,
  modelInfoLabel
} from '../src/composables/webUIScriptGenerationPresentation.js'

test('model labels prefer the editable provider name and preserve historic provider fallback', () => {
  assert.equal(
    modelConfigurationLabel({ provider: 'openai', provider_name: 'OpenAI 企业网关', model_name: 'gpt-4.1' }),
    'OpenAI 企业网关 · gpt-4.1'
  )
  assert.equal(modelConfigurationLabel({ provider: 'openai', model_name: 'gpt-4.1' }), 'openai · gpt-4.1')
  assert.equal(modelInfoLabel({ provider: 'openai', model_name: 'legacy-model' }), 'openai · legacy-model')
  assert.equal(modelInfoLabel({ provider_name: '内部模型', model_name: 'current-model' }), '内部模型 · current-model')
  assert.equal(modelInfoLabel({ provider: 'openai' }), '—')
})

test('only business-decision targets become clarification questions', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'exploring', error_code: 'INPUT_AMBIGUOUS',
    exploration_snapshot: {
      completion: {
        status: 'needs_user_decision',
        missing_targets: [
          { target: '保存按钮定位器', kind: 'observable', reason: '页面加载超时', user_question: '填写 CSS 选择器' },
          { target: '删除后的业务规则', kind: 'business_decision', reason: '规则未在页面显示', user_question: '删除后是否需要二次确认？' }
        ],
        user_questions: []
      }
    }
  })

  assert.equal(action.kind, 'clarifications')
  assert.deepEqual(action.questions, ['删除后是否需要二次确认？'])
})

test('observable evidence gaps do not create a DOM questionnaire', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'exploring', error_code: 'EVIDENCE_INSUFFICIENT',
    exploration_snapshot: { completion: { status: 'needs_targeted_exploration', missing_targets: [{ target: '提交按钮', kind: 'observable', reason: '未获得稳定证据' }] } }
  })

  assert.equal(action.kind, 'exploration_issue')
  assert.equal(action.primaryLabel, '')
})

test('preflight confirmation supports ordinary CRUD without promising extra-risk actions', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'preflighting',
    error_code: 'EXPLORATION_WRITE_CONFIRMATION_REQUIRED'
  })

  assert.equal(action.kind, 'target_scope')
  assert.equal(action.primaryLabel, '确认常规 CRUD 范围并继续')
  assert.match(action.description, /常规测试数据的新增、查询、编辑、删除已支持/)
  assert.match(action.description, /审批、付款、发布、上传/)
  assert.match(action.description, /测试描述已明确要求只读/)
  assert.doesNotMatch(action.primaryLabel, /仅只读/)
})

test('needs review preserves backend cleanup evidence and does not imply success', () => {
  const hint = generationResolutionHint({
    status: 'needs_review', error_message: '清理失败：仍有 1 条本轮测试数据。'
  })

  assert.match(hint, /清理失败：仍有 1 条本轮测试数据。/)
  assert.match(hint, /人工处理/)
  assert.doesNotMatch(hint, /已完成/)
})

test('extra risk rejection exposes the description editor instead of a blind continue button', () => {
  const action = generationActionRequired({
    status: 'needs_confirmation', current_stage: 'preflighting',
    error_code: 'EXPLORATION_EXTRA_RISK_BLOCKED', error_message: '请移除支付操作'
  })
  assert.equal(action.kind, 'description')
  assert.equal(action.description, '请移除支付操作')
})

test('model availability and gateway failures keep the backend error message', () => {
  const message = '模型服务暂时不可用：上游网关超时，请稍后重试。'
  assert.equal(generationResolutionHint({ status: 'failed', error_message: message }), message)
})

test('failure and cancellation with unknown cleanup warn before a new task', () => {
  for (const status of ['failed', 'cancelled']) {
    const hint = generationResolutionHint({
      status, error_message: '模型服务异常（HTTP 500）',
      exploration_snapshot: { cleanup_report: { status: 'unknown', attempted: false } }
    })
    assert.match(hint, /重新发起前/)
    assert.match(hint, /避免重复操作/)
  }
})

test('cleanup presentation does not infer success for legacy snapshots and flags residuals', () => {
  assert.equal(explorationCleanupPresentation({}).hasRecord, false)
  const cleanup = explorationCleanupPresentation({
    cleanup_report: { status: 'residual', attempted: true, residuals: ['user:test-42'], reason: '删除接口超时' }
  })
  assert.deepEqual(cleanup, {
    hasRecord: true, status: 'residual', label: '发现残留', type: 'error', attempted: true,
    residuals: ['user:test-42'], reason: '删除接口超时'
  })
})

test('workspace activity keeps polling alive and verified save requires the current revision', () => {
  const workspace = { verification: { status: 'passed', locked_revision: 4 }, repair: { status: 'idle' } }
  assert.equal(isWorkspaceActive(workspace), false)
  workspace.verification.environment_id = 12
  assert.equal(isCurrentRevisionVerified(workspace, 4, 12), true)
  assert.equal(isCurrentRevisionVerified(workspace, 4, 13), false)
  assert.equal(isCurrentRevisionVerified(workspace, 5), false)
  assert.equal(isWorkspaceActive({ verification: { status: 'running' }, repair: { status: 'idle' } }), true)
  assert.equal(isWorkspaceActive({ verification: { status: 'passed' }, repair: { status: 'running' } }), true)
})

test('timeline distinguishes exploration and process validation from script debugging', () => {
  const labels = buildGenerationTimeline({ status: 'ready', current_stage: 'completed' }).map(item => item.label)
  assert.deepEqual(labels, ['理解目标', '探索并验证测试流程', '生成并检查草稿', '进入可编辑工作区'])
})
