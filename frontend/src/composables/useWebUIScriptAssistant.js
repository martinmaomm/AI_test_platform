import { computed, onBeforeUnmount, ref, unref, watch } from 'vue'
import { getLLMConfigurations } from '@/api/aiConfig'
import { applyWebUIScriptAssistant, cancelWebUIScriptAssistant, createWebUIScriptAssistant, getWebUIScriptAssistant, getWebUIScriptAssistants, sendWebUIScriptAssistantMessage, verifyWebUIScriptAssistant } from '@/api/webTesting'
import { assistantErrorMessage, assistantList, assistantListParams, assistantsForContext, isAssistantActive, unwrapAssistantResponse } from './webUIScriptAssistantPresentation'

const POLL_INTERVAL_MS = 1500
const MAX_POLL_FAILURES = 3
const configuredModels = response => assistantList(response).filter(item => item?.is_active && item?.model_type === 'llm')

export const useWebUIScriptAssistant = ({ projectId, context }) => {
  const assistants = ref([])
  const assistant = ref(null)
  const modelConfigs = ref([])
  const loading = ref(false)
  const loadingModels = ref(false)
  const acting = ref(false)
  const lastError = ref('')
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
    acting.value = false
    lastError.value = ''
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
      const models = configuredModels(await getLLMConfigurations())
      if (inScope(scope)) modelConfigs.value = models
    } catch {
      if (inScope(scope)) modelConfigs.value = []
    } finally { if (inScope(scope)) loadingModels.value = false }
  }
  const loadRecent = async (scope = scopeVersion) => {
    const project = currentProjectId()
    if (!project || !inScope(scope)) return
    const request = ++requestVersion
    loading.value = true
    try {
      const items = assistantsForContext(assistantList(await getWebUIScriptAssistants(project, assistantListParams(currentContext()))), currentContext())
      if (!inScope(scope) || request !== requestVersion) return
      assistants.value = items
      const selectedId = assistant.value?.id
      const selected = items.find(item => String(item.id) === String(selectedId)) || items[0] || null
      stopPolling()
      assistant.value = selected
      if (!selected?.id) return
      const detail = unwrapAssistantResponse(await getWebUIScriptAssistant(project, selected.id))
      if (!inScope(scope) || request !== requestVersion || detail?.mode !== currentContext().mode) return
      replaceAssistant(detail)
    } catch (error) {
      if (inScope(scope) && request === requestVersion && assistant.value?.id) schedulePoll()
      if (inScope(scope) && request === requestVersion) lastError.value = assistantErrorMessage(error, '加载最近 AI 会话失败')
    } finally { if (inScope(scope) && request === requestVersion) loading.value = false }
  }
  const loadAssistant = async (id = assistant.value?.id, { quiet = false, scope = scopeVersion } = {}) => {
    const project = currentProjectId()
    if (!project || !id || !inScope(scope)) return null
    const request = ++requestVersion
    if (!quiet) loading.value = true
    try {
      const value = unwrapAssistantResponse(await getWebUIScriptAssistant(project, id))
      if (!inScope(scope) || request !== requestVersion || String(assistant.value?.id) !== String(id) || value?.mode !== currentContext().mode) return null
      pollFailures = 0
      return replaceAssistant(value)
    } catch (error) {
      if (inScope(scope) && request === requestVersion && String(assistant.value?.id) === String(id)) {
        if (quiet && active.value && ++pollFailures < MAX_POLL_FAILURES) schedulePoll()
        else if (quiet && active.value) lastError.value = '会话自动刷新已暂停，请点击“最近会话”恢复。'
        else lastError.value = assistantErrorMessage(error, '加载 AI 会话失败')
      }
      return null
    } finally { if (inScope(scope) && request === requestVersion && !quiet) loading.value = false }
  }
  const create = async payload => {
    const project = currentProjectId()
    const scope = scopeVersion
    if (!project || acting.value) return null
    acting.value = true
    lastError.value = ''
    try {
      const value = unwrapAssistantResponse(await createWebUIScriptAssistant(project, payload))
      return inScope(scope) ? replaceAssistant(value) : null
    } catch (error) {
      if (inScope(scope)) lastError.value = assistantErrorMessage(error, '创建 AI 会话失败')
      throw error
    } finally { if (inScope(scope)) acting.value = false }
  }
  const runAction = async (request, fallback, { reload = false } = {}) => {
    const project = currentProjectId()
    const sessionId = assistant.value?.id
    const scope = scopeVersion
    if (!project || !sessionId || acting.value) return null
    acting.value = true
    lastError.value = ''
    try {
      const value = unwrapAssistantResponse(await request(project, sessionId))
      if (!inScope(scope) || String(assistant.value?.id) !== String(sessionId)) return null
      return reload ? (await loadAssistant(sessionId, { quiet: true, scope }) || value) : replaceAssistant(value)
    } catch (error) {
      if (inScope(scope)) lastError.value = assistantErrorMessage(error, fallback)
      throw error
    } finally { if (inScope(scope)) acting.value = false }
  }
  const message = payload => runAction((project, id) => sendWebUIScriptAssistantMessage(project, id, payload), '发送消息失败')
  const verify = payload => runAction((project, id) => verifyWebUIScriptAssistant(project, id, payload), '启动调试验证失败')
  const apply = payload => runAction((project, id) => applyWebUIScriptAssistant(project, id, payload), '采用候选失败', { reload: true })
  const cancel = () => runAction((project, id) => cancelWebUIScriptAssistant(project, id, { expected_revision: assistant.value?.revision }), '取消任务失败')

  watch([() => currentProjectId(), () => JSON.stringify(assistantListParams(currentContext()))], () => {
    resetScope()
    const scope = scopeVersion
    loadRecent(scope)
    loadModels(scope)
  }, { immediate: true })
  onBeforeUnmount(resetScope)

  return { assistants, assistant, modelConfigs, loading, loadingModels, acting, active, lastError, selectAssistant, loadRecent, loadAssistant, create, message, verify, apply, cancel }
}
