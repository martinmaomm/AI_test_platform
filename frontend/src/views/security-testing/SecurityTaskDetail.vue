<template>
  <section class="security-task-detail">
    <div class="heading"><div><h2>{{ task?.kind === 'planning' ? '检查项规划详情' : '安全检查执行详情' }}</h2><p class="hint">{{ task?.snapshot?.plan_name || task?.plan_name || '安全测试任务' }}</p></div><div class="actions"><el-button @click="router.push('/security-testing/tasks')">返回任务记录</el-button><el-button :loading="loading" @click="load">刷新</el-button><el-button v-if="permissions.execute && isActiveTask(task)" type="danger" plain :loading="stopping" :disabled="task.cancel_requested" @click="stop">{{ task.cancel_requested ? '正在停止' : '停止任务' }}</el-button><el-button v-if="permissions.report && canExportTask(task)" type="primary" :loading="exporting" @click="download">导出 HTML 报告</el-button></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="space" />
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="space" />
    <el-skeleton v-if="loading && !task" :rows="6" animated />
    <template v-if="task">
      <el-descriptions :column="3" border class="space"><el-descriptions-item label="任务状态"><el-tag :type="statusType(task.status)">{{ task.cancel_requested && isActiveTask(task) ? '正在停止' : statusLabel(task.status) }}</el-tag></el-descriptions-item><el-descriptions-item label="计划版本">{{ task.snapshot?.plan_revision ?? '—' }}</el-descriptions-item><el-descriptions-item label="创建时间">{{ date(task.created_at) }}</el-descriptions-item><el-descriptions-item label="开始时间">{{ date(task.started_at) }}</el-descriptions-item><el-descriptions-item label="结束时间">{{ date(task.finished_at) }}</el-descriptions-item><el-descriptions-item label="任务 ID">{{ task.id }}</el-descriptions-item></el-descriptions>
      <el-alert v-if="task.error_message" :title="task.error_message" :description="task.error_code || ''" type="error" :closable="false" class="space" />
      <el-card v-if="isActiveTask(task)" shadow="never" class="space"><div class="heading"><span>{{ task.kind === 'planning' ? '正在生成检查计划' : `已完成 ${task.progress?.completed || 0} / ${task.progress?.total || task.snapshot?.cases?.length || 0} 项检查` }}</span><span v-if="task.kind === 'execution'">已发送 {{ task.progress?.requests_sent || 0 }} 次请求</span></div><el-progress :percentage="progress" :indeterminate="task.kind === 'planning'" class="space" /><p class="hint">{{ task.cancel_requested ? '已发送停止请求，当前检查会在下一个检查点结束。' : '任务在后台异步处理，本页自动更新，关闭页面不会中断任务。' }}</p></el-card>
      <template v-if="task.kind === 'planning'">
        <el-alert v-if="task.status === 'completed'" :title="task.result?.plan_applied === false ? '原计划已修改，本次规划未覆盖当前计划，请返回重新生成。' : '检查项已生成，返回计划勾选要执行的项目。'" :type="task.result?.plan_applied === false ? 'warning' : 'success'" :closable="false" class="space" />
        <p class="hint">生成方式：{{ task.result?.source === 'ai' || task.model_config_id ? 'AI 规划' : '规则规划' }}。规划阶段不请求被测接口。</p>
        <el-table :data="task.result?.cases || []" empty-text="尚无生成结果"><el-table-column label="接口" min-width="180"><template #default="{ row }">{{ endpointName(row.endpoint_index) }}</template></el-table-column><el-table-column label="检查方向" width="135"><template #default="{ row }">{{ checkLabel(row.check) }}</template></el-table-column><el-table-column prop="title" label="检查项" min-width="180" /><el-table-column prop="rationale" label="检查说明" min-width="260" /></el-table>
        <el-button class="space" type="primary" @click="router.push('/security-testing/plans')">返回计划选择检查项</el-button>
      </template>
      <template v-else>
        <el-descriptions :column="3" border class="space"><el-descriptions-item label="请求预算">{{ task.result?.summary?.requests_sent ?? task.progress?.requests_sent ?? 0 }} / {{ config.max_requests }} 次</el-descriptions-item><el-descriptions-item label="请求速率">{{ config.rate_per_second }} 次/秒</el-descriptions-item><el-descriptions-item label="单次等待">{{ config.timeout_seconds }} 秒</el-descriptions-item><el-descriptions-item label="允许来源" :span="3">{{ config.allowed_origins?.join('；') || '—' }}</el-descriptions-item></el-descriptions>
        <div class="metrics space"><el-card v-for="metric in resultCounts" :key="metric.status" shadow="never"><span class="hint">{{ statusLabel(metric.status) }}</span><strong>{{ metric.count }}</strong></el-card></div>
        <el-alert title="结论基于本次选中接口和实际采集证据；“数据不足”或“未执行”不能视为通过。HTTP 200 本身不证明权限校验通过。" type="info" :closable="false" class="space" />
        <el-table :data="items" row-key="case_id" empty-text="尚无检查结果" class="space">
          <el-table-column type="expand"><template #default="{ row }"><div class="evidence-detail"><p><strong>判断说明：</strong>{{ row.summary || '暂无说明' }}</p><p><strong>修复或排查建议：</strong>{{ row.recommendation || '暂无建议' }}</p><h4>请求与响应证据</h4><p class="hint">包含实际请求、状态、耗时及有限响应预览。认证凭据和响应中的敏感值已隐藏；响应截断会在证据中标明。</p><SecurityEvidence :evidence="row.evidence" /></div></template></el-table-column>
          <el-table-column label="结论" width="135"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="接口" min-width="160"><template #default="{ row }">{{ endpointName(row.endpoint_index) }}</template></el-table-column>
          <el-table-column label="检查方向" width="130"><template #default="{ row }">{{ checkLabel(row.check) }}</template></el-table-column><el-table-column prop="title" label="检查项" min-width="170" /><el-table-column prop="summary" label="判断说明" min-width="270" />
        </el-table>
      </template>
      <el-card v-if="limitations.length" class="space" shadow="never"><template #header>检查范围与数据限制</template><ul><li v-for="(item, index) in limitations" :key="index">{{ item }}</li></ul></el-card>
    </template>
  </section>
</template>
<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { getProject } from '@/api/projects'
import { getSecurityTask, stopSecurityTask, downloadSecurityReport } from '@/api/security'
import SecurityEvidence from './SecurityEvidence.vue'
import { unwrap, securityPermissions, createSecurityScope, securityError, statusLabel, statusType, isActiveTask, canExportTask, checkLabel } from './securityState'
const route = useRoute(), router = useRouter(), projectStore = useProjectStore(), authStore = useAuthStore()
const projectId = computed(() => projectStore.currentProjectId), taskId = computed(() => route.params.taskId), project = ref(null), task = ref(null), loading = ref(false), error = ref(''), actionError = ref(''), stopping = ref(false), exporting = ref(false)
const permissions = computed(() => securityPermissions(authStore.user, project.value)), config = computed(() => task.value?.snapshot?.config || {}), items = computed(() => task.value?.result?.items || []), limitations = computed(() => task.value?.result?.limitations || [])
const resultCounts = computed(() => ['confirmed', 'suspected', 'passed', 'inconclusive', 'error', 'skipped'].map((status) => ({ status, count: items.value.filter((item) => item.status === status).length })))
const progress = computed(() => { const data = task.value?.progress; return data?.total > 0 ? Math.min(100, Math.max(0, Math.round((data.completed || 0) / data.total * 100))) : 0 })
const date = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—'
const endpointName = (index) => { const endpoint = config.value.endpoints?.[index]; return endpoint ? `${endpoint.method} ${endpoint.name || endpoint.url}` : `接口 ${Number(index) + 1}` }
const scope = createSecurityScope(); let timer; let readEpoch = 0
function current(captured) { return scope.current(captured) && String(captured.projectId) === String(projectId.value) && route.fullPath === captured.route }
async function load() {
  const captured = scope.capture(); const id = taskId.value; const sequence = ++readEpoch; clearTimeout(timer)
  if (!captured.projectId || !id) return
  if (route.query.project_id && String(route.query.project_id) !== String(captured.projectId)) { error.value = '该记录属于其他项目，请切换到对应安全项目后从任务记录进入。'; return }
  loading.value = true
  try {
    const loadedProject = unwrap(await getProject(captured.projectId)); if (!current(captured) || sequence !== readEpoch) return
    project.value = loadedProject
    if (loadedProject.project_type !== 'security') { error.value = '请先选择安全测试项目'; return }
    if (!permissions.value.report && !(permissions.value.edit && permissions.value.execute)) { error.value = '当前账号没有查看任务记录的权限'; return }
    const value = unwrap(await getSecurityTask(captured.projectId, id)); if (!current(captured) || sequence !== readEpoch) return
    task.value = value; error.value = ''
    if (isActiveTask(value)) timer = setTimeout(load, 2500)
  } catch (cause) { if (current(captured) && sequence === readEpoch) { error.value = securityError(cause, '读取任务失败，请点击刷新重试'); if ([403, 404].includes(cause.response?.status)) task.value = null } }
  finally { if (current(captured) && sequence === readEpoch) loading.value = false }
}
async function stop() {
  if (!permissions.value.execute || !isActiveTask(task.value) || stopping.value) return
  const captured = scope.capture(); const id = taskId.value; stopping.value = true; actionError.value = ''
  try { const value = unwrap(await stopSecurityTask(captured.projectId, id)); if (current(captured)) { task.value = value; await load() } }
  catch (cause) { if (current(captured)) actionError.value = securityError(cause, '停止请求失败，请刷新确认状态后重试') }
  finally { if (current(captured)) stopping.value = false }
}
async function download() {
  if (exporting.value || !permissions.value.report || !canExportTask(task.value)) return
  const captured = scope.capture(); const id = taskId.value; exporting.value = true; actionError.value = ''
  try {
    const blob = await downloadSecurityReport(captured.projectId, id); if (!current(captured)) return
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `security-${id}.html`; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (cause) { if (current(captured)) actionError.value = securityError(cause, '导出报告失败') }
  finally { if (current(captured)) exporting.value = false }
}
watch([projectId, () => route.fullPath], () => { clearTimeout(timer); scope.reset(projectId.value, route.fullPath); readEpoch++; project.value = null; task.value = null; error.value = ''; actionError.value = ''; stopping.value = false; exporting.value = false; if (!projectId.value) router.replace('/security-testing/projects'); else load() }, { immediate: true })
onBeforeUnmount(() => { scope.dispose(); clearTimeout(timer) })
</script>
<style scoped>.security-task-detail { padding: 24px; max-width: 1500px; margin: 0 auto; }.heading, .actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }.heading { justify-content: space-between; }.heading h2 { margin: 0; }.hint { color: var(--app-text-muted); font-size: 13px; line-height: 1.65; }.space { margin-top: 20px; }.metrics { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }.metrics strong { display: block; font-size: 26px; margin-top: 8px; }.evidence-detail { padding: 12px 24px; line-height: 1.6; }.evidence-detail p { white-space: pre-wrap; overflow-wrap: anywhere; }li { line-height: 1.8; }@media (max-width: 1000px) { .metrics { grid-template-columns: repeat(3, 1fr); } }</style>
