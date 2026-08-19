<template>
  <div class="page-objects-container" v-if="selectedProject">
    <div class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Collection /></el-icon>
          </div>
          <div class="header-text">
            <h2>元素库管理</h2>
            <p>管理页面对象 (POM)，维护页面与元素的定位器</p>
          </div>
        </div>
      </div>
    </div>

    <el-row :gutter="16" class="content-row">
      <!-- 左侧：页面列表 -->
      <el-col :span="8">
        <el-card class="pages-card" shadow="hover">
          <template #header>
            <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span style="font-weight: bold;">页面列表（按模块分组）</span>
              <div style="display: flex; gap: 10px;">
                <el-button type="danger" plain size="small" @click="handleClearAll">
                  <el-icon><Delete /></el-icon>
                  一键清空
                </el-button>
                <el-button type="success" size="small" @click="openExtractDialog" plain>
                  <el-icon><Document /></el-icon>
                  智能提取
                </el-button>
                <el-button type="primary" size="small" @click="openPageDialog()">
                  <el-icon><Plus /></el-icon>
                  新增
                </el-button>
              </div>
            </div>
          </template>
          <div class="page-tree-container" v-loading="pagesLoading">
            <el-input
              v-model="searchPageKeyword"
              placeholder="搜索模块或页面..."
              :prefix-icon="Search"
              clearable
              class="tree-search"
            />
            <el-tree
              v-if="pageTreeData.length > 0"
              ref="pageTreeRef"
              :data="pageTreeData"
              :props="{ children: 'children', label: 'label' }"
              node-key="id"
              default-expand-all
              :filter-node-method="filterNode"
              highlight-current
              @node-click="handleNodeClick"
              class="custom-tree"
            >
              <template #default="{ node, data }">
                <span class="custom-tree-node">
                  <el-icon v-if="data.isModule" class="module-icon"><Folder /></el-icon>
                  <el-icon v-if="data.isPage" class="page-icon"><Document /></el-icon>
                  <span class="node-label">{{ node.label }}</span>
                  <span v-if="data.isPage" class="node-actions" @click.stop>
                    <el-button link type="primary" size="small" @click="openPageDialog(data)">编辑</el-button>
                    <el-button link type="danger" size="small" @click="deletePageConfirm(data)">删除</el-button>
                  </span>
                </span>
              </template>
            </el-tree>
            <el-empty v-else-if="!pagesLoading" description="暂无页面，点击上方新增" />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：元素表格 -->
      <el-col :span="16">
        <el-card class="elements-card" shadow="hover">
          <template #header>
            <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span style="font-weight: bold;">
                {{ selectedPage ? selectedPage.name + ' - 元素列表' : '请先选择页面' }}
              </span>
              <div style="display: flex; gap: 10px;">
                <el-button
                  type="danger"
                  size="small"
                  :disabled="selectedElementIds.length === 0"
                  @click="batchDeleteElements"
                  plain
                >
                  <el-icon><Delete /></el-icon>批量删除
                </el-button>
                <el-button
                  type="warning"
                  size="small"
                  :disabled="!selectedPage || elements.length === 0"
                  @click="openFillDialog"
                  plain
                >
                  <el-icon><MagicStick /></el-icon>智能回填
                </el-button>
                <el-button
                  type="primary"
                  size="small"
                  :disabled="!selectedPage"
                  @click="openElementDialog()"
                >
                  <el-icon><Plus /></el-icon>新增元素
                </el-button>
              </div>
            </div>
          </template>
          <div class="elements-table-wrap" v-loading="elementsLoading">
            <el-table
              v-if="selectedPage"
              ref="elementTableRef"
              :data="elements"
              :row-key="(row) => row.id"
              style="width: 100%"
              stripe
              @selection-change="handleElementSelectionChange"
            >
              <el-table-column type="selection" width="55" />
              <el-table-column prop="name" label="元素名称" min-width="120" />
              <el-table-column prop="locator_type" label="定位器类型" width="120">
                <template #default="scope">
                  <el-tag
                    size="small"
                    :type="scope.row.locator_type ? 'primary' : 'info'"
                  >
                    {{ scope.row.locator_type || '待提取' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="locator_value" label="定位器值" min-width="200" show-overflow-tooltip />
              <el-table-column prop="action_type" label="操作类型" width="100">
                <template #default="scope">
                  {{ scope.row.action_type || '-' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="scope">
                  <el-button type="primary" link size="small" @click="openElementDialog(scope.row)">编辑</el-button>
                  <el-button type="danger" link size="small" @click="deleteElementConfirm(scope.row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="请从左侧选择一个页面" />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 页面编辑弹窗 -->
    <el-dialog
      v-model="pageDialogVisible"
      :title="editingPage ? '编辑页面' : '新增页面'"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form ref="pageFormRef" :model="pageForm" :rules="pageRules" label-width="100px">
        <el-form-item label="页面名称" prop="name">
          <el-input v-model="pageForm.name" placeholder="如：登录页、首页" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pageDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="savePage" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 元素编辑弹窗 -->
    <el-dialog
      v-model="elementDialogVisible"
      :title="editingElement ? '编辑元素' : '新增元素'"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form ref="elementFormRef" :model="elementForm" :rules="elementRules" label-width="100px">
        <el-form-item label="元素名称" prop="name">
          <el-input v-model="elementForm.name" placeholder="如：登录按钮、用户名输入框" />
        </el-form-item>
        <el-form-item label="定位器类型" prop="locator_type">
          <el-select v-model="elementForm.locator_type" placeholder="选择定位方式" filterable allow-create>
            <el-option
              v-for="opt in LOCATOR_TYPES"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            >
              <span>{{ opt.label }}</span>
              <span class="option-tip">→ {{ opt.tip }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="定位器值" prop="locator_value">
          <el-input
            v-model="elementForm.locator_value"
            type="textarea"
            :rows="3"
            :placeholder="locatorTip"
          />
          <div class="input-tip">示例：{{ locatorTip }}</div>
        </el-form-item>
        <el-form-item label="操作类型" prop="action_type">
          <div class="action-type-wrap">
            <el-select v-model="elementForm.action_type" placeholder="选择操作类型（可选）" clearable style="flex: 1">
              <el-option
                v-for="opt in ACTION_TYPES"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <el-tooltip
              v-if="elementForm.action_type"
              placement="top"
              :content="ACTION_USAGE[elementForm.action_type] || ''"
            >
              <el-icon class="action-tip-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="elementDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveElement" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 智能提取 POM 弹窗 -->
    <el-dialog
      v-model="extractDialogVisible"
      title="✨ 从需求文档智能生成 POM"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form label-width="100px">
        <el-alert
          title="系统将自动解析文档内容，为您创建页面及元素骨架。"
          type="info"
          show-icon
          style="margin-bottom: 20px;"
          :closable="false"
        />
        <el-form-item label="选择文档" required>
          <el-select
            v-model="selectedKnowledgeFileId"
            placeholder="请选择知识库中的需求规格说明书"
            style="width: 100%"
          >
            <el-option
              v-for="file in knowledgeFiles"
              :key="file.id"
              :label="file.file_name"
              :value="file.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="extractDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="submitExtractPom" :loading="isExtracting">
            开始抽取
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 智能回填定位器弹窗 -->
    <el-dialog
      v-model="fillDialogVisible"
      title="✨ 从 HTML 源码智能匹配定位器"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-form v-loading="isFilling" element-loading-text="大模型正在努力将元素与源码进行连连看匹配...">
        <el-alert
          title="请在目标测试网页按 F12，复制对应的 HTML 源码片段并粘贴到下方。大模型将自动为当前页面的元素寻找最精准的 CSS 定位器。"
          type="info"
          show-icon
          style="margin-bottom: 20px;"
          :closable="false"
        />
        <el-input
          v-model="htmlSource"
          type="textarea"
          :rows="12"
          placeholder="请在此粘贴 HTML 源码 (例如 <div id='app'>...</div>)"
        />
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="fillDialogVisible = false" :disabled="isFilling">取消</el-button>
          <el-button type="primary" @click="submitFillLocators" :loading="isFilling">
            开始匹配回填
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 删除确认 -->
    <el-dialog v-model="deleteDialogVisible" title="确认删除" width="400px">
      <p>{{ deleteConfirmMessage }}</p>
      <template #footer>
        <el-button @click="deleteDialogVisible = false">取消</el-button>
        <el-button type="danger" @click="confirmDelete" :loading="deleting">删除</el-button>
      </template>
    </el-dialog>
  </div>

  <div v-else class="no-project-tip">
    <el-empty description="请先选择或创建项目">
      <el-button type="primary" @click="$router.push('/project/project-list')">去选择项目</el-button>
    </el-empty>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { Plus, Collection, QuestionFilled, Document, Delete, MagicStick, Folder, Search } from '@element-plus/icons-vue'
import {
  getWebPages,
  createWebPage,
  updateWebPage,
  deleteWebPage,
  getWebElements,
  createWebElement,
  updateWebElement,
  deleteWebElement,
  extractPomFromDoc,
  fillLocatorsFromHtml,
  batchDeleteWebElements,
  getTaskStatus,
  clearProjectWebAssets,
  getWebUIActionsDict
} from '@/api/webTesting'
import { getProjectKnowledgeFiles } from '@/api/projects'
import { useProjectStore } from '@/stores/project'

const projectStore = useProjectStore()
const selectedProject = computed(() => projectStore.currentProject)
const projectId = computed(() => projectStore.currentProjectId)

// 组件挂载状态，防止销毁后仍更新 DOM 导致报错
const isComponentMounted = ref(true)
onUnmounted(() => {
  isComponentMounted.value = false
})

// 定位方式选项（含案例提醒）
const LOCATOR_TYPES = [
  { value: 'role', label: 'role', tip: "button, [name='登录']" },
  { value: 'text', label: 'text', tip: '请输入文本内容' },
  { value: 'label', label: 'label', tip: '用户名' },
  { value: 'placeholder', label: 'placeholder', tip: '请输入密码' },
  { value: 'test_id', label: 'test_id', tip: 'data-testid 的值' },
  { value: 'id', label: 'id', tip: '#login-submit' },
  { value: 'xpath', label: 'xpath', tip: "//div[@class='btn']" },
  { value: 'css', label: 'css', tip: '.submit-button' }
]

// 操作类型选项（从后端动态拉取）
const ACTION_TYPES = ref([])

// 加载 WebUI 动作配置
async function loadWebUIActions() {
  if (!projectId.value) return
  try {
    const res = await getWebUIActionsDict(projectId.value)
    const data = res?.data ?? res
    ACTION_TYPES.value = Array.isArray(data) ? data : (data?.data ?? data?.results ?? [])
  } catch (e) {
    console.error('获取动作字典失败:', e)
    ACTION_TYPES.value = []
  }
}

// 操作类型详细用法（用于悬停提示，优先从后端配置的 label 展示）
const ACTION_USAGE = computed(() => {
  const map = {}
  ACTION_TYPES.value.forEach(opt => {
    map[opt.value] = opt.label || opt.value
  })
  return map
})


// 当前定位方式的案例提醒（切换定位方式时实时更新）
const locatorTip = computed(() => {
  const item = LOCATOR_TYPES.find(t => t.value === elementForm.value.locator_type)
  return item?.tip ?? (elementForm.value.locator_type ? '请输入该定位方式对应的值' : '请选择定位方式')
})

// 页面列表
const pages = ref([])
const pagesLoading = ref(false)
const selectedPage = ref(null)

// 树形结构：按模块分组的页面树
const searchPageKeyword = ref('')
const pageTreeRef = ref(null)

const pageTreeData = computed(() => {
  const moduleMap = new Map()
  const unassignedPages = []

  pages.value.forEach(page => {
    const moduleName = page.module_name || (page.module && page.module.name)
    const moduleId = page.module_id || page.module?.id

    if (moduleName || moduleId) {
      const key = moduleId ? `module_${moduleId}` : `module_${moduleName}`
      if (!moduleMap.has(key)) {
        moduleMap.set(key, {
          id: key,
          label: moduleName || '未命名模块',
          isModule: true,
          children: []
        })
      }
      moduleMap.get(key).children.push({
        ...page,
        label: page.name,
        isPage: true
      })
    } else {
      unassignedPages.push({
        ...page,
        label: page.name,
        isPage: true
      })
    }
  })

  const tree = Array.from(moduleMap.values())
  if (unassignedPages.length > 0) {
    tree.push({
      id: 'module_unassigned',
      label: '未归属模块',
      isModule: true,
      children: unassignedPages
    })
  }
  return tree
})

const filterNode = (value, data) => {
  if (!value) return true
  const keyword = value.toLowerCase()
  return data.label?.toLowerCase().includes(keyword)
}

watch(searchPageKeyword, (val) => {
  pageTreeRef.value?.filter(val)
})

const handleNodeClick = (data) => {
  if (data.isPage) {
    selectedPage.value = data
    pageTreeRef.value?.setCurrentKey(data.id)
  }
}

// 元素列表
const elements = ref([])
const elementsLoading = ref(false)
const selectedElementIds = ref([])
const elementTableRef = ref(null)

// 弹窗
const pageDialogVisible = ref(false)
const elementDialogVisible = ref(false)
const editingPage = ref(null)
const editingElement = ref(null)
const saving = ref(false)
const pageFormRef = ref(null)
const elementFormRef = ref(null)

const pageForm = ref({ name: '' })
const pageRules = {
  name: [{ required: true, message: '请输入页面名称', trigger: 'blur' }]
}

const elementForm = ref({
  name: '',
  locator_type: 'css',
  locator_value: '',
  action_type: ''
})
const elementRules = {
  name: [{ required: true, message: '请输入元素名称', trigger: 'blur' }],
  locator_type: [{ required: true, message: '请选择或输入定位器类型', trigger: 'blur' }],
  locator_value: [{ required: true, message: '请输入定位器值', trigger: 'blur' }]
}

// 智能提取 POM
const extractDialogVisible = ref(false)
const isExtracting = ref(false)
const selectedKnowledgeFileId = ref(null)
const knowledgeFiles = ref([])

// 智能回填定位器
const fillDialogVisible = ref(false)
const isFilling = ref(false)
const htmlSource = ref('')

// 删除确认
const deleteDialogVisible = ref(false)
const deleting = ref(false)
const deleteTarget = ref(null)
const deleteType = ref('') // 'page' | 'element'
const deleteConfirmMessage = computed(() => {
  if (deleteType.value === 'page') {
    return `确定要删除页面「${deleteTarget.value?.name}」吗？该页面下的所有元素也会被删除。`
  }
  return `确定要删除元素「${deleteTarget.value?.name}」吗？`
})

// 加载页面列表
async function loadPages() {
  if (!projectId.value) return
  pagesLoading.value = true
  try {
    const data = await getWebPages(projectId.value)
    pages.value = Array.isArray(data) ? data : (data?.results ?? data?.data ?? [])
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '加载页面列表失败')
    pages.value = []
  } finally {
    pagesLoading.value = false
  }
}

// 加载元素列表
async function loadElements() {
  if (!projectId.value || !selectedPage.value) {
    elements.value = []
    return
  }
  elementsLoading.value = true
  try {
    const data = await getWebElements(projectId.value, selectedPage.value.id)
    elements.value = Array.isArray(data) ? data : (data?.results ?? data?.data ?? [])
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '加载元素列表失败')
    elements.value = []
  } finally {
    elementsLoading.value = false
  }
}

function selectPage(page) {
  selectedPage.value = page
}

// 监听元素表格的多选
function handleElementSelectionChange(selection) {
  selectedElementIds.value = selection.map(item => item.id)
}

// 批量删除页面
async function batchDeletePages() {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedPageIds.value.length} 个页面及其所有包含的元素吗？此操作不可逆！`,
      '警告',
      { type: 'warning' }
    )
    await batchDeleteWebPages(projectId.value, selectedPageIds.value)
    ElMessage.success('批量删除页面成功')
    selectedPageIds.value = []
    await loadPages()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.message || '批量删除失败')
  }
}

// 批量删除元素
async function batchDeleteElements() {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedElementIds.value.length} 个元素吗？`,
      '警告',
      { type: 'warning' }
    )
    await batchDeleteWebElements(projectId.value, selectedElementIds.value)
    ElMessage.success('批量删除元素成功')
    selectedElementIds.value = []
    elementTableRef.value?.clearSelection?.()
    await loadElements()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.message || '批量删除失败')
  }
}

// 一键清空项目测试资产（调试专用）
const handleClearAll = async () => {
  try {
    await ElMessageBox.confirm(
      '警告：此操作将物理删除当前项目下【所有的模块、页面及元素】！该操作不可逆，确定要继续吗？',
      '一键清空测试资产 (调试专用)',
      {
        confirmButtonText: '确定核爆',
        cancelButtonText: '取消',
        type: 'error',
        center: true
      }
    )

    const id = projectId.value
    if (!id) {
      ElMessage.error('未选择项目')
      return
    }
    const res = await clearProjectWebAssets(id)

    if (res?.success || res?.code === 200) {
      ElMessage.success(res?.message || '资产已全部清空！')
      selectedPage.value = null
      await loadPages()
    } else {
      ElMessage.error(res?.message || '清空失败')
    }
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error(err?.response?.data?.message || err?.message || '清空失败，请查看控制台')
    }
  }
}

// 打开智能提取弹窗
async function openExtractDialog() {
  extractDialogVisible.value = true
  selectedKnowledgeFileId.value = null
  knowledgeFiles.value = []
  if (!selectedProject.value?.id) return
  try {
    const res = await getProjectKnowledgeFiles(selectedProject.value.id, { page_size: 100 })
    const responseData = res?.data ?? res
    const items = responseData?.data?.data?.items ?? responseData?.data?.items ?? responseData?.items ?? responseData?.results ?? []
    knowledgeFiles.value = Array.isArray(items) ? items : []
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '加载知识库文档列表失败')
  }
}

// 打开智能回填弹窗
function openFillDialog() {
  htmlSource.value = ''
  fillDialogVisible.value = true
}

// 提交智能回填定位器（异步提交 -> 关闭弹窗 -> 后台轮询）
async function submitFillLocators() {
  if (!htmlSource.value.trim()) {
    ElMessage.warning('请粘贴 HTML 源码！')
    return
  }
  if (!selectedProject.value?.id || !selectedPage.value?.id) return

  try {
    isFilling.value = true
    const res = await fillLocatorsFromHtml(
      selectedProject.value.id,
      selectedPage.value.id,
      htmlSource.value.trim()
    )
    const taskId = res?.task_id

    if (taskId) {
      fillDialogVisible.value = false
      ElNotification({
        title: '🚀 任务已提交',
        message: '后台正在为您匹配 HTML 源码与元素定位器，您可以继续其他操作。',
        type: 'info',
        duration: 0,
        position: 'bottom-right'
      })
      pollFillLocatorsTaskStatus(taskId)
    } else {
      ElMessage.error(res?.message || '提交失败，未获取到任务ID')
    }
  } catch (error) {
    console.error(error)
    ElMessage.error(error?.response?.data?.message || '请求失败')
  } finally {
    isFilling.value = false
  }
}

// 后台静默轮询智能回填任务状态
async function pollFillLocatorsTaskStatus(taskId) {
  let isFinished = false
  const maxRetries = 200
  let attempts = 0

  while (!isFinished && attempts < maxRetries && isComponentMounted.value) {
    await new Promise(resolve => setTimeout(resolve, 3000))
    attempts++

    try {
      const res = await getTaskStatus(projectId.value, taskId)
      const responseData = res?.data ?? res
      const data = responseData?.data ?? responseData
      const status = String(data?.status || data?.state || '').toUpperCase()

      if (status === 'SUCCESS' || status === 'COMPLETED') {
        isFinished = true
        const msg = data?.result?.message || data?.message || '定位器回填完成！'
        ElNotification({
          title: '✅ 回填完成',
          message: msg,
          type: 'success',
          duration: 0,
          position: 'bottom-right'
        })
        if (isComponentMounted.value) {
          await loadElements()
        }
      } else if (status === 'FAILURE' || status === 'FAILED') {
        isFinished = true
        ElNotification({
          title: '❌ 回填失败',
          message: data?.error || data?.message || '大模型匹配定位器过程中发生异常，请检查系统日志。',
          type: 'error',
          duration: 0,
          position: 'bottom-right'
        })
      }
    } catch (e) {
      console.error('轮询任务状态失败:', e)
    }
  }
}

// 提交智能提取（异步提交 -> 关闭弹窗 -> 后台轮询）
async function submitExtractPom() {
  if (!selectedKnowledgeFileId.value) {
    ElMessage.warning('请选择知识库文档')
    return
  }
  if (!selectedProject.value?.id) return

  try {
    isExtracting.value = true
    const res = await extractPomFromDoc(selectedProject.value.id, selectedKnowledgeFileId.value)
    const taskId = res?.task_id

    if (taskId) {
      extractDialogVisible.value = false
      ElNotification({
        title: '🚀 任务已提交',
        message: '后台正在为您努力深度阅读文档并提取 POM 骨架，您可以继续其他操作。',
        type: 'info',
        duration: 0,
        position: 'bottom-right'
      })
      pollTaskStatus(taskId)
    } else {
      ElMessage.error(res?.message || '提交失败，未获取到任务ID')
    }
  } catch (error) {
    console.error(error)
    ElMessage.error(error?.response?.data?.message || '网络请求失败')
  } finally {
    isExtracting.value = false
  }
}

// 后台静默轮询任务状态
async function pollTaskStatus(taskId) {
  let isFinished = false
  const maxRetries = 200
  let attempts = 0

  while (!isFinished && attempts < maxRetries && isComponentMounted.value) {
    await new Promise(resolve => setTimeout(resolve, 3000))
    attempts++

    try {
      const res = await getTaskStatus(projectId.value, taskId)
      const responseData = res?.data ?? res
      const data = responseData?.data ?? responseData
      const status = String(data?.status || data?.state || '').toUpperCase()

      if (status === 'SUCCESS' || status === 'COMPLETED') {
        isFinished = true
        ElNotification({
          title: '✅ 提取完成',
          message: '需求文档解析完成！页面及元素骨架已成功生成。',
          type: 'success',
          duration: 0,
          position: 'bottom-right'
        })
        if (isComponentMounted.value) {
          await loadPages()
        }
      } else if (status === 'FAILURE' || status === 'FAILED') {
        isFinished = true
        ElNotification({
          title: '❌ 提取失败',
          message: data?.error || data?.message || '大模型解析需求文档过程中发生异常，请检查系统日志。',
          type: 'error',
          duration: 0,
          position: 'bottom-right'
        })
      }
    } catch (error) {
      console.error('轮询任务状态失败:', error)
    }
  }

  if (attempts >= maxRetries && !isFinished && isComponentMounted.value) {
    ElNotification({
      title: '⚠️ 提取超时',
      message: '提取任务耗时超过10分钟，已停止后台自动查询。大模型可能仍在处理，稍后请手动刷新页面查看结果。',
      type: 'warning',
      duration: 0,
      position: 'bottom-right'
    })
  }
}

function openPageDialog(page = null) {
  editingPage.value = page
  pageForm.value = {
    name: page?.name ?? ''
  }
  pageDialogVisible.value = true
}

async function savePage() {
  await pageFormRef.value?.validate().catch(() => {})
  saving.value = true
  try {
    if (editingPage.value) {
      await updateWebPage(projectId.value, editingPage.value.id, pageForm.value)
      ElMessage.success('更新成功')
    } else {
      await createWebPage(projectId.value, pageForm.value)
      ElMessage.success('创建成功')
    }
    pageDialogVisible.value = false
    await loadPages()
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function deletePageConfirm(page) {
  deleteTarget.value = page
  deleteType.value = 'page'
  deleteDialogVisible.value = true
}

function openElementDialog(element = null) {
  editingElement.value = element
  elementForm.value = {
    name: element?.name ?? '',
    locator_type: element?.locator_type ?? 'css',
    locator_value: element?.locator_value ?? '',
    action_type: element?.action_type ?? ''
  }
  elementDialogVisible.value = true
}

async function saveElement() {
  await elementFormRef.value?.validate().catch(() => {})
  if (!selectedPage.value) return
  saving.value = true
  try {
    const payload = {
      page: selectedPage.value.id,
      name: elementForm.value.name,
      locator_type: elementForm.value.locator_type,
      locator_value: elementForm.value.locator_value,
      action_type: elementForm.value.action_type || null
    }
    if (editingElement.value) {
      await updateWebElement(projectId.value, editingElement.value.id, payload)
      ElMessage.success('更新成功')
    } else {
      await createWebElement(projectId.value, payload)
      ElMessage.success('创建成功')
    }
    elementDialogVisible.value = false
    await loadElements()
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function deleteElementConfirm(element) {
  deleteTarget.value = element
  deleteType.value = 'element'
  deleteDialogVisible.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value || !projectId.value) return
  deleting.value = true
  try {
    if (deleteType.value === 'page') {
      await deleteWebPage(projectId.value, deleteTarget.value.id)
      ElMessage.success('删除成功')
      if (selectedPage.value?.id === deleteTarget.value.id) {
        selectedPage.value = null
      }
      await loadPages()
    } else {
      await deleteWebElement(projectId.value, deleteTarget.value.id)
      ElMessage.success('删除成功')
      await loadElements()
    }
    deleteDialogVisible.value = false
  } catch (e) {
    ElMessage.error(e?.response?.data?.error?.message || '删除失败')
  } finally {
    deleting.value = false
  }
}

watch(selectedPage, (page) => {
  selectedElementIds.value = []
  if (page?.id) {
    pageTreeRef.value?.setCurrentKey(page.id)
  }
  loadElements()
}, { immediate: false })
watch(projectId, (id) => {
  if (id) {
    loadPages()
    loadWebUIActions()
    selectedPage.value = null
    selectedElementIds.value = []
  } else {
    pages.value = []
    elements.value = []
    selectedPage.value = null
    selectedElementIds.value = []
    ACTION_TYPES.value = []
  }
}, { immediate: true })
</script>

<style scoped>
.page-objects-container {
  padding: 20px;
  min-height: calc(100vh - 120px);
}

.page-header {
  margin-bottom: 20px;
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
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 24px;
}

.header-text h2 {
  margin: 0 0 4px 0;
  font-size: 20px;
  font-weight: 600;
}

.header-text p {
  margin: 0;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.content-row {
  margin-top: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pages-card,
.elements-card {
  min-height: 400px;
}

.page-tree-container {
  padding: 10px 0;
}

.tree-search {
  margin-bottom: 15px;
  padding: 0 10px;
}

.custom-tree {
  max-height: 450px;
  overflow-y: auto;
}

.custom-tree-node {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  padding-right: 8px;
}

.custom-tree-node .module-icon {
  color: #e6a23c;
  margin-right: 6px;
}

.custom-tree-node .page-icon {
  color: #409eff;
  margin-right: 6px;
}

.node-label {
  margin-left: 6px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-actions {
  display: none;
}

:deep(.el-tree-node__content:hover) .node-actions {
  display: inline-block;
}

.pages-list {
  max-height: 500px;
  overflow-y: auto;
}

.page-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
}

.page-item:hover {
  background: var(--el-fill-color-light);
}

.page-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.page-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-name {
  font-weight: 500;
}

.page-actions {
  opacity: 0.8;
}

.elements-table-wrap {
  min-height: 300px;
}

.no-project-tip {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

/* 元素编辑弹窗 - 定位与操作提示 */
.option-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.input-tip {
  margin-top: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 6px;
  border-left: 3px solid var(--el-color-primary);
}

.action-type-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.action-tip-icon {
  color: var(--el-color-info);
  cursor: help;
  font-size: 16px;
}
</style>
