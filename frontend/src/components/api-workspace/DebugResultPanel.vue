<template>
  <el-card shadow="never" class="debug-panel">
    <template #header
      ><div class="heading">
        <strong>调试结果</strong
        ><el-tag :type="resultType">{{
          stale ? "草稿已修改，结果已过期" : statusLabel(result?.status)
        }}</el-tag>
      </div></template
    >
    <el-alert
      v-if="!hasResult"
      title="尚未执行。未执行的用例不会显示为通过。"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-else>
      <el-alert
        v-if="stale"
        title="当前存在未保存或更高版本的草稿；该结果只对应旧草稿，不能据此判定当前草稿通过。"
        type="warning"
        :closable="false"
        show-icon
      />
      <div
        v-for="(step, index) in steps"
        :key="step.id || index"
        class="debug-step"
      >
        <div class="heading">
          <strong>步骤 {{ index + 1 }}：{{ step.name || "未命名" }}</strong
          ><el-tag :type="stepType(step)">{{
            statusLabel(step.status)
          }}</el-tag>
        </div>
        <p v-if="isSkipped(step)">已跳过：前序步骤失败。</p>
        <details v-if="step.request">
          <summary>请求</summary>
          <pre v-text="pretty(step.request)" />
        </details>
        <details v-if="step.response">
          <summary>响应</summary>
          <pre v-text="pretty(step.response)" />
        </details>
        <details v-if="step.extract || step.extractions">
          <summary>提取</summary>
          <pre v-text="pretty(step.extract || step.extractions)" />
        </details>
        <details v-if="step.assertions || step.validate">
          <summary>断言</summary>
          <pre v-text="pretty(step.assertions || step.validate)" />
        </details>
        <p v-if="step.error" class="error">{{ step.error }}</p>
      </div>
      <details v-if="result.log" class="execution-log">
        <summary>执行日志</summary>
        <pre v-text="pretty(result.log)" />
      </details>
      <details v-if="!steps.length">
        <summary>原始调试数据</summary>
        <pre v-text="pretty(result)" />
      </details>
    </template>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
const props = defineProps({
  result: { type: Object, default: null },
  stale: Boolean,
});
const hasResult = computed(() =>
  Boolean(props.result && Object.keys(props.result).length),
);
const rawSteps = computed(() => {
  const value =
    props.result?.step_datas ||
    props.result?.steps ||
    props.result?.results ||
    props.result?.debug_steps ||
    [];
  return Array.isArray(value) ? value : [];
});
const steps = computed(() =>
  rawSteps.value.map((step) => {
    const data = step?.data || {};
    const reqResps = data.req_resps || step?.req_resps || [];
    const requests = reqResps.map((item) => item?.request).filter(Boolean);
    const responses = reqResps.map((item) => item?.response).filter(Boolean);
    return {
      ...step,
      request: requests.length === 1 ? requests[0] : requests,
      response: responses.length === 1 ? responses[0] : responses,
      extract: step?.export_vars || step?.extract || step?.extractions,
      assertions:
        data.validators ||
        step?.validators ||
        step?.assertions ||
        step?.validate,
    };
  }),
);
const pretty = (value) => JSON.stringify(value, null, 2);
const normalizedStatus = (status) => String(status || "").toLowerCase();
const statusLabel = (status) => {
  const labels = {
    skipped: "已跳过",
    passed: "通过",
    failed: "失败",
    failure: "失败",
    error: "错误",
  };
  return labels[normalizedStatus(status)] || "未执行";
};
const isSkipped = (step) => normalizedStatus(step?.status) === "skipped";
const failed = (value) =>
  ["failed", "failure", "error"].includes(normalizedStatus(value?.status));
const stepType = (step) =>
  isSkipped(step)
    ? "info"
    : failed(step)
      ? "danger"
      : normalizedStatus(step?.status) === "passed"
        ? "success"
        : "warning";
const resultType = computed(() =>
  props.stale
    ? "warning"
    : failed(props.result)
      ? "danger"
      : normalizedStatus(props.result?.status) === "passed"
        ? "success"
        : "info",
);
</script>

<style scoped>
.heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.debug-step {
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 10px 0;
}
.debug-step p {
  margin: 8px 0;
}
.debug-step pre {
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
.execution-log {
  margin-top: 10px;
}
.execution-log pre {
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
.error {
  color: var(--el-color-danger);
}
</style>
