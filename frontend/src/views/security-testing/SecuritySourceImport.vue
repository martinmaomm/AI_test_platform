<template>
  <el-dialog :model-value="modelValue" title="从 API 项目导入接口草稿" width="900px" @update:model-value="$emit('update:modelValue', $event)" @closed="reset">
    <el-alert title="可选择接口文档或本人已完成网页探索中的 GET/HEAD 请求；导入后请核对 URL、参数和允许来源。认证凭据需要另行配置。" type="info" :closable="false" />
    <el-select v-model="sourceId" placeholder="选择有权限的 API 项目" filterable :loading="loadingProjects" @change="loadEndpoints" class="source-select">
      <el-option v-for="item in projects" :key="item.id" :label="item.name" :value="item.id" />
    </el-select>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-for="(warning, index) in warnings" :key="index" :title="String(warning)" type="warning" :closable="false" />
    <el-table v-loading="loading" :data="endpoints" max-height="400" row-key="source_id" @selection-change="selected = $event">
      <el-table-column type="selection" width="48" /><el-table-column prop="method" label="方法" width="85" />
      <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
      <el-table-column prop="url" label="完整 URL" min-width="350" show-overflow-tooltip />
    </el-table>
    <p class="hint">已选择 {{ selected.length }} 个；当前计划最多还能添加 {{ remaining }} 个。</p>
    <template #footer><el-button @click="$emit('update:modelValue', false)">取消</el-button><el-button type="primary" :disabled="!selected.length || selected.length > remaining" @click="confirm">导入选中接口</el-button></template>
  </el-dialog>
</template>
<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { getSecuritySources } from '@/api/security'
import { unwrap, securityError } from './securityState'
const props = defineProps({ modelValue: Boolean, projectId: [String, Number], remaining: { type: Number, default: 20 } })
const emit = defineEmits(['update:modelValue', 'import'])
const projects = ref([]), sourceId = ref(null), endpoints = ref([]), selected = ref([]), warnings = ref([]), error = ref(''), loading = ref(false), loadingProjects = ref(false)
let epoch = 0
function reset() { epoch++; sourceId.value = null; endpoints.value = []; selected.value = []; warnings.value = []; error.value = ''; loading.value = false; loadingProjects.value = false }
function current(id, projectId) { return epoch === id && props.modelValue && String(projectId) === String(props.projectId) }
async function loadProjects() {
  reset(); projects.value = []; const id = epoch; const projectId = props.projectId; loadingProjects.value = true
  try { const result = unwrap(await getSecuritySources(projectId)); if (current(id, projectId)) projects.value = result.projects || [] }
  catch (cause) { if (current(id, projectId)) error.value = securityError(cause, '加载来源项目失败') }
  finally { if (current(id, projectId)) loadingProjects.value = false }
}
async function loadEndpoints() {
  const id = ++epoch; const projectId = props.projectId; endpoints.value = []; selected.value = []; warnings.value = []; error.value = ''; loading.value = true
  try { const result = unwrap(await getSecuritySources(projectId, sourceId.value)); if (current(id, projectId)) { endpoints.value = result.endpoints || []; warnings.value = result.warnings || [] } }
  catch (cause) { if (current(id, projectId)) error.value = securityError(cause, '加载来源接口失败') }
  finally { if (current(id, projectId)) loading.value = false }
}
function confirm() { if (!selected.value.length || selected.value.length > props.remaining) return; emit('import', selected.value); emit('update:modelValue', false) }
watch([() => props.modelValue, () => props.projectId], ([visible]) => { if (visible) loadProjects(); else reset() })
onBeforeUnmount(reset)
</script>
<style scoped>.source-select { margin: 18px 0; width: 100%; }.hint { color: var(--app-text-muted); }</style>
