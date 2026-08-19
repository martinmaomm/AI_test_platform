<template>
  <div class="scheduled-tasks-page">
    <el-tabs v-model="activeTab" class="scheduled-tabs">
      <el-tab-pane label="任务管理" name="tasks">
        <ScheduledTasksIndex />
      </el-tab-pane>
      <el-tab-pane label="执行日志" name="logs">
        <ScheduledTasksLogs />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ScheduledTasksIndex from './index.vue'
import ScheduledTasksLogs from './logs.vue'

const activeTab = ref('tasks')
</script>

<style scoped>
.scheduled-tasks-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.scheduled-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.scheduled-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}

.scheduled-tabs :deep(.el-tab-pane) {
  height: 100%;
  overflow: auto;
}

.scheduled-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
  flex-shrink: 0;
}
</style>
