<template>
  <div class="perf-workspace">
    <el-alert type="info" :closable="false" show-icon class="phase-notice">
      <template #title>性能测试第一阶段：每次运行仅选择一个在线节点。</template>
      <template #default>节点“在线”仅表示 Agent 最近成功心跳，不代表 Worker 已就绪或能发压；启动速率是每秒启动虚拟用户数，不是每秒请求数。</template>
    </el-alert>

    <el-tabs v-model="activeTab" @tab-change="goTab">
      <el-tab-pane label="压测计划" name="plans">
        <div class="toolbar"><span>计划只描述受控请求步骤，不支持 Python 或动态表达式。</span><div><el-button v-if="canRead" @click="router.push({ name: 'PerfRuns' })">执行记录</el-button><el-button v-if="canManagePlans" type="primary" @click="openPlan()">新建计划</el-button></div></div>
        <el-alert v-if="!executionEnabled" type="warning" :closable="false" show-icon class="execution-unavailable">{{ executionUnavailableReason }}</el-alert>
        <el-empty v-if="!loading.plans && plans.length === 0" description="暂无压测计划" />
        <el-table v-else v-loading="loading.plans" :data="plans" row-key="id">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column label="目标" min-width="160"><template #default="{ row }">{{ targetName(row.target_id) }}</template></el-table-column>
          <el-table-column label="负载" min-width="180"><template #default="{ row }">{{ row.users }} users / 每秒启动 {{ row.spawn_rate }} users</template></el-table-column>
          <el-table-column label="步骤" width="90"><template #default="{ row }">{{ row.steps?.length || 0 }}</template></el-table-column>
          <el-table-column v-if="canExecute || canManagePlans || canDeletePlans" label="操作" width="220" fixed="right"><template #default="{ row }"><el-tooltip v-if="canExecute && !executionEnabled" :content="executionUnavailableReason"><el-button link type="primary" disabled>执行</el-button></el-tooltip><el-button v-else-if="canExecute" link type="primary" @click="openRun(row)">执行</el-button><el-button v-if="canManagePlans" link type="primary" @click="openPlan(row)">编辑</el-button><el-button v-if="canDeletePlans" link type="danger" @click="removePlan(row)">删除</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="节点管理" name="nodes">
        <div class="toolbar"><span>每 {{ config.heartbeat_interval_seconds }} 秒检查一次节点状态；在线 Agent 不等于 Worker 就绪或可发压。</span><el-button v-if="canManageNodes" type="primary" @click="openNode()">添加节点</el-button></div>
        <el-empty v-if="!loading.nodes && nodes.length === 0" description="暂无节点" />
        <el-table v-else v-loading="loading.nodes" :data="nodes" row-key="id">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column label="网络" width="100"><template #default="{ row }">{{ performanceNetworkModeLabel(row.network_mode) }}</template></el-table-column>
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="nodeType(row.status)">{{ performanceNodeStatusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="last_seen_at" label="最后心跳" min-width="170"><template #default="{ row }">{{ formatTime(row.last_seen_at) }}</template></el-table-column>
          <el-table-column label="版本" min-width="160"><template #default="{ row }">Agent {{ row.agent_version || '-' }} / 引擎 {{ row.engine_version || '-' }}</template></el-table-column>
          <el-table-column v-if="canManageNodes" label="操作" width="310" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openInstallation(row)">安装指导</el-button><el-button link type="primary" @click="openNode(row)">编辑</el-button><el-button link type="warning" @click="resetEnrollment(row)">重置身份</el-button><el-button link type="danger" @click="revokeNode(row)">吊销</el-button></template></el-table-column>
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
        <el-alert type="info" :closable="false" class="step-help">users 表示并发虚拟用户；启动速率表示每秒启动的虚拟用户数，不代表每秒请求数。保存计划不会自动执行，需在计划列表确认后创建运行。</el-alert>
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

    <el-dialog v-model="nodeDialog.visible" :title="nodeDialog.item ? '编辑节点' : '添加节点'" width="580px" :close-on-click-modal="false" @closed="resetNode">
      <el-form ref="nodeFormRef" :model="nodeForm" :rules="nodeRules" label-width="100px"><el-form-item label="节点名称" prop="name"><el-input v-model="nodeForm.name" placeholder="例如：上海压测机" /></el-form-item><el-form-item label="网络位置" prop="network_mode"><el-radio-group v-model="nodeForm.network_mode"><el-radio label="lan">内网：仅记录节点网络位置</el-radio><el-radio label="public">公网：仅记录节点网络位置</el-radio></el-radio-group></el-form-item><el-alert type="info" :closable="false">创建后会提供一条安装命令；无需在平台填写服务器地址或手动拼接参数。节点能连接平台不代表一定能访问测试目标。</el-alert><el-collapse class="advanced-options"><el-collapse-item title="高级选项"><el-form-item label="标签" :error="nodeLabelsError"><el-input v-model="nodeForm.labelsText" type="textarea" :rows="3" placeholder='例如 {"region":"shanghai"}' /></el-form-item></el-collapse-item></el-collapse></el-form>
      <template #footer><el-button @click="nodeDialog.visible = false">取消</el-button><el-button type="primary" :loading="saving.node" @click="saveNode">{{ nodeDialog.item ? '保存' : '创建并查看安装命令' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="installationDialog.visible" title="节点安装向导" width="680px" :close-on-click-modal="false" @closed="clearInstallationGuide">
      <template v-if="installationNode"><el-alert :type="installationStage.key === 'online' ? 'success' : ['offline', 'revoked'].includes(installationStage.key) ? 'warning' : 'info'" :closable="false" show-icon>{{ installationStage.text }}</el-alert><el-descriptions :column="1" border class="installation-summary"><el-descriptions-item label="节点">{{ installationNode.name }}</el-descriptions-item><el-descriptions-item label="归属项目">{{ projectStore.currentProject?.name || detail?.name || '-' }}</el-descriptions-item><el-descriptions-item label="状态">{{ performanceNodeStatusLabel(installationNode.status) }}</el-descriptions-item><el-descriptions-item label="平台地址">{{ installationInfo?.platform_url || '-' }}</el-descriptions-item><el-descriptions-item label="支持架构">{{ installationArchitectureText(installationInfo?.supported_architectures) }}</el-descriptions-item></el-descriptions><el-alert class="installation-requirements" type="info" :closable="false">需要 Linux 主机、Docker 和 root 权限。安装器会处理镜像、证书和平台参数；节点主动连接平台，无需开放节点入站端口。</el-alert><ul v-if="installationInfo?.requirements?.length" class="installation-requirement-list"><li v-for="requirement in installationInfo.requirements" :key="requirement">{{ requirement }}</li></ul><el-alert v-if="installationInfo?.available === false" type="warning" :closable="false">{{ installationInfo.reason || '当前无法生成安装命令，请检查平台安装配置。' }}</el-alert><template v-if="installationDialog.installation?.command && !installationCommandIsExpired"><p class="expiry">凭证 {{ formatTime(installationDialog.installation.expires_at || installationDialog.expiresAt) }} 前有效且敏感，请只在目标节点终端执行。</p><el-input :model-value="installationDialog.installation.command" readonly type="textarea" :rows="4" /><el-button class="copy-command" type="primary" @click="copyInstallationCommand">复制安装命令</el-button></template><template v-else-if="installationDialog.loaded && installationAvailable && canRegenerateInstallation(installationNode, installationDialog.installation, installationCommandIsExpired)"><el-alert type="warning" :closable="false">{{ installationCommandIsExpired ? '安装命令已过期，请重新生成。' : '安装命令未保存在网页中。' }}重新生成会使旧注册凭证和旧节点身份失效。</el-alert><el-button type="warning" :loading="saving.node" @click="regenerateInstallation(installationNode)">重新生成安装命令</el-button></template><template v-else-if="installationStage.key === 'offline'"><el-alert type="warning" :closable="false">请先在原节点检查容器日志、网络和平台地址；不要默认重装或重置身份。</el-alert></template></template>
      <template #footer><el-button @click="installationDialog.visible = false">关闭</el-button></template>
    </el-dialog>
    <el-dialog v-model="runDialog.visible" title="确认执行压测" width="600px" :close-on-click-modal="false" @closed="resetRun">
      <el-alert type="warning" :closable="false" show-icon>将向受控目标发起真实请求。Phase 1 每次仅运行一个节点。</el-alert>
      <el-descriptions v-if="runDialog.plan" :column="1" border class="run-summary"><el-descriptions-item label="计划">{{ runDialog.plan.name }}</el-descriptions-item><el-descriptions-item label="负载">{{ runDialog.plan.users }} 个虚拟用户；每秒启动 {{ runDialog.plan.spawn_rate }} 个虚拟用户；{{ runDialog.plan.duration_seconds }} 秒</el-descriptions-item><el-descriptions-item label="节点"><el-radio-group v-model="runDialog.nodeId" @change="resetRunRequestId"><el-radio v-for="node in onlineNodes" :key="node.id" :label="node.id">{{ node.name }}（{{ performanceNodeStatusLabel(node.status) }}）</el-radio></el-radio-group><span v-if="onlineNodes.length === 0" class="empty-node">暂无可用在线节点</span></el-descriptions-item></el-descriptions>
      <template #footer><el-button @click="runDialog.visible = false">取消</el-button><el-button type="danger" :disabled="!runDialog.nodeId" :loading="saving.run" @click="submitRun">确认执行</el-button></template>
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
import { createPerformanceNode, createPerformancePlan, createPerformanceRun, createPerformanceTarget, deletePerformancePlan, deletePerformanceTarget, getPerformanceConfig, getPerformanceNodeInstallation, getPerformanceNodes, getPerformancePlans, getPerformanceTargets, performanceErrorMessage, resetPerformanceNodeEnrollment, revokePerformanceNode, updatePerformanceNode, updatePerformancePlan, updatePerformanceTarget } from '@/api/performance'
import { copyText } from '@/utils/reportLinks'
import { canRegenerateInstallation, installationArchitectureText, installationCommandExpired, performanceInstallationStage } from '@/utils/performanceInstallation'
import { buildPerformanceTargetPayload, isPerformancePlatformAdmin, performanceNetworkModeLabel, performanceNodeStatusLabel, performancePlanPermissions, samePerformanceScope } from './performanceWorkspaceState'
import { createPerformanceRequestId, executionUnavailableMessage, performanceExecutionPermissions } from './performanceExecutionState'

const route = useRoute(); const router = useRouter(); const authStore = useAuthStore(); const projectStore = useProjectStore()
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const defaults = { heartbeat_interval_seconds: 5, execution_enabled: false, controller_online: false, execution_unavailable_reason: '', limits: { max_users: 100, max_duration_seconds: 600, max_spawn_rate: 100, max_steps: 20 } }
const config = reactive({ ...defaults, limits: { ...defaults.limits } })
const activeTab = ref('plans')
const projectId = computed(() => projectStore.currentProjectId)
const detail = ref(null); const plans = ref([]); const nodes = ref([]); const targets = ref([])
const loading = reactive({ plans: false, nodes: false, targets: false }); const saving = reactive({ plan: false, node: false, target: false, run: false })
const planDialog = reactive({ visible: false, item: null }); const nodeDialog = reactive({ visible: false, item: null }); const targetDialog = reactive({ visible: false, item: null }); const runDialog = reactive({ visible: false, plan: null, nodeId: null, requestId: '' })
const installationDialog = reactive({ visible: false, node: null, installation: null, expiresAt: '', loaded: false }); const planFormRef = ref(); const nodeFormRef = ref(); const targetFormRef = ref(); const stepErrors = ref([]); const nodeLabelsError = ref('')
const planForm = reactive({ name: '', description: '', target_id: null, users: 1, spawn_rate: 1, duration_seconds: 30, wait_seconds: 1, steps: [] })
const targetForm = reactive({ name: '', base_url: '', allowed_methods: ['GET'] }); const nodeForm = reactive({ name: '', network_mode: 'lan', labelsText: '{}' })
const currentMember = computed(() => detail.value?.members?.find((member) => member.username === authStore.user?.username))
const canManagePlans = computed(() => performancePlanPermissions(authStore.user, currentMember.value).canEdit)
const canDeletePlans = computed(() => performancePlanPermissions(authStore.user, currentMember.value).canDelete)
const canManageTargets = computed(() => isPerformancePlatformAdmin(authStore.user)); const canManageNodes = computed(() => isPerformancePlatformAdmin(authStore.user))
const executionPermissions = computed(() => performanceExecutionPermissions(authStore.user, currentMember.value))
const canExecute = computed(() => executionPermissions.value.canExecute); const canReport = computed(() => executionPermissions.value.canReport)
const canRead = computed(() => isPerformancePlatformAdmin(authStore.user) || Boolean(currentMember.value))
const executionEnabled = computed(() => config.execution_enabled === true && config.controller_online === true)
const executionUnavailableReason = computed(() => executionUnavailableMessage(config))
const onlineNodes = computed(() => nodes.value.filter((node) => node.status === 'online'))
const installationNode = computed(() => nodes.value.find((node) => String(node.id) === String(installationDialog.node?.id)) || installationDialog.node)
const installationInfo = computed(() => installationDialog.installation || config.installation || null)
const installationAvailable = computed(() => installationInfo.value?.available === true)
const installationClock = ref(Date.now())
const installationCommandIsExpired = computed(() => installationCommandExpired(installationDialog.installation, installationClock.value))
const installationStage = computed(() => performanceInstallationStage(installationNode.value, installationDialog.installation))
const planRules = { name: [{ required: true, message: '请输入计划名称', trigger: 'blur' }], target_id: [{ required: true, message: '请选择压测目标', trigger: 'change' }] }; const targetRules = { name: [{ required: true, message: '请输入目标名称', trigger: 'blur' }], base_url: [{ required: true, message: '请输入 HTTP(S) origin', trigger: 'blur' }], allowed_methods: [{ type: 'array', min: 1, message: '至少选择一种方法', trigger: 'change' }] }; const nodeRules = { name: [{ required: true, message: '请输入节点名称', trigger: 'blur' }], network_mode: [{ required: true, message: '请选择网络模式', trigger: 'change' }] }
const selectedTargetMethods = computed(() => targets.value.find((item) => String(item.id) === String(planForm.target_id))?.allowed_methods || methods)
let pollTimer; let epoch = 0; let installationRequestNonce = 0
const dataOf = (response) => response?.data ?? response; const listOf = (response) => { const data = dataOf(response); return data?.items || (Array.isArray(data) ? data : []) }
const formatTime = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'; const targetName = (id) => targets.value.find((item) => String(item.id) === String(id))?.name || `目标 #${id}`; const nodeType = (status) => ({ online: 'success', offline: 'info', pending: 'warning', revoked: 'danger' }[status] || 'info')
const syncActiveTab = () => { activeTab.value = ({ PerfNodes: 'nodes', PerfTargets: 'targets' }[route.name] || 'plans') }
const goTab = (tab) => router.push({ name: tab === 'nodes' ? 'PerfNodes' : tab === 'targets' ? 'PerfTargets' : 'PerfPlans' })
const captureScope = () => ({ projectId: projectId.value, epoch })
const scopeIsCurrent = (scope) => samePerformanceScope(scope, { projectId: projectId.value, epoch })
function invalidateProjectUi() {
  clearInterval(pollTimer)
  Object.assign(config, defaults)
  config.limits = { ...defaults.limits }
  delete config.installation
  detail.value = null
  plans.value = []
  nodes.value = []
  targets.value = []
  Object.assign(loading, { plans: false, nodes: false, targets: false })
  Object.assign(saving, { plan: false, node: false, target: false, run: false })
  planDialog.visible = false
  targetDialog.visible = false
  nodeDialog.visible = false
  runDialog.visible = false
  installationDialog.visible = false
  resetPlan()
  resetTarget()
  resetNode()
  clearInstallationGuide()
}
async function loadAccess(requestProjectId, requestEpoch) {
  try {
    const response = await getProject(requestProjectId)
    if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) detail.value = dataOf(response)
  } catch {
    if (requestEpoch === epoch) detail.value = null
  }
}
async function loadConfig(requestProjectId, requestEpoch) { try { const data = dataOf(await getPerformanceConfig(requestProjectId)); if (requestEpoch !== epoch || String(projectId.value) !== String(requestProjectId)) return; delete config.installation; Object.assign(config, defaults, data || {}); config.limits = { ...defaults.limits, ...(data?.limits || {}) } } catch (error) { if (requestEpoch === epoch) ElMessage.error(performanceErrorMessage(error, '加载性能配置失败')) } }
async function loadList(kind, requestProjectId, requestEpoch) { loading[kind] = true; const call = { plans: getPerformancePlans, nodes: getPerformanceNodes, targets: getPerformanceTargets }[kind]; try { const result = listOf(await call(requestProjectId)); if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) ({ plans, nodes, targets })[kind].value = result } catch (error) { if (requestEpoch === epoch) ElMessage.error(performanceErrorMessage(error, `加载${kind}失败`)) } finally { if (requestEpoch === epoch) loading[kind] = false } }
async function refreshAll() { const requestProjectId = projectId.value; const requestEpoch = ++epoch; clearInterval(pollTimer); if (!requestProjectId) return; if (projectStore.currentProject?.project_type !== 'perf') { ElMessage.warning('请先从性能测试项目列表选择性能项目'); router.replace('/perf-testing/projects'); return }; await Promise.all([loadAccess(requestProjectId, requestEpoch), loadConfig(requestProjectId, requestEpoch), loadList('targets', requestProjectId, requestEpoch), loadList('plans', requestProjectId, requestEpoch), loadList('nodes', requestProjectId, requestEpoch)]); if (requestEpoch === epoch && String(projectId.value) === String(requestProjectId)) pollTimer = window.setInterval(() => { installationClock.value = Date.now(); return Promise.all([loadConfig(requestProjectId, requestEpoch), loadList('nodes', requestProjectId, requestEpoch)]) }, Math.max(5000, Math.min(10000, config.heartbeat_interval_seconds * 1000))) }
function blankStep() { return { name: '', method: 'GET', path: '/', expected_status: 200, headersText: '{}', bodyText: '' } }; function addStep() { planForm.steps.push(blankStep()) }; function removeStep(index) { planForm.steps.splice(index, 1) }
function resetPlan() { Object.assign(planForm, { name: '', description: '', target_id: null, users: 1, spawn_rate: 1, duration_seconds: 30, wait_seconds: 1, steps: [] }); planDialog.item = null; stepErrors.value = []; planFormRef.value?.clearValidate() }
function resetRun() { Object.assign(runDialog, { plan: null, nodeId: null, requestId: '' }) }
function resetRunRequestId() { if (runDialog.visible) runDialog.requestId = createPerformanceRequestId() }
function openRun(plan) {
  if (!executionEnabled.value) return
  Object.assign(runDialog, { visible: true, plan, nodeId: onlineNodes.value[0]?.id || null, requestId: createPerformanceRequestId() })
}
async function submitRun() {
  if (saving.run || !runDialog.plan || !runDialog.nodeId || !runDialog.requestId) return
  const scope = captureScope(); const { plan, nodeId, requestId } = runDialog
  saving.run = true
  try {
    const run = dataOf(await createPerformanceRun(scope.projectId, plan.id, { node_id: nodeId, request_id: requestId }))
    if (!scopeIsCurrent(scope)) return
    if (!run?.id) throw new Error('服务端未返回运行记录')
    runDialog.visible = false
    if (canReport.value) {
      ElMessage.success('运行已创建')
      await router.push({ name: 'PerfRunDetail', params: { runId: run.id } })
    } else ElMessage.success('运行已创建；你没有查看执行详情的权限')
  } catch (error) { if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '创建运行失败')) } finally { if (scopeIsCurrent(scope)) saving.run = false }
}
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
    if (isNewNode && result?.node) showInstallation(result)
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '保存节点失败'))
  } finally { if (scopeIsCurrent(scope)) saving.node = false }
}
const hasInstallationCommand = (result) => result?.installation?.available === true && Boolean(result.installation.command)
function showInstallation(result) { installationRequestNonce += 1; installationClock.value = Date.now(); Object.assign(installationDialog, { visible: true, node: result.node, installation: result.installation || null, expiresAt: result.expires_at || '', loaded: true }) }
function clearInstallationGuide() { installationRequestNonce += 1; Object.assign(installationDialog, { node: null, installation: null, expiresAt: '', loaded: false }) }
async function openInstallation(node) {
  const scope = captureScope()
  const requestNonce = ++installationRequestNonce
  Object.assign(installationDialog, { visible: true, node, installation: null, expiresAt: '', loaded: false })
  try {
    const result = await getPerformanceNodeInstallation(scope.projectId, node.id)
    if (!scopeIsCurrent(scope) || !installationDialog.visible || installationRequestNonce !== requestNonce || String(installationDialog.node?.id) !== String(node.id)) return
    Object.assign(installationDialog, { node: result?.node || node, installation: result?.installation || null, loaded: true })
  } catch (error) {
    if (scopeIsCurrent(scope)) ElMessage.error(performanceErrorMessage(error, '加载安装指导失败'))
  }
}
async function copyInstallationCommand() {
  const command = installationDialog.installation?.command
  if (!command) return
  if (installationCommandIsExpired.value) { ElMessage.warning('安装命令已过期，请重新生成'); return }
  try {
    await copyText(command)
    ElMessage.success('安装命令已复制')
  } catch {
    ElMessage.warning('无法自动复制，请手动复制命令')
  }
}
const isCancelled = (error) => ['cancel', 'close'].includes(error)

async function resetEnrollment(node) {
  if (saving.node) return
  const scope = captureScope()
  try {
    await ElMessageBox.confirm('重置会立即使旧注册凭证和旧长期身份失效，原客户端必须重新注册。', '重置注册凭证', { type: 'warning', confirmButtonText: '重置' })
    if (!scopeIsCurrent(scope)) return
    saving.node = true
    const result = await resetPerformanceNodeEnrollment(scope.projectId, node.id)
    if (!scopeIsCurrent(scope)) return
    showInstallation(result)
    ElMessage.success(hasInstallationCommand(result) ? '已生成新的安装命令' : '已生成新的注册凭证')
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '重置凭证失败'))
  } finally { if (scopeIsCurrent(scope)) saving.node = false }
}

async function regenerateInstallation(node) {
  if (saving.node) return
  const scope = captureScope()
  const dialogNonce = installationRequestNonce
  try {
    await ElMessageBox.confirm('重新生成会立即使旧注册凭证和旧长期身份失效，原客户端必须重新注册。', '重新生成安装命令', { type: 'warning', confirmButtonText: '重新生成' })
    if (!scopeIsCurrent(scope) || !installationDialog.visible || installationRequestNonce !== dialogNonce) return
    saving.node = true
    const result = await resetPerformanceNodeEnrollment(scope.projectId, node.id)
    if (!scopeIsCurrent(scope) || !installationDialog.visible || installationRequestNonce !== dialogNonce) return
    installationClock.value = Date.now()
    Object.assign(installationDialog, { node: result.node, installation: result.installation || null, expiresAt: result.expires_at || '', loaded: true })
    ElMessage.success(hasInstallationCommand(result) ? '已生成新的安装命令' : '已生成新的注册凭证')
    await loadList('nodes', scope.projectId, scope.epoch)
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error)) ElMessage.error(performanceErrorMessage(error, '重新生成安装命令失败'))
  } finally { if (scopeIsCurrent(scope)) saving.node = false }
}

async function revokeNode(node) {
  const scope = captureScope()
  try {
    await ElMessageBox.confirm('吊销将使该节点的长期身份和未消费注册凭证失效；该节点正在运行的压测会被请求停止，尾统计可能不完整。', '吊销节点', { type: 'warning', confirmButtonText: '吊销' })
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

watch(projectId, () => { invalidateProjectUi(); refreshAll() })
watch(() => route.name, syncActiveTab, { immediate: true })
onMounted(refreshAll)
onBeforeUnmount(() => { ++epoch; invalidateProjectUi() })
</script>

<style scoped>
.perf-workspace { max-width: 1280px; margin: 0 auto; padding: 4px 10px 28px; }.phase-notice, .execution-unavailable { margin-bottom: 18px; }.toolbar { min-height: 44px; display: flex; justify-content: space-between; align-items: center; gap: 16px; color: var(--app-text-muted); margin-bottom: 12px; }.method-tag { margin: 2px; }.input-suffix { margin-left: 8px; color: var(--app-text-muted); font-size: 12px; }.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }.step-help { margin: 0 0 14px; }.steps-title, .step-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }.step-card { border: 1px solid var(--app-border-light); border-radius: 8px; padding: 12px; margin-bottom: 12px; }.step-grid { display: grid; grid-template-columns: 1.5fr .8fr 1.5fr .8fr; gap: 8px; }.json-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }.json-grid :deep(.el-form-item) { margin-bottom: 0; }.advanced-options { margin-top: 12px; }.installation-summary, .installation-requirements { margin-top: 16px; }.installation-requirement-list { margin: 10px 0; padding-left: 20px; color: var(--app-text-muted); }.copy-command { margin-top: 12px; }.expiry, .empty-node { color: var(--app-text-muted); font-size: 13px; }.run-summary { margin-top: 16px; } @media (max-width: 760px) { .toolbar, .form-grid, .step-grid, .json-grid { display: flex; flex-direction: column; align-items: stretch; }.toolbar { align-items: flex-start; }.step-grid { gap: 8px; } }
</style>
