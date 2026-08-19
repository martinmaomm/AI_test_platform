<template>
  <div class="endpoint-testcases-postman">
    <!-- 左侧树形结构 -->
    <div class="left-sidebar" :style="{ width: sidebarWidth + 'px' }">
      <RequestTree
        :test-cases="testCases"
        :selected-id="selectedTestCaseId"
        :loading="loading"
        @select="handleSelectTestCase"
        @create="handleCreateTestCase"
        @rename="handleRenameTestCase"
        @duplicate="handleDuplicateTestCase"
        @delete="handleDeleteTestCase"
        @add-to-suite="handleAddToSuite"
      />
    </div>

    <!-- 分隔条 -->
    <div class="resizer" @mousedown="startResize"></div>

    <!-- 右侧内容区 -->
    <div class="main-content">
      <div v-if="selectedTestCase" class="request-response-container">
        <!-- 顶部工具栏 -->
        <div class="toolbar">
          <div class="toolbar-left">
            <span class="test-case-title">{{ selectedTestCase.title }}</span>
            <el-tag v-if="hasUnsavedChanges" type="warning" size="small" effect="plain">
              未保存
            </el-tag>
          </div>
          <div class="toolbar-right">
            <el-button 
              v-if="hasUnsavedChanges" 
              size="default" 
              @click="discardChanges"
            >
              放弃更改
            </el-button>
            <el-button 
              type="primary" 
              size="default" 
              :disabled="!hasUnsavedChanges"
              @click="saveTestCase"
              :loading="saving"
            >
              <el-icon><Check /></el-icon>
              保存
            </el-button>
          </div>
        </div>

        <!-- 请求编辑器 -->
        <div class="request-section">
          <RequestEditor
            v-if="editingTestCase"
            :test-case="editingTestCase"
            :endpoint="selectedTestCase?.endpoint_info"
            @change="handleTestCaseChange"
            @send="handleSendRequest"
            @response="handleResponse"
          />
          <div v-else class="empty-state">
            <el-empty description="请选择一个测试用例" />
          </div>
        </div>

        <!-- 响应查看器（始终显示） -->
        <div class="response-section">
          <ResponseViewer
            :response="response"
            :loading="sendingRequest"
            :test-results="response?.testResults || []"
          />
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else class="empty-state">
        <el-empty description="请从左侧选择一个测试用例">
          <el-button type="primary" @click="handleCreateTestCase">
            <el-icon><Plus /></el-icon>
            创建测试用例
          </el-button>
        </el-empty>
      </div>
    </div>
  </div>

  <!-- 添加到测试套件 -->
  <el-dialog
    v-model="addToSuiteDialogVisible"
    title="添加到测试套件"
    width="560px"
    :close-on-click-modal="false"
  >
    <div class="suite-dialog-body">
      <div class="suite-target">
        <span class="suite-target-label">当前用例：</span>
        <span class="suite-target-name">{{ addToSuiteCase?.title || '-' }}</span>
      </div>

      <el-radio-group v-model="suiteMode" class="suite-mode">
        <el-radio-button label="select" :disabled="suiteOptions.length === 0">选择已有套件</el-radio-button>
        <el-radio-button label="create">创建新套件</el-radio-button>
      </el-radio-group>

      <div v-if="suiteMode === 'select'">
        <el-form label-width="90px">
          <el-form-item label="测试套件">
            <el-select
              v-model="selectedSuiteId"
              placeholder="请选择测试套件"
              style="width: 100%"
              :loading="loadingSuites"
              filterable
            >
              <el-option
                v-for="suite in suiteOptions"
                :key="suite.id"
                :label="suite.name"
                :value="suite.id"
              >
                <span>{{ suite.name }}</span>
                <span v-if="suite.description" class="suite-option-desc"> - {{ suite.description }}</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-alert
            v-if="!loadingSuites && suiteOptions.length === 0"
            title="当前项目暂无 API 测试套件，请先创建"
            type="info"
            show-icon
            :closable="false"
          />
        </el-form>
      </div>

      <div v-else>
        <el-form
          ref="suiteFormRef"
          :model="suiteForm"
          :rules="suiteRules"
          label-width="90px"
        >
          <el-form-item label="套件名称" prop="name">
            <el-input v-model="suiteForm.name" maxlength="200" show-word-limit placeholder="请输入套件名称" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input
              v-model="suiteForm.description"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="可选"
            />
          </el-form-item>
        </el-form>
      </div>
    </div>

    <template #footer>
      <el-button @click="addToSuiteDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="addingToSuite" @click="confirmAddToSuite">确定添加</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Plus } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'
import {
  getAPITestCases,
  getAPITestCase,
  updateAPITestCase,
  deleteAPITestCase,
  getAPITestSuites,
  createAPITestSuite,
  addTestCasesToSuite
} from '@/api/apiTesting'
import RequestTree from '@/components/api-testing/RequestTree.vue'
import RequestEditor from '@/components/api-testing/RequestEditor.vue'
import ResponseViewer from '@/components/api-testing/ResponseViewer.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

// 数据状态
const testCases = ref([])
const selectedTestCaseId = ref(null)
const selectedTestCase = ref(null)
const editingTestCase = ref(null)
const originalTestCase = ref(null)
const response = ref(null)

// UI 状态
const loading = ref(false)
const saving = ref(false)
const sendingRequest = ref(false)
const sidebarWidth = ref(350)

// 添加到测试套件
const addToSuiteDialogVisible = ref(false)
const addToSuiteCase = ref(null)
const suiteOptions = ref([])
const loadingSuites = ref(false)
const addingToSuite = ref(false)
const suiteMode = ref('select')
const selectedSuiteId = ref(null)
const suiteFormRef = ref(null)
const suiteForm = ref({
  name: '',
  description: ''
})
const suiteRules = {
  name: [
    { required: true, message: '请输入测试套件名称', trigger: 'blur' },
    { min: 2, max: 200, message: '长度在 2 到 200 个字符', trigger: 'blur' }
  ]
}

// 计算属性
const hasUnsavedChanges = computed(() => {
  if (!editingTestCase.value || !originalTestCase.value) return false
  return JSON.stringify(editingTestCase.value) !== JSON.stringify(originalTestCase.value)
})

// 加载测试用例列表
const loadTestCases = async () => {
  loading.value = true
  try {
    const res = await getAPITestCases(projectStore.currentProjectId, {
      test_case_type: 'endpoint',
      page: 1,
      page_size: 1000  // 加载大量数据，足够显示所有端点测试用例
    })
    if (res.success) {
      // 后端返回的是分页数据：{ items: [...], pagination: {...} }
      testCases.value = res.data?.items || res.data || []
    }
  } catch (error) {
    console.error('加载测试用例失败:', error)
    ElMessage.error('加载测试用例失败')
  } finally {
    loading.value = false
  }
}

// 选择测试用例
const handleSelectTestCase = async (testCaseId) => {
  // 防止重复请求：如果正在加载，直接返回
  if (loading.value) {
    return
  }
  
  // 如果选择的是当前测试用例，不需要重新加载
  if (selectedTestCaseId.value === testCaseId) {
    return
  }
  
  // 如果有未保存的更改，提示用户
  if (hasUnsavedChanges.value) {
    try {
      await ElMessageBox.confirm(
        '您有未保存的更改，是否保存后再切换？',
        '提示',
        {
          confirmButtonText: '保存并切换',
          cancelButtonText: '不保存',
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
      // 用户选择保存
      await saveTestCase()
    } catch (action) {
      if (action === 'close') {
        // 用户点击了关闭按钮，取消切换
        return
      }
      // 用户选择不保存，继续切换
    }
  }

  selectedTestCaseId.value = testCaseId
  
  // 调用详情接口获取完整数据（包括 script_content）
  try {
    loading.value = true
    const res = await getAPITestCase(projectStore.currentProjectId, testCaseId)
    
    // 再次检查是否仍然是当前选中的测试用例（用户可能在等待期间又切换了）
    if (selectedTestCaseId.value !== testCaseId) {
      return
    }
    
    if (res.success) {
      const testCaseDetail = res.data
      selectedTestCase.value = testCaseDetail
      editingTestCase.value = JSON.parse(JSON.stringify(testCaseDetail))
      originalTestCase.value = JSON.parse(JSON.stringify(testCaseDetail))
      response.value = null
    } else {
      ElMessage.error('加载测试用例详情失败')
    }
  } catch (error) {
    console.error('加载测试用例详情失败:', error)
    ElMessage.error('加载测试用例详情失败')
  } finally {
    loading.value = false
  }
}

const loadTestSuites = async () => {
  if (!projectStore.currentProjectId) return
  loadingSuites.value = true
  try {
    const res = await getAPITestSuites(projectStore.currentProjectId, {
      page: 1,
      page_size: 200,
      project_id: projectStore.currentProjectId
    })
    if (res.success) {
      suiteOptions.value = res.data?.items || res.data || []
    }
  } catch (error) {
    console.error('加载测试套件失败:', error)
  } finally {
    loadingSuites.value = false
  }
}

const openAddToSuiteDialog = async (testCase) => {
  addToSuiteCase.value = testCase
  selectedSuiteId.value = null
  suiteForm.value = { name: '', description: '' }
  await loadTestSuites()
  suiteMode.value = suiteOptions.value.length > 0 ? 'select' : 'create'
  addToSuiteDialogVisible.value = true
}

const confirmAddToSuite = async () => {
  if (!addToSuiteCase.value?.id) return
  if (!projectStore.currentProjectId) {
    ElMessage.warning('请先选择一个项目')
    return
  }

  addingToSuite.value = true
  try {
    let suiteId = selectedSuiteId.value

    if (suiteMode.value === 'select') {
      if (!suiteId) {
        ElMessage.warning('请选择测试套件')
        return
      }
    } else {
      await suiteFormRef.value?.validate()
      const createRes = await createAPITestSuite(projectStore.currentProjectId, {
        name: suiteForm.value.name,
        description: suiteForm.value.description,
        status: 'active',
        tags: []
      })
      if (!createRes?.success) {
        ElMessage.error(createRes?.message || '创建测试套件失败')
        return
      }
      suiteId = createRes?.data?.id || createRes?.data?.data?.id || createRes?.id
    }

    if (!suiteId) {
      ElMessage.error('创建测试套件失败：未获取到套件ID')
      return
    }

    const addRes = await addTestCasesToSuite(projectStore.currentProjectId, suiteId, {
      test_case_ids: [addToSuiteCase.value.id]
    })
    if (addRes?.success) {
      ElMessage.success('已添加到测试套件')
      addToSuiteDialogVisible.value = false
    } else {
      ElMessage.error(addRes?.message || '添加到测试套件失败')
    }
  } catch (error) {
    console.error('添加到测试套件失败:', error)
    ElMessage.error(error.message || '添加到测试套件失败')
  } finally {
    addingToSuite.value = false
  }
}

// 测试用例数据变更
const handleTestCaseChange = (updatedData) => {
  // RequestEditor发送的updatedData包含test_data字段，需要转换为script_content
  if (updatedData.test_data) {
    const testData = updatedData.test_data
    const scriptContent = JSON.stringify(testData, null, 2)
    
    // 从testData中提取variables和request_data（保持数据同步）
    const firstStep = testData.teststeps?.[0]
    const request = firstStep?.request || {}
    
    // 构建variables格式的数据
    const variables = {
      path_params: {},
      query_params: request.params || {},
      body: request.json || {}
    }
    
    editingTestCase.value = { 
      ...editingTestCase.value, 
      script_content: scriptContent,
      variables: variables,
      request_data: variables,  // 保持request_data和variables同步
      pre_script: updatedData.pre_script ?? editingTestCase.value?.pre_script ?? '',
      post_script: updatedData.post_script ?? editingTestCase.value?.post_script ?? ''
    }
  } else {
    editingTestCase.value = { ...editingTestCase.value, ...updatedData }
  }
}

// 保存测试用例
const saveTestCase = async () => {
  if (!editingTestCase.value) return

  saving.value = true
  try {
    const res = await updateAPITestCase(
      projectStore.currentProjectId,
      editingTestCase.value.id,
      editingTestCase.value
    )
    
    if (res.success) {
      ElMessage.success('保存成功')
      
      // 更新原始数据
      originalTestCase.value = JSON.parse(JSON.stringify(editingTestCase.value))
      selectedTestCase.value = { ...editingTestCase.value }
      
      // 更新列表中的数据
      const index = testCases.value.findIndex(tc => tc.id === editingTestCase.value.id)
      if (index !== -1) {
        testCases.value[index] = { ...editingTestCase.value }
      }
    } else {
      ElMessage.error(res.message || '保存失败')
    }
  } catch (error) {
    console.error('保存失败:', error)
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

// 放弃更改
const discardChanges = () => {
  ElMessageBox.confirm(
    '确定要放弃所有未保存的更改吗？',
    '提示',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    }
  ).then(() => {
    editingTestCase.value = JSON.parse(JSON.stringify(originalTestCase.value))
    ElMessage.info('已恢复到保存前的状态')
  }).catch(() => {
    // 用户取消
  })
}

// 发送请求（已弃用，现在由RequestEditor直接处理）
const handleSendRequest = async (requestData) => {
  // 这个函数现在由RequestEditor组件内部直接调用executeAPITestCase
  // 保留这个函数是为了兼容性
}

// 处理响应（从RequestEditor接收）
const handleResponse = (responseData) => {
  response.value = responseData
  sendingRequest.value = false
}

// 创建测试用例
const handleCreateTestCase = () => {
  // TODO: 打开创建对话框或跳转到创建页面
  ElMessage.info('创建测试用例功能待实现')
}

// 重命名测试用例
const handleRenameTestCase = async (testCaseId, newName) => {
  const testCase = testCases.value.find(tc => tc.id === testCaseId)
  if (!testCase) return

  try {
    const res = await updateAPITestCase(
      projectStore.currentProjectId,
      testCaseId,
      { title: newName }
    )
    
    if (res.success) {
      testCase.title = newName
      if (selectedTestCase.value?.id === testCaseId) {
        selectedTestCase.value.title = newName
        editingTestCase.value.title = newName
        originalTestCase.value.title = newName
      }
      ElMessage.success('重命名成功')
    }
  } catch (error) {
    console.error('重命名失败:', error)
    ElMessage.error('重命名失败')
  }
}

// 复制测试用例
const handleDuplicateTestCase = async (testCaseId) => {
  // TODO: 实现复制逻辑
  ElMessage.info('复制测试用例功能待实现')
}

// 删除测试用例
const handleDeleteTestCase = async (testCaseId) => {
  try {
    await ElMessageBox.confirm(
      '确定要删除这个测试用例吗？',
      '提示',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    const res = await deleteAPITestCase(projectStore.currentProjectId, testCaseId)
    
    if (res.success) {
      // 从列表中移除
      const index = testCases.value.findIndex(tc => tc.id === testCaseId)
      if (index !== -1) {
        testCases.value.splice(index, 1)
      }
      
      // 如果删除的是当前选中的，清空选择
      if (selectedTestCaseId.value === testCaseId) {
        selectedTestCaseId.value = null
        selectedTestCase.value = null
        editingTestCase.value = null
        originalTestCase.value = null
      }
      
      ElMessage.success('删除成功')
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除失败:', error)
      ElMessage.error('删除失败')
    }
  }
}

const handleAddToSuite = (testCase) => {
  if (!testCase) return
  openAddToSuiteDialog(testCase)
}

// 分隔条拖动
const startResize = (e) => {
  const startX = e.clientX
  const startWidth = sidebarWidth.value

  const onMouseMove = (e) => {
    const delta = e.clientX - startX
    const newWidth = startWidth + delta
    
    // 限制最小和最大宽度
    if (newWidth >= 200 && newWidth <= 600) {
      sidebarWidth.value = newWidth
    }
  }

  const onMouseUp = () => {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// 键盘快捷键
const handleKeyDown = (e) => {
  // Ctrl+S 保存
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    if (hasUnsavedChanges.value) {
      saveTestCase()
    }
  }
  
  // Ctrl+Enter 发送请求
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    if (editingTestCase.value) {
      handleSendRequest(editingTestCase.value)
    }
  }
}

// 生命周期
onMounted(() => {
  loadTestCases()
  document.addEventListener('keydown', handleKeyDown)
})

// 组件卸载时移除事件监听
onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeyDown)
})
</script>

<script>
import { onBeforeUnmount } from 'vue'
</script>

<style scoped lang="scss">
.endpoint-testcases-postman {
  display: flex;
  height: calc(100vh - 60px);
  background: #f5f5f5;
  overflow: hidden;
}

.left-sidebar {
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #e5e5e5;
  overflow: hidden;
}

.resizer {
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.2s;
  
  &:hover {
    background: #409eff;
  }
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #fff;
}

.request-response-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.request-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.request-section .empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 400px;
}

.response-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  border-top: 2px solid #e5e5e5;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e5e5e5;
  background: #fff;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.test-case-title {
  font-size: 16px;
  font-weight: 500;
  color: #303133;
}

.toolbar-right {
  display: flex;
  gap: 12px;
}

.suite-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.suite-target {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 6px;
}

.suite-target-label {
  color: #909399;
}

.suite-target-name {
  font-weight: 500;
  color: #303133;
}

.suite-mode {
  width: fit-content;
}

.suite-option-desc {
  color: #909399;
  margin-left: 4px;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
