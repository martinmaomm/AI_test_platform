<template>
  <el-drawer
    v-model="visible"
    :title="drawerTitle"
    size="70%"
    direction="rtl"
    :with-header="true"
    :before-close="handleClose"
    :show-close="false"
  >
    <template #header>
      <div class="drawer-header">
        <div class="header-content">
          <div class="header-left">
            <div class="header-title">
              <h3>{{ isCreateMode ? '新建 Web UI 测试用例' : '编辑 Web UI 测试用例' }}</h3>
              <div class="header-subtitle">
                <span class="subtitle-text">{{ isCreateMode ? '填写测试用例的详细信息' : '编辑测试用例的详细信息' }}</span>
              </div>
            </div>
          </div>
          <div class="header-right">
            <el-button 
              v-if="hasChanges" 
              type="primary" 
              size="default" 
              @click="saveTestCase" 
              class="save-btn"
            >
              <el-icon><Check /></el-icon>
              保存更改
            </el-button>
            <el-button size="default" @click="handleClose" class="close-btn">
              <el-icon><Close /></el-icon>
              关闭
            </el-button>
          </div>
        </div>
      </div>
    </template>
    
    <div v-if="testCase" class="test-case-detail-drawer">
      <!-- 基本信息 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>基本信息</h4>
        </div>
        <div class="section-content-edit">
          <div class="basic-info-container">
            <!-- 标题区域 -->
            <div class="info-section">
              <div class="info-label">
                <el-icon><Document /></el-icon>
                <span>用例标题</span>
              </div>
              <div class="info-content">
                <el-input 
                  v-model="editForm.title" 
                  placeholder="请输入测试用例标题"
                  size="large"
                />
              </div>
            </div>

            <!-- 属性标签区域 -->
            <div class="info-section">
              <div class="info-label">
                <el-icon><Star /></el-icon>
                <span>属性信息</span>
              </div>
              <div class="info-content">
                <div class="tags-container">
                  <div class="tag-group">
                    <span class="tag-label">优先级:</span>
                    <el-select 
                      v-model="editForm.priority" 
                      placeholder="选择优先级"
                      size="small"
                      style="width: 120px"
                    >
                      <el-option label="高" value="high" />
                      <el-option label="中" value="medium" />
                      <el-option label="低" value="low" />
                    </el-select>
                  </div>
                  
                  <div class="tag-group">
                    <span class="tag-label">类别:</span>
                    <el-select 
                      v-model="editForm.category" 
                      placeholder="选择类别"
                      size="small"
                      style="width: 140px"
                    >
                      <el-option label="功能测试" value="functional" />
                      <el-option label="异常测试" value="negative" />
                      <el-option label="边界测试" value="boundary" />
                      <el-option label="安全测试" value="security" />
                      <el-option label="性能测试" value="performance" />
                      <el-option label="界面测试" value="ui" />
                      <el-option label="集成测试" value="integration" />
                    </el-select>
                  </div>
                </div>
              </div>
            </div>

            <!-- 描述区域 -->
            <div class="info-section">
              <div class="info-label">
                <el-icon><EditPen /></el-icon>
                <span>测试描述</span>
              </div>
              <div class="info-content">
                <el-input 
                  v-model="editForm.description" 
                  type="textarea" 
                  :rows="3"
                  placeholder="请输入测试用例描述"
                  resize="none"
                />
              </div>
            </div>

          </div>
        </div>
      </div>

      <!-- 前置条件 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>前置条件</h4>
          <el-button size="small" type="primary" @click="addPrecondition">
            <el-icon><Plus /></el-icon>添加条件
          </el-button>
        </div>
        <div class="section-content-edit">
          <div class="preconditions-edit">
            <div 
              v-for="(condition, index) in editForm.preconditions" 
              :key="index" 
              class="precondition-item-edit"
            >
              <el-input 
                v-model="editForm.preconditions[index]" 
                placeholder="请输入前置条件"
                style="flex: 1"
              />
              <el-button 
                size="small" 
                type="danger" 
                @click="removePrecondition(index)"
                style="margin-left: 8px"
              >
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div v-if="editForm.preconditions.length === 0" class="empty-state">
              <el-empty description="暂无前置条件" size="small" />
            </div>
          </div>
        </div>
      </div>

      <!-- 测试步骤 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>测试步骤 ({{ editForm.steps.length }})</h4>
          <el-button size="small" type="primary" @click="addStep">
            <el-icon><Plus /></el-icon>添加步骤
          </el-button>
        </div>
        <div class="section-content-edit">
          <div class="steps-edit">
            <draggable
              v-model="editForm.steps"
              item-key="step_id"
              handle=".drag-handle"
              animation="200"
              @end="handleDragEnd"
            >
              <template #item="{ element: step, index }">
                <div class="step-item-edit">
                  <div class="step-header-edit">
                    <div class="drag-handle" title="按住拖拽排序">
                      <el-icon><Rank /></el-icon>
                    </div>
                    <div class="step-number">
                      <span class="step-number-text">{{ index + 1 }}</span>
                    </div>
                    <div class="step-fields">
                      <div class="step-description-row">
                        <label class="step-label step-description-label">步骤描述</label>
                        <el-input 
                          :model-value="step.description"
                          @update:model-value="updateStepField(index, 'description', $event)"
                          placeholder="详细描述这个步骤要做什么"
                          size="small"
                          class="step-description-input"
                        />
                      </div>
                      <div class="step-main-row">
                        <div class="step-input-group">
                          <label class="step-label">操作类型</label>
                          <el-select
                            :model-value="step.action"
                            @update:model-value="(v) => { updateStepField(index, 'action', v); handleActionChange(editForm.steps[index]) }"
                            placeholder="选择操作类型"
                            size="small"
                            class="step-input"
                            clearable
                            style="width: 100%"
                          >
                            <el-option
                              v-for="action in webUIActions"
                              :key="action.value"
                              :label="action.label"
                              :value="action.value"
                            />
                          </el-select>
                        </div>
                        <div v-if="getActionConfig(step.action).needTarget !== false" class="step-input-group">
                          <label class="step-label">目标元素</label>
                          <el-input
                            :model-value="getStepElementDisplay(step)"
                            placeholder="请选择: 模块 -> 页面 -> 元素"
                            readonly
                            style="width: 100%"
                            size="small"
                            @click="openSelector(index)"
                          >
                            <template #append>
                              <el-button :icon="Search" @click.stop="openSelector(index)" title="选择元素" />
                              <el-button
                                v-if="step.element_id"
                                :icon="Close"
                                @click.stop="updateStepField(index, 'element_id', null); updateStepField(index, '_display_name', '')"
                                title="清除"
                              />
                            </template>
                          </el-input>
                        </div>
                        <div v-if="getActionConfig(step.action).needValue !== false" class="step-input-group">
                          <label class="step-label">输入值</label>
                          <el-input 
                            :model-value="step.value"
                            @update:model-value="updateStepField(index, 'value', $event)"
                            :placeholder="getActionConfig(step.action).valuePlaceholder || '如：用户名、密码等'"
                            size="small"
                            class="step-input"
                          />
                        </div>
                        <div class="step-actions">
                          <el-button 
                            size="small" 
                            type="danger" 
                            @click="removeStep(index)"
                            class="step-delete-btn"
                            circle
                          >
                            <el-icon><Delete /></el-icon>
                          </el-button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </draggable>
            <div v-if="editForm.steps.length === 0" class="empty-state">
              <el-empty description="暂无测试步骤" size="small" />
            </div>
          </div>
        </div>
      </div>

      <!-- 预期结果 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>预期结果</h4>
        </div>
        <div class="section-content-edit">
          <el-form-item >
            <el-input 
              v-model="editForm.expected_result" 
              type="textarea" 
              :rows="3"
              placeholder="请输入预期结果"
            />
          </el-form-item>
        </div>
      </div>


      <!-- 测试脚本 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>测试脚本</h4>
        </div>
        <div class="section-content-edit">
          <div class="script-edit">
            <MonacoEditor
              v-model:value="editForm.test_script_content"
              language="python"
              theme="vs-dark"
              :read-only="false"
              height="400px"
              @change="handleScriptChange"
            />
          </div>
        </div>
      </div>
    </div>

    <PomElementSelector
      v-model="selectorVisible"
      :tree-data="pomTreeData"
      @select="handleElementSelect"
    />
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import {
  Document,
  List,
  Check,
  DataAnalysis,
  Calendar,
  User,
  Folder,
  Edit,
  Close,
  Plus,
  Delete,
  Star,
  Link,
  EditPen,
  Rank,
  Search
} from '@element-plus/icons-vue'
import { updateWebUITestCase, patchWebUITestCase, createWebUITestCase, getWebPages, getWebElements, getWebUIActionsDict } from '@/api/webTesting'
import MonacoEditor from './MonacoEditor.vue'
import PomElementSelector from './PomElementSelector.vue'
import { useProjectStore } from '@/stores/project'

// Props
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  testCase: {
    type: Object,
    default: null
  },
  existingTestCases: {
    type: Array,
    default: () => []
  }
})

// Emits
const emit = defineEmits(['update:modelValue', 'run', 'update'])

// 项目store
const projectStore = useProjectStore()

// 响应式数据
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const activeScriptTab = ref('yaml')

// 计算属性
const isCreateMode = computed(() => !props.testCase?.id)
const drawerTitle = computed(() => {
  if (!props.testCase) return '测试用例编辑'
  return props.testCase.id ? `测试用例编辑 - ${props.testCase.title}` : (props.testCase.title || '新建测试用例')
})

// 编辑表单数据
const editForm = ref({
  title: '',
  description: '',
  url: '',
  priority: 'medium',
  category: 'functional',
  preconditions: [],
  steps: [],
  expected_result: '',
  test_script_content: ''
})

// 原始数据存储
const originalData = ref({})

// POM 树数据（模块 -> 页面 -> 元素），用于三列选择器
const pomTreeData = ref([])

// 三列选择器弹窗状态
const selectorVisible = ref(false)
const currentStepIndex = ref(-1)

// WebUI 动作配置（从后端动态拉取）
const webUIActions = ref([])

// 获取动作的配置项（替代之前从本地常量读取）
const getActionConfig = (actionValue) => {
  return webUIActions.value.find(a => a.value === actionValue) || {}
}

// 处理动作变更时的联动清理
const handleActionChange = (step) => {
  const config = getActionConfig(step.action)
  if (config.needTarget === false) {
    step.element_id = null
    step._display_name = ''
  }
  if (config.needValue === false) {
    step.value = ''
  }
}

// 页面加载时拉取后端配置
onMounted(async () => {
  const projectId = projectStore.currentProjectId
  if (!projectId) return
  try {
    const res = await getWebUIActionsDict(projectId)
    if (res.success || res.code === 200) {
      webUIActions.value = res.data || []
    }
  } catch (error) {
    console.error('获取动作字典失败:', error)
  }
})

// 检查是否有数据变化（新建模式下始终显示保存按钮）
const hasChanges = computed(() => {
  if (isCreateMode.value) return true
  if (!props.testCase || Object.keys(originalData.value).length === 0) {
    return false
  }
  
  // 比较基本字段
  const basicFields = ['title', 'description', 'url', 'priority', 'category', 'expected_result']
  for (const field of basicFields) {
    if (editForm.value[field] !== originalData.value[field]) {
      return true
    }
  }
  
  // 比较数组字段
  const arrayFields = ['preconditions', 'steps']
  for (const field of arrayFields) {
    if (!isArrayEqual(editForm.value[field], originalData.value[field])) {
      return true
    }
  }
  
  // 比较脚本内容
  if (editForm.value.test_script_content !== originalData.value.test_script_content) {
    return true
  }
  
  return false
})

// 工具函数
const isArrayEqual = (arr1, arr2) => {
  if (!Array.isArray(arr1) || !Array.isArray(arr2)) {
    return arr1 === arr2
  }
  if (arr1.length !== arr2.length) {
    return false
  }
  
  // 对于对象数组，进行深度比较
  for (let i = 0; i < arr1.length; i++) {
    if (typeof arr1[i] === 'object' && typeof arr2[i] === 'object') {
      if (!isObjectEqual(arr1[i], arr2[i])) {
        return false
      }
    } else if (arr1[i] !== arr2[i]) {
      return false
    }
  }
  return true
}

const isObjectEqual = (obj1, obj2) => {
  const keys1 = Object.keys(obj1)
  const keys2 = Object.keys(obj2)
  
  if (keys1.length !== keys2.length) {
    return false
  }
  
  for (const key of keys1) {
    if (obj1[key] !== obj2[key]) {
      return false
    }
  }
  return true
}

// 方法
const handleClose = () => {
  visible.value = false
}



// 初始化编辑表单
const initEditForm = () => {
  if (props.testCase) {
    const formData = {
      title: props.testCase.title || '',
      description: props.testCase.description || '',
      url: props.testCase.url || '',
      priority: props.testCase.priority || 'medium',
      category: props.testCase.category || 'functional',
      preconditions: [...(props.testCase.preconditions || [])],
      steps: (props.testCase.steps || []).map((step, idx) => ({
        ...step,
        step_id: step.step_id ?? idx + 1,
        target: step.target ?? step.target_element ?? '',
        element_id: step.element_id ?? null,
        _display_name: step._display_name ?? ''
      })),
      expected_result: props.testCase.expected_result || '',
      test_script_content: props.testCase.test_script_content || ''
    }
    // 新建模式：若无步骤则自动添加一个空步骤，便于用户直接输入
    if (!props.testCase.id && (!formData.steps || formData.steps.length === 0)) {
      formData.steps = [{ step_id: 1, action: '', target: '', value: '', description: '', expected: '', element_id: null, _display_name: '' }]
    }
    editForm.value = formData
    
    // 保存原始数据用于比较
    originalData.value = {
      title: formData.title,
      description: formData.description,
      url: formData.url,
      priority: formData.priority,
      category: formData.category,
      preconditions: [...formData.preconditions],
      steps: formData.steps.map(step => ({ ...step })),
      expected_result: formData.expected_result,
      test_script_content: formData.test_script_content
    }
  }
}

// 保存测试用例
const saveTestCase = async () => {
  const isCreate = !props.testCase?.id
  if (!isCreate && !props.testCase?.id) {
    console.error('测试用例ID不存在')
    return
  }

  try {
    // 深拷贝原始数据，防止修改影响页面响应式
    const payload = JSON.parse(JSON.stringify({
      title: editForm.value.title,
      description: editForm.value.description,
      url: editForm.value.url,
      priority: editForm.value.priority,
      category: editForm.value.category,
      preconditions: editForm.value.preconditions,
      steps: editForm.value.steps,
      expected_result: editForm.value.expected_result,
      test_script_content: editForm.value.test_script_content
    }))

    // 【核心组装逻辑】：清洗测试步骤数据，适配三维 POM 结构
    // 保持数据库轻量：仅提交业务字段，不提交 code_template（后端生成脚本时从 constants 实时获取）
    payload.steps = payload.steps.map((step, index) => {
      const { code_template, ...rest } = step
      return {
        step_id: index + 1,
        step_number: index + 1,
        description: rest.description || '',
        element_id: (rest.element_id !== null && rest.element_id !== undefined && rest.element_id !== '') ? Number(rest.element_id) : null,
        action: rest.action || 'click',
        value: getActionConfig(rest.action).needValue ? (rest.value || '') : '',
        target: rest.target || '',
        expected: rest.expected || ''
      }
    })

    const saveData = payload

    // 新建模式：必填项校验
    if (isCreate) {
      if (!(saveData.title || '').trim()) {
        ElMessage.warning('请输入用例标题')
        return
      }
      if (!(saveData.description || '').trim()) {
        ElMessage.warning('请输入用例描述')
        return
      }
      if (!(saveData.expected_result || '').trim()) {
        ElMessage.warning('请输入预期结果')
        return
      }
    }

    // 【新增】重名前端拦截校验
    const existingList = props.existingTestCases || []
    if (existingList.length > 0) {
      const isDuplicate = existingList.some(
        item => item.title === saveData.title && item.id !== (props.testCase?.id)
      )
      if (isDuplicate) {
        ElMessage.warning('当前列表中已存在相同名称的测试用例，请修改用例标题！')
        return
      }
    }

    let response
    if (isCreate) {
      // 新增模式（克隆场景）：使用 createWebUITestCase，需传入 project 和可选的 module_id
      const createData = {
        ...saveData,
        project: projectStore.currentProjectId
      }
      if (props.testCase?.module_id != null) {
        createData.module = props.testCase.module_id
      }
      response = await createWebUITestCase(projectStore.currentProjectId, createData)
    } else {
      // 编辑模式：使用 PATCH 部分更新
      response = await patchWebUITestCase(projectStore.currentProjectId, props.testCase.id, saveData)
    }
    
    if (response.success) {
      // 保存成功后，更新原始数据
      originalData.value = {
        title: editForm.value.title,
        description: editForm.value.description,
        url: editForm.value.url,
        priority: editForm.value.priority,
        category: editForm.value.category,
        preconditions: [...editForm.value.preconditions],
        steps: [...editForm.value.steps],
        expected_result: editForm.value.expected_result,
        test_script_content: editForm.value.test_script_content
      }
      
      // 显示成功消息（根据新增/编辑区分）
      ElMessage.success(isCreate ? '测试用例创建成功' : '测试用例更新成功')
      
      // 触发父组件更新
      emit('update', response.data)
      
      // 保存成功后关闭窗口
      visible.value = false
    } else {
      ElMessage.error(response.message || '保存失败')
    }
  } catch (error) {
    console.error('保存测试用例失败:', error)
    ElMessage.error('保存失败，请重试')
  }
}

// 前置条件管理
const addPrecondition = () => {
  editForm.value.preconditions.push('')
}

const removePrecondition = (index) => {
  editForm.value.preconditions.splice(index, 1)
}

// 测试步骤管理
const addStep = () => {
  editForm.value.steps.push({
    step_id: editForm.value.steps.length + 1,
    action: '',
    target: '',
    value: '',
    description: '',
    expected: '',
    element_id: null,
    _display_name: ''
  })
}

const removeStep = (index) => {
  editForm.value.steps.splice(index, 1)
  // 重新分配步骤ID
  editForm.value.steps.forEach((step, idx) => {
    step.step_id = idx + 1
  })
}

// 处理拖拽排序结束
const handleDragEnd = () => {
  // 拖拽完成后，根据最新的数组顺序重新分配 step_id
  editForm.value.steps.forEach((step, idx) => {
    step.step_id = idx + 1
  })
}

// 处理脚本内容变化
const handleScriptChange = (value) => {
  editForm.value.test_script_content = value
}

// 更新测试步骤字段
const updateStepField = (index, field, value) => {
  if (editForm.value.steps[index]) {
    editForm.value.steps[index][field] = value
  }
}


// 工具方法
const getPriorityType = (priority) => {
  const types = { high: 'danger', medium: 'warning', low: 'info' }
  return types[priority] || 'info'
}

const getPriorityText = (priority) => {
  const texts = { high: '高', medium: '中', low: '低' }
  return texts[priority] || '未知'
}

const getCategoryType = (category) => {
  const types = {
    functional: 'success',
    negative: 'warning',
    boundary: 'info',
    security: 'danger',
    performance: 'primary',
    ui: 'success',
    integration: 'info'
  }
  return types[category] || 'info'
}

const getCategoryText = (category) => {
  const texts = {
    functional: '功能测试',
    negative: '异常测试',
    boundary: '边界测试',
    security: '安全测试',
    performance: '性能测试',
    ui: '界面测试',
    integration: '集成测试'
  }
  return texts[category] || '未知'
}

// 获取操作类型标签颜色
const getActionTagType = (action) => {
  const actionTypes = {
    '点击': 'primary',
    '输入': 'success',
    '选择': 'warning',
    '等待': 'info',
    '验证': 'danger',
    '拖拽': 'primary',
    '悬停': 'info',
    '滚动': 'warning',
    '刷新': 'info',
    '关闭': 'danger'
  }
  return actionTypes[action] || 'primary'
}

// 格式化日期时间
const formatDateTime = (dateTime) => {
  if (!dateTime) return '未知'
  return new Date(dateTime).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

// 监听测试用例变化，自动初始化编辑表单
watch(() => props.testCase, (newTestCase) => {
  if (newTestCase && visible.value) {
    initEditForm()
  }
}, { immediate: true })

// 监听抽屉显示状态，自动初始化编辑表单
watch(visible, (newVisible) => {
  if (newVisible && props.testCase) {
    initEditForm()
  }
  if (newVisible && projectStore.currentProjectId) {
    loadPomTreeData()
  }
})

// 加载 POM 树数据（模块 -> 页面 -> 元素），供三列选择器使用
const loadPomTreeData = async () => {
  const projectId = projectStore.currentProjectId
  if (!projectId) return
  try {
    const [pagesRes, elementsRes] = await Promise.all([
      getWebPages(projectId),
      getWebElements(projectId)
    ])
    const pages = Array.isArray(pagesRes) ? pagesRes : (pagesRes?.results ?? pagesRes?.data ?? [])
    const elements = Array.isArray(elementsRes) ? elementsRes : (elementsRes?.results ?? elementsRes?.data ?? [])

    // 按模块分组构建三级树
    const moduleMap = new Map()
    const unassignedKey = 'module_unassigned'
    if (!moduleMap.has(unassignedKey)) {
      moduleMap.set(unassignedKey, { id: unassignedKey, name: '未归属模块', children: [] })
    }

    pages.forEach(page => {
      const modId = page.module_id ? `module_${page.module_id}` : unassignedKey
      const modName = page.module_name || '未命名模块'
      if (!moduleMap.has(modId)) {
        moduleMap.set(modId, { id: modId, name: modName, children: [] })
      }
      const pageNode = {
        id: page.id,
        name: page.name,
        children: (elements || []).filter(e => e.page === page.id).map(e => ({
          id: e.id,
          name: e.name,
          action_type: e.action_type || ''
        }))
      }
      moduleMap.get(modId).children.push(pageNode)
    })

    pomTreeData.value = Array.from(moduleMap.values())
  } catch (e) {
    console.error('加载 POM 树失败:', e)
    pomTreeData.value = []
  }
}

// 打开三列选择器
const openSelector = (index) => {
  currentStepIndex.value = index
  selectorVisible.value = true
}

// 处理元素选择
const handleElementSelect = (data) => {
  if (currentStepIndex.value !== -1 && editForm.value.steps[currentStepIndex.value]) {
    const step = editForm.value.steps[currentStepIndex.value]
    step.element_id = data.elementId
    step._display_name = data.fullPath
  }
  currentStepIndex.value = -1
}

// 根据 element_id 从 pomTreeData 反查全路径用于展示
const getStepElementDisplay = (step) => {
  if (step._display_name) return step._display_name
  const eid = step.element_id
  if (eid == null || eid === '') return ''
  const tree = pomTreeData.value
  for (const mod of tree) {
    for (const page of mod.children || []) {
      for (const el of page.children || []) {
        if (el.id === eid || Number(el.id) === Number(eid)) {
          return `${mod.name} / ${page.name} / ${el.name}`
        }
      }
    }
  }
  return ''
}
</script>

<style>
/* 全局样式覆盖Element Plus默认样式 */
.el-drawer__header {
  padding: 0 !important;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  border-bottom: none !important;
  margin-bottom: 10px !important;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1) !important;
}
</style>

<style scoped>
/* 右侧滑栏详情样式 */
.test-case-detail-drawer {
  padding: 0;
}

.drawer-header {
  width: 100%;
  padding: 12px 32px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  position: relative;
  overflow: hidden;
}

.drawer-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.05) 100%);
  pointer-events: none;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  z-index: 1;
}

.header-left {
  flex: 1;
}

.header-title h3 {
  font-size: 20px;
  font-weight: 700;
  color: #ffffff;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  letter-spacing: -0.5px;
}

.header-subtitle {
  margin-top: 2px;
}

.subtitle-text {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.8);
  font-weight: 400;
  letter-spacing: 0.2px;
}

.header-right {
  flex-shrink: 0;
}

.save-btn {
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 600;
  font-size: 14px;
  color: #ffffff;
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.save-btn:hover {
  background: rgba(255, 255, 255, 0.25);
  border-color: rgba(255, 255, 255, 0.5);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}

.save-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.save-btn .el-icon {
  margin-right: 6px;
  font-size: 16px;
}

.close-btn {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 8px;
  padding: 12px 20px;
  font-weight: 500;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  margin-left: 12px;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.4);
  color: #ffffff;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.close-btn:active {
  transform: translateY(0);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
}

.close-btn .el-icon {
  margin-right: 6px;
  font-size: 16px;
}


/* 编辑模式详情区域样式 */
.detail-section-edit {
  margin-bottom: 20px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
  overflow: hidden;
  border: 1px solid #f0f0f0;
}

.section-header-edit {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 16px;
  background: #f8f9fa;
  border-bottom: 1px solid #e9ecef;
}

.section-header-edit .el-icon {
  font-size: 16px;
  color: #409eff;
}

.section-header-edit h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-content-edit {
  padding: 20px;
}

/* 基本信息容器样式 */
.basic-info-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.info-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  color: #303133;
  font-size: 14px;
  margin-bottom: 4px;
}

.info-label .el-icon {
  font-size: 16px;
  color: #409eff;
}

.info-content {
  flex: 1;
}


/* 标签容器样式 */
.tags-container {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
}

.tag-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tag-label {
  font-size: 13px;
  color: #909399;
  font-weight: 500;
  min-width: 50px;
}

.info-tag {
  font-weight: 600;
  border-radius: 12px;
  padding: 4px 12px;
}




/* 响应式设计 */
@media (max-width: 768px) {
  .drawer-header {
    padding: 20px 24px;
  }
  
  .header-content {
    flex-direction: column;
    gap: 16px;
    align-items: stretch;
  }
  
  .header-left {
    text-align: center;
  }
  
  .header-title h3 {
    font-size: 20px;
  }
  
  .subtitle-text {
    font-size: 13px;
  }
  
  .header-right {
    display: flex;
    justify-content: center;
    gap: 8px;
  }
  
  .save-btn {
    flex: 1;
    max-width: 200px;
    font-size: 13px;
    padding: 10px 20px;
    margin-left: 0;
  }
  
  .close-btn {
    flex: 1;
    max-width: 200px;
    font-size: 13px;
    padding: 10px 20px;
    margin-left: 0;
  }
  
  /* 当没有保存按钮时，关闭按钮居中 */
  .header-right:has(.close-btn:only-child) {
    justify-content: center;
  }
  
  .header-right:has(.close-btn:only-child) .close-btn {
    max-width: 150px;
  }
  
  .tags-container {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  
  .step-main-row {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  
  .step-header-edit {
    flex-direction: column;
    gap: 12px;
  }
  
  .step-number {
    align-self: flex-start;
  }
  
  .step-item-edit {
    padding: 12px;
  }
  
  .step-description-row {
    flex-direction: column;
    gap: 8px;
    padding: 8px;
  }
  
  .step-description-label {
    min-width: auto;
  }
  
  .step-fields {
    gap: 8px;
  }
}

/* 描述文本样式 */
.description-text {
  margin: 0;
  color: #606266;
  line-height: 1.5;
  font-size: 14px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 6px;
  border-left: 3px solid #409eff;
}

/* 前置条件编辑样式 */
.preconditions-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.precondition-item-edit {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: #f8f9fa;
  border-radius: 6px;
  border: 1px solid #e9ecef;
}


/* 测试步骤编辑样式 */
.steps-edit {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: grab;
  color: #909399;
  font-size: 20px;
  padding: 0 4px;
  transition: color 0.2s ease, transform 0.2s ease;
}

.drag-handle:hover {
  color: #409eff;
  transform: scale(1.1);
}

.drag-handle:active {
  cursor: grabbing;
}

.step-item-edit {
  padding: 16px;
  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
  border-radius: 12px;
  border: 1px solid #e1e8f0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.step-item-edit::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #409eff, #67c23a, #e6a23c);
  border-radius: 12px 12px 0 0;
}

.step-item-edit:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(64, 158, 255, 0.15);
  border-color: #409eff;
}

.step-header-edit {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.step-number {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.3);
  position: relative;
}

.step-number::after {
  content: '';
  position: absolute;
  inset: -2px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  border-radius: 50%;
  z-index: -1;
  opacity: 0.2;
  filter: blur(4px);
}

.step-number-text {
  font-size: 14px;
  font-weight: 700;
  color: #ffffff;
  line-height: 1;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}

.step-fields {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.step-description-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: rgba(64, 158, 255, 0.05);
  border-radius: 8px;
  border: 1px solid rgba(64, 158, 255, 0.1);
}

.step-description-label {
  min-width: 70px;
  font-size: 13px;
  font-weight: 600;
  color: #409eff;
  flex-shrink: 0;
}

.step-description-input {
  flex: 1;
}

.step-description-input .el-input__inner {
  border: 1px solid rgba(64, 158, 255, 0.2);
  border-radius: 6px;
  background: #ffffff;
  transition: all 0.2s ease;
}

.step-description-input .el-input__inner:focus {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.1);
}

.step-main-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr auto;
  gap: 12px;
  align-items: end;
}

.step-input-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.step-input {
  width: 100%;
}

.step-input .el-input__inner {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #ffffff;
  transition: all 0.2s ease;
  font-size: 13px;
}

.step-input .el-input__inner:focus {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.1);
}

.step-actions {
  display: flex;
  align-items: flex-end;
  height: 32px;
  padding-bottom: 2px;
}

.step-delete-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, #f56565, #e53e3e);
  border: none;
  box-shadow: 0 2px 8px rgba(245, 101, 101, 0.3);
  transition: all 0.2s ease;
}

.step-delete-btn:hover {
  transform: scale(1.05);
  box-shadow: 0 4px 12px rgba(245, 101, 101, 0.4);
}

.step-delete-btn .el-icon {
  color: #ffffff;
  font-size: 14px;
}


/* 测试数据编辑样式 */
.test-data-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.test-data-item-edit {
  padding: 8px;
  background: #f8f9fa;
  border-radius: 6px;
  border: 1px solid #e9ecef;
}


/* 脚本编辑样式 */
.script-edit {
  margin-top: 8px;
}


/* 空状态样式 */
.empty-state {
  text-align: center;
  padding: 20px;
}

/* 紧凑描述文本样式 */
.description-text-compact {
  margin: 0;
  color: #606266;
  line-height: 1.5;
  font-size: 14px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 6px;
  border-left: 3px solid #409eff;
}

/* 信息网格样式 */
.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f8f9fa;
  border-radius: 6px;
  border: 1px solid #e9ecef;
}

.info-item label {
  font-weight: 500;
  color: #606266;
  font-size: 13px;
  min-width: 60px;
}

.info-item span,
.info-item .el-tag {
  color: #303133;
  font-size: 13px;
}

/* 紧凑测试步骤卡片样式 */
.step-card-compact {
  background: #ffffff;
  border: 1px solid #e8f0fe;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
  position: relative;
}

.step-card-compact:hover {
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.15);
  border-color: #409eff;
  transform: translateY(-1px);
}

.step-card-compact::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  border-radius: 6px 0 0 6px;
}

/* 紧凑步骤头部 */
.step-header-compact {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.step-id {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  color: white;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: 0 2px 4px rgba(64, 158, 255, 0.3);
}

.step-main-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  flex-wrap: wrap;
}

.action-tag-compact {
  font-weight: 600;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 10px;
  border: none;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  flex-shrink: 0;
}

.step-target-compact {
  font-weight: 600;
  color: #303133;
  font-size: 14px;
  line-height: 1.3;
  flex: 1;
  min-width: 0;
}

.step-value-compact {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  color: #409eff;
  background: #e8f4fd;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #b3d8ff;
  font-weight: 500;
  flex-shrink: 0;
}

/* 紧凑步骤内容 */
.step-content-compact {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f0f2f5;
}

.info-item-compact {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 8px;
  background: #f8f9fa;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.4;
  transition: background-color 0.2s ease;
}

.info-item-compact:hover {
  background: #f0f2f5;
}

.info-icon {
  font-size: 12px;
  color: #909399;
  margin-top: 1px;
  flex-shrink: 0;
}

.info-text {
  color: #606266;
  flex: 1;
  word-break: break-word;
}

/* 特殊字段样式 */

.expected-item {
  background: #fef0f0;
  border-left: 2px solid #f56c6c;
}

.expected-item .info-icon {
  color: #f56c6c;
}

/* 时间线样式优化 */
.el-timeline-item__timestamp {
  display: none;
}

.el-timeline-item__node {
  background-color: #409eff;
  border-color: #409eff;
  width: 8px;
  height: 8px;
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #e1f5fe;
}

.el-timeline-item__tail {
  border-left: 2px solid #e1f5fe;
  left: 3px;
}

/* 空状态优化 */
.step-content-compact:empty {
  display: none;
}

/* 预期结果样式 */
.expected-result {
  line-height: 1.6;
  color: #606266;
  background: #f0f9ff;
  padding: 12px;
  border-radius: 4px;
  border-left: 3px solid #67c23a;
}

/* 紧凑脚本内容样式 */
.script-content-compact {
  max-height: 300px;
  overflow-y: auto;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 12px;
}

.script-content-compact pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.4;
  color: #2c3e50;
}

.script-content-compact code {
  background: none;
  padding: 0;
  color: inherit;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .header-info {
    flex-direction: column;
    align-items: flex-start;
  }
  
  .info-meta {
    margin-top: 8px;
  }
  
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
