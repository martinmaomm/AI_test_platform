import test from 'node:test'
import assert from 'node:assert/strict'
import { canAccessRoute, canEditManagedUser, canManageProjectMetadata, visibleDashboardLayout } from '../src/utils/accessControl.js'

const user = { id: 1, role: 'user', is_staff: false, is_superuser: false }
const admin = { id: 2, role: 'admin', is_staff: true, is_superuser: false }
const superuser = { id: 3, role: 'admin', is_staff: true, is_superuser: true }

test('ordinary users cannot enter global configuration routes or manage project metadata', () => {
  assert.equal(canAccessRoute(user, { meta: { requiresPlatformAdmin: true } }), false)
  assert.equal(canManageProjectMetadata(user), false)
  assert.equal(canAccessRoute(admin, { meta: { requiresPlatformAdmin: true } }), true)
})

test('dashboard filtering removes restricted cards even from a saved layout', () => {
  const layout = [{ i: 'portal-api' }, { i: 'section-infra' }, { i: 'portal-ai-config' }, { i: 'portal-settings' }]
  assert.deepEqual(visibleDashboardLayout(layout, user).map((item) => item.i), ['portal-api'])
  assert.equal(visibleDashboardLayout(layout, admin).length, 4)
})

test('only superusers can modify administrators and no one modifies self or superusers', () => {
  assert.equal(canEditManagedUser(admin, { id: 4, role: 'user' }), true)
  assert.equal(canEditManagedUser(admin, { id: 4, role: 'admin', is_staff: true }), false)
  assert.equal(canEditManagedUser(superuser, { id: 4, role: 'admin', is_staff: true }), true)
  assert.equal(canEditManagedUser(superuser, superuser), false)
  assert.equal(canEditManagedUser(superuser, { id: 5, is_superuser: true }), false)
})
