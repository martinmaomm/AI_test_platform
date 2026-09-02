<template>
  <div v-if="trace && Object.keys(trace).length" class="trace-content">
    <div class="trace-section"><h5>连续页面状态</h5><el-tag v-for="path in observedPaths" :key="path" class="path-tag" effect="plain">{{ path }}</el-tag><p>最后位置：{{ trace.last_location || '未记录' }}</p><p>累计工具调用：{{ stats.total_tool_calls || 0 }}，失败：{{ stats.failed_tool_calls || 0 }}，耗时：{{ stats.duration_seconds || 0 }} 秒</p></div>
    <div class="trace-section"><h5>连续成功动作</h5><el-table :data="successfulEvents" size="small" max-height="280"><el-table-column prop="event_id" label="事件" width="94" /><el-table-column label="阶段" width="90"><template #default="{ row }">{{ phaseLabel(row.phase) }}</template></el-table-column><el-table-column prop="action" label="动作" width="90" /><el-table-column prop="relative_path" label="相对路径" min-width="140" /><el-table-column label="定位/输入" min-width="240"><template #default="{ row }">{{ eventSummary(row) }}</template></el-table-column></el-table></div>
    <div class="trace-section"><h5>失败或拦截尝试</h5><el-table :data="failedEvents" size="small" max-height="220"><el-table-column prop="event_id" label="事件" width="94" /><el-table-column label="阶段" width="90"><template #default="{ row }">{{ phaseLabel(row.phase) }}</template></el-table-column><el-table-column prop="tool_name" label="工具" min-width="150" /><el-table-column prop="status" label="结果" width="90"><template #default="{ row }"><el-tag :type="row.status === 'blocked' ? 'warning' : 'danger'" size="small">{{ row.status === 'blocked' ? '已拦截' : '失败' }}</el-tag></template></el-table-column><el-table-column prop="result_excerpt" label="安全摘要" min-width="220" /></el-table></div>
    <div class="trace-section"><h5>定位器证据</h5><el-table :data="locatorEvidence" size="small" max-height="260"><el-table-column prop="evidence_id" label="证据" width="94" /><el-table-column prop="event_id" label="成功事件" width="100" /><el-table-column prop="strategy" label="策略" width="100" /><el-table-column prop="value" label="定位值" min-width="220" /><el-table-column prop="validation" label="质量" width="100" /></el-table></div>
    <div class="trace-section"><h5>语义断言证据</h5><el-table :data="assertionEvidence" size="small" max-height="240"><el-table-column prop="assertion_id" label="断言" width="88" /><el-table-column label="阶段" width="90"><template #default="{ row }">{{ phaseLabel(row.phase) }}</template></el-table-column><el-table-column prop="event_id" label="观察事件" width="110" /><el-table-column prop="kind" label="语义" min-width="150" /><el-table-column label="期望来源" min-width="180"><template #default="{ row }">{{ assertionExpected(row) }}</template></el-table-column></el-table></div>
    <div class="trace-section"><h5>页面观察与截图</h5><el-collapse><el-collapse-item v-for="event in observedEvents" :key="event.event_id" :title="event.event_id + ' ' + (event.relative_path || '页面观察')"><p>{{ event.result_excerpt || '无可展示摘要' }}</p><p v-if="event.screenshot_path">截图：{{ event.screenshot_path }}</p></el-collapse-item></el-collapse></div>
    <div class="trace-section"><h5>探索完成度与清理风险</h5><el-alert v-if="incompleteEvidence" type="warning" :closable="false" show-icon title="探索证据不完整：草稿仅包含已验证的成功 callback，不代表系统失败。" /><p v-if="trace.termination_reason">提前结束原因：{{ trace.termination_reason }}</p><p><el-tag :type="cleanupPresentation.type" size="small">{{ cleanupPresentation.label }}</el-tag></p><p v-if="cleanupActionIds.length">清理动作证据：{{ cleanupActionIds.join('、') }}</p><p v-if="cleanupVerificationIds.length">清理确认证据：{{ cleanupVerificationIds.join('、') }}</p><p v-if="cleanupPresentation.reason">清理说明：{{ cleanupPresentation.reason }}</p><el-alert v-if="cleanupNeedsAttention" type="warning" :closable="false" show-icon title="清理动作不等于清理完成；缺少后续页面观察证据时必须人工检查。" /></div>
  </div>
  <el-empty v-else description="尚无探索轨迹" :image-size="56" />
</template>

<script setup>
import { computed } from 'vue'
import { explorationCleanupPresentation } from '@/composables/webUIScriptGenerationPresentation'
const props = defineProps({ snapshot: { type: Object, default: () => ({}) }, toolStats: { type: Object, default: () => ({}) } })
const trace = computed(() => props.snapshot?.schema_version === 4 ? props.snapshot : null)
const events = computed(() => Array.isArray(trace.value?.events) ? trace.value.events : [])
const replayIds = computed(() => new Set(trace.value?.replay_event_ids || []))
const cleanupActionIds = computed(() => Array.isArray(trace.value?.cleanup_event_ids) ? trace.value.cleanup_event_ids : [])
const cleanupVerificationIds = computed(() => Array.isArray(trace.value?.cleanup_verification_event_ids) ? trace.value.cleanup_verification_event_ids : [])
const executableIds = computed(() => new Set([...replayIds.value, ...cleanupActionIds.value]))
const successfulEvents = computed(() => events.value.filter(item => item.status === 'succeeded' && executableIds.value.has(item.event_id)))
const failedEvents = computed(() => events.value.filter(item => item.status !== 'succeeded'))
const observedEvents = computed(() => events.value.filter(item => ['observe', 'screenshot'].includes(item.action) && item.status === 'succeeded'))
const locatorEvidence = computed(() => Array.isArray(trace.value?.locator_evidence) ? trace.value.locator_evidence : [])
const assertionEvidence = computed(() => Array.isArray(trace.value?.assertion_evidence) ? trace.value.assertion_evidence : [])
const observedPaths = computed(() => [...new Set(events.value.map(item => item.relative_path).filter(Boolean))])
const stats = computed(() => trace.value?.tool_stats || props.toolStats || {})
const cleanupPresentation = computed(() => explorationCleanupPresentation(trace.value || {}))
const cleanupNeedsAttention = computed(() => ['unknown', 'attempted', 'missing'].includes(cleanupPresentation.value.status))
const incompleteEvidence = computed(() => !replayIds.value.size || !trace.value?.assertion_event_ids?.length || trace.value?.warnings?.length)
const eventSummary = (event) => Object.entries(event?.locator_input || {}).map(([key, value]) => key + ': ' + value).concat(event?.input_refs || []).filter(Boolean).join('；') || '—'
const phaseLabel = (phase) => ({ exploration: '探索', main: '主场景', assertion: '断言', cleanup: '清理' })[phase] || '未标记'
const assertionExpected = (item) => item?.input_ref ? `运行时变量 ${item.input_ref}` : (item?.literal || '可见性')
</script>

<style scoped>
.trace-content { display: grid; gap: 18px; }.trace-section { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; }.trace-section h5 { margin: 0 0 10px; color: var(--app-text-primary); font-size: 14px; }.trace-section p { margin: 6px 0; color: var(--app-text-secondary); font-size: 13px; }.path-tag { margin: 0 8px 8px 0; }
</style>
