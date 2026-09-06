<template>
  <div class="api-suite-report">
    <section class="summary-grid">
      <article class="summary-card"><h3>执行信息</h3><dl>
        <div><dt>执行记录</dt><dd>#{{ result.execution || result.id }}</dd></div>
        <div><dt>测试套件</dt><dd>{{ result.test_suite_name || result.name || '未命名套件' }}</dd></div>
        <div><dt>环境</dt><dd>{{ environment }}</dd></div>
        <div><dt>开始时间</dt><dd>{{ formatTime(result.start_time || result.started_at) }}</dd></div>
        <div><dt>执行时长</dt><dd>{{ formatDuration(result.duration) }}</dd></div>
      </dl></article>
      <article class="summary-card"><h3>执行汇总</h3><div class="status-counts">
        <span>总数 <b>{{ result.total_cases || 0 }}</b></span><span class="passed">通过 <b>{{ result.passed_cases || 0 }}</b></span>
        <span class="failed">失败 <b>{{ result.failed_cases || 0 }}</b></span><span>跳过 <b>{{ result.skipped_cases || 0 }}</b></span>
      </div></article>
    </section>
    <section v-if="result.error_message" class="failure-summary"><h3>失败摘要</h3><pre>{{ result.error_message }}</pre></section>
    <section class="case-results"><h3>按执行顺序的子用例结果</h3><el-empty v-if="!caseExecutions.length" description="该次套件执行未返回子用例结果" :image-size="70" /><el-collapse v-else v-model="expandedCases">
      <el-collapse-item v-for="item in caseExecutions" :key="item.id" :name="String(item.id)"><template #title><div class="case-title"><span>{{ item.test_case_title || item.name || '未命名用例' }}</span><el-tag :type="statusType(item.status)" size="small">{{ statusText(item.status) }}</el-tag><small>{{ formatDuration(item.duration) }}</small></div></template>
        <APITestCaseExecutionDetail :result="item" />
      </el-collapse-item>
    </el-collapse></section>
    <section class="raw-log"><header><h3>原始执行日志</h3><el-button size="small" @click="copyLogs">复制</el-button></header>
      <el-collapse><el-collapse-item title="查看原始 stdout / stderr / log" name="technical"><pre>{{ logContent || '暂无技术日志' }}</pre></el-collapse-item></el-collapse>
    </section>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { copyText } from '@/utils/reportLinks'
import APITestCaseExecutionDetail from '@/components/APITestCaseExecutionDetail.vue'

const props = defineProps({ result: { type: Object, required: true, default: () => ({}) } })
const expandedCases = ref([])
const caseExecutions = computed(() => Array.isArray(props.result.case_executions) ? props.result.case_executions : [])
const environment = computed(() => props.result.environment_name && props.result.environment_base_url ? `${props.result.environment_name} (${props.result.environment_base_url})` : (props.result.environment_name || props.result.environment_base_url || '--'))
const logContent = computed(() => [props.result.stdout && `--- stdout ---\n${props.result.stdout}`, props.result.stderr && `--- stderr ---\n${props.result.stderr}`, props.result.log && `--- log ---\n${props.result.log}`].filter(Boolean).join('\n\n'))
const formatTime = value => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '--'
const formatDuration = value => typeof value === 'number' ? `${value.toFixed(2)}s` : (value || '0s')
const statusType = status => ({ passed: 'success', failed: 'danger', error: 'danger', incomplete: 'warning', skipped: 'info', running: 'warning', pending: 'info', stopped: 'warning' }[status] || 'info')
const statusText = status => ({ passed: '通过', failed: '失败', error: '错误', incomplete: '验证未完成', skipped: '跳过', running: '执行中', pending: '待执行', stopped: '已停止' }[status] || status || '未知')
const copyLogs = async () => { try { await copyText(logContent.value || '暂无技术日志'); ElMessage.success('日志已复制') } catch { ElMessage.error('日志复制失败') } }
</script>

<style scoped>
.api-suite-report { padding:24px; background:#fafbfc; color:#303133; }.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }.summary-card,.raw-log,.failure-summary,.case-results { padding:18px; background:#fff; border:1px solid #ebeef5; border-radius:8px; margin-bottom:16px; }.summary-card h3,.raw-log h3,.failure-summary h3,.case-results h3 { margin:0 0 12px; font-size:16px; }dl { margin:0; }dl div { display:flex; justify-content:space-between; gap:16px; padding:7px 0; border-bottom:1px solid #f2f4f7; }dt { color:#909399; }dd { margin:0; text-align:right; overflow-wrap:anywhere; }.status-counts { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }.status-counts span { padding:10px; background:#f5f7fa; border-radius:6px; }.status-counts b { display:block; font-size:20px; }.passed b { color:#67c23a; }.failed b { color:#f56c6c; }.failure-summary { background:#fef0f0; color:#b42318; border-color:#fde2e2; }.failure-summary pre,.raw-log pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }.case-title { display:flex; width:100%; align-items:center; gap:10px; padding-right:12px; }.case-title span { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }.case-title small { color:#909399; }.raw-log { margin-bottom:0; }.raw-log header { display:flex; justify-content:space-between; align-items:start; gap:16px; }
</style>

<style scoped>
.case-title > .el-tag { flex: 0 0 auto; }
</style>
