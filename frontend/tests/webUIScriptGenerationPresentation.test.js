import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildGenerationTimeline,
  generationActionRequired,
  isCurrentRevisionVerified,
  isWorkspaceActive
} from '../src/composables/webUIScriptGenerationPresentation.js'

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

test('timeline uses understandable generation stages rather than execution-pass labels', () => {
  const labels = buildGenerationTimeline({ status: 'ready', current_stage: 'completed' }).map(item => item.label)
  assert.deepEqual(labels, ['理解目标', '只读探索页面', '生成并检查草稿', '进入可编辑工作区'])
})
