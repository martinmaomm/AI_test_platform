import test from 'node:test'
import assert from 'node:assert/strict'
import { isSessionExpiredError } from '../src/utils/sessionErrors.js'

test('SMTP and model authentication errors never log the platform user out', () => {
  for (const message of ['SMTP 认证失败，请检查发件邮箱或授权码', 'Invalid email address', '模型 token 已过期', '被测网站登录失败', 'Unauthorized upstream']) {
    for (const data of [{ detail: message }, { message }, { error: { message } }]) {
      assert.equal(isSessionExpiredError({ response: { status: 400, data } }), false)
    }
  }
})

test('real platform authentication failures still trigger reauthentication', () => {
  assert.equal(isSessionExpiredError({ response: { status: 401 } }), true)
  for (const data of [{ code: 'token_not_valid' }, { error: { details: { code: 'token_not_valid' } } }]) {
    assert.equal(isSessionExpiredError({ response: { status: 400, data } }), true)
  }
  assert.equal(isSessionExpiredError({ response: { status: 403, data: { message: '权限不足' } } }), false)
  assert.equal(isSessionExpiredError(new Error('Network Error')), false)
})
