<template>
  <div class="assertion-expected-input">
    <el-input
      :model-value="text"
      :disabled="disabled"
      :aria-label="label"
      placeholder='401 是数字，"401" 是字符串'
      @update:model-value="updateText"
    />
    <span class="expected-type" data-testid="assertion-expected-type" aria-live="polite">
      类型：{{ assertionExpectedTypeLabel(modelValue) }}
    </span>
  </div>
</template>

<script setup>
import { ref, watch } from "vue";
import {
  assertionExpectedTypeLabel,
  formatAssertionExpected,
  parseAssertionExpected,
} from "./assertionValues.js";

const props = defineProps({
  modelValue: { default: "" },
  disabled: Boolean,
  label: { type: String, default: "断言期望值" },
});
const emit = defineEmits(["update:modelValue"]);
const text = ref(formatAssertionExpected(props.modelValue));
let lastEmitted;
watch(
  () => JSON.stringify(props.modelValue),
  (serialized) => {
    if (serialized !== lastEmitted)
      text.value = formatAssertionExpected(props.modelValue);
    lastEmitted = undefined;
  },
);

const updateText = (value) => {
  if (props.disabled) return;
  // Preserve unfinished quotes, decimals and JSON across parent v-model echoes.
  text.value = value;
  const expected = parseAssertionExpected(value);
  lastEmitted = JSON.stringify(expected);
  emit("update:modelValue", expected);
};
</script>

<style scoped>
.assertion-expected-input {
  min-width: 0;
}
.expected-type {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
