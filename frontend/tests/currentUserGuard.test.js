import test from 'node:test'
import assert from 'node:assert/strict'
import { isCurrentUserAuthenticationFailure } from '../src/utils/sessionErrors.js'
import { currentUserFailureLocation } from '../src/utils/routeAuth.js'
import { readFile } from 'node:fs/promises'

test('current-user 503 keeps a valid local session and routes once to retry UI, not login', () => {
  const to = { fullPath: '/settings/users' }
  assert.equal(isCurrentUserAuthenticationFailure({ response: { status: 503 } }), false)
  assert.deepEqual(currentUserFailureLocation(to, false), {
    name: 'PermissionLoadFailure', query: { redirect: '/settings/users' }
  })
})

test('only confirmed authentication failures route to login', () => {
  const to = { fullPath: '/ai-config' }
  assert.equal(isCurrentUserAuthenticationFailure({ response: { status: 401 } }), true)
  assert.deepEqual(currentUserFailureLocation(to, true), {
    path: '/login', query: { redirect: '/ai-config' }
  })
})

test('permission retry page clears local auth before sending a user to login', async () => {
  const source = await readFile(new URL('../src/views/PermissionLoadFailure.vue', import.meta.url), 'utf8')
  assert.match(source, /await authStore\.logout\(\)/)
  assert.match(source, /router\.replace\('\/login'\)/)
})
