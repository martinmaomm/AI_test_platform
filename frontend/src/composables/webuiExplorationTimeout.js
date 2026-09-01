export const EXPLORATION_TIMEOUT_MIN_SECONDS = 60
export const EXPLORATION_TIMEOUT_MAX_SECONDS = 1800

export const normalizeExplorationTimeoutSettings = (response) => {
  const value = response?.data ?? response ?? {}
  const timeout = Number(value.exploration_timeout_seconds)
  const min = Number(value.min_exploration_timeout_seconds)
  const max = Number(value.max_exploration_timeout_seconds)
  if (!Number.isInteger(timeout) || !Number.isInteger(min) || !Number.isInteger(max) || min > max || timeout < min || timeout > max) return null
  return { timeout, min, max }
}

export const isExplorationTimeoutValid = (value, settings = null) => {
  if (value === null || value === undefined || value === '') return true
  const timeout = Number(value)
  const min = settings?.min ?? EXPLORATION_TIMEOUT_MIN_SECONDS
  const max = settings?.max ?? EXPLORATION_TIMEOUT_MAX_SECONDS
  return Number.isInteger(timeout) && timeout >= min && timeout <= max
}

export const explorationTimeoutPayload = (value, settings = null) => {
  if (!isExplorationTimeoutValid(value, settings) || value === null || value === undefined || value === '') return {}
  return { exploration_timeout_seconds: Number(value) }
}
