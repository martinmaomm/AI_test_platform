<template>
  <div class="workspace">
    <el-alert type="info" :closable="false" show-icon title="生成完成仅表示已得到脚本草稿；只有“本版调试通过”才表示该版本在约定测试数据上实际执行成功。" />

    <div class="workspace-status">
      <div>
        <h5>当前草稿</h5>
        <p>版本 {{ draft?.revision ?? workspace.revision }}。{{ draft?.dirty ? '有未保存的本地修改。' : '已与工作区同步。' }}</p>
      </div>
      <el-tag :type="workspaceVerificationTagType(displayVerificationStatus)" effect="plain">{{ displayVerificationLabel }}</el-tag>
    </div>

    <section class="workspace-section script-section">
      <div class="section-heading"><div><h5>Python Playwright 脚本草稿</h5><p>可直接编辑；拖动右下角调整高度。保存或调试前会校验并保存当前草稿。</p></div><el-button size="small" @click="copyScript">复制脚本</el-button></div>
      <div class="script-editor"><MonacoEditor :value="form.script_draft" language="python" theme="vs-dark" :read-only="busy" height="100%" @update:value="updateScript" /></div>
    </section>

    <section class="workspace-section">
      <div class="section-heading"><div><h5>配置变量</h5><p>敏感默认值只留在本次页面内存；刷新后需要重新输入。调试覆盖值也只用于本次执行。</p></div><el-button size="small" plain :disabled="busy" @click="addVariable">添加变量</el-button></div>
      <el-table :data="form.variables" size="small" empty-text="暂无变量">
        <el-table-column label="变量名" min-width="145"><template #default="{ row }"><el-input v-model="row.name" :disabled="busy" placeholder="UI_TEST_USERNAME" @input="emitDraft" /></template></el-table-column>
        <el-table-column label="默认值" min-width="160"><template #default="{ row }"><el-input v-model="row.value" :type="row.is_secret ? 'password' : 'text'" show-password :disabled="busy" @input="emitDraft" /></template></el-table-column>
        <el-table-column label="说明" min-width="160"><template #default="{ row }"><el-input v-model="row.description" :disabled="busy" @input="emitDraft" /></template></el-table-column>
        <el-table-column label="必填" width="68"><template #default="{ row }"><el-switch v-model="row.required" :disabled="busy" @change="emitDraft" /></template></el-table-column>
        <el-table-column label="敏感" width="68"><template #default="{ row }"><el-switch v-model="row.is_secret" :disabled="busy" @change="emitDraft" /></template></el-table-column>
        <el-table-column width="64"><template #default="{ $index }"><el-button text type="danger" :disabled="busy" @click="removeVariable($index)">删除</el-button></template></el-table-column>
      </el-table>
    </section>

    <el-collapse class="runtime-variables">
      <el-collapse-item title="本次调试变量（可选）" name="runtime">
        <p>这里的值不会保存到草稿或浏览器存储，仅在本次真实调试请求中发送。</p>
        <el-table :data="runtimeVariables" size="small" empty-text="先在上方配置变量">
          <el-table-column prop="name" label="变量" min-width="160" />
          <el-table-column label="本次覆盖值" min-width="220"><template #default="{ row }"><el-input v-model="row.value" :type="row.is_secret ? 'password' : 'text'" show-password :disabled="busy" /></template></el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <div class="workspace-actions">
      <el-button :loading="draftSaving" :disabled="busy || !canSaveDraft" @click="emit('save-draft')">保存草稿</el-button>
      <el-button type="warning" :loading="debugging" :disabled="busy || !canDebug" @click="requestDebug">真实调试</el-button>
    </div>

    <el-alert v-if="verification.message || verification.error_message" class="verification-message" type="warning" :closable="false" show-icon :title="verification.message || verification.error_message" />

    <section v-if="debugExecution || debugExecutionLoading" class="workspace-section execution-section">
      <div class="section-heading"><div><h5>调试详情</h5><p>失败原因、失败截图和原始日志仅在服务端实际提供时展示。</p></div></div>
      <el-skeleton v-if="debugExecutionLoading && !debugExecution" :rows="5" animated />
      <WebUITestCaseExecutionDetail v-else-if="debugExecution" :execution="debugExecution" />
    </section>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MonacoEditor from '@/components/MonacoEditor.vue'
import WebUITestCaseExecutionDetail from '@/components/WebUITestCaseExecutionDetail.vue'
import { isCurrentRevisionVerified, workspaceVerificationLabel, workspaceVerificationTagType } from '@/composables/webUIScriptGenerationPresentation'

const props = defineProps({
  generation: { type: Object, default: null }, draft: { type: Object, default: null }, busy: Boolean,
  draftSaving: Boolean, debugging: Boolean,
  debugExecution: { type: Object, default: null }, debugExecutionLoading: Boolean
})
const emit = defineEmits(['update-draft', 'save-draft', 'debug'])
const form = reactive({ script_draft: '', variables: [] })
const runtimeVariables = reactive([])
const workspace = computed(() => props.generation?.workspace || { revision: 0, verification: {}, repair: {} })
const verification = computed(() => workspace.value.verification || {})
const hasPassed = computed(() => !props.draft?.dirty && isCurrentRevisionVerified(
  workspace.value, props.draft?.revision ?? workspace.value.revision, props.generation?.environment_id
))
const displayVerificationStatus = computed(() => verification.value.status === 'passed' && !hasPassed.value ? 'unverified' : verification.value.status)
const displayVerificationLabel = computed(() => verification.value.status === 'passed' && !hasPassed.value
  ? '本地修改尚未调试'
  : workspaceVerificationLabel(displayVerificationStatus.value))
const canSaveDraft = computed(() => Boolean(form.script_draft.trim()) && !form.variables.some(item => !item.name.trim()))
const canDebug = computed(() => canSaveDraft.value && !props.draftSaving)

const copyVariables = (variables) => (variables || []).map(item => ({
  name: item?.name || '', value: item?.value || '', is_secret: Boolean(item?.is_secret),
  required: Boolean(item?.required), description: item?.description || ''
}))
const reset = () => {
  form.script_draft = props.draft?.script_draft || ''
  form.variables = copyVariables(props.draft?.variables)
  runtimeVariables.splice(0, runtimeVariables.length, ...form.variables.map(item => ({ name: item.name, value: '', is_secret: item.is_secret })))
}
// Do not watch `dirty`: the first edit changes it and must not reset the
// editor or this run's in-memory runtime-variable overrides.
watch(() => [props.draft?.generationId, props.draft?.revision], reset, { immediate: true })
watch(() => form.variables, () => {
  const existing = new Map(runtimeVariables.map(item => [item.name, item.value]))
  runtimeVariables.splice(0, runtimeVariables.length, ...form.variables
    .filter(item => item.name)
    .map(item => ({ name: item.name, value: existing.get(item.name) || '', is_secret: item.is_secret })))
}, { deep: true })
const emitDraft = () => emit('update-draft', {
  generationId: props.generation?.id,
  revision: props.draft?.revision,
  script_draft: form.script_draft,
  variables: copyVariables(form.variables)
})
const updateScript = (value) => { form.script_draft = value || ''; emitDraft() }
const addVariable = () => { form.variables.push({ name: '', value: '', is_secret: false, required: false, description: '' }); emitDraft() }
const removeVariable = (index) => { form.variables.splice(index, 1); emitDraft() }
const copyScript = async () => { try { await navigator.clipboard.writeText(form.script_draft); ElMessage.success('脚本已复制') } catch { ElMessage.error('复制失败，请手动复制脚本') } }
const requestDebug = async () => {
  if (!canDebug.value) return ElMessage.warning('请先填写有效脚本和变量名。')
  try {
    await ElMessageBox.confirm('将真实执行当前脚本，并只允许操作约定的测试数据。系统不会自动重试业务写操作。是否确认继续？', '确认真实调试', { type: 'warning', confirmButtonText: '确认真实执行', cancelButtonText: '取消' })
    const values = runtimeVariables.filter(item => item.name && item.value !== '').map(item => ({ name: item.name, value: item.value }))
    emit('debug', values)
    runtimeVariables.forEach(item => { item.value = '' })
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('调试确认失败，请重试。')
  }
}
</script>

<style scoped>
.workspace { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0; max-width: 100%; }.workspace-status, .section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; min-width: 0; }.workspace-status h5, .section-heading h5 { margin: 0; color: var(--app-text-primary); font-size: 14px; }.workspace-status p, .section-heading p, .runtime-variables p { margin: 5px 0 0; color: var(--app-text-secondary); font-size: 13px; line-height: 1.6; }.workspace-section, .script-section, .script-editor, .runtime-variables { min-width: 0; max-width: 100%; }.workspace-section { padding: 16px; border: 1px solid var(--app-border); border-radius: 8px; }.script-editor { height: clamp(520px, 64vh, 820px); min-height: 420px; resize: vertical; overflow: hidden; margin-top: 12px; }.runtime-variables { margin-top: -4px; }.workspace-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }.verification-message { margin-top: -4px; }.execution-section { padding: 0; overflow: hidden; }.execution-section > .section-heading { padding: 16px 16px 0; }.workspace :deep(.el-table) { width: 100%; max-width: 100%; }.workspace :deep(.el-table__body-wrapper), .workspace :deep(.el-scrollbar__wrap) { overflow-x: auto; }.workspace :deep(.monaco-editor-container) { min-width: 0; max-width: 100%; } @media (max-width: 640px) { .workspace-status, .section-heading { flex-direction: column; }.workspace-actions :deep(.el-button) { flex: 1 1 100%; margin-left: 0; } }
</style>
