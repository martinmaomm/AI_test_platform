<template>
  <el-alert v-if="report?.status === 'stale'" type="info" :closable="false" show-icon :title="report.message || '草稿已修改，原静态检查结果已失效。'" />
  <div v-else-if="report && Object.keys(report).length" class="quality-content">
    <div class="quality-summary"><el-statistic title="警告" :value="grouped.warning.length" /><el-statistic title="阻断" :value="grouped.blocker.length" /></div>
    <p class="summary-message">{{ grouped.blocker.length ? '请修复以下静态检查问题，再调试或保存。' : '静态检查无阻断项；仍需实际调试验证业务流程。' }}</p>
    <el-tabs v-model="activeName"><el-tab-pane :label="`警告 (${grouped.warning.length})`" name="warning"><check-list :items="grouped.warning" type="warning" empty-text="暂无警告" /></el-tab-pane><el-tab-pane :label="`阻断 (${grouped.blocker.length})`" name="blocker"><check-list :items="grouped.blocker" type="danger" empty-text="暂无阻断项" /></el-tab-pane></el-tabs>
  </div>
  <el-empty v-else description="质量检查尚未完成" :image-size="70" />
</template>

<script setup>
import { computed, defineComponent, h, ref, watch } from 'vue'
const props = defineProps({ report: { type: Object, default: () => ({}) } })
const activeName = ref('warning')
const grouped = computed(() => ({
  warning: Array.isArray(props.report?.warnings) ? props.report.warnings : [],
  blocker: Array.isArray(props.report?.blockers) ? props.report.blockers : []
}))
watch(() => props.report, () => { activeName.value = grouped.value.blocker.length ? 'blocker' : 'warning' }, { immediate: true })
const CheckList = defineComponent({
  props: { items: { type: Array, default: () => [] }, type: String, emptyText: String },
  setup(componentProps) { return () => componentProps.items.length ? h('div', { class: 'check-list' }, componentProps.items.map(item => h('div', { class: 'check-item', key: `${item.code}-${item.line || ''}` }, [h('span', { class: ['check-code', `check-code-${componentProps.type}`] }, item.code), h('span', item.message), item.line ? h('small', `第 ${item.line} 行`) : null]))) : h('span', { class: 'empty-checks' }, componentProps.emptyText) }
})
</script>

<style scoped>
.quality-summary { display: flex; gap: 38px; padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; }.summary-message { color: var(--app-text-secondary); font-size: 13px; }.check-list { display: grid; gap: 9px; }.check-item { display: flex; gap: 8px; align-items: flex-start; color: var(--app-text-regular); font-size: 13px; }.check-item small { margin-left: auto; color: var(--app-text-secondary); white-space: nowrap; }.check-code { padding: 2px 6px; border: 1px solid currentColor; border-radius: 4px; font-size: 11px; line-height: 1.2; white-space: nowrap; }.check-code-success { color: var(--el-color-success); }.check-code-warning { color: var(--el-color-warning); }.check-code-danger { color: var(--el-color-danger); }.empty-checks { color: var(--app-text-secondary); font-size: 13px; }
</style>
