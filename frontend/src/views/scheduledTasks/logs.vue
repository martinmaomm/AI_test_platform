<template>
  <div class="execution-logs-container" v-if="projectStore.currentProject">
    <!-- 执行记录表格 -->
    <el-card class="table-card" shadow="hover">
      <!-- 批量操作栏 - 覆盖显示在table-header上方 -->
      <div v-if="selectedLogs.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedLogs.length }} 个执行记录</span>
        </div>
        <div class="batch-buttons">
          <el-button @click="batchDelete" type="danger" :loading="batchDeleteLoading">
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
      <div v-if="selectedLogs.length === 0" class="card-header">
        <div class="card-header-left">
          <h3>执行日志列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select v-model="filters.suite_type" placeholder="测试类型" clearable style="width: 120px;" @change="loadLogs">
              <el-option label="全部" value="" />
              <el-option label="Web测试" value="web" />
              <el-option label="API测试" value="api" />
              <el-option label="App测试" value="app" />
            </el-select>
            <el-select v-model="filters.status" placeholder="执行状态" clearable style="width: 140px;" @change="loadLogs">
              <el-option label="全部" value="" />
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
              <el-option label="执行中" value="running" />
              <el-option label="等待中" value="pending" />
              <el-option label="已取消" value="cancelled" />
            </el-select>
            <el-date-picker v-model="filters.date_range" type="datetimerange" range-separator="至" start-placeholder="开始时间"
              end-placeholder="结束时间" format="MM-DD HH:mm" value-format="YYYY-MM-DD HH:mm:ss" style="width: 280px;" @change="loadLogs" />
            <el-input
              v-model="filters.search"
              placeholder="搜索任务名称"
              clearable
              @input="handleSearch"
              style="width: 200px"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
          </div>
          <el-button @click="loadLogs" :icon="Refresh">
            刷新
          </el-button>
        </div>
      </div>

      <el-table
        ref="tableRef"
        :data="logs"
        v-loading="loading"
        class="execution-table"
        :row-class-name="getRowClassName"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="40" />
        
        <el-table-column prop="id" label="记录ID" width="80" align="center">
          <template #default="{ row }">
            <span class="execution-id">#{{ row.id || '-' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="task_name" label="任务信息" min-width="250">
          <template #default="{ row }">
            <div class="execution-name">
              <div class="test-case-title" @click.stop="viewExecutionRecord(row)">
                {{ row.task_name }}
              </div>
              <div class="name-desc" v-if="row.description">{{ row.description }}</div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="suite_type" label="测试类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getSuiteTypeTagType(row.suite_type)" size="small">
              {{ getSuiteTypeLabel(row.suite_type) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'running' ? 'warning' : 'info'"
              size="small"
              class="status-tag"
            >
              {{ getExecutionDisplayLabel(row) }}
            </el-tag>
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

        <el-table-column label="测试结果" width="240" align="center">
          <template #default="{ row }">
            <div class="test-results-summary">
              <span class="passed">{{ row.passed_cases ?? 0 }}</span>
              <span class="separator"> / </span>
              <span class="failed">{{ row.failed_cases ?? 0 }}</span>
              <span class="separator"> / </span>
              <span class="total">{{ row.total_cases ?? 0 }}</span>
              <el-tag
                :type="Number(row.failed_cases) > 0 ? 'danger' : 'success'"
                size="small"
                class="result-label-tag"
              >
                {{ Number(row.failed_cases) > 0 ? '测试未通过' : '测试通过' }}
              </el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="success_rate" label="成功率" width="100" align="center">
          <template #default="{ row }">
            <span class="success-rate-text">{{ row.success_rate || 0 }}%</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="120" fixed="right" align="center">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button size="small" @click.stop="viewExecutionRecord(row)" class="view-btn">
                查看记录
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-container">
        <el-pagination v-model:current-page="pagination.page" v-model:page-size="pagination.size" :page-sizes="[10, 20, 50, 100]"
          :total="pagination.total" layout="total, sizes, prev, pager, next, jumper" @size-change="loadLogs"
          @current-change="loadLogs" />
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
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { 
  Refresh, Delete, Search, Close
} from '@element-plus/icons-vue'
import { getExecutionLogs, deleteExecutionLog } from '../../api/scheduledTasks'
import { useProjectStore } from '@/stores/project'
import dayjs from 'dayjs'

// 响应式数据
const loading = ref(false)
const logs = ref([])
const selectedLogs = ref([])
const tableRef = ref(null)
const router = useRouter()

// 项目状态管理
const projectStore = useProjectStore()

// 筛选条件
const filters = reactive({
  suite_type: '',
  status: '',
  date_range: null,
  search: ''
})

// 分页
const pagination = reactive({
  page: 1,
  size: 20,
  total: 0
})


// 搜索防抖
let searchTimeout = null

// 生命周期
onMounted(() => {
  loadLogs()
})

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 方法
const loadLogs = async () => {
  try {
    loading.value = true
    
    // 检查是否有当前项目
    if (!projectStore.currentProjectId) {
      ElMessage.warning('请先选择一个项目')
      return
    }
    
    const params = {
      page: pagination.page,
      page_size: pagination.size,
      ...filters
    }
    
    // 处理日期范围
    if (filters.date_range && filters.date_range.length === 2) {
      params.start_time = filters.date_range[0]
      params.end_time = filters.date_range[1]
    }
    
    const response = await getExecutionLogs(projectStore.currentProjectId, params)
    // 适配新的分页数据结构
    if (response.data && response.data.items) {
      logs.value = response.data.items
      pagination.total = response.data.pagination.total
    } else {
      // 兼容旧的数据结构
      logs.value = response.results || response
      pagination.total = response.count || response.length
    }
  } catch (error) {
    ElMessage.error('加载执行日志失败')
    console.error('Load logs error:', error)
  } finally {
    loading.value = false
  }
}


const handleSearch = () => {
  if (searchTimeout) {
    clearTimeout(searchTimeout)
  }
  searchTimeout = setTimeout(() => {
    pagination.page = 1
    loadLogs()
  }, 500)
}

const viewExecutionRecord = (log) => {
  // 根据测试类型跳转到对应的执行记录页面
  if (log.suite_type === 'api') {
    // API测试执行记录
    router.push('/api-testing/test-executions')
  } else if (log.suite_type === 'web') {
    // Web测试执行记录
    router.push('/web-testing/test-executions')
  } else if (log.suite_type === 'app') {
    // App测试执行记录
    router.push('/app-testing/test-runs')
  } else {
    ElMessage.warning('未知的测试类型')
  }
}

// 批量操作相关函数
const handleSelectionChange = (selection) => {
  selectedLogs.value = selection
}

const clearSelection = () => {
  selectedLogs.value = []
  tableRef.value?.clearSelection()
}

const batchDeleteLoading = ref(false)

const batchDelete = async () => {
  const ids = selectedLogs.value.map((row) => row.id)
  if (!ids || ids.length === 0) {
    ElMessage.warning('请先勾选需要删除的执行记录')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${ids.length} 条执行记录吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    batchDeleteLoading.value = true
    let deletedCount = 0
    for (const id of ids) {
      try {
        await deleteExecutionLog(projectStore.currentProjectId, id)
        deletedCount++
      } catch (error) {
        console.error(`删除执行记录 #${id} 失败:`, error)
      }
    }

    ElMessage.success(`成功删除 ${deletedCount} 条执行记录`)
    clearSelection()
    await loadLogs()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error('批量删除失败')
    }
  } finally {
    batchDeleteLoading.value = false
  }
}

// 获取表格行样式类名
const getRowClassName = ({ row }) => {
  if (row.status === 'success') return 'success-row'
  if (row.status === 'failed') return 'failed-row'
  if (row.status === 'running') return 'running-row'
  if (row.status === 'pending') return 'pending-row'
  return ''
}

// 工具方法
const getSuiteTypeLabel = (type) => {
  const labels = {
    web: 'Web测试',
    api: 'API测试',
    app: 'App测试'
  }
  return labels[type] || type
}

const getSuiteTypeTagType = (type) => {
  const types = {
    web: 'primary',
    api: 'success',
    app: 'warning'
  }
  return types[type] || 'info'
}

const getExecutionStatusLabel = (status) => {
  const labels = {
    success: '成功',
    failed: '失败',
    running: '执行中',
    pending: '等待中',
    cancelled: '已取消'
  }
  return labels[status] || status
}

/** 状态列：任务正常结束统一为“已完成”(info)，否则执行中/等待中等 */
const getExecutionDisplayLabel = (row) => {
  const status = row?.status
  if (status === 'running') return '执行中'
  if (status === 'pending') return '等待中'
  if (status === 'cancelled') return '已取消'
  if (status === 'success' || status === 'failed') return '已完成'
  return '异常'
}

const getExecutionStatusTagType = (status) => {
  const types = {
    success: 'success',
    failed: 'danger',
    running: 'warning',
    pending: 'info',
    cancelled: 'info'
  }
  return types[status] || 'info'
}

const formatDateTime = (dateTime) => {
  if (!dateTime) return '--'
  return dayjs(dateTime).format('YYYY-MM-DD HH:mm:ss')
}

const formatDuration = (duration) => {
  if (!duration) return '0s'
  
  // 处理字符串格式 "0:00:00.089735" (HH:MM:SS.微秒)
  if (typeof duration === 'string') {
    const parts = duration.split(':')
    if (parts.length >= 3) {
      const hours = parseInt(parts[0]) || 0
      const minutes = parseInt(parts[1]) || 0
      const seconds = parseFloat(parts[2]) || 0
      
      if (hours > 0) {
        return `${hours}h ${minutes}m ${Math.floor(seconds)}s`
      } else if (minutes > 0) {
        return `${minutes}m ${Math.floor(seconds)}s`
      } else {
        return `${seconds.toFixed(1)}s`
      }
    }
  }
  
  // 处理数字格式（秒数）
  if (typeof duration === 'number') {
    const hours = Math.floor(duration / 3600)
    const minutes = Math.floor((duration % 3600) / 60)
    const seconds = duration % 60
    
    if (hours > 0) {
      return `${hours}h ${minutes}m ${Math.floor(seconds)}s`
    } else if (minutes > 0) {
      return `${minutes}m ${Math.floor(seconds)}s`
    } else {
      return `${seconds.toFixed(1)}s`
    }
  }
  
  return duration
}
</script>

<style scoped>
.execution-logs-container {
  padding: 10px;
  max-width: 1600px;
  margin: 0 auto;
  background: #f8fafc;
  min-height: 100vh;
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

.test-results-summary {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
  font-weight: 600;
}

.test-results-summary .total { color: #303133; }
.test-results-summary .passed { color: #67c23a; }
.test-results-summary .failed { color: #f56c6c; }
.test-results-summary .separator { color: #c0c4cc; }
.test-results-summary .result-label-tag { margin-left: 8px; }

.success-rate-text {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
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

/* 响应式设计 */
@media (max-width: 768px) {
  .execution-logs-container {
    padding: 16px;
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
  .card-header-filters .el-date-picker,
  .card-header-filters .el-input {
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
