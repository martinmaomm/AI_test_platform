<template>
  <div v-if="targetUrl || (scenario && Object.keys(scenario).length)" class="summary-grid">
    <div v-if="targetUrl" class="summary-item full"><span>目标地址</span><strong>{{ targetUrl }}</strong></div>
    <div class="summary-item full"><span>标题</span><strong>{{ scenario?.title || '未提供' }}</strong></div>
    <div class="summary-item full"><span>目标</span><strong>{{ scenario?.objective || '未提供' }}</strong></div>
    <div v-if="scenario?.original_user_target" class="summary-item full"><span>原始目标</span><strong>{{ scenario.original_user_target }}</strong></div>
    <div class="summary-item full"><span>连续场景步骤</span><ol v-if="instructions.length"><li v-for="item in instructions" :key="item">{{ item }}</li></ol><strong v-else>尚未拆分步骤</strong></div>
    <div v-if="successCriteria.length" class="summary-item full"><span>成功标准</span><ul><li v-for="item in successCriteria" :key="item">{{ item }}</li></ul></div>
  </div>
  <el-empty v-else description="场景理解尚未完成" :image-size="70" />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ scenario: { type: Object, default: () => ({}) }, targetUrl: { type: String, default: '' } })
const instructions = computed(() => Array.isArray(props.scenario?.instructions) ? props.scenario.instructions : [])
const successCriteria = computed(() => Array.isArray(props.scenario?.success_criteria) ? props.scenario.success_criteria : [])
</script>

<style scoped>
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.summary-item { padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; display: grid; gap: 6px; }.summary-item.full { grid-column: 1 / -1; }.summary-item span { color: var(--app-text-secondary); font-size: 12px; }.summary-item strong { color: var(--app-text-primary); font-size: 14px; }.summary-item ul, .summary-item ol { margin: 0; padding-left: 20px; color: var(--app-text-primary); font-size: 13px; line-height: 1.7; } @media (max-width: 640px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
