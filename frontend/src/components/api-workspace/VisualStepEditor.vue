<template>
  <el-card shadow="never" class="step-editor">
    <template #header>
      <div class="step-heading">
        <strong>步骤 {{ index + 1 }}</strong>
        <el-button
          text
          type="danger"
          :disabled="disabled"
          @click="$emit('remove')"
          >删除步骤</el-button
        >
      </div>
    </template>
    <el-form label-position="top" size="small">
      <el-form-item label="步骤名称"
        ><el-input
          :model-value="step.name"
          :disabled="disabled"
          @update:model-value="patch({ name: $event })"
      /></el-form-item>
      <el-form-item label="关联 API 端点">
        <el-select
          :model-value="step.endpoint_id || null"
          clearable
          filterable
          :disabled="disabled"
          placeholder="可选：从已选端点关联"
          style="width: 100%"
          @update:model-value="patch({ endpoint_id: $event || undefined })"
        >
          <el-option
            v-for="endpoint in endpoints"
            :key="endpoint.id"
            :value="endpoint.id"
            :label="endpointLabel(endpoint)"
          />
        </el-select>
      </el-form-item>
      <div class="request-line">
        <el-form-item label="方法"
          ><el-select
            :model-value="step.request.method"
            :disabled="disabled"
            @update:model-value="patchRequest({ method: $event })"
            ><el-option
              v-for="method in methods"
              :key="method"
              :value="method" /></el-select
        ></el-form-item>
        <el-form-item label="URL"
          ><el-input
            :model-value="step.request.url"
            :disabled="disabled"
            placeholder="/login"
            @update:model-value="patchRequest({ url: $event })"
        /></el-form-item>
      </div>
      <el-collapse>
        <el-collapse-item title="请求参数与请求头" name="request">
          <el-form-item label="Query 参数"
            ><KeyValueRows
              :model-value="step.request.params"
              :disabled="disabled"
              key-placeholder="参数"
              value-placeholder="值"
              @update:model-value="patchRequest({ params: $event })"
          /></el-form-item>
          <el-form-item label="Headers"
            ><KeyValueRows
              :model-value="step.request.headers"
              :disabled="disabled"
              key-placeholder="Header"
              value-placeholder="值"
              @update:model-value="patchRequest({ headers: $event })"
          /></el-form-item>
        </el-collapse-item>
        <el-collapse-item title="请求体" name="body">
          <el-radio-group
            :model-value="currentBodyKind"
            :disabled="disabled"
            @update:model-value="setBodyKind"
          >
            <el-radio-button label="none">无</el-radio-button
            ><el-radio-button label="json">JSON</el-radio-button
            ><el-radio-button label="form">表单</el-radio-button
            ><el-radio-button label="raw">原始文本</el-radio-button>
          </el-radio-group>
          <el-input
            v-if="currentBodyKind === 'json'"
            type="textarea"
            :rows="5"
            :model-value="jsonText"
            :disabled="disabled"
            placeholder='{"username":"demo"}'
            @update:model-value="setJson"
          />
          <KeyValueRows
            v-else-if="currentBodyKind === 'form'"
            :model-value="step.request.data"
            :disabled="disabled"
            key-placeholder="字段"
            value-placeholder="值"
            @update:model-value="patchRequest({ data: $event })"
          />
          <el-input
            v-else-if="currentBodyKind === 'raw'"
            type="textarea"
            :rows="4"
            :model-value="step.request.raw"
            :disabled="disabled"
            placeholder="原始请求体"
            @update:model-value="patchRequest({ raw: $event })"
          />
        </el-collapse-item>
        <el-collapse-item title="提取与断言" name="check">
          <el-form-item label="提取变量"
            ><KeyValueRows
              :model-value="step.extract"
              :disabled="disabled"
              key-placeholder="变量名"
              value-placeholder="如 body.data.token"
              @update:model-value="patch({ extract: $event })"
          /></el-form-item>
          <p class="selector-hint">
            按唯一名称提取示例：<code>{{ selectorExample }}</code>。
            unique_name 需先在变量中定义。提取必须恰好匹配一条；零条或多条会停止，不会默认选第一条。
            删除后可对筛选结果使用“长度等于 0”断言，无需再提取 ID。
          </p>
          <div class="assertion-title">断言</div>
          <div
            v-for="(assertion, assertionIndex) in assertions"
            :key="assertionIndex"
            class="assertion-row"
          >
            <el-select
              :model-value="assertion.operator"
              :disabled="disabled"
              @update:model-value="
                updateAssertion(assertionIndex, { operator: $event })
              "
              ><el-option label="等于" value="eq" /><el-option
                label="不等于"
                value="ne" /><el-option
                label="包含"
                value="contains" /><el-option
                label="不包含"
                value="not_contains" /><el-option
                label="大于"
                value="gt" /><el-option label="小于" value="lt"
              /><el-option label="大于等于" value="ge" />
              <el-option label="小于等于" value="le" />
              <el-option label="类型为" value="type" />
              <el-option label="长度等于" value="length" />
            </el-select>
            <el-input
              :model-value="assertion.target"
              :disabled="disabled"
              placeholder="status_code / body.data.id"
              @update:model-value="
                updateAssertion(assertionIndex, { target: $event })
              "
            />
            <el-input
              :model-value="assertion.expected"
              :disabled="disabled"
              placeholder="期望值"
              @update:model-value="
                updateAssertion(assertionIndex, { expected: $event })
              "
            />
            <el-button
              text
              type="danger"
              :disabled="disabled"
              @click="removeAssertion(assertionIndex)"
              >删除</el-button
            >
          </div>
          <el-button
            text
            type="primary"
            :disabled="disabled"
            @click="addAssertion"
            >+ 添加断言</el-button
          >
        </el-collapse-item>
      </el-collapse>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import KeyValueRows from "./KeyValueRows.vue";
import { bodyKind, normalizeStep } from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  modelValue: { type: Object, required: true },
  index: { type: Number, required: true },
  endpoints: { type: Array, default: () => [] },
  disabled: Boolean,
});
const emit = defineEmits(["update:modelValue", "remove"]);
const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const selectorExample = 'body.data[?(@.name == ${unique_name})][0].id';
const step = computed(() => normalizeStep(props.modelValue, props.index + 1));
const currentBodyKind = computed(() => bodyKind(step.value.request));
const jsonText = computed(() =>
  JSON.stringify(step.value.request.json ?? {}, null, 2),
);
const endpointLabel = (endpoint) =>
  `${String(endpoint.method || "GET").toUpperCase()} ${endpoint.path || endpoint.url || endpoint.name || endpoint.id}`;
const patch = (value) => emit("update:modelValue", { ...step.value, ...value });
const patchRequest = (value) =>
  patch({ request: { ...step.value.request, ...value } });
const setBodyKind = (kind) => {
  const request = { ...step.value.request };
  delete request.json;
  delete request.data;
  delete request.raw;
  if (kind === "json") request.json = {};
  if (kind === "form") request.data = {};
  if (kind === "raw") request.raw = "";
  patch({ request });
};
const setJson = (text) => {
  try {
    patchRequest({ json: JSON.parse(text) });
  } catch {
    patchRequest({ json: text });
  }
};
const assertions = computed(() =>
  step.value.validate.map((item) => {
    const [operator, values] = Object.entries(item || {})[0] || [
      "eq",
      ["", ""],
    ];
    return { operator, target: values?.[0] ?? "", expected: values?.[1] ?? "" };
  }),
);
const updateAssertion = (index, value) => {
  const next = assertions.value.map((item, itemIndex) =>
    itemIndex === index ? { ...item, ...value } : item,
  );
  patch({
    validate: next.map((item) => ({
      [item.operator]: [item.target, item.expected],
    })),
  });
};
const addAssertion = () =>
  patch({ validate: [...step.value.validate, { eq: ["status_code", 200] }] });
const removeAssertion = (index) =>
  patch({
    validate: step.value.validate.filter((_, itemIndex) => itemIndex !== index),
  });
</script>

<style scoped>
.step-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.request-line {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 12px;
}
.assertion-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.selector-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.selector-hint code {
  color: var(--el-text-color-regular);
  white-space: normal;
}
.assertion-row {
  display: grid;
  grid-template-columns: 100px minmax(120px, 1fr) minmax(120px, 1fr) auto;
  gap: 8px;
  margin-bottom: 8px;
}
@media (max-width: 680px) {
  .request-line,
  .assertion-row {
    grid-template-columns: 1fr;
  }
}
</style>
