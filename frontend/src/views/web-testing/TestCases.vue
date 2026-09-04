<template>
  <div class="page-shell">
    <header class="page-header">
      <div><h2>测试用例</h2><p>每条用例都是一份互相独立、可编辑、可重复执行的 Python Playwright 脚本。</p></div>
      <div><el-button @click="moduleDialogVisible = true">管理模块</el-button><el-button type="primary" @click="openCreate">新建脚本</el-button></div>
    </header>

    <section class="toolbar">
      <el-input v-model="filters.search" clearable placeholder="搜索用例名称或场景描述" @keyup.enter="loadCases" @clear="loadCases" />
      <el-tree-select v-model="filters.moduleId" :data="moduleOptions" clearable check-strictly node-key="value" placeholder="全部模块" @change="loadCases" />
      <el-select v-model="filters.scriptStatus" clearable placeholder="脚本状态" @change="loadCases"><el-option label="可执行" value="ready" /><el-option label="无效" value="invalid" /><el-option label="无脚本" value="none" /></el-select>
      <el-button @click="loadCases">刷新</el-button>
      <el-button v-if="selectedIds.length" type="danger" plain @click="batchRemove">删除选中（{{ selectedIds.length }}）</el-button>
    </section>

    <el-table v-loading="loading" :data="cases" @selection-change="items => selectedIds = items.map(item => item.id)">
      <el-table-column type="selection" width="44" />
      <el-table-column label="用例" min-width="260"><template #default="{ row }"><div class="case-title">{{ row.title }}</div><div class="case-desc">{{ row.description }}</div></template></el-table-column>
      <el-table-column prop="module_name" label="业务模块" width="150"><template #default="{ row }"><el-tag effect="plain">{{ row.module_name || '默认模块' }}</el-tag></template></el-table-column>
      <el-table-column label="脚本" width="120"><template #default="{ row }"><el-tag :type="scriptTagType(row.script_status)">{{ scriptStatusText(row.script_status) }}</el-tag></template></el-table-column>
      <el-table-column label="断言" width="130"><template #default="{ row }"><el-tag :type="assertionTagType(row.assertion_state)" effect="plain">{{ assertionStatusText(row.assertion_state) }}</el-tag></template></el-table-column>
      <el-table-column label="最近执行" width="130"><template #default="{ row }"><el-tag :type="runTagType(row.last_execute_status)" effect="plain">{{ runStatusText(row.last_execute_status) }}</el-tag></template></el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="170"><template #default="{ row }">{{ formatTime(row.updated_at) }}</template></el-table-column>
      <el-table-column label="操作" width="210" fixed="right"><template #default="{ row }"><el-button text type="primary" @click="openEdit(row)">编辑</el-button><el-button text type="success" :disabled="row.script_status !== 'ready'" @click="openRun(row)">运行</el-button><el-button text type="danger" @click="removeCase(row)">删除</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="pagination.page" v-model:page-size="pagination.pageSize" :total="pagination.total" layout="total, sizes, prev, pager, next" @current-change="loadCases" @size-change="loadCases" />

    <WebUICaseEditDetail v-model="editorVisible" :test-case="editingCase" :modules="modules" @saved="handleSaved" />

    <el-dialog v-model="runVisible" title="运行测试用例" width="620px">
      <el-form label-position="top">
        <el-form-item label="测试环境" required><el-select v-model="runForm.environmentId" placeholder="请选择 WebUI 环境"><el-option v-for="item in environments" :key="item.id" :label="`${item.name} · ${item.config?.base_url || ''}`" :value="item.id" /></el-select></el-form-item>
        <div class="run-grid"><el-form-item label="运行模式"><el-switch v-model="runForm.headed" active-text="显示浏览器" inactive-text="无头模式" /></el-form-item><el-form-item label="超时时间（秒）"><el-input-number v-model="runForm.timeout" :min="30" :max="1800" /></el-form-item></div>
        <div class="variables-heading"><div><strong>本次覆盖变量</strong><span>仅本次执行有效，不会保存到用例。</span></div><el-button text type="primary" @click="addRuntimeVariable">添加</el-button></div>
        <div v-for="(item, index) in runForm.variables" :key="index" class="variable-row"><el-input v-model="item.name" placeholder="变量名" /><el-input v-model="item.value" :type="item.is_secret ? 'password' : 'text'" show-password placeholder="本次值" /><el-switch v-model="item.is_secret" active-text="敏感" /><el-button text type="danger" @click="runForm.variables.splice(index, 1)">删除</el-button></div>
      </el-form>
      <template #footer><el-button @click="runVisible = false">取消</el-button><el-button type="primary" :loading="running" @click="runCase">开始运行</el-button></template>
    </el-dialog>

    <el-dialog v-model="moduleDialogVisible" title="业务模块管理" width="560px">
      <div class="module-create"><el-input v-model.trim="newModuleName" placeholder="新模块名称" /><el-button type="primary" @click="addModule">新增</el-button></div>
      <el-table :data="flatModules"><el-table-column prop="label" label="模块" /><el-table-column width="150"><template #default="{ row }"><el-button text type="primary" :disabled="row.is_default" @click="renameModule(row)">重命名</el-button><el-button text type="danger" :disabled="row.is_default" @click="removeModule(row)">删除</el-button></template></el-table-column></el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { assertionStateTagType as assertionTagType, assertionStateLabel as assertionStatusText } from '@/composables/webUIScriptGenerationPresentation'
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { getProjectEnvironments } from '@/api/projects'
import {
  batchDeleteWebUITestCases,
  createWebUITestModule,
  deleteWebUITestCase,
  deleteWebUITestModule,
  executeWebUITestCase,
  getWebUITestCase,
  getWebUITestCases,
  getWebUITestModules,
  updateWebUITestModule
} from '@/api/webTesting'
import WebUICaseEditDetail from '@/components/WebUICaseEditDetail.vue'

const projectStore = useProjectStore()
const projectId = computed(() => projectStore.currentProject?.id)
const loading = ref(false)
const running = ref(false)
const cases = ref([])
const modules = ref([])
const environments = ref([])
const selectedIds = ref([])
const editorVisible = ref(false)
const editingCase = ref(null)
const runVisible = ref(false)
const runningCase = ref(null)
const moduleDialogVisible = ref(false)
const newModuleName = ref('')
const filters = reactive({ search: '', moduleId: null, scriptStatus: null })
const pagination = reactive({ page: 1, pageSize: 20, total: 0 })
const runForm = reactive({ environmentId: null, headed: true, timeout: 300, variables: [] })

const unwrap = (result) => result?.data ?? result ?? {}
const asList = (result) => {
  const body = unwrap(result)
  if (Array.isArray(body)) return body
  if (Array.isArray(body.results)) return body.results
  if (Array.isArray(body.items)) return body.items
  if (Array.isArray(body.data)) return body.data
  if (Array.isArray(body.data?.results)) return body.data.results
  return []
}
const flatten = (items, depth = 0) => items.flatMap(item => [{ ...item, label: `${'　'.repeat(depth)}${item.name}` }, ...flatten(item.children || [], depth + 1)])
const flatModules = computed(() => flatten(modules.value))
const moduleOptions = computed(() => {
  const map = items => items.map(item => ({ value: item.id, label: `${item.name}${item.is_default ? '（默认）' : ''}`, children: map(item.children || []) }))
  return map(modules.value)
})

const loadCases = async () => {
  if (!projectId.value) return
  loading.value = true
  try {
    const result = await getWebUITestCases(projectId.value, { page: pagination.page, page_size: pagination.pageSize, search: filters.search || undefined, module_id: filters.moduleId || undefined, script_status: filters.scriptStatus || undefined })
    const body = unwrap(result)
    cases.value = asList(result)
    pagination.total = body.count ?? body.total ?? body.data?.count ?? cases.value.length
  } catch { ElMessage.error('加载测试用例失败') } finally { loading.value = false }
}
const loadModules = async () => { modules.value = asList(await getWebUITestModules(projectId.value)) }
const loadEnvironments = async () => { environments.value = asList(await getProjectEnvironments(projectId.value, { category: 'web' })).filter(item => item.is_active); runForm.environmentId = environments.value[0]?.id || null }
const reload = async () => { if (!projectId.value) return; await Promise.all([loadCases(), loadModules(), loadEnvironments()]) }
watch(projectId, reload, { immediate: true })

const openCreate = () => { editingCase.value = null; editorVisible.value = true }
const openEdit = async row => { try { const result = await getWebUITestCase(projectId.value, row.id); editingCase.value = unwrap(result); editorVisible.value = true } catch { ElMessage.error('加载用例详情失败') } }
const handleSaved = () => loadCases()
const removeCase = async row => { try { await ElMessageBox.confirm(`确定删除“${row.title}”吗？`, '删除测试用例', { type: 'warning' }); await deleteWebUITestCase(projectId.value, row.id); ElMessage.success('删除成功'); loadCases() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('删除失败') } }
const batchRemove = async () => { try { await ElMessageBox.confirm(`确定删除选中的 ${selectedIds.value.length} 个用例吗？`, '批量删除', { type: 'warning' }); await batchDeleteWebUITestCases(projectId.value, selectedIds.value); selectedIds.value = []; ElMessage.success('删除成功'); loadCases() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('批量删除失败') } }

const openRun = async row => { const result = await getWebUITestCase(projectId.value, row.id); runningCase.value = unwrap(result); runForm.variables = []; runVisible.value = true }
const addRuntimeVariable = () => runForm.variables.push({ name: '', value: '', is_secret: false })
const runCase = async () => {
  if (!runForm.environmentId) return ElMessage.warning('请选择测试环境')
  if (runForm.variables.some(item => !item.name.trim())) return ElMessage.warning('变量名不能为空')
  running.value = true
  try { const result = await executeWebUITestCase(projectId.value, runningCase.value.id, { environment_id: runForm.environmentId, options: { headed: runForm.headed, timeout: runForm.timeout }, runtime_variables: runForm.variables }); runVisible.value = false; ElMessage.success(`执行任务已启动${unwrap(result).task_id ? `：${unwrap(result).task_id}` : ''}`); loadCases() } catch (error) { ElMessage.error(error?.response?.data?.message || '启动执行失败') } finally { running.value = false }
}

const addModule = async () => { if (!newModuleName.value) return; try { await createWebUITestModule(projectId.value, { name: newModuleName.value }); newModuleName.value = ''; await loadModules(); ElMessage.success('模块已创建') } catch { ElMessage.error('创建模块失败') } }
const renameModule = async row => { try { const { value } = await ElMessageBox.prompt('请输入新模块名称', '重命名模块', { inputValue: row.name, inputValidator: value => Boolean(value?.trim()) || '名称不能为空' }); await updateWebUITestModule(projectId.value, row.id, { name: value.trim() }); await loadModules(); ElMessage.success('模块已更新') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('更新模块失败') } }
const removeModule = async row => { try { await ElMessageBox.confirm(`删除模块“${row.name}”后，其中用例会移动到默认模块。`, '删除模块', { type: 'warning' }); await deleteWebUITestModule(projectId.value, row.id); await Promise.all([loadModules(), loadCases()]); ElMessage.success('模块已删除') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.message || '删除模块失败') } }

const scriptTagType = status => ({ ready: 'success', invalid: 'danger', none: 'info' }[status] || 'info')
const scriptStatusText = status => ({ ready: '可执行', invalid: '无效', none: '无脚本' }[status] || status)
const runTagType = status => ({ passed: 'success', incomplete: 'warning', failed: 'danger', running: 'warning', untested: 'info' }[status] || 'info')
const runStatusText = status => ({ passed: '通过', incomplete: '验证未完成', failed: '失败', running: '执行中', untested: '未执行' }[status] || status)
const formatTime = value => value ? new Date(value).toLocaleString() : '-'
</script>

<style scoped>
.page-shell { display: grid; gap: 16px; min-height: 0; }
.page-header, .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }
.page-header h2 { margin: 0; }.page-header p { margin: 6px 0 0; color: var(--app-text-secondary); }
.toolbar { justify-content: flex-start; }.toolbar > :first-child { width: min(360px, 35vw); }.toolbar :deep(.el-select), .toolbar :deep(.el-tree-select) { width: 180px; }
.case-title { font-weight: 600; color: var(--app-text-primary); }.case-desc { margin-top: 4px; color: var(--app-text-secondary); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.el-pagination { justify-content: flex-end; }.run-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.variables-heading { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; }.variables-heading span { display: block; color: var(--app-text-secondary); font-size: 12px; margin-top: 3px; }.variable-row { display: grid; grid-template-columns: 1fr 1fr auto auto; gap: 8px; align-items: center; margin-bottom: 8px; }
.module-create { display: flex; gap: 10px; margin-bottom: 14px; }
@media (max-width: 900px) { .page-header, .toolbar { align-items: stretch; flex-direction: column; }.toolbar > :first-child, .toolbar :deep(.el-select), .toolbar :deep(.el-tree-select) { width: 100%; }.variable-row, .run-grid { grid-template-columns: 1fr; } }
</style>
