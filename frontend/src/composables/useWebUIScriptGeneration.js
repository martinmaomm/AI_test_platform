import { computed, onUnmounted, ref, unref, watch } from 'vue'
import {
  cancelWebUIScriptGeneration,
  createWebUIScriptGeneration,
  getWebUIScriptGeneration,
  saveWebUIScriptGeneration
} from '@/api/webTesting'
import {
  generationStorageKey,
  isActiveGeneration,
  isPausedGeneration,
  isTerminalGeneration,
  matchesGenerationWebSocketEvent
} from './webUIScriptGenerationPresentation'

const POLL_INTERVAL_MS = 2000

const apiData = (response) => response?.data ?? response

/**
 * Durable V2 generation state.  localStorage intentionally stores only the
 * generation UUID, scoped to the current user and project.
 */
export function useWebUIScriptGeneration({ projectId, userId }) {
  const generation = ref(null)
  const loading = ref(false)
  const submitting = ref(false)
  const saving = ref(false)
  const cancelling = ref(false)
  const lastError = ref('')
  let pollingTimer = null
  let refreshPromise = null
  let refreshScope = ''
  let scopeVersion = 0

  const currentProjectId = computed(() => unref(projectId))
  const currentUserId = computed(() => unref(userId))
  const storageKey = computed(() => generationStorageKey(currentUserId.value, currentProjectId.value))
  const isActive = computed(() => isActiveGeneration(generation.value?.status))
  const isPaused = computed(() => isPausedGeneration(generation.value?.status))
  const isTerminal = computed(() => isTerminalGeneration(generation.value?.status))

  const stopPolling = () => {
    if (pollingTimer) window.clearInterval(pollingTimer)
    pollingTimer = null
  }

  const clearStoredGeneration = () => {
    if (typeof window !== 'undefined') window.localStorage.removeItem(storageKey.value)
  }

  const persistGenerationId = (generationId) => {
    if (generationId && typeof window !== 'undefined') {
      window.localStorage.setItem(storageKey.value, String(generationId))
    }
  }

  const updatePolling = () => {
    if (!isActive.value || isPaused.value || isTerminal.value) {
      stopPolling()
      return
    }
    if (!pollingTimer) pollingTimer = window.setInterval(() => refresh(), POLL_INTERVAL_MS)
  }

  const applyGeneration = (value) => {
    generation.value = value || null
    if (generation.value?.id) persistGenerationId(generation.value.id)
    if (isTerminalGeneration(generation.value?.status) || isPausedGeneration(generation.value?.status)) stopPolling()
    else updatePolling()
    return generation.value
  }

  const invalidateScope = () => {
    scopeVersion += 1
    loading.value = false
    submitting.value = false
    saving.value = false
    cancelling.value = false
  }
  const isCurrentScope = (requestScope, requestProjectId) => (
    requestScope === scopeVersion && String(currentProjectId.value || '') === String(requestProjectId || '')
  )

  const refresh = async (generationId = generation.value?.id) => {
    const requestProjectId = currentProjectId.value
    if (!generationId || !requestProjectId) return null
    const requestScope = scopeVersion
    const requestKey = `${requestScope}:${requestProjectId}:${generationId}`
    if (refreshPromise && refreshScope === requestKey) return refreshPromise
    loading.value = true
    let request
    request = (async () => {
      try {
        const response = await getWebUIScriptGeneration(requestProjectId, generationId)
        if (!isCurrentScope(requestScope, requestProjectId)) return null
        const record = apiData(response)
        if (response?.success === false) throw new Error(response?.message || '无法读取生成记录')
        lastError.value = ''
        return applyGeneration(record)
      } catch (error) {
        if (!isCurrentScope(requestScope, requestProjectId)) return null
        if ([403, 404].includes(error?.response?.status)) { clearStoredGeneration(); generation.value = null; stopPolling(); return null }
        lastError.value = error?.response?.data?.message || error?.message || '读取生成记录失败'
        return null
      } finally {
        if (refreshPromise === request) {
          refreshPromise = null
          refreshScope = ''
        }
        if (isCurrentScope(requestScope, requestProjectId)) loading.value = false
      }
    })()
    refreshPromise = request
    refreshScope = requestKey
    return request
  }

  const restore = async () => {
    invalidateScope()
    stopPolling()
    generation.value = null
    lastError.value = ''
    if (!currentProjectId.value || !currentUserId.value || typeof window === 'undefined') return null
    const generationId = window.localStorage.getItem(storageKey.value)
    return generationId ? refresh(generationId) : null
  }

  const create = async (payload) => {
    const requestProjectId = currentProjectId.value
    if (!requestProjectId) throw new Error('请先选择项目')
    invalidateScope()
    const requestScope = scopeVersion
    submitting.value = true
    lastError.value = ''
    try {
      const response = await createWebUIScriptGeneration(requestProjectId, payload)
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const record = apiData(response)
      if (response?.success === false || !record?.id) throw new Error(response?.message || '创建生成任务失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      lastError.value = error?.response?.data?.message || error?.message || '创建生成任务失败'
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) submitting.value = false
    }
  }

  const cancel = async () => {
    if (!generation.value?.id || cancelling.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    invalidateScope()
    const requestScope = scopeVersion
    stopPolling()
    cancelling.value = true
    try {
      const response = await cancelWebUIScriptGeneration(requestProjectId, generationId)
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '取消失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      lastError.value = error?.response?.data?.message || error?.message || '取消失败'
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) cancelling.value = false
    }
  }

  const save = async (title) => {
    if (!generation.value?.id || saving.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    invalidateScope()
    const requestScope = scopeVersion
    saving.value = true
    try {
      const response = await saveWebUIScriptGeneration(requestProjectId, generationId, title ? { title } : {})
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const data = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '保存失败')
      if (data?.generation) applyGeneration(data.generation)
      return data
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      lastError.value = error?.response?.data?.message || error?.message || '保存失败'
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) saving.value = false
    }
  }

  // WebSocket only wakes an API refresh. It never mutates the durable state.
  const handleWebSocketEvent = (message) => {
    const record = generation.value
    if (!matchesGenerationWebSocketEvent(message, record)) return false
    refresh()
    return true
  }

  watch([currentProjectId, currentUserId], restore, { immediate: true })
  onUnmounted(stopPolling)

  return {
    generation, loading, submitting, saving, cancelling, lastError,
    isActive, isPaused, isTerminal,
    create, refresh, restore, cancel, save, stopPolling, handleWebSocketEvent,
    clearStoredGeneration, storageKey
  }
}
