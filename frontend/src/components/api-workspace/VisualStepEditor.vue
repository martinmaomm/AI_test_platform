<template>
  <el-card shadow="never" class="step-editor">
    <template #header>
      <div class="step-heading">
        <strong>步骤 {{ index + 1 }}<ActionHelpTooltip label="步骤编辑操作" content="删除步骤只从当前草稿移除请求，不会调用被测网站的删除接口。请求体类型切换会清空原类型的内容，请先核对；所有修改需保存草稿并重新验证。" /></strong>
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
      <el-form-item label="步骤阶段">
        <el-select
          :model-value="step.phase || ''"
          :disabled="disabled"
          placeholder="普通执行步骤"
          @update:model-value="setPhase"
        >
          <el-option label="普通执行" value="" />
          <el-option label="清理测试数据" value="cleanup" />
        </el-select>
        <p class="phase-hint">清理步骤只可依赖本轮前序步骤已经提取的变量。</p>
      </el-form-item>
      <el-form-item v-if="step.phase === 'cleanup'" label="清理依赖变量">
        <el-checkbox-group
          :model-value="step.requires"
          :disabled="disabled"
          @update:model-value="patch({ requires: $event })"
        >
          <el-checkbox
            v-for="variable in availableExtractVariables"
            :key="variable"
            :label="variable"
          >{{ variable }}</el-checkbox>
        </el-checkbox-group>
        <p v-if="!availableExtractVariables.length" class="phase-hint">前序步骤尚未提取变量；清理步骤不能填写额外依赖。</p>
      </el-form-item>
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
            unique_name 需先在变量中定义；条件筛选提取必须恰好匹配一条，零条或多条会停止，不会默认选第一条。
            显式数字索引如 <code>body.data[0].id</code> 或 <code>body.data.0.id</code> 会按指定位置取值，即使后续用于 POST 等写请求也不要求列表只有一条；索引越界或字段缺失仍会失败。列表顺序可能变化，针对指定业务对象优先使用唯一条件筛选；不要直接提取未指定索引的整个列表用于后续写请求。
            删除后可对筛选结果使用“长度等于 0”断言，无需再提取 ID。
            “长度大于”可检查 <code>body.data</code>；阈值 0 表示非空，建议保留 <code>type</code> 为 <code>list</code> 的断言。
          </p>
          <div class="assertion-title">断言</div>
          <p class="selector-hint">
            期望值输入 <code>401</code> 表示数字，<code>"401"</code> 表示字符串（使用英文双引号）；
            普通文本可直接输入，也支持 <code>true</code> / <code>false</code>、<code>null</code>、JSON 数组和对象。
            变量如 <code>${expected_code}</code> 在运行时取值。下方类型提示对应实际保存的值；修改后需保存并重新验证。
          </p>
          <div
            v-for="(assertion, assertionIndex) in assertions"
            :key="assertionIndex"
            class="assertion-row"
          >
            <el-select
              :model-value="assertion.operator"
              :disabled="disabled"
              :aria-label="`断言 ${assertionIndex + 1} 操作符`"
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
              <el-option label="长度大于" value="length_gt" />
            </el-select>
            <el-input
              :model-value="assertion.target"
              :disabled="disabled"
              :aria-label="`断言 ${assertionIndex + 1} 目标`"
              placeholder="status_code / body.data.id"
              @update:model-value="
                updateAssertion(assertionIndex, { target: $event })
              "
            />
            <AssertionExpectedInput
              :model-value="assertion.expected"
              :disabled="disabled"
              :label="`断言 ${assertionIndex + 1} 期望值`"
              @update:model-value="
                updateAssertion(assertionIndex, { expected: $event })
              "
            />
            <el-button
              text
              type="danger"
              :disabled="disabled"
              :aria-label="`删除断言 ${assertionIndex + 1}`"
              @click="removeAssertion(assertionIndex)"
              >删除</el-button
            >
          </div>
          <el-button
            text
            type="primary"
            :disabled="disabled"
            aria-label="添加断言"
            @click="addAssertion"
            >+ 添加断言</el-button
          >
          <ActionHelpTooltip label="断言操作" content="添加断言会新增一条默认 HTTP 状态码等于 200 的检查，请按接口实际预期修改。删除只移除当前检查项；不要为了让结果通过而删除必要断言。修改后需保存并重新验证。" />
        </el-collapse-item>
      </el-collapse>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
import AssertionExpectedInput from "./AssertionExpectedInput.vue";
import KeyValueRows from "./KeyValueRows.vue";
import { bodyKind, normalizeStep } from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  modelValue: { type: Object, required: true },
  index: { type: Number, required: true },
  endpoints: { type: Array, default: () => [] },
  availableExtractVariables: { type: Array, default: () => [] },
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
const setPhase = (phase) =>
  patch(
    phase === "cleanup"
      ? { phase: "cleanup" }
      : { phase: undefined, requires: [] },
  );
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
    return {
      operator,
      target: values?.[0] ?? "",
      expected: values?.[1] === undefined ? "" : values[1],
    };
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
.phase-hint {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.assertion-row {
  display: grid;
  grid-template-columns: 100px minmax(120px, 1fr) minmax(120px, 1fr) auto;
  gap: 8px;
  margin-bottom: 8px;
  align-items: start;
}
@media (max-width: 680px) {
  .request-line,
  .assertion-row {
    grid-template-columns: 1fr;
  }
}
</style>
