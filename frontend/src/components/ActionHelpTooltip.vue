<template>
  <el-tooltip
    :content="content"
    :placement="placement"
    :trigger="['hover', 'focus', 'click']"
    :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))', lineHeight: '1.6' }"
  >
    <button
      type="button"
      class="action-help-tooltip"
      :aria-label="`${label}说明`"
      @click.stop
      @keydown.stop
    >
      <el-icon aria-hidden="true"><QuestionFilled /></el-icon>
    </button>
  </el-tooltip>
</template>

<script setup>
import { QuestionFilled } from "@element-plus/icons-vue";

defineProps({
  label: { type: String, required: true },
  content: { type: String, required: true },
  placement: { type: String, default: "top" },
});
</script>

<style scoped>
.action-help-tooltip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: help;
}
.action-help-tooltip:hover,
.action-help-tooltip:focus-visible {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.action-help-tooltip:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
</style>
