import test from 'node:test'
import assert from 'node:assert/strict'
import {
  explorationTimeoutPayload,
  isExplorationTimeoutValid,
  normalizeExplorationTimeoutSettings
} from '../src/composables/webuiExplorationTimeout.js'

test('server settings provide the prefilled default and API bounds', () => {
  assert.deepEqual(normalizeExplorationTimeoutSettings({
    data: {
      exploration_timeout_seconds: 600,
      min_exploration_timeout_seconds: 60,
      max_exploration_timeout_seconds: 1800
    }
  }), { timeout: 600, min: 60, max: 1800 })
})

test('a user override is sent only when it is within the server bounds', () => {
  const settings = { timeout: 600, min: 60, max: 1800 }
  assert.deepEqual(explorationTimeoutPayload(900, settings), { exploration_timeout_seconds: 900 })
  assert.equal(isExplorationTimeoutValid(1801, settings), false)
  assert.deepEqual(explorationTimeoutPayload(1801, settings), {})
})

test('a failed settings load leaves the field empty and omits it from create payload', () => {
  assert.equal(normalizeExplorationTimeoutSettings({ data: {} }), null)
  assert.deepEqual(explorationTimeoutPayload(null), {})
})
