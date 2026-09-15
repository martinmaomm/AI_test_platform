<template>
  <div class="native-report">
    <section class="summary-grid">
      <article class="summary-card">
        <h3>执行信息</h3>
        <dl>
          <div><dt>执行记录</dt><dd>#{{ execution.execution || execution.id }}</dd></div>
          <div><dt>测试套件</dt><dd>{{ execution.test_suite_name || execution.name || '未命名套件' }}</dd></div>
          <div><dt>开始时间</dt><dd>{{ formatTime(execution.start_time) }}</dd></div>
          <div><dt>执行时长</dt><dd>{{ formatDuration(execution.duration) }}</dd></div>
          <div><dt>通过率</dt><dd>{{ execution.pass_rate || 0 }}%</dd></div>
          <div v-if="actualUrl"><dt>实际访问地址</dt><dd class="url">{{ actualUrl }}</dd></div>
        </dl>
      </article>
      <article class="summary-card result-card">
        <h3>执行汇总</h3>
        <div class="status-counts">
          <span>总数 <b>{{ execution.total_cases || 0 }}</b></span>
          <span class="passed">通过 <b>{{ execution.passed_cases || 0 }}</b></span>
          <span class="incomplete">验证未完成 <b>{{ execution.incomplete_cases || 0 }}</b></span>
          <span class="failed">失败 <b>{{ execution.failed_cases || 0 }}</b></span>
          <span>跳过 <b>{{ execution.skipped_cases || 0 }}</b></span>
          <span v-if="execution.not_executed_cases">未执行 <b>{{ execution.not_executed_cases }}</b></span>
        </div>
      </article>
    </section>

    <section v-if="execution.error_message" class="failure-summary">
      <h3>失败摘要</h3><pre>{{ execution.error_message }}</pre>
    </section>

    <section class="case-section">
      <header class="section-header">
        <div><h3>按执行顺序的子用例结果</h3><p>“验证未完成”和“跳过”不计为通过。</p></div>
        <div class="actions"><el-switch v-model="onlyFailures" active-text="只看失败" /><el-button v-if="casesError" size="small" @click="loadCases">重试</el-button></div>
      </header>
      <div v-if="casesLoading" class="state"><el-icon class="is-loading"><Loading /></el-icon>正在加载子用例结果…</div>
      <el-alert v-else-if="casesError" type="error" :title="casesError" show-icon :closable="false" />
      <el-empty v-else-if="!caseExecutions.length" description="该次套件执行未返回子用例结果" :image-size="70" />
      <el-empty v-else-if="!filteredCases.length" description="没有失败或错误的子用例" :image-size="70" />
      <el-collapse v-else v-model="expandedCases" class="case-list">
        <el-collapse-item v-for="caseItem in filteredCases" :key="caseItem.id" :name="String(caseItem.id)">
          <template #title>
            <div class="case-title"><span>{{ caseItem.test_case_title || caseItem.name || '未命名用例' }}</span><el-tag :type="statusType(caseItem.status)" size="small">{{ caseItem.status_display || statusText(caseItem.status) }}</el-tag><small>{{ formatDuration(caseItem.duration) }}</small><el-button v-if="canRepairCase(caseItem)" size="small" type="danger" plain @click.stop="toggleRepairCase(caseItem)">AI 修复</el-button></div>
          </template>
          <pre v-if="caseItem.error_message" class="case-error">{{ caseItem.error_message }}</pre>
          <el-alert v-if="!publicReport && ['failed', 'error'].includes(caseItem.status) && caseItem.repair_availability?.available === false" :title="caseItem.repair_availability.reason" type="info" :closable="false" show-icon />
          <WebUIScriptAssistantPanel
            v-if="selectedRepairCaseId === String(caseItem.id) && canRepairCase(caseItem)"
            :ref="element => setRepairPanelRef(caseItem.id, element)"
            :project-id="execution.project_id"
            :repair-context="{ executionId: execution.execution || execution.id, suiteCaseId: caseItem.id }"
          />
          <WebUIExecutionScreenshot v-if="expandedCases.includes(String(caseItem.id))" :project-id="execution.project_id" :execution-id="execution.execution || execution.id" :case-execution-id="caseItem.id" :screenshot-path="caseItem.screenshot_path || ''" :status="caseItem.status" :public-report="publicReport" />
          <el-collapse v-if="caseLog(caseItem)" class="raw-log"><el-collapse-item title="查看原始 stdout / stderr / log" name="log"><pre>{{ caseLog(caseItem) }}</pre></el-collapse-item></el-collapse>
        </el-collapse-item>
      </el-collapse>
    </section>

    <section class="raw-log suite-log">
      <header class="section-header"><h3>套件原始日志</h3><el-button size="small" @click="copyLogs">复制</el-button></header>
      <el-collapse><el-collapse-item title="查看原始 stdout / stderr / log" name="suite-log"><pre>{{ execution.log || '暂无技术日志' }}</pre></el-collapse-item></el-collapse>
    </section>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { getTestExecutionCases } from '@/api/webTesting'
import WebUIExecutionScreenshot from '@/components/WebUIExecutionScreenshot.vue'
import { copyText } from '@/utils/reportLinks'
import { expandedAssistantRowIds } from '@/composables/webUIScriptAssistantPresentation'

const WebUIScriptAssistantPanel = defineAsyncComponent(() => import('@/components/WebUIScriptAssistantPanel.vue'))

const props = defineProps({
  execution: { type: Object, required: true, default: () => ({}) },
  publicReport: { type: Boolean, default: false }
})
const caseExecutions = ref([])
const casesLoading = ref(false)
const casesError = ref('')
const onlyFailures = ref(false)
const expandedCases = ref([])
const selectedRepairCaseId = ref(null)
const repairPanelRefs = new Map()
let requestVersion = 0

const filteredCases = computed(() => onlyFailures.value ? caseExecutions.value.filter(item => ['failed', 'error'].includes(item.status)) : caseExecutions.value)
const actualUrl = computed(() => props.execution?.diagnostics?.actual_url || props.execution?.actual_url || '')
const canRepairCase = item => !props.publicReport && item?.repair_availability?.available === true && ['failed', 'error'].includes(item?.status) && Boolean(props.execution?.project_id && (props.execution?.execution || props.execution?.id) && item?.id)
const setRepairPanelRef = (id, element) => {
  const key = String(id)
  if (element) repairPanelRefs.set(key, element)
  else repairPanelRefs.delete(key)
}
const toggleRepairCase = async item => {
  const id = String(item.id)
  const opening = selectedRepairCaseId.value !== id
  selectedRepairCaseId.value = opening ? id : null
  if (!opening) return
  expandedCases.value = expandedAssistantRowIds(expandedCases.value, id, true)
  await nextTick(() => repairPanelRefs.get(id)?.$el?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}

const loadCases = async () => {
  const version = ++requestVersion
  const projectId = props.execution?.project_id
  const executionId = props.execution?.execution || props.execution?.id
  caseExecutions.value = []
  expandedCases.value = []
  selectedRepairCaseId.value = null
  casesError.value = ''
  casesLoading.value = false
  if (props.publicReport) {
    const embeddedCases = props.execution?.case_executions
    if (!Array.isArray(embeddedCases)) {
      casesError.value = '公开报告缺少子用例结果。'
      return
    }
    caseExecutions.value = embeddedCases.map(item => ({
      ...item,
      test_case_id: item?.test_case_id ?? item?.test_case ?? null,
      test_case_description: item?.test_case_description ?? item?.description ?? '',
      test_case_module: item?.test_case_module ?? item?.module_name ?? null
    }))
    return
  }
  if (!projectId || !executionId) {
    casesError.value = '缺少执行记录信息，无法加载子用例结果。'
    return
  }
  casesLoading.value = true
  try {
    const response = await getTestExecutionCases(projectId, executionId)
    if (version !== requestVersion) return
    if (!response?.success) throw new Error(response?.message || '加载子用例结果失败')
    const cases = response.data?.cases ?? response.data
    if (!Array.isArray(cases)) throw new Error('子用例结果格式无效')
    caseExecutions.value = cases
  } catch (error) {
    if (version === requestVersion) casesError.value = error?.response?.data?.message || error?.message || '加载子用例结果失败，请重试。'
  } finally {
    if (version === requestVersion) casesLoading.value = false
  }
}

const caseLog = item => [item.stdout && `--- stdout ---\n${item.stdout}`, item.stderr && `--- stderr ---\n${item.stderr}`, item.log && `--- log ---\n${item.log}`].filter(Boolean).join('\n\n')
const formatTime = value => value ? new Date(value).toLocaleString() : '--'
const formatDuration = value => typeof value === 'number' ? `${value.toFixed(2)}s` : (value || '0s')
const statusType = status => ({ passed: 'success', incomplete: 'warning', failed: 'danger', error: 'danger', running: 'warning', pending: 'info', skipped: 'info', stopped: 'warning' }[status] || 'info')
const statusText = status => ({ passed: '通过', incomplete: '验证未完成', failed: '失败', error: '错误', running: '执行中', pending: '待执行', skipped: '跳过', stopped: '已停止' }[status] || status || '未知')
const copyLogs = async () => { try { await copyText(props.execution.log || '暂无技术日志'); ElMessage.success('日志已复制') } catch { ElMessage.error('日志复制失败') } }

watch(() => [props.execution?.project_id, props.execution?.execution || props.execution?.id, props.execution?.case_executions, props.publicReport], loadCases, { immediate: true })
onBeforeUnmount(() => { requestVersion += 1 })
</script>

<style scoped>
.native-report { padding: 24px; background: #fafbfc; color: #303133; }.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }.summary-card,.case-section,.raw-log,.failure-summary { padding: 18px; background: #fff; border: 1px solid #ebeef5; border-radius: 8px; margin-bottom: 16px; }.summary-card h3,.case-section h3,.raw-log h3,.failure-summary h3 { margin: 0 0 12px; font-size: 16px; }dl { margin: 0; }dl div { display:flex; justify-content:space-between; gap:16px; padding:7px 0; border-bottom:1px solid #f2f4f7; }dt { color:#909399; }dd { margin:0; text-align:right; overflow-wrap:anywhere; }.url { max-width:70%; }.status-counts { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }.status-counts span { padding:10px; background:#f5f7fa; border-radius:6px; }.status-counts b { display:block; font-size:20px; }.passed b { color:#67c23a; }.incomplete b { color:#e6a23c; }.failed b { color:#f56c6c; }.failure-summary,.case-error { background:#fef0f0; border-color:#fde2e2; color:#b42318; }.failure-summary pre,.case-error,.raw-log pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }.section-header { display:flex; justify-content:space-between; align-items:start; gap:16px; margin-bottom:12px; }.section-header p { margin:4px 0 0; color:#909399; font-size:13px; }.actions { display:flex; align-items:center; gap:10px; white-space:nowrap; }.state { display:flex; justify-content:center; gap:8px; padding:32px; color:#606266; }.case-list { border-top:1px solid #ebeef5; }.case-title { width:100%; display:flex; align-items:center; gap:10px; padding-right:12px; }.case-title span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.case-title small { color:#909399; }.case-error { padding:10px; margin-bottom:12px; border-radius:6px; }.raw-log { margin-top:12px; margin-bottom:0; padding:0; border:0; }.suite-log { margin-bottom:0; }
</style>

<style scoped>
.case-title > .el-tag { flex: 0 0 auto; }
</style>
