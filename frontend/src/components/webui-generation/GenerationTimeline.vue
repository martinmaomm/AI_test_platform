<template>
  <section class="generation-card timeline-card">
    <div class="timeline-header"><div><h4>生成流程</h4><p>{{ generationStatusLabel(generation?.status) }}。流程阶段完成不代表脚本已实际调试通过。</p></div><div class="timeline-meta"><el-tag :type="statusTagType" effect="plain">{{ generation?.progress || 0 }}%</el-tag><span v-if="modelName">{{ modelName }}</span></div></div>
    <el-progress :percentage="Number(generation?.progress || 0)" :status="progressStatus" :stroke-width="8" />
    <el-steps :active="activeIndex" :process-status="stepProcessStatus" finish-status="success" align-center class="generation-steps"><el-step v-for="item in timeline" :key="item.stage" :title="item.label" :status="item.state" /></el-steps>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { buildGenerationTimeline, generationStatusLabel, isPausedGeneration, modelInfoLabel } from '@/composables/webUIScriptGenerationPresentation'
const props = defineProps({ generation: { type: Object, default: null } })
const timeline = computed(() => buildGenerationTimeline(props.generation))
const activeIndex = computed(() => Math.max(0, timeline.value.findIndex(item => item.state === 'process')))
const modelName = computed(() => modelInfoLabel(props.generation?.model_info, ''))
const progressStatus = computed(() => props.generation?.status === 'failed' ? 'exception' : props.generation?.status === 'cancelled' || isPausedGeneration(props.generation?.status) ? 'warning' : '')
const stepProcessStatus = computed(() => props.generation?.status === 'failed' ? 'error' : 'process')
const statusTagType = computed(() => props.generation?.status === 'failed' ? 'danger' : ['ready_with_warnings', 'needs_review'].includes(props.generation?.status) || isPausedGeneration(props.generation?.status) ? 'warning' : props.generation?.status === 'ready' ? 'success' : 'info')
</script>

<style scoped>
.generation-card { padding: 18px 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.timeline-header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 14px; }.timeline-header h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }.timeline-header p { margin: 5px 0 0; color: var(--app-text-secondary); font-size: 13px; }.timeline-meta { display: flex; gap: 10px; align-items: center; color: var(--app-text-secondary); font-size: 12px; text-align: right; }.generation-steps { margin-top: 22px; } @media (max-width: 820px) { .generation-steps { overflow-x: auto; padding-bottom: 8px; min-width: 780px; }.timeline-card { overflow-x: auto; } }
</style>
