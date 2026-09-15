<template>
  <div v-if="selectedProject" class="webui-generation-page">
    <header class="page-header">
      <div><h3>连续探索并编写测试脚本</h3><p>AI 理解测试目标后连续探索页面、编写 Python 草稿并进行静态检查；草稿就绪不代表实际调试通过</p></div>
      <div class="header-actions"><el-button plain :loading="historyLoading" @click="handleOpenHistory">我的生成记录</el-button><el-tag :type="isConnected ? 'success' : 'info'" effect="plain">{{ isConnected ? '实时通知已连接' : '使用详情查询恢复状态' }}</el-tag></div>
    </header>
    <el-alert v-if="lastError" :title="lastError" type="warning" :closable="false" show-icon class="page-alert" />
    <div class="generation-layout">
      <GenerationInputPanel :project-id="selectedProject.id" :modules="modules" :model-configs="modelConfigs" :exploration-settings="explorationSettings" :loading-modules="loadingModules" :loading-models="loadingModels" :busy="submitting || isWorkspaceBusy || hasRepairCandidate || saving || resolving || debugging || repairing || repairApplying || repairDiscarding || draftSaving || isDeletingGeneration || historySwitching" :generation-active="isActive" :paused="isPaused" :submitting="submitting" :cancelling="cancelling" @submit="handleCreate" @cancel="handleCancel" />
      <div class="result-column">
        <GenerationTimeline v-if="generation" :generation="generation" />
        <GenerationResultPanel v-if="generation" :generation="generation" :draft="localDraft" :saving="saving" :resolving="resolving" :draft-saving="draftSaving" :debugging="debugging" :repairing="repairing" :repair-applying="repairApplying" :repair-discarding="repairDiscarding" :busy="isActive || isWorkspaceBusy || hasRepairCandidate || submitting || saving || resolving || draftSaving || debugging || repairing || repairApplying || repairDiscarding || isDeletingGeneration || historySwitching" :repair-busy="isActive || isWorkspaceBusy || submitting || saving || resolving || draftSaving || debugging || repairing || repairApplying || repairDiscarding || isDeletingGeneration || historySwitching" :draft-conflict="draftConflict" :can-resume-exploration="canResumeExploration" :debug-execution="debugExecution" :debug-execution-loading="debugExecutionLoading" :repair-execution="repairExecution" :repair-execution-loading="repairExecutionLoading" :selected-repair-execution-id="selectedRepairExecutionId" @resolve="handleResolve" @resume-exploration="handleResumeExploration" @retry-generation="handleRetryGeneration" @cancel="handleCancel" @save="handleSave" @update-draft="updateLocalDraft" @save-draft="handleSaveDraft" @debug="handleDebug" @repair="handleRepair" @apply-repair="handleApplyRepair" @discard-repair="handleDiscardRepair" @view-repair-execution="handleViewRepairExecution" @discard-local-draft="handleDiscardLocalDraft" @open-test-case="router.push('/web-testing/test-cases')" />
        <el-empty v-else :image-size="96" description="填写场景并确认目标范围后开始。未保存到测试用例的生成记录可在刷新后恢复；保存成功后会清空当前工作区。" class="empty-result" />
      </div>
    </div>
    <GenerationHistoryPanel :visible="historyVisible" :items="historyItems" :page="historyPage" :page-size="historyPageSize" :total="historyTotal" :error="historyError" :loading="historyLoading" :switching="historySwitching" :switch-disabled="isHistorySwitchBlocked" :delete-disabled="isHistorySwitchBlocked || historyDeleteConfirming" :deleting-generation-id="deletingGenerationId" :current-generation-id="generation?.id" @close="historyVisible = false" @load="loadHistory" @select="handleHistorySelect" @delete="handleHistoryDelete" />
  </div>
  <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon><template #default><el-button type="primary" size="small" @click="router.push('/project/project-list')">前往项目管理</el-button></template></el-alert>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { getAvailableLLMConfigurations } from '@/api/aiConfig'
import { getWebUIScriptGenerationSettings, getWebUITestModules } from '@/api/webTesting'
import { WebSocketManager } from '@/config/websocket'
import { useWebUIScriptGeneration } from '@/composables/useWebUIScriptGeneration'
import { normalizeExplorationTimeoutSettings } from '@/composables/webuiExplorationTimeout'
import GenerationInputPanel from '@/components/webui-generation/GenerationInputPanel.vue'
import GenerationTimeline from '@/components/webui-generation/GenerationTimeline.vue'
import GenerationResultPanel from '@/components/webui-generation/GenerationResultPanel.vue'
import GenerationHistoryPanel from '@/components/webui-generation/GenerationHistoryPanel.vue'

const router = useRouter()
const projectStore = useProjectStore()
const authStore = useAuthStore()
const selectedProject = computed(() => projectStore.currentProject)
const projectId = computed(() => selectedProject.value?.id || null)
const userId = computed(() => authStore.user?.id || authStore.user?.username || null)
const modules = ref([])
const modelConfigs = ref([])
const explorationSettings = ref(null)
const loadingModules = ref(false)
const loadingModels = ref(false)
const isConnected = ref(false)
const historyVisible = ref(false)
const historyDeleteConfirming = ref(false)
let websocketManager = null

const { generation, localDraft, submitting, saving, cancelling, resolving, draftSaving, debugging, repairing, repairApplying, repairDiscarding, debugExecution, debugExecutionLoading, repairExecution, repairExecutionLoading, selectedRepairExecutionId, draftConflict, lastError, isActive, isPaused, isWorkspaceBusy, hasUnsavedDraft, hasUnpersistedGeneration, hasRepairCandidate, canResumeExploration, historyItems, historyPage, historyPageSize, historyTotal, historyLoading, historyError, historySwitching, isHistorySwitchBlocked, deletingGenerationId, isDeletingGeneration, create, cancel, resolve: resolveGeneration, resumeExploration, retryGeneration, save, saveDraft, debug, repair, applyRepairCandidate, discardRepairCandidate, loadRepairExecution, loadHistory, openHistoryGeneration, deleteGeneration, updateLocalDraft, discardLocalDraftAndRefresh, handleWebSocketEvent } = useWebUIScriptGeneration({ projectId, userId })
const needsReplacementConfirmation = computed(() => hasUnsavedDraft.value || hasUnpersistedGeneration.value)

const asList = (response) => {
  const body = response?.data ?? response ?? {}
  if (Array.isArray(body)) return body
  if (Array.isArray(body.items)) return body.items
  if (Array.isArray(body.results)) return body.results
  if (Array.isArray(body.data)) return body.data
  if (Array.isArray(body.data?.items)) return body.data.items
  return []
}
const loadModels = async () => {
  loadingModels.value = true
  try { modelConfigs.value = asList(await getAvailableLLMConfigurations()).filter(item => item.is_active && item.model_type === 'llm') } catch { modelConfigs.value = []; ElMessage.error('加载可用模型失败') } finally { loadingModels.value = false }
}
const loadModules = async () => {
  if (!projectId.value) { modules.value = []; return }
  loadingModules.value = true
  try { modules.value = asList(await getWebUITestModules(projectId.value)) } catch { modules.value = []; ElMessage.error('加载业务模块失败') } finally { loadingModules.value = false }
}
const loadExplorationSettings = async () => {
  explorationSettings.value = null
  if (!projectId.value) return
  try { explorationSettings.value = normalizeExplorationTimeoutSettings(await getWebUIScriptGenerationSettings(projectId.value)) } catch { /* A blank field lets the server apply its env default. */ }
}
const closeWebSocket = () => { websocketManager?.closeWebSocket(); websocketManager = null; isConnected.value = false }
const initWebSocket = () => {
  closeWebSocket()
  if (!authStore.accessToken) return
  websocketManager = new WebSocketManager()
  websocketManager.initWebSocket('/ws/webui_auto_test-streaming/', authStore.accessToken, {
    autoReconnect: true,
    onOpen: () => { isConnected.value = true }, onClose: () => { isConnected.value = false }, onError: () => { isConnected.value = false },
    onMessage: (event) => { try { handleWebSocketEvent(JSON.parse(event.data)) } catch { /* Ignore malformed notification. */ } }
  })
}
const handleCreate = async (payload) => {
  try {
    if (needsReplacementConfirmation.value) {
      const dirty = hasUnsavedDraft.value
      await ElMessageBox.confirm(dirty ? '当前草稿有未保存的本地编辑。创建新的生成任务后，这些编辑将无法恢复；“我的生成记录”只能找回上次服务端保存的版本。' : '当前生成记录尚未保存为测试用例。创建新的生成任务后，仍可从“我的生成记录”找回该记录的服务端保存版本。', '创建新的生成任务', { type: 'warning', confirmButtonText: dirty ? '丢弃并新建' : '新建并保留记录', cancelButtonText: dirty ? '返回保存草稿' : '取消' })
    }
    const result = await create(payload)
    if (!result) return
    ElMessage.success('已创建生成记录，正在按阶段处理。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '创建生成任务失败')
  }
}
const handleOpenHistory = async () => { historyVisible.value = true; await loadHistory(1) }
const handleHistorySelect = async (generationId) => {
  if (String(generation.value?.id || '') === String(generationId || '') || isHistorySwitchBlocked.value) return
  try {
    if (needsReplacementConfirmation.value) {
      const dirty = hasUnsavedDraft.value
      await ElMessageBox.confirm(dirty ? '当前草稿有未保存的本地编辑。恢复历史记录会明确丢弃这些编辑，且无法恢复；“我的生成记录”只能找回上次服务端保存的版本。' : '当前生成记录尚未保存为测试用例。恢复历史记录后，当前内容仍可从“我的生成记录”找回其服务端保存版本。', '恢复历史记录', { type: 'warning', confirmButtonText: dirty ? '丢弃并恢复' : '恢复记录', cancelButtonText: dirty ? '保留当前草稿' : '取消' })
    }
    const result = await openHistoryGeneration(generationId)
    if (result) { historyVisible.value = false; ElMessage.success('已恢复历史生成记录') }
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(historyError.value || '读取生成记录失败')
  }
}
const generationRevision = () => Number(generation.value?.workspace?.revision ?? generation.value?.revision ?? 0)
const isCurrentHistoryDeleteContext = context => (
  String(projectId.value ?? '') === String(context.projectId ?? '')
  && String(userId.value ?? '') === String(context.userId ?? '')
  && String(generation.value?.id ?? '') === String(context.currentGenerationId ?? '')
  && generationRevision() === context.currentGenerationRevision
)
const handleHistoryDelete = async (item) => {
  if (!item?.id || item.can_delete !== true || historyDeleteConfirming.value || isHistorySwitchBlocked.value) return
  const target = { id: item.id, updated_at: item.updated_at, can_delete: true }
  const context = {
    projectId: projectId.value,
    userId: userId.value,
    currentGenerationId: generation.value?.id ?? null,
    currentGenerationRevision: generationRevision()
  }
  const deletingCurrent = String(generation.value?.id || '') === String(target.id)
  const unsavedCurrentDraft = deletingCurrent && hasUnsavedDraft.value
  historyDeleteConfirming.value = true
  try {
    await ElMessageBox.confirm(
      `删除后将永久删除此生成草稿及其探索和生成轨迹，且不可撤销。已保存的测试用例、执行记录、报告和文件不会被删除。${unsavedCurrentDraft ? '当前记录还有未保存的本地编辑，删除后这些编辑也会丢失。' : ''}`,
      '确认删除生成记录',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
    if (!isCurrentHistoryDeleteContext(context) || isHistorySwitchBlocked.value) return
    const result = await deleteGeneration(target)
    if (result) ElMessage.success('生成记录已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '删除生成记录失败')
  } finally {
    historyDeleteConfirming.value = false
  }
}
const handleCancel = async () => {
  try { await ElMessageBox.confirm('确定取消当前脚本生成吗？已保存的阶段结果仍可查看。', '取消生成', { type: 'warning', confirmButtonText: '取消生成', cancelButtonText: '继续等待' }); const result = await cancel(); if (result) ElMessage.success('已请求取消生成任务') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '取消失败') }
}
const handleResolve = async (payload) => { try { const result = await resolveGeneration(payload); if (result) ElMessage.success('补充信息已提交，任务正在继续。') } catch { ElMessage.error(lastError.value || '提交补充信息失败') } }
const handleResumeExploration = async (payload) => { const recoveryNotes = typeof payload === 'object' ? payload.recoveryNotes : payload; try { const result = await resumeExploration(recoveryNotes, typeof payload === 'object' ? { generationId: payload.generationId, revision: payload.revision } : {}); if (result) ElMessage.success('已确认现场，正在启动新的浏览器会话继续探索。') } catch { ElMessage.error(lastError.value || '确认现场后继续探索失败') } }
const handleRetryGeneration = async () => { try { const result = await retryGeneration(); if (result) ElMessage.success('正在仅基于已保存的证据整理脚本，不会启动浏览器。') } catch { ElMessage.error(lastError.value || '基于轨迹整理脚本失败') } }
const handleSave = async (title) => { try { const result = await save(title); if (result) ElMessage.success(result?.created ? '已创建并保存到测试用例' : '已保存到测试用例') } catch { ElMessage.error(lastError.value || '保存失败') } }
const handleSaveDraft = async () => { try { const result = await saveDraft(); if (result) ElMessage.success('草稿已保存') } catch { ElMessage.error(lastError.value || '保存草稿失败') } }
const handleDebug = async (runtimeVariables) => { try { const result = await debug(runtimeVariables); if (result) ElMessage.success('已启动真实调试；不会自动重试业务写操作。') } catch { ElMessage.error(lastError.value || '启动调试失败') } }
const handleRepair = async (runtimeVariables) => { try { const result = await repair(runtimeVariables); if (result) ElMessage.success('AI 已开始分析失败证据并验证候选脚本。') } catch { ElMessage.error(lastError.value || '启动 AI 分析并修复失败') } }
const handleApplyRepair = async (candidateHash) => { try { const result = await applyRepairCandidate(candidateHash); if (result) ElMessage.success('已采用候选脚本并同步到新的工作区版本。') } catch { ElMessage.error(lastError.value || '采用修复候选失败') } }
const handleDiscardRepair = async (candidateHash) => { try { const result = await discardRepairCandidate(candidateHash); if (result) ElMessage.success('已放弃候选，原草稿和版本保持不变，可继续编辑和调试。') } catch { ElMessage.error(lastError.value || '放弃修复候选失败') } }
const handleViewRepairExecution = async (executionId) => { await loadRepairExecution(executionId) }
const handleDiscardLocalDraft = async () => { try { await ElMessageBox.confirm('将丢弃当前未保存的本地脚本和变量编辑，并刷新服务端版本。', '确认刷新工作区', { type: 'warning', confirmButtonText: '丢弃并刷新', cancelButtonText: '保留本地编辑' }); const result = await discardLocalDraftAndRefresh(); if (result) ElMessage.success('已刷新服务端工作区版本') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '刷新工作区失败') } }

watch(projectId, () => { loadModules(); loadModels(); loadExplorationSettings() }, { immediate: true })
watch(() => authStore.accessToken, initWebSocket)
onMounted(initWebSocket)
onUnmounted(closeWebSocket)
</script>

<style scoped>
.webui-generation-page { height: 100%; min-height: 0; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column; gap: 16px; padding-right: 4px; scrollbar-gutter: stable; }.page-header, .header-actions { display: flex; align-items: flex-start; gap: 12px; }.page-header { justify-content: space-between; padding: 18px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.header-actions { flex-wrap: wrap; justify-content: flex-end; }.page-header h3 { margin: 0; color: var(--app-text-primary); font-size: 20px; }.page-header p { margin: 7px 0 0; color: var(--app-text-secondary); font-size: 13px; }.page-alert { margin: 0; }.generation-layout { display: grid; grid-template-columns: minmax(320px, .9fr) minmax(460px, 1.35fr); gap: 16px; align-items: start; }.result-column { display: grid; gap: 16px; min-width: 0; }.empty-result { min-height: 360px; padding: 36px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; } @media (max-width: 1050px) { .generation-layout { grid-template-columns: 1fr; }.page-header { flex-direction: column; }.header-actions { justify-content: flex-start; } }
</style>
