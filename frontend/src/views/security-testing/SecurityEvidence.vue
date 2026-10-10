<template>
  <el-empty v-if="!entries.length" description="没有可用证据，不能仅凭状态判定通过" :image-size="50" />
  <div v-else class="evidence-list">
    <el-card v-for="(entry, index) in entries" :key="index" shadow="never">
      <template #header><strong>{{ entry.variant_label || `证据 ${index + 1}` }}</strong></template>
      <el-descriptions v-if="entry && typeof entry === 'object'" :column="3" border>
        <el-descriptions-item label="请求方法">{{ entry.method || '—' }}</el-descriptions-item>
        <el-descriptions-item label="HTTP 状态">{{ entry.status_code ?? '无响应' }}</el-descriptions-item>
        <el-descriptions-item label="耗时">{{ entry.elapsed_ms != null ? `${entry.elapsed_ms} ms` : '—' }}</el-descriptions-item>
        <el-descriptions-item label="请求 URL" :span="3"><span class="url">{{ entry.url || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="已读取响应">{{ entry.response_bytes != null ? `${entry.response_bytes} B` : '—' }}</el-descriptions-item>
        <el-descriptions-item label="资源证据匹配">{{ entry.proof_match === true ? '匹配' : entry.proof_match === false ? '不匹配' : '未配置或无法判断' }}</el-descriptions-item>
        <el-descriptions-item label="响应完整性">{{ entry.truncated === true ? '已截断' : entry.truncated === false ? '未截断' : '—' }}</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="entry.error_code" :title="entry.error_message || entry.message || '该请求未能完成'" :description="entry.error_code" type="error" :closable="false" class="space" />
      <details v-if="entry.response_headers && Object.keys(entry.response_headers).length" class="space"><summary>响应头</summary><pre>{{ format(entry.response_headers) }}</pre></details>
      <div v-if="entry.response_preview != null" class="space"><strong>响应预览</strong><pre>{{ formatPreview(entry.response_preview) }}</pre></div>
      <el-alert v-if="entry.truncated" title="响应已截断，未采集的内容不在本次检查范围内。" type="warning" :closable="false" class="space" />
      <pre v-if="typeof entry !== 'object'">{{ format(entry) }}</pre>
    </el-card>
  </div>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({ evidence: { type: [Object, Array, String], default: null } })
const entries = computed(() => Array.isArray(props.evidence) ? props.evidence.filter((item) => item != null) : props.evidence ? [props.evidence] : [])
const format = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const formatPreview = (value) => { if (typeof value !== 'string') return format(value); try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value } }
</script>
<style scoped>.evidence-list { display: grid; gap: 12px; }.url { overflow-wrap: anywhere; }.space { margin-top: 12px; }pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 400px; overflow-y: auto; font: 12px/1.6 ui-monospace, monospace; background: var(--page-wrapper-bg); padding: 12px; border-radius: 6px; }summary { cursor: pointer; }</style>
