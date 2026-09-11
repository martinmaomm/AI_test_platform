<template>
  <el-drawer :model-value="visible" title="我的生成记录" size="min(620px, 94vw)" :close-on-click-modal="false" @close="emit('close')">
    <template #default>
      <div class="history-panel">
        <p class="history-hint">仅显示当前项目中属于当前账号的生成记录。恢复记录只读取已有详情，不会重新生成、调试或修复。</p>
        <el-alert v-if="error" type="warning" :title="error" :closable="false" show-icon>
          <template #default><el-button link type="primary" @click="emit('load', page)">重试</el-button></template>
        </el-alert>
        <el-skeleton v-if="loading && !items.length" :rows="6" animated />
        <el-empty v-else-if="!items.length && !error" description="暂无生成记录" :image-size="82" />
        <div v-else class="history-list" v-loading="loading">
          <article v-for="item in items" :key="item.id" class="history-item" :class="{ current: isCurrent(item) }">
            <div class="history-main">
              <strong>{{ item.title || '未命名生成记录' }}</strong>
              <div class="history-meta"><span>{{ formatTime(item.updated_at || item.created_at) }}</span><span>{{ modelInfoLabel(item.model_info, '未记录模型') }}</span></div>
            </div>
            <div class="history-actions">
              <el-tag :type="statusType(item.status)" effect="plain" size="small">{{ generationStatusLabel(item.status) }}</el-tag>
              <el-tag v-if="item.test_case_id" type="success" effect="plain" size="small">已保存</el-tag>
              <el-button link type="primary" :loading="switching && !isCurrent(item)" :disabled="switchDisabled || isCurrent(item)" @click="emit('select', item.id)">{{ isCurrent(item) ? '当前记录' : '恢复' }}</el-button>
            </div>
          </article>
        </div>
        <el-pagination v-if="total > pageSize" :current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="nextPage => emit('load', nextPage)" />
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { generationStatusLabel, modelInfoLabel } from '@/composables/webUIScriptGenerationPresentation'

const props = defineProps({
  visible: Boolean,
  items: { type: Array, default: () => [] },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  total: { type: Number, default: 0 },
  error: { type: String, default: '' },
  loading: Boolean,
  switching: Boolean,
  switchDisabled: Boolean,
  currentGenerationId: { type: [String, Number], default: null }
})
const emit = defineEmits(['close', 'load', 'select'])
const isCurrent = item => String(item?.id || '') === String(props.currentGenerationId || '')
const statusType = status => {
  if (status === 'ready') return 'success'
  if (['failed'].includes(status)) return 'danger'
  if (['cancelled', 'needs_input', 'needs_confirmation', 'needs_review', 'ready_with_warnings'].includes(status)) return 'warning'
  return 'info'
}
const formatTime = value => {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.history-panel, .history-list { align-content: start; }
.history-panel { display: grid; gap: 14px; min-height: 100%; }.history-hint { margin: 0; color: var(--app-text-secondary); font-size: 13px; line-height: 1.6; }.history-list { display: grid; gap: 10px; min-height: 110px; }.history-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px; border: 1px solid var(--app-border); border-radius: 8px; }.history-item.current { border-color: var(--el-color-primary-light-5); background: var(--el-color-primary-light-9); }.history-main { min-width: 0; }.history-main strong { display: block; overflow: hidden; color: var(--app-text-primary); text-overflow: ellipsis; white-space: nowrap; }.history-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 6px; color: var(--app-text-secondary); font-size: 12px; }.history-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 6px; }.el-pagination { justify-content: flex-end; } @media (max-width: 520px) { .history-item { align-items: flex-start; flex-direction: column; }.history-actions { justify-content: flex-start; } }
</style>
