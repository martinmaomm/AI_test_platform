<template>
  <div class="scenario-list">
    <!-- 头部 -->
    <div class="list-header">
      <span class="list-title">场景用例</span>
      <el-tooltip content="刷新列表">
        <el-button :icon="Refresh" circle size="small" :loading="loading" @click="loadScenarios" />
      </el-tooltip>
    </div>

    <!-- 批量操作栏 -->
    <div v-if="selectedScenarios.length > 0" class="batch-actions-overlay">
      <div class="batch-info">
        <span>已选择 {{ selectedScenarios.length }} 个场景用例</span>
      </div>
      <div class="batch-buttons">
        <el-button type="success" size="small" @click="handleBatchJoinSuite">
          <el-icon><FolderAdd /></el-icon>
          批量加入套件
        </el-button>
        <el-button type="danger" size="small" @click="handleBatchDelete">
          <el-icon><Delete /></el-icon>
          批量删除
        </el-button>
        <el-button size="small" @click="clearSelection">
          <el-icon><Close /></el-icon>
          取消选择
        </el-button>
      </div>
    </div>

    <!-- 搜索框 -->
    <div class="list-search">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索场景名称..."
        size="small"
        clearable
        :prefix-icon="Search"
      />
    </div>

    <!-- 无项目提示 -->
    <div v-if="!projectId" class="list-empty">
      <el-text type="info" size="small">请先选择项目</el-text>
    </div>

    <!-- 加载中 -->
    <div v-else-if="loading" class="list-loading">
      <el-skeleton :rows="5" animated />
    </div>

    <!-- 空列表 -->
    <div v-else-if="filteredScenarios.length === 0" class="list-empty">
      <el-empty description="暂无场景用例" :image-size="60" />
    </div>

    <!-- 场景列表 -->
    <Draggable
      v-else
      v-model="draggableList"
      item-key="id"
      handle=".drag-handle"
      class="list-items"
      :animation="150"
      @end="handleDragEnd"
    >
      <template #item="{ element: scenario }">
        <div
          class="list-item"
          :class="{ 'is-active': scenario.id === activeScenarioId }"
          @click="handleSelect(scenario)"
        >
          <div class="drag-handle" title="拖拽排序" @click.stop>
            <el-icon><Rank /></el-icon>
          </div>
          <el-checkbox
            :model-value="selectedScenarios.some(s => s.id === scenario.id)"
            @update:model-value="(v) => toggleSelection(scenario, v)"
            @click.stop
            class="item-checkbox"
          />
          <!-- 图标 -->
          <div class="item-icon">
          <el-icon><DataAnalysis /></el-icon>
        </div>

        <!-- 内容 -->
        <div class="item-content">
          <div class="item-name" :title="scenario.title">{{ scenario.title }}</div>
          <div class="item-meta">
            <span class="meta-steps">
              <el-icon style="vertical-align: -2px; font-size: 11px;"><List /></el-icon>
              {{ scenario.steps_count ?? 0 }} 个步骤
            </span>
          </div>
        </div>

        <!-- 更多操作按钮 -->
        <el-dropdown
          trigger="click"
          placement="bottom-end"
          @command="(cmd) => handleAction(cmd, scenario)"
          @click.stop
        >
          <el-button
            class="item-more"
            :icon="MoreFilled"
            size="small"
            text
            @click.stop
          />
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="rename" :icon="Edit">重命名</el-dropdown-item>
              <el-dropdown-item command="copy" :icon="DocumentCopy">复制</el-dropdown-item>
              <el-dropdown-item command="joinSuite" :icon="FolderAdd">加入测试套件</el-dropdown-item>
              <el-dropdown-item command="delete" :icon="Delete" divided class="danger-item">
                删除
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        </div>
      </template>
    </Draggable>

    <!-- 底部统计 -->
    <div class="list-footer" v-if="scenarios.length > 0">
      <el-text size="small" type="info">共 {{ scenarios.length }} 个场景</el-text>
    </div>

    <!-- 加入测试套件弹窗 -->
    <SuiteSelectionDialog
      v-model="showSuiteDialog"
      :project-id="projectId"
      :case-ids="suiteCaseIds"
      @success="onSuiteDialogSuccess"
    />

    <!-- 重命名对话框 -->
    <el-dialog
      v-model="renameDialog.visible"
      title="重命名场景"
      width="400px"
      :close-on-click-modal="false"
      @close="resetRenameDialog"
    >
      <el-form @submit.prevent="confirmRename">
        <el-form-item label="场景名称" label-width="80px">
          <el-input
            v-model="renameDialog.newTitle"
            ref="renameInputRef"
            placeholder="请输入新的场景名称"
            maxlength="200"
            show-word-limit
            clearable
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="renameDialog.saving"
          @click="confirmRename"
        >
          确认
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { Refresh, Search, DataAnalysis, List, MoreFilled, Edit, Delete, DocumentCopy, FolderAdd, Rank, Close } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, ElLoading } from 'element-plus'
import { getAPITestCases, getAPITestCase, createAPITestCase, patchAPITestCase, deleteAPITestCase, batchDeleteAPITestCases, updateScenarioTestCasesOrder } from '@/api/apiTesting'
import SuiteSelectionDialog from '@/components/SuiteSelectionDialog.vue'
import Draggable from 'vuedraggable'

const props = defineProps({
  projectId: { type: [Number, String], default: null },
  activeScenarioId: { type: [Number, String], default: null }
})

const emit = defineEmits(['select', 'deleted', 'renamed', 'selection-change'])

const loading = ref(false)
const selectedScenarios = ref([])
const scenarios = ref([])
const searchKeyword = ref('')

// 重命名对话框状态
const renameDialog = ref({ visible: false, scenario: null, newTitle: '', saving: false })
const renameInputRef = ref(null)

// -------- 过滤 --------
const filteredScenarios = computed(() => {
  if (!searchKeyword.value.trim()) return scenarios.value
  const kw = searchKeyword.value.trim().toLowerCase()
  return scenarios.value.filter(s => (s.title || '').toLowerCase().includes(kw))
})

// 可拖拽列表（与 filteredScenarios 同步，供 Draggable v-model）
const draggableList = ref([])
watch(filteredScenarios, (val) => {
  draggableList.value = [...val]
}, { immediate: true })

// 拖拽结束：同步回 scenarios 并调用后端
const handleDragEnd = async () => {
  const list = draggableList.value
  const idsInList = new Set(list.map(s => s.id))
  const rest = scenarios.value.filter(s => !idsInList.has(s.id))
  scenarios.value = [...list, ...rest]
  const caseIds = list.map(s => s.id)
  if (caseIds.length === 0) return
  try {
    await updateScenarioTestCasesOrder(props.projectId, caseIds)
    ElMessage.success('场景顺序已更新')
  } catch (e) {
    ElMessage.error('更新场景顺序失败：' + (e?.message || '未知错误'))
    loadScenarios()
  }
}

// -------- 从 script_content 计算步骤数 --------
// scriptContent 可以是字符串、对象或 null
const calcStepsCount = (scriptContent) => {
  try {
    const sc = typeof scriptContent === 'string'
      ? JSON.parse(scriptContent)
      : (scriptContent || {})
    return Array.isArray(sc?.teststeps) ? sc.teststeps.length : 0
  } catch {
    return 0
  }
}

// -------- 加载数据 --------
const loadScenarios = async () => {
  if (!props.projectId) return
  loading.value = true
  try {
    const response = await getAPITestCases(props.projectId, { test_case_type: 'scenario' })
    let list = []
    if (response && response.success && response.data) {
      list = response.data.items ?? response.data ?? []
    } else if (Array.isArray(response)) {
      list = response
    }
    // 后端已返回 steps_count（由 script_content 计算），
    // 仅在缺失时用 script_content 降级计算（兼容旧接口）
    list.forEach(item => {
      if (item.steps_count == null) {
        item.steps_count = calcStepsCount(item.script_content)
      }
    })
    scenarios.value = list
  } catch (e) {
    ElMessage.error('加载场景用例失败：' + (e.message || '未知错误'))
  } finally {
    loading.value = false
  }
}

// -------- 操作分发 --------
const handleSelect = (scenario) => emit('select', scenario)

const toggleSelection = (scenario, checked) => {
  if (checked) {
    if (!selectedScenarios.value.some(s => s.id === scenario.id)) {
      selectedScenarios.value = [...selectedScenarios.value, scenario]
    }
  } else {
    selectedScenarios.value = selectedScenarios.value.filter(s => s.id !== scenario.id)
  }
  emit('selection-change', selectedScenarios.value)
}

const handleAction = (command, scenario) => {
  if (command === 'rename')    openRenameDialog(scenario)
  if (command === 'copy')      copyScenario(scenario)
  if (command === 'joinSuite') openSuiteDialog(scenario)
  if (command === 'delete')    confirmDelete(scenario)
}

// -------- 加入测试套件 --------
const showSuiteDialog = ref(false)
const suiteCaseIds    = ref([])

const openSuiteDialog = (scenario) => {
  suiteCaseIds.value    = [scenario.id]
  showSuiteDialog.value = true
}

const onSuiteDialogSuccess = () => {
  clearSelection()
}

const handleBatchJoinSuite = () => {
  if (selectedScenarios.value.length === 0) return
  suiteCaseIds.value = selectedScenarios.value.map(s => s.id)
  showSuiteDialog.value = true
}

const handleBatchDelete = async () => {
  if (selectedScenarios.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedScenarios.value.length} 个场景用例吗？此操作不可恢复。`,
      '批量删除确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    const caseIds = selectedScenarios.value.map(s => s.id)
    const res = await batchDeleteAPITestCases(props.projectId, caseIds)
    if (res?.success !== false) {
      ElMessage.success(`成功删除 ${res?.data?.deleted_count ?? caseIds.length} 个场景用例`)
    } else {
      throw new Error(res?.message || '批量删除失败')
    }
    const deletedIds = new Set(caseIds)
    selectedScenarios.value.forEach(s => { if (deletedIds.has(s.id)) emit('deleted', s) })
    clearSelection()
    loadScenarios()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('批量删除失败：' + (e?.message || '未知错误'))
    }
  }
}

const clearSelection = () => {
  selectedScenarios.value = []
  emit('selection-change', [])
}

// -------- 复制（克隆）场景 --------
const copyScenario = async (scenario) => {
  // 第一步：让用户确认新名称，默认附加" - 副本"
  let newTitle
  try {
    const { value } = await ElMessageBox.prompt(
      '克隆后的新场景将与原场景完全独立，所有步骤均为深拷贝。',
      '复制场景',
      {
        confirmButtonText: '确认复制',
        cancelButtonText: '取消',
        inputValue: `${scenario.title} - 副本`,
        inputPlaceholder: '请输入新场景名称',
        inputValidator: (v) => (v?.trim() ? true : '场景名称不能为空'),
      }
    )
    newTitle = value.trim()
  } catch {
    return // 用户取消
  }

  // 第二步：拉取完整详情（列表数据可能缺少 script_content）
  const loading = ElLoading.service({ text: '正在复制场景…', background: 'rgba(0,0,0,0.4)' })
  try {
    const res = await getAPITestCase(props.projectId, scenario.id)
    const detail = res?.data ?? res

    // 第三步：深拷贝 script_content 并更新内部名称
    let clonedScript
    try {
      const originalScript = typeof detail.script_content === 'string'
        ? JSON.parse(detail.script_content)
        : detail.script_content
      clonedScript = JSON.parse(JSON.stringify(originalScript))
      if (clonedScript?.config) clonedScript.config.name = newTitle
    } catch {
      // script_content 解析失败时用空场景兜底
      clonedScript = { config: { name: newTitle }, teststeps: [] }
    }

    // 第四步：构造 POST Payload —— 严格遵循序列化器的 scenario 规则
    // （endpoint / test_type 对 scenario 类型属于禁止字段，不传）
    const payload = {
      title: newTitle,
      test_case_type: 'scenario',
      description: detail.description || `由"${scenario.title}"复制`,
      timeout: detail.timeout ?? 10,
      retry_count: detail.retry_count ?? 0,
      script_content: JSON.stringify(clonedScript),
    }

    const createRes = await createAPITestCase(props.projectId, payload)
    const newScenario = createRes?.data ?? createRes

    // 第五步：乐观更新列表（追加到末尾，不重新 fetch 避免闪烁）
    const stepsCount = Array.isArray(clonedScript?.teststeps)
      ? clonedScript.teststeps.length
      : 0
    scenarios.value.push({
      ...newScenario,
      steps_count: newScenario.steps_count ?? stepsCount,
    })

    ElMessage.success(`复制成功，新场景"${newTitle}"已创建`)
  } catch (e) {
    ElMessage.error('复制失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  } finally {
    loading.close()
  }
}

// -------- 重命名 --------
const openRenameDialog = (scenario) => {
  renameDialog.value = { visible: true, scenario, newTitle: scenario.title, saving: false }
  nextTick(() => renameInputRef.value?.focus())
}

const resetRenameDialog = () => {
  renameDialog.value = { visible: false, scenario: null, newTitle: '', saving: false }
}

const confirmRename = async () => {
  const { scenario, newTitle } = renameDialog.value
  if (!newTitle.trim()) {
    ElMessage.warning('场景名称不能为空')
    return
  }
  if (newTitle.trim() === scenario.title) {
    renameDialog.value.visible = false
    return
  }
  renameDialog.value.saving = true
  try {
    await patchAPITestCase(props.projectId, scenario.id, { title: newTitle.trim() })
    ElMessage.success('重命名成功')
    // 本地同步更新
    const idx = scenarios.value.findIndex(s => s.id === scenario.id)
    if (idx !== -1) scenarios.value[idx] = { ...scenarios.value[idx], title: newTitle.trim() }
    emit('renamed', { ...scenario, title: newTitle.trim() })
    renameDialog.value.visible = false
  } catch (e) {
    ElMessage.error('重命名失败：' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    renameDialog.value.saving = false
  }
}

// -------- 删除 --------
const confirmDelete = async (scenario) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除场景"${scenario.title}"吗？此操作不可撤销。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' }
    )
    await deleteAPITestCase(props.projectId, scenario.id)
    ElMessage.success('删除成功')
    scenarios.value = scenarios.value.filter(s => s.id !== scenario.id)
    selectedScenarios.value = selectedScenarios.value.filter(s => s.id !== scenario.id)
    emit('selection-change', selectedScenarios.value)
    emit('deleted', scenario)
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
    }
  }
}

// -------- 监听 --------
watch(() => props.projectId, (val) => {
  if (val) loadScenarios()
  else scenarios.value = []
})

onMounted(() => {
  if (props.projectId) loadScenarios()
})

/**
 * 父组件保存场景后调用此方法，在不重新 fetch 的前提下刷新指定条目的步骤数。
 * @param {number|string} scenarioId
 * @param {string|object} scriptContent  最新的 script_content
 */
const refreshStepsCount = (scenarioId, scriptContent) => {
  const idx = scenarios.value.findIndex(s => s.id === scenarioId)
  if (idx !== -1) {
    scenarios.value[idx].steps_count = calcStepsCount(scriptContent)
  }
}

/**
 * 按 id 自动选中并高亮场景（供父组件路由跳转后调用）
 * 如果列表中还没加载到该 id，则等列表刷新完再触发一次
 */
const selectById = async (id) => {
  // 如果当前列表为空或找不到，先刷新一次
  let target = scenarios.value.find(s => s.id === id || s.id === Number(id))
  if (!target) {
    await loadScenarios()
    target = scenarios.value.find(s => s.id === id || s.id === Number(id))
  }
  if (target) emit('select', target)
}

// 暴露刷新方法供父组件调用
defineExpose({ loadScenarios, refreshStepsCount, selectById, selectedScenarios, clearSelection: () => { selectedScenarios.value = []; emit('selection-change', []) } })
</script>

<style scoped>
.scenario-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--el-bg-color);
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.list-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.batch-actions-overlay {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px;
  background-color: #f0f9ff;
  border-bottom: 1px solid #b3d8ff;
  flex-shrink: 0;
}

.batch-info {
  font-size: 13px;
  color: #409eff;
  font-weight: 500;
}

.batch-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.list-search {
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.list-loading,
.list-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.list-items {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

/* 列表项 */
.list-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px 9px 14px;
  cursor: pointer;
  transition: background 0.15s;
  border-left: 3px solid transparent;
  position: relative;
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  color: #909399;
  cursor: grab;
  opacity: 0;
  transition: opacity 0.2s;
  margin-right: 4px;
  flex-shrink: 0;
}

.item-checkbox {
  flex-shrink: 0;
  margin-right: 4px;
}

.drag-handle:active {
  cursor: grabbing;
}

.list-item:hover .drag-handle {
  opacity: 0.6;
}

.list-item:hover .drag-handle:hover {
  opacity: 1;
  color: #409eff;
}

.list-item:hover {
  background: var(--el-fill-color-light);
}

.list-item.is-active {
  background: var(--el-color-primary-light-9);
  border-left-color: var(--el-color-primary);
}

.item-icon {
  flex-shrink: 0;
  color: var(--el-color-primary);
  font-size: 15px;
}

.item-content {
  flex: 1;
  min-width: 0;
}

.item-name {
  font-size: 13px;
  color: var(--el-text-color-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}

.list-item.is-active .item-name {
  color: var(--el-color-primary);
}

.item-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.meta-steps {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 更多按钮：默认隐藏，hover 时显示 */
.item-more {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
  color: var(--el-text-color-secondary) !important;
}

.list-item:hover .item-more,
.list-item.is-active .item-more {
  opacity: 1;
}

/* 危险操作 */
:deep(.danger-item) {
  color: var(--el-color-danger) !important;
}

.list-footer {
  padding: 8px 14px;
  border-top: 1px solid var(--el-border-color-lighter);
  text-align: center;
}
</style>
