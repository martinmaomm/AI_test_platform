<template>
  <div v-if="scenario && Object.keys(scenario).length" class="summary-grid">
    <div class="summary-item full"><span>目标</span><strong>{{ scenario.objective || '未提供' }}</strong></div>
    <div v-if="scenario.preconditions?.length" class="summary-item full"><span>前置条件</span><ul><li v-for="item in scenario.preconditions" :key="item">{{ item }}</li></ul></div>
    <div class="summary-item full"><span>目标计划</span><ol><li v-for="goal in scenario.goals || []" :key="goal.id"><div class="goal-title"><strong>{{ goal.id }} · {{ goal.objective }}</strong><el-tag size="small" effect="plain">{{ goalKindLabel(goal.kind) }}</el-tag><el-tag v-if="goal.side_effect !== 'none'" size="small" type="warning" effect="plain">{{ sideEffectLabel(goal.side_effect) }}</el-tag></div><small>完成标准：{{ goal.completion_criteria }}</small><small v-if="goal.input_refs?.length">运行变量：{{ inputRefNames(goal) }}</small><small v-if="goal.verification">验证方式：{{ verificationLabel(goal.verification) }}</small><small v-if="goal.cleanup_for_goal_ids?.length">清理目标：{{ goal.cleanup_for_goal_ids.join('、') }}</small></li></ol></div>
    <el-alert v-if="scenario.ambiguities?.length" class="full" type="warning" :closable="false" title="存在待确认项" :description="scenario.ambiguities.join('；')" show-icon />
  </div>
  <el-empty v-else description="场景理解尚未完成" :image-size="70" />
</template>

<script setup>
defineProps({ scenario: { type: Object, default: () => ({}) } })
const goalKindLabel = (kind) => ({ setup: '准备', exercise: '操作', verify: '验证', cleanup: '清理' })[kind] || '目标'
const sideEffectLabel = (effect) => ({ test_data: '测试数据写入', external: '外部副作用', unknown: '副作用待确认' })[effect] || '无副作用'
const inputRefNames = (goal) => (goal?.input_refs || []).map(item => item?.name || item).filter(Boolean).join('、')
const verificationLabel = (verification) => ({
  visible: '目标元素可见',
  contains_ref: `目标区域包含变量 ${verification.input_ref || ''}`,
  not_contains_ref: `目标区域不包含变量 ${verification.input_ref || ''}`
})[verification?.mode] || '页面证据验证'
</script>

<style scoped>
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.summary-item { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-bg-secondary, #fafafa); }.summary-item.full { grid-column: 1 / -1; }.summary-item > span { display: block; margin-bottom: 6px; color: var(--app-text-secondary); font-size: 12px; }.summary-item strong { color: var(--app-text-primary); }.goal-title { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } ul, ol { margin: 0; padding-left: 20px; color: var(--app-text-regular); line-height: 1.75; } li + li { margin-top: 8px; } small { display: block; color: var(--app-text-secondary); } @media (max-width: 720px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
