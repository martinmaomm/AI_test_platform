<template>
  <div class="perf-workspace-placeholder">
    <el-empty description="性能专项测试工作区">
      <template #description>
        <p>性能专项测试工作区</p>
        <p class="hint">功能开发中，敬请期待。</p>
      </template>
    </el-empty>
  </div>
</template>

<script setup>
</script>

<style scoped>
.perf-workspace-placeholder {
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hint {
  font-size: 13px;
  color: #909399;
  margin-top: 8px;
}
</style>
