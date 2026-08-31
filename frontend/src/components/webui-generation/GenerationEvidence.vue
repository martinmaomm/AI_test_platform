<template>
  <div v-if="snapshot && Object.keys(snapshot).length" class="evidence-content">
    <div class="evidence-section"><h5>访问路径</h5><el-tag v-for="path in snapshot.visited_paths || []" :key="path" class="path-tag" effect="plain">{{ path }}</el-tag><span v-if="!(snapshot.visited_paths || []).length" class="muted">尚无页面路径</span></div>
    <div class="evidence-section"><h5>页面与关键区域</h5><el-table :data="snapshot.page_states || []" size="small" max-height="240"><el-table-column prop="name" label="页面" min-width="130" /><el-table-column prop="path" label="路径" min-width="150" /><el-table-column label="关键区域" min-width="180"><template #default="{ row }">{{ (row.key_regions || []).join('、') || '—' }}</template></el-table-column></el-table></div>
    <div class="evidence-section"><h5>关键元素</h5><el-collapse><el-collapse-item v-for="(element, index) in snapshot.elements || []" :key="`${element.page_name}-${element.visible_name}-${index}`" :title="`${element.page_name || '页面'} · ${element.visible_name || '未命名元素'}`"><p>角色：{{ element.role || '未识别' }}</p><p v-if="Object.keys(element.stable_attributes || {}).length">稳定属性：{{ attributeText(element.stable_attributes) }}</p><div v-if="element.candidate_locators?.length"><span class="muted">候选定位器</span><pre v-for="locator in element.candidate_locators" :key="locator">{{ locator }}</pre></div></el-collapse-item></el-collapse></div>
    <div class="evidence-section"><h5>场景步骤证据</h5><el-table :data="evidenceRows" size="small" max-height="260"><el-table-column prop="stepId" label="步骤" width="86" /><el-table-column prop="status" label="证据状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'confirmed' ? 'success' : row.status === 'partial' || row.status === 'partially_confirmed' ? 'warning' : 'danger'" size="small">{{ statusText(row.status) }}</el-tag></template></el-table-column><el-table-column prop="reason" label="说明" min-width="220" /></el-table></div>
    <div v-if="completion.status" class="evidence-section checkpoint"><h5>探索完成情况</h5><p><el-tag :type="completionType" size="small">{{ completionLabel }}</el-tag></p><el-table v-if="missingTargets.length" :data="missingTargets" size="small" max-height="240"><el-table-column prop="target" label="待确认目标" min-width="150" /><el-table-column label="类型" width="130"><template #default="{ row }">{{ targetKindLabel(row.kind) }}</template></el-table-column><el-table-column prop="reason" label="原因 / 后续处理" min-width="240" /></el-table><p v-if="completion.budget_exhausted">探索轮次预算已用尽，平台不会继续猜测或自动执行写操作。</p></div>
    <div v-if="checkpoints.length" class="evidence-section"><h5>探索检查点</h5><el-table :data="checkpoints" size="small" max-height="200"><el-table-column prop="call_index" label="调用序号" width="100" /><el-table-column prop="tool_name" label="工具" min-width="180" /><el-table-column label="结果" width="110"><template #default="{ row }"><el-tag :type="row.status === 'succeeded' ? 'success' : 'danger'" size="small">{{ row.status === 'succeeded' ? '成功' : '失败' }}</el-tag></template></el-table-column></el-table></div>
  <el-alert v-if="snapshot.unresolved_steps?.length" type="warning" :closable="false" show-icon :title="`未确认步骤：${snapshot.unresolved_steps.join('、')}`" />
    <div class="evidence-section stats"><h5>探索统计</h5><span>工具调用 {{ toolStats.total_tool_calls || 0 }}</span><span>失败调用 {{ toolStats.failed_tool_calls || 0 }}</span><span>耗时 {{ formatDuration(toolStats.duration_seconds) }}</span><span v-if="toolStats.termination_reason">结束原因：{{ toolStats.termination_reason }}</span></div>
  </div>
  <el-empty v-else description="页面探索尚未完成" :image-size="70" />
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ snapshot: { type: Object, default: () => ({}) }, toolStats: { type: Object, default: () => ({}) } })
const evidenceRows = computed(() => Object.entries(props.snapshot?.step_evidence || {}).map(([stepId, item]) => ({ stepId, status: item.status || 'unresolved', reason: item.reason || (item.element_names || []).join('、') || '—' })))
const completion = computed(() => props.snapshot?.completion || {})
const missingTargets = computed(() => Array.isArray(completion.value?.missing_targets) ? completion.value.missing_targets : [])
const checkpoints = computed(() => Array.isArray(props.snapshot?.checkpoints) ? props.snapshot.checkpoints : [])
const completionLabel = computed(() => ({ complete: '探索完成', needs_targeted_exploration: '需要补充探索', needs_user_decision: '需要业务决策', blocked: '探索受阻' })[completion.value.status] || '探索状态未知')
const completionType = computed(() => ({ complete: 'success', needs_targeted_exploration: 'warning', needs_user_decision: 'warning', blocked: 'danger' })[completion.value.status] || 'info')
const attributeText = (attributes) => Object.entries(attributes || {}).map(([key, value]) => `${key}=${value}`).join('，')
const statusText = (status) => ({ confirmed: '已确认', partial: '部分确认', partially_confirmed: '部分确认', unresolved: '未确认' })[status] || status || '未知'
const targetKindLabel = (kind) => ({ observable: '页面可观察项', business_decision: '业务决策', permission_scope: '权限范围', data_scope: '数据范围' })[kind] || '未知'
const formatDuration = (value) => value ? `${Number(value).toFixed(value < 10 ? 1 : 0)} 秒` : '—'
</script>

<style scoped>
.evidence-content { display: grid; gap: 18px; }.evidence-section h5 { margin: 0 0 10px; color: var(--app-text-primary); font-size: 14px; }.path-tag { margin: 0 8px 8px 0; }.muted { color: var(--app-text-secondary); font-size: 13px; }.evidence-section p { margin: 6px 0; color: var(--app-text-regular); font-size: 13px; }.evidence-section pre { margin: 8px 0 0; padding: 8px; overflow-x: auto; color: var(--app-text-primary); background: var(--app-bg-secondary, #f7f7f7); border-radius: 6px; font-size: 12px; }.stats { display: flex; flex-wrap: wrap; gap: 12px; color: var(--app-text-secondary); font-size:13px; }.checkpoint { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; }
</style>
