<template>
  <div class="perf-workspace">
    <el-alert type="info" :closable="false" show-icon class="phase-notice">
      <template #title>性能测试第一阶段：仅管理计划、受控目标和接入节点；当前没有压测执行能力。</template>
      <template #default>节点“在线”仅表示 Agent 最近成功心跳，不代表 Locust Worker 已就绪或能发压。</template>
    </el-alert>

    <el-tabs v-model="activeTab" @tab-change="goTab">
      <el-tab-pane label="压测计划" name="plans">
        <div class="toolbar"><span>计划只描述受控请求步骤，不支持 Python 或动态表达式。</span><el-button v-if="canManagePlans" type="primary" @click="openPlan()">新建计划</el-button></div>
        <el-empty v-if="!loading.plans && plans.length === 0" description="暂无压测计划" />
        <el-table v-else v-loading="loading.plans" :data="plans" row-key="id">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column label="目标" min-width="160"><template #default="{ row }">{{ targetName(row.target_id) }}</template></el-table-column>
          <el-table-column label="负载" min-width="180"><template #default="{ row }">{{ row.users }} users / 每秒启动 {{ row.spawn_rate }} users</template></el-table-column>
          <el-table-column label="步骤" width="90"><template #default="{ row }">{{ row.steps?.length || 0 }}</template></el-table-column>
          <el-table-column v-if="canManagePlans || canDeletePlans" label="操作" width="160" fixed="right"><template #default="{ row }"><el-button v-if="canManagePlans" link type="primary" @click="openPlan(row)">编辑</el-button><el-button v-if="canDeletePlans" link type="danger" @click="removePlan(row)">删除</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="节点管理" name="nodes">
        <div class="toolbar"><span>每 {{ config.heartbeat_interval_seconds }} 秒轮询一次；在线 Agent 不等于 Worker 就绪。</span><el-button v-if="canManageNodes" type="primary" @click="openNode()">登记节点</el-button></div>
        <el-empty v-if="!loading.nodes && nodes.length === 0" description="暂无节点" />
        <el-table v-else v-loading="loading.nodes" :data="nodes" row-key="id">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column label="网络" width="100"><template #default="{ row }">{{ performanceNetworkModeLabel(row.network_mode) }}</template></el-table-column>
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="nodeType(row.status)">{{ performanceNodeStatusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="last_seen_at" label="最后心跳" min-width="170"><template #default="{ row }">{{ formatTime(row.last_seen_at) }}</template></el-table-column>
          <el-table-column label="版本" min-width="160"><template #default="{ row }">Agent {{ row.agent_version || '-' }} / 引擎 {{ row.engine_version || '-' }}</template></el-table-column>
          <el-table-column v-if="canManageNodes" label="操作" width="220" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openNode(row)">编辑</el-button><el-button link type="warning" @click="resetEnrollment(row)">重置凭证</el-button><el-button link type="danger" @click="revokeNode(row)">吊销</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="压测目标" name="targets">
        <div class="toolbar"><span>只允许 HTTP(S) origin；服务端会校验目标、方法和跨项目关联。</span><el-button v-if="canManageTargets" type="primary" @click="openTarget()">新建目标</el-button></div>
        <el-empty v-if="!loading.targets && targets.length === 0" description="暂无压测目标" />
        <el-table v-else v-loading="loading.targets" :data="targets" row-key="id">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column prop="base_url" label="受控 Origin" min-width="260" show-overflow-tooltip />
          <el-table-column label="允许方法" min-width="190"><template #default="{ row }"><el-tag v-for="method in row.allowed_methods" :key="method" size="small" class="method-tag">{{ method }}</el-tag></template></el-table-column>
          <el-table-column v-if="canManageTargets" label="操作" width="160" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openTarget(row)">编辑</el-button><el-button link type="danger" @click="removeTarget(row)">删除</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="planDialog.visible" :title="planDialog.item ? '编辑压测计划' : '新建压测计划'" width="900px" :close-on-click-modal="false" @closed="resetPlan">
      <el-form ref="planFormRef" :model="planForm" :rules="planRules" label-width="105px">
        <el-form-item label="名称" prop="name"><el-input v-model="planForm.name" maxlength="100" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="planForm.description" type="textarea" :rows="2" maxlength="500" /></el-form-item>
        <el-form-item label="压测目标" prop="target_id"><el-select v-model="planForm.target_id" style="width:100%"><el-option v-for="target in targets" :key="target.id" :label="target.name" :value="target.id" /></el-select></el-form-item>
        <div class="form-grid"><el-form-item label="并发 users" prop="users"><el-input-number v-model="planForm.users" :min="1" :max="config.limits.max_users" /></el-form-item><el-form-item label="启动速率" prop="spawn_rate"><el-input-number v-model="planForm.spawn_rate" :min="1" :max="config.limits.max_spawn_rate" /><span class="input-suffix">users/秒</span></el-form-item><el-form-item label="时长（秒）" prop="duration_seconds"><el-input-number v-model="planForm.duration_seconds" :min="1" :max="config.limits.max_duration_seconds" /></el-form-item><el-form-item label="等待（秒）" prop="wait_seconds"><el-input-number v-model="planForm.wait_seconds" :min="0.1" :max="60" :step="0.1" /></el-form-item></div>
        <el-alert type="info" :closable="false" class="step-help">users 表示并发虚拟用户；启动速率表示每秒启动的虚拟用户数，不代表每秒请求数。本批仅保存计划，不会执行。</el-alert>
        <div class="steps-title"><span>请求步骤</span><el-button plain size="small" :disabled="planForm.steps.length >= config.limits.max_steps" @click="addStep">添加步骤</el-button></div>
        <div v-for="(step, index) in planForm.steps" :key="index" class="step-card">
          <div class="step-heading">步骤 {{ index + 1 }}<el-button link type="danger" :disabled="planForm.steps.length === 1" @click="removeStep(index)">移除</el-button></div>
          <div class="step-grid"><el-input v-model="step.name" placeholder="步骤名称" /><el-select v-model="step.method"><el-option v-for="method in selectedTargetMethods" :key="method" :label="method" :value="method" /></el-select><el-input v-model="step.path" placeholder="/relative-path" /><el-input-number v-model="step.expected_status" :min="100" :max="599" /></div>
          <div class="json-grid"><el-form-item :error="stepErrors[index]?.headers"><el-input v-model="step.headersText" type="textarea" :rows="2" placeholder='Headers JSON，例如 {"Accept":"application/json"}' /></el-form-item><el-form-item :error="stepErrors[index]?.body"><el-input v-model="step.bodyText" type="textarea" :rows="2" placeholder="Body JSON（留空表示 null）" /></el-form-item></div>
        </div>
      </el-form>
      <template #footer><el-button @click="planDialog.visible = false">取消</el-button><el-button type="primary" :loading="saving.plan" @click="savePlan">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="targetDialog.visible" :title="targetDialog.item ? '编辑压测目标' : '新建压测目标'" width="560px" :close-on-click-modal="false" @closed="resetTarget">
      <el-form ref="targetFormRef" :model="targetForm" :rules="targetRules" label-width="100px"><el-form-item label="名称" prop="name"><el-input v-model="targetForm.name" /></el-form-item><el-form-item label="HTTP Origin" prop="base_url"><el-input v-model="targetForm.base_url" placeholder="https://example.com" /></el-form-item><el-form-item label="允许方法" prop="allowed_methods"><el-checkbox-group v-model="targetForm.allowed_methods"><el-checkbox v-for="method in methods" :key="method" :label="method">{{ method }}</el-checkbox></el-checkbox-group></el-form-item></el-form>
      <template #footer><el-button @click="targetDialog.visible = false">取消</el-button><el-button type="primary" :loading="saving.target" @click="saveTarget">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="nodeDialog.visible" :title="nodeDialog.item ? '编辑节点' : '登记节点'" width="560px" :close-on-click-modal="false" @closed="resetNode">
      <el-form ref="nodeFormRef" :model="nodeForm" :rules="nodeRules" label-width="100px"><el-form-item label="名称" prop="name"><el-input v-model="nodeForm.name" /></el-form-item><el-form-item label="网络模式" prop="network_mode"><el-radio-group v-model="nodeForm.network_mode"><el-radio label="lan">LAN</el-radio><el-radio label="public">Public</el-radio></el-radio-group></el-form-item><el-form-item label="标签 JSON" :error="nodeLabelsError"><el-input v-model="nodeForm.labelsText" type="textarea" :rows="3" placeholder='例如 {"region":"shanghai"}' /></el-form-item></el-form>
      <template #footer><el-button @click="nodeDialog.visible = false">取消</el-button><el-button type="primary" :loading="saving.node" @click="saveNode">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="tokenDialog.visible" title="一次性注册凭证" width="640px" :close-on-click-modal="false" @closed="clearEnrollmentToken">
      <el-alert type="warning" :closable="false">请立即复制给节点管理员。关闭此窗口后凭证将从页面内存清除，且不会再次显示。</el-alert>
      <p class="expiry">过期时间：{{ formatTime(tokenDialog.expiresAt) }}</p><el-input :model-value="tokenDialog.token" readonly type="textarea" :rows="3" />
      <template #footer><el-button type="primary" @click="copyEnrollmentToken">复制并关闭</el-button><el-button @click="tokenDialog.visible = false">关闭并清除</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { getProject } from '@/api/projects'
import { createPerformanceNode, createPerformancePlan, createPerformanceTarget, deletePerformancePlan, deletePerformanceTarget, getPerformanceConfig, getPerformanceNodes, getPerformancePlans, getPerformanceTargets, performanceErrorMessage, resetPerformanceNodeEnrollment, revokePerformanceNode, updatePerformanceNode, updatePerformancePlan, updatePerformanceTarget } from '@/api/performance'
import { buildPerformanceTargetPayload, copyEnrollmentTokenToClipboard, isPerformancePlatformAdmin, performanceNetworkModeLabel, performanceNodeStatusLabel, performancePlanPermissions, samePerformanceScope } from './performanceWorkspaceState'

const route = useRoute(); const router = useRouter(); const authStore = useAuthStore(); const projectStore = useProjectStore()
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const defaults = { heartbeat_interval_seconds: 5, limits: { max_users: 100, max_duration_seconds: 600, max_spawn_rate: 100, max_steps: 20 } }
const config = reactive({ ...defaults, limits: { ...defaults.limits } })
const activeTab = ref('plans')
const projectId = computed(() => projectStore.currentProjectId)
const detail = ref(null); const plans = ref([]); const nodes = ref([]); const targets = ref([])
const loading = reactive({ plans: false, nodes: false, targets: false }); const saving = reactive({ plan: false, node: false, target: false })
const planDialog = reactive({ visible: false, item: null }); const nodeDialog = reactive({ visible: false, item: null }); const targetDialog = reactive({ visible: false, item: null })
const tokenDialog = reactive({ visible: false, token: '', expiresAt: '' }); const planFormRef = ref(); const nodeFormRef = ref(); const targetFormRef = ref(); const stepErrors = ref([]); const nodeLabelsError = ref('')
const planForm = reactive({ name: '', description: '', target_id: null, users: 1, spawn_rate: 1, duration_seconds: 30, wait_seconds: 1, steps: [] })
const targetForm = reactive({ name: '', base_url: '', allowed_methods: ['GET'] }); const nodeForm = reactive({ name: '', network_mode: 'lan', labelsText: '{}' })
const currentMember = computed(() => detail.value?.members?.find((member) => member.username === authStore.user?.username))
const canManagePlans = computed(() => performancePlanPermissions(authStore.user, currentMember.value).canEdit)
const canDeletePlans = computed(() => performancePlanPermissions(authStore.user, currentMember.value).canDelete)
const canManageTargets = computed(() => isPerformancePlatformAdmin(authStore.user)); const canManageNodes = computed(() => isPerformancePlatformAdmin(authStore.user))
const planRules = { name: [{ required: true, message: '请输入计划名称', trigger: 'blur' }], target_id: [{ required: true, message: '请选择压测目标', trigger: 'change' }] }; const targetRules = { name: [{ required: true, message: '请输入目标名称', trigger: 'blur' }], base_url: [{ required: true, message: '请输入 HTTP(S) origin', trigger: 'blur' }], allowed_methods: [{ type: 'array', min: 1, message: '至少选择一种方法', trigger: 'change' }] }; const nodeRules = { name: [{ required: true, message: '请输入节点名称', trigger: 'blur' }], network_mode: [{ required: true, message: '请选择网络模式', trigger: 'change' }] }
const selectedTargetMethods = computed(() => targets.value.find((item) => String(item.id) === String(planForm.target_id))?.allowed_methods || methods)
let pollTimer; let epoch = 0
const dataOf = (response) => response?.data ?? response; const listOf = (response) => { const data = dataOf(response); return data?.items || (Array.isArray(data) ? data : []) }
const formatTime = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'; const targetName = (id) => targets.value.find((item) => String(item.id) === String(id))?.name || `目标 #${id}`; const nodeType = (status) => ({ online: 'success', offline: 'info', pending: 'warning', revoked: 'danger' }[status] || 'info')
const syncActiveTab = () => { activeTab.value = ({ PerfNodes: 'nodes', PerfTargets: 'targets' }[route.name] || 'plans') }
const goTab = (tab) => router.push({ name: tab === 'nodes' ? 'PerfNodes' : tab === 'targets' ? 'PerfTargets' : 'PerfPlans' })
const captureScope = () => ({ projectId: projectId.value, epoch })
const scopeIsCurrent = (scope) => samePerformanceScope(scope, { projectId: projectId.value, epoch })
function invalidateProjectUi() {
  clearInterval(pollTimer)
  detail.value = null
  plans.value = []
  nodes.value = []
  targets.value = []
  Object.assign(loading, { plans: false, nodes: false, targets: false })
  Object.assign(saving, { plan: false, node: false, target: false })
  planDialog.visible = false
  targetDialog.visible = false
  nodeDialog.visible = false
  tokenDialog.visible = false
  resetPlan()
  resetTarget()
  resetNode()
  clearEnrollmentToken()
}
async function loadAccess(requestProjectId, requestEpoch) {
  try {
    const response = await getProject(requestProjectId)
    if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) detail.value = dataOf(response)
  } catch {
    if (requestEpoch === epoch) detail.value = null
  }
}
async function loadConfig(requestProjectId, requestEpoch) { try { const data = dataOf(await getPerformanceConfig(requestProjectId)); if (requestEpoch !== epoch || String(projectId.value) !== String(requestProjectId)) return; Object.assign(config, defaults, data || {}); config.limits = { ...defaults.limits, ...(data?.limits || {}) } } catch (error) { if (requestEpoch === epoch) ElMessage.error(performanceErrorMessage(error, '加载性能配置失败')) } }
async function loadList(kind, requestProjectId, requestEpoch) { loading[kind] = true; const call = { plans: getPerformancePlans, nodes: getPerformanceNodes, targets: getPerformanceTargets }[kind]; try { const result = listOf(await call(requestProjectId)); if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) ({ plans, nodes, targets })[kind].value = result } catch (error) { if (requestEpoch === epoch) ElMessage.error(performanceErrorMessage(error, `加载${kind}失败`)) } finally { if (requestEpoch === epoch) loading[kind] = false } }
async function refreshAll() { const requestProjectId = projectId.value; const requestEpoch = ++epoch; clearInterval(pollTimer); if (!requestProjectId) return; if (projectStore.currentProject?.project_type !== 'perf') { ElMessage.warning('请先从性能测试项目列表选择性能项目'); router.replace('/perf-testing/projects'); return }; await Promise.all([loadAccess(requestProjectId, requestEpoch), loadConfig(requestProjectId, requestEpoch), loadList('targets', requestProjectId, requestEpoch), loadList('plans', requestProjectId, requestEpoch), loadList('nodes', requestProjectId, requestEpoch)]); if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) pollTimer = window.setInterval(() => loadList('nodes', requestProjectId, requestEpoch), Math.max(5000, Math.min(10000, config.heartbeat_interval_seconds * 1000))) }
function blankStep() { return { name: '', method: 'GET', path: '/', expected_status: 200, headersText: '{}', bodyText: '' } }; function addStep() { planForm.steps.push(blankStep()) }; function removeStep(index) { planForm.steps.splice(index, 1) }
function resetPlan() { Object.assign(planForm, { name: '', description: '', target_id: null, users: 1, spawn_rate: 1, duration_seconds: 30, wait_seconds: 1, steps: [] }); planDialog.item = null; stepErrors.value = []; planFormRef.value?.clearValidate() }
function openPlan(item) { resetPlan(); planDialog.item = item || null; Object.assign(planForm, item ? { ...item, steps: (item.steps || []).map((step) => ({ ...step, headersText: JSON.stringify(step.headers || {}, null, 2), bodyText: step.body == null ? '' : JSON.stringify(step.body, null, 2) })) } : { steps: [blankStep()] }); planDialog.visible = true }
function stepsPayload() { stepErrors.value = []; let invalid = false; const result = planForm.steps.map((step, index) => { const errors = {}; let headers = {}; let body = null; try { headers = JSON.parse(step.headersText || '{}'); if (!headers || Array.isArray(headers) || typeof headers !== 'object') throw new Error() } catch { errors.headers = 'Headers 必须是 JSON 对象'; invalid = true } try { if (step.bodyText?.trim()) body = JSON.parse(step.bodyText) } catch { errors.body = 'Body 必须是合法 JSON'; invalid = true } if (!step.name?.trim()) { errors.headers ||= '请填写步骤名称'; invalid = true }; if (!/^\/(?!\/)/.test(step.path || '') || /\\|[\x00-\x1f]/.test(step.path || '')) { errors.body ||= '路径必须以单个 / 开头，且不能包含反斜线或控制字符'; invalid = true }; stepErrors.value[index] = errors; return { name: step.name?.trim(), method: step.method, path: step.path, expected_status: step.expected_status || 200, headers, body } }); return invalid ? null : result }
async function savePlan() {
  if (saving.plan) return
  const scope = captureScope()
  const valid = await planFormRef.value?.validate().catch(() => false)
  if (!scopeIsCurrent(scope)) return
  const steps = stepsPayload()
  if (!valid || !steps) return
  const payload = {
    name: planForm.name.trim(), description: planForm.description?.trim() || '', target_id: planForm.target_id,
    users: planForm.users, spawn_rate: planForm.spawn_rate, duration_seconds: planForm.duration_seconds,
    wait_seconds: planForm.wait_seconds, steps,
  }
  saving.plan = true
  try {
    if (planDialog.item) await updatePerformancePlan(scope.projectId, planDialog.item.id, payload)
    else await createPerformancePlan(scope.projectId, payload)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success('计划已保存')
    planDialog.visible = false
    await loadList('plans', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '保存计划失败'))
  } finally { if (scopeIsCurrent(scope)) saving.plan = false }
}
function resetTarget() { Object.assign(targetForm, { name: '', base_url: '', allowed_methods: ['GET'] }); targetDialog.item = null; targetFormRef.value?.clearValidate() }; function openTarget(item) { resetTarget(); if (item) Object.assign(targetForm, { ...item, allowed_methods: [...item.allowed_methods] }); targetDialog.item = item || null; targetDialog.visible = true }
async function saveTarget() {
  if (saving.target) return
  const scope = captureScope()
  const valid = await targetFormRef.value?.validate().catch(() => false)
  if (!scopeIsCurrent(scope)) return
  if (!valid) return
  const payload = buildPerformanceTargetPayload(targetForm)
  saving.target = true
  try {
    if (targetDialog.item) await updatePerformanceTarget(scope.projectId, targetDialog.item.id, payload)
    else await createPerformanceTarget(scope.projectId, payload)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success('目标已保存')
    targetDialog.visible = false
    await loadList('targets', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '保存目标失败'))
  } finally { if (scopeIsCurrent(scope)) saving.target = false }
}
function resetNode() { Object.assign(nodeForm, { name: '', network_mode: 'lan', labelsText: '{}' }); nodeDialog.item = null; nodeLabelsError.value = ''; nodeFormRef.value?.clearValidate() }; function openNode(item) { resetNode(); if (item) Object.assign(nodeForm, { name: item.name, network_mode: item.network_mode, labelsText: JSON.stringify(item.labels || {}, null, 2) }); nodeDialog.item = item || null; nodeDialog.visible = true }
async function saveNode() {
  if (saving.node) return
  const scope = captureScope()
  const valid = await nodeFormRef.value?.validate().catch(() => false)
  if (!scopeIsCurrent(scope)) return
  if (!valid) return
  let labels
  try {
    labels = JSON.parse(nodeForm.labelsText || '{}')
    if (!labels || Array.isArray(labels) || typeof labels !== 'object') throw new Error()
  } catch { nodeLabelsError.value = '标签必须是 JSON 对象'; return }
  nodeLabelsError.value = ''
  const isNewNode = !nodeDialog.item
  const payload = { name: nodeForm.name.trim(), network_mode: nodeForm.network_mode, labels }
  saving.node = true
  try {
    const result = isNewNode
      ? await createPerformanceNode(scope.projectId, payload)
      : await updatePerformanceNode(scope.projectId, nodeDialog.item.id, payload)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success(isNewNode ? '节点已登记' : '节点已保存')
    nodeDialog.visible = false
    if (isNewNode && result?.enrollment_token) showEnrollment(result)
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '保存节点失败'))
  } finally { if (scopeIsCurrent(scope)) saving.node = false }
}
function showEnrollment(result) { tokenDialog.token = result.enrollment_token; tokenDialog.expiresAt = result.expires_at; tokenDialog.visible = true }; function clearEnrollmentToken() { tokenDialog.token = ''; tokenDialog.expiresAt = '' }
const isCancelled = (error) => ['cancel', 'close'].includes(error)

async function resetEnrollment(node) {
  const scope = captureScope()
  try {
    await ElMessageBox.confirm('重置会立即使旧注册凭证和旧长期身份失效，原客户端必须重新注册。', '重置注册凭证', { type: 'warning', confirmButtonText: '重置' })
    if (!scopeIsCurrent(scope)) return
    const result = await resetPerformanceNodeEnrollment(scope.projectId, node.id)
    if (!scopeIsCurrent(scope)) return
    showEnrollment(result)
    ElMessage.success('已生成新的注册凭证')
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '重置凭证失败'))
  }
}

async function revokeNode(node) {
  const scope = captureScope()
  try {
    await ElMessageBox.confirm('吊销将使该节点的长期身份和未消费注册凭证失效。本批没有运行任务需要终止。', '吊销节点', { type: 'warning', confirmButtonText: '吊销' })
    if (!scopeIsCurrent(scope)) return
    await revokePerformanceNode(scope.projectId, node.id)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success('节点已吊销')
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '吊销节点失败'))
  }
}

async function removeTarget(target) {
  const scope = captureScope()
  try {
    await ElMessageBox.confirm(`确定删除目标“${target.name}”吗？被计划引用时服务端会拒绝删除。`, '删除目标', { type: 'warning' })
    if (!scopeIsCurrent(scope)) return
    await deletePerformanceTarget(scope.projectId, target.id)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success('目标已删除')
    await Promise.all([loadList('targets', scope.projectId, scope.epoch), loadList('plans', scope.projectId, scope.epoch)])
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '删除目标失败'))
  }
}

async function removePlan(plan) {
  const scope = captureScope()
  try {
    await ElMessageBox.confirm(`确定删除计划“${plan.name}”吗？`, '删除计划', { type: 'warning' })
    if (!scopeIsCurrent(scope)) return
    await deletePerformancePlan(scope.projectId, plan.id)
    if (!scopeIsCurrent(scope)) return
    ElMessage.success('计划已删除')
    await loadList('plans', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '删除计划失败'))
  }
}

async function copyEnrollmentToken() {
  try {
    await copyEnrollmentTokenToClipboard(tokenDialog.token, navigator.clipboard.writeText.bind(navigator.clipboard))
    ElMessage.success('凭证已复制')
    tokenDialog.visible = false
  } catch {
    ElMessage.warning('无法自动复制，请手动复制后关闭')
  }
}
watch(projectId, () => { invalidateProjectUi(); refreshAll() })
watch(() => route.name, syncActiveTab, { immediate: true })
onMounted(refreshAll)
onBeforeUnmount(() => { ++epoch; invalidateProjectUi() })
</script>

<style scoped>
.perf-workspace { max-width: 1280px; margin: 0 auto; padding: 4px 10px 28px; }.phase-notice { margin-bottom: 18px; }.toolbar { min-height: 44px; display: flex; justify-content: space-between; align-items: center; gap: 16px; color: var(--app-text-muted); margin-bottom: 12px; }.method-tag { margin: 2px; }.input-suffix { margin-left: 8px; color: var(--app-text-muted); font-size: 12px; }.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }.step-help { margin: 0 0 14px; }.steps-title, .step-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }.step-card { border: 1px solid var(--app-border-light); border-radius: 8px; padding: 12px; margin-bottom: 12px; }.step-grid { display: grid; grid-template-columns: 1.5fr .8fr 1.5fr .8fr; gap: 8px; }.json-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }.json-grid :deep(.el-form-item) { margin-bottom: 0; }.expiry { color: var(--app-text-muted); font-size: 13px; } @media (max-width: 760px) { .toolbar, .form-grid, .step-grid, .json-grid { display: flex; flex-direction: column; align-items: stretch; }.toolbar { align-items: flex-start; }.step-grid { gap: 8px; } }
</style>
