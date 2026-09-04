<template>
  <section v-if="showScreenshot" class="execution-screenshot-card">
    <div class="screenshot-header">
      <h3>{{ title }}</h3>
      <span v-if="loading" class="screenshot-hint">正在加载截图…</span>
    </div>
    <template v-if="screenshotUrl">
      <el-image
        :src="screenshotUrl"
        :preview-src-list="[screenshotUrl]"
        :preview-teleported="true"
        :hide-on-click-modal="true"
        fit="contain"
        class="execution-screenshot-image"
        :alt="title"
      />
      <p class="screenshot-hint">浏览器关闭前保存的页面，点击截图可放大查看。截图本身不代表断言通过。</p>
    </template>
    <el-empty v-else-if="!loading" :description="error || '未保存截图（页面未能打开、提前关闭或截图失败）'" :image-size="60">
      <el-button v-if="error" size="small" @click="reload">重新加载截图</el-button>
    </el-empty>
  </section>
</template>

<script setup>
import { useWebUIExecutionScreenshot } from '@/composables/useWebUIExecutionScreenshot'

const props = defineProps({
  projectId: [Number, String],
  executionId: [Number, String],
  caseExecutionId: { type: [Number, String], default: null },
  screenshotPath: { type: String, default: '' },
  status: { type: String, default: '' }
})
const { screenshotUrl, loading, error, showScreenshot, title, reload } = useWebUIExecutionScreenshot(() => props)
</script>

<style scoped>
.execution-screenshot-card { min-width: 0; padding: 20px; background: #fff; border: 1px solid #e8eaed; border-left: 4px solid #409eff; border-radius: 8px; }
.screenshot-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.screenshot-header h3 { margin: 0; font-size: 16px; color: #303133; }
.execution-screenshot-image { display: block; width: 100%; height: 280px; background: #f8f9fa; cursor: zoom-in; }
.screenshot-hint { margin: 10px 0 0; color: #606266; font-size: 12px; line-height: 1.6; }
</style>
