<template>
  <section class="stop-context" aria-label="停止位置">
    <div class="stop-context-header"><h5>{{ locationLabel }}</h5><el-tag type="danger" effect="plain">{{ locationTag }}</el-tag></div>
    <template v-if="context">
      <dl class="stop-context-details">
        <dt>页面标题</dt><dd>{{ failureEvidenceText(context.page_title) }}</dd>
        <dt>页面地址</dt><dd>{{ failureEvidenceText(context.page_url) }}</dd>
        <dt>面包屑</dt><dd>{{ failureEvidenceBreadcrumbs(context.breadcrumbs) }}</dd>
        <dt>操作</dt><dd>{{ failureActionText(context.action) }}</dd>
        <dt>操作对象</dt><dd>{{ failureEvidenceText(context.element_label) }}</dd>
        <dt>所属表单</dt><dd>{{ failureEvidenceText(context.container_label) }}</dd>
        <dt>停止原因</dt><dd>{{ reason }}</dd>
      </dl>
      <el-collapse class="selector-collapse">
        <el-collapse-item title="查看具体定位器">
          <pre>{{ failureEvidenceText(context.selector) }}</pre>
        </el-collapse-item>
      </el-collapse>
      <div class="screenshot-section">
        <div class="screenshot-heading"><strong>停止时截图</strong><span v-if="loading">正在加载截图…</span></div>
        <el-image v-if="screenshotUrl" :src="screenshotUrl" :preview-src-list="[screenshotUrl]" preview-teleported fit="contain" class="failure-screenshot" alt="停止时页面截图" />
        <p v-else class="screenshot-hint">{{ screenshotHint }}</p>
      </div>
    </template>
    <template v-else>
      <p>本次停止时的页面、操作和截图未采集，无法还原现场。</p>
      <p v-if="failureReason">基础错误：{{ failureReason }}</p>
    </template>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { failureActionText, failureEvidenceBreadcrumbs, failureEvidenceText, generationFailureContext, generationFailureContextLocationLabel, generationFailureContextReason } from '@/composables/webUIScriptGenerationPresentation'
import { useGenerationFailureScreenshot } from '@/composables/useGenerationFailureScreenshot'

const props = defineProps({ generation: { type: Object, default: null }, failureReason: { type: String, default: '' } })
const projectStore = useProjectStore()
const context = computed(() => generationFailureContext(props.generation))
const locationLabel = computed(() => generationFailureContextLocationLabel(context.value))
const locationTag = computed(() => context.value?.location_source === 'failure_event'
  ? '停止现场'
  : context.value?.location_source === 'last_observed'
    ? '页面记录'
    : '现场未采集')
const reason = computed(() => generationFailureContextReason(context.value, props.generation))
const screenshotContext = computed(() => ({
  projectId: projectStore.currentProject?.id,
  generationId: props.generation?.id,
  eventId: context.value?.event_id,
  capturedAt: context.value?.captured_at,
  screenshotStatus: context.value?.screenshot_status
}))
const { screenshotUrl, loading, error } = useGenerationFailureScreenshot(() => screenshotContext.value)
const screenshotHint = computed(() => {
  if (error.value) return error.value
  if (context.value?.screenshot_status === 'unavailable') return context.value.screenshot_message || '后端未能保存停止时截图。'
  if (context.value?.screenshot_status === 'not_requested') return context.value.screenshot_message || '本次未请求停止时截图。'
  if (context.value?.screenshot_status === 'captured') return '截图已记录，正在准备查看。'
  return '截图状态未采集。'
})
</script>

<style scoped>
.stop-context {
  margin: 0 0 16px;
  padding: 14px;
  border: 1px solid var(--el-color-danger-light-5, #fbc4c4);
  border-left: 4px solid var(--el-color-danger, #f56c6c);
  border-radius: 8px;
  background: var(--page-content-bg);
}

.stop-context-header,
.screenshot-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.stop-context-header h5 {
  margin: 0;
  color: var(--app-text-primary);
  font-size: 14px;
}

.stop-context-details {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 8px 12px;
  margin: 12px 0 0;
  color: var(--app-text-regular);
  font-size: 13px;
}

.stop-context-details dt { color: var(--app-text-secondary); }
.stop-context-details dd { margin: 0; overflow-wrap: anywhere; }
.selector-collapse { margin-top: 10px; }

.selector-collapse pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--app-text-regular);
  font-size: 12px;
}

.screenshot-section { margin-top: 12px; }
.screenshot-heading strong { color: var(--app-text-primary); font-size: 13px; }

.screenshot-heading span,
.screenshot-hint,
.stop-context > p {
  margin: 8px 0 0;
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.failure-screenshot {
  display: block;
  width: 100%;
  max-height: 360px;
  margin-top: 8px;
  background: var(--app-bg-secondary, #f5f7fa);
  cursor: zoom-in;
}
</style>
