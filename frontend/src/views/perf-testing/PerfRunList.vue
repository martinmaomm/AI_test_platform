<template>
  <section class="performance-runs">
    <div class="toolbar"><el-button @click="router.push({ name: 'PerfPlans' })">返回计划</el-button><el-button :loading="loading" @click="loadRuns">刷新</el-button></div>
    <el-alert v-if="!canRead" type="warning" :closable="false" show-icon>你没有查看本项目执行记录的权限。</el-alert>
    <el-empty v-else-if="!loading && runs.length === 0" description="暂无执行记录" />
    <el-table v-else v-loading="loading" :data="runs" row-key="id">
      <el-table-column prop="plan_name" label="计划" min-width="150" />
      <el-table-column prop="node_name" label="节点" min-width="130" />
      <el-table-column label="状态" min-width="170"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ runSummaryText(row) }}</el-tag></template></el-table-column>
      <el-table-column label="请求 / 失败" min-width="120"><template #default="{ row }">{{ row.latest_metrics?.requests ?? '-' }} / {{ row.latest_metrics?.failures ?? '-' }}</template></el-table-column>
      <el-table-column label="创建时间" min-width="175"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button v-if="canReport" link type="primary" @click="router.push({ name: 'PerfRunDetail', params: { runId: row.id } })">详情</el-button><span v-else>-</span></template></el-table-column>
    </el-table>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { getProject } from '@/api/projects'
import { getPerformanceRuns, performanceErrorMessage } from '@/api/performance'
import { performanceExecutionPermissions, runSummaryText } from './performanceExecutionState'

const router = useRouter(); const authStore = useAuthStore(); const projectStore = useProjectStore()
const runs = ref([]); const project = ref(null); const loading = ref(false); let epoch = 0
const projectId = computed(() => projectStore.currentProjectId)
const member = computed(() => project.value?.members?.find((item) => item.username === authStore.user?.username))
const canRead = computed(() => authStore.user?.is_staff === true || authStore.user?.is_superuser === true || Boolean(member.value))
const canReport = computed(() => performanceExecutionPermissions(authStore.user, member.value).canReport)
const formatTime = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'
const statusType = (status) => ({ completed: 'success', failed: 'danger', incomplete: 'danger', cancelled: 'info', stopping: 'warning', preparing: 'warning', queued: 'warning', running: 'primary' }[status] || 'info')
async function loadRuns() {
  const id = projectId.value; const requestEpoch = ++epoch; if (!id) return
  loading.value = true
  try {
    const [access, response] = await Promise.all([getProject(id), getPerformanceRuns(id)])
    if (requestEpoch !== epoch || String(projectId.value) !== String(id)) return
    project.value = access?.data ?? access
    const data = response?.data ?? response
    runs.value = data?.items || []
  } catch (error) { if (requestEpoch === epoch) ElMessage.error(performanceErrorMessage(error, '加载执行记录失败')) } finally { if (requestEpoch === epoch) loading.value = false }
}
watch(projectId, () => { runs.value = []; project.value = null; loadRuns() })
onMounted(loadRuns); onBeforeUnmount(() => { ++epoch })
</script>

<style scoped>
.performance-runs { max-width: 1280px; margin: 0 auto; padding: 4px 10px 28px; }.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
</style>
