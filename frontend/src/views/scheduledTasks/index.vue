<template>
  <div class="scheduled-tasks-container" v-if="selectedProject">
    <!-- 项目选择提示 -->
    <el-alert v-if="!selectedProject" title="请先选择一个项目" type="info" :closable="false" show-icon
      style="margin-bottom: 20px;">
      <template #default>
        <div>
          <p>您还没有选择当前工作项目，请前往项目管理页面选择项目。</p>
          <el-button type="primary" size="small" @click="goToProjects" style="margin-top: 10px;">
            前往项目管理
          </el-button>
        </div>
      </template>
    </el-alert>

    <!-- 页面头部 -->
    <div v-if="selectedProject" class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Clock />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>定时任务中心</h2>
            <p>统一管理所有测试类型的定时任务</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="showCreateDialog = true" :disabled="!selectedProject" class="create-btn">
            创建任务
          </el-button>
        </div>
      </div>
    </div>

    <!-- 任务列表 -->
    <el-card class="scheduled-tasks-card">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedTasks.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedTasks.length }} 个任务</span>
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
          <h3>任务列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select v-model="filters.suite_type" placeholder="测试类型" clearable @change="loadTasks" style="width: 120px;">
              <el-option label="全部" value="" />
              <el-option label="Web测试" value="web" />
              <el-option label="API测试" value="api" />
              <el-option label="App测试" value="app" />
            </el-select>
            
            <el-select v-model="filters.status" placeholder="任务状态" clearable @change="loadTasks" style="width: 120px;">
              <el-option label="全部" value="" />
              <el-option label="启用" value="active" />
              <el-option label="暂停" value="paused" />
              <el-option label="禁用" value="disabled" />
            </el-select>
            
            <el-input
              v-model="filters.search"
              placeholder="搜索任务名称或描述"
              clearable
              @input="handleSearch"
              style="width: 200px;"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            
            <el-button @click="loadTasks">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </div>
      </div>
      
      <!-- 表格布局 -->
      <div class="table-container">
        <el-table
          :data="tasks"
          v-loading="loading"
          stripe
          style="width: 100%; height: 100%"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="40" />
          
          <el-table-column prop="name" label="任务名称" min-width="200">
          <template #default="{ row }">
            <div class="task-name">
              <div class="name">{{ row.name }}</div>
              <div class="description">{{ row.description || '无描述' }}</div>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="suite_type" label="测试类型" width="100">
          <template #default="{ row }">
            <el-tag :type="getSuiteTypeTagType(row.suite_type)">
              {{ getSuiteTypeLabel(row.suite_type) }}
            </el-tag>
          </template>
        </el-table-column>
        
        <el-table-column prop="suite_name" label="测试套件" min-width="150" />
        
        <el-table-column prop="cron_expression" label="执行时间" min-width="180">
          <template #default="{ row }">
            <div class="cron-info">
              <div class="cron">{{ row.cron_expression }}</div>
              <div class="cron-desc">{{ getCronDescription(row.cron_expression) }}</div>
              <div class="next-run" v-if="row.next_run_time">
                下次: {{ formatDateTime(row.next_run_time) }}
              </div>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusTagType(row.status)">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        
        <el-table-column prop="last_execution_status" label="最近执行" width="120">
          <template #default="{ row }">
            <template v-if="row.last_execution_status === 'running'">
              <el-tag type="warning">
                <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
                执行中
              </el-tag>
            </template>
            <el-tag v-else-if="row.last_execution_status" :type="getExecutionStatusTagType(row.last_execution_status)">
              {{ getExecutionStatusLabel(row.last_execution_status) }}
            </el-tag>
            <span v-else class="no-data">未执行</span>
          </template>
        </el-table-column>
        
        <el-table-column label="最近执行结果" width="180">
          <template #default="{ row }">
            <div v-if="row.last_total_cases > 0" class="last-result">
              <span class="lr-pass">{{ row.last_passed_cases }}</span>
              <span class="lr-sep">/</span>
              <span class="lr-fail">{{ row.last_failed_cases }}</span>
              <span class="lr-sep">/</span>
              <span class="lr-total">{{ row.last_total_cases }}</span>
              <el-tag
                :type="row.last_failed_cases > 0 ? 'danger' : 'success'"
                size="small"
                style="margin-left:6px;"
              >
                {{ row.last_failed_cases > 0 ? '未通过' : '通过' }}
              </el-tag>
            </div>
            <span v-else class="no-data">未执行</span>
          </template>
        </el-table-column>

        <el-table-column prop="success_rate" label="成功率" width="120">
          <template #default="{ row }">
            <div class="success-rate">
              <el-progress
                :percentage="row.success_rate"
                :color="getSuccessRateColor(row.success_rate)"
                :show-text="false"
                :stroke-width="8"
              />
              <span class="rate-text">{{ row.success_rate }}%</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="通知对象" min-width="180">
          <template #default="{ row }">
            <template v-if="!row.notice_targets || !row.notice_targets.length">
              <span class="no-data">无</span>
            </template>
            <div v-else class="notice-targets-cell">
              <el-tag
                v-for="target in row.notice_targets"
                :key="typeof target === 'object' && target != null ? target.id : target"
                size="small"
                type="info"
                style="margin: 2px;"
              >
                <template v-if="target && typeof target === 'object' && target.type === 'email'">
                  [邮件] {{ maskEmailList(target.target_address || '') }}
                </template>
                <template v-else-if="target && typeof target === 'object'">
                  [{{ getNoticeChannelTypeLabel(target.type) }}] {{ target.name || '-' }}
                </template>
                <template v-else>-</template>
              </el-tag>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button type="" size="small" @click="editTask(row)">
                <el-icon>
                  <Edit />
                </el-icon>
                编辑
              </el-button>
              <el-button 
                type="primary" 
                size="small" 
                @click="runTask(row)"
                class="execute-button"
              >
                <el-icon>
                  <VideoPlay />
                </el-icon>
                执行
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
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.size"
            :total="pagination.total"
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="loadTasks"
            @current-change="loadTasks"
          />
        </div>
      </div>
    </el-card>

    <!-- 创建/编辑任务对话框 -->
    <TaskEditDialog
      v-model="showCreateDialog"
      :task="editingTask"
      @success="handleTaskSuccess"
    />

    <!-- 任务详情对话框 -->
    <TaskDetailDialog
      v-model="showDetailDialog"
      :task="viewingTask"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { Plus, Clock, Search, Refresh, Delete, Close, Edit, VideoPlay, Loading } from '@element-plus/icons-vue'
import { 
  getScheduledTasks, 
  runScheduledTask, 
  deleteScheduledTask,
  getExecutionLog
} from '../../api/scheduledTasks'
import TaskEditDialog from '../../components/scheduledTasks/TaskEditDialog.vue'
import TaskDetailDialog from '../../components/scheduledTasks/TaskDetailDialog.vue'
import { useProjectStore } from '@/stores/project'
import { maskEmailList } from '@/utils/mask'
import cronstrue from 'cronstrue/i18n'

const router = useRouter()

// 项目状态管理
const projectStore = useProjectStore()

// 响应式数据
const loading = ref(false)
const tasks = ref([])
const selectedTasks = ref([])
const showCreateDialog = ref(false)
const showDetailDialog = ref(false)
const editingTask = ref(null)
const viewingTask = ref(null)

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

/** 将 Cron 表达式转为中文易读说明（cronstrue 多语言） */
const getCronDescription = (cron) => {
  if (!cron || typeof cron !== 'string' || !cron.trim()) return ''
  try {
    return cronstrue.toString(cron.trim(), { locale: 'zh_CN' })
  } catch {
    return '格式异常或无法解析'
  }
}

// 筛选条件
const filters = reactive({
  suite_type: '',
  status: '',
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

// 异步任务完成轮询（组件销毁时须清除，防止内存泄漏）
let executionPollingTimerId = null

const stopExecutionPolling = () => {
  if (executionPollingTimerId != null) {
    clearInterval(executionPollingTimerId)
    executionPollingTimerId = null
  }
}

const pollExecutionUntilDone = (executionId, taskName) => {
  const projectId = projectStore.currentProjectId
  if (!projectId || !executionId) return
  let isNotified = false
  const check = async () => {
    try {
      const res = await getExecutionLog(projectId, executionId)
      const log = res?.data ?? res
      const status = log?.status
      if (status === 'success' || status === 'failed') {
        stopExecutionPolling()
        if (!isNotified) {
          isNotified = true
          const rate = log?.success_rate ?? 0
          ElNotification({
            title: '任务执行完成',
            message: `任务：${taskName || '定时任务'} 执行完毕，成功率：${rate}%。`,
            type: status === 'success' ? 'success' : 'warning',
            position: 'bottom-right',
            duration: 0
          })
          loadTasks()
        }
      }
    } catch (e) {
      console.error('轮询执行详情失败:', e)
    }
  }
  check()
  executionPollingTimerId = setInterval(check, 2500)
}

onUnmounted(() => {
  stopExecutionPolling()
  if (searchTimeout) clearTimeout(searchTimeout)
})

// 生命周期
onMounted(async () => {
  // 初始化项目
  await projectStore.initializeUserPreferences()
  
  loadTasks()
  
  // 检查URL参数，如果有suite_type等参数，自动打开创建对话框
  checkUrlParams()
})

// 方法
const loadTasks = async () => {
  if (!selectedProject.value) {
    tasks.value = []
    pagination.total = 0
    return
  }

  try {
    loading.value = true
    const params = {
      page: pagination.page,
      page_size: pagination.size,
      ...filters
    }
    
    const response = await getScheduledTasks(projectStore.currentProjectId, params)
    tasks.value = response.results || response
    pagination.total = response.count || response.length
  } catch (error) {
    ElMessage.error('加载任务列表失败')
    console.error('Load tasks error:', error)
  } finally {
    loading.value = false
  }
}

const checkUrlParams = () => {
  // 检查URL参数，如果有suite_type等参数，自动打开创建对话框
  const urlParams = new URLSearchParams(window.location.search)
  const suiteType = urlParams.get('suite_type')
  const suiteId = urlParams.get('suite_id')
  const projectId = urlParams.get('project_id')
  
  if (suiteType && suiteId && projectId && selectedProject.value && projectId == projectStore.currentProjectId) {
    // 自动打开创建对话框
    showCreateDialog.value = true
    
    // 清除URL参数，避免刷新页面时重复打开
    const newUrl = new URL(window.location)
    newUrl.searchParams.delete('suite_type')
    newUrl.searchParams.delete('suite_id')
    newUrl.searchParams.delete('suite_name')
    newUrl.searchParams.delete('project_id')
    window.history.replaceState({}, '', newUrl)
  }
}

const handleSearch = () => {
  if (searchTimeout) {
    clearTimeout(searchTimeout)
  }
  searchTimeout = setTimeout(() => {
    pagination.page = 1
    loadTasks()
  }, 500)
}

const editTask = (task) => {
  editingTask.value = task
  showCreateDialog.value = true
}

const runTask = async (task) => {
  try {
    await ElMessageBox.confirm(
      `确定要手动执行任务"${task.name}"吗？`,
      '确认执行',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const response = await runScheduledTask(projectStore.currentProjectId, task.id)
    ElMessage.success('任务执行已启动')
    const executionId = response?.data?.execution_id ?? response?.execution_id
    if (executionId) {
      pollExecutionUntilDone(executionId, task.name)
    }
    loadTasks()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('执行任务失败')
      console.error('Run task error:', error)
    }
  }
}

const handleTaskSuccess = () => {
  loadTasks()
}

// 处理选择变化
const handleSelectionChange = (selection) => {
  selectedTasks.value = selection
}

// 批量删除
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTasks.value.length} 个任务吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // 批量删除
    for (const task of selectedTasks.value) {
      await deleteScheduledTask(projectStore.currentProjectId, task.id)
    }
    
    ElMessage.success(`成功删除 ${selectedTasks.value.length} 个任务`)
    clearSelection()
    loadTasks()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error('批量删除失败')
    }
  }
}

// 清除选择
const clearSelection = () => {
  selectedTasks.value = []
}

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
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

const getStatusLabel = (status) => {
  const labels = {
    active: '启用',
    paused: '暂停',
    disabled: '禁用'
  }
  return labels[status] || status
}

const getStatusTagType = (status) => {
  const types = {
    active: 'success',
    paused: 'warning',
    disabled: 'danger'
  }
  return types[status] || 'info'
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

const getNoticeChannelTypeLabel = (type) => {
  const labels = {
    wechat_work: '企微',
    dingtalk: '钉钉',
    email: '邮件'
  }
  return labels[type] || type || '-'
}

const getSuccessRateColor = (rate) => {
  if (rate >= 80) return '#67c23a'
  if (rate >= 60) return '#e6a23c'
  return '#f56c6c'
}

const formatDateTime = (dateTime) => {
  if (!dateTime) return ''
  return new Date(dateTime).toLocaleString('zh-CN')
}

// 监听对话框关闭
watch(showCreateDialog, (newVal) => {
  if (!newVal) {
    editingTask.value = null
  }
})

watch(showDetailDialog, (newVal) => {
  if (!newVal) {
    viewingTask.value = null
  }
})

// 监听项目选择变化
watch(selectedProject, async (newProject, oldProject) => {
  if (newProject && newProject !== oldProject) {
    // 项目变化时重新加载数据
    await loadTasks()
  }
}, { immediate: true })
</script>

<style scoped>
.scheduled-tasks-container {
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

.scheduled-tasks-card {
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

.card-header-left h3 {
  margin: 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
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

.task-name .name {
  font-weight: 600;
  margin-bottom: 4px;
  color: #303133;
  cursor: pointer;
  transition: color 0.2s ease;
}

.task-name .name:hover {
  color: #409eff;
}

.task-name .description {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cron-info .cron {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  margin-bottom: 2px;
  color: #303133;
  font-weight: 500;
}

.cron-info .cron-desc {
  font-size: 11px;
  color: #909399;
  line-height: 1.35;
  margin-bottom: 4px;
  word-break: break-word;
}

.cron-info .next-run {
  font-size: 11px;
  color: #909399;
  line-height: 1.2;
}

.last-result {
  display: flex;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
}
.lr-pass { color: #67c23a; }
.lr-fail { color: #f56c6c; }
.lr-total { color: #303133; }
.lr-sep { color: #c0c4cc; margin: 0 2px; }

.success-rate {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rate-text {
  font-size: 12px;
  min-width: 35px;
  font-weight: 500;
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

.notice-targets-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  align-items: center;
}

.no-data {
  color: #999;
  font-size: 12px;
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

/* 响应式设计 */
@media (max-width: 768px) {
  .scheduled-tasks-container {
    padding: 10px;
  }
  
  .page-header {
    padding: 20px;
  }
  
  .header-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 20px;
  }
  
  .header-actions {
    width: 100%;
    justify-content: flex-end;
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
  
  .table-container {
    height: calc(100vh - 250px);
  }
}

@media (max-width: 480px) {
  .header-text h2 {
    font-size: 18px;
  }
  
  .header-text p {
    font-size: 12px;
  }
  
  .create-btn {
    padding: 10px 20px;
    font-size: 14px;
  }
}
</style>
