<template>
  <div class="smart-create-page">
    <div class="create-header">
      <div class="header-copy">
        <h2>智能创建</h2>
        <p>选择适合当前任务的创建方式，生成结果统一进入测试用例管理。</p>
      </div>

      <el-tabs :model-value="activeMode" class="create-mode-tabs" @tab-change="switchMode">
        <el-tab-pane name="requirements">
          <template #label>
            <span class="mode-label">
              <el-icon><Document /></el-icon>
              从需求生成
            </span>
          </template>
        </el-tab-pane>
        <el-tab-pane name="explore">
          <template #label>
            <span class="mode-label">
              <el-icon><Compass /></el-icon>
              探索网页生成
              <el-tag size="small" type="warning" effect="plain">实验</el-tag>
            </span>
          </template>
        </el-tab-pane>
      </el-tabs>

      <div class="mode-description">
        <template v-if="activeMode === 'requirements'">
          根据业务需求、知识库和已维护的页面元素，生成可编辑的结构化测试用例。
        </template>
        <template v-else>
          使用 Playwright MCP 探索真实页面并生成 Python 脚本草稿，保存前需要人工检查。
        </template>
      </div>
    </div>

    <div class="create-content">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Compass, Document } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

const activeMode = computed(() => (
  route.path.endsWith('/explore') ? 'explore' : 'requirements'
))

const switchMode = (mode) => {
  const target = mode === 'explore'
    ? '/web-testing/create/explore'
    : '/web-testing/create/requirements'

  if (route.path !== target) router.push(target)
}
</script>

<style scoped>
.smart-create-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.create-header {
  flex: none;
  padding: 16px 20px 0;
  background: var(--page-content-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
}

.header-copy h2 {
  margin: 0;
  color: var(--app-text-primary);
  font-size: 20px;
}

.header-copy p {
  margin: 6px 0 10px;
  color: var(--app-text-secondary);
  font-size: 13px;
}

.create-mode-tabs :deep(.el-tabs__header) {
  margin-bottom: 8px;
}

.create-mode-tabs :deep(.el-tabs__content) {
  display: none;
}

.mode-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.mode-description {
  min-height: 20px;
  padding-bottom: 12px;
  color: var(--app-text-secondary);
  font-size: 13px;
}

.create-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.create-content :deep(.webui-generator),
.create-content :deep(.webui-agent),
.create-content :deep(.main-container) {
  height: 100%;
}
</style>
