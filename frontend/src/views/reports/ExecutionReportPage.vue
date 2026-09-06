<template>
  <main class="execution-report-page">
    <div v-if="loading" class="state-wrap"><el-icon class="is-loading"><Loading /></el-icon>正在加载报告…</div>
    <el-result
      v-else-if="error"
      :icon="isNotFound ? 'warning' : 'error'"
      :title="isNotFound ? '报告不存在或无权访问' : '报告加载失败'"
      :sub-title="error"
    >
      <template #extra><el-button type="primary" @click="loadReport">重试</el-button></template>
    </el-result>
    <section v-else-if="execution" class="report-shell">
      <header class="report-header">
        <div>
          <p class="report-eyebrow">{{ kind === 'web' ? 'WebUI' : 'API' }} 原生执行报告</p>
          <h1>{{ execution.name || execution.test_suite_name || execution.test_case_title || '未命名执行' }}</h1>
          <p>执行记录 #{{ executionId }} · {{ statusText(execution.status) }}</p>
        </div>
        <el-button @click="copyReportLink">复制报告链接</el-button>
      </header>
      <WebUITestSuiteExecutionDetail
        v-if="kind === 'web' && execution.exec_type === 'suite'"
        :execution="execution"
      />
      <WebUITestCaseExecutionDetail
        v-else-if="kind === 'web'"
        :execution="execution"
      />
      <APITestSuiteExecutionDetai
        v-else-if="execution.exec_type === 'suite'"
        :result="execution"
      />
      <APITestCaseExecutionDetail v-else :result="execution" />
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { getWebUITestExecutionReport } from '@/api/webTesting'
import { getAPITestExecutionReport } from '@/api/apiTesting'
import WebUITestSuiteExecutionDetail from '@/components/WebUITestSuiteExecutionDetail.vue'
import WebUITestCaseExecutionDetail from '@/components/WebUITestCaseExecutionDetail.vue'
import APITestSuiteExecutionDetai from '@/components/APITestSuiteExecutionDetai.vue'
import APITestCaseExecutionDetail from '@/components/APITestCaseExecutionDetail.vue'
import { copyText, reportUrl } from '@/utils/reportLinks'

const props = defineProps({ kind: { type: String, required: true } })
const route = useRoute()
const loading = ref(false)
const error = ref('')
const isNotFound = ref(false)
const execution = ref(null)

const projectId = computed(() => route.params.projectId)
const executionId = computed(() => route.params.executionId)
let requestVersion = 0

const normalizeReport = (payload) => {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  return ['case', 'scenario', 'suite'].includes(payload.exec_type) ? payload : null
}

const loadReport = async () => {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  isNotFound.value = false
  execution.value = null
  try {
    const getExecutionReport = props.kind === 'web' ? getWebUITestExecutionReport : getAPITestExecutionReport
    const response = await getExecutionReport(projectId.value, executionId.value)
    if (version !== requestVersion) return
    if (!response?.success) throw new Error(response?.message || '获取执行报告失败')
    const normalized = normalizeReport(response.data)
    if (!normalized?.exec_type) throw new Error('执行报告缺少执行类型')
    execution.value = normalized
  } catch (requestError) {
    if (version !== requestVersion) return
    const status = requestError?.response?.status
    isNotFound.value = status === 404 || status === 403
    error.value = requestError?.response?.data?.message || requestError?.response?.data?.detail || requestError?.message || '请求报告失败，请重试。'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

const copyReportLink = async () => {
  try {
    await copyText(reportUrl(props.kind, projectId.value, executionId.value))
    ElMessage.success('报告链接已复制')
  } catch {
    ElMessage.error('复制失败，请从地址栏手动复制链接')
  }
}

const statusText = status => ({ passed: '通过', failed: '失败', error: '错误', incomplete: '验证未完成', skipped: '跳过', running: '执行中', pending: '待执行', stopped: '已停止' }[status] || status || '未知')

watch(() => [props.kind, projectId.value, executionId.value], loadReport, { immediate: true })
onBeforeUnmount(() => { requestVersion += 1 })
</script>

<style scoped>
.execution-report-page { min-height: 100vh; padding: 24px; background: #f5f7fa; }
.state-wrap { display: flex; justify-content: center; align-items: center; gap: 8px; min-height: 240px; color: #606266; }
.report-shell { max-width: 1180px; margin: 0 auto; border-radius: 12px; background: #fff; box-shadow: 0 2px 12px rgb(0 0 0 / 8%); overflow: hidden; }
.report-header { display: flex; justify-content: space-between; gap: 16px; align-items: start; padding: 24px; border-bottom: 1px solid #ebeef5; }
.report-eyebrow { margin: 0 0 6px; color: #409eff; font-size: 13px; font-weight: 600; }
h1 { margin: 0; font-size: 22px; } .report-header p:last-child { margin: 8px 0 0; color: #606266; }
</style>
