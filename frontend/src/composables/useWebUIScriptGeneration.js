import { computed, onUnmounted, ref, unref, watch } from 'vue'
import {
  applyWebUIScriptGenerationRepair,
  cancelWebUIScriptGeneration,
  createWebUIScriptGeneration,
  debugWebUIScriptGeneration,
  discardWebUIScriptGenerationRepair,
  getWebUIScriptGeneration,
  getWebUIScriptGenerations,
  getWebUITestCaseExecution,
  repairWebUIScriptGeneration,
  retryWebUIScriptGenerationFromTrace,
  resolveWebUIScriptGeneration,
  saveWebUIScriptGeneration,
  updateWebUIScriptGenerationDraft
} from '@/api/webTesting'
import {
  generationApiErrorMessage,
  generationStorageKey,
  isActiveGeneration,
  isCurrentRevisionVerified,
  isPausedGeneration,
  isTerminalGeneration,
  isWorkspaceActive,
  matchesGenerationWebSocketEvent
} from './webUIScriptGenerationPresentation'

const POLL_INTERVAL_MS = 2000

const apiData = (response) => response?.data ?? response
const emptyWorkspace = () => ({
  revision: 0,
  variables: [],
  verification: { status: 'unverified' },
  repair: { status: 'idle' }
})
const normalizeWorkspace = (workspace) => ({
  ...emptyWorkspace(),
  ...(workspace || {}),
  verification: { status: 'unverified', ...(workspace?.verification || {}) },
  repair: { status: 'idle', ...(workspace?.repair || {}) }
})
const cloneVariables = (variables) => (Array.isArray(variables) ? variables : []).map(item => ({
  name: item?.name || '',
  value: item?.is_secret ? '' : (item?.value || ''),
  is_secret: Boolean(item?.is_secret),
  required: Boolean(item?.required),
  description: item?.description || ''
}))
const repairAttempts = (record) => {
  const attempts = record?.workspace?.repair?.attempts
  return Array.isArray(attempts) ? attempts : []
}
const repairAttemptByExecutionId = (record, executionId) => {
  if (executionId === null || executionId === undefined) return null
  return repairAttempts(record).find(attempt => (
    attempt?.execution_id !== null
    && attempt?.execution_id !== undefined
    && String(attempt.execution_id) === String(executionId)
  )) || null
}
const latestRepairAttempt = (record) => [...repairAttempts(record)]
  .reverse()
  .find(attempt => attempt?.execution_id !== null && attempt?.execution_id !== undefined) || null

/**
 * Durable v5 generation state. localStorage intentionally stores only the
 * generation UUID. Draft source and secret/runtime values stay in memory.
 */
export function useWebUIScriptGeneration({ projectId, userId }) {
  const generation = ref(null)
  const loading = ref(false)
  const submitting = ref(false)
  const saving = ref(false)
  const cancelling = ref(false)
  const resolving = ref(false)
  const draftSaving = ref(false)
  const debugging = ref(false)
  const repairing = ref(false)
  const repairApplying = ref(false)
  const repairDiscarding = ref(false)
  const localDraft = ref(null)
  const draftConflict = ref(false)
  const debugExecution = ref(null)
  const debugExecutionLoading = ref(false)
  const repairExecution = ref(null)
  const repairExecutionLoading = ref(false)
  const selectedRepairExecutionId = ref(null)
  const lastError = ref('')
  const historyItems = ref([])
  const historyPage = ref(1)
  const historyPageSize = ref(20)
  const historyTotal = ref(0)
  const historyLoading = ref(false)
  const historyError = ref('')
  const historySwitching = ref(false)
  let pollingTimer = null
  let refreshPromise = null
  let refreshScope = ''
  let debugExecutionRequestId = null
  let repairExecutionRequestId = null
  let repairExecutionRequestVersion = 0
  let repairExecutionSelectionManual = false
  let scopeVersion = 0
  let historyLoadVersion = 0
  let historySwitchVersion = 0

  const currentProjectId = computed(() => unref(projectId))
  const currentUserId = computed(() => unref(userId))
  const storageKey = computed(() => generationStorageKey(currentUserId.value, currentProjectId.value))
  const isActive = computed(() => isActiveGeneration(generation.value?.status))
  const isPaused = computed(() => isPausedGeneration(generation.value?.status))
  const isTerminal = computed(() => isTerminalGeneration(generation.value?.status))
  const workspace = computed(() => generation.value?.workspace || emptyWorkspace())
  const isWorkspaceBusy = computed(() => isWorkspaceActive(workspace.value))
  const hasUnsavedDraft = computed(() => Boolean(localDraft.value?.dirty))
  const hasRepairCandidate = computed(() => ['candidate_ready', 'candidate_passed'].includes(workspace.value.repair?.status))
  const canApplyRepairCandidate = computed(() => hasRepairCandidate.value
    && Boolean(workspace.value.repair?.candidate_hash && workspace.value.repair?.candidate_script))
  const canDiscardRepairCandidate = computed(() => hasRepairCandidate.value
    && Boolean(workspace.value.repair?.candidate_hash))
  const canStartRepair = computed(() => {
    const verification = workspace.value.verification || {}
    return ['failed', 'error'].includes(verification.status)
      && Array.isArray(verification.diagnostics)
      && verification.diagnostics.length > 0
      && !isActive.value
      && !hasUnsavedDraft.value
      && !isWorkspaceBusy.value
      && !hasRepairCandidate.value
      && !repairing.value
      && !repairApplying.value
      && !repairDiscarding.value
  })
  const hasUnpersistedGeneration = computed(() => (
    Boolean(generation.value?.id) && !hasPersistedSaveResult(generation.value)
  ))
  const isHistorySwitchBlocked = computed(() => (
    historySwitching.value
    || submitting.value
    || saving.value
    || cancelling.value
    || resolving.value
    || draftSaving.value
    || debugging.value
    || repairing.value
    || repairApplying.value
    || repairDiscarding.value
    || isActive.value
    || isWorkspaceBusy.value
  ))

  const stopPolling = () => {
    if (pollingTimer) window.clearInterval(pollingTimer)
    pollingTimer = null
  }

  const resetGenerationWorkspace = ({ removeStorageKey = false } = {}) => {
    invalidateScope()
    stopPolling()
    generation.value = null
    localDraft.value = null
    draftConflict.value = false
    debugExecution.value = null
    repairExecution.value = null
    selectedRepairExecutionId.value = null
    repairExecutionSelectionManual = false
    lastError.value = ''
    refreshPromise = null
    refreshScope = ''
    historyItems.value = []
    historyPage.value = 1
    historyTotal.value = 0
    historyError.value = ''
    historyLoading.value = false
    historySwitching.value = false
    if (removeStorageKey) clearStoredGeneration()
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
    if ((!isActive.value || isPaused.value || isTerminal.value) && !isWorkspaceBusy.value) {
      stopPolling()
      return
    }
    if (!pollingTimer) pollingTimer = window.setInterval(() => refresh(), POLL_INTERVAL_MS)
  }

  const generationWithWorkspace = (value) => value ? { ...value, workspace: normalizeWorkspace(value.workspace) } : null
  const sourceDraft = (record) => {
    const priorSecretValues = new Map(
      String(localDraft.value?.generationId) === String(record?.id)
        ? (localDraft.value?.variables || []).filter(item => item.is_secret && item.value).map(item => [item.name, item.value])
        : []
    )
    const variables = cloneVariables(record?.workspace?.variables).map(item => (
      item.is_secret && !item.value && priorSecretValues.has(item.name)
        ? { ...item, value: priorSecretValues.get(item.name) }
        : item
    ))
    return {
      generationId: record?.id || null,
      revision: Number(record?.workspace?.revision ?? record?.revision ?? 0),
      script_draft: record?.script_draft || '',
      variables,
      dirty: false
    }
  }
  const resetLocalDraft = (record = generation.value) => {
    localDraft.value = record?.id ? sourceDraft(record) : null
    draftConflict.value = false
    return localDraft.value
  }
  const syncRepairExecutionSelection = (record, sameGeneration) => {
    const selectedAttempt = sameGeneration && repairExecutionSelectionManual
      ? repairAttemptByExecutionId(record, selectedRepairExecutionId.value)
      : null
    const nextAttempt = selectedAttempt || latestRepairAttempt(record)
    const nextExecutionId = nextAttempt?.execution_id ?? null
    if (!selectedAttempt) repairExecutionSelectionManual = false
    if (sameGeneration && String(selectedRepairExecutionId.value ?? '') === String(nextExecutionId ?? '')) return
    selectedRepairExecutionId.value = nextExecutionId
    repairExecution.value = null
    repairExecutionLoading.value = false
    repairExecutionRequestId = null
    repairExecutionRequestVersion += 1
  }
  const applyGeneration = (value, { forceDraftSync = false } = {}) => {
    const previousGenerationId = generation.value?.id
    const record = generationWithWorkspace(value)
    generation.value = record
    const incomingExecutionId = record?.workspace?.verification?.execution_id
    if (incomingExecutionId && String(debugExecution.value?.execution || '') !== String(incomingExecutionId)) debugExecution.value = null
    syncRepairExecutionSelection(
      record,
      previousGenerationId !== null
        && previousGenerationId !== undefined
        && String(previousGenerationId) === String(record?.id)
    )
    const sameGeneration = record?.id && localDraft.value?.generationId && String(record.id) === String(localDraft.value.generationId)
    if (record?.id && (!sameGeneration || !localDraft.value?.dirty || forceDraftSync)) resetLocalDraft(record)
    if (generation.value?.id) persistGenerationId(generation.value.id)
    if ((isTerminalGeneration(generation.value?.status) || isPausedGeneration(generation.value?.status)) && !isWorkspaceBusy.value) stopPolling()
    else updatePolling()
    return generation.value
  }

  const invalidateScope = () => {
    scopeVersion += 1
    loading.value = false
    submitting.value = false
    saving.value = false
    cancelling.value = false
    resolving.value = false
    draftSaving.value = false
    debugging.value = false
    repairing.value = false
    repairApplying.value = false
    repairDiscarding.value = false
    debugExecutionLoading.value = false
    debugExecutionRequestId = null
    repairExecutionLoading.value = false
    repairExecutionRequestId = null
    repairExecutionRequestVersion += 1
    repairExecutionSelectionManual = false
    historyLoadVersion += 1
    historySwitchVersion += 1
    historyLoading.value = false
  }
  const isCurrentScope = (requestScope, requestProjectId) => (
    requestScope === scopeVersion && String(currentProjectId.value || '') === String(requestProjectId || '')
  )
  const isCurrentGenerationScope = (requestScope, requestProjectId, generationId) => (
    isCurrentScope(requestScope, requestProjectId) && String(generation.value?.id || '') === String(generationId || '')
  )
  const hasPersistedSaveResult = (payload) => {
    const testCaseId = payload?.test_case_id
    if (testCaseId === null || testCaseId === undefined) return false
    if (typeof testCaseId !== 'number' && typeof testCaseId !== 'string') return false
    if (typeof testCaseId === 'string' && testCaseId.trim() === '') return false
    const normalizedId = Number(testCaseId)
    return Number.isSafeInteger(normalizedId) && normalizedId > 0
  }

  const loadHistory = async (page = historyPage.value) => {
    const requestProjectId = currentProjectId.value
    if (!requestProjectId) return null
    const requestScope = scopeVersion
    const requestVersion = ++historyLoadVersion
    const requestPage = Math.max(1, Number(page) || 1)
    historyLoading.value = true
    historyError.value = ''
    try {
      const response = await getWebUIScriptGenerations(requestProjectId, {
        page: requestPage,
        page_size: historyPageSize.value
      })
      if (!isCurrentScope(requestScope, requestProjectId) || requestVersion !== historyLoadVersion) return null
      const data = apiData(response)
      if (response?.success === false || !Array.isArray(data?.items)) throw new Error(response?.message || '读取生成记录列表失败')
      historyItems.value = data.items
      historyPage.value = Math.max(1, Number(data.page) || requestPage)
      historyPageSize.value = Math.min(50, Math.max(1, Number(data.page_size) || historyPageSize.value))
      historyTotal.value = Math.max(0, Number(data.total) || 0)
      return data
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || requestVersion !== historyLoadVersion) return null
      historyError.value = generationApiErrorMessage(error, '读取生成记录列表失败')
      return null
    } finally {
      if (isCurrentScope(requestScope, requestProjectId) && requestVersion === historyLoadVersion) historyLoading.value = false
    }
  }

  const openHistoryGeneration = async (generationId) => {
    if (!generationId || String(generation.value?.id || '') === String(generationId)) return generation.value
    if (isHistorySwitchBlocked.value) return null
    invalidateScope()
    stopPolling()
    const requestProjectId = currentProjectId.value
    const requestScope = scopeVersion
    const requestVersion = ++historySwitchVersion
    historySwitching.value = true
    historyError.value = ''
    try {
      const response = await getWebUIScriptGeneration(requestProjectId, generationId)
      if (!isCurrentScope(requestScope, requestProjectId) || requestVersion !== historySwitchVersion) return null
      const record = apiData(response)
      if (response?.success === false || !record?.id || String(record.id) !== String(generationId)) throw new Error(response?.message || '读取生成记录失败')
      debugExecution.value = null
      repairExecution.value = null
      selectedRepairExecutionId.value = null
      const applied = applyGeneration(record)
      void loadDebugExecution(applied)
      void loadSelectedRepairExecution(applied)
      return applied
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || requestVersion !== historySwitchVersion) return null
      historyError.value = generationApiErrorMessage(error, '读取生成记录失败')
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId) && requestVersion === historySwitchVersion) historySwitching.value = false
    }
  }

  const refresh = async (generationId = generation.value?.id) => {
    const requestProjectId = currentProjectId.value
    if (!generationId || !requestProjectId || submitting.value || historySwitching.value) return null
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
        const applied = applyGeneration(record)
        void loadDebugExecution(applied)
        void loadSelectedRepairExecution(applied)
        return applied
      } catch (error) {
        if (!isCurrentScope(requestScope, requestProjectId)) return null
        if ([403, 404].includes(error?.response?.status)) { clearStoredGeneration(); generation.value = null; stopPolling(); return null }
        lastError.value = generationApiErrorMessage(error, '读取生成记录失败')
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
    resetGenerationWorkspace()
    if (!currentProjectId.value || !currentUserId.value || typeof window === 'undefined') return null
    const generationId = window.localStorage.getItem(storageKey.value)
    return generationId ? refresh(generationId) : null
  }

  const create = async (payload) => {
    const requestProjectId = currentProjectId.value
    if (!requestProjectId) throw new Error('请先选择项目')
    if (submitting.value || historySwitching.value || isActive.value || isWorkspaceBusy.value || saving.value || resolving.value || draftSaving.value || debugging.value || repairing.value || repairApplying.value || repairDiscarding.value) return null
    invalidateScope()
    stopPolling()
    const requestScope = scopeVersion
    submitting.value = true
    lastError.value = ''
    try {
      const response = await createWebUIScriptGeneration(requestProjectId, payload)
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const record = apiData(response)
      if (response?.success === false || !record?.id) throw new Error(response?.message || '创建生成任务失败')
      debugExecution.value = null
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      lastError.value = generationApiErrorMessage(error, '创建生成任务失败')
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) {
        submitting.value = false
        updatePolling()
      }
    }
  }

  const cancel = async () => {
    if (!generation.value?.id || historySwitching.value || cancelling.value) return null
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
      lastError.value = generationApiErrorMessage(error, '取消失败')
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) cancelling.value = false
    }
  }

  const save = async (title) => {
    if (!generation.value?.id || historySwitching.value || saving.value || isWorkspaceBusy.value || hasRepairCandidate.value || repairing.value || repairApplying.value || repairDiscarding.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    saving.value = true
    try {
      if (hasUnsavedDraft.value) await saveDraft()
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const revision = Number(localDraft.value?.revision ?? generation.value?.workspace?.revision ?? 0)
      const mode = isCurrentRevisionVerified(generation.value?.workspace, revision) ? 'verified' : 'draft'
      const response = await saveWebUIScriptGeneration(requestProjectId, generationId, {
        ...(title ? { title } : {}), mode, expected_revision: revision
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const data = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '保存失败')
      if (hasPersistedSaveResult(data)) {
        resetGenerationWorkspace({ removeStorageKey: true })
        return data
      }
      throw new Error('保存响应缺少测试用例标识，无法确认保存结果')
    } catch (error) {
      if (!isCurrentGenerationScope(requestScope, requestProjectId, generationId)) return null
      lastError.value = generationApiErrorMessage(error, '保存失败')
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) saving.value = false
    }
  }

  const updateLocalDraft = (draft) => {
    if (!generation.value?.id || historySwitching.value || !draft || String(draft.generationId || generation.value.id) !== String(generation.value.id)) return
    localDraft.value = {
      generationId: generation.value.id,
      revision: Number(draft.revision ?? localDraft.value?.revision ?? generation.value.workspace?.revision ?? 0),
      script_draft: draft.script_draft || '',
      variables: cloneVariables(draft.variables),
      dirty: true
    }
    draftConflict.value = false
  }

  const saveDraft = async () => {
    if (!generation.value?.id || historySwitching.value || !localDraft.value || draftSaving.value || isWorkspaceBusy.value || hasRepairCandidate.value || repairing.value || repairApplying.value || repairDiscarding.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    draftSaving.value = true
    lastError.value = ''
    try {
      const response = await updateWebUIScriptGenerationDraft(requestProjectId, generationId, {
        script_draft: localDraft.value.script_draft,
        // Secret defaults are page-memory only. The backend receives their definitions
        // but never their values; debug supplies those values as one-time runtime input.
        variables: localDraft.value.variables.map(item => ({
          ...item,
          value: item.is_secret ? '' : item.value
        })),
        expected_revision: Number(localDraft.value.revision || 0)
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '保存草稿失败')
      return applyGeneration(record, { forceDraftSync: true })
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      if (error?.response?.status === 409) {
        draftConflict.value = true
        lastError.value = '工作区已在其他位置更新。你的本地修改仍已保留；请确认后刷新最新版本。'
      } else {
        lastError.value = generationApiErrorMessage(error, '保存草稿失败')
      }
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) draftSaving.value = false
    }
  }

  const debug = async (runtimeVariables = []) => {
    if (!generation.value?.id || historySwitching.value || debugging.value || isWorkspaceBusy.value || hasRepairCandidate.value || repairing.value || repairApplying.value || repairDiscarding.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    if (hasUnsavedDraft.value) await saveDraft()
    if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
    debugging.value = true
    lastError.value = ''
    try {
      const response = await debugWebUIScriptGeneration(requestProjectId, generationId, {
        expected_revision: Number(localDraft.value?.revision ?? generation.value.workspace?.revision ?? 0),
        confirm_execution: true,
        runtime_variables: mergedRuntimeVariables(runtimeVariables)
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '启动调试失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      if (error?.response?.status === 409) draftConflict.value = true
      lastError.value = generationApiErrorMessage(error, '启动调试失败')
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) debugging.value = false
    }
  }

  const mergedRuntimeVariables = (runtimeVariables = []) => {
    const variableDefinitions = new Map((localDraft.value?.variables || []).map(item => [item.name, item]))
    const runtimeByName = new Map((localDraft.value?.variables || [])
      .filter(item => item.is_secret && item.value)
      .map(item => [item.name, { name: item.name, value: item.value, is_secret: true }]))
    ;(runtimeVariables || []).forEach(item => {
      if (!item?.name || item.value === undefined || item.value === '') return
      runtimeByName.set(item.name, {
        name: item.name,
        value: item.value,
        is_secret: Boolean(variableDefinitions.get(item.name)?.is_secret)
      })
    })
    return [...runtimeByName.values()]
  }

  const repair = async (runtimeVariables = []) => {
    if (!generation.value?.id || historySwitching.value || !canStartRepair.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    repairing.value = true
    lastError.value = ''
    try {
      const response = await repairWebUIScriptGeneration(requestProjectId, generationId, {
        expected_revision: Number(localDraft.value?.revision ?? generation.value.workspace?.revision ?? 0),
        confirm_execution: true,
        runtime_variables: mergedRuntimeVariables(runtimeVariables)
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '启动 AI 分析并修复失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      if (error?.response?.status === 409) {
        draftConflict.value = true
      }
      lastError.value = generationApiErrorMessage(error, '启动 AI 分析并修复失败')
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) repairing.value = false
    }
  }

  const applyRepairCandidate = async (candidateHash = generation.value?.workspace?.repair?.candidate_hash) => {
    if (
      !generation.value?.id ||
      historySwitching.value ||
      !canApplyRepairCandidate.value ||
      candidateHash !== workspace.value.repair?.candidate_hash ||
      repairApplying.value ||
      isWorkspaceBusy.value
    ) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    repairApplying.value = true
    lastError.value = ''
    try {
      const response = await applyWebUIScriptGenerationRepair(requestProjectId, generationId, {
        expected_revision: Number(localDraft.value?.revision ?? generation.value.workspace?.revision ?? 0),
        candidate_hash: candidateHash
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '采用修复候选失败')
      return applyGeneration(record, { forceDraftSync: true })
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      if (error?.response?.status === 409) {
        draftConflict.value = true
      }
      lastError.value = generationApiErrorMessage(error, '采用修复候选失败')
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) repairApplying.value = false
    }
  }

  const discardRepairCandidate = async (candidateHash = generation.value?.workspace?.repair?.candidate_hash) => {
    if (
      !generation.value?.id ||
      historySwitching.value ||
      !canDiscardRepairCandidate.value ||
      candidateHash !== workspace.value.repair?.candidate_hash ||
      repairDiscarding.value ||
      repairApplying.value ||
      isWorkspaceBusy.value
    ) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    repairDiscarding.value = true
    lastError.value = ''
    try {
      const response = await discardWebUIScriptGenerationRepair(requestProjectId, generationId, {
        expected_revision: Number(localDraft.value?.revision ?? generation.value.workspace?.revision ?? 0),
        candidate_hash: candidateHash
      })
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '放弃修复候选失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId) || String(generation.value?.id) !== String(generationId)) return null
      if (error?.response?.status === 409) draftConflict.value = true
      lastError.value = generationApiErrorMessage(error, '放弃修复候选失败')
      throw error
    } finally {
      if (isCurrentGenerationScope(requestScope, requestProjectId, generationId)) repairDiscarding.value = false
    }
  }

  const loadDebugExecution = async (record = generation.value) => {
    const verification = record?.workspace?.verification || {}
    const executionId = verification.execution_id
    if (
      !executionId ||
      !['passed', 'incomplete', 'failed', 'error'].includes(verification.status) ||
      (debugExecutionLoading.value && String(debugExecutionRequestId) === String(executionId))
    ) return null
    const requestProjectId = currentProjectId.value
    const requestGenerationId = record?.id
    debugExecutionLoading.value = true
    debugExecutionRequestId = executionId
    try {
      const response = await getWebUITestCaseExecution(requestProjectId, executionId)
      const detail = apiData(response)
      if (
        String(generation.value?.id) !== String(requestGenerationId) ||
        String(currentProjectId.value) !== String(requestProjectId) ||
        String(generation.value?.workspace?.verification?.execution_id) !== String(executionId)
      ) return null
      debugExecution.value = detail ? { ...detail, project_id: detail.project_id || requestProjectId } : null
      return debugExecution.value
    } catch {
      // The debug task may not expose detail immediately. The workspace's safe summary remains visible.
      return null
    } finally {
      if (String(debugExecutionRequestId) === String(executionId)) {
        debugExecutionLoading.value = false
        debugExecutionRequestId = null
      }
    }
  }

  const requestRepairExecution = async (executionId, record = generation.value) => {
    const attempt = repairAttemptByExecutionId(record, executionId)
    const requestProjectId = currentProjectId.value
    const requestGenerationId = record?.id
    if (
      !attempt
      || !requestProjectId
      || !requestGenerationId
      || String(generation.value?.id) !== String(requestGenerationId)
      || (repairExecutionLoading.value && String(repairExecutionRequestId) === String(executionId))
    ) return null
    const requestVersion = ++repairExecutionRequestVersion
    repairExecutionLoading.value = true
    repairExecutionRequestId = executionId
    try {
      const response = await getWebUITestCaseExecution(requestProjectId, executionId)
      const detail = apiData(response)
      if (
        requestVersion !== repairExecutionRequestVersion ||
        String(generation.value?.id) !== String(requestGenerationId) ||
        String(currentProjectId.value) !== String(requestProjectId) ||
        String(selectedRepairExecutionId.value) !== String(executionId) ||
        !repairAttemptByExecutionId(generation.value, executionId)
      ) return null
      repairExecution.value = detail ? { ...detail, project_id: detail.project_id || requestProjectId } : null
      return repairExecution.value
    } catch {
      // The repair task can publish its summary before its execution detail is readable.
      return null
    } finally {
      if (requestVersion === repairExecutionRequestVersion) {
        repairExecutionLoading.value = false
        repairExecutionRequestId = null
      }
    }
  }

  const loadSelectedRepairExecution = (record = generation.value) => (
    requestRepairExecution(selectedRepairExecutionId.value, record)
  )

  const loadRepairExecution = (executionId) => {
    const attempt = repairAttemptByExecutionId(generation.value, executionId)
    if (!attempt) return Promise.resolve(null)
    repairExecutionSelectionManual = true
    if (String(selectedRepairExecutionId.value) !== String(attempt.execution_id)) {
      selectedRepairExecutionId.value = attempt.execution_id
      repairExecution.value = null
      repairExecutionLoading.value = false
      repairExecutionRequestId = null
      repairExecutionRequestVersion += 1
    }
    return requestRepairExecution(attempt.execution_id, generation.value)
  }

  const discardLocalDraftAndRefresh = async () => {
    if (!generation.value?.id || historySwitching.value) return null
    resetLocalDraft(generation.value)
    return refresh(generation.value.id)
  }

  const resolve = async (payload) => {
    if (!generation.value?.id || historySwitching.value || resolving.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    resolving.value = true
    lastError.value = ''
    try {
      const response = await resolveWebUIScriptGeneration(requestProjectId, generationId, payload)
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '提交补充信息失败')
      return applyGeneration(record)
    } catch (error) {
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const latest = error?.response?.data?.data
      if (latest?.id) applyGeneration(latest)
      lastError.value = generationApiErrorMessage(error, '提交补充信息失败')
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) resolving.value = false
    }
  }

  const retryGeneration = async () => {
    if (!generation.value?.id || historySwitching.value || resolving.value || isActive.value || isWorkspaceBusy.value || hasRepairCandidate.value || repairing.value || repairApplying.value || repairDiscarding.value) return null
    const requestProjectId = currentProjectId.value
    const generationId = generation.value.id
    const requestScope = scopeVersion
    resolving.value = true
    try {
      const response = await retryWebUIScriptGenerationFromTrace(requestProjectId, generationId, {
        expected_revision: Number(generation.value.revision || 0)
      })
      if (!isCurrentScope(requestScope, requestProjectId)) return null
      const record = apiData(response)
      if (response?.success === false) throw new Error(response?.message || '仅重试脚本生成失败')
      return applyGeneration(record)
    } catch (error) {
      lastError.value = generationApiErrorMessage(error, '仅重试脚本生成失败')
      throw error
    } finally {
      if (isCurrentScope(requestScope, requestProjectId)) resolving.value = false
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
    generation, workspace, localDraft, loading, submitting, saving, cancelling, resolving,
    draftSaving, debugging, repairing, repairApplying, repairDiscarding, debugExecution, debugExecutionLoading, repairExecution, repairExecutionLoading, selectedRepairExecutionId, draftConflict,
    lastError, isActive, isPaused, isTerminal, isWorkspaceBusy, hasUnsavedDraft, hasUnpersistedGeneration, hasRepairCandidate, canApplyRepairCandidate, canDiscardRepairCandidate, canStartRepair,
    historyItems, historyPage, historyPageSize, historyTotal, historyLoading, historyError, historySwitching, isHistorySwitchBlocked,
    create, refresh, restore, loadHistory, openHistoryGeneration, cancel, resolve, retryGeneration, save, saveDraft, debug, repair, applyRepairCandidate, discardRepairCandidate, updateLocalDraft,
    discardLocalDraftAndRefresh, loadRepairExecution, stopPolling, handleWebSocketEvent, clearStoredGeneration, storageKey
  }
}
