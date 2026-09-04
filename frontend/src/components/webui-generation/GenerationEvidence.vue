<template>
  <div v-if="trace && Object.keys(trace).length" class="trace-content">
    <div class="trace-section">
      <h5>最新探索状态</h5>
      <el-tag v-for="path in observedPaths" :key="path" class="path-tag" effect="plain">{{ path }}</el-tag>
      <p>累计工具调用：{{ stats.total_tool_calls || 0 }}，失败：{{ stats.failed_tool_calls || 0 }}，耗时：{{ stats.duration_seconds || 0 }} 秒</p>
      <p v-if="trace.final_message">最新消息：{{ trace.final_message }}</p>
    </div>

    <div class="trace-section">
      <h5>草稿完整性</h5>
      <el-alert v-if="artifact.completion !== 'complete'" type="warning" :closable="false" show-icon :title="completionTitle" />
      <p v-else>智能体已标记草稿完成；请检查下面的探索证据，这不代表脚本已实际调试通过。</p>
      <p>草稿版本：{{ artifact.revision ?? '未记录' }}</p>
      <div v-if="completedSteps.length"><strong>已完成步骤</strong><ul><li v-for="step in completedSteps" :key="stepKey(step)">{{ stepText(step) }}</li></ul></div>
      <div v-if="remainingSteps.length"><strong>剩余步骤</strong><ul><li v-for="step in remainingSteps" :key="stepKey(step)">{{ stepText(step) }}</li></ul></div>
    </div>

    <div v-if="failureReason" class="trace-section">
      <h5>实际终止原因</h5>
      <p>{{ failureReason }}</p>
    </div>

    <div class="trace-section">
      <h5>页面状态</h5>
      <el-table :data="pageStates" size="small" max-height="240" empty-text="尚无页面状态">
        <el-table-column label="位置" min-width="180"><template #default="{ row }">{{ pageLocation(row) }}</template></el-table-column>
        <el-table-column label="页面摘要" min-width="260"><template #default="{ row }">{{ pageSummary(row) }}</template></el-table-column>
      </el-table>
    </div>

    <div class="trace-section">
      <h5>已记录动作</h5>
      <el-table :data="successfulEvents" size="small" max-height="280" empty-text="尚无已完成动作">
        <el-table-column prop="event_id" label="事件" width="100" /><el-table-column prop="action" label="动作" width="100" /><el-table-column label="位置" min-width="160"><template #default="{ row }">{{ eventLocation(row) }}</template></el-table-column><el-table-column label="摘要" min-width="240"><template #default="{ row }">{{ eventSummary(row) }}</template></el-table-column>
      </el-table>
    </div>

    <div v-if="failedEvents.length" class="trace-section">
      <h5>失败或拦截动作</h5>
      <el-table :data="failedEvents" size="small" max-height="220"><el-table-column prop="event_id" label="事件" width="100" /><el-table-column prop="tool_name" label="工具" min-width="130" /><el-table-column prop="status" label="结果" width="100" /><el-table-column label="原因" min-width="250"><template #default="{ row }">{{ eventSummary(row) }}</template></el-table-column></el-table>
    </div>

    <div class="trace-section">
      <h5>定位器证据</h5>
      <el-table :data="locatorEvidence" size="small" max-height="260" empty-text="尚无定位器证据"><el-table-column prop="evidence_id" label="证据" width="100" /><el-table-column prop="event_id" label="事件" width="100" /><el-table-column prop="strategy" label="策略" width="110" /><el-table-column prop="value" label="定位值" min-width="220" /><el-table-column prop="validation" label="校验" width="100" /></el-table>
    </div>

    <div v-if="variables.length" class="trace-section">
      <h5>变量定义</h5>
      <el-table :data="variables" size="small" max-height="220"><el-table-column label="变量" min-width="160"><template #default="{ row }">{{ variableName(row) }}</template></el-table-column><el-table-column label="说明" min-width="240"><template #default="{ row }">{{ variableDescription(row) }}</template></el-table-column><el-table-column label="必填" width="80"><template #default="{ row }">{{ variableRequired(row) ? '是' : '否' }}</template></el-table-column></el-table>
    </div>

    <div v-if="history.length" class="trace-section">
      <h5>草稿历史</h5>
      <el-table :data="history" size="small" max-height="220"><el-table-column prop="revision" label="版本" width="90" /><el-table-column label="完成度" width="120"><template #default="{ row }">{{ row.artifact?.completion === 'complete' ? '草稿完成' : '待补充' }}</template></el-table-column><el-table-column label="剩余工作" min-width="280"><template #default="{ row }">{{ (row.artifact?.remaining_steps || []).join('；') || '未记录待补充项' }}</template></el-table-column></el-table>
    </div>
  </div>
  <el-empty v-else description="尚无 schema v5 探索轨迹" :image-size="56" />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  snapshot: { type: Object, default: () => ({}) },
  toolStats: { type: Object, default: () => ({}) },
  failureMessage: { type: String, default: '' }
})
const trace = computed(() => props.snapshot?.schema_version === 5 ? props.snapshot : null)
const artifact = computed(() => trace.value?.artifact && typeof trace.value.artifact === 'object' ? trace.value.artifact : {})
const events = computed(() => Array.isArray(trace.value?.events) ? trace.value.events : [])
const pageStates = computed(() => Array.isArray(trace.value?.page_states) ? trace.value.page_states : [])
const successfulEvents = computed(() => events.value.filter(item => item?.status === 'succeeded'))
const failedEvents = computed(() => events.value.filter(item => item?.status && item.status !== 'succeeded'))
const locatorEvidence = computed(() => Array.isArray(trace.value?.locator_evidence) ? trace.value.locator_evidence : [])
const variables = computed(() => Array.isArray(artifact.value.variables) ? artifact.value.variables : [])
const history = computed(() => Array.isArray(artifact.value.history)
  ? artifact.value.history
  : (Array.isArray(trace.value?.artifact_history) ? trace.value.artifact_history : []))
const completedSteps = computed(() => Array.isArray(artifact.value.completed_steps) ? artifact.value.completed_steps : [])
const remainingSteps = computed(() => Array.isArray(artifact.value.remaining_steps) ? artifact.value.remaining_steps : [])
const observedPaths = computed(() => [...new Set(pageStates.value.map(pageLocation).filter(Boolean))])
const stats = computed(() => trace.value?.tool_stats || props.toolStats || {})
const failureReason = computed(() => props.failureMessage || '')
const completionTitle = computed(() => artifact.value.completion === 'partial'
  ? '草稿未完成：仅可将已记录证据整理为可编辑草稿，不能视为测试通过。'
  : '草稿完整性未知：请以已完成步骤、剩余步骤和终止原因为准。')
const pageLocation = item => item?.relative_path || item?.path || item?.url || item?.location || '未记录'
const pageSummary = item => item?.excerpt || item?.summary || item?.title || item?.result_excerpt || item?.message || '—'
const eventLocation = item => item?.relative_path || item?.path || item?.url || '—'
const eventSummary = (item) => item?.result_excerpt || item?.message || item?.error_message || item?.summary || Object.entries(item?.locator_input || {}).map(([key, value]) => `${key}: ${value}`).join('；') || '—'
const stepText = step => typeof step === 'string' ? step : step?.title || step?.name || step?.id || '未命名步骤'
const stepKey = step => typeof step === 'string' ? step : JSON.stringify(step)
const variableName = item => typeof item === 'string' ? item : item?.name || '—'
const variableDescription = item => typeof item === 'string' ? '' : item?.description || '—'
const variableRequired = item => typeof item === 'object' && Boolean(item?.required)
</script>

<style scoped>
.trace-content { display: grid; gap: 18px; }.trace-section { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; }.trace-section h5 { margin: 0 0 10px; color: var(--app-text-primary); font-size: 14px; }.trace-section p, .trace-section strong { color: var(--app-text-secondary); font-size: 13px; }.trace-section p { margin: 6px 0; }.trace-section ul { margin: 8px 0 0; padding-left: 20px; color: var(--app-text-primary); font-size: 13px; line-height: 1.7; }.path-tag { margin: 0 8px 8px 0; }
</style>
