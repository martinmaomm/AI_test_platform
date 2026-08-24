<template>
  <el-drawer
    v-model="visible"
    :title="drawerTitle"
    size="70%"
    direction="rtl"
    :with-header="true"
    :before-close="handleClose"
    :show-close="false"
    class="api-case-edit-drawer"
    body-class="api-case-edit-drawer-body"
  >
    <template #header>
      <div class="drawer-header">
        <div class="header-content">
          <div class="header-left">
            <div class="header-title">
              <h3>API 测试用例编辑</h3>
              <div class="header-subtitle">
                <span class="subtitle-text">编辑测试用例的详细信息</span>
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
                  <div v-if="showPriority" class="tag-group">
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
                    <span class="tag-label">测试类型:</span>
                    <el-select 
                      v-model="editForm.test_type" 
                      placeholder="选择测试类型"
                      size="small"
                      style="width: 140px"
                    >
                      <el-option label="正向测试" value="positive" />
                      <el-option label="负向测试" value="negative" />
                      <el-option label="边界测试" value="boundary" />
                      <el-option label="安全测试" value="security" />
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
      
      <!-- 测试脚本 -->
      <div class="detail-section-edit">
        <div class="section-header-edit">
          <h4>测试脚本</h4>
        </div>
        <div class="section-content-edit">
          <el-tabs v-model="activeTab" @tab-change="handleTabChange" class="script-tabs">
            <!-- Tab 1: 脚本模式 -->
            <el-tab-pane label="脚本模式" name="script">
              <div class="script-edit">
                <MonacoEditor
                  v-model:value="editForm.script_content"
                  language="python"
                  theme="vs-dark"
                  :read-only="false"
                  height="500px"
                  @change="handleScriptChange"
                />
              </div>
            </el-tab-pane>

            <!-- Tab 2: 可视化编辑 -->
            <el-tab-pane label="可视化编辑" name="visual">
              <div class="visual-edit">
                <el-alert
                  v-if="parseError"
                  title="脚本解析失败"
                  type="error"
                  :description="parseError"
                  :closable="false"
                  style="margin-bottom: 16px"
                />
                
                <div v-else class="visual-editor-container">
                  <!-- 配置区域 -->
                  <el-card class="config-card" shadow="hover">
                    <template #header>
                      <div class="card-header">
                        <span class="card-title">
                          <el-icon><Setting /></el-icon>
                          测试配置
                        </span>
                      </div>
                    </template>
                    <el-form :model="visualForm.config" label-width="120px" label-position="left">
                      <el-form-item label="测试名称">
                        <el-input v-model="visualForm.config.name" placeholder="请输入测试名称" />
                      </el-form-item>
                      <el-form-item label="基础URL">
                        <el-input v-model="visualForm.config.base_url" placeholder="例如: http://api.example.com" />
                      </el-form-item>
                      <el-form-item label="SSL验证">
                        <el-switch v-model="visualForm.config.verify" />
                      </el-form-item>
                    </el-form>
                  </el-card>

                  <!-- 测试步骤区域 -->
                  <el-card class="steps-card" shadow="hover">
                    <template #header>
                      <div class="card-header">
                        <span class="card-title">
                          <el-icon><List /></el-icon>
                          测试步骤
                        </span>
                        <el-dropdown
                          v-if="isScenarioType"
                          split-button
                          type="primary"
                          size="small"
                          @click="addTestStep"
                          @command="handleAddStepCommand"
                        >
                          <el-icon><Plus /></el-icon>
                          添加空白步骤
                          <template #dropdown>
                            <el-dropdown-menu>
                              <el-dropdown-item command="import">
                                <el-icon><MagicStick /></el-icon>
                                从接口用例导入
                              </el-dropdown-item>
                            </el-dropdown-menu>
                          </template>
                        </el-dropdown>
                        <el-button v-else type="primary" size="small" @click="addTestStep">
                          <el-icon><Plus /></el-icon>
                          添加步骤
                        </el-button>
                      </div>
                    </template>

                    <el-collapse v-model="activeSteps" accordion>
                      <el-collapse-item
                        v-for="(step, index) in visualForm.teststeps"
                        :key="index"
                        :name="index"
                        class="step-item"
                      >
                        <template #title>
                          <div class="step-title">
                            <el-tag :type="getMethodTagType(step.request?.method)" size="small">
                              {{ step.request?.method || 'GET' }}
                            </el-tag>
                            <span class="step-name">{{ step.name || `步骤 ${index + 1}` }}</span>
                            <el-button
                              type="danger"
                              size="small"
                              text
                              @click.stop="removeTestStep(index)"
                              class="delete-step-btn"
                            >
                              <el-icon><Delete /></el-icon>
                            </el-button>
                          </div>
                        </template>

                        <el-form :model="step" label-width="120px" label-position="left">
                          <el-form-item label="步骤名称">
                            <el-input v-model="step.name" placeholder="请输入步骤名称" />
                          </el-form-item>

                          <el-divider content-position="left">请求配置</el-divider>

                          <el-form-item label="请求方法">
                            <el-select v-model="step.request.method" placeholder="选择请求方法">
                              <el-option label="GET" value="GET" />
                              <el-option label="POST" value="POST" />
                              <el-option label="PUT" value="PUT" />
                              <el-option label="DELETE" value="DELETE" />
                              <el-option label="PATCH" value="PATCH" />
                            </el-select>
                          </el-form-item>

                          <el-form-item label="请求路径">
                            <el-input v-model="step.request.url" placeholder="例如: /api/users" />
                          </el-form-item>

                          <el-form-item label="请求头">
                            <el-input
                              v-model="step.request.headersText"
                              type="textarea"
                              :rows="3"
                              placeholder='JSON格式，例如: {"Content-Type": "application/json"}'
                            />
                          </el-form-item>

                          <el-form-item label="请求参数">
                            <el-input
                              v-model="step.request.paramsText"
                              type="textarea"
                              :rows="3"
                              placeholder='JSON格式，例如: {"page": 1, "size": 10}'
                            />
                          </el-form-item>

                          <el-form-item label="请求体" v-if="['POST', 'PUT', 'PATCH'].includes(step.request.method)">
                            <el-input
                              v-model="step.request.jsonText"
                              type="textarea"
                              :rows="5"
                              placeholder='JSON格式，例如: {"username": "test", "password": "123456"}'
                            />
                          </el-form-item>

                          <el-divider content-position="left">断言验证</el-divider>

                          <el-form-item label="状态码断言">
                            <el-input-number v-model="step.validateStatusCode" :min="100" :max="599" placeholder="200" style="width: 150px;" />
                          </el-form-item>

                          <el-form-item label="响应断言">
                            <div class="assertions-builder">
                              <!-- 断言列表 -->
                              <div v-for="(assertion, aIndex) in step.assertions" :key="aIndex" class="assertion-item">
                                <div class="assertion-row">
                                  <!-- 字段路径选择 -->
                                  <div class="field-selector">
                                    <el-input
                                      v-model="assertion.field"
                                      placeholder="字段路径，如: body.code"
                                      style="width: 200px;"
                                      @change="syncAssertionsToValidateText(step)"
                                    >
                                      <template #append>
                                        <el-button
                                          @click="openFieldSelector(step, assertion)"
                                          :icon="Search"
                                          title="从响应结构中选择"
                                        />
                                      </template>
                                    </el-input>
                                  </div>
                                  
                                  <!-- 运算符 -->
                                  <el-select 
                                    v-model="assertion.operator" 
                                    placeholder="选择运算符" 
                                    style="width: 120px;"
                                    @change="syncAssertionsToValidateText(step)"
                                  >
                                    <el-option label="等于 (==)" value="eq" />
                                    <el-option label="不等于 (!=)" value="ne" />
                                    <el-option label="包含" value="contains" />
                                    <el-option label="不包含" value="not_contains" />
                                    <el-option label="大于 (>)" value="gt" />
                                    <el-option label="大于等于 (>=)" value="gte" />
                                    <el-option label="小于 (<)" value="lt" />
                                    <el-option label="小于等于 (<=)" value="lte" />
                                    <el-option label="存在" value="exists" />
                                    <el-option label="不存在" value="not_exists" />
                                    <el-option label="类型是" value="type_match" />
                                  </el-select>
                                  
                                  <!-- 值类型选择 -->
                                  <el-select
                                    v-model="assertion.valueType"
                                    placeholder="类型"
                                    style="width: 100px;"
                                    v-if="!['exists', 'not_exists'].includes(assertion.operator)"
                                    @change="syncAssertionsToValidateText(step)"
                                  >
                                    <el-option label="字符串" value="string" />
                                    <el-option label="整数" value="integer" />
                                    <el-option label="小数" value="number" />
                                    <el-option label="布尔" value="boolean" />
                                    <el-option label="null" value="null" />
                                  </el-select>
                                  
                                  <!-- 期望值 -->
                                  <el-input
                                    v-model="assertion.expected"
                                    placeholder="期望值"
                                    style="width: 200px;"
                                    v-if="!['exists', 'not_exists', 'null'].includes(assertion.operator) && assertion.valueType !== 'null'"
                                    @change="syncAssertionsToValidateText(step)"
                                  />
                                  
                                  <!-- 删除按钮 -->
                                  <el-button
                                    type="danger"
                                    size="small"
                                    text
                                    @click="removeAssertion(step, aIndex)"
                                    :icon="Delete"
                                  />
                                </div>
                              </div>
                              
                              <!-- 添加断言按钮 -->
                              <el-button
                                type="primary"
                                size="small"
                                @click="addAssertion(step)"
                                :icon="Plus"
                                plain
                              >
                                添加断言
                              </el-button>
                            </div>
                          </el-form-item>
                        </el-form>
                      </el-collapse-item>
                    </el-collapse>

                    <el-empty v-if="!visualForm.teststeps || visualForm.teststeps.length === 0" description="暂无测试步骤，点击上方按钮添加" />
                  </el-card>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
      
    </div>
  </el-drawer>

  <!-- 字段选择器对话框 -->
  <el-dialog
    v-model="fieldSelectorVisible"
    title="选择响应字段"
    width="700px"
    :close-on-click-modal="false"
  >
    <div class="field-selector-dialog">
      <el-alert
        title="点击下方的字段路径来选择要断言的字段"
        type="info"
        :closable="false"
        style="margin-bottom: 16px;"
      />

      <!-- 响应结构树 -->
      <div class="response-structure">
        <h4>响应数据结构示例</h4>
        <el-tree
          ref="fieldTree"
          :data="responseTreeData"
          :props="treeProps"
          node-key="path"
          :default-expand-all="true"
          :highlight-current="true"
          :current-node-key="selectedFieldPath"
          @node-click="handleTreeNodeClick"
          class="response-tree"
        >
          <template #default="{ node, data }">
            <div class="tree-node-content">
              <div class="node-main">
                <span class="node-label">{{ data.label }}</span>
                <el-tag size="small" :type="getTypeTagType(data.type)" style="margin-left: 8px;">
                  {{ data.type }}
                </el-tag>
              </div>
              <div class="node-info" v-if="data.example !== undefined">
                <span class="node-path">{{ data.path }}</span>
                <span class="node-example">示例: <code>{{ formatExampleValue(data.example) }}</code></span>
              </div>
            </div>
          </template>
        </el-tree>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="fieldSelectorVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmFieldSelection" :disabled="!selectedFieldPath">
          确定
        </el-button>
      </div>
    </template>
  </el-dialog>

  <!-- 导入测试用例弹框 -->
  <el-dialog
    v-model="showImportDialog"
    title="导入测试用例"
    width="780px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <!-- 多条件联动过滤 -->
    <div class="import-filter-bar">
      <el-select
        v-model="importModuleFilter"
        clearable
        placeholder="选择模块"
        size="small"
        style="width: 160px;"
        @change="importEndpointFilter = ''"
      >
        <el-option v-for="m in importModuleOptions" :key="m" :label="m" :value="m" />
      </el-select>
      <el-select
        v-model="importEndpointFilter"
        clearable
        placeholder="选择接口端点"
        size="small"
        style="flex: 1; min-width: 180px;"
      >
        <el-option v-for="ep in importEndpointOptions" :key="ep" :label="ep" :value="ep" />
      </el-select>
      <el-input
        v-model="importTitleSearch"
        clearable
        placeholder="搜索用例标题..."
        size="small"
        style="width: 200px;"
        :prefix-icon="Search"
      />
      <span class="import-filter-total">共 {{ filteredImportCases.length }} 条</span>
    </div>

    <el-table
      ref="importTableRef"
      v-loading="importLoading"
      :data="filteredImportCases"
      @selection-change="handleImportSelectionChange"
      height="380"
      size="small"
      border
      stripe
      row-key="id"
      empty-text="暂无单接口测试用例"
      style="width: 100%;"
    >
      <el-table-column type="selection" width="44" reserve-selection />
      <el-table-column label="方法" width="72" align="center">
        <template #default="{ row }">
          <el-tag :type="getMethodTagType(row.endpoint_info?.method)" size="small" effect="dark">
            {{ row.endpoint_info?.method || '—' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="接口路径" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <code class="path-code">{{ row.endpoint_info?.path || '—' }}</code>
        </template>
      </el-table-column>
      <el-table-column label="用例标题" min-width="200" show-overflow-tooltip prop="title" />
    </el-table>

    <template #footer>
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span style="font-size: 13px; color: var(--el-text-color-secondary);">
          已选 {{ importSelected.length }} 个用例
        </span>
        <div>
          <el-button @click="showImportDialog = false">取消</el-button>
          <el-button
            type="primary"
            :disabled="importSelected.length === 0"
            :loading="importConfirming"
            @click="confirmImportCases"
          >
            确认导入 ({{ importSelected.length }})
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Edit,
  VideoPlay,
  View,
  Connection,
  DataAnalysis,
  List,
  Calendar,
  User,
  Setting,
  Document,
  Check,
  Close,
  Star,
  EditPen,
  Plus,
  Delete,
  Search,
  MagicStick
} from '@element-plus/icons-vue'
import { updateAPITestCase, getAPITestCases, getAPITestCase } from '@/api/apiTesting'
import { useProjectStore } from '@/stores/project'
import MonacoEditor from '@/components/MonacoEditor.vue'

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
  showPriority: {
    type: Boolean,
    default: true
  }
})

// Emits
const emit = defineEmits(['update:modelValue', 'edit', 'run', 'update'])

// 项目store
const projectStore = useProjectStore()

// 响应式数据
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})


// 编辑表单数据
const editForm = ref({
  title: '',
  description: '',
  priority: 'medium',
  test_type: 'positive',
  script_content: ''
})

// 原始数据存储
const originalData = ref({})

// Tab状态
const activeTab = ref('script')
const activeSteps = ref([0]) // 默认展开第一个步骤

// 可视化编辑表单
const visualForm = ref({
  config: {
    name: '',
    base_url: '',
    verify: false,
    variables: {}
  },
  teststeps: []
})

// 解析错误信息
const parseError = ref('')

// 字段选择器状态
const fieldSelectorVisible = ref(false)
const selectedFieldPath = ref('')
const currentEditingAssertion = ref(null)
const currentEditingStep = ref(null)
const responseFields = ref([])
const responseTreeData = ref([])
const fieldTree = ref(null)

// 导入用例弹框状态
const showImportDialog = ref(false)
const endpointCaseList = ref([])  // 原始数据源（拉取到的所有单接口用例）
const importSelected = ref([])
const importLoading = ref(false)
const importConfirming = ref(false)
const importTableRef = ref(null)

// 多条件联动过滤
const importModuleFilter = ref('')
const importEndpointFilter = ref('')
const importTitleSearch = ref('')

// 从 endpointCaseList 提取去重模块名
const importModuleOptions = computed(() => {
  const set = new Set()
  endpointCaseList.value.forEach(tc => {
    const name = tc.endpoint_info?.module_name
    if (name) set.add(name)
  })
  return Array.from(set).sort()
})

// 联动：有模块时只显示该模块下的端点；否则显示全部
const importEndpointOptions = computed(() => {
  const mod = importModuleFilter.value
  const list = mod
    ? endpointCaseList.value.filter(tc => tc.endpoint_info?.module_name === mod)
    : endpointCaseList.value
  const set = new Set()
  list.forEach(tc => {
    const m = tc.endpoint_info?.method || 'GET'
    const p = tc.endpoint_info?.path || ''
    if (p) set.add(`${m} ${p}`)
  })
  return Array.from(set).sort()
})

// 过滤后的导入用例列表（模块 + 端点 + 标题）
const filteredImportCases = computed(() => {
  const list = endpointCaseList.value
  const mod = importModuleFilter.value
  const ep = importEndpointFilter.value
  const kw = importTitleSearch.value.trim()
  return list.filter(tc => {
    if (mod && tc.endpoint_info?.module_name !== mod) return false
    if (ep) {
      const pathPart = (ep.split(' ')[1] || '').trim()
      if (pathPart && !(tc.endpoint_info?.path || '').includes(pathPart)) return false
    }
    if (kw && !(tc.title || '').includes(kw)) return false
    return true
  })
})

// 是否为场景类型（仅场景支持从接口用例导入）
const isScenarioType = computed(() => props.testCase?.test_case_type === 'scenario')

// -------- 导入用例弹框方法 --------
const handleAddStepCommand = (cmd) => {
  if (cmd === 'import') openImportDialog()
}

const fetchEndpointCases = async () => {
  const projectId = projectStore.currentProjectId
  if (!projectId) return
  importLoading.value = true
  try {
    const res = await getAPITestCases(projectId, {
      test_case_type: 'endpoint',
      page: 1,
      page_size: 500
    })
    const data = res?.data ?? {}
    endpointCaseList.value = Array.isArray(data.items) ? data.items : []
  } catch (e) {
    ElMessage.error('加载端点用例失败：' + (e?.message || '未知错误'))
  } finally {
    importLoading.value = false
  }
}

const openImportDialog = async () => {
  importModuleFilter.value = ''
  importEndpointFilter.value = ''
  importTitleSearch.value = ''
  importSelected.value = []
  showImportDialog.value = true
  await fetchEndpointCases()
  importTableRef.value?.clearSelection?.()
}

const handleImportSelectionChange = (rows) => {
  importSelected.value = rows
}

/**
 * 确认导入：将选中用例的 teststeps[0] 逐条追加到当前场景
 */
const confirmImportCases = async () => {
  const rows = importSelected.value
  if (!rows.length) return

  importConfirming.value = true
  const newSteps = []
  const skipped = []

  try {
    const projectId = projectStore.currentProjectId
    const results = await Promise.allSettled(
      rows.map(row => getAPITestCase(projectId, row.id))
    )

    results.forEach((result, idx) => {
      const title = rows[idx].title || `用例 #${rows[idx].id}`

      if (result.status === 'rejected') {
        skipped.push(title)
        return
      }

      try {
        const detail = result.value
        const tc = (detail?.success !== undefined ? detail.data : detail) ?? {}
        const raw = tc.script_content

        if (!raw) { skipped.push(title); return }

        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
        const firstStep = parsed?.teststeps?.[0] ?? parsed?.steps?.[0]
        if (!firstStep) { skipped.push(title); return }

        const step = JSON.parse(JSON.stringify(firstStep))
        step.name = tc.title || step.name || `步骤 ${visualForm.value.teststeps.length + newSteps.length + 1}`
        newSteps.push(step)
      } catch {
        skipped.push(title)
      }
    })
  } finally {
    importConfirming.value = false
  }

  if (newSteps.length === 0) {
    ElMessage.warning(skipped.length ? '所有选中用例均无法解析步骤' : '没有可导入的步骤')
    return
  }

  newSteps.forEach(rawStep => {
    const request = rawStep.request || {}
    const validate = rawStep.validate || []
    const assertions = parseValidateToAssertions(validate)
    const statusCodeAssertion = validate.find(v => v[1] && v[1][0] === 'status_code')
    visualForm.value.teststeps.push({
      name: rawStep.name || '',
      request: {
        method: request.method || 'GET',
        url: request.url || '',
        headers: request.headers || {},
        headersText: JSON.stringify(request.headers || {}, null, 2),
        params: request.params || {},
        paramsText: JSON.stringify(request.params || {}, null, 2),
        json: request.json || request.data || {},
        jsonText: JSON.stringify(request.json || request.data || {}, null, 2)
      },
      assertions,
      validate,
      validateText: JSON.stringify(validate, null, 2),
      validateStatusCode: statusCodeAssertion ? statusCodeAssertion[1][1] : 200
    })
  })

  showImportDialog.value = false
  importSelected.value = []

  const msg = skipped.length
    ? `成功导入 ${newSteps.length} 个步骤（${skipped.length} 个用例解析失败已跳过）`
    : `成功导入 ${newSteps.length} 个步骤`
  ElMessage.success(msg)
}

// 树形组件配置
const treeProps = {
  children: 'children',
  label: 'label'
}

// 检查是否有数据变化
const hasChanges = computed(() => {
  if (!props.testCase || Object.keys(originalData.value).length === 0) {
    return false
  }
  
  // 比较基本字段
  const basicFields = ['title', 'description', 'priority', 'test_type']
  for (const field of basicFields) {
    if (editForm.value[field] !== originalData.value[field]) {
      return true
    }
  }
  
  // 比较脚本内容
  if (editForm.value.script_content !== originalData.value.script_content) {
    return true
  }
  
  return false
})

// 计算属性
const drawerTitle = computed(() => {
  return props.testCase ? `测试用例编辑 - ${props.testCase.title}` : '测试用例编辑'
})

// 监听可视化表单的变化，实时同步到脚本
watch(
  () => visualForm.value,
  () => {
    if (activeTab.value === 'visual' && visible.value) {
      // 防抖：延迟同步，避免频繁操作
      if (syncTimer) {
        clearTimeout(syncTimer)
      }
      syncTimer = setTimeout(() => {
        syncAllVisualToScript()
      }, 500)  // 500ms 延迟
    }
  },
  { deep: true }
)

let syncTimer = null

// 组件卸载时清理定时器
onUnmounted(() => {
  if (syncTimer) {
    clearTimeout(syncTimer)
  }
})

// 方法
const handleClose = async () => {
  // 如果有未保存的更改，提示用户
  if (hasChanges.value) {
    try {
      await ElMessageBox.confirm(
        '您有未保存的更改，是否保存后再关闭？',
        '提示',
        {
          confirmButtonText: '保存并关闭',
          cancelButtonText: '不保存',
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
      // 用户选择保存
      await saveTestCase()
    } catch (action) {
      // action === 'cancel' 表示用户选择"不保存"
      // action === 'close' 表示用户点击了X或按ESC
      if (action === 'cancel') {
        // 用户明确选择不保存，直接关闭
        visible.value = false
      }
      // 如果是 'close'，不做任何操作（保持drawer打开）
      return
    }
  } else {
    // 没有变更，直接关闭
    visible.value = false
  }
}

const handleEdit = () => {
  emit('edit', props.testCase)
}

const handleRun = () => {
  emit('run', props.testCase)
}

// 初始化编辑表单
const initEditForm = () => {
  if (props.testCase) {
    const formData = {
      title: props.testCase.title || '',
      description: props.testCase.description || '',
      priority: props.testCase.priority || 'medium',
      test_type: props.testCase.test_type || 'positive',
      script_content: props.testCase.script_content || ''
    }
    
    editForm.value = formData
    
    // 保存原始数据用于比较
    originalData.value = {
      title: formData.title,
      description: formData.description,
      priority: formData.priority,
      test_type: formData.test_type,
      script_content: formData.script_content
    }
    
    // 如果当前在可视化编辑模式，解析脚本到可视化表单
    if (activeTab.value === 'visual') {
      parseScriptToVisual()
    }
  }
}

// 保存测试用例
const saveTestCase = async () => {
  if (!props.testCase?.id) {
    console.error('测试用例ID不存在')
    return
  }
  
  try {
    // 如果当前在可视化编辑模式，先同步到脚本
    if (activeTab.value === 'visual') {
      syncAllVisualToScript()
    }
    
    // 准备保存数据
    const saveData = {
      title: editForm.value.title,
      description: editForm.value.description,
      priority: editForm.value.priority,
      test_type: editForm.value.test_type,
      script_content: editForm.value.script_content
    }
    
    // 调用API保存 (使用PUT进行完整更新)
    const response = await updateAPITestCase(projectStore.currentProjectId, props.testCase.id, saveData)
    
    if (response.success) {
      // 保存成功后，更新原始数据
      originalData.value = {
        title: editForm.value.title,
        description: editForm.value.description,
        priority: editForm.value.priority,
        test_type: editForm.value.test_type,
        script_content: editForm.value.script_content
      }
      
      // 显示成功消息
      ElMessage.success('测试用例保存成功')
      
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

// 处理脚本内容变化
const handleScriptChange = (value) => {
  editForm.value.script_content = value
}

// Tab切换处理
const handleTabChange = (tabName) => {
  if (tabName === 'visual') {
    // 切换到可视化编辑：解析JSON到表单
    parseScriptToVisual()
  } else if (tabName === 'script') {
    // 切换到脚本模式：将可视化表单数据序列化为JSON
    syncAllVisualToScript()
  }
}

// 解析脚本到可视化表单
const parseScriptToVisual = () => {
  try {
    parseError.value = ''
    
    if (!editForm.value.script_content || editForm.value.script_content.trim() === '') {
      // 空脚本，初始化为空表单
      visualForm.value = {
        config: {
          name: editForm.value.title || '测试用例',
          base_url: '',
          verify: false,
          variables: {}
        },
        teststeps: []
      }
      return
    }
    
    // 尝试解析JSON
    const parsed = JSON.parse(editForm.value.script_content)
    
    // 初始化配置
    visualForm.value.config = {
      name: parsed.config?.name || editForm.value.title || '测试用例',
      base_url: parsed.config?.base_url || '',
      verify: parsed.config?.verify !== undefined ? parsed.config.verify : false,
      variables: parsed.config?.variables || {}
    }
    
    // 初始化测试步骤
    visualForm.value.teststeps = (parsed.teststeps || []).map(step => {
      const request = step.request || {}
      const validate = step.validate || []
      
      // 解析断言为可视化格式
      const assertions = parseValidateToAssertions(validate)
      
      // 查找状态码断言
      const statusCodeAssertion = validate.find(v => 
        v[1] && v[1][0] === 'status_code'
      )
      
      return {
        name: step.name || '',
        request: {
          method: request.method || 'GET',
          url: request.url || '',
          headers: request.headers || {},
          headersText: JSON.stringify(request.headers || {}, null, 2),
          params: request.params || {},
          paramsText: JSON.stringify(request.params || {}, null, 2),
          json: request.json || request.data || {},
          jsonText: JSON.stringify(request.json || request.data || {}, null, 2)
        },
        assertions: assertions,  // 可视化断言
        validate: validate,
        validateText: JSON.stringify(validate, null, 2),
        validateStatusCode: statusCodeAssertion ? statusCodeAssertion[1][1] : 200
      }
    })
    
    // 默认展开第一个步骤
    if (visualForm.value.teststeps.length > 0) {
      activeSteps.value = [0]
    }
    
  } catch (error) {
    console.error('解析脚本失败:', error)
    parseError.value = `JSON解析失败: ${error.message}。请检查脚本格式是否正确。`
  }
}

// 将可视化表单同步到脚本（单个步骤）
const syncVisualToScript = (step) => {
  // 构建validate数组
  let validate = []
  
  // 1. 添加状态码断言
  if (step.validateStatusCode) {
    validate.push(['eq', ['status_code', parseInt(step.validateStatusCode)]])
  }
  
  // 2. 添加可视化断言
  if (step.assertions && step.assertions.length > 0) {
    for (const assertion of step.assertions) {
      const { field, operator, expected } = assertion
      
      // 根据运算符构建断言规则
      let rule
      switch (operator) {
        case 'exists':
        case 'not_exists':
          rule = [operator, [field]]
          break
        default:
          rule = [operator, [field, parseValue(expected)]]
      }
      
      validate.push(rule)
    }
  }
  
  // 更新步骤的validate和validateText
  step.validate = validate
  step.validateText = JSON.stringify(validate, null, 2)
}

// 将所有可视化表单同步到脚本
const syncAllVisualToScript = () => {
  try {
    parseError.value = ''
    
    // 先同步所有步骤的断言
    visualForm.value.teststeps.forEach(step => {
      syncVisualToScript(step)
    })
    
    // 构建HttpRunner JSON结构
    const httprunnerJson = {
      config: {
        name: visualForm.value.config.name,
        base_url: visualForm.value.config.base_url,
        verify: visualForm.value.config.verify,
        variables: visualForm.value.config.variables
      },
      teststeps: visualForm.value.teststeps.map(step => {
        // 解析JSON字符串
        let headers = {}
        let params = {}
        let json = {}
        
        try {
          if (step.request.headersText) {
            headers = JSON.parse(step.request.headersText)
          }
        } catch (e) {
          console.warn('Headers解析失败:', e)
        }
        
        try {
          if (step.request.paramsText) {
            params = JSON.parse(step.request.paramsText)
          }
        } catch (e) {
          console.warn('Params解析失败:', e)
        }
        
        try {
          if (step.request.jsonText && ['POST', 'PUT', 'PATCH'].includes(step.request.method)) {
            json = JSON.parse(step.request.jsonText)
          }
        } catch (e) {
          console.warn('JSON body解析失败:', e)
        }
        
        const stepObj = {
          name: step.name,
          request: {
            method: step.request.method,
            url: step.request.url
          }
        }
        
        // 只添加非空的字段
        if (Object.keys(headers).length > 0) {
          stepObj.request.headers = headers
        }
        if (Object.keys(params).length > 0) {
          stepObj.request.params = params
        }
        if (Object.keys(json).length > 0 && ['POST', 'PUT', 'PATCH'].includes(step.request.method)) {
          stepObj.request.json = json
        }
        if (step.validate && step.validate.length > 0) {
          stepObj.validate = step.validate
        }
        
        return stepObj
      })
    }
    
    // 序列化为格式化的JSON字符串
    editForm.value.script_content = JSON.stringify(httprunnerJson, null, 2)
    
  } catch (error) {
    console.error('序列化表单失败:', error)
    ElMessage.error(`表单数据转换失败: ${error.message}`)
  }
}

// 添加测试步骤
const addTestStep = () => {
  const newStep = {
    name: `新步骤 ${visualForm.value.teststeps.length + 1}`,
    request: {
      method: 'GET',
      url: '',
      headers: {},
      headersText: '{}',
      params: {},
      paramsText: '{}',
      json: {},
      jsonText: '{}'
    },
    assertions: [],  // 可视化断言数组
    validate: [],
    validateText: '[]',
    validateStatusCode: 200
  }
  
  visualForm.value.teststeps.push(newStep)
  
  // 展开新添加的步骤
  activeSteps.value = [visualForm.value.teststeps.length - 1]
  
  ElMessage.success('已添加新步骤')
}

// 添加断言
const addAssertion = (step) => {
  if (!step.assertions) {
    step.assertions = []
  }
  
  step.assertions.push({
    field: 'body.code',
    operator: 'eq',
    valueType: 'string',  // 默认类型为 string
    expected: '0'
  })

  // 同步更新validateText
  syncAssertionsToValidateText(step)
}

// 删除断言
const removeAssertion = (step, index) => {
  step.assertions.splice(index, 1)
  
  // 同步更新validateText
  syncAssertionsToValidateText(step)
}

// 将可视化断言同步到validateText和validate（JSON格式）
const syncAssertionsToValidateText = (step) => {
  if (!step.assertions || step.assertions.length === 0) {
    step.validate = []
    step.validateText = '[]'
    return
  }
  
  const validateRules = step.assertions
    .filter(assertion => assertion.field && assertion.field.trim() !== '')  // 过滤空字段
    .map(assertion => {
      const { field, operator, expected, valueType } = assertion
      
      // 根据 valueType 转换期望值
      const convertedValue = convertValueByType(expected, valueType || 'string')
      
      // 根据运算符构建断言规则
      switch (operator) {
        case 'eq':
        case 'ne':
        case 'gt':
        case 'gte':
        case 'lt':
        case 'lte':
          return [operator, [field, convertedValue]]
        case 'contains':
        case 'not_contains':
          // contains 和 not_contains 总是使用字符串
          return [operator, [field, String(expected)]]
        case 'exists':
        case 'not_exists':
          return [operator, [field]]
        case 'type_match':
          // type_match 期望值是类型名称（字符串）
          return ['type_match', [field, String(expected)]]
        default:
          return ['eq', [field, convertedValue]]
      }
    })
  
  // 同时更新 validate 数组和 validateText
  step.validate = validateRules
  step.validateText = JSON.stringify(validateRules, null, 2)
  
  // 实时同步到script_content，以便hasChanges能检测到变化
  if (activeTab.value === 'visual') {
    syncAllVisualToScript()
  }
}

/**
 * 根据指定类型转换值
 * @param {string} value - 原始值（字符串）
 * @param {string} type - 目标类型 (string, integer, number, boolean, null)
 * @returns {any} 转换后的值
 */
const convertValueByType = (value, type) => {
  if (!value && value !== 0 && value !== false && value !== '') {
    return null
  }
  
  switch (type) {
    case 'string':
      return String(value)
    
    case 'integer':
      const intValue = parseInt(value, 10)
      return isNaN(intValue) ? 0 : intValue
    
    case 'number':
      const numValue = parseFloat(value)
      return isNaN(numValue) ? 0 : numValue
    
    case 'boolean':
      if (typeof value === 'boolean') return value
      if (value === 'true' || value === '1' || value === 1) return true
      if (value === 'false' || value === '0' || value === 0) return false
      return Boolean(value)
    
    case 'null':
      return null
    
    default:
      return String(value)
  }
}

// 解析值（尝试自动转换为正确的类型 - 用于向后兼容）
const parseValue = (value) => {
  if (value === 'true') return true
  if (value === 'false') return false
  if (value === 'null') return null
  if (value === '') return ''
  
  // 尝试解析为数字
  const num = Number(value)
  if (!isNaN(num) && value.trim() !== '') {
    return num
  }
  
  return value
}

// 将JSON格式的validate解析为可视化断言
/**
 * 推断值的类型
 * @param {any} value - 值
 * @returns {string} 类型 (string, integer, number, boolean, null)
 */
const inferValueType = (value) => {
  if (value === null || value === undefined) {
    return 'null'
  }
  
  const valueType = typeof value
  
  if (valueType === 'boolean') {
    return 'boolean'
  }
  
  if (valueType === 'number') {
    // 判断是整数还是小数
    return Number.isInteger(value) ? 'integer' : 'number'
  }
  
  // 默认为字符串
  return 'string'
}

const parseValidateToAssertions = (validate) => {
  if (!validate || !Array.isArray(validate)) {
    return []
  }
  
  const assertions = []
  
  for (const rule of validate) {
    if (!Array.isArray(rule) || rule.length < 2) continue
    
    const operator = rule[0]
    const params = rule[1]
    
    if (!Array.isArray(params) || params.length === 0) continue
    
    // 跳过状态码断言（单独处理）
    if (params[0] === 'status_code') continue
    
    // 提取字段和期望值
    const field = params[0]
    const expectedValue = params.length > 1 ? params[1] : ''
    
    // 推断值的类型
    const valueType = inferValueType(expectedValue)
    
    const assertion = {
      field: field,
      operator: operator,
      valueType: valueType,  // 添加类型字段
      expected: String(expectedValue)  // UI 中总是用字符串显示
    }
    
    // 对于 exists 和 not_exists，不需要期望值
    if (operator === 'exists' || operator === 'not_exists') {
      assertion.expected = ''
      assertion.valueType = 'string'  // 默认类型
    }
    
    assertions.push(assertion)
  }
  
  return assertions
}

// 删除测试步骤
const removeTestStep = (index) => {
  visualForm.value.teststeps.splice(index, 1)
  ElMessage.success('已删除步骤')
}

// 获取请求方法的Tag类型
const getMethodTagType = (method) => {
  const typeMap = {
    'GET': 'success',
    'POST': 'primary',
    'PUT': 'warning',
    'DELETE': 'danger',
    'PATCH': 'info'
  }
  return typeMap[method] || 'info'
}

// 打开字段选择器
const openFieldSelector = (step, assertion) => {
  currentEditingStep.value = step
  currentEditingAssertion.value = assertion
  selectedFieldPath.value = assertion.field || ''
  
  // 构建响应字段列表
  buildResponseFields()
  
  fieldSelectorVisible.value = true
}

// ========== API 规范兼容性处理函数 ==========

/**
 * 提取响应内容（兼容 OpenAPI 3.0 和 Swagger 2.0）
 * @param {Object} response - 响应对象
 * @returns {Object} 响应内容对象
 * 
 * 支持的格式：
 * - OpenAPI 3.0: response.content['application/json'] 或 response.content['*\/*']
 * - Swagger 2.0: response（直接返回）
 */
const extractResponseContent = (response) => {
  // OpenAPI 3.0：响应内容在 content 字段中
  if (response.content && typeof response.content === 'object') {
    // 优先使用 application/json
    if (response.content['application/json']) {
      return response.content['application/json']
    }
    
    // 其次尝试其他常见的 JSON 媒体类型
    const jsonMediaTypes = [
      'application/vnd.api+json',
      'application/hal+json',
      'application/problem+json'
    ]
    
    for (const mediaType of jsonMediaTypes) {
      if (response.content[mediaType]) {
        return response.content[mediaType]
      }
    }
    
    // 最后尝试通配符
    if (response.content['*/*']) {
      return response.content['*/*']
    }
    
    // 如果都没有，返回第一个可用的媒体类型
    const firstMediaType = Object.keys(response.content)[0]
    if (firstMediaType) {
      return response.content[firstMediaType]
    }
  }
  
  // Swagger 2.0：响应内容直接在 response 对象中
  return response
}

/**
 * 提取 schema（兼容多种格式）
 * @param {Object} responseContent - 响应内容对象
 * @returns {Object|null} schema 对象
 * 
 * 支持的格式：
 * - responseContent.schema (标准格式)
 * - responseContent.content.schema (嵌套格式)
 */
const extractSchema = (responseContent) => {
  if (!responseContent) return null
  
  // 标准格式
  if (responseContent.schema) {
    return responseContent.schema
  }
  
  // 嵌套格式（某些非标准实现）
  if (responseContent.content && responseContent.content.schema) {
    return responseContent.content.schema
  }
  
  return null
}

/**
 * 提取 example（支持多种来源和优先级）
 * @param {Object} response - 原始响应对象
 * @param {Object} responseContent - 响应内容对象
 * @param {Object} schema - schema 对象
 * @returns {any} example 值
 * 
 * 优先级（从高到低）：
 * 1. response.generated_example（系统生成的示例）
 * 2. responseContent.example（直接定义的示例）
 * 3. responseContent.examples（OpenAPI 3.0 多示例，取第一个）
 * 4. schema.example（schema 中的示例）
 */
const extractExample = (response, responseContent, schema) => {
  // 1. 最高优先级：系统生成的示例（我们的自定义字段）
  if (response.generated_example !== undefined && response.generated_example !== null) {
    return response.generated_example
  }
  
  // 2. 直接定义的示例
  if (responseContent.example !== undefined && responseContent.example !== null) {
    return responseContent.example
  }
  
  // 3. OpenAPI 3.0 的多示例格式
  if (responseContent.examples && typeof responseContent.examples === 'object') {
    const exampleKeys = Object.keys(responseContent.examples)
    if (exampleKeys.length > 0) {
      const firstExample = responseContent.examples[exampleKeys[0]]
      
      // examples 可能是 { value: ... } 或直接是值
      if (firstExample && typeof firstExample === 'object' && 'value' in firstExample) {
        return firstExample.value
      }
      
      return firstExample
    }
  }
  
  // 4. schema 中的示例
  if (schema && schema.example !== undefined && schema.example !== null) {
    return schema.example
  }
  
  // 5. 没有找到任何示例
  return null
}

// 构建响应字段树形结构
const buildResponseFields = () => {
  // 获取当前测试用例关联的 API 端点信息
  const endpointInfo = props.testCase?.endpoint_info
  
  // 基础结构：status_code 和 headers
  const baseFields = [
    {
      label: 'status_code',
      path: 'status_code',
      type: 'integer',
      example: 200,
      isLeaf: true
    },
    {
      label: 'headers',
      path: 'headers',
      type: 'object',
      example: undefined,
      children: [
        {
          label: 'content-type',
          path: 'headers.content-type',
          type: 'string',
          example: 'application/json',
          isLeaf: true
        }
      ]
    }
  ]
  
  // 如果有 endpoint_info，从中获取响应结构
  if (endpointInfo && endpointInfo.responses) {
    try {
      // 优先使用 200 状态码的响应，其次 201
      const response200 = endpointInfo.responses['200'] || endpointInfo.responses['201']
      
      if (response200) {
        let bodySchema = null
        let bodyExample = null
        
        // ========== 兼容性处理：支持多种 API 规范格式 ==========
        
        // 1. 检测并提取响应内容（兼容 OpenAPI 3.0 和 Swagger 2.0）
        const responseContent = extractResponseContent(response200)
        
        // 2. 提取 schema（兼容多种格式）
        bodySchema = extractSchema(responseContent)
        
        // 3. 提取 example（优先级：generated_example > example > examples > schema.example）
        bodyExample = extractExample(response200, responseContent, bodySchema)
        
        // ========== 构建响应字段树 ==========
        
        if (bodySchema || bodyExample) {
          const bodyNode = buildResponseTree(bodySchema, bodyExample, 'body')
          if (bodyNode) {
            baseFields.push(bodyNode)
          } else {
            baseFields.push(getDefaultBodyStructure())
          }
        } else {
          // 如果既没有 schema 也没有 example，使用默认结构
          baseFields.push(getDefaultBodyStructure())
        }
      } else {
        // 没有找到 200/201 响应，使用默认结构
        baseFields.push(getDefaultBodyStructure())
      }
    } catch (error) {
      console.error('解析响应结构失败:', error)
      baseFields.push(getDefaultBodyStructure())
    }
  } else {
    // 没有 endpoint_info，使用默认结构
    baseFields.push(getDefaultBodyStructure())
  }
  
  responseTreeData.value = baseFields
}

// 根据 schema 和 example 构建响应树
const buildResponseTree = (schema, example, pathPrefix = '') => {
  if (!schema && !example) {
    return null
  }
  
  console.log('📊 buildResponseTree 调用:', {
    hasSchema: !!schema,
    hasExample: !!example,
    pathPrefix,
    exampleKeys: example && typeof example === 'object' ? Object.keys(example) : 'N/A',
    schemaType: schema?.type
  })
  
  // 如果有 example，优先使用 example 构建
  if (example && typeof example === 'object') {
    const tree = buildTreeFromExample(example, pathPrefix)
    console.log('✅ 从 example 构建树完成:', tree)
    return tree
  }
  
  // 否则使用 schema 构建
  if (schema) {
    const tree = buildTreeFromSchema(schema, pathPrefix)
    console.log('✅ 从 schema 构建树完成:', tree)
    return tree
  }
  
  return null
}

/**
 * 从 example 构建树（通用方法，适用于所有格式）
 * @param {any} example - 示例数据
 * @param {string} pathPrefix - 字段路径前缀
 * @returns {Object} 树节点
 * 
 * 说明：
 * - 支持对象、数组和基本类型
 * - 递归处理嵌套结构
 * - 与 API 规范格式无关，只依赖实际数据结构
 */
const buildTreeFromExample = (example, pathPrefix = '') => {
  const type = Array.isArray(example) ? 'array' : typeof example
  const label = pathPrefix.split('.').pop() || 'body'
  
  const node = {
    label,
    path: pathPrefix || 'body',
    type,
    example: type === 'object' || type === 'array' ? undefined : example
  }
  
  if (typeof example === 'object' && example !== null && !Array.isArray(example)) {
    // 对象：递归构建子节点
    const keys = Object.keys(example)
    
    node.children = keys.map(key => {
      const childPath = pathPrefix ? `${pathPrefix}.${key}` : key
      const childValue = example[key]
      
      if (typeof childValue === 'object' && childValue !== null && !Array.isArray(childValue)) {
        // 嵌套对象，递归处理
        return buildTreeFromExample(childValue, childPath)
      } else if (Array.isArray(childValue)) {
        // 数组
        const arrayNode = {
          label: key,
          path: childPath,
          type: 'array',
          example: undefined
        }
        if (childValue.length > 0) {
          arrayNode.children = [buildTreeFromExample(childValue[0], `${childPath}[0]`)]
        } else {
          arrayNode.isLeaf = true
        }
        return arrayNode
      } else {
        // 基本类型
        return {
          label: key,
          path: childPath,
          type: typeof childValue,
          example: childValue,
          isLeaf: true
        }
      }
    })
  } else if (Array.isArray(example) && example.length > 0) {
    // 数组：使用第一个元素构建子节点
    node.children = [buildTreeFromExample(example[0], `${pathPrefix}[0]`)]
  } else {
    node.isLeaf = true
  }
  
  return node
}

/**
 * 从 schema 构建树（兼容 OpenAPI 3.0 和 Swagger 2.0）
 * @param {Object} schema - JSON Schema 对象
 * @param {string} pathPrefix - 字段路径前缀
 * @returns {Object} 树节点
 * 
 * 兼容性说明：
 * - OpenAPI 3.0 和 Swagger 2.0 都使用 JSON Schema 标准
 * - 支持 type, properties, items, example, description 等标准字段
 * - 支持 allOf, oneOf, anyOf（通过 resolved 字段）
 * - 忽略 $ref（假设已被后端解析）
 */
const buildTreeFromSchema = (schema, pathPrefix = '') => {
  if (!schema) return null
  
  const label = pathPrefix.split('.').pop() || 'body'
  
  // 处理 resolved schema（已解析的引用）
  const resolvedSchema = schema.resolved || schema
  
  const node = {
    label,
    path: pathPrefix || 'body',
    type: resolvedSchema.type || 'object',
    example: resolvedSchema.example,
    description: resolvedSchema.description
  }
  
  // 对象类型：递归构建属性
  if ((resolvedSchema.type === 'object' || !resolvedSchema.type) && resolvedSchema.properties) {
    node.children = Object.keys(resolvedSchema.properties).map(key => {
      const prop = resolvedSchema.properties[key]
      const childPath = pathPrefix ? `${pathPrefix}.${key}` : key
      
      // 嵌套对象
      if (prop.type === 'object' || prop.properties) {
        return buildTreeFromSchema(prop, childPath)
      } 
      // 数组类型
      else if (prop.type === 'array') {
        const arrayNode = {
          label: key,
          path: childPath,
          type: 'array',
          example: prop.example,
          description: prop.description
        }
        
        if (prop.items) {
          // 如果 items 是对象或有 properties，递归构建
          if (prop.items.type === 'object' || prop.items.properties) {
            arrayNode.children = [buildTreeFromSchema(prop.items, `${childPath}[0]`)]
          } else {
            // 基本类型数组
            arrayNode.children = [{
              label: '[item]',
              path: `${childPath}[0]`,
              type: prop.items.type || 'string',
              example: prop.items.example,
              isLeaf: true
            }]
          }
        } else {
          arrayNode.isLeaf = true
        }
        
        return arrayNode
      } 
      // 基本类型
      else {
        return {
          label: key,
          path: childPath,
          type: prop.type || 'string',
          example: prop.example,
          description: prop.description,
          isLeaf: true
        }
      }
    })
  } 
  // 数组类型
  else if (resolvedSchema.type === 'array' && resolvedSchema.items) {
    if (resolvedSchema.items.type === 'object' || resolvedSchema.items.properties) {
      node.children = [buildTreeFromSchema(resolvedSchema.items, `${pathPrefix}[0]`)]
    } else {
      // 基本类型数组
      node.children = [{
        label: '[item]',
        path: `${pathPrefix}[0]`,
        type: resolvedSchema.items.type || 'string',
        example: resolvedSchema.items.example,
        isLeaf: true
      }]
    }
  } 
  // 基本类型（叶子节点）
  else {
    node.isLeaf = true
  }
  
  return node
}

// 获取默认的 body 结构（当无法从 API 获取时使用）
const getDefaultBodyStructure = () => {
  return {
    label: 'body',
    path: 'body',
    type: 'object',
    example: undefined,
    children: [
      {
        label: 'code',
        path: 'body.code',
        type: 'string/integer',
        example: '0 或 0',
        isLeaf: true
      },
      {
        label: 'msg',
        path: 'body.msg',
        type: 'string',
        example: '操作成功',
        isLeaf: true
      },
      {
        label: 'data',
        path: 'body.data',
        type: 'object/array',
        example: undefined,
        children: [
          {
            label: 'id',
            path: 'body.data.id',
            type: 'integer',
            example: 1,
            isLeaf: true
          }
        ]
      }
    ]
  }
}

// 原来的硬编码字段（备份，以防需要）
const getOldDefaultBodyStructure = () => {
  return {
    label: 'body',
    path: 'body',
    type: 'object',
    example: undefined,
    children: [
      {
        label: 'code',
        path: 'body.code',
        type: 'string/integer',
        example: '0 或 0',
        isLeaf: true
      },
      {
        label: 'msg',
        path: 'body.msg',
        type: 'string',
        example: '操作成功',
        isLeaf: true
      },
      {
        label: 'data',
        path: 'body.data',
        type: 'object/array',
        example: undefined,
        children: [
          {
            label: 'id',
            path: 'body.data.id',
            type: 'integer',
            example: 1,
            isLeaf: true
          }
        ]
      }
    ]
  }
}


// 处理树节点点击
const handleTreeNodeClick = (data, node) => {
  // 只有叶子节点才能选择（或者所有节点都可以选择）
  if (data.path) {
    selectedFieldPath.value = data.path
    
    // 设置当前选中节点
    if (fieldTree.value) {
      fieldTree.value.setCurrentKey(data.path)
    }
  }
}

// 获取类型标签的样式
const getTypeTagType = (type) => {
  if (!type) return 'info'
  
  const typeMap = {
    'string': 'success',
    'integer': 'warning',
    'number': 'warning',
    'boolean': 'danger',
    'object': 'primary',
    'array': 'primary',
    'object/array': 'primary'
  }
  
  // 检查是否包含某个类型
  for (const [key, value] of Object.entries(typeMap)) {
    if (type.toLowerCase().includes(key)) {
      return value
    }
  }
  
  return 'info'
}

// 格式化示例值
const formatExampleValue = (example) => {
  if (example === undefined || example === null) return ''
  if (typeof example === 'object') {
    return JSON.stringify(example)
  }
  return String(example)
}

// 确认字段选择
const confirmFieldSelection = () => {
  if (currentEditingAssertion.value && selectedFieldPath.value) {
    currentEditingAssertion.value.field = selectedFieldPath.value
    
    // 同步更新validateText
    if (currentEditingStep.value) {
      syncAssertionsToValidateText(currentEditingStep.value)
    }
    
    fieldSelectorVisible.value = false
    ElMessage.success('字段已选择')
  }
}

// 工具方法
const getMethodClass = (method) => {
  const methodClasses = {
    'GET': 'method-get',
    'POST': 'method-post',
    'PUT': 'method-put',
    'DELETE': 'method-delete',
    'PATCH': 'method-patch'
  }
  return methodClasses[method] || 'method-default'
}

const getTestCaseTypeLabel = (type) => {
  const typeLabels = {
    'endpoint': '端点测试',
    'scenario': '场景测试'
  }
  return typeLabels[type] || type
}

const getTestCaseTypeTag = (type) => {
  const typeTags = {
    'endpoint': 'primary',
    'scenario': 'success'
  }
  return typeTags[type] || 'info'
}

const getTestTypeLabel = (type) => {
  const typeLabels = {
    'positive': '正向测试',
    'negative': '负向测试',
    'boundary': '边界测试',
    'security': '安全测试'
  }
  return typeLabels[type] || type
}

const getTestTypeTag = (type) => {
  const typeTags = {
    'positive': 'success',
    'negative': 'danger',
    'boundary': 'warning',
    'security': 'warning'
  }
  return typeTags[type] || 'info'
}

const getPriorityLabel = (priority) => {
  const priorityLabels = {
    'low': '低',
    'medium': '中',
    'high': '高',
    'critical': '紧急'
  }
  return priorityLabels[priority] || priority
}

const getPriorityTag = (priority) => {
  const priorityTags = {
    'low': 'info',
    'medium': 'warning',
    'high': 'danger',
    'critical': 'danger'
  }
  return priorityTags[priority] || 'info'
}

const formatDate = (dateStr) => {
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

const formatJson = (jsonStr) => {
  try {
    return JSON.stringify(JSON.parse(jsonStr), null, 2)
  } catch {
    return jsonStr || '{}'
  }
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
})
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
  font-size: 22px;
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
  display: flex;
  gap: 12px;
  align-items: center;
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

.edit-btn {
  background: rgba(103, 194, 58, 0.15);
  border: 1px solid rgba(103, 194, 58, 0.3);
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 600;
  font-size: 14px;
  color: #ffffff;
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 12px rgba(103, 194, 58, 0.15);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.edit-btn:hover {
  background: rgba(103, 194, 58, 0.25);
  border-color: rgba(103, 194, 58, 0.5);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(103, 194, 58, 0.2);
}

.edit-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(103, 194, 58, 0.15);
}

.edit-btn .el-icon {
  margin-right: 6px;
  font-size: 16px;
}

.cancel-btn {
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
}

.cancel-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.4);
  color: #ffffff;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.cancel-btn:active {
  transform: translateY(0);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
}

.cancel-btn .el-icon {
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
    flex-wrap: wrap;
  }
  
  .save-btn,
  .edit-btn,
  .cancel-btn,
  .close-btn {
    flex: 1;
    max-width: 200px;
    font-size: 13px;
    padding: 10px 20px;
  }
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

/* 紧凑JSON内容样式 */
.json-content-compact {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  line-height: 1.3;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
}

/* 紧凑脚本内容样式 */
.script-content-compact {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  line-height: 1.3;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid #e4e7ed;
  max-height: 300px;
  overflow-y: auto;
}

/* MonacoEditor 样式 */
.monaco-editor-container {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  overflow: hidden;
}

.no-script {
  text-align: center;
  padding: 40px 0;
  color: #909399;
}


/* 方法标签样式 */
.method-get {
  background-color: #67c23a;
}

.method-post {
  background-color: #409eff;
}

.method-put {
  background-color: #e6a23c;
}

.method-delete {
  background-color: #f56c6c;
}

.method-patch {
  background-color: #909399;
}

.method-default {
  background-color: #909399;
}

/* 编辑状态样式 */
.title-input {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
}

.title-input .el-input__inner {
  color: white;
  font-size: 20px;
  font-weight: 600;
}

.title-input .el-input__inner::placeholder {
  color: rgba(255, 255, 255, 0.7);
}

.description-input {
  width: 100%;
}


/* 脚本编辑样式 */
.script-edit {
  margin-top: 8px;
}

/* Tab样式 */
.script-tabs {
  margin-top: 8px;
}

.script-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

.script-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
  padding: 0 24px;
  height: 40px;
  line-height: 40px;
}

/* 可视化编辑器样式 */
.visual-edit {
  min-height: 500px;
  padding: 8px 0;
}

.visual-editor-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 卡片样式 */
.config-card,
.steps-card {
  border-radius: 8px;
  transition: all 0.3s ease;
}

.config-card:hover,
.steps-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.card-title .el-icon {
  font-size: 18px;
  color: #409eff;
}

/* 测试步骤样式 */
.step-item {
  margin-bottom: 12px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  transition: all 0.3s ease;
}

.step-item:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.1);
}

.step-title {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.step-name {
  flex: 1;
  font-weight: 500;
  color: #303133;
  font-size: 14px;
}

.delete-step-btn {
  margin-left: auto;
  color: #f56c6c;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.step-item:hover .delete-step-btn {
  opacity: 1;
}

.delete-step-btn:hover {
  color: #f56c6c;
  background: rgba(245, 108, 108, 0.1);
}

/* 表单区域内的分割线样式 */
.el-divider {
  margin: 16px 0;
}

.el-divider__text {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  background-color: #fff;
  padding: 0 16px;
}

/* 空状态样式 */
.visual-edit :deep(.el-empty) {
  padding: 40px 0;
}

.visual-edit :deep(.el-empty__description) {
  color: #909399;
  font-size: 14px;
}

/* 表单项优化 */
.visual-edit :deep(.el-form-item__label) {
  font-weight: 500;
  color: #606266;
}

.visual-edit :deep(.el-textarea__inner) {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
}

/* Collapse展开面板优化 */
.visual-edit :deep(.el-collapse) {
  border-top: none;
  border-bottom: none;
}

.visual-edit :deep(.el-collapse-item__header) {
  height: auto;
  line-height: 1.5;
  padding: 16px 20px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  margin-bottom: 8px;
  transition: all 0.3s ease;
}

.visual-edit :deep(.el-collapse-item__header:hover) {
  background: #f0f9ff;
  border-color: #409eff;
}

.visual-edit :deep(.el-collapse-item__wrap) {
  border: none;
  background: #fff;
}

.visual-edit :deep(.el-collapse-item__content) {
  padding: 20px 16px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  margin-bottom: 12px;
}

/* 请求方法标签样式 */
.visual-edit :deep(.el-tag) {
  font-weight: 600;
  font-family: monospace;
  padding: 0 10px;
  height: 24px;
  line-height: 22px;
}

/* 断言构建器样式 */
.assertions-builder {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}

.assertion-item {
  background: #ffffff;
  padding: 12px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  transition: all 0.3s ease;
}

.assertion-item:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.1);
}

.assertion-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.assertion-row .el-input,
.assertion-row .el-select {
  flex-shrink: 0;
}

.assertion-row .el-button {
  margin-left: auto;
}

.assertions-builder > .el-button {
  align-self: flex-start;
}

/* 断言项动画 */
.assertion-item {
  animation: slideIn 0.3s ease;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(-10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 字段选择器对话框样式 */
.field-selector-dialog {
  max-height: 600px;
  overflow-y: auto;
}

.response-structure h4 {
  margin: 0 0 12px 0;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

/* 树形组件样式 */
.response-tree {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 12px;
}

.response-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 40px;
  padding: 8px 0;
  border-radius: 4px;
  transition: all 0.3s ease;
}

.response-tree :deep(.el-tree-node__content:hover) {
  background: #e6f7ff;
}

.response-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: #d9ecff;
  border: 1px solid #409eff;
}

.tree-node-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 8px;
}

.node-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.node-label {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.node-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #909399;
}

.node-path {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  color: #606266;
  background: #f0f0f0;
  padding: 2px 6px;
  border-radius: 3px;
  display: inline-block;
  max-width: fit-content;
}

.node-example {
  font-size: 11px;
  color: #909399;
}

.node-example code {
  background: #ffffff;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  color: #67c23a;
}

/* 树形节点图标优化 */
.response-tree :deep(.el-tree-node__expand-icon) {
  font-size: 14px;
  color: #409eff;
}

.response-tree :deep(.el-tree-node__expand-icon.is-leaf) {
  color: transparent;
}

.field-selector .el-input {
  cursor: pointer;
}

.field-selector :deep(.el-input__inner) {
  cursor: text;
}

/* 导入用例弹窗 - 多条件过滤栏 */
.import-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.import-filter-total {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
</style>

<style>
/* 针对 teleport 到 body 的 el-drawer 强制覆盖样式，不能使用 scoped */
.api-case-edit-drawer {
  display: flex !important;
  flex-direction: column !important;
  height: 100% !important;
  overflow: hidden !important;
}

.api-case-edit-drawer .el-drawer__header {
  flex-shrink: 0 !important;
  margin-bottom: 0 !important;
  padding-bottom: 20px !important;
  border-bottom: 1px solid #ebeef5; /* 加个下划线更美观 */
}

/* 强制开启 body 区域的独立滚动 */
.api-case-edit-drawer-body {
  flex: 1 !important;
  min-height: 0 !important;
  overflow-y: auto !important;
  padding: 20px !important;
}
</style>
