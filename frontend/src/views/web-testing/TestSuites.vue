<template>
  <div class="page-shell">
    <header class="page-header">
      <div><h2>测试套件</h2><p>按顺序组合多个独立脚本；某个用例失败后，后续用例仍会继续执行。</p></div>
      <el-button type="primary" @click="openCreate">新建套件</el-button>
    </header>
    <section class="toolbar"><el-input v-model="filters.search" clearable placeholder="搜索套件" @keyup.enter="loadSuites" @clear="loadSuites" /><el-select v-model="filters.status" clearable placeholder="全部状态" @change="loadSuites"><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /><el-option label="归档" value="archived" /></el-select><el-button @click="loadSuites">刷新</el-button></section>
    <el-table v-loading="loading" :data="suites">
      <el-table-column label="套件" min-width="260"><template #default="{ row }"><div class="suite-title">{{ row.name }}</div><div class="suite-desc">{{ row.description || '暂无描述' }}</div></template></el-table-column>
      <el-table-column prop="test_cases_count" label="脚本数" width="100" />
      <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'active' ? 'success' : 'info'">{{ row.status_display }}</el-tag></template></el-table-column>
      <el-table-column label="变量" width="100"><template #default="{ row }">{{ row.variables?.length || 0 }}</template></el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="180"><template #default="{ row }">{{ formatTime(row.updated_at) }}</template></el-table-column>
      <el-table-column label="操作" width="210" fixed="right"><template #default="{ row }"><el-button text type="primary" @click="openEdit(row)">编辑</el-button><el-button text type="success" :disabled="row.status !== 'active' || !row.test_cases_count" @click="openRun(row)">运行</el-button><el-button text type="danger" @click="removeSuite(row)">删除</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="pagination.page" v-model:page-size="pagination.pageSize" :total="pagination.total" layout="total, sizes, prev, pager, next" @current-change="loadSuites" @size-change="loadSuites" />

    <el-drawer v-model="editorVisible" size="70%" :close-on-click-modal="false">
      <template #header><div class="drawer-header"><div><h3>{{ editingId ? '编辑测试套件' : '新建测试套件' }}</h3><p>套件只定义脚本顺序和覆盖变量，不改变用例脚本本身。</p></div><div><el-button @click="editorVisible = false">关闭</el-button><el-button type="primary" :loading="saving" @click="saveSuite">保存</el-button></div></div></template>
      <el-form label-position="top">
        <div class="form-grid"><el-form-item label="套件名称" required><el-input v-model.trim="form.name" /></el-form-item><el-form-item label="状态"><el-select v-model="form.status"><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /><el-option label="归档" value="archived" /></el-select></el-form-item></div>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="3" resize="vertical" /></el-form-item>
        <section class="section-card"><div class="section-heading"><div><h4>套件变量</h4><p>优先级高于用例变量；运行时覆盖变量的优先级最高。</p></div><el-button type="primary" plain @click="addSuiteVariable">添加变量</el-button></div><div v-for="(item, index) in form.variables" :key="index" class="variable-row"><el-input v-model="item.name" placeholder="变量名" /><el-input v-model="item.value" :type="item.is_secret ? 'password' : 'text'" show-password placeholder="变量值" /><el-input v-model="item.description" placeholder="说明" /><el-switch v-model="item.required" active-text="必填" /><el-switch v-model="item.is_secret" active-text="敏感" /><el-button text type="danger" @click="form.variables.splice(index, 1)">删除</el-button></div><el-empty v-if="!form.variables.length" :image-size="64" description="暂无套件变量" /></section>
        <section class="section-card"><div class="section-heading"><div><h4>执行顺序</h4><p>每个脚本使用独立浏览器执行环境，失败不会阻止后续脚本。</p></div><el-select v-model="caseToAdd" filterable placeholder="选择脚本加入套件" @change="addSelectedCase"><el-option v-for="item in availableCases" :key="item.id" :label="item.title" :value="item.id" /></el-select></div><div v-for="(item, index) in form.testCases" :key="item.id" class="case-row"><span class="order-index">{{ index + 1 }}</span><div><strong>{{ item.title }}</strong><p>{{ item.description || '暂无描述' }} · {{ assertionStatusText(item.assertion_state) }}</p></div><div class="order-actions"><el-button size="small" :disabled="index === 0" @click="moveCase(index, -1)">上移</el-button><el-button size="small" :disabled="index === form.testCases.length - 1" @click="moveCase(index, 1)">下移</el-button><el-button size="small" type="danger" plain @click="form.testCases.splice(index, 1)">移除</el-button></div></div><el-empty v-if="!form.testCases.length" :image-size="72" description="请添加至少一个可执行脚本" /></section>
      </el-form>
    </el-drawer>

    <el-dialog v-model="runVisible" title="运行测试套件" width="640px">
      <el-alert title="套件会按顺序独立执行所有脚本；单个脚本失败后仍继续执行。" type="info" :closable="false" show-icon />
      <el-form label-position="top" class="run-form"><el-form-item label="测试环境" required><el-select v-model="runForm.environmentId"><el-option v-for="item in environments" :key="item.id" :label="`${item.name} · ${item.config?.base_url || ''}`" :value="item.id" /></el-select></el-form-item><div class="form-grid"><el-form-item label="运行模式"><el-switch v-model="runForm.headed" active-text="显示浏览器" inactive-text="无头模式" /></el-form-item><el-form-item label="超时时间（每个脚本/秒）"><el-input-number v-model="runForm.timeout" :min="30" :max="1800" /></el-form-item></div></el-form>
      <div class="section-heading"><div><h4>本次覆盖变量</h4><p>优先级：本次覆盖 &gt; 套件变量 &gt; 用例变量 &gt; 环境变量。</p></div><el-button text type="primary" @click="addRuntimeVariable">添加</el-button></div><div v-for="(item, index) in runForm.variables" :key="index" class="runtime-row"><el-input v-model="item.name" placeholder="变量名" /><el-input v-model="item.value" :type="item.is_secret ? 'password' : 'text'" show-password placeholder="本次值" /><el-switch v-model="item.is_secret" active-text="敏感" /><el-button text type="danger" @click="runForm.variables.splice(index, 1)">删除</el-button></div>
      <template #footer><el-button @click="runVisible = false">取消</el-button><el-button type="primary" :loading="running" @click="runSuite">开始运行</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { getProjectEnvironments } from '@/api/projects'
import {
  addTestCasesToSuite,
  createWebUITestSuite,
  deleteWebUITestSuite,
  executeWebUITestSuite,
  getWebUITestCases,
  getWebUITestSuite,
  getWebUITestSuites,
  removeTestCaseFromSuite,
  reorderTestSuiteCases,
  updateWebUITestSuite
} from '@/api/webTesting'

const projectStore = useProjectStore()
const projectId = computed(() => projectStore.currentProject?.id)
const suites = ref([])
const allCases = ref([])
const environments = ref([])
const loading = ref(false)
const saving = ref(false)
const running = ref(false)
const editorVisible = ref(false)
const runVisible = ref(false)
const editingId = ref(null)
const runningSuite = ref(null)
const caseToAdd = ref(null)
const filters = reactive({ search: '', status: null })
const pagination = reactive({ page: 1, pageSize: 20, total: 0 })
const form = reactive({ name: '', description: '', status: 'active', variables: [], testCases: [], originalCaseIds: [] })
const runForm = reactive({ environmentId: null, headed: true, timeout: 300, variables: [] })
const unwrap = result => result?.data ?? result ?? {}
const asList = result => { const body = unwrap(result); if (Array.isArray(body)) return body; if (Array.isArray(body.results)) return body.results; if (Array.isArray(body.items)) return body.items; if (Array.isArray(body.data)) return body.data; if (Array.isArray(body.data?.results)) return body.data.results; return [] }
const availableCases = computed(() => allCases.value.filter(item => !form.testCases.some(selected => selected.id === item.id)))

const loadSuites = async () => { if (!projectId.value) return; loading.value = true; try { const result = await getWebUITestSuites(projectId.value, { page: pagination.page, page_size: pagination.pageSize, search: filters.search || undefined, status: filters.status || undefined }); const body = unwrap(result); suites.value = asList(result); pagination.total = body.count ?? body.total ?? body.data?.count ?? suites.value.length } catch { ElMessage.error('加载测试套件失败') } finally { loading.value = false } }
const loadCases = async () => { allCases.value = asList(await getWebUITestCases(projectId.value, { page_size: 100, script_status: 'ready' })) }
const loadEnvironments = async () => { environments.value = asList(await getProjectEnvironments(projectId.value, { category: 'web' })).filter(item => item.is_active); runForm.environmentId = environments.value[0]?.id || null }
watch(projectId, async () => { if (!projectId.value) return; await Promise.all([loadSuites(), loadCases(), loadEnvironments()]) }, { immediate: true })

const resetForm = source => { editingId.value = source?.id || null; form.name = source?.name || ''; form.description = source?.description || ''; form.status = source?.status || 'active'; form.variables = (source?.variables || []).map(item => ({ ...item })); form.testCases = (source?.test_cases || []).map(item => ({ ...item })); form.originalCaseIds = form.testCases.map(item => item.id) }
const openCreate = () => { resetForm(null); editorVisible.value = true }
const openEdit = async row => { try { const result = await getWebUITestSuite(projectId.value, row.id); resetForm(unwrap(result)); editorVisible.value = true } catch { ElMessage.error('加载套件详情失败') } }
const addSuiteVariable = () => form.variables.push({ name: '', value: '', description: '', required: false, is_secret: false })
const addSelectedCase = caseId => { const item = allCases.value.find(entry => entry.id === caseId); if (item) form.testCases.push(item); caseToAdd.value = null }
const moveCase = (index, offset) => { const target = index + offset; if (target < 0 || target >= form.testCases.length) return; const [item] = form.testCases.splice(index, 1); form.testCases.splice(target, 0, item) }
const assertionStatusText = state => {
  if (!state) return '断言未检查'
  if (state.status === 'complete') return '断言已补齐'
  if (state.pending_count) return `${state.pending_count} 项断言待补充`
  return '缺少有效断言'
}

const saveSuite = async () => {
  if (!form.name) return ElMessage.warning('请填写套件名称')
  if (form.variables.some(item => !item.name.trim())) return ElMessage.warning('变量名不能为空')
  saving.value = true
  try {
    const payload = { name: form.name, description: form.description, status: form.status, variables: form.variables }
    let suiteId = editingId.value
    if (suiteId) await updateWebUITestSuite(projectId.value, suiteId, payload)
    else { const result = await createWebUITestSuite(projectId.value, payload); suiteId = unwrap(result).id }
    const desiredIds = form.testCases.map(item => item.id)
    const removed = form.originalCaseIds.filter(id => !desiredIds.includes(id))
    const added = desiredIds.filter(id => !form.originalCaseIds.includes(id))
    for (const caseId of removed) await removeTestCaseFromSuite(projectId.value, suiteId, caseId)
    if (added.length) await addTestCasesToSuite(projectId.value, suiteId, { test_case_ids: added })
    if (desiredIds.length) await reorderTestSuiteCases(projectId.value, suiteId, desiredIds)
    editorVisible.value = false
    ElMessage.success('测试套件已保存')
    await loadSuites()
  } catch (error) { ElMessage.error(error?.response?.data?.message || '保存测试套件失败') } finally { saving.value = false }
}
const removeSuite = async row => { try { await ElMessageBox.confirm(`确定删除“${row.name}”吗？`, '删除测试套件', { type: 'warning' }); await deleteWebUITestSuite(projectId.value, row.id); ElMessage.success('删除成功'); loadSuites() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('删除失败') } }

const openRun = async row => { const result = await getWebUITestSuite(projectId.value, row.id); runningSuite.value = unwrap(result); runForm.variables = []; runVisible.value = true }
const addRuntimeVariable = () => runForm.variables.push({ name: '', value: '', is_secret: false })
const runSuite = async () => { if (!runForm.environmentId) return ElMessage.warning('请选择测试环境'); if (runForm.variables.some(item => !item.name.trim())) return ElMessage.warning('变量名不能为空'); running.value = true; try { const result = await executeWebUITestSuite(projectId.value, runningSuite.value.id, { environment_id: runForm.environmentId, options: { headed: runForm.headed, timeout: runForm.timeout }, runtime_variables: runForm.variables }); runVisible.value = false; ElMessage.success(`套件执行已启动${unwrap(result).task_id ? `：${unwrap(result).task_id}` : ''}`) } catch (error) { ElMessage.error(error?.response?.data?.message || '启动套件执行失败') } finally { running.value = false } }
const formatTime = value => value ? new Date(value).toLocaleString() : '-'
</script>

<style scoped>
.page-shell { display: grid; gap: 16px; }.page-header, .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }.page-header h2, .drawer-header h3, .section-heading h4 { margin: 0; }.page-header p, .drawer-header p, .section-heading p, .suite-desc, .case-row p { color: var(--app-text-secondary); }.page-header p, .drawer-header p, .section-heading p { margin: 6px 0 0; }.toolbar { justify-content: flex-start; }.toolbar .el-input { width: 340px; }.toolbar .el-select { width: 160px; }.suite-title { font-weight: 600; }.suite-desc { margin-top: 4px; font-size: 12px; }.el-pagination { justify-content: flex-end; }
.drawer-header, .section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; width: 100%; }.form-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }.section-card { margin-top: 18px; padding: 18px; border: 1px solid var(--app-border); border-radius: 10px; }.variable-row { display: grid; grid-template-columns: 1fr 1fr 1fr auto auto auto; gap: 8px; align-items: center; margin-bottom: 8px; }.case-row { display: grid; grid-template-columns: 40px 1fr auto; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--app-border); border-radius: 8px; margin-bottom: 8px; }.case-row p { margin: 4px 0 0; font-size: 12px; }.order-index { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--el-fill-color-light); }.order-actions { display: flex; gap: 6px; }.run-form { margin-top: 14px; }.runtime-row { display: grid; grid-template-columns: 1fr 1fr auto auto; gap: 8px; align-items: center; margin-bottom: 8px; }
@media (max-width: 900px) { .page-header, .toolbar, .drawer-header, .section-heading { flex-direction: column; align-items: stretch; }.toolbar .el-input, .toolbar .el-select { width: 100%; }.form-grid, .variable-row, .runtime-row, .case-row { grid-template-columns: 1fr; }.order-actions { flex-wrap: wrap; } }
</style>
