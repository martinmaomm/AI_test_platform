<template>
  <main class="report-detail-page">
    <div v-if="loading" class="state-wrap"><el-icon class="is-loading"><Loading /></el-icon>加载中…</div>
    <el-result v-else-if="error || is404" :icon="is404 ? 'warning' : 'error'" :title="is404 ? '报告不存在或已删除' : '报告加载失败'" :sub-title="error">
      <template #extra><el-button type="primary" @click="fetchDetail">重试</el-button></template>
    </el-result>
    <article v-else-if="log" class="report-card">
      <header class="report-header"><div><h1>定时任务执行报告</h1><p>{{ log.task_name || '未命名任务' }}</p></div></header>
      <section><h2>概览</h2><div class="overview-cards">
        <div><span>测试类型</span><b>{{ suiteType(log.suite_type) }}</b></div><div><span>执行状态</span><b>{{ statusText(log.report_status || log.status) }}</b></div>
        <div><span>成功率</span><b>{{ log.success_rate ?? '--' }}%</b></div><div><span>通过 / 失败 / 验证未完成 / 错误 / 跳过 / 总计</span><b>{{ log.passed_cases ?? 0 }} / {{ log.failed_cases ?? 0 }} / {{ log.incomplete_cases ?? 0 }} / {{ log.error_cases ?? 0 }} / {{ log.skipped_cases ?? 0 }} / {{ log.total_cases ?? 0 }}</b></div>
        <div><span>开始时间</span><b>{{ formatDateTime(log.start_time) }}</b></div><div><span>结束时间</span><b>{{ formatDateTime(log.end_time) }}</b></div><div><span>执行时长</span><b>{{ log.duration || '--' }}</b></div>
      </div><div v-if="executionErrors" class="error-block"><h3>错误信息</h3><pre>{{ executionErrors }}</pre></div></section>
      <section><h2>执行步骤与日志</h2><el-collapse v-if="displayStepLog"><el-collapse-item title="查看原始执行日志" name="log"><pre class="log-pre">{{ displayStepLog }}</pre></el-collapse-item></el-collapse><p v-else class="muted">暂无本次执行日志。</p></section>
      <section><h2>关联的原生执行报告</h2><el-empty v-if="!linkedExecutions.length" description="该定时任务未返回可关联的实际执行报告。" :image-size="70" /><div v-else class="report-links">
        <router-link v-for="item in linkedExecutions" :key="`${item.kind}-${item.project_id}-${item.execution_id}`" :to="reportPath(item.kind, item.project_id, item.execution_id)" target="_blank" class="native-report-link">{{ item.name || `${item.kind === 'web' ? 'WebUI' : 'API'} 执行 #${item.execution_id}` }}</router-link>
      </div></section>
    </article>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Loading } from '@element-plus/icons-vue'
import { getPublicScheduledReport } from '@/api/publicReports'
import dayjs from 'dayjs'
import { reportPath } from '@/utils/reportLinks'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const is404 = ref(false)
const log = ref(null)
const id = computed(() => route.params.id)
const displayStepLog = computed(() => (log.value?.step_log || log.value?.log || '').trim())
const linkedExecutions = computed(() => Array.isArray(log.value?.linked_executions) ? log.value.linked_executions.filter(item => ['web', 'api'].includes(item?.kind) && item.project_id && item.execution_id) : [])
const executionErrors = computed(() => {
  const count = Number(log.value?.execution_errors || 0)
  return log.value?.error_message || (count > 0 ? `本次有 ${count} 个执行级异常，请查看关联报告或原始日志。` : '')
})
let requestVersion = 0

async function fetchDetail() {
  const version = ++requestVersion
  loading.value = true; error.value = ''; is404.value = false; log.value = null
  try {
    const response = await getPublicScheduledReport(id.value)
    if (version !== requestVersion) return
    const data = response?.data || response
    if (!data || typeof data !== 'object') { is404.value = true; error.value = '请检查报告链接是否正确。'; return }
    log.value = data
  } catch (requestError) {
    if (version !== requestVersion) return
    is404.value = [403, 404].includes(requestError?.response?.status)
    error.value = requestError?.response?.data?.message || requestError?.response?.data?.detail || requestError?.message || '请求失败，请重试。'
  } finally { if (version === requestVersion) loading.value = false }
}
const suiteType = type => ({ web: 'Web 测试', api: 'API 测试' }[type] || type || '--')
const statusText = status => ({ running: '执行中', pending: '等待中', cancelled: '已取消', passed: '通过', success: '通过', failed: '失败', error: '错误', incomplete: '验证未完成', skipped: '跳过' }[status] || status || '--')
const formatDateTime = value => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '--'
watch(id, fetchDetail, { immediate: true })
onBeforeUnmount(() => { requestVersion += 1 })
</script>

<style scoped>
.report-detail-page { min-height:100vh; padding:24px; background:#f5f7fa; }.state-wrap { min-height:240px; display:flex; justify-content:center; align-items:center; gap:8px; color:#606266; }.report-card { max-width:960px; margin:0 auto; padding:24px; background:#fff; border-radius:12px; box-shadow:0 2px 12px rgb(0 0 0 / 8%); }.report-header { display:flex; justify-content:space-between; align-items:start; gap:16px; padding-bottom:16px; border-bottom:1px solid #ebeef5; }.report-header h1 { margin:0; font-size:20px; }.report-header p { margin:6px 0 0; color:#606266; }section { margin-top:28px; }h2 { margin:0 0 12px; font-size:16px; }.overview-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }.overview-cards div { padding:10px 14px; background:#f8f9fa; border-radius:8px; }.overview-cards span { display:block; margin-bottom:4px; color:#909399; font-size:12px; }.overview-cards b { font-size:14px; }.error-block { padding:12px; margin-top:16px; color:#b42318; background:#fef0f0; border-radius:8px; }.error-block h3 { margin:0 0 8px; font-size:13px; }.error-block pre,.log-pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }.muted { color:#909399; }.report-links { display:flex; flex-wrap:wrap; gap:10px; }.native-report-link { display:inline-block; padding:8px 12px; color:#409eff; text-decoration:none; border:1px solid #b3d8ff; border-radius:4px; background:#ecf5ff; }.native-report-link:hover { background:#d9ecff; }
</style>
