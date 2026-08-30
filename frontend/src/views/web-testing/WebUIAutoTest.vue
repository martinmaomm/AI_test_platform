<template>
  <div v-if="selectedProject" class="webui-generation-page">
    <header class="page-header">
      <div><h3>AI 脚本生成</h3><p>AI 先理解测试场景，再通过 Playwright MCP 只读探索页面并生成 Python 脚本</p></div>
      <el-tag :type="isConnected ? 'success' : 'info'" effect="plain">{{ isConnected ? '实时通知已连接' : '使用详情查询恢复状态' }}</el-tag>
    </header>
    <el-alert v-if="lastError" :title="lastError" type="warning" :closable="false" show-icon class="page-alert" />
    <div class="generation-layout">
      <GenerationInputPanel :project-id="selectedProject.id" :environments="environments" :model-configs="modelConfigs" :loading-environments="loadingEnvironments" :loading-models="loadingModels" :busy="isActive || submitting" :submitting="submitting" :cancelling="cancelling" @submit="handleCreate" @cancel="handleCancel" />
      <div class="result-column">
        <GenerationTimeline v-if="generation" :generation="generation" />
        <GenerationResultPanel v-if="generation" :generation="generation" :saving="saving" @save="handleSave" @open-test-case="router.push('/web-testing/test-cases')" />
        <el-empty v-else :image-size="96" description="填写场景后开始生成。生成记录会在刷新页面后自动恢复。" class="empty-result" />
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
import { getProjectEnvironments } from '@/api/projects'
import { getLLMConfigurations } from '@/api/aiConfig'
import { WebSocketManager } from '@/config/websocket'
import { useWebUIScriptGeneration } from '@/composables/useWebUIScriptGeneration'
import GenerationInputPanel from '@/components/webui-generation/GenerationInputPanel.vue'
import GenerationTimeline from '@/components/webui-generation/GenerationTimeline.vue'
import GenerationResultPanel from '@/components/webui-generation/GenerationResultPanel.vue'

const router = useRouter()
const projectStore = useProjectStore()
const authStore = useAuthStore()
const selectedProject = computed(() => projectStore.currentProject)
const projectId = computed(() => selectedProject.value?.id || null)
const userId = computed(() => authStore.user?.id || authStore.user?.username || null)
const environments = ref([])
const modelConfigs = ref([])
const loadingEnvironments = ref(false)
const loadingModels = ref(false)
const isConnected = ref(false)
let websocketManager = null

const { generation, submitting, saving, cancelling, lastError, isActive, create, cancel, save, handleWebSocketEvent } = useWebUIScriptGeneration({ projectId, userId })

const asList = (response) => {
  const body = response?.data ?? response ?? {}
  if (Array.isArray(body)) return body
  if (Array.isArray(body.items)) return body.items
  if (Array.isArray(body.results)) return body.results
  if (Array.isArray(body.data)) return body.data
  if (Array.isArray(body.data?.items)) return body.data.items
  return []
}
const loadEnvironments = async () => {
  if (!projectId.value) { environments.value = []; return }
  loadingEnvironments.value = true
  try { environments.value = asList(await getProjectEnvironments(projectId.value, { category: 'web' })).filter(item => item.is_active) } catch { environments.value = []; ElMessage.error('加载 WebUI 环境失败') } finally { loadingEnvironments.value = false }
}
const loadModels = async () => {
  loadingModels.value = true
  try { modelConfigs.value = asList(await getLLMConfigurations()).filter(item => item.is_active && item.model_type === 'llm') } catch { modelConfigs.value = []; ElMessage.error('加载可用模型失败') } finally { loadingModels.value = false }
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
const handleCreate = async (payload) => { try { await create(payload); ElMessage.success('已创建生成记录，正在按阶段处理。') } catch { ElMessage.error(lastError.value || '创建生成任务失败') } }
const handleCancel = async () => {
  try { await ElMessageBox.confirm('确定取消当前脚本生成吗？已保存的阶段结果仍可查看。', '取消生成', { type: 'warning', confirmButtonText: '取消生成', cancelButtonText: '继续等待' }); await cancel(); ElMessage.success('已请求取消生成任务') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '取消失败') }
}
const handleSave = async (title) => { try { const result = await save(title); ElMessage.success(result?.created ? '已创建并保存到测试用例' : '已保存到测试用例') } catch { ElMessage.error(lastError.value || '保存失败') } }

watch(projectId, () => { loadEnvironments(); loadModels() }, { immediate: true })
watch(() => authStore.accessToken, initWebSocket)
onMounted(initWebSocket)
onUnmounted(closeWebSocket)
</script>

<style scoped>
.webui-generation-page { height: 100%; min-height: 0; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column; gap: 16px; padding-right: 4px; scrollbar-gutter: stable; }.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.page-header h3 { margin: 0; color: var(--app-text-primary); font-size: 20px; }.page-header p { margin: 7px 0 0; color: var(--app-text-secondary); font-size: 13px; }.page-alert { margin: 0; }.generation-layout { display: grid; grid-template-columns: minmax(320px, .9fr) minmax(460px, 1.35fr); gap: 16px; align-items: start; }.result-column { display: grid; gap: 16px; min-width: 0; }.empty-result { min-height: 360px; padding: 36px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; } @media (max-width: 1050px) { .generation-layout { grid-template-columns: 1fr; }.page-header { flex-direction: column; } }
</style>
