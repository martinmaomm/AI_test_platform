<template>
  <div v-if="selectedProject" class="webui-generation-page">
    <header class="page-header">
      <div><h3>连续探索并编写测试脚本</h3><p>AI 理解测试目标后连续探索页面、编写 Python 草稿并进行静态检查；草稿就绪不代表实际调试通过</p></div>
      <el-tag :type="isConnected ? 'success' : 'info'" effect="plain">{{ isConnected ? '实时通知已连接' : '使用详情查询恢复状态' }}</el-tag>
    </header>
    <el-alert v-if="lastError" :title="lastError" type="warning" :closable="false" show-icon class="page-alert" />
    <div class="generation-layout">
      <GenerationInputPanel :project-id="selectedProject.id" :modules="modules" :model-configs="modelConfigs" :exploration-settings="explorationSettings" :loading-modules="loadingModules" :loading-models="loadingModels" :busy="isActive || submitting || isWorkspaceBusy" :paused="isPaused" :submitting="submitting" :cancelling="cancelling" @submit="handleCreate" @cancel="handleCancel" />
      <div class="result-column">
        <GenerationTimeline v-if="generation" :generation="generation" />
        <GenerationResultPanel v-if="generation" :generation="generation" :draft="localDraft" :saving="saving" :resolving="resolving" :draft-saving="draftSaving" :debugging="debugging" :busy="isActive || isWorkspaceBusy" :draft-conflict="draftConflict" :debug-execution="debugExecution" :debug-execution-loading="debugExecutionLoading" @resolve="handleResolve" @retry-generation="handleRetryGeneration" @cancel="handleCancel" @save="handleSave" @update-draft="updateLocalDraft" @save-draft="handleSaveDraft" @debug="handleDebug" @discard-local-draft="handleDiscardLocalDraft" @open-test-case="router.push('/web-testing/test-cases')" />
        <el-empty v-else :image-size="96" description="填写场景并确认目标范围后开始。未保存到测试用例的生成记录可在刷新后恢复；保存成功后会清空当前工作区。" class="empty-result" />
      </div>
    </div>
  </div>
  <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon><template #default><el-button type="primary" size="small" @click="router.push('/project/project-list')">前往项目管理</el-button></template></el-alert>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { getLLMConfigurations } from '@/api/aiConfig'
import { getWebUIScriptGenerationSettings, getWebUITestModules } from '@/api/webTesting'
import { WebSocketManager } from '@/config/websocket'
import { useWebUIScriptGeneration } from '@/composables/useWebUIScriptGeneration'
import { normalizeExplorationTimeoutSettings } from '@/composables/webuiExplorationTimeout'
import GenerationInputPanel from '@/components/webui-generation/GenerationInputPanel.vue'
import GenerationTimeline from '@/components/webui-generation/GenerationTimeline.vue'
import GenerationResultPanel from '@/components/webui-generation/GenerationResultPanel.vue'

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
let websocketManager = null

const { generation, localDraft, submitting, saving, cancelling, resolving, draftSaving, debugging, debugExecution, debugExecutionLoading, draftConflict, lastError, isActive, isPaused, isWorkspaceBusy, create, cancel, resolve: resolveGeneration, retryGeneration, save, saveDraft, debug, updateLocalDraft, discardLocalDraftAndRefresh, handleWebSocketEvent } = useWebUIScriptGeneration({ projectId, userId })

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
  try { modelConfigs.value = asList(await getLLMConfigurations()).filter(item => item.is_active && item.model_type === 'llm') } catch { modelConfigs.value = []; ElMessage.error('加载可用模型失败') } finally { loadingModels.value = false }
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
    if (localDraft.value?.dirty) {
      await ElMessageBox.confirm('当前草稿有未保存的编辑。创建新的生成任务后，这些本地编辑将被丢弃。', '创建新的生成任务', { type: 'warning', confirmButtonText: '丢弃并新建', cancelButtonText: '返回保存草稿' })
    }
    const result = await create(payload)
    if (!result) return
    ElMessage.success('已创建生成记录，正在按阶段处理。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '创建生成任务失败')
  }
}
const handleCancel = async () => {
  try { await ElMessageBox.confirm('确定取消当前脚本生成吗？已保存的阶段结果仍可查看。', '取消生成', { type: 'warning', confirmButtonText: '取消生成', cancelButtonText: '继续等待' }); const result = await cancel(); if (result) ElMessage.success('已请求取消生成任务') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '取消失败') }
}
const handleResolve = async (payload) => { try { const result = await resolveGeneration(payload); if (result) ElMessage.success('补充信息已提交，任务正在继续。') } catch { ElMessage.error(lastError.value || '提交补充信息失败') } }
const handleRetryGeneration = async () => { try { const result = await retryGeneration(); if (result) ElMessage.success('正在仅基于已保存的证据整理脚本，不会启动浏览器。') } catch { ElMessage.error(lastError.value || '基于轨迹整理脚本失败') } }
const handleSave = async (title) => { try { const result = await save(title); if (result) ElMessage.success(result?.created ? '已创建并保存到测试用例' : '已保存到测试用例') } catch { ElMessage.error(lastError.value || '保存失败') } }
const handleSaveDraft = async () => { try { const result = await saveDraft(); if (result) ElMessage.success('草稿已保存') } catch { ElMessage.error(lastError.value || '保存草稿失败') } }
const handleDebug = async (runtimeVariables) => { try { const result = await debug(runtimeVariables); if (result) ElMessage.success('已启动真实调试；不会自动重试业务写操作。') } catch { ElMessage.error(lastError.value || '启动调试失败') } }
const handleDiscardLocalDraft = async () => { try { await ElMessageBox.confirm('将丢弃当前未保存的本地脚本和变量编辑，并刷新服务端版本。', '确认刷新工作区', { type: 'warning', confirmButtonText: '丢弃并刷新', cancelButtonText: '保留本地编辑' }); const result = await discardLocalDraftAndRefresh(); if (result) ElMessage.success('已刷新服务端工作区版本') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '刷新工作区失败') } }

watch(projectId, () => { loadModules(); loadModels(); loadExplorationSettings() }, { immediate: true })
watch(() => authStore.accessToken, initWebSocket)
onMounted(initWebSocket)
onUnmounted(closeWebSocket)
</script>

<style scoped>
.webui-generation-page { height: 100%; min-height: 0; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column; gap: 16px; padding-right: 4px; scrollbar-gutter: stable; }.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.page-header h3 { margin: 0; color: var(--app-text-primary); font-size: 20px; }.page-header p { margin: 7px 0 0; color: var(--app-text-secondary); font-size: 13px; }.page-alert { margin: 0; }.generation-layout { display: grid; grid-template-columns: minmax(320px, .9fr) minmax(460px, 1.35fr); gap: 16px; align-items: start; }.result-column { display: grid; gap: 16px; min-width: 0; }.empty-result { min-height: 360px; padding: 36px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; } @media (max-width: 1050px) { .generation-layout { grid-template-columns: 1fr; }.page-header { flex-direction: column; } }
</style>
