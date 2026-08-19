<template>
  <div class="test-cases-container">
    <!-- 场景测试用例列表 -->
    <el-card v-if="selectedProject" class="test-cases-card">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedTestCases.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedTestCases.length }} 个场景测试用例</span>
        </div>
        <div class="batch-buttons">
          <el-button type="success" @click="handleBatchJoinSuite">
            <el-icon><FolderAdd /></el-icon>
            批量加入套件
          </el-button>
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
          <h3>场景测试用例列表</h3>
          <span class="subtitle">管理跨多个API端点的业务流程测试用例</span>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select v-model="endpointFilter" placeholder="涉及端点" clearable style="width: 250px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option v-for="endpoint in endpointOptions" :key="endpoint" :label="endpoint" :value="endpoint" />
            </el-select>
            <el-select v-model="summaryFilter" placeholder="端点摘要/操作" clearable style="width: 250px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option v-for="summary in summaryOptions" :key="summary" :label="summary" :value="summary" />
            </el-select>
            <el-select v-model="testCaseTypeFilter" placeholder="用例类型" clearable style="width: 120px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option label="正向用例" value="positive" />
              <el-option label="负向用例" value="negative" />
              <el-option label="边界测试" value="boundary" />
              <el-option label="安全测试" value="security" />
            </el-select>
            <el-select v-model="priorityFilter" placeholder="优先级" clearable style="width: 120px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option label="低" value="low" />
              <el-option label="中" value="medium" />
              <el-option label="高" value="high" />
              <el-option label="紧急" value="critical" />
            </el-select>
            <el-input v-model="searchQuery" placeholder="输入关键字查询" style="width: 200px;" clearable
              @input="handleSearch">
              <template #prefix>
                <el-icon>
                  <Search />
                </el-icon>
              </template>
            </el-input>
          </div>
        </div>
      </div>

      <!-- 左右分栏：左侧可拖拽列表 + 右侧详情 -->
      <div class="split-body" v-loading="loading">
        <div class="left-panel">
          <div v-if="filteredTestCases.length === 0" class="list-empty-wrap">
            <el-empty description="暂无场景测试用例" :image-size="60" />
          </div>
          <Draggable
            v-else
            v-model="draggableList"
            item-key="id"
            handle=".drag-handle"
            class="draggable-list"
            :animation="150"
            @end="handleDragEnd"
          >
            <template #item="{ element: testCase }">
              <div
                class="scenario-list-item"
                :class="{ 'is-active': selectedTestCase?.id === testCase.id }"
                @click="viewTestCase(testCase)"
              >
                <div class="drag-handle" @click.stop title="拖拽排序">
                  <el-icon><Rank /></el-icon>
                </div>
                <div class="item-main-content">
                  <el-checkbox
                    :model-value="selectedTestCases.some(t => t.id === testCase.id)"
                    @update:model-value="(v) => toggleSelection(testCase, v)"
                    @click.stop
                    class="item-checkbox"
                  />
                  <div class="item-title" :title="testCase.title">{{ testCase.title }}</div>
                  <div class="item-meta">
                    <el-tag v-if="testCase.test_type" :type="getTestTypeTag(testCase.test_type)" size="small">
                      {{ getTestTypeLabel(testCase.test_type) }}
                    </el-tag>
                    <el-tag type="primary" size="small">{{ getStepsCount(testCase) }} 步</el-tag>
                    <el-tag :type="getPriorityTag(testCase.priority)" size="small">
                      {{ getPriorityLabel(testCase.priority) }}
                    </el-tag>
                  </div>
                  <div class="item-actions">
                    <el-button type="primary" size="small" @click.stop="runTestCase(testCase)"
                      :loading="executingTestCases.has(testCase.id)" :disabled="executingTestCases.has(testCase.id)">
                      {{ executingTestCases.has(testCase.id) ? '执行中' : '执行' }}
                    </el-button>
                    <el-button size="small" @click.stop="viewTestCase(testCase)">编辑</el-button>
                    <el-button type="danger" size="small" @click.stop="deleteTestCase(testCase)">删除</el-button>
                  </div>
                </div>
              </div>
            </template>
          </Draggable>
        </div>
        <div class="right-panel">
          <div v-if="selectedTestCase" class="detail-preview">
            <div class="preview-header">
              <h4>{{ selectedTestCase.title }}</h4>
              <el-button type="primary" size="small" @click="showDetailDialog = true">编辑详情</el-button>
            </div>
            <p v-if="selectedTestCase.description" class="preview-desc">{{ selectedTestCase.description }}</p>
            <div class="preview-meta">
              <el-tag size="small">{{ getStepsCount(selectedTestCase) }} 步</el-tag>
              <el-tag :type="getPriorityTag(selectedTestCase.priority)" size="small">
                {{ getPriorityLabel(selectedTestCase.priority) }}
              </el-tag>
            </div>
          </div>
          <div v-else class="empty-state">
            <el-empty description="请从左侧选择场景用例" :image-size="100">
              <template #image>
                <el-icon class="empty-icon"><Edit /></el-icon>
              </template>
            </el-empty>
          </div>
        </div>
      </div>

      <!-- 分页区域 -->
      <div class="bottom-actions-container">
        <!-- 分页 -->
        <div class="pagination-container">
          <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[10, 20, 50, 100]"
            :total="total" layout="total, sizes, prev, pager, next, jumper" @size-change="handleSizeChange"
            @current-change="handleCurrentChange" />
        </div>
      </div>
    </el-card>

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

    <!-- 测试用例详情右侧滑栏 -->
    <APICaseEditDetail v-model="showDetailDialog" :test-case="selectedTestCase" @run="runTestCase"
      @update="handleTestCaseUpdate" />

    <!-- 测试结果详情对话框 -->
    <el-dialog v-model="showResultDialog" title="测试结果详情" width="80%" :close-on-click-modal="false">
      <APITestCaseExecutionDetail v-if="selectedTestResult" :result="selectedTestResult" />
    </el-dialog>

    <!-- 批量加入套件弹窗 -->
    <SuiteSelectionDialog
      v-model="showSuiteDialog"
      :project-id="currentProjectId"
      :case-ids="selectedTestCases.map(tc => tc.id)"
      @success="onBatchJoinSuccess"
    />

    <!-- API测试执行配置弹框 -->
    <el-dialog v-model="configDialogVisible" title="场景测试执行配置" width="600px" :close-on-click-modal="false" :modal="true"
      :append-to-body="true" class="api-config-dialog">
      <div v-if="selectedTestCase" class="config-form">
        <div class="config-section">
          <h4>测试场景信息</h4>
          <div class="test-case-info">
            <p><strong>场景名称：</strong>{{ selectedTestCase.title }}</p>
            <p v-if="selectedTestCase.description"><strong>场景描述：</strong>{{ selectedTestCase.description }}</p>
            <p><strong>步骤数量：</strong>{{ getStepsCount(selectedTestCase) }} 步</p>
          </div>
        </div>

        <div class="config-section">
          <h4>测试环境</h4>
          <el-form :model="executionOptions" label-width="120px">
            <el-form-item label="选择环境" required>
              <el-select v-model="selectedEnvironment" placeholder="请选择测试环境" style="width: 100%"
                :loading="loadingEnvironments" value-key="id">
                <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env">
                  <div class="environment-option">
                    <div class="environment-header">
                      <div class="environment-name-inline">{{ env.name }}</div>
                      <div class="environment-url-inline" v-if="env.config?.base_url">{{ env.config.base_url }}</div>
                    </div>
                  </div>
                </el-option>
                <!-- 当没有环境时显示提示信息 -->
                <el-option v-if="environments.length === 0 && !loadingEnvironments" :value="null" disabled
                  class="no-environments-option">
                  <div class="no-environments-content">
                    <el-icon class="warning-icon">
                      <Warning />
                    </el-icon>
                    <div class="no-environments-text">
                      <div class="no-environments-title">暂无API测试环境</div>
                      <div class="no-environments-desc">请先在项目管理中创建API测试环境</div>
                    </div>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
        </div>

        <div class="config-section">
          <h4>执行配置</h4>
          <el-form :model="executionOptions" label-width="120px">
            <el-form-item label="超时时间（秒）">
              <el-input-number v-model="executionOptions.timeout" :min="10" :max="300" :step="10"
                style="width: 200px" />
            </el-form-item>

            <el-form-item label="SSL验证">
              <el-radio-group v-model="executionOptions.verify_ssl">
                <el-radio :label="true">验证SSL</el-radio>
                <el-radio :label="false">跳过SSL验证</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-form>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="configDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="confirmRunTestCase" :loading="executingTestCases.has(selectedTestCase?.id)"
            :disabled="!selectedEnvironment">
            {{ executingTestCases.has(selectedTestCase?.id) ? '执行中...' : '确认执行' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import Draggable from 'vuedraggable'
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh,
  Search,
  Warning,
  Edit,
  VideoPlay,
  Delete,
  Close,
  ArrowRight,
  Rank,
  FolderAdd
} from '@element-plus/icons-vue'
import {
  getAPITestCases,
  getAPITestCase,
  deleteAPITestCase,
  batchDeleteAPITestCases,
  executeAPITestCase,
  getAPITestCaseExecutionDetail,
  getTaskStatus,
  updateScenarioTestCasesOrder
} from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'
import APICaseEditDetail from '@/components/APICaseEditDetail.vue'
import APITestCaseExecutionDetail from '@/components/APITestCaseExecutionDetail.vue'
import SuiteSelectionDialog from '@/components/SuiteSelectionDialog.vue'
import { useProjectStore } from '@/stores/project'
import dayjs from 'dayjs'

const router = useRouter()

// 状态管理
const loading = ref(false)
const showDetailDialog = ref(false)
const showResultDialog = ref(false)
const showSuiteDialog = ref(false)
const selectedTestCase = ref(null)
const selectedTestResult = ref(null)

// 执行配置弹框相关
const configDialogVisible = ref(false)
const environments = ref([])
const selectedEnvironment = ref(null)
const loadingEnvironments = ref(false)
const executionOptions = ref({
  timeout: 30,
  verify_ssl: true,
  generate_report: true
})

// 任务轮询相关状态
const pollingTasks = ref(new Map())
const pollingIntervals = ref(new Map())

// 执行状态跟踪
const executingTestCases = ref(new Set())

// 过滤和搜索
const endpointFilter = ref('')  // 端点过滤器
const summaryFilter = ref('')  // 端点摘要/操作过滤器
const testCaseTypeFilter = ref('')
const priorityFilter = ref('')
const searchQuery = ref('')

// 视图模式 - 场景测试用例只需要列表视图
const viewMode = ref('list')
const expandedGroups = ref([])

// 数据
const testCases = ref([])
const selectedTestCases = ref([])
const draggableList = ref([])

// 使用项目状态管理
const projectStore = useProjectStore()
const selectedProject = computed(() => projectStore.currentProject)
const currentProjectId = computed(() => projectStore.currentProjectId)

onMounted(async () => {
  try {
    await loadData()
  } catch (error) {
    handleError('初始化失败，请刷新页面重试')
  }
})

onUnmounted(() => {
  cleanupPolling()
})

// 分页
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 工具函数：从 script_content 动态解析涉及的端点列表
const getInvolvedEndpoints = (testCase) => {
  const endpoints = []

  // 从 script_content 解析步骤（唯一数据源）
  const steps = parseTeststeps(testCase)
  if (steps.length > 0) {
    // 遍历步骤，提取每个步骤的HTTP方法和路径
    steps.forEach(step => {
      if (step.request) {
        const method = step.request.method || step.method || 'GET'
        const url = step.request.url || step.url || ''
        
        // 提取路径（去掉域名和查询参数）
        let path = url
        try {
          if (url.startsWith('http')) {
            const urlObj = new URL(url)
            path = urlObj.pathname
          } else if (url.startsWith('/')) {
            path = url.split('?')[0]
          }
        } catch (e) {
          path = url.split('?')[0]
        }
        
        if (path) {
          endpoints.push({ method, path })
        }
      }
    })
  }
  
  // 去重（基于 method + path）
  const uniqueEndpoints = []
  const seen = new Set()
  endpoints.forEach(ep => {
    const key = `${ep.method} ${ep.path}`
    if (!seen.has(key)) {
      seen.add(key)
      uniqueEndpoints.push(ep)
    }
  })
  
  return uniqueEndpoints
}

// 工具函数：从端点路径动态提取模块列表
const getInvolvedModules = (testCase) => {
  const modules = new Set()
  const endpoints = getInvolvedEndpoints(testCase)
  
  // 从每个端点提取模块信息
  endpoints.forEach(endpoint => {
    const pathParts = endpoint.path.split('/').filter(p => p && !p.startsWith('{') && !p.match(/^\d+$/))
    if (pathParts.length > 0) {
      const moduleName = pathParts[0]
      const moduleNameMap = {
        'user': '用户模块',
        'order': '订单模块',
        'product': '商品模块',
        'building': '楼栋模块',
        'community': '小区模块',
        'file': '文件模块',
        'owner': '业主模块',
        'payment': '支付模块',
        'api': 'API模块',
        'auth': '认证模块'
      }
      modules.add(moduleNameMap[moduleName.toLowerCase()] || `${moduleName}模块`)
    }
  })
  
  // 如果没有从端点提取到模块，尝试从标题提取
  if (modules.size === 0 && testCase.title) {
    const title = testCase.title
    const modulePatterns = [
      { pattern: /^(用户|会员|账号|账户)/, name: '用户模块' },
      { pattern: /^(订单|下单)/, name: '订单模块' },
      { pattern: /^(商品|产品|货物)/, name: '商品模块' },
      { pattern: /^(支付|付款|缴费)/, name: '支付模块' },
      { pattern: /^(楼栋|楼宇|建筑)/, name: '楼栋模块' },
      { pattern: /^(小区|社区|园区)/, name: '小区模块' },
      { pattern: /^(业主|住户|居民)/, name: '业主模块' },
      { pattern: /^(文件|附件|上传|下载)/, name: '文件模块' },
      { pattern: /^(登录|认证|授权)/, name: '认证模块' },
      { pattern: /^(购物|结算|购买)/, name: '购物模块' }
    ]
    
    for (const { pattern, name } of modulePatterns) {
      if (pattern.test(title)) {
        modules.add(name)
        break
      }
    }
  }
  
  return Array.from(modules)
}

// 工具函数：获取模块名称（保留用于兼容）
const getModuleName = (testCase) => {
  const modules = getInvolvedModules(testCase)
  return modules.length > 0 ? modules[0] : '未分类'
}

/**
 * 解析用例中的 teststeps 数组，优先读 script_content，兜底读 test_data/request_data
 */
const parseTeststeps = (testCase) => {
  // 1. 优先从 script_content 中解析（新架构）
  if (testCase.script_content) {
    try {
      const sc = typeof testCase.script_content === 'string'
        ? JSON.parse(testCase.script_content)
        : testCase.script_content
      if (Array.isArray(sc?.teststeps)) return sc.teststeps
      if (Array.isArray(sc?.steps))     return sc.steps
    } catch { /* 解析失败降级 */ }
  }
  // 2. 旧字段兜底
  const fallback = testCase.test_data ?? testCase.request_data
  if (!fallback) return []
  if (Array.isArray(fallback?.teststeps)) return fallback.teststeps
  if (Array.isArray(fallback?.steps))     return fallback.steps
  if (Array.isArray(fallback))            return fallback
  return []
}

// 获取场景步骤数量
const getStepsCount = (testCase) => {
  return parseTeststeps(testCase).length || 1
}

// 计算属性 - 过滤场景测试用例
const filteredTestCases = computed(() => {
  // 后端已经只返回场景测试用例，这里只需要应用其他过滤条件
  let filtered = testCases.value

  // 端点过滤（新增）
  if (endpointFilter.value) {
    filtered = filtered.filter(tc => {
      const endpoints = getInvolvedEndpoints(tc)
      return endpoints.some(ep => `${ep.method} ${ep.path}` === endpointFilter.value)
    })
  }

  // 端点摘要/操作过滤
  if (summaryFilter.value) {
    filtered = filtered.filter(tc => {
      // 从involved_endpoints_with_summary中获取所有summary
      const summaries = (tc.involved_endpoints_with_summary || [])
        .map(ep => ep.summary)
        .filter(s => s) // 过滤掉空值
      return summaries.includes(summaryFilter.value)
    })
  }

  if (testCaseTypeFilter.value) {
    filtered = filtered.filter(tc => tc.test_type === testCaseTypeFilter.value)
  }

  if (priorityFilter.value) {
    filtered = filtered.filter(tc => tc.priority === priorityFilter.value)
  }

  if (searchQuery.value) {
    filtered = filtered.filter(testCase =>
      testCase.title.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      testCase.description?.toLowerCase().includes(searchQuery.value.toLowerCase())
    )
  }

  return filtered
})

// 同步 filteredTestCases 到 draggableList（筛选或数据变化时）
watch(filteredTestCases, (val) => {
  draggableList.value = [...val]
}, { immediate: true })

// 获取所有端点选项
const endpointOptions = computed(() => {
  const endpoints = new Set()
  testCases.value.forEach(tc => {
    const tcEndpoints = getInvolvedEndpoints(tc)
    tcEndpoints.forEach(ep => {
      endpoints.add(`${ep.method} ${ep.path}`)
    })
  })
  return Array.from(endpoints).sort()
})

// 获取所有端点摘要/操作选项
const summaryOptions = computed(() => {
  const summaries = new Set()
  testCases.value.forEach(tc => {
    // 从involved_endpoints_with_summary中提取所有summary
    const endpoints = tc.involved_endpoints_with_summary || []
    endpoints.forEach(ep => {
      if (ep.summary) {
        summaries.add(ep.summary)
      }
    })
  })
  return Array.from(summaries).sort()
})

// 方法
const loadData = async () => {
  try {
    loading.value = true
    if (projectStore.currentProject) {
      await loadTestCases()
    }
  } catch (error) {
    handleError('加载数据失败')
  } finally {
    loading.value = false
  }
}

const loadTestCases = async () => {
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      test_case_type: 'scenario',  // 只加载场景测试用例
      test_type: testCaseTypeFilter.value,
      priority: priorityFilter.value,
      search: searchQuery.value
    }

    const response = await getAPITestCases(projectStore.currentProjectId, params)
    const { items, total: totalCount } = extractDataFromResponse(response)
    testCases.value = ensureArray(items)
    total.value = totalCount
  } catch (error) {
    handleError('加载测试用例失败')
  }
}

// 拖拽结束：同步数据并调用后端
const handleDragEnd = async () => {
  const list = draggableList.value
  const filteredIds = new Set(list.map(t => t.id))
  const unfiltered = testCases.value.filter(t => !filteredIds.has(t.id))
  testCases.value = [...list, ...unfiltered]

  const caseIds = list.map(t => t.id)
  if (caseIds.length === 0) return
  try {
    await updateScenarioTestCasesOrder(currentProjectId.value, caseIds)
    ElMessage.success('场景顺序已更新')
  } catch (e) {
    ElMessage.error('更新场景顺序失败')
    loadTestCases()
  }
}

// 勾选/取消勾选
const toggleSelection = (testCase, checked) => {
  if (checked) {
    if (!selectedTestCases.value.some(t => t.id === testCase.id)) {
      selectedTestCases.value = [...selectedTestCases.value, testCase]
    }
  } else {
    selectedTestCases.value = selectedTestCases.value.filter(t => t.id !== testCase.id)
  }
}

const handleSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadTestCases()
}

const handleCurrentChange = (page) => {
  currentPage.value = page
  loadTestCases()
}

const handleTestCaseUpdate = async (updatedTestCase) => {
  try {
    loadTestCases()
  } catch (error) {
    handleError(error.message || '未知错误', '刷新失败')
  }
}

const viewTestCase = async (testCase) => {
  selectedTestCase.value = testCase
  try {
    const response = await getAPITestCase(projectStore.currentProjectId, testCase.id)
    if (response?.success && response.data) {
      selectedTestCase.value = response.data
    }
    showDetailDialog.value = true
  } catch (error) {
    console.error('获取测试用例详情失败:', error)
    handleError('获取测试用例详情失败')
  }
}

const runTestCase = async (testCase) => {
  if (executingTestCases.value.has(testCase.id)) {
    return
  }

  if (!projectStore.currentProjectId) {
    ElMessage.error('请先选择一个项目')
    return
  }

  try {
    await loadEnvironments()
    selectedTestCase.value = testCase
    configDialogVisible.value = true
  } catch (error) {
    handleError(error.message || '未知错误', '执行失败')
  }
}

const confirmRunTestCase = async () => {
  try {
    if (!selectedTestCase.value) return

    if (!selectedEnvironment.value) {
      ElMessage.warning('请选择一个测试环境')
      return
    }

    executingTestCases.value.add(selectedTestCase.value.id)

    const executionData = {
      environment_id: selectedEnvironment.value.id,
      ...executionOptions.value
    }

    const result = await executeAPITestCase(projectStore.currentProjectId, selectedTestCase.value.id, executionData)

    if (result && result.success && result.data) {
      const { execution_id, task_id, execution_name } = result.data
      ElMessage.success(`场景测试执行已启动: ${execution_name}`)
      startTaskPolling(task_id, execution_id, selectedTestCase.value.title, selectedTestCase.value.id)
    } else {
      ElMessage.error(`执行场景测试失败: ${result?.message || '未知错误'}`)
      executingTestCases.value.delete(selectedTestCase.value.id)
    }
  } catch (error) {
    handleError(error.message || '未知错误', '执行失败')
    executingTestCases.value.delete(selectedTestCase.value.id)
  } finally {
    configDialogVisible.value = false
    selectedTestCase.value = null
    selectedEnvironment.value = null
  }
}

const loadEnvironments = async () => {
  if (!projectStore.currentProject?.id) return

  try {
    loadingEnvironments.value = true

    const params = {
      category: 'api'
    }

    const response = await getProjectEnvironments(projectStore.currentProject.id, params)

    if (response.success) {
      const allEnvironments = response.data.items || []
      environments.value = allEnvironments.filter(env => env.is_active === true)
      if (environments.value.length > 0 && !selectedEnvironment.value) {
        selectedEnvironment.value = environments.value[0]
      }
    } else {
      console.warn('加载环境列表失败:', response.message)
      environments.value = []
    }
  } catch (error) {
    console.error('加载环境列表失败:', error)
    environments.value = []
  } finally {
    loadingEnvironments.value = false
  }
}

const getMethodClass = (method) => {
  return methodMap[method]?.class || 'method-default'
}

const getTestTypeLabel = (type) => {
  return testTypeMap[type]?.label || type
}

const getTestTypeTag = (type) => {
  return testTypeMap[type]?.tag || 'info'
}

const getPriorityLabel = (priority) => {
  return priorityMap[priority]?.label || priority
}

const getPriorityTag = (priority) => {
  return priorityMap[priority]?.tag || 'info'
}

const toggleGroup = (groupKey) => {
  const index = expandedGroups.value.indexOf(groupKey)
  if (index > -1) {
    expandedGroups.value.splice(index, 1)
  } else {
    expandedGroups.value.push(groupKey)
  }
}

const formatDate = (dateStr) => {
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

const goToProjects = () => {
  router.push('/project/project-list')
}

const handleError = (error, defaultMessage = '操作失败') => {
  const message = error?.message || error || defaultMessage
  ElMessage.error(message)
  return message
}

// 状态映射
const methodMap = {
  'GET': { class: 'method-get', color: '#67c23a' },
  'POST': { class: 'method-post', color: '#409eff' },
  'PUT': { class: 'method-put', color: '#e6a23c' },
  'DELETE': { class: 'method-delete', color: '#f56c6c' },
  'PATCH': { class: 'method-patch', color: '#409eff' }
}

const priorityMap = {
  'low': { label: '低', tag: 'info' },
  'medium': { label: '中', tag: 'warning' },
  'high': { label: '高', tag: 'danger' },
  'critical': { label: '紧急', tag: 'danger' }
}

const testTypeMap = {
  'positive': { label: '正向用例', tag: 'success' },
  'negative': { label: '负向用例', tag: 'danger' },
  'boundary': { label: '边界测试', tag: 'warning' },
  'security': { label: '安全测试', tag: 'info' }
}

const ensureArray = (data) => {
  return Array.isArray(data) ? data : []
}

const extractDataFromResponse = (response) => {
  if (response && response.success && response.data) {
    return {
      items: response.data.items || response.data,
      total: response.data.pagination?.total || response.data.total || 0
    }
  }
  return { items: [], total: 0 }
}

const handleSearch = () => {
  currentPage.value = 1
  loadTestCases()
}

// 批量操作相关方法
const clearSelection = () => {
  selectedTestCases.value = []
}

const handleBatchJoinSuite = () => {
  if (selectedTestCases.value.length === 0) return
  showSuiteDialog.value = true
}

const onBatchJoinSuccess = () => {
  clearSelection()
}

const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTestCases.value.length} 个场景测试用例吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    const caseIds = selectedTestCases.value.map(tc => tc.id)
    const res = await batchDeleteAPITestCases(projectStore.currentProjectId, caseIds)
    if (res?.success !== false) {
      ElMessage.success(`成功删除 ${res?.data?.deleted_count ?? caseIds.length} 个场景测试用例`)
    } else {
      throw new Error(res?.message || '批量删除失败')
    }
    clearSelection()
    loadTestCases()
  } catch (error) {
    if (error !== 'cancel') {
      handleError(error?.message || '未知错误', '批量删除失败')
    }
  }
}

// 删除单个测试用例
const deleteTestCase = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除场景测试用例"${testCase.title}"吗？此操作不可恢复。`,
      '删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteAPITestCase(projectStore.currentProjectId, testCase.id)
    ElMessage.success('场景测试用例删除成功')
    loadTestCases()
  } catch (error) {
    if (error !== 'cancel') {
      handleError(error.message || '未知错误', '删除失败')
    }
  }
}

// 任务轮询相关方法
const startTaskPolling = (taskId, testRunId, testCaseName, testCaseId) => {
  pollingTasks.value.set(taskId, {
    taskId,
    testRunId,
    testCaseName,
    testCaseId,
    startTime: Date.now()
  })

  checkTaskStatus(taskId)

  const interval = setInterval(() => {
    checkTaskStatus(taskId)
  }, 2000)

  pollingIntervals.value.set(taskId, interval)
}

const checkTaskStatus = async (taskId) => {
  try {
    if (!projectStore.currentProjectId) {
      console.warn('当前项目ID为空，停止任务轮询')
      stopTaskPolling(taskId)
      return
    }

    const result = await getTaskStatus(projectStore.currentProjectId, taskId)

    if (result && result.success && result.data) {
      const { status, progress, message } = result.data
      const taskInfo = pollingTasks.value.get(taskId)

      if (!taskInfo) {
        return
      }

      const statusUpper = status.toUpperCase()

      if (['COMPLETED', 'SUCCESS'].includes(statusUpper)) {
        ElMessage.success(`场景测试任务完成: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)

        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }

        await loadAndShowTestResults(taskInfo.testRunId)

      } else if (['FAILED', 'FAILURE'].includes(statusUpper)) {
        ElMessage.error(`场景测试任务失败: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)

        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }

      } else if (['PROCESSING', 'PENDING'].includes(statusUpper)) {
        // 任务进行中
      }
    }
  } catch (error) {
    const taskInfo = pollingTasks.value.get(taskId)
    if (taskInfo) {
      const failCount = taskInfo.failCount || 0
      if (failCount >= 3) {
        ElMessage.error(`检查任务状态失败次数过多，停止监控: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)
      } else {
        pollingTasks.value.set(taskId, { ...taskInfo, failCount: failCount + 1 })
      }
    }
  }
}

const stopTaskPolling = (taskId) => {
  const taskInfo = pollingTasks.value.get(taskId)

  const interval = pollingIntervals.value.get(taskId)
  if (interval) {
    clearInterval(interval)
    pollingIntervals.value.delete(taskId)
  }

  if (taskInfo && taskInfo.testCaseId) {
    executingTestCases.value.delete(taskInfo.testCaseId)
  }

  pollingTasks.value.delete(taskId)
}

const loadAndShowTestResults = async (testRunId) => {
  try {
    if (!projectStore.currentProjectId) {
      ElMessage.warning('当前项目ID为空，无法加载测试结果')
      return
    }

    const testExecutionResult = await getAPITestCaseExecutionDetail(projectStore.currentProjectId, testRunId)

    if (testExecutionResult && testExecutionResult.success && testExecutionResult.data) {
      selectedTestResult.value = testExecutionResult.data
      showResultDialog.value = true
    } else if (testExecutionResult && (testExecutionResult.id || testExecutionResult.name)) {
      selectedTestResult.value = testExecutionResult
      showResultDialog.value = true
    } else {
      ElMessage.warning('无法获取测试执行详情')
    }
  } catch (error) {
    handleError(error.message || '未知错误', '加载测试执行详情失败')
  }
}

const cleanupPolling = () => {
  pollingIntervals.value.forEach((interval, taskId) => {
    clearInterval(interval)
  })
  pollingIntervals.value.clear()
  pollingTasks.value.clear()
  executingTestCases.value.clear()
}

</script>

<style scoped>
/* 场景测试用例特有样式 */
.test-cases-container {
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  overflow: hidden;
}

.test-cases-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
  min-height: 0;
}

.test-cases-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
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

.card-header-left h3 {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.subtitle {
  font-size: 13px;
  color: #909399;
  font-style: italic;
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

.view-mode-switch {
  margin-bottom: 15px;
  display: flex;
  justify-content: flex-end;
  padding: 0 10px;
}

/* ===== 左右分栏布局 ===== */
.split-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.left-panel {
  width: 380px;
  flex-shrink: 0;
  border-right: 1px solid var(--el-border-color-lighter);
  overflow-y: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.draggable-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.scenario-list-item {
  display: flex;
  align-items: flex-start;
  padding: 12px 10px;
  border-bottom: 1px solid #ebeef5;
  transition: background-color 0.2s;
  cursor: pointer;
}

.scenario-list-item:hover {
  background-color: var(--el-fill-color-light);
}

.scenario-list-item.is-active {
  background-color: var(--el-color-primary-light-9);
}

.drag-handle {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  cursor: grab;
  opacity: 0.3;
  margin-right: 8px;
  transition: all 0.2s;
}

.drag-handle:active {
  cursor: grabbing;
}

.scenario-list-item:hover .drag-handle {
  opacity: 1;
  color: #409eff;
}

.item-main-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.item-checkbox {
  flex-shrink: 0;
  margin-top: 2px;
  margin-bottom: 6px;
}

.item-title {
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 6px;
}

.item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.list-empty-wrap {
  padding: 40px 20px;
}

.right-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
  padding-bottom: 20px;
  background: var(--el-bg-color-page);
}

.detail-preview {
  padding: 16px;
  background: var(--el-bg-color);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.preview-header h4 {
  margin: 0;
  font-size: 16px;
  color: var(--el-text-color-primary);
}

.preview-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin: 0 0 12px;
  line-height: 1.5;
}

.preview-meta {
  display: flex;
  gap: 8px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.empty-state .empty-icon {
  font-size: 80px;
  color: var(--el-color-info-light-5);
}

.test-case-id {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: #909399;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}

.test-case-name-simple {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.test-case-title {
  font-weight: 600;
  color: #303133;
  cursor: pointer;
  transition: color 0.2s ease;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.test-case-title:hover {
  color: #409eff;
}

.test-case-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-muted {
  color: #909399;
  font-style: italic;
  font-size: 12px;
}

.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.execute-button {
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

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

.bottom-actions-container {
  flex-shrink: 0;
  background: #fff;
  height: 50px;
}

.pagination-container {
  padding: 10px;
  text-align: center;
}

.grouped-container {
  height: calc(100vh - 280px);
  overflow-y: auto;
  padding: 10px;
}

.module-group {
  margin-bottom: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.06);
  transition: all 0.3s ease;
}

.module-group:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.module-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 20px;
  /* 场景测试用例使用绿色渐变 */
  background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
  cursor: pointer;
  user-select: none;
  transition: all 0.3s ease;
}

.module-header:hover {
  background: linear-gradient(135deg, #0f8a7f 0%, #32d970 100%);
}

.module-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.expand-icon {
  font-size: 16px;
  color: #fff;
  transition: transform 0.3s ease;
}

.expand-icon.expanded {
  transform: rotate(90deg);
}

.module-name {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.5px;
}

.module-badge {
  margin-left: 5px;
}

.module-badge :deep(.el-badge__content) {
  background-color: #fff;
  color: #11998e;
  font-weight: 600;
  border: none;
}

.module-content {
  padding: 0;
  animation: slideDown 0.3s ease;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.module-content .el-table {
  border-radius: 0;
  border: none;
}

.module-content .el-table th {
  background-color: #f8f9fa;
  color: #606266;
  font-weight: 600;
}

.module-content .el-table tr:hover {
  background-color: #f5f7fa !important;
}

.module-tag {
  font-weight: 500;
}

/* API配置弹框样式 */
:deep(.api-config-dialog) {
  max-height: 80vh;
}

:deep(.api-config-dialog .el-dialog) {
  margin-top: 5vh !important;
  margin-bottom: 5vh !important;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}

:deep(.api-config-dialog .el-dialog__body) {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-height: calc(90vh - 120px);
}

:deep(.api-config-dialog .el-dialog__footer) {
  flex-shrink: 0;
  padding: 15px 20px;
  border-top: 1px solid #e4e7ed;
}

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
  border-left: 3px solid #11998e;
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

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.environment-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
  min-height: auto;
}

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

:deep(.el-select-dropdown__item) {
  height: auto !important;
  min-height: 40px;
  padding: 8px 20px;
  line-height: 1.4;
}

:deep(.el-select-dropdown__item .environment-option) {
  width: 100%;
}

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

/* 涉及端点和模块的样式 */
.involved-endpoints {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.involved-modules {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

</style>
