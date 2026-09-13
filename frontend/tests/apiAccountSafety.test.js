import test from 'node:test';
import assert from 'node:assert/strict';
import { currentScenarioStatus, normalizeDraft } from '../src/views/api-testing/apiWorkspace.js';

test('account protection keeps a current debug result reviewable instead of generation failed', () => {
  for (const error_type of ['AccountSafetyBlocked', 'AccountSafetyReview']) {
    const scene = {revision: 3, debug_revision: 3, status: 'ready',
      debug_result: {success: false, status: 'failed', error_type}};
    assert.equal(currentScenarioStatus(scene), 'needs_review');
    assert.equal(currentScenarioStatus({...scene, debug_revision: 2, generation: {status: 'stale'}}), 'stale');
  }
});

test('visual editing retains step account protection metadata', () => {
  const account_safety = [{operation: 'mutate', entity: 'account', effect: '角色分配',
    target: {source: 'json', path: ['principalRef']}}];
  const draft = normalizeDraft({config: {variables: {}}, teststeps: [
    {name: '临时账号测试', request: {method: 'POST', url: '/principals'}, account_safety},
  ]});
  assert.deepEqual(draft.teststeps[0].account_safety, account_safety);
});
