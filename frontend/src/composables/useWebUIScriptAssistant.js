import { computed, onBeforeUnmount, ref, unref, watch } from 'vue'
import { getAvailableLLMConfigurations } from '@/api/aiConfig'
import { applyWebUIScriptAssistant, cancelWebUIScriptAssistant, createWebUIScriptAssistant, getWebUIScriptAssistant, getWebUIScriptAssistants, resetWebUIScriptAssistant, sendWebUIScriptAssistantMessage, verifyWebUIScriptAssistant } from '@/api/webTesting'
import { assistantErrorMessage, assistantList, assistantListParams, assistantsForContext, isAssistantActive, unwrapAssistantResponse } from './webUIScriptAssistantPresentation'

const POLL_INTERVAL_MS = 1500
const MAX_POLL_FAILURES = 3
const configuredModels = response => assistantList(response).filter(item => item?.is_active && item?.model_type === 'llm')
const defaultRequests = {
  apply: applyWebUIScriptAssistant,
  cancel: cancelWebUIScriptAssistant,
  create: createWebUIScriptAssistant,
  get: getWebUIScriptAssistant,
  list: getWebUIScriptAssistants,
  message: sendWebUIScriptAssistantMessage,
  reset: resetWebUIScriptAssistant,
  verify: verifyWebUIScriptAssistant
}

export const useWebUIScriptAssistant = ({ projectId, context, requests = defaultRequests, loadModelConfigs = getAvailableLLMConfigurations }) => {
  const assistants = ref([])
  const assistant = ref(null)
  const modelConfigs = ref([])
  const loading = ref(false)
  const loadingModels = ref(false)
  const acting = ref(false)
  const lastError = ref('')
  const refreshRequired = ref(false)
  let pollTimer = null
  let requestVersion = 0
  let scopeVersion = 0
  let pollFailures = 0

  const active = computed(() => isAssistantActive(assistant.value))
  const currentProjectId = () => unref(projectId)
  const currentContext = () => unref(context) || {}
  const inScope = scope => scope === scopeVersion
  const stopPolling = () => { if (pollTimer) clearTimeout(pollTimer); pollTimer = null }
  const resetScope = () => {
    scopeVersion += 1
    requestVersion += 1
    pollFailures = 0
    stopPolling()
    assistants.value = []
    assistant.value = null
    loading.value = false
    acting.value = false
    lastError.value = ''
    refreshRequired.value = false
  }
  const schedulePoll = () => {
    stopPolling()
    const assistantId = assistant.value?.id
    const scope = scopeVersion
    if (!assistantId || !active.value) return
    pollTimer = setTimeout(() => loadAssistant(assistantId, { quiet: true, scope }), POLL_INTERVAL_MS)
  }
  const selectAssistant = value => {
    assistant.value = value ? unwrapAssistantResponse(value) : null
    schedulePoll()
    return assistant.value
  }
  const replaceAssistant = value => {
    const index = assistants.value.findIndex(item => String(item.id) === String(value.id))
    assistants.value = index < 0 ? [value, ...assistants.value] : assistants.value.map((item, itemIndex) => itemIndex === index ? value : item)
    return selectAssistant(value)
  }
  const loadModels = async (scope = scopeVersion) => {
    loadingModels.value = true
    try {
      const models = configuredModels(await loadModelConfigs())
      if (inScope(scope)) modelConfigs.value = models
    } catch {
      if (inScope(scope)) modelConfigs.value = []
    } finally { if (inScope(scope)) loadingModels.value = false }
  }
  const loadRecent = async (scope = scopeVersion) => {
    const project = currentProjectId()
    if (!project || !inScope(scope) || loading.value || acting.value) return
    stopPolling()
    const request = ++requestVersion
    loading.value = true
    try {
      const items = assistantsForContext(assistantList(await requests.list(project, assistantListParams(currentContext()))), currentContext())
      if (!inScope(scope) || request !== requestVersion) return
      assistants.value = items
      lastError.value = ''
      refreshRequired.value = false
      const selectedId = assistant.value?.id
      const selected = items.find(item => String(item.id) === String(selectedId)) || items[0] || null
      stopPolling()
      assistant.value = selected
      if (!selected?.id) return
      const detail = unwrapAssistantResponse(await requests.get(project, selected.id))
      if (!inScope(scope) || request !== requestVersion || detail?.mode !== currentContext().mode) return
      refreshRequired.value = false
      replaceAssistant(detail)
    } catch (error) {
      if (inScope(scope) && request === requestVersion && assistant.value?.id) schedulePoll()
      if (inScope(scope) && request === requestVersion) {
        if (currentContext().mode === 'edit') {
          refreshRequired.value = true
          lastError.value = '加载 AI 会话失败，请重新获取状态。'
        } else {
          refreshRequired.value = true
          lastError.value = assistantErrorMessage(error, '加载修复记录失败，请刷新修复状态后再重新分析。')
        }
      }
    } finally { if (inScope(scope) && request === requestVersion) loading.value = false }
  }
  const loadAssistant = async (id = assistant.value?.id, { quiet = false, scope = scopeVersion } = {}) => {
    const project = currentProjectId()
    if (!project || !id || !inScope(scope) || (!quiet && acting.value)) return null
    const request = ++requestVersion
    if (!quiet) loading.value = true
    try {
      const value = unwrapAssistantResponse(await requests.get(project, id))
      if (!inScope(scope) || request !== requestVersion || String(assistant.value?.id) !== String(id) || value?.mode !== currentContext().mode) return null
      pollFailures = 0
      lastError.value = ''
      refreshRequired.value = false
      return replaceAssistant(value)
    } catch (error) {
      if (inScope(scope) && request === requestVersion && String(assistant.value?.id) === String(id)) {
        if (quiet && active.value && ++pollFailures < MAX_POLL_FAILURES) schedulePoll()
        else if (quiet && active.value) {
          if (currentContext().mode === 'edit') {
            refreshRequired.value = true
            lastError.value = '会话自动刷新失败，请重新获取状态。'
          } else {
            refreshRequired.value = true
            lastError.value = '修复状态自动刷新失败，请点击“刷新修复状态”恢复。'
          }
        } else {
          const isRepair = currentContext().mode === 'repair'
          if (isRepair) refreshRequired.value = true
          const fallback = isRepair ? '加载修复状态失败，请刷新修复状态后重试。' : '加载 AI 会话失败'
          lastError.value = assistantErrorMessage(error, fallback)
        }
      }
      return null
    } finally { if (inScope(scope) && request === requestVersion && !quiet) loading.value = false }
  }
  const create = async payload => {
    const project = currentProjectId()
    const scope = scopeVersion
    if (!project || acting.value) return null
    const isEditRequest = currentContext().mode === 'edit'
    const request = isEditRequest ? ++requestVersion : requestVersion
    if (isEditRequest) {
      stopPolling()
      loading.value = false
    }
    acting.value = true
    lastError.value = ''
    try {
      const value = unwrapAssistantResponse(await requests.create(project, payload))
      return inScope(scope) && (!isEditRequest || request === requestVersion) ? replaceAssistant(value) : null
    } catch (error) {
      if (inScope(scope)) {
        lastError.value = assistantErrorMessage(error, isEditRequest ? '创建 AI 会话失败' : '启动 AI 修复失败')
        if (error?.response?.status === 409) refreshRequired.value = true
      }
      throw error
    } finally { if (inScope(scope)) acting.value = false }
  }
  const runAction = async (request, fallback, { reload = false } = {}) => {
    const project = currentProjectId()
    const sessionId = assistant.value?.id
    const scope = scopeVersion
    if (!project || !sessionId || acting.value) return null
    const isEditAction = currentContext().mode === 'edit'
    const actionVersion = isEditAction ? ++requestVersion : requestVersion
    if (isEditAction) {
      stopPolling()
      loading.value = false
    }
    acting.value = true
    lastError.value = ''
    try {
      const value = unwrapAssistantResponse(await request(project, sessionId))
      if (!inScope(scope) || (isEditAction && actionVersion !== requestVersion) || String(assistant.value?.id) !== String(sessionId)) return null
      return reload ? (await loadAssistant(sessionId, { quiet: true, scope }) || value) : replaceAssistant(value)
    } catch (error) {
      if (inScope(scope)) {
        lastError.value = assistantErrorMessage(error, fallback)
        if (isEditAction && error?.response?.status === 409) refreshRequired.value = true
        if (isEditAction && active.value) schedulePoll()
      }
      throw error
    } finally { if (inScope(scope)) acting.value = false }
  }
  const message = payload => runAction((project, id) => requests.message(project, id, payload), '发送消息失败')
  const clearEditConversation = () => {
    if (currentContext().mode !== 'edit' || loading.value || refreshRequired.value || acting.value || active.value) return false
    requestVersion += 1
    pollFailures = 0
    stopPolling()
    assistants.value = []
    assistant.value = null
    lastError.value = ''
    refreshRequired.value = false
    return true
  }
  const reset = async () => {
    const project = currentProjectId()
    const session = assistant.value
    const scope = scopeVersion
    if (!project || !session?.id || currentContext().mode !== 'edit' || loading.value || acting.value || active.value) return null
    const sessionId = session.id
    const revision = session.revision
    const request = ++requestVersion
    pollFailures = 0
    stopPolling()
    acting.value = true
    try {
      const value = unwrapAssistantResponse(await requests.reset(project, sessionId, { expected_revision: revision }))
      if (!inScope(scope) || request !== requestVersion || String(assistant.value?.id) !== String(sessionId) || value?.mode !== 'edit') return null
      lastError.value = ''
      refreshRequired.value = false
      return replaceAssistant(value)
    } catch (error) {
      if (inScope(scope) && request === requestVersion && String(assistant.value?.id) === String(sessionId)) {
        lastError.value = assistantErrorMessage(error, '新建 AI 会话失败')
        if (error?.response?.status === 409) refreshRequired.value = true
      }
      throw error
    } finally { if (inScope(scope) && request === requestVersion) acting.value = false }
  }
  const verify = payload => runAction((project, id) => requests.verify(project, id, payload), currentContext().mode === 'repair' ? '启动运行验证失败' : '启动调试验证失败')
  const apply = payload => runAction((project, id) => requests.apply(project, id, payload), '采用候选失败', { reload: true })
  const cancel = () => runAction((project, id) => requests.cancel(project, id, { expected_revision: assistant.value?.revision }), '取消任务失败')

  watch([() => currentProjectId(), () => JSON.stringify(assistantListParams(currentContext()))], () => {
    resetScope()
    const scope = scopeVersion
    loadRecent(scope)
    loadModels(scope)
  }, { immediate: true })
  onBeforeUnmount(resetScope)

  return { assistants, assistant, modelConfigs, loading, loadingModels, acting, active, lastError, refreshRequired, selectAssistant, loadRecent, loadAssistant, clearEditConversation, create, message, reset, verify, apply, cancel }
}
