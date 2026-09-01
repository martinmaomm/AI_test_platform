<template>
  <div v-if="trace && Object.keys(trace).length" class="trace-content">
    <div class="trace-section"><h5>到达页面与最后位置</h5><el-tag v-for="path in observedPaths" :key="path" class="path-tag" effect="plain">{{ path }}</el-tag><p>最后位置：{{ lastLocation || '未记录' }}</p></div>
    <div class="trace-section"><h5>目标执行结果</h5><el-table :data="goalRuns" size="small" max-height="240"><el-table-column prop="goal_id" label="目标" width="90" /><el-table-column prop="status" label="状态" width="110"><template #default="{ row }"><el-tag :type="goalStatusType(row.status)" size="small">{{ goalStatusLabel(row.status) }}</el-tag></template></el-table-column><el-table-column label="已选事件" width="100"><template #default="{ row }">{{ row.selected_event_ids?.length || 0 }}</template></el-table-column><el-table-column prop="reason" label="说明" min-width="240" /></el-table></div>
    <div class="trace-section"><h5>可回放成功动作</h5><el-table :data="successfulEvents" size="small" max-height="280"><el-table-column prop="event_id" label="事件" width="94" /><el-table-column prop="goal_id" label="目标" width="80" /><el-table-column prop="action" label="动作" width="90" /><el-table-column prop="relative_path" label="相对路径" min-width="140" /><el-table-column label="定位/输入" min-width="240"><template #default="{ row }">{{ eventSummary(row) }}</template></el-table-column></el-table></div>
    <div class="trace-section"><h5>定位器证据</h5><el-table :data="locatorEvidence" size="small" max-height="260"><el-table-column prop="evidence_id" label="证据" width="94" /><el-table-column prop="goal_id" label="目标" width="80" /><el-table-column prop="strategy" label="策略" width="100" /><el-table-column prop="value" label="定位值" min-width="220" /><el-table-column prop="validation" label="质量" width="100" /></el-table></div>
    <div class="trace-section"><h5>失败或拦截尝试</h5><el-table :data="failedEvents" size="small" max-height="220"><el-table-column prop="event_id" label="事件" width="94" /><el-table-column prop="goal_id" label="目标" width="80" /><el-table-column prop="tool_name" label="工具" min-width="150" /><el-table-column prop="status" label="结果" width="90"><template #default="{ row }"><el-tag :type="row.status === 'blocked' ? 'warning' : 'danger'" size="small">{{ row.status === 'blocked' ? '已拦截' : '失败' }}</el-tag></template></el-table-column><el-table-column prop="result_excerpt" label="安全摘要" min-width="220" /></el-table></div>
    <div class="trace-section"><h5>页面观察与截图</h5><el-collapse><el-collapse-item v-for="event in observedEvents" :key="event.event_id" :title="`${event.event_id || event.sequence} ${event.relative_path || '页面观察'}`"><p>{{ event.result_excerpt || '无可展示摘要' }}</p><p v-if="event.screenshot_path">截图：{{ event.screenshot_path }}</p></el-collapse-item></el-collapse></div>
    <div class="trace-section"><h5>提前结束与清理</h5><p v-if="trace.termination_reason">提前结束原因：{{ trace.termination_reason }}</p><el-alert v-if="cleanupNeedsAttention" type="warning" :closable="false" show-icon title="可能存在未清理测试数据或结果未知；平台不会自动重放探索。" /></div>
    <div class="trace-section stats"><h5>工具统计</h5><span>调用 {{ stats.total_tool_calls || 0 }}</span><span>失败 {{ stats.failed_tool_calls || 0 }}</span><span>耗时 {{ stats.duration_seconds || 0 }} 秒</span></div>
  </div>
  <el-empty v-else description="尚无探索轨迹" :image-size="56" />
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ snapshot: { type: Object, default: () => ({}) }, toolStats: { type: Object, default: () => ({}) } })
const trace = computed(() => props.snapshot?.schema_version === 3 ? props.snapshot : null)
const events = computed(() => Array.isArray(trace.value?.events) ? trace.value.events : [])
const goalRuns = computed(() => Array.isArray(trace.value?.goal_runs) ? trace.value.goal_runs : [])
const selectedEventIds = computed(() => new Set(goalRuns.value.flatMap(item => item.selected_event_ids || [])))
const successfulEvents = computed(() => events.value.filter(item => item.status === 'succeeded' && selectedEventIds.value.has(item.event_id)))
const failedEvents = computed(() => events.value.filter(item => item.status !== 'succeeded'))
const observedEvents = computed(() => events.value.filter(item => ['observe', 'screenshot', 'navigate'].includes(item.action) && item.status === 'succeeded'))
const locatorEvidence = computed(() => Array.isArray(trace.value?.locator_evidence) ? trace.value.locator_evidence : [])
const observedPaths = computed(() => [...new Set(events.value.map(item => item.relative_path).filter(Boolean))])
const lastLocation = computed(() => [...events.value].reverse().find(item => item.relative_path)?.relative_path || '')
const stats = computed(() => trace.value?.tool_stats || props.toolStats || {})
const cleanupNeedsAttention = computed(() => ['unknown', 'residual', 'not_attempted'].includes(trace.value?.cleanup?.status))
const eventSummary = (event) => Object.entries(event?.locator_input || {}).map(([key, value]) => `${key}: ${value}`).concat(event?.input_refs || []).filter(Boolean).join('；') || '—'
const goalStatusLabel = (status) => ({ completed: '已完成', blocked: '已阻断', uncertain: '待检查' })[status] || '未开始'
const goalStatusType = (status) => ({ completed: 'success', blocked: 'danger', uncertain: 'warning' })[status] || 'info'
</script>

<style scoped>
.trace-content { display: grid; gap: 18px; }.trace-section { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; }.trace-section h5 { margin: 0 0 10px; color: var(--app-text-primary); font-size: 14px; }.trace-section p { margin: 6px 0; color: var(--app-text-secondary); font-size: 13px; }.path-tag { margin: 0 8px 8px 0; }.stats { display: flex; flex-wrap: wrap; gap: 12px; color: var(--app-text-secondary); font-size: 13px; }
</style>
