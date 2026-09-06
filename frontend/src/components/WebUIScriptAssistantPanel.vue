<template>
  <section class="assistant-panel" aria-label="AI 脚本助手">
    <header class="assistant-heading">
      <div>
        <h4>AI 脚本助手</h4>
        <p>{{ isEdit ? '对话只生成候选，不会调用浏览器或保存用例。' : '修复和验证会实际运行测试网站；仅使用测试账号和测试数据。' }}</p>
      </div>
      <div class="assistant-heading-actions">
        <el-button size="small" :loading="loading" @click="() => loadRecent()">最近会话</el-button>
        <el-button size="small" :disabled="active || acting" @click="newConversation">新建会话</el-button>
        <el-button v-if="assistant && active" size="small" type="danger" plain :loading="acting" @click="requestCancel">取消任务</el-button>
      </div>
    </header>

    <el-alert v-if="lastError" :title="lastError" type="warning" :closable="false" show-icon />
    <el-alert v-if="isEdit && !editContext?.testCaseId" title="新建用例需先保存，才能使用 AI 对话编辑。" type="info" :closable="false" show-icon />
    <el-alert v-if="isRepair" title="确认后，模型、MCP（如需要）和测试网站会接收必要上下文；执行日志或截图可能包含测试信息。已发生的网站操作不会因取消而回滚。" type="warning" :closable="false" show-icon />

    <div class="assistant-config">
      <el-select v-model="modelConfigId" :loading="loadingModels" :disabled="acting" placeholder="选择模型和提供商">
        <el-option v-for="model in modelConfigs" :key="model.id" :label="assistantModelLabel(model)" :value="model.id" />
      </el-select>
      <el-tag v-if="assistant?.model_info" effect="plain">当前会话：{{ assistantModelLabel(assistant.model_info) }}</el-tag>
      <el-select v-if="assistants.length" v-model="selectedAssistantId" :disabled="acting" placeholder="最近会话">
        <el-option v-for="item in assistants" :key="item.id" :label="recentLabel(item)" :value="item.id" />
      </el-select>
    </div>

    <template v-if="isRepair && !assistant">
      <div class="runtime-heading"><strong>本次运行变量</strong><el-button text type="primary" @click="addRuntimeVariable">添加</el-button></div>
      <div v-for="(item, index) in runtimeVariables" :key="index" class="runtime-row">
        <el-input v-model="item.name" placeholder="变量名" /><el-input v-model="item.value" :type="item.is_secret ? 'password' : 'text'" show-password placeholder="本次值" /><el-button text type="danger" @click="runtimeVariables.splice(index, 1)">删除</el-button>
      </div>
      <el-form-item label="运行超时（秒）"><el-input-number v-model="runtimeTimeout" :min="30" :max="1800" /></el-form-item>
      <el-button type="danger" :disabled="!modelConfigId || acting" :loading="acting" @click="requestRepair">确认并开始 AI 修复</el-button>
    </template>

    <template v-if="isEdit && !assistant">
      <el-input v-model="messageText" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="说明你希望如何修改脚本…" :disabled="!editContext?.testCaseId || acting" />
      <div class="message-actions"><el-button type="primary" :disabled="!canStartEdit" :loading="acting" @click="sendMessage(false)">发送</el-button></div>
    </template>

    <template v-if="assistant">
      <div class="session-status">
        <el-tag :type="statusTagType(assistant.status)" effect="plain">{{ statusLabel(assistant.status) }}</el-tag>
        <span>{{ assistant.message || '会话状态已恢复。' }}</span>
        <el-tag :type="verificationTagType(assistant.verification)" effect="plain">{{ verificationLabel(assistant.verification) }}</el-tag>
      </div>

      <section v-if="assistant.summary || assistant.blockers?.length" class="assistant-summary">
        <h5>本轮中文摘要</h5><p v-if="assistant.summary">{{ assistant.summary }}</p>
        <el-alert v-for="(blocker, index) in assistant.blockers || []" :key="index" :title="typeof blocker === 'string' ? blocker : blocker.message || '需要人工处理'" type="warning" :closable="false" />
      </section>

      <section v-if="assistant.candidate_script || assistant.candidate_diff" class="candidate-section">
        <div class="candidate-heading"><h5>候选修改差异</h5><el-tag :type="candidateTagType" effect="plain">{{ candidateTagLabel }}</el-tag></div>
        <el-radio-group v-model="candidateView" size="small" aria-label="候选内容视图"><el-radio-button label="diff">修改差异</el-radio-button><el-radio-button label="full">完整候选代码</el-radio-button></el-radio-group>
        <pre>{{ candidateView === 'full' ? assistant.candidate_script : (assistant.candidate_diff || assistant.candidate_script) }}</pre>
        <div class="candidate-actions">
          <el-button v-if="isEdit" type="primary" :disabled="!canContinue || acting" @click="applyToEditor">采用到编辑器</el-button>
          <el-button v-if="isEdit" :disabled="!canContinue || acting" :loading="acting" @click="continueCandidate">继续调整候选</el-button>
          <el-button v-if="isEdit" type="warning" plain :disabled="!canVerify || acting" @click="requestVerify">调试验证</el-button>
          <el-button v-if="isRepair" :type="requiresManualReview ? 'warning' : 'primary'" :disabled="!canApplyRepair || acting" :loading="acting" @click="requestApply">{{ requiresManualReview ? '人工确认并保存' : '采用并保存' }}</el-button>
        </div>
      </section>

      <section v-if="isEdit && hasCandidate" class="verification-options">
        <div class="runtime-heading"><strong>调试验证的本次变量</strong><el-button text type="primary" @click="addRuntimeVariable">添加</el-button></div>
        <div v-for="(item, index) in runtimeVariables" :key="index" class="runtime-row"><el-input v-model="item.name" placeholder="变量名" /><el-input v-model="item.value" :type="item.is_secret ? 'password' : 'text'" show-password placeholder="本次值" /><el-button text type="danger" @click="runtimeVariables.splice(index, 1)">删除</el-button></div>
        <el-form-item label="调试超时（秒）"><el-input-number v-model="runtimeTimeout" :min="30" :max="1800" /></el-form-item>
      </section>

      <section v-if="assistant.attempts?.length || assistant.verification?.execution_id" class="attempt-section">
        <h5>本轮执行与截图</h5>
        <el-button v-for="attempt in assistant.attempts || []" :key="`${attempt.round}-${attempt.execution_id}`" size="small" plain :disabled="!attempt.execution_id" @click="loadAttempt(attempt.execution_id)">
          第 {{ attempt.round || '—' }} 轮：{{ attemptStatusLabel(attempt.execution_status) }}{{ attempt.has_screenshot ? '（有截图）' : '' }}
        </el-button>
        <el-button v-if="assistant.verification?.execution_id" size="small" plain @click="loadAttempt(assistant.verification.execution_id)">查看调试验证详情</el-button>
        <WebUITestCaseExecutionDetail v-if="attemptExecution" :execution="attemptExecution" hide-ai-repair />
      </section>

      <section v-if="isEdit" class="conversation-section">
        <h5>会话</h5>
        <div class="message-list"><article v-for="(item, index) in assistant.messages || []" :key="`${item.created_at || index}-${item.role}`" :class="['message', item.role]">{{ item.content }}</article></div>
        <el-input v-model="messageText" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="说明你希望如何修改脚本…" :disabled="active || acting" />
        <div class="message-actions"><el-button type="primary" :disabled="!canSend" :loading="acting" @click="sendMessage(false)">发送</el-button></div>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getWebUITestCaseExecution } from '@/api/webTesting'
import { useWebUIScriptAssistant } from '@/composables/useWebUIScriptAssistant'
import { assistantAttemptStatusLabel, assistantModelLabel, assistantPanelContext, canApplyRepairCandidate, canContinueCandidate, canVerifyCandidate, repairAdoptionState, verificationLabel, verificationTagType } from '@/composables/webUIScriptAssistantPresentation'

const WebUITestCaseExecutionDetail = defineAsyncComponent(() => import('@/components/WebUITestCaseExecutionDetail.vue'))
const props = defineProps({
  projectId: { type: [Number, String], required: true },
  editContext: { type: Object, default: null },
  repairContext: { type: Object, default: null }
})
const emit = defineEmits(['apply-to-editor'])
const context = computed(() => assistantPanelContext(props.editContext, props.repairContext))
const isEdit = computed(() => Boolean(props.editContext))
const isRepair = computed(() => Boolean(props.repairContext))
const { assistants, assistant, modelConfigs, loading, loadingModels, acting, active, lastError, selectAssistant, loadRecent, loadAssistant, create, message, verify, apply, cancel } = useWebUIScriptAssistant({ projectId: computed(() => props.projectId), context })
const modelConfigId = ref(null)
const messageText = ref('')
const candidateView = ref('diff')
const runtimeVariables = ref([])
const runtimeTimeout = ref(300)
const attemptExecution = ref(null)
let attemptRequestVersion = 0
const clearAttempt = () => {
  attemptRequestVersion += 1
  attemptExecution.value = null
}
const selectedAssistantId = computed({ get: () => assistant.value?.id || null, set: value => {
  const selected = assistants.value.find(item => String(item.id) === String(value)) || null
  selectAssistant(selected)
  if (selected?.id) loadAssistant(selected.id, { quiet: true })
} })
const hasCandidate = computed(() => Boolean(assistant.value?.candidate_hash && assistant.value?.candidate_script))
const canContinue = computed(() => canContinueCandidate(assistant.value))
const canVerify = computed(() => canVerifyCandidate(assistant.value))
const canApplyRepair = computed(() => canApplyRepairCandidate(assistant.value))
const adoption = computed(() => repairAdoptionState(assistant.value))
const requiresManualReview = computed(() => adoption.value.requires_acknowledge_review)
const wasManuallyAdopted = computed(() => assistant.value?.quality_report?.manual_adoption?.reason === 'REPAIR_SCOPE_CHANGED')
const candidateTagLabel = computed(() => {
  if (assistant.value?.status === 'applied') return wasManuallyAdopted.value ? '已人工确认保存' : '已保存'
  if (requiresManualReview.value) return '需人工确认保存'
  return assistant.value?.candidate_hash ? '候选已生成' : '候选生成中'
})
const candidateTagType = computed(() => {
  if (assistant.value?.status === 'applied') return 'success'
  return requiresManualReview.value ? 'warning' : 'info'
})
const canSend = computed(() => Boolean(messageText.value.trim() && assistant.value && !active.value && !acting.value))
const canStartEdit = computed(() => Boolean(messageText.value.trim() && modelConfigId.value && props.editContext?.testCaseId && !acting.value))

watch(modelConfigs, items => {
  const sessionConfigId = assistant.value?.model_info?.config_id ?? assistant.value?.model_config_id
  if (sessionConfigId && items.some(item => String(item.id) === String(sessionConfigId))) modelConfigId.value = sessionConfigId
  else if (!items.some(item => item.id === modelConfigId.value)) modelConfigId.value = items[0]?.id || null
}, { immediate: true })
watch(assistant, value => {
  const configId = value?.model_info?.config_id ?? value?.model_config_id
  if (configId && modelConfigs.value.some(item => String(item.id) === String(configId))) modelConfigId.value = configId
}, { immediate: true })
watch([() => props.projectId, () => JSON.stringify(context.value), () => assistant.value?.id], clearAttempt, { flush: 'sync' })
onBeforeUnmount(clearAttempt)

const statusLabel = status => ({ idle: '等待操作', queued: '已排队', running: '处理中', candidate_ready: '候选待审核', candidate_passed: '候选已实际验证', failed: '处理失败', cancelled: '已取消', applied: '已采用' })[status] || '状态未知'
const statusTagType = status => ({ queued: 'warning', running: 'warning', candidate_ready: 'warning', candidate_passed: 'success', failed: 'danger', cancelled: 'info', applied: 'success' })[status] || 'info'
const attemptStatusLabel = assistantAttemptStatusLabel
const recentLabel = item => `${item.mode === 'repair' ? '修复' : '编辑'} · ${statusLabel(item.status)} · ${new Date(item.updated_at || item.created_at || Date.now()).toLocaleString()}`
const newConversation = () => {
  selectAssistant(null)
  messageText.value = ''
  runtimeVariables.value = []
  attemptExecution.value = null
}
const addRuntimeVariable = () => runtimeVariables.value.push({ name: '', value: '', is_secret: false })
const runtimePayload = () => {
  if (runtimeVariables.value.some(item => !item.name?.trim())) throw new Error('变量名不能为空')
  return runtimeVariables.value.map(item => ({ ...item, name: item.name.trim() }))
}
const editPayload = () => ({
  test_case_id: props.editContext.testCaseId,
  expected_edit_version: props.editContext.editVersion,
  script_content: props.editContext.scriptContent,
  description: props.editContext.description,
  variables: props.editContext.variables,
  message: messageText.value.trim()
})
const requestRepair = async () => {
  try {
    const runtime = runtimePayload()
    await ElMessageBox.confirm('确认后将对测试网站执行修复验证，可能写入测试数据。请确认仅使用测试账号和测试数据。', '确认 AI 修复', { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' })
    await create({ mode: 'repair', model_config_id: modelConfigId.value, execution_id: props.repairContext.executionId, ...(props.repairContext.suiteCaseId ? { suite_case_id: props.repairContext.suiteCaseId } : {}), confirm_execution: true, runtime_variables: runtime, options: { timeout: runtimeTimeout.value } })
  } catch (error) { if (error !== 'cancel' && error !== 'close' && error?.message !== '变量名不能为空') ElMessage.error(lastError.value || '启动 AI 修复失败'); else if (error?.message === '变量名不能为空') ElMessage.warning(error.message) }
}
const sendMessage = async useCandidate => {
  try {
    if (!assistant.value) {
      await create({ mode: 'edit', model_config_id: modelConfigId.value, ...editPayload() })
    } else {
      await message({ expected_revision: assistant.value.revision, message: messageText.value.trim(), script_content: props.editContext.scriptContent, description: props.editContext.description, variables: props.editContext.variables, use_candidate: useCandidate })
    }
    messageText.value = ''
  } catch { ElMessage.error(lastError.value || '发送消息失败') }
}
const continueCandidate = async () => {
  if (!canContinue.value) return
  if (assistant.value.source_script && props.editContext.scriptContent !== assistant.value.source_script) {
    try {
      await ElMessageBox.confirm('继续调整候选会以已有候选为基线，不会包含候选生成后对编辑器作出的手工修改。若要保留手工修改，请使用“发送”。', '确认继续调整候选', { type: 'warning', confirmButtonText: '继续调整候选', cancelButtonText: '返回编辑' })
    } catch { return }
  }
  if (!messageText.value.trim()) messageText.value = '请在保留当前候选目标的前提下，继续调整候选脚本。'
  await sendMessage(true)
}
const applyToEditor = async () => {
  if (!hasCandidate.value) return
  if (assistant.value.source_script && props.editContext.scriptContent !== assistant.value.source_script) {
    ElMessage.warning('编辑器已有候选生成后的手工修改；为避免覆盖，请使用“发送”基于当前编辑器新建下一轮候选。')
    return
  }
  emit('apply-to-editor', { scriptContent: assistant.value.candidate_script, candidateHash: assistant.value.candidate_hash })
  ElMessage.success('候选已采用到编辑器；点击页面“保存”后才会写入用例。')
}
const requestVerify = async () => {
  if (!canVerify.value) return ElMessage.warning('候选正在处理、已采用或存在安全限制，不能调试验证。')
  try {
    const runtime = runtimePayload()
    await ElMessageBox.confirm('调试验证会实际运行当前候选脚本，可能写入测试数据。确认仅使用测试账号和测试数据？', '确认调试验证', { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' })
    await verify({ expected_revision: assistant.value.revision, candidate_hash: assistant.value.candidate_hash, confirm_execution: true, runtime_variables: runtime, options: { timeout: runtimeTimeout.value } })
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '启动调试验证失败') }
}
const requestApply = async () => {
  if (!canApplyRepair.value) return ElMessage.warning('候选正在处理、已采用或不符合保存条件，不能采用并保存。')
  const frozen = {
    id: assistant.value.id,
    revision: assistant.value.revision,
    candidateHash: assistant.value.candidate_hash,
    sourceEditVersion: assistant.value.source_edit_version,
    acknowledgeReview: requiresManualReview.value
  }
  try {
    if (frozen.acknowledgeReview) {
      await ElMessageBox.confirm('该候选未通过自动修复范围检查，尚未实际验证，请检查完整代码，保存不会运行。确认后将保存到测试用例；历史失败执行不会被改写。', '人工确认并保存', { type: 'warning', confirmButtonText: '人工确认并保存', cancelButtonText: '取消' })
    } else {
      await ElMessageBox.confirm('确认采用候选并保存到测试用例？历史失败执行不会被改写。', '确认采用并保存', { type: 'warning', confirmButtonText: '采用并保存', cancelButtonText: '取消' })
    }
    const current = assistant.value
    if (!current || String(current.id) !== String(frozen.id) || current.revision !== frozen.revision || current.candidate_hash !== frozen.candidateHash || current.source_edit_version !== frozen.sourceEditVersion || requiresManualReview.value !== frozen.acknowledgeReview || !canApplyRepair.value) {
      ElMessage.warning('候选已变化，请刷新后重新确认。')
      return
    }
    await apply({ expected_revision: frozen.revision, candidate_hash: frozen.candidateHash, expected_edit_version: frozen.sourceEditVersion, acknowledge_review: frozen.acknowledgeReview })
    ElMessage.success(frozen.acknowledgeReview ? '已人工确认并保存；候选未实际验证。' : '候选已采用并保存。')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '采用候选失败') }
}
const requestCancel = async () => {
  try {
    await ElMessageBox.confirm('取消会阻止助手后续步骤和迟到结果写入；已经发生的网站操作不会回滚。', '取消 AI 任务', { type: 'warning', confirmButtonText: '取消任务', cancelButtonText: '继续等待' })
    await cancel()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(lastError.value || '取消任务失败') }
}
const loadAttempt = async executionId => {
  const version = ++attemptRequestVersion
  const projectId = props.projectId
  const sessionId = assistant.value?.id
  const current = () => version === attemptRequestVersion && projectId === props.projectId && sessionId === assistant.value?.id
  attemptExecution.value = null
  try {
    const response = await getWebUITestCaseExecution(projectId, executionId)
    if (!current()) return
    const detail = response?.data ?? response
    if (!response?.success || !detail || typeof detail !== 'object' || Array.isArray(detail)) throw new Error(response?.message || '执行详情格式无效')
    attemptExecution.value = { ...detail, exec_type: 'case' }
  } catch (error) { if (current()) ElMessage.error(error?.response?.data?.message || error?.message || '加载本轮执行详情失败') }
}
</script>

<style scoped>
.assistant-panel { display:grid; gap:14px; padding:16px; border:1px solid var(--app-border); border-radius:10px; background:var(--page-content-bg); }.assistant-heading,.assistant-heading-actions,.assistant-config,.session-status,.candidate-heading,.candidate-actions,.message-actions { display:flex; align-items:center; gap:10px; }.assistant-heading { justify-content:space-between; }.assistant-heading h4,.assistant-summary h5,.candidate-section h5,.attempt-section h5,.conversation-section h5 { margin:0; }.assistant-heading p { margin:5px 0 0; color:var(--app-text-secondary); font-size:13px; }.assistant-config > * { flex:1; }.runtime-heading { display:flex; justify-content:space-between; align-items:center; }.runtime-row { display:grid; grid-template-columns:1fr 1fr auto; gap:8px; }.session-status { flex-wrap:wrap; color:var(--app-text-secondary); font-size:13px; }.assistant-summary,.candidate-section,.attempt-section,.conversation-section,.verification-options { display:grid; gap:10px; }.assistant-summary p { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; }.candidate-heading { justify-content:space-between; }.candidate-section pre { max-height:380px; overflow:auto; margin:0; padding:12px; border-radius:6px; background:var(--el-fill-color-light); white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.6; }.candidate-actions,.message-actions { justify-content:flex-end; flex-wrap:wrap; }.message-list { display:grid; gap:8px; max-height:260px; overflow:auto; }.message { padding:9px 11px; border-radius:6px; white-space:pre-wrap; overflow-wrap:anywhere; }.message.user { background:var(--el-color-primary-light-9); }.message.assistant { background:var(--el-fill-color-light); }.attempt-section { min-width:0; overflow:hidden; }.attempt-section :deep(.test-report-container) { block-size:min(480px, 52vh); min-height:0; max-block-size:min(480px, 52vh); margin-top:6px; overflow:hidden; border:1px solid var(--app-border); border-radius:8px; }.attempt-section :deep(.report-content) { block-size:100%; min-height:0; overflow:hidden; }.attempt-section :deep(.main-content) { block-size:100%; min-height:0; max-height:none; overflow-x:hidden; overflow-y:auto; } @media (max-width:700px) { .assistant-heading { align-items:flex-start; flex-direction:column; }.assistant-config,.runtime-row { grid-template-columns:1fr; display:grid; }.candidate-actions :deep(.el-button) { flex:1 1 100%; } }
</style>
