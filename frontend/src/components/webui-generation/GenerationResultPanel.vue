<template>
  <section class="generation-card result-panel">
    <div class="result-header">
      <div><h4>脚本工作区</h4><p>{{ statusLabel }}</p></div>
      <div class="result-actions"><el-button v-if="canRetryGeneration" type="warning" plain :disabled="busy" @click="emit('retry-generation')">基于轨迹整理脚本（不启动浏览器）</el-button><el-button v-if="canSave" type="success" :loading="saving" :disabled="busy" @click="requestSave">{{ generation?.is_saved ? '再次保存更新' : '保存到测试用例' }}</el-button><el-button v-if="generation?.is_saved" type="primary" plain @click="emit('open-test-case')">进入用例管理</el-button></div>
    </div>
    <GenerationActionRequired v-if="paused" :generation="generation" :resolving="resolving" @resolve="emit('resolve', $event)" @cancel="emit('cancel')" />
    <el-alert v-else-if="resolutionHint" :title="resolutionHint" :type="hintType" :closable="false" show-icon class="resolution-hint" />
    <el-alert v-else-if="draftCompletion.isPartial && !draft?.dirty" title="当前草稿未完成：可继续编辑或基于已保存证据整理脚本，不能视为测试通过。" type="warning" :closable="false" show-icon class="resolution-hint" />
    <el-tabs v-model="activeTab">
      <el-tab-pane label="场景摘要" name="scenario"><GenerationScenarioSummary :scenario="generation?.scenario_spec" :target-url="generation?.target_url" /></el-tab-pane>
      <el-tab-pane label="脚本工作区" name="script"><el-alert v-if="draftConflict" type="warning" :closable="false" show-icon title="工作区已更新；你的本地编辑仍保留，刷新会丢弃这些未保存内容。"><template #default><el-button size="small" type="warning" plain @click="emit('discard-local-draft')">丢弃本地编辑并刷新</el-button></template></el-alert><GenerationWorkspace v-if="generation?.script_draft || draft?.script_draft" :generation="generation" :draft="draft" :busy="busy" :draft-saving="draftSaving" :debugging="debugging" :debug-execution="debugExecution" :debug-execution-loading="debugExecutionLoading" @update-draft="emit('update-draft', $event)" @save-draft="emit('save-draft')" @debug="emit('debug', $event)" /><el-empty v-else description="脚本草稿尚未生成" :image-size="70" /></el-tab-pane>
      <el-tab-pane label="探索轨迹" name="evidence"><GenerationEvidence :snapshot="generation?.exploration_snapshot" :tool-stats="generation?.tool_stats" :failure-message="failureReason" /></el-tab-pane>
      <el-tab-pane label="质量报告" name="quality"><GenerationQualityReport :report="generation?.quality_report" /></el-tab-pane>
      <el-tab-pane label="技术信息" name="technical"><el-collapse><el-collapse-item title="查看任务技术信息"><dl class="technical-list"><dt>生成记录 ID</dt><dd>{{ generation?.id || '—' }}</dd><dt>任务 ID</dt><dd>{{ generation?.celery_task_id || '—' }}</dd><dt>状态 / 阶段 / 进度</dt><dd>{{ generation?.status || '—' }} / {{ generation?.current_stage || '—' }} / {{ generation?.progress || 0 }}%</dd><dt>模型</dt><dd>{{ modelText }}</dd><dt>探索工具统计</dt><dd>{{ toolStatsText }}</dd><dt v-if="generation?.error_code">错误码</dt><dd v-if="generation?.error_code">{{ generation.error_code }}</dd><dt v-if="generation?.exploration_snapshot?.termination_reason">轨迹终止标记</dt><dd v-if="generation?.exploration_snapshot?.termination_reason">{{ generation.exploration_snapshot.termination_reason }}</dd></dl></el-collapse-item><el-collapse-item v-if="technicalDiagnostics || technicalFinalMessage || rawModelOutput || technicalPendingDetails" title="查看模型最终回复、待补充项与原始输出（技术诊断）"><p class="technical-note">以下内容不会作为失败原因或普通提示。</p><pre v-if="technicalDiagnostics" class="technical-output">{{ technicalDiagnostics }}</pre><pre v-if="technicalPendingDetails" class="technical-output">{{ technicalPendingDetails }}</pre><pre v-if="technicalFinalMessage" class="technical-output">{{ technicalFinalMessage }}</pre><pre v-if="rawModelOutput" class="technical-output">{{ rawModelOutput }}</pre></el-collapse-item></el-collapse></el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { canRetryScriptFromTrace, canSaveGeneratedDraft, generationDraftCompletion, generationFailureReason, generationResolutionHint, generationStatusLabel, isPausedGeneration, modelInfoLabel } from '@/composables/webUIScriptGenerationPresentation'
import GenerationScenarioSummary from './GenerationScenarioSummary.vue'
import GenerationEvidence from './GenerationEvidence.vue'
import GenerationQualityReport from './GenerationQualityReport.vue'
import GenerationActionRequired from './GenerationActionRequired.vue'
import GenerationWorkspace from './GenerationWorkspace.vue'

const props = defineProps({ generation: { type: Object, default: null }, draft: { type: Object, default: null }, saving: Boolean, resolving: Boolean, draftSaving: Boolean, debugging: Boolean, busy: Boolean, draftConflict: Boolean, debugExecution: { type: Object, default: null }, debugExecutionLoading: Boolean })
const emit = defineEmits(['resolve', 'cancel', 'save', 'open-test-case', 'update-draft', 'save-draft', 'debug', 'retry-generation', 'discard-local-draft'])
const activeTab = ref('scenario')
const paused = computed(() => isPausedGeneration(props.generation?.status))
const statusLabel = computed(() => generationStatusLabel(props.generation?.status))
const resolutionHint = computed(() => generationResolutionHint(props.generation, { draftDirty: Boolean(props.draft?.dirty) }))
const hintType = computed(() => props.generation?.status === 'failed' ? 'error' : ['needs_review', 'needs_confirmation', 'needs_input'].includes(props.generation?.status) ? 'warning' : props.generation?.status === 'cancelled' ? 'info' : 'success')
const canSave = computed(() => canSaveGeneratedDraft(props.generation, props.draft, props.busy))
const canRetryGeneration = computed(() => canRetryScriptFromTrace(props.generation, props.busy))
const draftCompletion = computed(() => generationDraftCompletion(props.generation))
const failureReason = computed(() => generationFailureReason(props.generation))
const modelText = computed(() => modelInfoLabel(props.generation?.model_info))
const rawModelOutput = computed(() => props.generation?.exploration_snapshot?.model_output_raw || '')
const technicalFinalMessage = computed(() => {
  const message = props.generation?.exploration_snapshot?.final_message || ''
  return message === rawModelOutput.value ? '' : message
})
const technicalDiagnostics = computed(() => {
  const snapshot = props.generation?.exploration_snapshot || {}
  const values = {
    generation_error_message: props.generation?.error_message || '',
    snapshot_error_message: snapshot.error_message || ''
  }
  return Object.values(values).some(Boolean) ? JSON.stringify(values, null, 2) : ''
})
const technicalPendingDetails = computed(() => {
  const snapshot = props.generation?.exploration_snapshot || {}
  const pending = props.generation?.workspace?.verification?.assertion_state?.pending || props.generation?.quality_report?.assertion_state?.pending || []
  const completed = snapshot?.artifact?.completed_steps || []
  const remaining = snapshot?.artifact?.remaining_steps || []
  const history = (snapshot.artifact_history || []).map(item => ({
    revision: item.revision,
    completed_steps: item.artifact?.completed_steps || [],
    remaining_steps: item.artifact?.remaining_steps || []
  }))
  return pending.length || completed.length || remaining.length || history.length
    ? JSON.stringify({ completed_steps: completed, remaining_steps: remaining, pending, artifact_history: history }, null, 2)
    : ''
})
const toolStatsText = computed(() => { const stats = props.generation?.tool_stats || {}; return `调用 ${stats.total_tool_calls || 0} 次，失败 ${stats.failed_tool_calls || 0} 次${stats.duration_seconds ? `，耗时 ${stats.duration_seconds} 秒` : ''}` })
const requestSave = async () => {
  try {
    if (props.generation?.status === 'ready_with_warnings') {
      await ElMessageBox.confirm('当前脚本存在质量警告。建议先查看定位器和探索轨迹；是否仍要保存？', '存在警告', { type: 'warning', confirmButtonText: '继续保存', cancelButtonText: '返回查看' })
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
.generation-card { padding: 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.result-panel { min-width: 0; max-width: 100%; overflow: hidden; }.result-panel :deep(.el-tabs), .result-panel :deep(.el-tabs__content), .result-panel :deep(.el-tab-pane) { min-width: 0; max-width: 100%; }.result-header { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }.result-header h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }.result-header p, .technical-note { margin: 5px 0 0; color: var(--app-text-secondary); font-size: 13px; }.result-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }.resolution-hint { margin-bottom: 16px; }.technical-list { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 9px 14px; margin: 0; color: var(--app-text-regular); font-size: 13px; }.technical-list dt { color: var(--app-text-secondary); }.technical-list dd { margin: 0; word-break: break-all; }.technical-output { max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; background: var(--app-bg-secondary, #f5f7fa); border-radius: 6px; color: var(--app-text-regular); font-size: 12px; line-height: 1.6; } @media (max-width: 640px) { .result-header { flex-direction: column; }.result-actions { justify-content: flex-start; }.technical-list { grid-template-columns: 1fr; gap: 2px; }.technical-list dd { margin-bottom: 10px; } }
</style>
