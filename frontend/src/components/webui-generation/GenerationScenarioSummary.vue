<template>
  <div v-if="scenario && Object.keys(scenario).length" class="summary-grid">
    <div class="summary-item full"><span>目标</span><strong>{{ scenario.objective || '未提供' }}</strong></div>
    <div class="summary-item full"><span>连续场景步骤</span><ol><li v-for="item in scenario.instructions || []" :key="item">{{ item }}</li></ol></div>
    <div class="summary-item full"><span>成功标准</span><ul><li v-for="item in scenario.success_criteria || []" :key="item">{{ item }}</li></ul></div>
    <div v-if="scenario.preconditions?.length" class="summary-item full"><span>前置条件</span><ul><li v-for="item in scenario.preconditions" :key="item">{{ item }}</li></ul></div>
    <div class="summary-item"><span>测试数据写入</span><strong>{{ scenario.allow_test_data_writes ? '仅限本轮命名空间' : '不允许' }}</strong></div>
    <div class="summary-item"><span>清理预期</span><strong>{{ scenario.cleanup_expected ? '需要核对' : '不需要' }}</strong></div>
    <div v-if="scenario.input_refs?.length" class="summary-item full"><span>运行变量</span><strong>{{ scenario.input_refs.map(item => item.name).join('、') }}</strong></div>
  </div>
  <el-empty v-else description="场景理解尚未完成" :image-size="70" />
</template>

<script setup>
defineProps({ scenario: { type: Object, default: () => ({}) } })
</script>

<style scoped>
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.summary-item { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; display: grid; gap: 6px; }.summary-item.full { grid-column: 1 / -1; }.summary-item span { color: var(--app-text-secondary); font-size: 12px; }.summary-item strong { color: var(--app-text-primary); font-size: 14px; }.summary-item ul, .summary-item ol { margin: 0; padding-left: 20px; color: var(--app-text-primary); font-size: 13px; line-height: 1.7; } @media (max-width: 640px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
