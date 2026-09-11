<template>
  <section v-if="hasRepairContent" class="repair-panel workspace-section">
    <div class="section-heading">
      <div><h5>草稿 AI 修复</h5><p>原草稿保持不变。候选脚本仅在你二次确认采用后才写入工作区。</p></div>
      <el-tag :type="repairTagType" effect="plain">{{ repairLabel }}</el-tag>
    </div>

    <el-descriptions :column="2" size="small" border class="repair-summary">
      <el-descriptions-item label="处理阶段">{{ phaseLabel }}</el-descriptions-item>
      <el-descriptions-item label="尝试轮次">{{ repair.attempt_count || 0 }} / 2</el-descriptions-item>
      <el-descriptions-item v-if="repair.summary" label="分析摘要" :span="2">{{ repair.summary }}</el-descriptions-item>
      <el-descriptions-item v-if="repair.message" label="当前消息" :span="2">{{ repair.message }}</el-descriptions-item>
    </el-descriptions>

    <el-alert v-if="repair.blockers?.length" type="warning" :closable="false" show-icon title="修复受阻">
      <template #default><ul><li v-for="(blocker, index) in repair.blockers" :key="index">{{ blockerText(blocker) }}</li></ul></template>
    </el-alert>
    <el-alert v-if="finalCandidateBlockers.length" type="warning" :closable="false" show-icon title="最终候选静态检查问题">
      <template #default><ul><li v-for="(blocker, index) in finalCandidateBlockers" :key="blockerKey(blocker, index)">{{ blockerText(blocker) }}</li></ul></template>
    </el-alert>

    <div v-if="attempts.length" class="attempts">
      <h6>候选验证轮次</h6>
      <el-timeline>
        <el-timeline-item v-for="(attempt, index) in attempts" :key="attempt.execution_id ?? index" :type="attemptTagType(attemptExecutionStatus(attempt))">
          <div class="attempt-heading">
            <div><strong>第 {{ index + 1 }} 轮 · {{ executionStatusLabel(attemptExecutionStatus(attempt)) }}</strong><span v-if="hasExecutionId(attempt)"> · 执行 #{{ attempt.execution_id }}</span></div>
            <el-button v-if="hasExecutionId(attempt)" size="small" type="primary" plain :loading="repairExecutionLoading && isSelectedAttempt(attempt)" @click="requestExecutionDetails(attempt)">查看本轮执行详情</el-button>
          </div>
          <p v-if="attemptFailure(attempt)">{{ attemptFailure(attempt) }}</p>
          <p v-if="isStaticFailure(attempt)" class="attempt-static-note">本轮未创建执行记录：候选在静态检查阶段被拦截，并非浏览器实际运行失败。</p>
          <ul v-if="attemptBlockers(attempt).length" class="attempt-blockers"><li v-for="(blocker, blockerIndex) in attemptBlockers(attempt)" :key="blockerKey(blocker, blockerIndex)">{{ blockerText(blocker) }}</li></ul>
        </el-timeline-item>
      </el-timeline>
    </div>

    <el-collapse v-if="hasCandidate" class="candidate-content">
      <el-collapse-item title="查看候选差异" name="diff"><pre>{{ repair.candidate_diff || '候选未提供文本差异。' }}</pre></el-collapse-item>
      <el-collapse-item title="查看完整候选脚本" name="script"><pre>{{ repair.candidate_script }}</pre></el-collapse-item>
    </el-collapse>

    <div v-if="hasDiscardableCandidate" class="repair-actions">
      <el-alert type="info" :closable="false" title="候选审核期间已锁定保存、真实调试和保存用例；请先比较候选并决定是否采用。" />
      <div class="repair-action-buttons">
        <el-button v-if="hasCandidate" type="success" :loading="applying" :disabled="busy || discarding" @click="requestApply">采用此版本</el-button>
        <el-button type="warning" plain :loading="discarding" :disabled="busy || applying" @click="requestDiscard">放弃候选，继续编辑原草稿</el-button>
      </div>
    </div>

    <section v-if="repairExecution || repairExecutionLoading" class="repair-execution">
      <div class="section-heading"><div><h6>{{ selectedExecutionTitle }}</h6><p>日志与截图来自该轮真实执行记录。</p></div></div>
      <el-skeleton v-if="repairExecutionLoading && !repairExecution" :rows="5" animated />
      <WebUITestCaseExecutionDetail v-else-if="repairExecution" :execution="repairExecution" hide-ai-repair />
    </section>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import WebUITestCaseExecutionDetail from '@/components/WebUITestCaseExecutionDetail.vue'

const props = defineProps({ repair: { type: Object, default: () => ({}) }, busy: Boolean, applying: Boolean, discarding: Boolean, repairExecution: { type: Object, default: null }, repairExecutionLoading: Boolean, selectedExecutionId: { type: [Number, String], default: null } })
const emit = defineEmits(['apply', 'discard', 'view-execution'])
const repair = computed(() => props.repair || {})
const attempts = computed(() => Array.isArray(repair.value.attempts) ? repair.value.attempts : [])
const finalCandidateBlockers = computed(() => Array.isArray(repair.value.candidate_quality_report?.blockers)
  ? repair.value.candidate_quality_report.blockers
  : [])
const selectedAttemptIndex = computed(() => attempts.value.findIndex(attempt => isSelectedAttempt(attempt)))
const selectedExecutionTitle = computed(() => selectedAttemptIndex.value >= 0 ? `第 ${selectedAttemptIndex.value + 1} 轮执行详情` : '本轮执行详情')
const hasCandidate = computed(() => Boolean(repair.value.candidate_hash && repair.value.candidate_script))
const hasDiscardableCandidate = computed(() => Boolean(repair.value.candidate_hash))
const hasRepairContent = computed(() => repair.value.status && repair.value.status !== 'idle')
const repairLabel = computed(() => ({ pending: '等待 AI 分析', running: 'AI 正在修复', candidate_ready: '候选待审核（未确认成功）', candidate_passed: '候选已通过实际验证', failed: '修复未完成' })[repair.value.status] || '修复状态未知')
const repairTagType = computed(() => ({ pending: 'warning', running: 'warning', candidate_ready: 'warning', candidate_passed: 'success', failed: 'danger' })[repair.value.status] || 'info')
const phaseLabel = computed(() => ({ collecting: '收集失败证据', analyzing: '分析失败原因', exploring: '定向检查页面', validating: '验证候选脚本', completed: '候选处理结束，请查看验证结果' })[repair.value.phase] || '等待处理')
const attemptTagType = status => ({ passed: 'success', failed: 'danger', error: 'danger', incomplete: 'warning', not_run: 'warning', pending: 'warning', running: 'warning' })[status] || 'info'
const attemptExecutionStatus = attempt => attempt?.execution_status || ''
const executionStatusLabel = status => ({ passed: '执行通过', failed: '执行失败', error: '执行异常', incomplete: '验证未完成', not_run: '未进入浏览器验证', pending: '等待执行', running: '正在执行' })[status] || '等待执行'
const attemptFailure = attempt => attempt?.summary || attempt?.failure_summary || attempt?.error_message || attempt?.message || ''
const attemptBlockers = attempt => Array.isArray(attempt?.blockers) ? attempt.blockers : []
const isStaticFailure = attempt => attemptExecutionStatus(attempt) === 'not_run'
  && !hasExecutionId(attempt)
  && (attempt?.static_status === 'needs_review' || attemptBlockers(attempt).length > 0)
const blockerText = blocker => {
  if (typeof blocker === 'string') return blocker
  const code = blocker?.code ? `[${blocker.code}] ` : ''
  const message = blocker?.message || '存在未说明的修复阻塞项。'
  const line = blocker?.line === null || blocker?.line === undefined || blocker?.line === '' ? '' : `（第 ${blocker.line} 行）`
  return `${code}${message}${line}`
}
const blockerKey = (blocker, index) => `${blocker?.code || 'unknown'}-${blocker?.line ?? 'none'}-${index}`
const hasExecutionId = attempt => attempt?.execution_id !== null && attempt?.execution_id !== undefined
const isSelectedAttempt = attempt => hasExecutionId(attempt)
  && props.selectedExecutionId !== null
  && props.selectedExecutionId !== undefined
  && String(attempt.execution_id) === String(props.selectedExecutionId)
const requestExecutionDetails = attempt => {
  if (hasExecutionId(attempt)) emit('view-execution', attempt.execution_id)
}
const requestApply = async () => {
  if (!hasCandidate.value || props.busy || props.applying) return
  try {
    const caution = repair.value.status === 'candidate_passed'
      ? '候选已完成实际验证。采用后会以候选脚本创建新的工作区版本。'
      : '该候选尚未通过实际验证。采用后仍需继续修改和真实调试。'
    await ElMessageBox.confirm(`${caution} 原草稿将不再作为当前版本，是否确认采用？`, '确认采用修复候选', { type: 'warning', confirmButtonText: '确认采用', cancelButtonText: '继续比较' })
    emit('apply', repair.value.candidate_hash)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('采用确认失败，请重试。')
  }
}
const requestDiscard = async () => {
  if (!hasDiscardableCandidate.value || props.busy || props.applying || props.discarding) return
  try {
    await ElMessageBox.confirm('放弃后将清除当前候选，保留原草稿与版本号，并恢复编辑、真实调试和保存。是否确认继续？', '确认放弃修复候选', { type: 'warning', confirmButtonText: '确认放弃', cancelButtonText: '继续比较' })
    emit('discard', repair.value.candidate_hash)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('放弃确认失败，请重试。')
  }
}
</script>

<style scoped>
.repair-panel { display: grid; gap: 14px; margin-top: 16px; padding: 16px; border: 1px solid var(--app-border); border-radius: 8px; }.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }.section-heading h5, .section-heading h6 { margin: 0; color: var(--app-text-primary); font-size: 14px; }.section-heading p { margin: 5px 0 0; color: var(--app-text-secondary); font-size: 13px; line-height: 1.6; }.repair-summary :deep(.el-descriptions__label) { width: 100px; }.attempts h6 { margin: 0 0 8px; font-size: 13px; }.attempt-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.attempts p { margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--app-text-secondary); }.attempt-static-note { color: var(--el-color-warning-dark-2, #b88230) !important; }.attempt-blockers { display: grid; gap: 5px; margin: 8px 0 0; padding-left: 20px; color: var(--app-text-regular); }.candidate-content pre { max-height: 360px; overflow: auto; margin: 0; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--app-bg-secondary, #f5f7fa); border-radius: 6px; font-size: 12px; line-height: 1.6; }.repair-actions { display: grid; gap: 10px; }.repair-action-buttons { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }.repair-execution { overflow: hidden; border: 1px solid var(--app-border); border-radius: 8px; }.repair-execution > .section-heading { padding: 16px 16px 0; } @media (max-width: 640px) { .repair-panel { margin-top: 12px; padding: 12px; }.section-heading, .attempt-heading { flex-direction: column; align-items: stretch; }.attempt-heading :deep(.el-button) { align-self: flex-start; }.repair-action-buttons :deep(.el-button) { flex: 1 1 100%; } }
</style>
