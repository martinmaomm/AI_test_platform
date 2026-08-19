<template>
  <div class="test-suites-container" v-if="selectedProject">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Collection />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>测试套件管理</h2>
            <p>管理和执行测试用例套件，提高测试效率</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="showCreateDialog = true" :disabled="!selectedProject" class="create-btn">
            新建套件
          </el-button>
        </div>
      </div>
    </div>

    <!-- 测试套件列表 -->
    <el-card class="test-suites-card">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedSuites.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedSuites.length }} 个套件</span>
        </div>
        <div class="batch-buttons">
          <el-button @click="batchDelete" type="danger">
            <el-icon>
              <Delete />
            </el-icon>
            批量删除
          </el-button>
          <el-button @click="clearSelection">
            <el-icon>
              <Close />
            </el-icon>
            取消选择
          </el-button>
        </div>
      </div>

      <!-- 原始card-header - 当没有选中项时显示 -->
      <div v-else class="card-header">
        <div class="card-header-left">
          <h3>套件列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select 
              v-model="statusFilter" 
              placeholder="状态筛选" 
              clearable 
              style="width: 120px;" 
              @change="handleStatusFilter"
            >
              <el-option label="全部状态" value="" />
              <el-option label="激活" value="active" />
              <el-option label="停用" value="inactive" />
              <el-option label="已归档" value="archived" />
            </el-select>

            <el-select 
              v-model="tagFilter" 
              placeholder="标签筛选" 
              clearable 
              style="width: 120px;" 
              @change="handleTagFilter"
            >
              <el-option label="全部标签" value="" />
              <el-option label="功能测试" value="功能测试" />
              <el-option label="回归测试" value="回归测试" />
              <el-option label="冒烟测试" value="冒烟测试" />
              <el-option label="集成测试" value="集成测试" />
              <el-option label="端到端测试" value="端到端测试" />
            </el-select>

            <el-input
              v-model="searchKeyword"
              placeholder="搜索测试套件..."
              style="width: 200px;"
              clearable
              @input="handleSearch"
            >
              <template #prefix>
                <el-icon>
                  <Search />
                </el-icon>
              </template>
            </el-input>
          </div>
        </div>
      </div>

      <!-- 表格布局 -->
      <div class="table-container">
        <el-table 
          :data="testSuites" 
          style="width: 100%; height: 100%" 
          v-loading="loading"
          @selection-change="handleSelectionChange"
          :row-class-name="getRowClassName"
        >
          <el-table-column type="selection" width="40" />
          
          <el-table-column prop="id" label="ID" width="70" align="center">
            <template #default="scope">
              <span class="test-suite-id">{{ scope.row.id }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="name" label="套件名称" min-width="300">
            <template #default="scope">
              <div class="test-suite-name-simple">
                <div class="test-suite-title" @click="editTestSuite(scope.row)">
                  {{ scope.row.name }}
                </div>
                <div class="test-suite-desc" v-if="scope.row.description">
                  {{ scope.row.description }}
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="status" label="状态" width="100" align="center">
            <template #default="scope">
              <el-tag :type="getStatusType(scope.row.status)" size="small">
                {{ getStatusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="test_cases_count" label="用例数量" width="120" align="center">
            <template #default="scope">
              <div class="cases-count">
                <span class="total-cases">{{ scope.row.test_cases_count }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="tags" label="标签" width="120">
            <template #default="scope">
              <div class="tags-cell">
                <el-tag 
                  v-for="tag in scope.row.tags.slice(0, 2)" 
                  :key="tag" 
                  size="small" 
                  type="info"
                >
                  {{ tag }}
                </el-tag>
                <el-tag v-if="scope.row.tags.length > 2" size="small" type="info">
                  +{{ scope.row.tags.length - 2 }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="created_at" label="创建时间" width="180" align="center">
            <template #default="scope">
              <span class="created-time">{{ formatDateTime(scope.row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="280" fixed="right">
            <template #default="scope">
              <div class="action-buttons">
                <el-button type="" size="small" @click="editTestSuite(scope.row)">
                  <el-icon>
                    <Edit />
                  </el-icon>
                  编辑
                </el-button>
                <el-button 
                  type="primary" 
                  size="small" 
                  @click="executeTestSuite(scope.row)"
                  :disabled="scope.row.test_cases_count === 0 || executingSuites.has(scope.row.id)"
                  :loading="executingSuites.has(scope.row.id)"
                  class="execute-button"
                >
                  <el-icon v-if="!executingSuites.has(scope.row.id)"><VideoPlay /></el-icon>
                  {{ scope.row.test_cases_count === 0 ? '无用例' : (executingSuites.has(scope.row.id) ? '执行中...' : '执行套件') }}
                </el-button>
                <el-button 
                  type="success" 
                  size="small" 
                  @click="createScheduledTask(scope.row)"
                  :disabled="scope.row.test_cases_count === 0"
                >
                  <el-icon>
                    <Clock />
                  </el-icon>
                  定时任务
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 分页区域 -->
      <div class="bottom-actions-container">
        <!-- 分页 -->
        <div class="pagination-container">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="totalCount"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="handleSizeChange"
            @current-change="handleCurrentChange"
          />
        </div>
      </div>
    </el-card>

  </div>
  <!-- 项目选择提示 -->
  <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon style="margin-bottom: 20px;">
    <template #default>
      <div>
        <p>您还没有选择当前工作项目，请前往项目管理页面选择项目。</p>
        <el-button type="primary" size="small" @click="goToProjects" style="margin-top: 10px;">
          前往项目管理
        </el-button>
      </div>
    </template>
  </el-alert>

  <!-- 创建/编辑测试套件对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingSuite ? '编辑测试套件' : '新建测试套件'"
      width="900px"
      :close-on-click-modal="false"
      class="suite-dialog"
    >
      <el-tabs v-model="activeTab" type="card" class="dialog-tabs">
        <!-- 基本信息标签页 -->
        <el-tab-pane label="基本信息" name="basic">
          <el-form :model="suiteForm" :rules="suiteRules" ref="suiteFormRef" label-width="100px">
            <el-form-item label="套件名称" prop="name">
              <el-input 
                v-model="suiteForm.name" 
                placeholder="请输入测试套件名称" 
                maxlength="200"
                show-word-limit
              />
            </el-form-item>
            <el-form-item label="套件描述" prop="description">
              <el-input
                v-model="suiteForm.description"
                type="textarea"
                :rows="3"
                placeholder="请输入测试套件描述"
                maxlength="500"
                show-word-limit
              />
            </el-form-item>
            <el-form-item label="状态" prop="status">
              <el-select v-model="suiteForm.status" placeholder="请选择状态" style="width: 100%">
                <el-option label="激活" value="active" />
                <el-option label="停用" value="inactive" />
                <el-option label="已归档" value="archived" />
              </el-select>
            </el-form-item>
            <el-form-item label="标签" prop="tags">
              <el-select
                v-model="suiteForm.tags"
                multiple
                filterable
                allow-create
                placeholder="请选择或输入标签"
                style="width: 100%"
              >
                <el-option label="功能测试" value="功能测试" />
                <el-option label="回归测试" value="回归测试" />
                <el-option label="冒烟测试" value="冒烟测试" />
                <el-option label="集成测试" value="集成测试" />
                <el-option label="端到端测试" value="端到端测试" />
                <el-option label="性能测试" value="性能测试" />
                <el-option label="安全测试" value="安全测试" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        
        <!-- 测试用例管理标签页 -->
        <el-tab-pane label="测试用例管理" name="testcases" v-if="editingSuite">
          <div class="test-case-management">
            <div class="management-header">
              <div class="header-left">
                <h4>当前测试用例 ({{ editingSuite.test_cases?.length || 0 }})</h4>
                <p class="header-desc">管理套件中的测试用例</p>
              </div>
              <div class="header-right">
                <el-button type="primary" @click="openAddTestCaseDialog">
                  <el-icon><Plus /></el-icon>
                  添加测试用例
                </el-button>
                <el-button @click="clearAllTestCases" :disabled="!editingSuite.test_cases?.length">
                  <el-icon><Delete /></el-icon>
                  清空所有
                </el-button>
              </div>
            </div>
            
            <div v-if="editingSuite.test_cases?.length" class="test-cases-table">
              <el-table :data="editingSuite.test_cases" style="width: 100%" max-height="400">
                <el-table-column prop="id" label="ID" width="80" align="center" />
                <el-table-column prop="title" label="用例名称" min-width="200" show-overflow-tooltip />
                <el-table-column prop="priority" label="优先级" width="100" align="center">
                  <template #default="scope">
                    <el-tag :type="getPriorityType(scope.row.priority)" size="small">
                      {{ getPriorityText(scope.row.priority) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="category" label="类别" width="120" align="center">
                  <template #default="scope">
                    <el-tag type="info" size="small">
                      {{ getCategoryText(scope.row.category) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="120" align="center">
                  <template #default="scope">
                    <el-button
                      type="danger"
                      size="small"
                      @click="removeTestCaseFromSuite(scope.row)"
                    >
                      移除
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            
            <el-empty v-else description="暂无测试用例" />
          </div>
        </el-tab-pane>
      </el-tabs>
      
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showCreateDialog = false">取消</el-button>
          <el-button type="primary" @click="saveTestSuite" :loading="saving">
            {{ editingSuite ? '更新' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 执行测试套件对话框 -->
    <el-dialog 
      v-model="showExecuteDialog" 
      title="执行测试套件" 
      width="600px" 
      :close-on-click-modal="false"
      :modal="true"
      :append-to-body="true"
      class="playwright-config-dialog"
    >
      <div v-if="selectedSuite" class="config-form">
        <div class="config-section">
          <h4>测试套件信息</h4>
          <div class="test-case-info">
            <p><strong>套件名称：</strong>{{ selectedSuite.name }}</p>
            <p v-if="selectedSuite.description"><strong>套件描述：</strong>{{ selectedSuite.description }}</p>
            <p><strong>用例数量：</strong>{{ selectedSuite.test_cases_count }} 个</p>
            <p><strong>激活用例：</strong>{{ selectedSuite.active_test_cases_count }} 个</p>
          </div>
        </div>

        <div class="config-section">
          <h4>测试环境</h4>
          <el-form :model="executeForm" label-width="120px">
            <el-form-item label="选择环境" required>
              <el-select 
                v-model="selectedEnvironment" 
                placeholder="请选择测试环境" 
                style="width: 100%"
                :loading="loadingEnvironments"
                value-key="id"
              >
                <el-option
                  v-for="env in environments"
                  :key="env.id"
                  :label="env.name"
                  :value="env"
                >
                  <div class="environment-option">
                    <div class="environment-header">
                      <div class="environment-name-inline">{{ env.name }}</div>
                      <div class="environment-url-inline" v-if="env.config?.base_url">{{ env.config.base_url }}</div>
                    </div>
                  </div>
                </el-option>
                <!-- 当没有环境时显示提示信息 -->
                <el-option
                  v-if="environments.length === 0 && !loadingEnvironments"
                  :value="null"
                  disabled
                  class="no-environments-option"
                >
                  <div class="no-environments-content">
                    <el-icon class="warning-icon"><Warning /></el-icon>
                    <div class="no-environments-text">
                      <div class="no-environments-title">暂无WebUI测试环境</div>
                      <div class="no-environments-desc">请先在项目管理中创建WebUI测试环境</div>
                    </div>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
        </div>

        <div class="config-section">
          <h4>浏览器配置</h4>
          <el-form :model="executeForm" label-width="120px">
            <el-form-item label="浏览器类型">
              <el-select v-model="executeForm.browser" style="width: 200px">
                <el-option label="Chromium" value="chromium" />
                <el-option label="Firefox" value="firefox" />
                <el-option label="WebKit" value="webkit" />
              </el-select>
            </el-form-item>

            <el-form-item label="显示模式">
              <el-radio-group v-model="executeForm.headed">
                <el-radio :label="true">有头模式（显示浏览器）</el-radio>
                <el-radio :label="false">无头模式（后台运行）</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="超时时间（秒）">
              <el-input-number v-model="executeForm.timeout" :min="30" :max="1800" :step="30"
                style="width: 200px" />
            </el-form-item>

          </el-form>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showExecuteDialog = false">取消</el-button>
          <el-button type="primary" @click="confirmExecute" :loading="executing" :disabled="!selectedEnvironment">
            {{ executing ? '执行中...' : '确认执行' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 添加测试用例对话框 -->
    <el-dialog
      v-model="showAddTestCaseDialog"
      title="添加测试用例到套件"
      width="1000px"
      :close-on-click-modal="false"
      class="add-testcase-dialog"
    >
      <div class="add-test-case-content">
        <div class="search-section">
          <div class="search-header">
            <h4>选择测试用例</h4>
            <div class="search-stats">
              <span>共找到 {{ availableTestCases.length }} 个可用测试用例</span>
            </div>
          </div>
          
          <div class="search-filters">
            <el-input
              v-model="testCaseSearchKeyword"
              placeholder="搜索测试用例名称或描述..."
              style="width: 300px;"
              clearable
              @input="handleTestCaseSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            
            <el-select 
              v-model="testCasePriorityFilter" 
              placeholder="优先级" 
              clearable 
              style="width: 120px;" 
              @change="handleTestCaseSearch"
            >
              <el-option label="全部" value="" />
              <el-option label="高" value="high" />
              <el-option label="中" value="medium" />
              <el-option label="低" value="low" />
            </el-select>
            
            <el-select 
              v-model="testCaseCategoryFilter" 
              placeholder="类别" 
              clearable 
              style="width: 120px;" 
              @change="handleTestCaseSearch"
            >
              <el-option label="全部" value="" />
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
        
        <div class="test-cases-section">
          <el-table
            :data="availableTestCases"
            style="width: 100%"
            max-height="400"
            v-loading="loadingTestCases"
            @selection-change="handleTestCaseSelectionChange"
          >
            <el-table-column type="selection" width="55" />
            <el-table-column prop="id" label="ID" width="80" align="center" />
            <el-table-column prop="title" label="用例名称" min-width="200" show-overflow-tooltip />
            <el-table-column prop="priority" label="优先级" width="100" align="center">
              <template #default="scope">
                <el-tag :type="getPriorityType(scope.row.priority)" size="small">
                  {{ getPriorityText(scope.row.priority) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="类别" width="120" align="center">
              <template #default="scope">
                <el-tag type="info" size="small">
                  {{ getCategoryText(scope.row.category) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
          </el-table>
        </div>
      </div>
      
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showAddTestCaseDialog = false">取消</el-button>
          <el-button 
            type="primary" 
            @click="confirmAddTestCases" 
            :loading="addingTestCases" 
            :disabled="selectedTestCases.length === 0"
          >
            添加选中的测试用例 ({{ selectedTestCases.length }})
          </el-button>
        </div>
      </template>
    </el-dialog>

  <!-- 隐藏跳转按钮：供通知中的「点击前往」触发路由跳转 -->
  <button id="hidden-nav-btn" style="display: none;" @click="router.push('/web-testing/test-executions')"></button>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { Search, Plus, VideoPlay, Edit, Delete, Collection, List, Refresh, Clock, Document, Check, MoreFilled, CopyDocument, Close, Warning } from '@element-plus/icons-vue'
import * as webTestingApi from '@/api/webTesting'
import { getProjectEnvironments } from '@/api/projects'
import { getTaskStatus } from '@/api/webTesting'

// 路由实例
const router = useRouter()

const projectStore = useProjectStore()
const authStore = useAuthStore()

// 响应式数据
const loading = ref(false)
const saving = ref(false)
const executing = ref(false)
const testSuites = ref([])
const searchKeyword = ref('')
const statusFilter = ref('')
const tagFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
const selectedSuites = ref([])

// 对话框状态
const showCreateDialog = ref(false)
const showExecuteDialog = ref(false)
const showAddTestCaseDialog = ref(false)
const editingSuite = ref(null)
const selectedSuite = ref(null)
const activeTab = ref('basic')

// 添加测试用例相关数据
const availableTestCases = ref([])
const selectedTestCases = ref([])
const loadingTestCases = ref(false)
const addingTestCases = ref(false)
const testCaseSearchKeyword = ref('')
const testCasePriorityFilter = ref('')
const testCaseCategoryFilter = ref('')

// 表单数据
const suiteForm = ref({
  name: '',
  description: '',
  status: 'active',
  tags: []
})

const executeForm = ref({
  browser: 'chromium',
  headed: true,
  timeout: 300
})

// 环境相关状态
const environments = ref([])
const selectedEnvironment = ref(null)
const loadingEnvironments = ref(false)

// 任务轮询相关状态
const pollingTasks = ref(new Map()) // 存储正在轮询的任务信息
const pollingIntervals = ref(new Map()) // 存储轮询定时器

// 执行状态跟踪
const executingSuites = ref(new Set()) // 存储正在执行的测试套件ID

// 模板引用
const suiteFormRef = ref(null)
const executeFormRef = ref(null)

// 表单验证规则
const suiteRules = {
  name: [
    { required: true, message: '请输入测试套件名称', trigger: 'blur' },
    { min: 2, max: 200, message: '长度在 2 到 200 个字符', trigger: 'blur' }
  ],
  status: [
    { required: true, message: '请选择状态', trigger: 'change' }
  ]
}


// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 统计信息
const activeSuitesCount = computed(() => {
  return testSuites.value.filter(suite => suite.status === 'active').length
})

const totalTestCasesCount = computed(() => {
  return testSuites.value.reduce((total, suite) => total + suite.test_cases_count, 0)
})

const hasActiveFilters = computed(() => {
  return searchKeyword.value || statusFilter.value || tagFilter.value
})

// 方法
const loadTestSuites = async () => {
  if (!selectedProject.value) return

  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      project_id: selectedProject.value.id
    }
    
    // 添加搜索参数
    if (searchKeyword.value) {
      params.search = searchKeyword.value
    }
    
    // 添加状态过滤参数
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    
    // 添加标签过滤参数
    if (tagFilter.value) {
      params.tags = tagFilter.value
    }
    
    const response = await webTestingApi.getTestSuites(selectedProject.value.id, params)
    testSuites.value = response.data.items || []
    totalCount.value = response.data.pagination?.total || 0
  } catch (error) {
    console.error('加载测试套件失败:', error)
    ElMessage.error('加载测试套件失败')
  } finally {
    loading.value = false
  }
}

const loadEnvironments = async () => {
  if (!selectedProject.value) return

  try {
    loadingEnvironments.value = true
    
    const params = {
      category: 'web'  // 只获取WebUI测试环境
    }
    
    const response = await getProjectEnvironments(selectedProject.value.id, params)
    
    if (response.success) {
      // 根据实际返回的数据结构处理，只显示启用的环境
      const allEnvironments = response.data.items || []
      const filteredEnvironments = allEnvironments.filter(env => env.is_active === true)
      
      // 如果之前已经选择了环境，尝试根据ID重新匹配
      if (selectedEnvironment.value && selectedEnvironment.value.id) {
        const matchedEnv = filteredEnvironments.find(env => env.id === selectedEnvironment.value.id)
        if (matchedEnv) {
          selectedEnvironment.value = matchedEnv
        } else {
          // 如果找不到匹配的环境，清空选择
          selectedEnvironment.value = null
        }
      }
      
      environments.value = filteredEnvironments
      
      // 如果有环境且没有选中环境，默认选择第一个
      if (environments.value.length > 0 && !selectedEnvironment.value) {
        selectedEnvironment.value = environments.value[0]
      }
    } else {
      console.warn('加载环境列表失败:', response.message)
      environments.value = []
      selectedEnvironment.value = null
    }
  } catch (error) {
    console.error('加载环境列表失败:', error)
    environments.value = []
  } finally {
    loadingEnvironments.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadTestSuites()
}

const handleStatusFilter = () => {
  currentPage.value = 1
  loadTestSuites()
}

const handleTagFilter = () => {
  currentPage.value = 1
  loadTestSuites()
}

const resetFilters = () => {
  searchKeyword.value = ''
  statusFilter.value = ''
  tagFilter.value = ''
  currentPage.value = 1
  loadTestSuites()
}

const handleSelectionChange = (selection) => {
  selectedSuites.value = selection
}

const getRowClassName = ({ row }) => {
  if (row.status === 'archived') {
    return 'archived-row'
  }
  return ''
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  loadTestSuites()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadTestSuites()
}

const getStatusType = (status) => {
  const statusMap = {
    active: 'success',
    inactive: 'info',
    archived: 'warning'
  }
  return statusMap[status] || 'info'
}

const getStatusText = (status) => {
  const statusMap = {
    active: '激活',
    inactive: '停用',
    archived: '已归档'
  }
  return statusMap[status] || '未知'
}

const getPriorityType = (priority) => {
  const priorityMap = {
    low: 'info',
    medium: 'warning',
    high: 'danger'
  }
  return priorityMap[priority] || 'info'
}

const getPriorityText = (priority) => {
  const priorityMap = {
    low: '低',
    medium: '中',
    high: '高'
  }
  return priorityMap[priority] || '未知'
}

const formatDateTime = (dateTime) => {
  if (!dateTime) return '-'
  return new Date(dateTime).toLocaleString('zh-CN')
}

const getCategoryText = (category) => {
  const categoryMap = {
    functional: '功能测试',
    negative: '异常测试',
    boundary: '边界测试',
    security: '安全测试',
    performance: '性能测试',
    ui: '界面测试',
    integration: '集成测试'
  }
  return categoryMap[category] || '未知'
}

// 加载可用的测试用例
const loadAvailableTestCases = async () => {
  if (!selectedProject.value) {
    console.warn('没有选择项目，无法加载测试用例')
    return
  }

  console.log('开始加载测试用例，项目ID:', selectedProject.value.id)
  loadingTestCases.value = true
  
  try {
    const params = {
      project_id: selectedProject.value.id,
      page: 1,
      page_size: 1000 // 加载所有测试用例
    }
    
    // 添加搜索和过滤参数
    if (testCaseSearchKeyword.value) {
      params.search = testCaseSearchKeyword.value
    }
    if (testCasePriorityFilter.value) {
      params.priority = testCasePriorityFilter.value
    }
    if (testCaseCategoryFilter.value) {
      params.category = testCaseCategoryFilter.value
    }
    
    console.log('调用API参数:', params)
    const response = await webTestingApi.getWebUITestCases(selectedProject.value.id, params)
    console.log('API响应:', response)
    console.log('API响应data字段:', response.data)
    
    // 过滤掉已经在套件中的测试用例
    const suiteTestCaseIds = editingSuite.value?.test_cases?.map(tc => tc.id) || []
    console.log('套件中已有的测试用例ID:', suiteTestCaseIds)
    
    // 根据新的分页结构获取数据
    let testCases = []
    if (response.data.items && Array.isArray(response.data.items)) {
      testCases = response.data.items
    } else if (response.data.data && Array.isArray(response.data.data)) {
      testCases = response.data.data
    } else if (response.data.results && Array.isArray(response.data.results)) {
      testCases = response.data.results
    } else if (Array.isArray(response.data)) {
      testCases = response.data
    } else {
      // 如果都不是数组，尝试查找可能的数组字段
      console.log('response.data的所有属性:', Object.keys(response.data))
      for (const key in response.data) {
        if (Array.isArray(response.data[key])) {
          console.log(`找到数组字段 ${key}:`, response.data[key])
          testCases = response.data[key]
          break
        }
      }
    }
    
    console.log('原始测试用例数据:', testCases)
    
    availableTestCases.value = testCases.filter(tc => !suiteTestCaseIds.includes(tc.id))
    console.log('过滤后的可用测试用例:', availableTestCases.value)
  } catch (error) {
    console.error('加载测试用例失败:', error)
    ElMessage.error(`加载测试用例失败: ${error.message || '未知错误'}`)
  } finally {
    loadingTestCases.value = false
  }
}

// 打开添加测试用例对话框
const openAddTestCaseDialog = async () => {
  showAddTestCaseDialog.value = true
  // 重置搜索和过滤条件
  testCaseSearchKeyword.value = ''
  testCasePriorityFilter.value = ''
  testCaseCategoryFilter.value = ''
  selectedTestCases.value = []
  // 加载可用的测试用例
  await loadAvailableTestCases()
}

// 处理测试用例搜索
const handleTestCaseSearch = () => {
  loadAvailableTestCases()
}

// 处理测试用例选择变化
const handleTestCaseSelectionChange = (selection) => {
  selectedTestCases.value = selection
}

// 确认添加测试用例
const confirmAddTestCases = async () => {
  if (selectedTestCases.value.length === 0) {
    ElMessage.warning('请选择要添加的测试用例')
    return
  }

  addingTestCases.value = true
  try {
    const testCaseIds = selectedTestCases.value.map(tc => tc.id)
    await webTestingApi.addTestCasesToSuite(selectedProject.value.id, editingSuite.value.id, {
      test_case_ids: testCaseIds
    })
    
    ElMessage.success(`成功添加 ${testCaseIds.length} 个测试用例到套件`)
    showAddTestCaseDialog.value = false
    
    // 重新加载套件详情
    const response = await webTestingApi.getTestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
    
    // 重新加载测试套件列表
    loadTestSuites()
  } catch (error) {
    console.error('添加测试用例失败:', error)
    ElMessage.error('添加测试用例失败')
  } finally {
    addingTestCases.value = false
  }
}

// 从套件中移除测试用例
const removeTestCaseFromSuite = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      `确定要从套件中移除测试用例"${testCase.title}"吗？`,
      '确认移除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await webTestingApi.removeTestCaseFromSuite(selectedProject.value.id, editingSuite.value.id, testCase.id)
    ElMessage.success('测试用例已从套件中移除')
    
    // 重新加载套件详情
    const response = await webTestingApi.getTestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
    
    // 重新加载测试套件列表
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('移除测试用例失败:', error)
      ElMessage.error('移除测试用例失败')
    }
  }
}

const executeTestSuite = async (suite) => {
  try {
    // 检查是否已经在执行中
    if (executingSuites.value.has(suite.id)) {
      ElMessage.warning('测试套件正在执行中，请稍候...')
      return
    }

    // 重置选中环境，避免使用旧的环境对象引用
    selectedEnvironment.value = null
    
    // 加载环境列表
    await loadEnvironments()
    
    // 显示配置弹框
    selectedSuite.value = suite
    showExecuteDialog.value = true
  } catch (error) {
    console.error('执行测试套件失败:', error)
    ElMessage.error('执行测试套件失败')
  }
}

const createScheduledTask = (suite) => {
  // 跳转到当前模块的定时任务页，并传递参数
  router.push({
    path: '/web-testing/scheduled-tasks',
    query: {
      suite_type: 'web',
      suite_id: suite.id,
      suite_name: suite.name,
      project_id: selectedProject.value.id
    }
  })
}

const editTestSuite = async (suite) => {
  try {
    // 加载完整的测试套件数据（包括测试用例）
    const response = await webTestingApi.getTestSuite(selectedProject.value.id, suite.id)
    editingSuite.value = response.data
    
    suiteForm.value = {
      name: response.data.name,
      description: response.data.description || '',
      status: response.data.status,
      tags: response.data.tags || []
    }
    
    // 重置标签页到基本信息
    activeTab.value = 'basic'
    showCreateDialog.value = true
  } catch (error) {
    console.error('加载测试套件详情失败:', error)
    ElMessage.error('加载测试套件详情失败')
  }
}

const deleteTestSuite = async (suite) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除测试套件"${suite.name}"吗？此操作不可恢复。`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await webTestingApi.deleteTestSuite(selectedProject.value.id, suite.id)
    ElMessage.success('删除成功')
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除测试套件失败:', error)
      ElMessage.error('删除测试套件失败')
    }
  }
}

const saveTestSuite = async () => {
  try {
    await suiteFormRef.value.validate()
    
    saving.value = true
    const data = {
      ...suiteForm.value,
      project: selectedProject.value.id
    }

    if (editingSuite.value) {
      await webTestingApi.updateTestSuite(selectedProject.value.id, editingSuite.value.id, data)
      ElMessage.success('更新成功')
    } else {
      await webTestingApi.createTestSuite(selectedProject.value.id, data)
      ElMessage.success('创建成功')
    }

    showCreateDialog.value = false
    editingSuite.value = null
    suiteForm.value = {
      name: '',
      description: '',
      status: 'active',
      tags: []
    }
    loadTestSuites()
  } catch (error) {
    console.error('保存测试套件失败:', error)
    ElMessage.error('保存测试套件失败')
  } finally {
    saving.value = false
  }
}

const confirmExecute = async () => {
  try {
    // 检查是否选择了环境
    if (!selectedEnvironment.value) {
      ElMessage.warning('请选择一个测试环境')
      return
    }

    // 添加到执行状态集合
    executingSuites.value.add(selectedSuite.value.id)
    
    // 构建执行选项，包含环境配置
    const executionOptions = {
      environment_id: selectedEnvironment.value.id,
      options: executeForm.value
    }

    const response = await webTestingApi.executeTestSuite(selectedProject.value.id, selectedSuite.value.id, executionOptions)
    
    if (response && response.success && response.data) {
      const { execution_id, task_id, test_suite_name } = response.data

      ElMessage.success(`测试套件执行已启动: ${test_suite_name}`)

      // 开始轮询任务状态，传递测试套件ID用于完成后清理状态
      startTaskPolling(task_id, execution_id, selectedSuite.value.name, selectedSuite.value.id)

    } else {
      ElMessage.error(`执行测试套件失败: ${response?.message || '未知错误'}`)
      // 执行失败时移除执行状态
      executingSuites.value.delete(selectedSuite.value.id)
    }
  } catch (error) {
    console.error('执行测试套件失败:', error)
    ElMessage.error('执行测试套件失败')
    // 执行失败时移除执行状态
    executingSuites.value.delete(selectedSuite.value.id)
  } finally {
    // 关闭弹框
    showExecuteDialog.value = false
    selectedSuite.value = null
    selectedEnvironment.value = null
  }
}


// 批量操作
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedSuites.value.length} 个测试套件吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // 批量删除
    for (const suite of selectedSuites.value) {
      await webTestingApi.deleteTestSuite(selectedProject.value.id, suite.id)
    }
    
    ElMessage.success(`成功删除 ${selectedSuites.value.length} 个测试套件`)
    clearSelection()
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error('批量删除失败')
    }
  }
}

const clearSelection = () => {
  selectedSuites.value = []
}

// 测试用例管理
const clearAllTestCases = async () => {
  try {
    await ElMessageBox.confirm(
      '确定要清空套件中的所有测试用例吗？',
      '清空确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // 这里需要实现清空所有测试用例的API
    // await webTestingApi.clearAllTestCases(selectedProject.value.id, editingSuite.value.id)
    
    ElMessage.success('已清空所有测试用例')
    // 重新加载套件详情
    const response = await webTestingApi.getTestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
  } catch (error) {
    if (error !== 'cancel') {
      console.error('清空测试用例失败:', error)
      ElMessage.error('清空测试用例失败')
    }
  }
}

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// ============ 任务轮询相关方法 ============

// 开始轮询任务状态
const startTaskPolling = (taskId, executionId, testSuiteName, testSuiteId) => {
  // 存储任务信息
  pollingTasks.value.set(taskId, {
    taskId,
    executionId,
    testSuiteName,
    testSuiteId,
    startTime: Date.now()
  })

  // 立即检查一次状态
  checkTaskStatus(taskId)

  // 设置定时轮询，每2秒检查一次
  const interval = setInterval(() => {
    checkTaskStatus(taskId)
  }, 2000)

  pollingIntervals.value.set(taskId, interval)
}

// 检查任务状态
const checkTaskStatus = async (taskId) => {
  try {
    // 验证项目ID
    if (!projectStore.currentProject?.id) {
      console.warn('当前项目ID为空，停止任务轮询')
      stopTaskPolling(taskId)
      return
    }

    const result = await getTaskStatus(projectStore.currentProject.id, taskId)

    if (result && result.success && result.data) {
      const { status, progress, message } = result.data
      const taskInfo = pollingTasks.value.get(taskId)

      if (!taskInfo) {
        return
      }

      const statusUpper = status.toUpperCase()

      if (['COMPLETED', 'SUCCESS', 'FAILED', 'FAILURE'].includes(statusUpper)) {
        // 1. 停止轮询和清理状态
        stopTaskPolling(taskId)
        if (taskInfo.testSuiteId) {
          executingSuites.value.delete(taskInfo.testSuiteId)
        }

        // 2. 尝试从 result 中提取统计数据（需后端 /status 接口在 task_result 中返回统计）
        let taskResult = result.data?.result || result.data || {}

        // 防御性解析：如果数据被作为字符串返回
        if (typeof taskResult === 'string') {
          try { taskResult = JSON.parse(taskResult) } catch (e) { taskResult = {} }
        }

        // 防御性解构：如果 celery 把结果又套了一层 result
        if (taskResult.result && typeof taskResult.result === 'object' && taskResult.total_cases === undefined) {
          taskResult = { ...taskResult, ...taskResult.result }
        }

        const total = Number(taskResult.total_cases) || 0
        const passed = Number(taskResult.passed_cases) || 0
        const failed = Number(taskResult.failed_cases) || 0
        const passRate = total > 0 ? Math.round((passed / total) * 100) : 0

        // 优化成功判断：要有总用例，且失败数为0
        const isSuccess = ['COMPLETED', 'SUCCESS'].includes(statusUpper) && total > 0 && failed === 0

        // 3. 弹出右下角富文本通知
        ElNotification({
          title: '测试执行完成',
          message: `<div>
            <p style="margin: 0 0 5px 0;"><strong>任务：</strong>${taskInfo.testSuiteName || '测试套件'}</p>
            <p style="margin: 0 0 5px 0;"><strong>结论：</strong><span style="color: ${isSuccess ? '#67C23A' : '#F56C6C'}">${isSuccess ? '测试通过' : '测试未通过'}</span></p>
            <p style="margin: 0 0 10px 0;"><strong>通过率：</strong>${passRate}% (${passed}/${total})</p>
            <p style="margin: 0; font-size: 12px; color: #409EFF; cursor: pointer; text-decoration: underline;" onclick="document.getElementById('hidden-nav-btn')?.click()">点击前往「测试执行记录」查看详情</p>
          </div>`,
          dangerouslyUseHTMLString: true,
          type: isSuccess ? 'success' : 'warning',
          duration: 8000,
          position: 'bottom-right'
        })
      } else if (['PROCESSING', 'PENDING'].includes(statusUpper)) {
        // 任务进行中，更新进度信息
      }
    }
  } catch (error) {
    // 如果连续失败多次，停止轮询
    const taskInfo = pollingTasks.value.get(taskId)
    if (taskInfo) {
      const failCount = taskInfo.failCount || 0
      if (failCount >= 3) {
        ElMessage.error(`检查任务状态失败次数过多，停止监控: ${taskInfo.testSuiteName}`)
        stopTaskPolling(taskId)
      } else {
        pollingTasks.value.set(taskId, { ...taskInfo, failCount: failCount + 1 })
      }
    }
  }
}

// 停止任务轮询
const stopTaskPolling = (taskId) => {
  // 获取任务信息
  const taskInfo = pollingTasks.value.get(taskId)

  // 清除定时器
  const interval = pollingIntervals.value.get(taskId)
  if (interval) {
    clearInterval(interval)
    pollingIntervals.value.delete(taskId)
  }

  // 清理执行状态
  if (taskInfo && taskInfo.testSuiteId) {
    executingSuites.value.delete(taskInfo.testSuiteId)
  }

  // 清除任务信息
  pollingTasks.value.delete(taskId)
}

// 组件卸载时清理所有轮询
const cleanupPolling = () => {
  pollingIntervals.value.forEach((interval, taskId) => {
    clearInterval(interval)
  })
  pollingIntervals.value.clear()
  pollingTasks.value.clear()
  executingSuites.value.clear()
}

// 监听项目变化
watch(selectedProject, () => {
  if (selectedProject.value) {
    loadTestSuites()
  }
}, { immediate: true })

// 组件挂载时不需要再次加载，因为watch已经处理了
onMounted(() => {
  // 页面初始化加载已由watch处理，这里不需要重复调用
})

// 组件卸载时清理所有轮询
onUnmounted(() => {
  cleanupPolling()
})
</script>

<style scoped>
.test-suites-container {
  margin: 0 auto;
}

/* 页面头部样式 */
.page-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  margin-bottom: 0;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  position: relative;
  overflow: hidden;
}

.page-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
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
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-icon {
  width: 40px;
  height: 40px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(10px);
}

.header-icon .el-icon {
  font-size: 24px;
  color: white;
}

.header-text h2 {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 2px 0;
  color: white;
  line-height: 1.2;
}

.header-text p {
  font-size: 13px;
  margin: 0;
  opacity: 0.9;
  color: white;
  line-height: 1.2;
}

.create-btn {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  backdrop-filter: blur(10px);
  font-weight: 500;
  padding: 12px 24px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.create-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.create-btn:disabled {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
  opacity: 0.6;
  cursor: not-allowed;
}

.test-suites-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  flex-shrink: 0;
}

.card-header-left {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-header-right {
  display: flex;
  gap: 10px;
  align-items: center;
}

.card-header-filters {
  display: flex;
  gap: 10px;
  align-items: center;
}



/* 表格容器样式 */
.table-container {
  height: calc(100vh - 315px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: none;
  box-shadow: none;
}

/* 表格样式优化 */
.el-table {
  border-radius: 8px;
  overflow: hidden;
}

.el-table .el-table__row {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.el-table .el-table__row:hover {
  background-color: #f5f7fa !important;
}

/* 测试套件ID样式 */
.test-suite-id {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: #909399;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}

/* 简化的测试套件名称样式 */
.test-suite-name-simple {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.test-suite-title {
  font-weight: 600;
  color: #303133;
  cursor: pointer;
  transition: color 0.2s ease;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.test-suite-title:hover {
  color: #409eff;
}

.test-suite-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cases-count {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.total-cases {
  font-weight: 600;
  color: #303133;
  font-size: 14px;
}

.tags-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 操作按钮样式 */
.action-buttons {
  display: flex;
  flex-wrap: wrap;
}

.execute-button {
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 底部操作容器 */
.bottom-actions-container {
  flex-shrink: 0;
  border-top: 1px solid #e4e7ed;
  background: #fff;
  height: 50px;
}

.pagination-container {
  padding: 10px;
  text-align: center;
}

/* 批量操作栏覆盖样式 */
.batch-actions-overlay {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background-color: #f0f9ff;
  border-bottom: 1px solid #b3d8ff;
  margin-bottom: 15px;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

.batch-info {
  font-size: 14px;
  color: #409eff;
  font-weight: 500;
}

.batch-buttons {
  display: flex;
  gap: 10px;
}

/* 对话框样式 */
.suite-dialog :deep(.el-dialog) {
  border-radius: 12px;
}

.dialog-tabs {
  margin-top: 10px;
}

.test-case-management {
  padding: 20px 0;
}

.management-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #e4e7ed;
}

.header-left h4 {
  margin: 0 0 5px 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.header-desc {
  margin: 0;
  font-size: 14px;
  color: #909399;
}

.header-right {
  display: flex;
  gap: 10px;
}

.test-cases-table {
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e4e7ed;
}


/* 添加测试用例对话框 */
.add-testcase-dialog :deep(.el-dialog) {
  border-radius: 12px;
}

.add-test-case-content {
  padding: 10px 0;
}

.search-section {
  margin-bottom: 20px;
}

.search-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.search-header h4 {
  margin: 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.search-stats {
  font-size: 14px;
  color: #909399;
}

.search-filters {
  display: flex;
  align-items: center;
  gap: 15px;
  flex-wrap: wrap;
}

.test-cases-section {
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e4e7ed;
}

/* Playwright配置弹框样式 */
:deep(.playwright-config-dialog) {
  max-height: 80vh;
}

:deep(.playwright-config-dialog .el-dialog) {
  margin-top: 5vh !important;
  margin-bottom: 5vh !important;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}

:deep(.playwright-config-dialog .el-dialog__body) {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-height: calc(90vh - 120px);
}

:deep(.playwright-config-dialog .el-dialog__footer) {
  flex-shrink: 0;
  padding: 15px 20px;
  border-top: 1px solid #e4e7ed;
}

/* 配置弹框样式 */
.config-form {
  padding: 0;
}

.config-section {
  margin-bottom: 25px;
}

.config-section h4 {
  margin: 0 0 12px 0;
  color: #303133;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 6px;
}

.test-case-info {
  background: #f8f9fa;
  padding: 12px;
  border-radius: 6px;
  border-left: 3px solid #409eff;
}

.test-case-info p {
  margin: 4px 0;
  color: #606266;
  font-size: 13px;
  line-height: 1.4;
}

.test-case-info strong {
  color: #303133;
}

/* 环境选择器样式 */
.environment-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
  min-height: auto;
}

.environment-name {
  font-weight: 600;
  color: #303133;
}

.environment-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 2px;
}

/* 环境名称和URL在同一行显示 */
.environment-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 20px;
}

.environment-name-inline {
  font-weight: 600;
  color: #303133;
  flex-shrink: 0;
  line-height: 1.2;
}

.environment-url-inline {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  background: #f0f9eb;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #c2e7b0;
  flex-shrink: 0;
  line-height: 1.2;
}

/* 确保下拉选项有足够的高度 */
:deep(.el-select-dropdown__item) {
  height: auto !important;
  min-height: 40px;
  padding: 8px 20px;
  line-height: 1.4;
}

:deep(.el-select-dropdown__item .environment-option) {
  width: 100%;
}

.no-environments-tip {
  margin-top: 10px;
}

/* 无环境选项样式 */
.no-environments-option {
  cursor: not-allowed !important;
}

.no-environments-content {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  color: #e6a23c;
}

.warning-icon {
  font-size: 14px;
  color: #e6a23c;
  flex-shrink: 0;
}

.no-environments-text {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #e6a23c;
  line-height: 1.2;
}

.no-environments-title {
  font-weight: 500;
}

.no-environments-desc {
  color: #909399;
}

/* 对话框底部 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 表格行样式 */
:deep(.archived-row) {
  background-color: #f5f5f5;
  opacity: 0.7;
}

:deep(.archived-row:hover) {
  background-color: #f0f0f0;
}


@media (max-width: 768px) {
  .test-suites-container {
    padding: 10px;
  }
  
  .page-header {
    padding: 20px;
  }
  
  .page-title {
    font-size: 24px;
  }
  
  .header-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 20px;
  }
  
  .header-right {
    width: 100%;
    justify-content: flex-end;
  }
  
  .filter-container {
    flex-direction: column;
    align-items: stretch;
    gap: 15px;
  }
  
  .filter-left {
    flex-direction: column;
    align-items: stretch;
  }
  
  .filter-right {
    justify-content: center;
  }
  
  
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }

  .card-header-right {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
  }

  .card-header-filters {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    width: 100%;
  }

  .card-header-filters .el-select,
  .card-header-filters .el-input {
    width: 100% !important;
  }

  .action-buttons {
    flex-direction: column;
    gap: 5px;
  }

  .batch-actions-overlay {
    flex-direction: column;
    gap: 15px;
    text-align: center;
  }

  .batch-buttons {
    justify-content: center;
    flex-wrap: wrap;
  }
  
  .management-header {
    flex-direction: column;
    align-items: stretch;
    gap: 15px;
  }
  
  .header-right {
    justify-content: center;
  }
  
  .search-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  
  .search-filters {
    flex-direction: column;
    align-items: stretch;
  }
}

</style>
