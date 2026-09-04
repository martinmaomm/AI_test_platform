<template>
  <div class="test-runs-container" v-if="selectedProject">
    <!-- 统计概览 -->
    <div class="stats-overview">
      <div class="stat-card total">
        <div class="stat-icon">
          <el-icon>
            <Document />
          </el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-number">{{ stats.totalRuns }}</div>
          <div class="stat-label">总执行次数</div>
        </div>
      </div>

      <div class="stat-card success">
        <div class="stat-icon">
          <el-icon>
            <Check />
          </el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-number">{{ stats.passedRuns }}</div>
          <div class="stat-label">成功次数</div>
        </div>
      </div>

      <div class="stat-card failed">
        <div class="stat-icon">
          <el-icon>
            <Close />
          </el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-number">{{ stats.failedRuns }}</div>
          <div class="stat-label">失败次数</div>
        </div>
      </div>

      <div class="stat-card incomplete">
        <div class="stat-icon">
          <el-icon>
            <Document />
          </el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-number">{{ stats.incompleteRuns }}</div>
          <div class="stat-label">验证未完成</div>
        </div>
      </div>

      <div class="stat-card rate">
        <div class="stat-icon">
          <el-icon>
            <DataBoard />
          </el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-number">{{ stats.successRate }}%</div>
          <div class="stat-label">成功率</div>
        </div>
      </div>
    </div>

    <!-- 执行记录表格 -->
    <el-card class="table-card" shadow="hover">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedRuns.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedRuns.length }} 个执行记录</span>
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
      <div v-if="selectedRuns.length === 0" class="card-header">
        <div class="card-header-left">
          <h3>执行记录列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select v-model="filters.exec_type" placeholder="执行类型" clearable style="width: 120px;">
              <el-option label="全部" value="" />
              <el-option label="单用例" value="case" />
              <el-option label="套件" value="suite" />
            </el-select>
            <el-select v-model="filters.status" placeholder="执行状态" clearable style="width: 140px;">
              <el-option label="全部" value="" />
              <el-option label="待执行" value="pending" />
              <el-option label="执行中" value="running" />
              <el-option label="执行通过" value="passed" />
              <el-option label="验证未完成" value="incomplete" />
              <el-option label="执行失败" value="failed" />
              <el-option label="执行错误" value="error" />
              <el-option label="已停止" value="stopped" />
            </el-select>
            <el-select v-model="filters.trigger_type" placeholder="触发方式" clearable style="width: 120px;">
              <el-option label="全部" value="" />
              <el-option label="手动触发" value="manual" />
              <el-option label="计划任务" value="schedule" />
              <el-option label="API调用" value="api" />
              <el-option label="LLM执行" value="llm" />
              <el-option label="Jenkins" value="jenkins" />
              <el-option label="CI/CD" value="ci_cd" />
            </el-select>
            <el-date-picker v-model="filters.dateRange" type="daterange" range-separator="至" start-placeholder="开始日期"
              end-placeholder="结束日期" style="width: 280px;" />
          </div>
          <el-button @click="refreshData" :icon="Refresh">
            刷新
          </el-button>
        </div>
      </div>

      <el-table :data="filteredTestRuns" v-loading="loading" class="execution-table" :row-class-name="getRowClassName"
        @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="40" />

        <el-table-column prop="id" label="记录ID" width="80" align="center">
          <template #default="{ row }">
            <span class="execution-id">#{{ row.id }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="exec_type" label="执行类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.exec_type === 'case' ? 'primary' : 'success'" size="small">
              {{ row.exec_type === 'case' ? '单用例' : '套件' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="执行名称" min-width="250">
          <template #default="{ row }">
            <div class="execution-name">
              <div class="test-case-title" @click.stop="viewDetails(row)">
                {{ row.name }}
              </div>
              <div class="name-desc" v-if="row.description">{{ row.description }}</div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small" class="status-tag">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="trigger_type" label="触发方式" width="100" align="center">
          <template #default="{ row }">
            <span class="trigger-type">{{ getTriggerTypeText(row.trigger_type) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="duration" label="执行时长" width="120" align="center">
          <template #default="{ row }">
            <span class="duration-text">{{ formatDuration(row.duration) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="start_time" label="开始时间" width="180">
          <template #default="{ row }">
            <div class="time-info">
              <div class="time-date">{{ formatDateTime(row.start_time) }}</div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="executor_name" label="执行者" width="100" align="center">
          <template #default="{ row }">
            <span class="executor-name">{{ row.executor_name }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="120" fixed="right" align="center">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button size="small" @click.stop="viewDetails(row)" class="view-btn">
                查看详情
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-container">
        <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[10, 20, 50, 100]"
          :total="totalRuns" layout="total, sizes, prev, pager, next, jumper" @size-change="handleSizeChange"
          @current-change="handleCurrentChange" />
      </div>
    </el-card>

    <!-- 详情对话框 -->
    <el-dialog v-model="detailDialogVisible" title="执行详情" width="90%"
      :top="detailDialogExpanded ? '4vh' : '12vh'" :close-on-click-modal="false" :show-close="true"
      :style="detailDialogStyle" class="execution-detail-dialog" @closed="resetDetailDialogSize">
      <template #header>
        <div class="execution-dialog-header">
          <span class="execution-dialog-title">执行详情</span>
          <button
            type="button"
            class="execution-dialog-resize-handle"
            :title="detailDialogExpanded ? '恢复默认窗口' : '放大窗口'"
            :aria-label="detailDialogExpanded ? '恢复默认窗口' : '放大窗口'"
            @click="toggleDetailDialogSize"
          >
            {{ detailDialogExpanded ? '↙' : '⛶' }}
          </button>
        </div>
      </template>

      <!-- 套件执行详情 -->
      <WebUITestSuiteExecutionDetail v-if="selectedRun && selectedRun.exec_type === 'suite'" :execution="selectedRun"
        :visible="detailDialogVisible" @close="detailDialogVisible = false" />

      <!-- 单用例执行详情 -->
      <WebUITestCaseExecutionDetail v-else-if="selectedRun && selectedRun.exec_type === 'case'" :execution="selectedRun"
        :visible="detailDialogVisible" @close="detailDialogVisible = false" />
    </el-dialog>
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
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Check, Close, DataBoard, Refresh, Delete } from '@element-plus/icons-vue'
import { getTestExecutions, getTestExecutionCases, getWebUITestExecutionStatistics, getWebUITestCaseExecution, getWebUITestSuiteExecution, deleteTestExecution } from '@/api/webTesting'
import WebUITestSuiteExecutionDetail from '@/components/WebUITestSuiteExecutionDetail.vue'
import WebUITestCaseExecutionDetail from '@/components/WebUITestCaseExecutionDetail.vue'
import dayjs from 'dayjs'
import { useProjectStore } from '@/stores/project'
const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const totalRuns = ref(0)
const detailDialogVisible = ref(false)
const detailDialogExpanded = ref(false)
const selectedRun = ref(null)
const router = useRouter()
const projectStore = useProjectStore()


// 计算属性
const selectedProject = computed(() => projectStore.currentProject)
const currentProject = computed(() => projectStore.currentProject)
const detailDialogStyle = computed(() => ({
  height: detailDialogExpanded.value ? '88vh' : '72vh'
}))

const toggleDetailDialogSize = () => {
  detailDialogExpanded.value = !detailDialogExpanded.value
}

const resetDetailDialogSize = () => {
  detailDialogExpanded.value = false
}

// 统计数据
const stats = reactive({
  totalRuns: 0,
  passedRuns: 0,
  incompleteRuns: 0,
  failedRuns: 0,
  successRate: 0
})

// 存储所有执行记录用于统计
const allExecutions = ref([])

// 筛选条件
const filters = reactive({
  exec_type: '',
  status: '',
  trigger_type: '',
  dateRange: null
})

// 测试执行记录数据
const testRuns = ref([])

// 批量选择相关
const selectedRuns = ref([])

// 计算属性
const filteredTestRuns = computed(() => {
  let filtered = testRuns.value

  if (filters.status) {
    filtered = filtered.filter(run => run.status === filters.status)
  }


  if (filters.dateRange && filters.dateRange.length === 2) {
    const [start, end] = filters.dateRange
    filtered = filtered.filter(run => {
      const runDate = new Date(run.startTime)
      return runDate >= start && runDate <= end
    })
  }

  return filtered
})

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 统一错误处理
const handleError = (error, defaultMessage = '操作失败') => {
  const message = error?.message || error || defaultMessage
  ElMessage.error(message)
}

// 状态映射
const statusMap = {
  pending: { type: 'info', text: '待执行' },
  running: { type: 'warning', text: '执行中' },
  passed: { type: 'success', text: '执行通过' },
  incomplete: { type: 'warning', text: '验证未完成' },
  failed: { type: 'danger', text: '执行失败' },
  error: { type: 'danger', text: '执行错误' },
  stopped: { type: 'info', text: '已停止' }
}

// 触发方式映射
const triggerTypeMap = {
  manual: '手动触发',
  schedule: '计划任务',
  api: 'API调用',
  llm: 'LLM执行',
  jenkins: 'Jenkins',
  ci_cd: 'CI/CD'
}

// 获取测试名称
const getTestName = (item) => {
  return item.name || item.test_case_name || '未命名测试'
}

// 计算用例数量
const calculateCaseCounts = (status) => {
  return {
    passedCases: status === 'completed' ? 1 : 0,
    failedCases: status === 'failed' ? 1 : 0
  }
}

const getStatusType = (status) => {
  return statusMap[status]?.type || 'info'
}

const getStatusText = (status) => {
  return statusMap[status]?.text || status
}

const getTriggerTypeText = (triggerType) => {
  return triggerTypeMap[triggerType] || triggerType
}

// 格式化执行时长
const formatDuration = (duration) => {
  if (!duration) return '0s'
  if (typeof duration === 'number') {
    const hours = Math.floor(duration / 3600)
    const minutes = Math.floor((duration % 3600) / 60)
    const seconds = Math.floor(duration % 60)

    if (hours > 0) {
      return `${hours}h ${minutes}m ${seconds}s`
    } else if (minutes > 0) {
      return `${minutes}m ${seconds}s`
    } else {
      return `${seconds}s`
    }
  }
  return duration
}

// 转换后端状态到前端状态
const convertStatus = (backendStatus) => {
  const statusMap = {
    'completed': 'success',
    'failed': 'failed',
    'running': 'running',
    'pending': 'running',
    'success': 'success'
  }
  return statusMap[backendStatus] || backendStatus
}

// 格式化日期时间
const formatDateTime = (dateTime) => {
  if (!dateTime) return '--'
  return dayjs(dateTime).format('YYYY-MM-DD HH:mm:ss')
}

// 获取表格行样式类名
const getRowClassName = ({ row }) => {
  if (row.status === 'passed') return 'success-row'
  if (row.status === 'incomplete') return 'running-row'
  if (row.status === 'failed' || row.status === 'error') return 'failed-row'
  if (row.status === 'running') return 'running-row'
  if (row.status === 'pending') return 'pending-row'
  return ''
}


// 加载统计数据（使用专门的统计接口）
const loadStatistics = async () => {
  try {
    const response = await getWebUITestExecutionStatistics(projectStore.currentProjectId)

    if (response && response.success && response.data) {
      const statsData = response.data

      // 更新统计数据（后端返回 passed 表示成功次数，非 completed）
      stats.totalRuns = statsData.total || 0
      stats.passedRuns = statsData.passed || 0
      stats.incompleteRuns = statsData.incomplete || 0
      stats.failedRuns = statsData.failed || 0
      stats.successRate = statsData.success_rate || 0
    }
  } catch (error) {
    console.error('加载统计数据失败:', error)
  }
}

// 加载测试执行记录
const loadTestRuns = async () => {
  try {
    loading.value = true

    // 构建查询参数
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }

    // 添加筛选条件
    if (filters.exec_type) params.exec_type = filters.exec_type
    if (filters.status) params.status = filters.status
    if (filters.trigger_type) params.trigger_type = filters.trigger_type

    const response = await getTestExecutions(projectStore.currentProjectId, params)

    if (response.success) {
      // 处理新的分页数据结构
      const dataItems = response.data.items || []

      testRuns.value = dataItems.map(item => {
        return {
          id: item.id,
          exec_type: item.exec_type,
          name: item.name,
          status: item.status,
          trigger_type: item.trigger_type,
          executor_name: item.executor_name,
          environment_name: item.environment_name,
          browser: item.browser,
          start_time: item.start_time,
          end_time: item.end_time,
          duration: item.duration,
          execution_duration: item.execution_duration,
          pass_rate: item.pass_rate,
          log_path: item.log_path,
          report_path: item.report_path,
          created_at: item.created_at,
          updated_at: item.updated_at
        }
      })

      // 设置总数 - 使用新的分页结构
      totalRuns.value = response.data.pagination?.total || testRuns.value.length
    } else {
      handleError(response.message, '加载数据失败')
    }
  } catch (error) {
    console.error('加载测试执行记录失败:', error)
    handleError('加载数据失败')
  } finally {
    loading.value = false
  }
}

// 计算执行时长
const calculateDuration = (startTime, endTime) => {
  const start = new Date(startTime)
  const end = new Date(endTime)
  const diffMs = end - start
  const diffSeconds = Math.floor(diffMs / 1000)
  const diffMinutes = Math.floor(diffSeconds / 60)
  const diffHours = Math.floor(diffMinutes / 60)

  if (diffHours > 0) {
    return `${diffHours}h ${diffMinutes % 60}m`
  } else if (diffMinutes > 0) {
    return `${diffMinutes}m ${diffSeconds % 60}s`
  } else {
    return `${diffSeconds}s`
  }
}

const viewDetails = async (row) => {
  try {
    // 根据执行类型调用不同的API
    let response
    if (row.exec_type === 'case') {
      response = await getWebUITestCaseExecution(projectStore.currentProjectId, row.id)
    } else if (row.exec_type === 'suite') {
      response = await getWebUITestSuiteExecution(projectStore.currentProjectId, row.id)
    } else {
      // 未知执行类型，显示错误
      ElMessage.error('未知的执行类型')
      return
    }

    // API调用成功，直接使用响应数据（Django REST Framework直接返回序列化器数据）
    console.log('API响应数据:', response)
    console.log('allure_report_url字段:', response.allure_report_url)

    // 确保正确合并数据
    selectedRun.value = {
      ...row,  // 先使用原始行数据
      ...response  // 然后用API响应数据覆盖
    }

    console.log('合并后的selectedRun:', selectedRun.value)
    console.log('合并后的allure_report_url:', selectedRun.value.allure_report_url)

    detailDialogVisible.value = true
  } catch (error) {
    console.error('获取执行详情失败:', error)
    ElMessage.error('获取执行详情失败')
    selectedRun.value = row
    detailDialogVisible.value = true
  }
}


const refreshData = () => {
  loadTestRuns()
}

// 批量操作相关方法
const handleSelectionChange = (selection) => {
  selectedRuns.value = selection
}

const clearSelection = () => {
  selectedRuns.value = []
}

const batchDelete = async () => {
  if (selectedRuns.value.length === 0) {
    ElMessage.warning('请选择要删除的执行记录')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedRuns.value.length} 个执行记录吗？删除后无法恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 批量删除
    let deletedCount = 0
    for (const run of selectedRuns.value) {
      try {
        const response = await deleteTestExecution(projectStore.currentProjectId, run.id)
        if (response.success) {
          deletedCount++
        }
      } catch (error) {
        console.error(`删除执行记录 ${run.id} 失败:`, error)
      }
    }

    if (deletedCount > 0) {
      ElMessage.success(`成功删除 ${deletedCount} 个执行记录`)
      clearSelection()
      await loadTestRuns()
      await loadStatistics()
    } else {
      ElMessage.error('删除失败，请稍后重试')
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error('批量删除失败')
    }
  }
}


// 更新统计数据（现在由后端统计接口提供，此函数保留用于兼容性）
const updateStats = () => {
  // 统计数据现在由后端统计接口直接提供，无需前端计算
}

// 分页处理
const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  loadTestRuns()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadTestRuns()
}

onMounted(() => {
  loadStatistics() // 先加载统计数据
  loadTestRuns() // 再加载分页数据
})
</script>

<style scoped>
.test-runs-container {
  padding: 10px;
  max-width: 1600px;
  margin: 0 auto;
  background: #f8fafc;
  min-height: 100vh;
}

/* 统计卡片样式 */
.stats-overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 12px;
}

.stat-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  transition: all 0.3s ease;
  border: 1px solid #f1f5f9;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #667eea, #764ba2);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
}

.stat-card.total::before {
  background: linear-gradient(90deg, #409eff, #66b1ff);
}

.stat-card.success::before {
  background: linear-gradient(90deg, #67c23a, #85ce61);
}

.stat-card.failed::before {
  background: linear-gradient(90deg, #f56c6c, #f78989);
}

.stat-card.rate::before {
  background: linear-gradient(90deg, #e6a23c, #ebb563);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
}

.stat-card.total .stat-icon {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.stat-card.success .stat-icon {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.stat-card.failed .stat-icon {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.stat-card.rate .stat-icon {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.stat-info {
  flex: 1;
  min-width: 0;
}

.stat-number {
  font-size: 24px;
  font-weight: 700;
  color: #1a202c;
  line-height: 1;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 13px;
  color: #64748b;
  font-weight: 500;
  line-height: 1.2;
}

/* 卡片头部样式 */
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

/* 表格样式 */
.table-card {
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  overflow: hidden;
}

.execution-table {
  --el-table-border-color: #e2e8f0;
  --el-table-header-bg-color: #f8fafc;
  --el-table-row-hover-bg-color: #f8fafc;
}

.execution-table :deep(.el-table__header) {
  background: #f8fafc;
}

.execution-table :deep(.el-table__header th) {
  background: #f8fafc;
  color: #374151;
  font-weight: 600;
  font-size: 14px;
  border-bottom: 2px solid #e2e8f0;
}

.execution-table :deep(.el-table__body tr) {
  transition: all 0.2s ease;
}

.execution-table :deep(.el-table__body tr:hover) {
  background: #f8fafc !important;
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.execution-table :deep(.el-table__body tr:hover td) {
  background: #f8fafc !important;
}

.execution-table :deep(.success-row) {
  background: #f0f9ff;
}

.execution-table :deep(.success-row td) {
  background: #f0f9ff !important;
}

.execution-table :deep(.failed-row) {
  background: #fef2f2;
}

.execution-table :deep(.failed-row td) {
  background: #fef2f2 !important;
}

.execution-table :deep(.running-row) {
  background: #fffbeb;
}

.execution-table :deep(.running-row td) {
  background: #fffbeb !important;
}

.execution-table :deep(.pending-row) {
  background: #f8fafc;
}

.execution-table :deep(.pending-row td) {
  background: #f8fafc !important;
}

/* 表格内容样式 */
.trigger-type {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.executor-name {
  font-size: 13px;
  color: #374151;
  font-weight: 500;
}

/* 表格内容样式 */
.execution-id {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: #64748b;
  background: #f1f5f9;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
}

.execution-name {
  display: flex;
  flex-direction: column;
  gap: 4px;
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

.name-desc {
  font-size: 12px;
  color: #64748b;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}


.status-tag {
  font-weight: 500;
  border-radius: 8px;
}

.duration-text {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  color: #64748b;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}

.time-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.time-date {
  font-size: 13px;
  color: #374151;
  font-weight: 500;
}

.action-buttons {
  display: flex;
  justify-content: center;
}

.view-btn {
  border-radius: 8px;
  font-weight: 500;
}

/* 分页样式 */
.pagination-container {
  padding: 20px;
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

/* 详情对话框样式 */
.execution-detail-dialog {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 88vh;
  min-height: min(560px, 80vh);
  max-height: 92vh;
  overflow: hidden;
}

.execution-detail-dialog :deep(.el-dialog__header) {
  flex-shrink: 0;
}

.execution-dialog-header {
  align-items: center;
  display: flex;
  min-height: 24px;
  padding-right: 38px;
}

.execution-dialog-title {
  color: #303133;
  font-size: 18px;
  line-height: 24px;
}

.execution-dialog-resize-handle {
  align-items: center;
  background: rgba(64, 158, 255, 0.9);
  border: 0;
  border-radius: 4px;
  color: #ffffff;
  cursor: pointer;
  display: flex;
  font-size: 16px;
  height: 24px;
  justify-content: center;
  line-height: 1;
  margin-left: auto;
  padding: 0;
  user-select: none;
  width: 28px;
}

.execution-dialog-resize-handle:hover {
  background: #409eff;
}

.execution-detail-dialog :deep(.el-dialog__body) {
  flex: 1;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}


/* 响应式设计 */
@media (max-width: 1200px) {
  .stats-overview {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .test-runs-container {
    padding: 16px;
  }

  .stats-overview {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .stat-card {
    padding: 16px;
    gap: 12px;
  }

  .stat-icon {
    width: 40px;
    height: 40px;
    font-size: 20px;
  }

  .stat-number {
    font-size: 20px;
  }

  .stat-label {
    font-size: 12px;
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
  .card-header-filters .el-date-picker {
    width: 100% !important;
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
}
</style>
