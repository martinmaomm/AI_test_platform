<template>
  <div class="test-cases-container">
    <!-- 测试用例列表 -->
    <el-card v-if="selectedProject" class="test-cases-card">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedTestCases.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedTestCases.length }} 个测试用例</span>
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
          <h3>用例列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select v-model="moduleFilter" placeholder="模块" clearable style="width: 200px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option v-for="module in moduleOptions" :key="module" :label="module" :value="module" />
            </el-select>
            <el-select v-model="endpointFilter" placeholder="端点" clearable style="width: 250px;"
              @change="handleSearch">
              <el-option label="全部" value="" />
              <el-option v-for="endpoint in endpointOptions" :key="endpoint" :label="endpoint" :value="endpoint" />
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

      <!-- 分组展示开关 -->
      <div class="view-mode-switch">
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button label="endpoint_group">端点分组</el-radio-button>
          <el-radio-button label="module_group">模块分组</el-radio-button>
          <el-radio-button label="list">列表视图</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 列表视图 -->
      <div v-if="viewMode === 'list'" class="table-container">
        <el-table :data="filteredTestCases" style="width: 100%; height: 100%" v-loading="loading"
          @selection-change="handleSelectionChange">

          <el-table-column type="selection" width="40" />

          <el-table-column prop="id" label="ID" width="70" align="center">
            <template #default="scope">
              <span class="test-case-id">{{ scope.row.id }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="name" label="用例名称" min-width="250">
            <template #default="scope">
              <div class="test-case-name-simple">
                <div class="test-case-title" @click="viewTestCase(scope.row)">
                  {{ scope.row.title }}
                </div>
                <div class="test-case-desc" v-if="scope.row.description">
                  {{ scope.row.description }}
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="module" label="所属模块" width="150">
            <template #default="scope">
              <el-tag v-if="getModuleName(scope.row)" type="info" size="small" class="module-tag">
                {{ getModuleName(scope.row) }}
              </el-tag>
              <span v-else class="text-muted">未分类</span>
            </template>
          </el-table-column>

          <el-table-column prop="test_type" label="用例类型" width="120">
            <template #default="scope">
              <el-tag v-if="scope.row.test_type" :type="getTestTypeTag(scope.row.test_type)" size="small">
                {{ getTestTypeLabel(scope.row.test_type) }}
              </el-tag>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>

          <el-table-column prop="endpoint_info" label="端点信息" min-width="180" v-show="showEndpointColumn">
            <template #default="scope">
              <div v-if="scope.row.endpoint_info" class="endpoint-info">
                <el-tag size="small" :class="getMethodClass(scope.row.endpoint_info.method)">
                  {{ scope.row.endpoint_info.method }}
                </el-tag>
                <span class="endpoint-path">{{ scope.row.endpoint_info.path }}</span>
              </div>
              <span v-else class="text-muted">场景测试</span>
            </template>
          </el-table-column>

          <el-table-column prop="priority" label="优先级" width="100">
            <template #default="scope">
              <el-tag :type="getPriorityTag(scope.row.priority)" size="small">
                {{ getPriorityLabel(scope.row.priority) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="created_at" label="创建时间" width="150">
            <template #default="scope">
              {{ formatDate(scope.row.created_at) }}
            </template>
          </el-table-column>

          <el-table-column label="操作" width="200" fixed="right">
            <template #default="scope">
              <div class="action-buttons">
                <el-button type="" size="small" @click="viewTestCase(scope.row)">
                  <el-icon>
                    <Edit />
                  </el-icon>
                  编辑
                </el-button>
                <el-button type="primary" size="small" @click="runTestCase(scope.row)"
                  :loading="executingTestCases.has(scope.row.id)" :disabled="executingTestCases.has(scope.row.id)"
                  class="execute-button">
                  <el-icon v-if="!executingTestCases.has(scope.row.id)">
                    <VideoPlay />
                  </el-icon>
                  {{ executingTestCases.has(scope.row.id) ? '执行中...' : '执行用例' }}
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 模块分组视图 -->
      <div v-else-if="viewMode === 'module_group'" class="grouped-container">
        <div v-for="(group, moduleName) in groupedByModule" :key="moduleName" class="module-group">
          <div class="module-header" @click="toggleGroup(moduleName)">
            <div class="module-header-left">
              <el-icon class="expand-icon" :class="{ expanded: expandedGroups.includes(moduleName) }">
                <ArrowRight />
              </el-icon>
              <span class="module-name">{{ moduleName }}</span>
              <el-badge :value="group.length" class="module-badge" type="primary" />
            </div>
          </div>

          <div v-show="expandedGroups.includes(moduleName)" class="module-content">
            <el-table :data="group" style="width: 100%" size="small">
              <el-table-column type="selection" width="40" />

              <el-table-column prop="id" label="ID" width="70" align="center">
                <template #default="scope">
                  <span class="test-case-id">{{ scope.row.id }}</span>
                </template>
              </el-table-column>

              <el-table-column prop="name" label="用例名称" min-width="250">
                <template #default="scope">
                  <div class="test-case-name-simple">
                    <div class="test-case-title" @click="viewTestCase(scope.row)">
                      {{ scope.row.title }}
                    </div>
                    <div class="test-case-desc" v-if="scope.row.description">
                      {{ scope.row.description }}
                    </div>
                  </div>
                </template>
              </el-table-column>

              <el-table-column prop="test_type" label="用例类型" width="120">
                <template #default="scope">
                  <el-tag v-if="scope.row.test_type" :type="getTestTypeTag(scope.row.test_type)" size="small">
                    {{ getTestTypeLabel(scope.row.test_type) }}
                  </el-tag>
                  <span v-else class="text-muted">-</span>
                </template>
              </el-table-column>

              <el-table-column prop="endpoint_info" label="端点信息" min-width="180">
                <template #default="scope">
                  <div v-if="scope.row.endpoint_info" class="endpoint-info">
                    <el-tag size="small" :class="getMethodClass(scope.row.endpoint_info.method)">
                      {{ scope.row.endpoint_info.method }}
                    </el-tag>
                    <span class="endpoint-path">{{ scope.row.endpoint_info.path }}</span>
                  </div>
                  <span v-else class="text-muted">场景测试</span>
                </template>
              </el-table-column>

              <el-table-column prop="priority" label="优先级" width="100">
                <template #default="scope">
                  <el-tag :type="getPriorityTag(scope.row.priority)" size="small">
                    {{ getPriorityLabel(scope.row.priority) }}
                  </el-tag>
                </template>
              </el-table-column>

              <el-table-column prop="created_at" label="创建时间" width="150">
                <template #default="scope">
                  {{ formatDate(scope.row.created_at) }}
                </template>
              </el-table-column>

              <el-table-column label="操作" width="200" fixed="right">
                <template #default="scope">
                  <div class="action-buttons">
                    <el-button type="" size="small" @click="viewTestCase(scope.row)">
                      <el-icon>
                        <Edit />
                      </el-icon>
                      编辑
                    </el-button>
                    <el-button type="primary" size="small" @click="runTestCase(scope.row)"
                      :loading="executingTestCases.has(scope.row.id)" :disabled="executingTestCases.has(scope.row.id)"
                      class="execute-button">
                      <el-icon v-if="!executingTestCases.has(scope.row.id)">
                        <VideoPlay />
                      </el-icon>
                      {{ executingTestCases.has(scope.row.id) ? '执行中...' : '执行用例' }}
                    </el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </div>

      <!-- 端点分组视图 -->
      <div v-else-if="viewMode === 'endpoint_group'" class="grouped-container">
        <div v-for="(endpoint, endpointKey) in groupedByEndpoint" :key="endpointKey" class="endpoint-group">
          <div class="endpoint-header" @click="toggleGroup(endpointKey)">
            <div class="endpoint-header-left">
              <el-icon class="expand-icon" :class="{ expanded: expandedGroups.includes(endpointKey) }">
                <ArrowRight />
              </el-icon>
              <el-tag size="small" :class="getMethodClass(endpoint.method)" class="method-badge">
                {{ endpoint.method }}
              </el-tag>
              <span class="endpoint-path">{{ endpoint.path }}</span>
              <el-badge :value="endpoint.testCases.length" class="module-badge" type="primary" />
            </div>
            <div class="endpoint-header-right">
              <el-tag v-if="endpoint.module" type="info" size="small">{{ endpoint.module }}</el-tag>
              <span v-if="endpoint.summary" class="endpoint-summary">{{ endpoint.summary }}</span>
            </div>
          </div>

          <div v-show="expandedGroups.includes(endpointKey)" class="endpoint-content">
            <el-table :data="endpoint.testCases" style="width: 100%" size="small">
              <el-table-column type="selection" width="40" />

              <el-table-column prop="id" label="ID" width="70" align="center">
                <template #default="scope">
                  <span class="test-case-id">{{ scope.row.id }}</span>
                </template>
              </el-table-column>

              <el-table-column prop="name" label="用例名称" min-width="250">
                <template #default="scope">
                  <div class="test-case-name-simple">
                    <div class="test-case-title" @click="viewTestCase(scope.row)">
                      {{ scope.row.title }}
                    </div>
                    <div class="test-case-desc" v-if="scope.row.description">
                      {{ scope.row.description }}
                    </div>
                  </div>
                </template>
              </el-table-column>

              <el-table-column prop="test_type" label="用例类型" width="120">
                <template #default="scope">
                  <el-tag v-if="scope.row.test_type" :type="getTestTypeTag(scope.row.test_type)" size="small">
                    {{ getTestTypeLabel(scope.row.test_type) }}
                  </el-tag>
                  <span v-else class="text-muted">-</span>
                </template>
              </el-table-column>

              <el-table-column prop="priority" label="优先级" width="100">
                <template #default="scope">
                  <el-tag :type="getPriorityTag(scope.row.priority)" size="small">
                    {{ getPriorityLabel(scope.row.priority) }}
                  </el-tag>
                </template>
              </el-table-column>

              <el-table-column prop="created_at" label="创建时间" width="150">
                <template #default="scope">
                  {{ formatDate(scope.row.created_at) }}
                </template>
              </el-table-column>

              <el-table-column label="操作" width="200" fixed="right">
                <template #default="scope">
                  <div class="action-buttons">
                    <el-button type="" size="small" @click="viewTestCase(scope.row)">
                      <el-icon>
                        <Edit />
                      </el-icon>
                      编辑
                    </el-button>
                    <el-button type="primary" size="small" @click="runTestCase(scope.row)"
                      :loading="executingTestCases.has(scope.row.id)" :disabled="executingTestCases.has(scope.row.id)"
                      class="execute-button">
                      <el-icon v-if="!executingTestCases.has(scope.row.id)">
                        <VideoPlay />
                      </el-icon>
                      {{ executingTestCases.has(scope.row.id) ? '执行中...' : '执行用例' }}
                    </el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
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


    <!-- 导入测试用例对话框 -->
    <el-dialog v-model="showImportDialog" title="导入测试用例" width="500px">
      <div class="import-content">
        <el-upload class="upload-demo" drag action="#" :auto-upload="false" :on-change="handleFileChange"
          accept=".json,.xlsx,.csv">
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            将文件拖到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 JSON、Excel、CSV 格式文件
            </div>
          </template>
        </el-upload>

        <el-form :model="importForm" label-width="100px" style="margin-top: 20px">
          <el-form-item label="导入选项">
            <el-checkbox v-model="importForm.overwrite">覆盖已存在的测试用例</el-checkbox>
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showImportDialog = false">取消</el-button>
          <el-button type="primary" @click="handleImport" :loading="importing">
            开始导入
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 导出测试用例对话框 -->
    <el-dialog v-model="showExportDialog" title="导出测试用例" width="500px">
      <div class="export-content">
        <el-form :model="exportForm" label-width="100px">
          <el-form-item label="导出格式">
            <el-radio-group v-model="exportForm.format">
              <el-radio label="json">JSON</el-radio>
              <el-radio label="xlsx">Excel</el-radio>
              <el-radio label="csv">CSV</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="导出范围">
            <el-radio-group v-model="exportForm.scope">
              <el-radio label="all">全部测试用例</el-radio>
              <el-radio label="filtered">当前筛选结果</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="包含字段">
            <el-checkbox-group v-model="exportForm.fields">
              <el-checkbox label="basic">基本信息</el-checkbox>
              <el-checkbox label="test_data">测试数据</el-checkbox>
              <el-checkbox label="assertions">断言规则</el-checkbox>
              <el-checkbox label="script">脚本内容</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showExportDialog = false">取消</el-button>
          <el-button type="primary" @click="handleExport" :loading="exporting">
            开始导出
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 测试结果详情对话框 -->
    <el-dialog v-model="showResultDialog" title="测试结果详情" width="80%" :close-on-click-modal="false">
      <APITestCaseExecutionDetail v-if="selectedTestResult" :result="selectedTestResult" />
    </el-dialog>

    <!-- 测试运行详情对话框 -->
    <el-dialog v-model="showRunDialog" title="测试运行详情" width="80%" :close-on-click-modal="false">
      <APITestRunDetail v-if="selectedTestRun" :run="selectedTestRun" />
    </el-dialog>

    <!-- API测试执行配置弹框 -->
    <el-dialog v-model="configDialogVisible" title="API测试执行配置" width="600px" :close-on-click-modal="false" :modal="true"
      :append-to-body="true" class="api-config-dialog">
      <div v-if="selectedTestCase" class="config-form">
        <div class="config-section">
          <h4>测试用例信息</h4>
          <div class="test-case-info">
            <p><strong>用例名称：</strong>{{ selectedTestCase.title }}</p>
            <p v-if="selectedTestCase.description"><strong>用例描述：</strong>{{ selectedTestCase.description }}</p>
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
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh,
  Search,
  UploadFilled,
  Connection,
  Setting,
  Warning,
  Edit,
  VideoPlay,
  Delete,
  Close,
  ArrowRight
} from '@element-plus/icons-vue'
import {
  getAPITestCases,
  getAPITestCase,
  createAPITestCase,
  updateAPITestCase,
  deleteAPITestCase,
  executeAPITestCase,
  getAPITestCaseExecutionDetail,
  getAPITestSuiteExecutionDetail,
  getTaskStatus
} from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'
import APICaseEditDetail from '@/components/APICaseEditDetail.vue'
import APITestCaseExecutionDetail from '@/components/APITestCaseExecutionDetail.vue'
import APITestRunDetail from '@/components/APITestRunDetail.vue'
import { useProjectStore } from '@/stores/project'
import dayjs from 'dayjs'

const router = useRouter()

// 状态管理
const loading = ref(false)
const importing = ref(false)
const exporting = ref(false)
const showDetailDialog = ref(false)
const showImportDialog = ref(false)
const showExportDialog = ref(false)
const showResultDialog = ref(false)
const showRunDialog = ref(false)
const selectedTestCase = ref(null)
const selectedTestResult = ref(null)
const selectedTestRun = ref(null)

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
const pollingTasks = ref(new Map()) // 存储正在轮询的任务信息
const pollingIntervals = ref(new Map()) // 存储轮询定时器

// 执行状态跟踪
const executingTestCases = ref(new Set()) // 存储正在执行的测试用例ID

// 过滤和搜索
const moduleFilter = ref('')  // 模块过滤器
const endpointFilter = ref('')  // 端点过滤器
const testCaseTypeFilter = ref('')
const priorityFilter = ref('')
const searchQuery = ref('')

// 视图模式
const viewMode = ref('endpoint_group') // 'endpoint_group'、'module_group' 或 'list'
const expandedGroups = ref([]) // 展开的分组列表（模块或端点）

// 数据
const testCases = ref([])
const selectedTestCases = ref([])

// 使用项目状态管理
const projectStore = useProjectStore()

// 使用store的计算属性，但避免重复初始化
const selectedProject = computed(() => projectStore.currentProject)
const currentProjectId = computed(() => projectStore.currentProjectId)

// 监听视图模式切换，自动展开第一个分组
watch(viewMode, (newMode) => {
  nextTick(() => {
    if (newMode === 'endpoint_group') {
      // 切换到端点分组：展开第一个端点
      const firstEndpointKey = Object.keys(groupedByEndpoint.value)[0]
      if (firstEndpointKey && !expandedGroups.value.includes(firstEndpointKey)) {
        expandedGroups.value = [firstEndpointKey]
      }
    } else if (newMode === 'module_group') {
      // 切换到模块分组：展开第一个模块
      const firstModuleName = Object.keys(groupedByModule.value)[0]
      if (firstModuleName && !expandedGroups.value.includes(firstModuleName)) {
        expandedGroups.value = [firstModuleName]
      }
    }
  })
})

onMounted(async () => {
  try {
    // 直接加载测试用例数据，不重复初始化用户偏好
    // MainLayout已经处理了用户偏好的初始化
    await loadData()
  } catch (error) {
    handleError('初始化失败，请刷新页面重试')
  }
})

onUnmounted(() => {
  // 清理所有轮询
  cleanupPolling()
})

// 分页
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 导入表单
const importForm = reactive({
  overwrite: false
})

// 导出表单
const exportForm = reactive({
  format: 'json',
  scope: 'all',
  fields: ['basic', 'test_data', 'assertions']
})

// 工具函数：获取模块名称
const getModuleName = (testCase) => {
  // 优先使用 tags
  if (testCase.endpoint_info && testCase.endpoint_info.tags && testCase.endpoint_info.tags.length > 0) {
    return testCase.endpoint_info.tags[0]
  }
  
  // 如果没有 tags，尝试从 endpoint path 中提取模块名
  if (testCase.endpoint_info && testCase.endpoint_info.path) {
    const path = testCase.endpoint_info.path
    // 提取第一层路径作为模块名，例如: /user/register -> 用户模块, /order/list -> 订单模块
    const pathParts = path.split('/').filter(p => p && !p.startsWith('{') && !p.match(/^\d+$/))
    if (pathParts.length > 0) {
      const moduleName = pathParts[0]
      // 将英文路径转换为中文描述
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
      return moduleNameMap[moduleName.toLowerCase()] || `${moduleName}模块`
    }
  }
  
  // 最后尝试从用例标题中提取模块信息
  if (testCase.title) {
    const title = testCase.title
    // 匹配常见的模块前缀，如"用户注册"、"订单创建"等
    const modulePatterns = [
      { pattern: /^(用户|会员|账号|账户)/, name: '用户模块' },
      { pattern: /^(订单|下单)/, name: '订单模块' },
      { pattern: /^(商品|产品|货物)/, name: '商品模块' },
      { pattern: /^(支付|付款|缴费)/, name: '支付模块' },
      { pattern: /^(楼栋|楼宇|建筑)/, name: '楼栋模块' },
      { pattern: /^(小区|社区|园区)/, name: '小区模块' },
      { pattern: /^(业主|住户|居民)/, name: '业主模块' },
      { pattern: /^(文件|附件|上传|下载)/, name: '文件模块' }
    ]
    
    for (const { pattern, name } of modulePatterns) {
      if (pattern.test(title)) {
        return name
      }
    }
  }
  
  return '未分类'
}

// 计算属性
const filteredTestCases = computed(() => {
  let filtered = testCases.value

  // 应用模块过滤器
  if (moduleFilter.value) {
    filtered = filtered.filter(tc => {
      const moduleName = getModuleName(tc)
      return moduleName === moduleFilter.value
    })
  }

  // 应用端点过滤器
  if (endpointFilter.value) {
    filtered = filtered.filter(tc => {
      if (tc.endpoint_info) {
        const endpointKey = `${tc.endpoint_info.method} ${tc.endpoint_info.path}`
        return endpointKey === endpointFilter.value
      }
      return false
    })
  }

  // 应用用例类型过滤器
  if (testCaseTypeFilter.value) {
    filtered = filtered.filter(tc => tc.test_type === testCaseTypeFilter.value)
  }

  // 应用优先级过滤器
  if (priorityFilter.value) {
    filtered = filtered.filter(tc => tc.priority === priorityFilter.value)
  }

  // 搜索过滤（用例名称关键字）
  if (searchQuery.value) {
    filtered = filtered.filter(testCase =>
      testCase.title.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      testCase.description?.toLowerCase().includes(searchQuery.value.toLowerCase())
    )
  }

  return filtered
})

// 获取所有模块选项
const moduleOptions = computed(() => {
  const modules = new Set()
  testCases.value.forEach(tc => {
    const moduleName = getModuleName(tc)
    if (moduleName) {
      modules.add(moduleName)
    }
  })
  return Array.from(modules).sort()
})

// 获取所有端点选项
const endpointOptions = computed(() => {
  const endpoints = new Set()
  testCases.value.forEach(tc => {
    if (tc.endpoint_info) {
      const endpointKey = `${tc.endpoint_info.method} ${tc.endpoint_info.path}`
      endpoints.add(endpointKey)
    }
  })
  return Array.from(endpoints).sort()
})

// 动态显示/隐藏表格列
const showEndpointColumn = computed(() => {
  return testCases.value.some(tc => tc.test_case_type === 'endpoint')
})

// 按模块分组的测试用例
const groupedByModule = computed(() => {
  const groups = {}
  filteredTestCases.value.forEach(testCase => {
    const moduleName = getModuleName(testCase)
    if (!groups[moduleName]) {
      groups[moduleName] = []
    }
    groups[moduleName].push(testCase)
  })
  return groups
})

// 按端点分组的测试用例
const groupedByEndpoint = computed(() => {
  const groups = {}
  filteredTestCases.value.forEach(testCase => {
    if (testCase.endpoint_info) {
      const endpointKey = `${testCase.endpoint_info.method} ${testCase.endpoint_info.path}`
      if (!groups[endpointKey]) {
        groups[endpointKey] = {
          method: testCase.endpoint_info.method,
          path: testCase.endpoint_info.path,
          summary: testCase.endpoint_info.summary,
          module: getModuleName(testCase),
          testCases: []
        }
      }
      groups[endpointKey].testCases.push(testCase)
    }
  })
  return groups
})

// 方法
const loadData = async () => {
  try {
    loading.value = true

    // 只有在有项目时才加载测试用例列表
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
      test_type: testCaseTypeFilter.value,
      priority: priorityFilter.value,
      search: searchQuery.value
    }

    const response = await getAPITestCases(projectStore.currentProjectId, params)

    // 使用工具函数处理响应数据
    const { items, total: totalCount } = extractDataFromResponse(response)
    testCases.value = ensureArray(items)
    total.value = totalCount

    // 根据视图模式，默认展开第一个分组
    nextTick(() => {
      if (viewMode.value === 'endpoint_group') {
        // 端点分组模式：展开第一个端点
        const firstEndpointKey = Object.keys(groupedByEndpoint.value)[0]
        if (firstEndpointKey && !expandedGroups.value.includes(firstEndpointKey)) {
          expandedGroups.value = [firstEndpointKey]
        }
      } else if (viewMode.value === 'module_group') {
        // 模块分组模式：展开第一个模块
        const firstModuleName = Object.keys(groupedByModule.value)[0]
        if (firstModuleName && !expandedGroups.value.includes(firstModuleName)) {
          expandedGroups.value = [firstModuleName]
        }
      }
    })

  } catch (error) {
    handleError('加载测试用例失败')
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

// 处理测试用例更新
const handleTestCaseUpdate = async (updatedTestCase) => {
  try {
    // 刷新列表
    loadTestCases()
  } catch (error) {
    handleError(error.message || '未知错误', '刷新失败')
  }
}

const viewTestCase = async (testCase) => {
  try {
    // 使用getAPITestCase获取完整的测试用例详情
    const response = await getAPITestCase(projectStore.currentProjectId, testCase.id)
    
    if (response.success) {
      selectedTestCase.value = response.data
      showDetailDialog.value = true
    } else {
      handleError(response.message, '获取测试用例详情失败')
    }
  } catch (error) {
    console.error('获取测试用例详情失败:', error)
    handleError('获取测试用例详情失败')
  }
}

const runTestCase = async (testCase) => {
  // 防止重复执行
  if (executingTestCases.value.has(testCase.id)) {
    return
  }

  // 验证项目ID
  if (!projectStore.currentProjectId) {
    ElMessage.error('请先选择一个项目')
    return
  }

  try {
    // 加载环境列表
    await loadEnvironments()

    // 显示配置弹框
    selectedTestCase.value = testCase
    configDialogVisible.value = true
  } catch (error) {
    handleError(error.message || '未知错误', '执行失败')
  }
}

// 确认执行测试用例
const confirmRunTestCase = async () => {
  try {
    if (!selectedTestCase.value) return

    // 检查是否选择了环境
    if (!selectedEnvironment.value) {
      ElMessage.warning('请选择一个测试环境')
      return
    }

    // 添加到执行状态集合
    executingTestCases.value.add(selectedTestCase.value.id)

    // 构建执行选项，包含环境配置
    const executionData = {
      environment_id: selectedEnvironment.value.id,
      ...executionOptions.value
    }

    // 调用执行API，传递配置选项
    const result = await executeAPITestCase(projectStore.currentProjectId, selectedTestCase.value.id, executionData)

    if (result && result.success && result.data) {
      const { execution_id, task_id, execution_name } = result.data

      ElMessage.success(`测试用例执行已启动: ${execution_name}`)

      // 开始轮询任务状态，传递测试用例ID用于完成后清理状态
      startTaskPolling(task_id, execution_id, selectedTestCase.value.title, selectedTestCase.value.id)

    } else {
      ElMessage.error(`执行测试用例失败: ${result?.message || '未知错误'}`)
      // 执行失败时移除执行状态
      executingTestCases.value.delete(selectedTestCase.value.id)
    }
  } catch (error) {
    handleError(error.message || '未知错误', '执行失败')
    // 执行失败时移除执行状态
    executingTestCases.value.delete(selectedTestCase.value.id)
  } finally {
    // 关闭弹框
    configDialogVisible.value = false
    selectedTestCase.value = null
    selectedEnvironment.value = null
  }
}

// 加载项目环境列表
const loadEnvironments = async () => {
  if (!projectStore.currentProject?.id) return

  try {
    loadingEnvironments.value = true

    const params = {
      category: 'api'  // 只获取API测试环境
    }

    const response = await getProjectEnvironments(projectStore.currentProject.id, params)

    if (response.success) {
      // 根据实际返回的数据结构处理，只显示启用的环境
      const allEnvironments = response.data.items || []
      environments.value = allEnvironments.filter(env => env.is_active === true)
      // 如果有环境且没有选中环境，默认选择第一个
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

// 处理文件选择
const handleFileChange = (file) => {
  // 文件选择处理
}

// 处理导入
const handleImport = async () => {
  try {
    importing.value = true
    // 注意：导入功能在后端未实现
    ElMessage.warning('导入功能暂未实现，请联系管理员')
  } catch (error) {
    handleError(error.message || '未知错误', '导入失败')
  } finally {
    importing.value = false
  }
}

// 处理导出
const handleExport = async () => {
  try {
    exporting.value = true
    // 注意：导出功能在后端未实现
    ElMessage.warning('导出功能暂未实现，请联系管理员')
  } catch (error) {
    handleError(error.message || '未知错误', '导出失败')
  } finally {
    exporting.value = false
  }
}


// 工具方法
const getMethodClass = (method) => {
  return methodMap[method]?.class || 'method-default'
}

const getTestCaseTypeLabel = (type) => {
  return testCaseTypeMap[type]?.label || type
}

const getTestCaseTypeTag = (type) => {
  return testCaseTypeMap[type]?.tag || 'info'
}

const getPriorityLabel = (priority) => {
  return priorityMap[priority]?.label || priority
}

const getPriorityTag = (priority) => {
  return priorityMap[priority]?.tag || 'info'
}

// 获取测试类型标签
const getTestTypeLabel = (type) => {
  return testTypeMap[type]?.label || type
}

const getTestTypeTag = (type) => {
  return testTypeMap[type]?.tag || 'info'
}

// 切换模块分组展开/折叠
// 切换分组展开/折叠（适用于模块和端点）
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

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 统一错误处理
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

const testCaseTypeMap = {
  'endpoint': { label: '端点测试', tag: 'primary' },
  'scenario': { label: '场景测试', tag: 'success' }
}

const priorityMap = {
  'low': { label: '低', tag: 'info' },
  'medium': { label: '中', tag: 'warning' },
  'high': { label: '高', tag: 'danger' },
  'critical': { label: '紧急', tag: 'danger' }
}

// 测试类型映射（test_type: 正向/负向/边界/安全）
const testTypeMap = {
  'positive': { label: '正向用例', tag: 'success' },
  'negative': { label: '负向用例', tag: 'danger' },
  'boundary': { label: '边界测试', tag: 'warning' },
  'security': { label: '安全测试', tag: 'info' }
}

// 数据处理工具函数
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
const handleSelectionChange = (selection) => {
  selectedTestCases.value = selection
}

const clearSelection = () => {
  selectedTestCases.value = []
}

// 批量删除
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTestCases.value.length} 个测试用例吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 批量删除
    for (const testCase of selectedTestCases.value) {
      await deleteAPITestCase(projectStore.currentProjectId, testCase.id)
    }

    ElMessage.success(`成功删除 ${selectedTestCases.value.length} 个测试用例`)
    clearSelection()
    loadTestCases()
  } catch (error) {
    if (error !== 'cancel') {
      handleError(error.message || '未知错误', '批量删除失败')
    }
  }
}



const goToProjectManagement = () => {
  router.push('/project/list')
}


// ============ 任务轮询相关方法 ============

// 开始轮询任务状态
const startTaskPolling = (taskId, testRunId, testCaseName, testCaseId) => {
  // 存储任务信息
  pollingTasks.value.set(taskId, {
    taskId,
    testRunId,
    testCaseName,
    testCaseId,
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
        // 任务完成
        ElMessage.success(`测试任务完成: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)

        // 清理执行状态
        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }

        // 获取测试结果并显示
        await loadAndShowTestResults(taskInfo.testRunId)

      } else if (['FAILED', 'FAILURE'].includes(statusUpper)) {
        // 任务失败
        ElMessage.error(`测试任务失败: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)

        // 清理执行状态
        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }

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
        ElMessage.error(`检查任务状态失败次数过多，停止监控: ${taskInfo.testCaseName}`)
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
  if (taskInfo && taskInfo.testCaseId) {
    executingTestCases.value.delete(taskInfo.testCaseId)
  }

  // 清除任务信息
  pollingTasks.value.delete(taskId)
}

// 加载并显示测试结果
const loadAndShowTestResults = async (testRunId) => {
  try {
    // 验证项目ID
    if (!projectStore.currentProjectId) {
      ElMessage.warning('当前项目ID为空，无法加载测试结果')
      return
    }

    // 使用获取API测试用例执行详情接口
    const testExecutionResult = await getAPITestCaseExecutionDetail(projectStore.currentProjectId, testRunId)

    // 检查响应格式，支持两种格式：统一响应格式和直接数据格式
    if (testExecutionResult && testExecutionResult.success && testExecutionResult.data) {
      // 统一响应格式：{ success: true, data: {...}, message: "..." }
      selectedTestResult.value = testExecutionResult.data
      showResultDialog.value = true
    } else if (testExecutionResult && (testExecutionResult.id || testExecutionResult.name)) {
      // 直接数据格式：直接返回测试执行记录数据
      selectedTestResult.value = testExecutionResult
      showResultDialog.value = true
    } else {
      ElMessage.warning('无法获取测试执行详情')
    }
  } catch (error) {
    handleError(error.message || '未知错误', '加载测试执行详情失败')
  }
}

// 组件卸载时清理所有轮询
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
.test-cases-container {
  margin: 0 auto;
}


.test-cases-card {
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

/* 当前环境卡片样式 */
.current-environment-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #ffffff;
  border: 1px solid #e1e5e9;
  border-radius: 8px;
  padding: 10px 14px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
  transition: all 0.3s ease;
  min-width: 220px;
  max-width: 320px;
}

.current-environment-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  transform: translateY(-1px);
  border-color: #409eff;
}

.environment-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.environment-icon {
  font-size: 16px;
  color: #409eff;
  flex-shrink: 0;
}

.environment-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.environment-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.environment-name {
  font-size: 13px;
  font-weight: 500;
  color: #1a1a1a;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.environment-url {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  background: #f8f9fa;
  border-radius: 4px;
  border: 1px solid #e8eaed;
  max-width: 100%;
}

.url-icon {
  font-size: 12px;
  color: #67c23a;
  flex-shrink: 0;
}

.url-text {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

/* 无环境占位符样式 */
.no-environment-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #f8f9fa;
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  color: #6b7280;
  transition: all 0.3s ease;
  cursor: pointer;
  min-width: 220px;
  max-width: 320px;
}

.no-environment-placeholder:hover {
  background: #f0f9ff;
  border-color: #409eff;
  color: #409eff;
}

.placeholder-icon {
  font-size: 16px;
  color: #9ca3af;
}

.no-environment-placeholder:hover .placeholder-icon {
  color: #409eff;
}

.placeholder-text {
  font-size: 13px;
  font-weight: 500;
}

.card-header-right {
  display: flex;
  gap: 10px;
  align-items: center;
}



.test-case-name-text {
  font-weight: 500;
  color: #409eff;
  cursor: pointer;
}

.test-case-name-text:hover {
  text-decoration: underline;
}



/* 项目选择提示卡片 */
.project-selection-card {
  text-align: center;
  padding: 60px 20px;
}

.project-selection-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}

.project-selection-icon {
  font-size: 64px;
  color: #c0c4cc;
}

.project-selection-content h3 {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.project-selection-content p {
  font-size: 16px;
  color: #909399;
  margin: 0;
}

/* 底部操作容器 */
.bottom-actions-container {
  flex-shrink: 0;
  background: #fff;
  height: 50px;
}

.pagination-container {
  padding: 10px;
  text-align: center;
}

/* 创建者名称样式 */
.creator-name {
  font-weight: 500;
  color: #606266;
  font-size: 13px;
}

/* 测试用例ID样式 */
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

/* 简化的测试用例名称样式 */
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

/* 端点信息样式 */
.endpoint-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.endpoint-info .el-tag {
  width: fit-content;
  align-self: flex-start;
}

.endpoint-path {
  font-weight: 500;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.method-get {
  background-color: #f0f9eb;
  color: #67c23a;
  border-color: #c2e7b0;
}

.method-post {
  background-color: #f0f2ff;
  color: #409eff;
  border-color: #b3d8ff;
}

.method-put {
  background-color: #fdf6ec;
  color: #e6a23c;
  border-color: #f5dab1;
}

.method-delete {
  background-color: #fef0f0;
  color: #f56c6c;
  border-color: #fbc4c4;
}

.method-patch {
  background-color: #f0f9ff;
  color: #409eff;
  border-color: #b3d8ff;
}

.method-default {
  background-color: #f4f4f5;
  color: #909399;
  border-color: #d3d4d6;
}

/* 表格容器样式 */
.table-container {
  height: calc(100vh - 230px);
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

.text-muted {
  color: #909399;
  font-style: italic;
  font-size: 12px;
}
















.upload-demo {
  text-align: center;
}

.el-upload__tip {
  color: #909399;
  font-size: 12px;
  margin-top: 10px;
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

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
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

.environment-url {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  line-height: 1.2;
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

/* 响应式设计 */
@media (max-width: 768px) {
  .test-cases-container {
    padding: 10px;
  }

  .page-header h1 {
    font-size: 24px;
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

  .current-environment-card {
    min-width: auto;
    width: 100%;
  }

  .no-environment-placeholder {
    min-width: auto;
    width: 100%;
  }

  .environment-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .environment-url {
    max-width: none;
  }

  .url-text {
    max-width: none;
  }

  .card-header-filters {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    width: 100%;
  }

  .card-header-filters .el-select,
  .card-header-filters .el-input {
    width: 100% !important;
  }


  .batch-buttons {
    justify-content: center;
    flex-wrap: wrap;
  }
}

/* 操作按钮样式 */
.action-buttons {
  display: flex;
  flex-wrap: wrap;
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


.execute-button {
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execute-button .el-loading-spinner {
  width: 14px;
  height: 14px;
}

.execute-button .el-loading-spinner .circular {
  width: 14px;
  height: 14px;
}

/* 视图模式切换器样式 */
.view-mode-switch {
  margin-bottom: 15px;
  display: flex;
  justify-content: flex-end;
  padding: 0 10px;
}

/* 模块标签样式 */
.module-tag {
  font-weight: 500;
}

/* 分组容器样式 */
.grouped-container {
  height: calc(100vh - 280px);
  overflow-y: auto;
  padding: 10px;
}

/* 模块分组样式 */
.module-group,
.endpoint-group {
  margin-bottom: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.06);
  transition: all 0.3s ease;
}

.module-group:hover,
.endpoint-group:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* 模块/端点头部样式 */
.module-header,
.endpoint-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  cursor: pointer;
  user-select: none;
  transition: all 0.3s ease;
}

.module-header:hover,
.endpoint-header:hover {
  background: linear-gradient(135deg, #5568d3 0%, #6a4191 100%);
}

/* 端点头部特殊样式 */
.endpoint-header {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  padding: 12px 20px;
}

.endpoint-header:hover {
  background: linear-gradient(135deg, #e082ea 0%, #e4465b 100%);
}

.module-header-left,
.endpoint-header-left,
.endpoint-title {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.endpoint-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.expand-icon {
  font-size: 16px;
  color: #fff;
  transition: transform 0.3s ease;
}

/* 端点信息样式 */
.endpoint-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}

.endpoint-main {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}

.endpoint-path {
  font-family: 'Courier New', monospace;
}

.endpoint-sub {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  opacity: 0.95;
}

.endpoint-summary {
  color: rgba(255, 255, 255, 0.9);
}

/* 方法徽章样式（用于端点分组） */
.method-badge {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: bold;
  background: rgba(255, 255, 255, 0.25);
  color: white;
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
  color: #667eea;
  font-weight: 600;
  border: none;
}

/* 模块内容样式 */
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

/* 优化用例类型标签样式 */
.el-tag.is-success {
  background-color: #f0f9eb;
  border-color: #c2e7b0;
  color: #67c23a;
}

.el-tag.is-danger {
  background-color: #fef0f0;
  border-color: #fbc4c4;
  color: #f56c6c;
}

.el-tag.is-warning {
  background-color: #fdf6ec;
  border-color: #f5dab1;
  color: #e6a23c;
}

.el-tag.is-info {
  background-color: #f4f4f5;
  border-color: #d3d4d6;
  color: #909399;
}

/* 响应式适配 */
@media (max-width: 768px) {
  .module-header {
    padding: 12px 15px;
  }

  .module-name {
    font-size: 14px;
  }

  .grouped-container {
    padding: 5px;
  }
}
</style>
