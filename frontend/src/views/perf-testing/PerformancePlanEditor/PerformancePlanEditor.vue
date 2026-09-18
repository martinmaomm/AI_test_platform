<template>
  <el-drawer
    class="performance-plan-drawer"
    data-testid="performance-plan-editor"
    :model-value="visible"
    size="min(1080px, 96vw)"
    direction="rtl"
    :with-header="false"
    :close-on-click-modal="false"
    :before-close="requestClose"
    @closed="emit('closed')"
  >
    <div class="plan-editor-shell">
      <header class="editor-header">
        <div class="editor-title">
          <strong>{{ item ? '编辑压测计划' : '新建压测计划' }}</strong
          ><el-button
            text
            aria-label="关闭计划编辑器"
            :disabled="saving"
            @click="requestClose"
            >关闭</el-button
          >
        </div>
        <p>保存计划不会自动执行。</p>
      </header>

      <main class="editor-body">
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
        >
          <section>
            <h3>基本信息</h3>
            <div class="form-grid">
              <el-form-item label="计划名称" prop="name">
                <el-input
                  v-model="form.name"
                  data-testid="plan-name"
                  maxlength="100"
                />
              </el-form-item>
              <el-form-item label="压测目标" prop="target_id">
                <div class="target-select">
                  <el-select
                    v-model="form.target_id"
                    data-testid="plan-target"
                    filterable
                    :loading="targetsLoading"
                    placeholder="选择名称和 URL"
                  >
                    <el-option
                      v-for="target in targets"
                      :key="target.id"
                      :label="`${target.name} · ${target.base_url}`"
                      :value="target.id"
                    />
                  </el-select>
                  <el-button
                    data-testid="plan-target-refresh"
                    :loading="targetsLoading"
                    @click="refreshTargets"
                    >刷新</el-button
                  >
                </div>
                <div
                  v-if="targetError"
                  data-testid="plan-target-error"
                  class="target-state"
                >
                  {{ targetError }}
                  <el-button link @click="refreshTargets">重试</el-button>
                </div>
                <div
                  v-else-if="!targetsLoading && !targets.length"
                  data-testid="plan-target-empty"
                  class="target-state"
                >
                  当前项目暂无可用压测目标。
                </div>
              </el-form-item>
              <el-form-item label="说明"
                ><el-input
                  v-model="form.description"
                  type="textarea"
                  :rows="2"
                  maxlength="500"
              /></el-form-item>
            </div>
          </section>

          <section>
            <h3>负载配置</h3>
            <div class="form-grid load-grid">
              <el-form-item prop="users"
                ><template #label
                  >虚拟用户数
                  <el-tooltip content="同时运行的虚拟用户数量。"
                    ><span class="help">?</span></el-tooltip
                  ></template
                ><el-input-number
                  v-model="form.users"
                  :min="1"
                  :max="limits.max_users"
              /></el-form-item>
              <el-form-item prop="spawn_rate"
                ><template #label
                  >每秒启动用户数
                  <el-tooltip content="每秒启动的虚拟用户数，不等于每秒请求数。"
                    ><span class="help">?</span></el-tooltip
                  ></template
                ><el-input-number
                  v-model="form.spawn_rate"
                  :min="0.000001"
                  :max="limits.max_spawn_rate"
              /></el-form-item>
              <el-form-item prop="duration_seconds"
                ><template #label
                  >持续时间（秒）
                  <el-tooltip content="本次压测持续的总时间。"
                    ><span class="help">?</span></el-tooltip
                  ></template
                ><el-input-number
                  v-model="form.duration_seconds"
                  :min="1"
                  :max="limits.max_duration_seconds"
              /></el-form-item>
              <el-form-item prop="wait_seconds"
                ><template #label
                  >每轮等待时间（秒）
                  <el-tooltip content="每轮全部步骤执行后的等待时间。"
                    ><span class="help">?</span></el-tooltip
                  ></template
                ><el-input-number
                  v-model="form.wait_seconds"
                  :min="0.1"
                  :max="60"
                  :step="0.1"
              /></el-form-item>
            </div>
          </section>

          <section>
            <div class="steps-heading">
              <h3>请求步骤</h3>
              <el-button
                plain
                size="small"
                :disabled="form.steps.length >= limits.max_steps"
                @click="addStep"
                >添加步骤</el-button
              >
            </div>
            <div class="steps-layout">
              <aside data-testid="plan-step-list" class="step-list">
                <button
                  v-for="(step, index) in form.steps"
                  :key="step.ui_id"
                  type="button"
                  :class="{ active: selectedIndex === index }"
                  @click="selectedIndex = index"
                >
                  {{ index + 1 }}. {{ step.name || '未命名步骤' }}
                </button>
              </aside>
              <div v-if="currentStep" class="step-editor">
                <div class="step-actions">
                  <strong>步骤 {{ selectedIndex + 1 }}</strong
                  ><span
                    ><el-button
                      link
                      @click="moveStep(-1)"
                      :disabled="selectedIndex === 0"
                      >上移</el-button
                    ><el-button
                      link
                      @click="moveStep(1)"
                      :disabled="selectedIndex === form.steps.length - 1"
                      >下移</el-button
                    ><el-button
                      link
                      @click="copyStep"
                      :disabled="form.steps.length >= limits.max_steps"
                      >复制</el-button
                    ><el-button
                      link
                      type="danger"
                      :disabled="form.steps.length === 1"
                      @click="deleteStep"
                      >删除</el-button
                    ></span
                  >
                </div>
                <el-form-item label="步骤名称" :error="stepError.name"
                  ><el-input v-model="currentStep.name" data-testid="step-name"
                /></el-form-item>
                <div class="step-grid">
                  <el-form-item label="请求方法" :error="stepError.method"
                    ><el-select
                      v-model="currentStep.method"
                      data-testid="step-method"
                      ><el-option
                        v-for="method in targetMethods"
                        :key="method"
                        :label="method"
                        :value="method" /></el-select
                  ></el-form-item>
                  <el-form-item label="路径" :error="stepError.path"
                    ><el-input
                      v-model="currentStep.path"
                      data-testid="step-path"
                      placeholder="/relative-path"
                      @blur="splitCurrentPathQuery"
                  /></el-form-item>
                  <el-form-item
                    label="预期 HTTP 状态码"
                    :error="stepError.status"
                    ><el-input-number
                      v-model="currentStep.expected_status"
                      data-testid="step-expected-status"
                      :min="100"
                      :max="599"
                  /></el-form-item>
                </div>
                <p data-testid="step-url-preview" class="url-preview">
                  最终 URL：{{ previewStepUrl(selectedTarget, currentStep) }}
                </p>
                <el-form-item label="Query 参数" :error="stepError.query"
                  ><KeyValueRows
                    v-model="currentStep.query"
                    data-testid="step-query-rows"
                    key-placeholder="Query 参数名"
                    value-placeholder="Query 参数值"
                /></el-form-item>
                <el-form-item label="Headers" :error="stepError.headers"
                  ><KeyValueRows
                    v-model="currentStep.headers"
                    data-testid="step-header-rows"
                    key-placeholder="Header 名称"
                    value-placeholder="Header 值"
                /></el-form-item>
                <el-form-item label="Body" :error="stepError.body">
                  <el-radio-group v-model="currentStep.bodyMode"
                    ><el-radio label="none">无</el-radio
                    ><el-radio label="json">JSON</el-radio></el-radio-group
                  >
                  <template v-if="currentStep.bodyMode === 'json'"
                    ><el-input
                      v-model="currentStep.bodyText"
                      data-testid="step-body-json"
                      type="textarea"
                      :rows="7"
                      placeholder="输入 JSON"
                    /><el-button link @click="formatBody"
                      >格式化 JSON</el-button
                    ></template
                  >
                </el-form-item>
              </div>
            </div>
          </section>
        </el-form>
      </main>

      <footer
        data-testid="performance-plan-editor-footer"
        class="drawer-footer"
      >
        <el-button :disabled="saving" @click="requestClose">取消</el-button
        ><el-button
          data-testid="plan-save"
          type="primary"
          :loading="saving"
          :disabled="saveDisabled"
          @click="save"
          >保存</el-button
        >
      </footer>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import KeyValueRows from './KeyValueRows.vue'
import {
  clonePlanStep,
  createPlanStep,
  formatJsonText,
  jsonErrorMessage,
  parsePathAndQuery,
  previewStepUrl,
  serializePlanSteps,
} from '../performancePlanEditorState'

const props = defineProps({
  visible: Boolean,
  item: Object,
  targets: { type: Array, default: () => [] },
  targetsLoading: Boolean,
  targetError: String,
  limits: { type: Object, required: true },
  saving: Boolean,
})
const emit = defineEmits(['save', 'request-close', 'closed', 'refresh-targets'])
const formRef = ref()
const selectedIndex = ref(0)
const errors = ref([])
const initialSnapshot = ref('')
const blank = () => ({
  name: '',
  description: '',
  target_id: null,
  users: 1,
  spawn_rate: 1,
  duration_seconds: 30,
  wait_seconds: 1,
  steps: [createPlanStep()],
})
const form = reactive(blank())
const numericRule = (label, minimum, maximum, integer = false) => ({
  validator: (_, value, callback) => {
    const valid =
      typeof value === 'number' &&
      Number.isFinite(value) &&
      value >= minimum &&
      value <= maximum() &&
      (!integer || Number.isInteger(value))
    callback(
      valid
        ? undefined
        : new Error(
            `${label}须为 ${minimum} 到 ${maximum()} 的${integer ? '整数' : '数值'}。`,
          ),
    )
  },
  trigger: 'blur',
})
const rules = {
  name: [
    {
      required: true,
      whitespace: true,
      message: '请输入计划名称',
      trigger: 'blur',
    },
  ],
  target_id: [{ required: true, message: '请选择压测目标', trigger: 'change' }],
  users: [numericRule('虚拟用户数', 1, () => props.limits.max_users, true)],
  spawn_rate: [
    numericRule('每秒启动用户数', 0.000001, () => props.limits.max_spawn_rate),
  ],
  duration_seconds: [
    numericRule('持续时间', 1, () => props.limits.max_duration_seconds, true),
  ],
  wait_seconds: [numericRule('每轮等待时间', 0.1, () => 60)],
}
const snapshot = () => JSON.stringify(form)
const currentStep = computed(() => form.steps[selectedIndex.value])
const stepError = computed(() => errors.value[selectedIndex.value] || {})
const selectedTarget = computed(() =>
  props.targets.find((target) => String(target.id) === String(form.target_id)),
)
const targetMethods = computed(
  () => selectedTarget.value?.allowed_methods || [],
)
const saveDisabled = computed(
  () =>
    props.saving ||
    props.targetsLoading ||
    Boolean(props.targetError) ||
    !selectedTarget.value,
)
const refreshTargets = () => emit('refresh-targets')
const reset = () => {
  Object.assign(
    form,
    props.item
      ? { ...props.item, steps: (props.item.steps || []).map(createPlanStep) }
      : blank(),
  )
  selectedIndex.value = 0
  errors.value = []
  formRef.value?.clearValidate()
  initialSnapshot.value = snapshot()
}
watch(
  () => props.visible,
  async (open) => {
    if (open) {
      reset()
      emit('refresh-targets')
      await nextTick()
    }
  },
)
const splitCurrentPathQuery = () => {
  if (!currentStep.value?.path.includes('?')) return
  const parsed = parsePathAndQuery(currentStep.value.path)
  currentStep.value.path = parsed.path
  currentStep.value.query.push(...parsed.query)
}
const addStep = () => {
  if (form.steps.length >= props.limits.max_steps) return
  form.steps.push(createPlanStep({ method: targetMethods.value[0] || 'GET' }))
  selectedIndex.value = form.steps.length - 1
  errors.value = []
}
const deleteStep = () => {
  if (form.steps.length > 1) {
    form.steps.splice(selectedIndex.value, 1)
    selectedIndex.value = Math.min(selectedIndex.value, form.steps.length - 1)
    errors.value = []
  }
}
const copyStep = () => {
  if (form.steps.length >= props.limits.max_steps) return
  form.steps.splice(
    selectedIndex.value + 1,
    0,
    clonePlanStep(currentStep.value),
  )
  selectedIndex.value += 1
  errors.value = []
}
const moveStep = (offset) => {
  const next = selectedIndex.value + offset
  if (next >= 0 && next < form.steps.length) {
    const [step] = form.steps.splice(selectedIndex.value, 1)
    form.steps.splice(next, 0, step)
    selectedIndex.value = next
    errors.value = []
  }
}
const formatBody = () => {
  try {
    currentStep.value.bodyText = formatJsonText(currentStep.value.bodyText)
    errors.value[selectedIndex.value] = { ...stepError.value, body: undefined }
  } catch (error) {
    errors.value[selectedIndex.value] = {
      ...stepError.value,
      body: jsonErrorMessage(currentStep.value.bodyText, error),
    }
  }
}
const save = async () => {
  if (saveDisabled.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const serialized = serializePlanSteps(form.steps, targetMethods.value)
  errors.value = serialized.errors
  const firstError = errors.value.findIndex(Boolean)
  if (firstError >= 0) {
    selectedIndex.value = firstError
    return
  }
  emit('save', {
    name: form.name.trim(),
    description: form.description?.trim() || '',
    target_id: form.target_id,
    users: form.users,
    spawn_rate: form.spawn_rate,
    duration_seconds: form.duration_seconds,
    wait_seconds: form.wait_seconds,
    steps: serialized.value,
  })
}
const requestClose = () => {
  if (!props.saving) emit('request-close', snapshot() !== initialSnapshot.value)
}
</script>

<style scoped>
.performance-plan-drawer {
  color: var(--app-text-primary);
  background: var(--page-content-bg);
  --el-fill-color-blank: var(--page-content-bg);
  --el-bg-color-overlay: var(--page-content-bg);
}
.editor-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.url-preview {
  overflow-wrap: anywhere;
}
.editor-body :deep(.el-radio-group) {
  width: 100%;
  margin-bottom: 8px;
}
.plan-editor-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.editor-header,
.drawer-footer {
  flex: 0 0 auto;
  padding: 0 24px;
}
.editor-header {
  padding-top: 4px;
}
.editor-header p,
.target-state,
.url-preview {
  color: var(--app-text-muted);
  font-size: 13px;
  margin: 6px 0;
}
.editor-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 20px 24px;
}
.editor-body section {
  margin-bottom: 28px;
}
.editor-body h3 {
  margin: 0 0 14px;
}
.form-grid,
.step-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.load-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.target-select {
  display: flex;
  width: 100%;
  gap: 8px;
}
.target-select .el-select {
  flex: 1;
  min-width: 0;
}
.help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid currentColor;
  border-radius: 50%;
  font-size: 11px;
  cursor: help;
}
.steps-heading,
.step-actions,
.drawer-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.steps-layout {
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
  min-height: 430px;
  border: 1px solid var(--app-border-light);
  border-radius: 8px;
}
.step-list {
  border-right: 1px solid var(--app-border-light);
  padding: 8px;
}
.step-list button {
  display: block;
  width: 100%;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  padding: 9px;
  border-radius: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.step-list button.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.step-editor {
  padding: 16px;
  min-width: 0;
}
.drawer-footer {
  border-top: 1px solid var(--app-border-light);
  padding-top: 12px;
  padding-bottom: 12px;
  justify-content: flex-end;
  gap: 8px;
}
@media (max-width: 760px) {
  .form-grid,
  .load-grid,
  .step-grid,
  .steps-layout {
    display: block;
  }
  .step-list {
    border-right: 0;
    border-bottom: 1px solid var(--app-border-light);
    white-space: nowrap;
    overflow: auto;
  }
  .step-list button {
    display: inline-block;
    width: auto;
  }
  .editor-body,
  .editor-header,
  .drawer-footer {
    padding-left: 14px;
    padding-right: 14px;
  }
}
</style>
