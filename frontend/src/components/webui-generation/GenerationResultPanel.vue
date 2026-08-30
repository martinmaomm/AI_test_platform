<template>
  <section class="generation-card result-panel">
    <div class="result-header">
      <div><h4>生成结果</h4><p>{{ statusLabel }}</p></div>
      <div class="result-actions"><el-button v-if="canSave" type="success" :loading="saving" :disabled="Boolean(generation?.is_saved)" @click="requestSave">{{ generation?.is_saved ? '已保存到测试用例' : '保存到测试用例' }}</el-button><el-button v-if="generation?.is_saved" type="primary" plain @click="emit('open-test-case')">进入用例管理</el-button></div>
    </div>
    <el-alert v-if="resolutionHint" :title="resolutionHint" :type="hintType" :closable="false" show-icon class="resolution-hint" />
    <el-tabs v-model="activeTab">
      <el-tab-pane label="场景摘要" name="scenario"><GenerationScenarioSummary :scenario="generation?.scenario_spec" /></el-tab-pane>
      <el-tab-pane label="最终脚本" name="script"><div v-if="generation?.script_draft" class="script-pane"><div class="script-actions"><el-button size="small" @click="copyScript">复制脚本</el-button></div><MonacoEditor :value="generation.script_draft" language="python" theme="vs-dark" :read-only="true" height="460px" /></div><el-empty v-else description="尚未生成脚本" :image-size="70" /></el-tab-pane>
      <el-tab-pane label="探索证据" name="evidence"><GenerationEvidence :snapshot="generation?.exploration_snapshot" :tool-stats="generation?.tool_stats" /></el-tab-pane>
      <el-tab-pane label="质量报告" name="quality"><GenerationQualityReport :report="generation?.quality_report" /></el-tab-pane>
      <el-tab-pane label="技术信息" name="technical"><el-collapse><el-collapse-item title="查看安全技术信息"><dl class="technical-list"><dt>生成记录 ID</dt><dd>{{ generation?.id || '—' }}</dd><dt>任务 ID</dt><dd>{{ generation?.celery_task_id || '—' }}</dd><dt>状态 / 阶段 / 进度</dt><dd>{{ generation?.status || '—' }} / {{ generation?.current_stage || '—' }} / {{ generation?.progress || 0 }}%</dd><dt>模型</dt><dd>{{ modelText }}</dd><dt>探索工具统计</dt><dd>{{ toolStatsText }}</dd><dt v-if="generation?.error_code">错误码</dt><dd v-if="generation?.error_code">{{ generation.error_code }}</dd></dl></el-collapse-item></el-collapse></el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MonacoEditor from '@/components/MonacoEditor.vue'
import { generationResolutionHint, generationStatusLabel } from '@/composables/webUIScriptGenerationPresentation'
import GenerationScenarioSummary from './GenerationScenarioSummary.vue'
import GenerationEvidence from './GenerationEvidence.vue'
import GenerationQualityReport from './GenerationQualityReport.vue'

const props = defineProps({ generation: { type: Object, default: null }, saving: Boolean })
const emit = defineEmits(['save', 'open-test-case'])
const activeTab = ref('scenario')
const statusLabel = computed(() => generationStatusLabel(props.generation?.status))
const resolutionHint = computed(() => generationResolutionHint(props.generation))
const hintType = computed(() => ['failed', 'needs_review', 'needs_confirmation', 'needs_credentials', 'needs_input'].includes(props.generation?.status) ? 'warning' : props.generation?.status === 'cancelled' ? 'info' : 'success')
const canSave = computed(() => ['ready', 'ready_with_warnings'].includes(props.generation?.status))
const modelText = computed(() => { const model = props.generation?.model_info || {}; return model.model_name ? `${model.provider || 'LLM'} · ${model.model_name}` : '—' })
const toolStatsText = computed(() => { const stats = props.generation?.tool_stats || {}; return `调用 ${stats.total_tool_calls || 0} 次，失败 ${stats.failed_tool_calls || 0} 次${stats.duration_seconds ? `，耗时 ${stats.duration_seconds} 秒` : ''}` })
const copyScript = async () => { try { await navigator.clipboard.writeText(props.generation?.script_draft || ''); ElMessage.success('脚本已复制') } catch { ElMessage.error('复制失败，请手动复制脚本') } }
const requestSave = async () => {
  try {
    if (props.generation?.status === 'ready_with_warnings') {
      await ElMessageBox.confirm('当前脚本存在质量警告。建议先查看定位器和探索证据；是否仍要保存？', '存在警告', { type: 'warning', confirmButtonText: '继续保存', cancelButtonText: '返回查看' })
    }
    const result = await ElMessageBox.prompt('保存标题最多 200 个字符，不能为空。', '保存到测试用例', {
      inputValue: props.generation?.scenario_spec?.title || '', inputPlaceholder: '测试用例标题', confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (value) => {
        const title = String(value || '').trim()
        if (!title) return '请输入测试用例标题。'
        if (title.length > 200) return '测试用例标题不能超过 200 个字符。'
        return true
      }
    })
    emit('save', result.value.trim())
  } catch (error) {
    // Cancelling either dialog is an expected user action and must stay silent.
    if (error !== 'cancel' && error !== 'close') ElMessage.error('保存标题校验失败，请重试。')
  }
}
</script>

<style scoped>
.generation-card { padding: 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.result-header { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }.result-header h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }.result-header p { margin: 5px 0 0; color: var(--app-text-secondary); font-size: 13px; }.result-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }.resolution-hint { margin-bottom: 16px; }.script-actions { display: flex; justify-content: flex-end; margin-bottom: 8px; }.technical-list { display: grid; grid-template-columns: 150px 1fr; gap: 9px 14px; margin: 0; color: var(--app-text-regular); font-size: 13px; }.technical-list dt { color: var(--app-text-secondary); }.technical-list dd { margin: 0; word-break: break-all; } @media (max-width: 640px) { .result-header { flex-direction: column; }.result-actions { justify-content: flex-start; }.technical-list { grid-template-columns: 1fr; gap: 2px; }.technical-list dd { margin-bottom: 10px; } }
</style>
