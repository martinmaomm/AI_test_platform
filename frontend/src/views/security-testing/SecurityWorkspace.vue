<template>
  <section class="security-workspace">
    <div class="heading"><div><h2>{{ taskPage ? '安全任务记录' : '安全测试计划' }}</h2><p class="hint">{{ project?.name }} · 导入接口 → 配置范围与账号 → 生成检查项 → 勾选执行 → 查看证据与报告</p></div><div><el-button :loading="loading" @click="refresh()">刷新</el-button><el-button v-if="!taskPage && permissions.edit" type="primary" @click="edit(null)">新建计划</el-button></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="space" />
    <el-skeleton v-if="loading && !project" :rows="5" animated />
    <template v-else-if="project">
      <el-alert v-if="taskPage && !canReadTasks" title="当前账号没有查看任务记录的权限，请联系项目管理员。" type="warning" :closable="false" />
      <el-table v-else-if="taskPage" :data="tasks" empty-text="暂无任务记录">
        <el-table-column label="计划" min-width="180"><template #default="{ row }">{{ taskName(row) }}</template></el-table-column>
        <el-table-column label="任务" width="140"><template #default="{ row }">{{ row.kind === 'planning' ? '生成检查项' : '执行安全检查' }}</template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.cancel_requested && isActiveTask(row) ? '正在停止' : statusLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="进度" width="160"><template #default="{ row }">{{ progressText(row) }}</template></el-table-column>
        <el-table-column label="创建时间" width="180"><template #default="{ row }">{{ date(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click="openTask(row)">查看详情</el-button></template></el-table-column>
      </el-table>
      <template v-else>
        <el-table :data="plans" highlight-current-row row-key="id" empty-text="暂无计划，先配置接口与测试范围" @current-change="selectPlan">
          <el-table-column label="计划名称" min-width="190"><template #default="{ row }"><el-button link type="primary" @click.stop="selectPlan(row)">{{ row.name }}</el-button></template></el-table-column>
          <el-table-column label="接口 / 检查项" width="125"><template #default="{ row }">{{ row.config?.endpoints?.length || 0 }} / {{ row.cases?.length || 0 }}</template></el-table-column>
          <el-table-column label="创建时间" min-width="165"><template #default="{ row }">{{ date(row.created_at) }}</template></el-table-column>
          <el-table-column label="修改时间" min-width="165"><template #default="{ row }">{{ date(row.updated_at) }}</template></el-table-column>
          <el-table-column label="操作" width="155"><template #default="{ row }"><el-button v-if="permissions.edit" link type="primary" @click.stop="edit(row)">编辑</el-button><el-button v-if="permissions.edit" link type="danger" @click.stop="remove(row)">删除</el-button><el-button link @click.stop="selectPlan(row)">检查项</el-button></template></el-table-column>
        </el-table>
        <el-card v-if="selectedPlan" class="space" shadow="never">
          <template #header><div class="heading compact"><strong>{{ selectedPlan.name }} · 版本 {{ selectedPlan.revision }}</strong><el-tag>{{ selectedPlan.cases?.length || 0 }} 项检查</el-tag></div></template>
          <p class="hint">请求预算 {{ selectedPlan.config?.max_requests }} 次 · 速率 {{ selectedPlan.config?.rate_per_second }} 次/秒 · 单次等待 {{ selectedPlan.config?.timeout_seconds }} 秒。保存修改后需要重新生成检查项。</p>
          <div class="generation-row"><el-select v-model="modelId" clearable placeholder="不选择模型：使用规则生成" :disabled="submitting || !permissions.edit || !permissions.execute" style="width: 340px"><el-option v-for="model in models" :key="model.id" :value="model.id" :label="`${model.name}${model.model_name ? ' · ' + model.model_name : ''}`" /></el-select><el-button v-if="permissions.edit && permissions.execute" :loading="submitting === 'planning'" :disabled="!!submitting || !!pendingPlanning" @click="generate">{{ modelId ? 'AI 生成检查项' : '规则生成检查项' }}</el-button></div>
          <p class="hint">{{ modelId ? 'AI 根据接口的认证意图和检查方向规划，不会向模型发送账号凭据。' : '规则生成不调用 AI；选择全局可用模型后可使用 AI 规划。' }}生成检查项不会请求被测接口。</p>
          <el-alert v-if="modelError" :title="modelError" type="warning" :closable="false" />
          <el-alert v-if="pendingPlanning" :title="`检查项${statusLabel(pendingPlanning.status)}，页面会自动更新。`" type="info" :closable="false" class="space"><el-button v-if="canReadTasks" link type="primary" @click="openTask(pendingPlanning)">查看规划任务</el-button></el-alert>
          <el-alert v-else-if="latestPlanning?.status === 'failed' || latestPlanning?.status === 'cancelled'" :title="latestPlanning.error_message || `最近一次规划${statusLabel(latestPlanning.status)}，可重新生成检查项。`" type="warning" :closable="false" class="space"><el-button link type="primary" @click="openTask(latestPlanning)">查看规划任务</el-button></el-alert>
          <el-alert v-else-if="latestPlanning?.status === 'completed' && !selectedPlan.cases?.length" title="最近一次规划已完成，当前计划尚无检查项。计划可能已修改，请查看规划详情或重新生成。" type="info" :closable="false" class="space"><el-button link type="primary" @click="openTask(latestPlanning)">查看规划任务</el-button></el-alert>
          <el-table :key="`${selectedPlan.id}:${selectedPlan.revision}:${caseSignature}`" :data="selectedPlan.cases || []" row-key="id" @selection-change="selectedCases = $event.map(item => item.id)" empty-text="请先生成检查项，再选择执行">
            <el-table-column type="selection" width="50" :selectable="() => permissions.execute && permissions.report" /><el-table-column label="检查方向" width="125"><template #default="{ row }">{{ checkLabel(row.check) }}</template></el-table-column>
            <el-table-column label="接口" min-width="160"><template #default="{ row }">{{ endpointLabel(row) }}</template></el-table-column>
            <el-table-column prop="title" label="检查项" min-width="160" /><el-table-column prop="rationale" label="检查说明" min-width="250" />
          </el-table>
          <div class="heading space"><span class="hint">已选 {{ selectedCases.length }} 项。执行将访问配置中的被测接口，可随时停止。</span><el-button v-if="permissions.execute && permissions.report" type="primary" :loading="submitting === 'execution'" :disabled="!!submitting || !selectedCases.length || !!pendingPlanning" @click="execute">执行选中检查</el-button></div>
          <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="space" />
        </el-card>
        <el-empty v-else-if="plans.length" description="选择一个计划，查看或生成检查项" />
      </template>
    </template>
    <SecurityPlanEditor v-model="editorOpen" :plan="editingPlan" :project-id="projectId" :saving="saving" :server-error="saveError" @save="save" />
  </section>
</template>
<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { getProject } from '@/api/projects'
import { getAvailableLLMConfigurations } from '@/api/aiConfig'
import { getSecurityPlans, createSecurityPlan, updateSecurityPlan, deleteSecurityPlan, generateSecurityCases, runSecurityCases, getSecurityTasks } from '@/api/security'
import SecurityPlanEditor from './SecurityPlanEditor.vue'
import { unwrap, securityPermissions, createSecurityScope, securityError, newSubmission, isActiveTask, statusLabel, statusType, checkLabel, loadSecurityWorkspaceData, shouldRenewSubmission } from './securityState'
const router = useRouter(), route = useRoute(), projectStore = useProjectStore(), authStore = useAuthStore()
const projectId = computed(() => projectStore.currentProjectId), taskPage = computed(() => route.name === 'SecurityTasks')
const project = ref(null), plans = ref([]), tasks = ref([]), loading = ref(false), error = ref(''), selectedId = ref(null), selectedCases = ref([])
const models = ref([]), modelId = ref(null), modelError = ref(''), actionError = ref(''), submitting = ref(''), editorOpen = ref(false), editingPlan = ref(null), saving = ref(false), saveError = ref('')
const permissions = computed(() => securityPermissions(authStore.user, project.value))
const canReadTasks = computed(() => permissions.value.report || (permissions.value.edit && permissions.value.execute))
const selectedPlan = computed(() => plans.value.find((item) => item.id === selectedId.value))
const caseSignature = computed(() => JSON.stringify(selectedPlan.value?.cases || []))
const latestPlanning = computed(() => tasks.value.find((item) => item.kind === 'planning' && String(item.plan_id ?? item.plan) === String(selectedId.value)))
const pendingPlanning = computed(() => tasks.value.find((item) => item.kind === 'planning' && String(item.plan_id ?? item.plan) === String(selectedId.value) && isActiveTask(item)))
const scope = createSecurityScope(); let timer; let readEpoch = 0; let retrySubmission = null
const date = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—'
const taskName = (task) => task.plan_name || task.snapshot?.plan_name || plans.value.find((item) => String(item.id) === String(task.plan_id ?? task.plan))?.name || '安全测试任务'
const progressText = (task) => task.progress?.total ? `${task.progress.completed || 0} / ${task.progress.total}` : task.kind === 'planning' ? '规划检查项' : '等待执行'
const endpointLabel = (item) => { const endpoint = selectedPlan.value?.config?.endpoints?.[item.endpoint_index]; return endpoint ? `${endpoint.method} ${endpoint.name || endpoint.url}` : `接口 ${item.endpoint_index + 1}` }
function current(captured) { return scope.current(captured) && String(projectId.value) === String(captured.projectId) && route.fullPath === captured.route }
function selectPlan(value) { if (selectedId.value !== value?.id) { selectedId.value = value?.id || null; selectedCases.value = []; actionError.value = ''; retrySubmission = null } }
function edit(value) { if (!permissions.value.edit) return; editingPlan.value = value; saveError.value = ''; editorOpen.value = true }
function openTask(task) { router.push({ name: 'SecurityTaskDetail', params: { taskId: task.id }, query: { project_id: projectId.value } }) }
function schedule() { clearTimeout(timer); if (tasks.value.some(isActiveTask)) timer = setTimeout(() => refresh(true), 2500) }
async function refresh(poll = false) {
  const captured = scope.capture(); if (!captured.projectId) return
  const sequence = ++readEpoch; if (!poll) loading.value = true
  try {
    const loadedProject = unwrap(await getProject(captured.projectId)); if (!current(captured) || sequence !== readEpoch) return
    if (loadedProject.project_type !== 'security') return router.replace('/security-testing/projects')
    project.value = loadedProject
    const results = await loadSecurityWorkspaceData(
      () => canReadTasks.value ? getSecurityTasks(captured.projectId) : Promise.resolve({ data: { items: [] } }),
      () => getSecurityPlans(captured.projectId),
      () => current(captured) && sequence === readEpoch,
    )
    if (!results) return
    plans.value = results.plans; tasks.value = results.tasks; error.value = ''
    if (!plans.value.some((item) => item.id === selectedId.value)) selectPlan(plans.value[0] || null)
    schedule()
  } catch (cause) { if (current(captured) && sequence === readEpoch) { error.value = securityError(cause, '加载安全测试工作区失败，点击刷新重试'); clearTimeout(timer) } }
  finally { if (current(captured) && sequence === readEpoch) loading.value = false }
}
async function loadModels(captured) {
  try {
    const response = await getAvailableLLMConfigurations(); if (!current(captured)) return
    const data = unwrap(response.data); models.value = Array.isArray(data) ? data : data?.items || data?.results || []; modelError.value = ''
  } catch (cause) { if (current(captured)) modelError.value = '模型列表暂不可用，仍可使用规则生成。' }
}
async function save(payload) {
  if (saving.value || !permissions.value.edit) return
  const captured = scope.capture(); const planId = editingPlan.value?.id; saving.value = true; saveError.value = ''
  try {
    const result = unwrap(await (planId ? updateSecurityPlan(captured.projectId, planId, payload) : createSecurityPlan(captured.projectId, payload)))
    if (!current(captured)) return
    editorOpen.value = false; selectedId.value = result.id; selectedCases.value = []; ElMessage.success('计划已保存，请生成检查项'); await refresh()
  } catch (cause) { if (current(captured)) saveError.value = securityError(cause, '保存计划失败') }
  finally { if (current(captured)) saving.value = false }
}
async function remove(plan) {
  const captured = scope.capture(); if (!permissions.value.edit) return
  try {
    await ElMessageBox.confirm(`删除计划“${plan.name}”？已有任务引用的计划无法删除。`, '删除计划', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
    if (!current(captured)) return
    await deleteSecurityPlan(captured.projectId, plan.id); if (current(captured)) { ElMessage.success('计划已删除'); await refresh() }
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close' && current(captured)) error.value = securityError(cause, '删除失败') }
}
async function submit(kind) {
  if (submitting.value || !selectedPlan.value || !permissions.value.execute || (kind === 'planning' ? !permissions.value.edit : !permissions.value.report)) return
  const captured = scope.capture(); const plan = selectedPlan.value
  if (kind === 'execution' && !selectedCases.value.length) return
  submitting.value = kind; actionError.value = ''
  try {
    const payload = kind === 'planning' ? (modelId.value ? { model_config_id: modelId.value } : {}) : { case_ids: [...selectedCases.value] }
    retrySubmission = newSubmission(retrySubmission, `${plan.id}:${plan.revision}:${kind}`, payload)
    const task = unwrap(await (kind === 'planning' ? generateSecurityCases : runSecurityCases)(captured.projectId, plan.id, { ...payload, request_id: retrySubmission.requestId }))
    if (!current(captured)) return
    retrySubmission = null
    if (kind === 'execution') return openTask(task)
    tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)]; selectedCases.value = []
    ElMessage.success(modelId.value ? 'AI 规划任务已提交' : '规则规划任务已提交'); schedule()
  } catch (cause) { if (current(captured)) { actionError.value = securityError(cause, '提交结果未确认，请重试；相同请求不会重复执行'); if (shouldRenewSubmission(cause)) retrySubmission = null } }
  finally { if (current(captured)) submitting.value = '' }
}
const generate = () => submit('planning'); const execute = () => submit('execution')
watch([projectId, () => route.fullPath], () => {
  clearTimeout(timer); scope.reset(projectId.value, route.fullPath); readEpoch++; project.value = null; plans.value = []; tasks.value = []; selectedId.value = null; selectedCases.value = []; editorOpen.value = false; editingPlan.value = null; saving.value = false; submitting.value = ''; retrySubmission = null; models.value = []; modelId.value = null; error.value = ''; actionError.value = ''
  if (!projectId.value || projectStore.currentProject?.project_type !== 'security') return router.replace('/security-testing/projects')
  refresh(); if (!taskPage.value) loadModels(scope.capture())
}, { immediate: true })
watch(caseSignature, () => { selectedCases.value = [] })
onBeforeUnmount(() => { scope.dispose(); clearTimeout(timer) })
</script>
<style scoped>.security-workspace { padding: 24px; max-width: 1500px; margin: 0 auto; }.heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }.heading h2 { margin: 0; }.compact { flex-wrap: nowrap; }.hint { color: var(--app-text-muted); font-size: 13px; line-height: 1.6; }.space { margin-top: 20px; }.generation-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }</style>
