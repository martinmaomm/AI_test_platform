<template>
  <section v-if="action" class="action-required">
    <div class="action-heading">
      <div>
        <el-tag type="warning" effect="dark">需要你处理</el-tag>
        <h4>{{ action.title }}</h4>
        <p>{{ action.description }}</p>
      </div>
      <span class="attempts">还可继续 {{ action.remainingAttempts }} 次</span>
    </div>

    <div v-if="action.kind === 'clarifications'" class="question-list">
      <div v-for="(question, index) in action.questions" :key="question" class="question-item">
        <label>{{ index + 1 }}. {{ question }}</label>
        <el-input v-model="form.answers[index]" type="textarea" :rows="2" maxlength="1000" show-word-limit :disabled="resolving" placeholder="请输入明确答案" />
      </div>
    </div>

    <div v-else-if="action.kind === 'description'" class="description-editor">
      <label>修订后的完整测试描述</label>
      <el-input v-model="form.description" type="textarea" :rows="8" maxlength="2000" show-word-limit :disabled="resolving" placeholder="请写清步骤、成功标准、清理约束，并明确探索阶段只读。" />
    </div>

    <div v-if="action.kind === 'credentials'" class="credentials-editor">
      <el-alert title="登录信息仅供本次探索使用，不会写入生成记录、脚本或浏览器存储。" type="warning" :closable="false" show-icon />
      <div class="credential-grid">
        <el-input v-model="form.username" autocomplete="off" placeholder="用户名" :disabled="resolving" />
        <el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="密码" :disabled="resolving" />
      </div>
    </div>

    <el-collapse v-else-if="generation?.credentials_required" class="optional-credentials">
      <el-collapse-item title="登录信息可能已过期？可在此重新提供" name="credentials">
        <div class="credential-grid">
          <el-input v-model="form.username" autocomplete="off" placeholder="用户名（可选）" :disabled="resolving" />
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="密码（可选）" :disabled="resolving" />
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="action-footer">
      <el-button :disabled="resolving" @click="emit('cancel')">放弃本次生成</el-button>
      <el-button type="primary" :loading="resolving" :disabled="action.remainingAttempts <= 0" @click="submit">{{ action.primaryLabel }}</el-button>
    </div>
  </section>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { generationActionRequired } from '@/composables/webUIScriptGenerationPresentation'

const props = defineProps({ generation: { type: Object, default: null }, resolving: Boolean })
const emit = defineEmits(['resolve', 'cancel'])
const action = computed(() => generationActionRequired(props.generation))
const form = reactive({ description: '', answers: [], username: '', password: '' })

watch(
  () => [props.generation?.id, props.generation?.revision, props.generation?.status, props.generation?.error_code],
  () => {
    form.description = props.generation?.description_safe || ''
    form.answers = (action.value?.questions || []).map(() => '')
    form.username = ''
    form.password = ''
  },
  { immediate: true }
)

const submit = () => {
  const current = action.value
  if (!current) return
  if (current.kind === 'description' && !form.description.trim()) return ElMessage.warning('请填写修订后的完整测试描述。')
  if (current.kind === 'clarifications' && form.answers.some(item => !item.trim())) return ElMessage.warning('请逐项回答全部待确认问题。')
  if ((form.username && !form.password) || (!form.username && form.password)) return ElMessage.warning('请同时填写用户名和密码。')
  if (current.kind === 'credentials' && (!form.username || !form.password)) return ElMessage.warning('请提供本次探索用户名和密码。')

  const payload = {
    expected_status: props.generation.status,
    expected_revision: Number(props.generation.revision || 0)
  }
  if (current.kind === 'description') payload.description = form.description.trim()
  if (current.kind === 'clarifications') {
    payload.clarification_answers = current.questions.map((question, index) => ({ question, answer: form.answers[index].trim() }))
  }
  if (form.username && form.password) payload.temporary_credentials = { username: form.username, password: form.password }
  emit('resolve', payload)
}
</script>

<style scoped>
.action-required { margin-bottom: 18px; padding: 18px; border: 1px solid var(--el-color-warning-light-5); border-radius: 10px; background: var(--el-color-warning-light-9); }
.action-heading { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.action-heading h4 { margin: 10px 0 5px; color: var(--app-text-primary); font-size: 17px; }
.action-heading p { margin: 0; color: var(--app-text-secondary); font-size: 13px; line-height: 1.6; }
.attempts { flex: 0 0 auto; color: var(--app-text-secondary); font-size: 12px; }
.question-list, .description-editor, .credentials-editor { display: grid; gap: 14px; margin-top: 18px; }
.question-item { display: grid; gap: 7px; }
.question-item label, .description-editor label { color: var(--app-text-regular); font-size: 13px; font-weight: 600; }
.credential-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.optional-credentials { margin-top: 14px; }
.action-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
@media (max-width: 700px) { .action-heading { flex-direction: column; gap: 8px; }.credential-grid { grid-template-columns: 1fr; }.action-footer { flex-direction: column-reverse; }.action-footer :deep(.el-button) { width: 100%; margin-left: 0; } }
</style>
