<template>
  <div v-if="scenario && Object.keys(scenario).length" class="summary-grid">
    <div class="summary-item full"><span>目标</span><strong>{{ scenario.objective || '未提供' }}</strong></div>
    <div v-if="scenario.preconditions?.length" class="summary-item full"><span>前置条件</span><ul><li v-for="item in scenario.preconditions" :key="item">{{ item }}</li></ul></div>
    <div class="summary-item full"><span>场景步骤</span><ol><li v-for="step in scenario.steps || []" :key="step.id"><strong>{{ step.name }}</strong><small>{{ step.expected || step.target_hint }}</small></li></ol></div>
    <div v-if="scenario.assertions?.length" class="summary-item"><span>断言</span><ul><li v-for="item in scenario.assertions" :key="item.id">{{ item.name }}：{{ item.expected }}</li></ul></div>
    <div v-if="scenario.cleanup?.length" class="summary-item"><span>清理策略</span><ul><li v-for="item in scenario.cleanup" :key="item.id || item.name">{{ item.name || item }}</li></ul></div>
    <el-alert v-if="scenario.ambiguities?.length" class="full" type="warning" :closable="false" title="存在待确认项" :description="scenario.ambiguities.join('；')" show-icon />
  </div>
  <el-empty v-else description="场景理解尚未完成" :image-size="70" />
</template>

<script setup>
defineProps({ scenario: { type: Object, default: () => ({}) } })
</script>

<style scoped>
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.summary-item { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-bg-secondary, #fafafa); }.summary-item.full { grid-column: 1 / -1; }.summary-item > span { display: block; margin-bottom: 6px; color: var(--app-text-secondary); font-size: 12px; }.summary-item strong { color: var(--app-text-primary); } ul, ol { margin: 0; padding-left: 20px; color: var(--app-text-regular); line-height: 1.75; } small { display: block; color: var(--app-text-secondary); } @media (max-width: 720px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
