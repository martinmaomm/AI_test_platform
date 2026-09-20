<template>
  <div class="evidence-value">
    <pre>{{ value?.content || "（空）" }}</pre>
    <span v-if="value?.truncated" class="truncated">内容过长，已截断展示</span>
  </div>
</template>
<script setup>
defineProps({ value: Object });
</script>
<style scoped>
pre {
  margin: 0;
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.6;
}
.truncated { color: var(--el-color-warning); font-size: 12px; }
</style>
