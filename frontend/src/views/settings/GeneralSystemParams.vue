<template>
  <div class="general-params-page">
    <div class="page-header">
      <BackButton to="/settings" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Operation /></el-icon>
          </div>
          <div class="header-text">
            <h2>通用系统参数</h2>
            <p>环境标识、系统备份等基础配置</p>
          </div>
        </div>
      </div>
    </div>

    <el-card class="config-card">
      <el-empty description="功能开发中，敬请期待">
        <template #image>
          <el-icon :size="64" color="var(--app-text-muted)">
            <Operation />
          </el-icon>
        </template>
      </el-empty>
    </el-card>
  </div>
</template>

<script setup>
import { Operation } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
</script>

<style scoped>
.general-params-page {
  min-height: 100%;
  padding: 24px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header :deep(.back-btn) {
  margin-bottom: 12px;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: rgba(100, 116, 139, 0.15);
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}

.header-text h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--app-text-primary);
  margin: 0 0 4px 0;
}

.header-text p {
  font-size: 14px;
  color: var(--app-text-muted);
  margin: 0;
}

.config-card {
  min-height: 300px;
}
</style>
