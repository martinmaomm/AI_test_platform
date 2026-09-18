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
    <div class="shell">
      <header>
        <strong>{{ item ? "编辑压测计划" : "新建压测计划" }}</strong>
        <p>
          保存计划不会自动执行。完整
          <code v-pre>${name}</code> 保留变量原类型；嵌入文本时会转为字符串。
        </p>
      </header>
      <main>
        <el-alert
          v-if="draftWarnings.length || requiredVariables.length"
          title="探索草稿需要人工确认"
          type="warning"
          :closable="false"
          show-icon
          class="discovery-draft-warning"
        >
          <template #default>
            <p v-for="warning in draftWarnings" :key="warning">{{ warning }}</p>
            <p v-if="requiredVariables.length">
              来源样本还需要填写固定变量 JSON：
              <code v-for="variable in requiredVariables" :key="requiredVariableKey(variable)">
                ${{ "{" }}{{ variable.name }}{{ "}" }}
              </code>
              。请在下方“固定变量 JSON 值”中补齐后再保存。
            </p>
          </template>
        </el-alert>
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
        >
          <section>
            <h3>基本信息</h3>
            <div class="grid">
              <el-form-item label="计划名称" prop="name"
                ><el-input
                  v-model="form.name"
                  data-testid="plan-name" /></el-form-item
              ><el-form-item label="压测目标" prop="target_id"
                ><div class="target">
                  <el-select
                    v-model="form.target_id"
                    data-testid="plan-target"
                    :loading="targetsLoading"
                    ><el-option
                      v-for="target in targets"
                      :key="target.id"
                      :value="target.id"
                      :label="`${target.name} · ${target.base_url}`" /></el-select
                  ><el-button
                    data-testid="plan-target-refresh"
                    @click="emit('refresh-targets')"
                    >刷新</el-button
                  >
                </div>
                <p v-if="targetError" data-testid="plan-target-error">
                  {{ targetError }}
                </p>
                <p
                  v-else-if="!targetsLoading && !targets.length"
                  data-testid="plan-target-empty"
                >
                  当前项目暂无可用压测目标。
                </p></el-form-item
              ><el-form-item label="说明"
                ><el-input v-model="form.description" type="textarea"
              /></el-form-item>
            </div>
          </section>
          <section>
            <h3>负载配置</h3>
            <div class="grid">
              <el-form-item label="虚拟用户数" prop="users"
                ><el-input-number
                  v-model="form.users"
                  :min="1"
                  :max="limits.max_users" /></el-form-item
              ><el-form-item label="每秒启动用户数" prop="spawn_rate"
                ><el-input-number
                  v-model="form.spawn_rate"
                  :min="0.000001"
                  :max="limits.max_spawn_rate" /></el-form-item
              ><el-form-item label="持续时间（秒）" prop="duration_seconds"
                ><el-input-number
                  v-model="form.duration_seconds"
                  :min="1"
                  :max="limits.max_duration_seconds" /></el-form-item
              ><el-form-item label="每轮等待（秒）" prop="wait_seconds"
                ><el-input-number v-model="form.wait_seconds" :min="0"
              /></el-form-item>
            </div>
          </section>
          <section>
            <h3>变量</h3>
            <el-form-item label="固定变量 JSON 值"
              ><el-input
                v-model="form.variablesText"
                data-testid="plan-variables"
                type="textarea"
                :rows="4"
                placeholder='{"username":"测试账号"}'
              />
              <p v-if="formError.variables" class="error">
                {{ formError.variables }}
              </p></el-form-item
            >
            <div data-testid="plan-unique-variables">
              <h4>每轮唯一变量</h4>
              <div
                v-for="(item, index) in form.unique_variables"
                :key="item.id"
                class="row"
              >
                <label>名称<el-input v-model="item.name" /></label
                ><label>前缀<el-input v-model="item.prefix" /></label
                ><el-button
                  link
                  type="danger"
                  @click="form.unique_variables.splice(index, 1)"
                  >删除</el-button
                >
              </div>
              <el-button
                size="small"
                @click="
                  form.unique_variables.push({
                    id: `unique-${Date.now()}`,
                    name: '',
                    prefix: '',
                  })
                "
                >添加唯一变量</el-button
              >
            </div>
          </section>
          <section>
            <div class="heading">
              <h3>请求步骤</h3>
              <el-button size="small" @click="addStep">添加步骤</el-button>
            </div>
            <div class="steps">
              <aside data-testid="plan-step-list">
                <button
                  v-for="(step, index) in form.steps"
                  :key="step.ui_id"
                  type="button"
                  :class="{ active: selectedIndex === index }"
                  @click="selectedIndex = index"
                >
                  {{ index + 1 }}. {{ step.name || "未命名步骤" }}
                </button>
              </aside>
              <div v-if="currentStep">
                <div class="heading">
                  <strong>步骤 {{ selectedIndex + 1 }}</strong
                  ><span
                    ><el-button
                      link
                      @click="move(-1)"
                      :disabled="!selectedIndex"
                      >上移</el-button
                    ><el-button
                      link
                      @click="move(1)"
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
                      @click="removeStep"
                      :disabled="form.steps.length === 1"
                      >删除</el-button
                    ></span
                  >
                </div>
                <el-form-item label="步骤名称" :error="stepError.name"
                  ><el-input v-model="currentStep.name" data-testid="step-name"
                /></el-form-item>
                <div class="grid">
                  <el-form-item label="阶段" :error="stepError.phase"
                    ><el-select
                      v-model="currentStep.phase"
                      data-testid="step-phase"
                      ><el-option
                        label="准备（每用户一次）"
                        value="setup" /><el-option
                        label="主流程（每轮）"
                        value="main" /></el-select></el-form-item
                  ><el-form-item label="请求方法" :error="stepError.method"
                    ><el-select
                      v-model="currentStep.method"
                      data-testid="step-method"
                      ><el-option
                        v-for="method in targetMethods"
                        :key="method"
                        :value="method" /></el-select></el-form-item
                  ><el-form-item label="路径" :error="stepError.path"
                    ><el-input
                      v-model="currentStep.path"
                      data-testid="step-path"
                  /></el-form-item>
                </div>
                <p data-testid="step-url-preview">
                  最终 URL：{{ previewStepUrl(selectedTarget, currentStep) }}
                </p>
                <el-form-item label="Query 参数" :error="stepError.query"
                  ><KeyValueRows
                    v-model="currentStep.query"
                    data-testid="step-query-rows"
                    key-placeholder="参数名"
                    value-placeholder="参数值" /></el-form-item
                ><el-form-item label="Headers" :error="stepError.headers"
                  ><KeyValueRows
                    v-model="currentStep.headers"
                    data-testid="step-header-rows"
                    key-placeholder="Header 名称"
                    value-placeholder="Header 值" /></el-form-item
                ><el-form-item label="Body" :error="stepError.body"
                  ><el-radio-group
                    v-model="currentStep.bodyType"
                    data-testid="step-body-type"
                    ><el-radio label="none">无</el-radio
                    ><el-radio label="json">JSON</el-radio
                    ><el-radio label="form">Form</el-radio
                    ><el-radio label="raw">Raw</el-radio></el-radio-group
                  ><el-input
                    v-if="currentStep.bodyType !== 'none'"
                    v-model="currentStep.bodyText"
                    data-testid="step-body-json"
                    type="textarea"
                    :rows="5"
                    :placeholder="
                      currentStep.bodyType === 'raw'
                        ? '原始文本'
                        : '输入 JSON 值'
                    "
                /></el-form-item>
                <div data-testid="step-extract">
                  <h4>响应变量提取</h4>
                  <div
                    v-for="(item, index) in currentStep.extract"
                    :key="item.id"
                    class="row"
                  >
                    <label>名称<el-input v-model="item.name" /></label
                    ><label
                      >路径<el-input
                        v-model="item.check"
                        placeholder="body.data[0].id" /></label
                    ><el-button
                      link
                      type="danger"
                      @click="currentStep.extract.splice(index, 1)"
                      >删除</el-button
                    >
                  </div>
                  <el-button
                    size="small"
                    @click="currentStep.extract.push(createExtract())"
                    >添加提取</el-button
                  >
                  <p v-if="stepError.extract" class="error">
                    {{ stepError.extract }}
                  </p>
                </div>
                <div data-testid="step-assertions">
                  <h4>断言</h4>
                  <p>
                    检查字段支持
                    status_code、body、headers.Content-Type、text；expected
                    必须是 JSON 值，<code>200</code> 与
                    <code>"200"</code> 类型不同，length_gt 的 expected 应为大于
                    0 的数字。
                  </p>
                  <div
                    v-for="(item, index) in currentStep.assertions"
                    :key="item.id"
                    class="assertion"
                  >
                    <label>检查<el-input v-model="item.check" /></label
                    ><label
                      >比较器<el-select v-model="item.comparator"
                        ><el-option
                          v-for="option in comparators"
                          :key="option"
                          :value="option" /></el-select></label
                    ><label
                      >expected(JSON)<el-input
                        v-model="item.expectedText" /></label
                    ><el-button
                      link
                      type="danger"
                      @click="currentStep.assertions.splice(index, 1)"
                      >删除</el-button
                    >
                  </div>
                  <el-button
                    size="small"
                    @click="currentStep.assertions.push(createAssertion())"
                    >添加断言</el-button
                  >
                  <p v-if="stepError.assertions" class="error">
                    {{ stepError.assertions }}
                  </p>
                </div>
              </div>
            </div>
          </section>
        </el-form>
      </main>
      <footer data-testid="performance-plan-editor-footer">
        <el-button @click="requestClose">取消</el-button
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
import { computed, nextTick, reactive, ref, watch } from "vue";
import KeyValueRows from "./KeyValueRows.vue";
import {
  clonePlanStep,
  createAssertion,
  createExtract,
  createPlanStep,
  jsonErrorMessage,
  previewStepUrl,
  serializePlanSteps,
} from "../performancePlanEditorState";
const props = defineProps({
  visible: Boolean,
  item: Object,
  plan: Object,
  currentTarget: Object,
  draftWarnings: { type: Array, default: () => [] },
  draftSource: { type: Object, default: null },
  targets: { type: Array, default: () => [] },
  targetsLoading: Boolean,
  targetError: String,
  limits: { type: Object, required: true },
  saving: Boolean,
});
const emit = defineEmits([
  "save",
  "request-close",
  "closed",
  "refresh-targets",
]);
const formRef = ref();
const selectedIndex = ref(0);
const errors = ref([]);
const formError = reactive({ variables: "" });
const initialSnapshot = ref("");
const comparators = [
  "eq",
  "ne",
  "contains",
  "not_contains",
  "gt",
  "ge",
  "lt",
  "le",
  "type",
  "length",
  "length_gt",
  "exists",
];
const blank = () => ({
  name: "",
  description: "",
  target_id: props.currentTarget?.id || null,
  users: 1,
  spawn_rate: 1,
  duration_seconds: 30,
  wait_seconds: 1,
  variablesText: "{}",
  unique_variables: [],
  steps: [createPlanStep()],
});
const form = reactive(blank());
const requiredVariables = computed(
  () => props.draftSource?.required_variables || [],
);
const requiredVariableKey = (variable) =>
  `${variable.name || "variable"}-${variable.record_id || variable.location || ""}`;
const snapshot = () => JSON.stringify(form);
const rules = {
  name: [
    {
      required: true,
      whitespace: true,
      message: "请输入计划名称",
      trigger: "blur",
    },
  ],
  target_id: [{ required: true, message: "请选择压测目标", trigger: "change" }],
};
const currentStep = computed(() => form.steps[selectedIndex.value]);
const stepError = computed(() => errors.value[selectedIndex.value] || {});
const selectedTarget = computed(
  () =>
    props.targets.find(
      (target) => String(target.id) === String(form.target_id),
    ) || props.currentTarget,
);
const targetMethods = computed(
  () => selectedTarget.value?.allowed_methods || [],
);
const saveDisabled = computed(
  () =>
    props.saving ||
    props.targetsLoading ||
    Boolean(props.targetError) ||
    !selectedTarget.value,
);
function reset() {
  const source = props.plan || props.item;
  Object.assign(
    form,
    source
      ? {
          ...blank(),
          ...source,
          variablesText: JSON.stringify(source.variables || {}, null, 2),
          unique_variables: (source.unique_variables || []).map(
            (item, index) => ({
              id: `unique-${index}`,
              name: item.name || "",
              prefix: item.prefix || "",
            }),
          ),
          steps: (source.steps || []).map(createPlanStep),
        }
      : blank(),
  );
  selectedIndex.value = 0;
  errors.value = [];
  formError.variables = "";
  initialSnapshot.value = snapshot();
}
watch(
  () => props.visible,
  async (open) => {
    if (open) {
      reset();
      emit("refresh-targets");
      await nextTick();
    }
  },
);
const addStep = () => {
  if (form.steps.length < (props.limits.max_steps || 20)) {
    form.steps.push(
      createPlanStep({ method: targetMethods.value[0] || "GET" }),
    );
    selectedIndex.value = form.steps.length - 1;
  }
};
const move = (offset) => {
  const next = selectedIndex.value + offset;
  if (next < 0 || next >= form.steps.length) return;
  const [step] = form.steps.splice(selectedIndex.value, 1);
  form.steps.splice(next, 0, step);
  selectedIndex.value = next;
};
const copyStep = () => {
  if (form.steps.length >= (props.limits.max_steps || 20)) return;
  form.steps.splice(
    selectedIndex.value + 1,
    0,
    clonePlanStep(currentStep.value),
  );
  selectedIndex.value += 1;
  errors.value = [];
};
const removeStep = () => {
  if (form.steps.length > 1) {
    form.steps.splice(selectedIndex.value, 1);
    selectedIndex.value = Math.min(selectedIndex.value, form.steps.length - 1);
  }
};
async function save() {
  if (saveDisabled.value) return;
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;
  let variables;
  try {
    variables = JSON.parse(form.variablesText);
  } catch (error) {
    formError.variables = jsonErrorMessage(form.variablesText, error);
    return;
  }
  if (!variables || Array.isArray(variables) || typeof variables !== "object") {
    formError.variables = "固定变量必须是 JSON 对象。";
    return;
  }
  formError.variables = "";
  const serialized = serializePlanSteps(form.steps, targetMethods.value);
  errors.value = serialized.errors;
  const invalid = errors.value.findIndex(Boolean);
  if (invalid >= 0) {
    selectedIndex.value = invalid;
    return;
  }
  emit("save", {
    name: form.name.trim(),
    description: form.description.trim(),
    target_id: form.target_id,
    users: form.users,
    spawn_rate: form.spawn_rate,
    duration_seconds: form.duration_seconds,
    wait_seconds: form.wait_seconds,
    variables,
    unique_variables: form.unique_variables.map(({ name, prefix }) => ({
      name: name.trim(),
      prefix: prefix.trim(),
    })),
    steps: serialized.value,
  });
}
const requestClose = () => {
  if (!props.saving)
    emit("request-close", snapshot() !== initialSnapshot.value);
};
</script>
<style scoped>
.performance-plan-drawer {
  color: var(--app-text-primary);
  background: var(--page-content-bg);
  --el-fill-color-blank: var(--page-content-bg);
  --el-bg-color-overlay: var(--page-content-bg);
}
.shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.shell > header,
.shell > footer {
  flex: 0 0 auto;
  padding: 16px 24px;
}
.shell > main {
  padding: 20px 24px;
  overflow: auto;
  flex: 1;
  min-height: 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.target,
.heading,
.row,
.assertion {
  display: flex;
  gap: 8px;
  align-items: center;
}
.target {
  width: 100%;
}
.target > *:first-child {
  flex: 1;
}
.heading {
  justify-content: space-between;
}
.steps {
  display: grid;
  grid-template-columns: 170px 1fr;
  gap: 16px;
}
.steps aside {
  display: grid;
  align-content: start;
  gap: 4px;
}
.steps aside button {
  text-align: left;
  border: 0;
  padding: 8px;
  background: transparent;
  color: inherit;
}
.steps aside .active {
  background: var(--el-color-primary-light-9);
}
.row label,
.assertion label {
  flex: 1;
}
.error {
  color: var(--el-color-danger);
  font-size: 12px;
}
.shell > footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--app-border-light);
}
@media (max-width: 700px) {
  .grid,
  .steps {
    grid-template-columns: 1fr;
  }
  .assertion {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
