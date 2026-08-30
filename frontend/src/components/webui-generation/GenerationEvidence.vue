<template>
  <div v-if="snapshot && Object.keys(snapshot).length" class="evidence-content">
    <div class="evidence-section"><h5>访问路径</h5><el-tag v-for="path in snapshot.visited_paths || []" :key="path" class="path-tag" effect="plain">{{ path }}</el-tag><span v-if="!(snapshot.visited_paths || []).length" class="muted">尚无页面路径</span></div>
    <div class="evidence-section"><h5>页面与关键区域</h5><el-table :data="snapshot.page_states || []" size="small" max-height="240"><el-table-column prop="name" label="页面" min-width="130" /><el-table-column prop="path" label="路径" min-width="150" /><el-table-column label="关键区域" min-width="180"><template #default="{ row }">{{ (row.key_regions || []).join('、') || '—' }}</template></el-table-column></el-table></div>
    <div class="evidence-section"><h5>关键元素</h5><el-collapse><el-collapse-item v-for="(element, index) in snapshot.elements || []" :key="`${element.page_name}-${element.visible_name}-${index}`" :title="`${element.page_name || '页面'} · ${element.visible_name || '未命名元素'}`"><p>角色：{{ element.role || '未识别' }}</p><p v-if="Object.keys(element.stable_attributes || {}).length">稳定属性：{{ attributeText(element.stable_attributes) }}</p><div v-if="element.candidate_locators?.length"><span class="muted">候选定位器</span><pre v-for="locator in element.candidate_locators" :key="locator">{{ locator }}</pre></div></el-collapse-item></el-collapse></div>
    <div class="evidence-section"><h5>场景步骤证据</h5><el-table :data="evidenceRows" size="small" max-height="260"><el-table-column prop="stepId" label="步骤" width="86" /><el-table-column prop="status" label="证据状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'confirmed' ? 'success' : row.status === 'partial' || row.status === 'partially_confirmed' ? 'warning' : 'danger'" size="small">{{ statusText(row.status) }}</el-tag></template></el-table-column><el-table-column prop="reason" label="说明" min-width="220" /></el-table></div>
    <el-alert v-if="snapshot.unresolved_steps?.length" type="warning" :closable="false" show-icon :title="`未确认步骤：${snapshot.unresolved_steps.join('、')}`" />
    <div class="evidence-section stats"><h5>探索统计</h5><span>工具调用 {{ toolStats.total_tool_calls || 0 }}</span><span>失败调用 {{ toolStats.failed_tool_calls || 0 }}</span><span>耗时 {{ formatDuration(toolStats.duration_seconds) }}</span><span v-if="toolStats.termination_reason">结束原因：{{ toolStats.termination_reason }}</span></div>
  </div>
  <el-empty v-else description="页面探索尚未完成" :image-size="70" />
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ snapshot: { type: Object, default: () => ({}) }, toolStats: { type: Object, default: () => ({}) } })
const evidenceRows = computed(() => Object.entries(props.snapshot?.step_evidence || {}).map(([stepId, item]) => ({ stepId, status: item.status || 'unresolved', reason: item.reason || (item.element_names || []).join('、') || '—' })))
const attributeText = (attributes) => Object.entries(attributes || {}).map(([key, value]) => `${key}=${value}`).join('，')
const statusText = (status) => ({ confirmed: '已确认', partial: '部分确认', partially_confirmed: '部分确认', unresolved: '未确认' })[status] || status || '未知'
const formatDuration = (value) => value ? `${Number(value).toFixed(value < 10 ? 1 : 0)} 秒` : '—'
</script>

<style scoped>
.evidence-content { display: grid; gap: 18px; }.evidence-section h5 { margin: 0 0 10px; color: var(--app-text-primary); font-size: 14px; }.path-tag { margin: 0 8px 8px 0; }.muted { color: var(--app-text-secondary); font-size: 13px; }.evidence-section p { margin: 6px 0; color: var(--app-text-regular); font-size: 13px; }.evidence-section pre { margin: 8px 0 0; padding: 8px; overflow-x: auto; color: var(--app-text-primary); background: var(--app-bg-secondary, #f7f7f7); border-radius: 6px; font-size: 12px; }.stats { display: flex; flex-wrap: wrap; gap: 12px; color: var(--app-text-secondary); font-size: 13px; }
</style>
