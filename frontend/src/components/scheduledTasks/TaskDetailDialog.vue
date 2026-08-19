<template>
  <el-dialog
    v-model="visible"
    title="任务详情"
    width="1000px"
    :before-close="handleClose"
  >
    <div v-if="task" class="task-detail">
      <!-- 基本信息 -->
      <el-card class="info-card">
        <template #header>
          <div class="card-header">
            <span>基本信息</span>
            <el-tag :type="getStatusTagType(task.status)">
              {{ getStatusLabel(task.status) }}
            </el-tag>
          </div>
        </template>
        
        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务名称">
            {{ task.name }}
          </el-descriptions-item>
          <el-descriptions-item label="测试类型">
            <el-tag :type="getSuiteTypeTagType(task.suite_type)">
              {{ getSuiteTypeLabel(task.suite_type) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="测试套件">
            {{ task.suite_name }}
          </el-descriptions-item>
          <el-descriptions-item label="执行环境">
            {{ task.environment_name }}
          </el-descriptions-item>
          <el-descriptions-item label="Cron表达式">
            <code>{{ task.cron_expression }}</code>
          </el-descriptions-item>
          <el-descriptions-item label="创建用户">
            {{ task.user_name }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ formatDateTime(task.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">
            {{ formatDateTime(task.updated_at) }}
          </el-descriptions-item>
        </el-descriptions>
        
        <div v-if="task.description" class="task-description">
          <h4>任务描述</h4>
          <p>{{ task.description }}</p>
        </div>
      </el-card>

      <!-- 执行统计 -->
      <el-card class="info-card">
        <template #header>
          <span>执行统计</span>
        </template>
        
        <el-row :gutter="20">
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-number">{{ task.total_executions || 0 }}</div>
              <div class="stat-label">总执行次数</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-number">{{ task.success_rate || 0 }}%</div>
              <div class="stat-label">成功率</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-number">{{ formatDateTime(task.last_run_time) || '未执行' }}</div>
              <div class="stat-label">上次执行</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-number">{{ formatDateTime(task.next_run_time) || '未计划' }}</div>
              <div class="stat-label">下次执行</div>
            </div>
          </el-col>
        </el-row>
      </el-card>

      <!-- 通知配置 -->
      <el-card v-if="task.notification" class="info-card">
        <template #header>
          <span>通知配置</span>
        </template>
        
        <el-descriptions :column="2" border>
          <el-descriptions-item label="通知方式">
            {{ getNotificationTypeLabel(task.notification.notification_type) }}
          </el-descriptions-item>
          <el-descriptions-item label="成功时通知">
            <el-tag :type="task.notification.notify_on_success ? 'success' : 'info'">
              {{ task.notification.notify_on_success ? '是' : '否' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="失败时通知">
            <el-tag :type="task.notification.notify_on_failure ? 'success' : 'info'">
              {{ task.notification.notify_on_failure ? '是' : '否' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="task.notification.webhook_url" label="Webhook URL">
            <el-text type="primary">{{ task.notification.webhook_url }}</el-text>
          </el-descriptions-item>
        </el-descriptions>
        
        <div v-if="task.notification.email_recipients && task.notification.email_recipients.length" class="email-recipients">
          <h4>邮件接收者</h4>
          <el-tag
            v-for="email in task.notification.email_recipients"
            :key="email"
            class="email-tag"
          >
            {{ email }}
          </el-tag>
        </div>
      </el-card>

      <!-- 最近执行记录 -->
      <el-card class="info-card">
        <template #header>
          <div class="card-header">
            <span>最近执行记录</span>
            <el-button size="small" @click="viewAllLogs">查看全部</el-button>
          </div>
        </template>
        
        <el-table
          :data="recentLogs"
          v-loading="loadingLogs"
          stripe
          size="small"
        >
          <el-table-column prop="start_time" label="开始时间" width="150">
            <template #default="{ row }">
              {{ formatDateTime(row.start_time) }}
            </template>
          </el-table-column>
          
          <el-table-column prop="end_time" label="结束时间" width="150">
            <template #default="{ row }">
              {{ row.end_time ? formatDateTime(row.end_time) : '-' }}
            </template>
          </el-table-column>
          
          <el-table-column prop="duration" label="执行时长" width="100">
            <template #default="{ row }">
              {{ row.duration || '-' }}
            </template>
          </el-table-column>
          
          <el-table-column prop="status" label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="getExecutionStatusTagType(row.status)" size="small">
                {{ getExecutionStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          
          <el-table-column prop="total_cases" label="用例数" width="80" />
          <el-table-column prop="passed_cases" label="通过" width="60" />
          <el-table-column prop="failed_cases" label="失败" width="60" />
          
          <el-table-column prop="success_rate" label="成功率" width="80">
            <template #default="{ row }">
              <el-progress
                :percentage="row.success_rate"
                :color="getSuccessRateColor(row.success_rate)"
                :show-text="false"
                :stroke-width="6"
              />
              <span class="rate-text">{{ row.success_rate }}%</span>
            </template>
          </el-table-column>
          
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" @click="viewLogDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>

    <!-- 执行日志详情对话框 -->
    <el-dialog
      v-model="showLogDetail"
      title="执行日志详情"
      width="800px"
      append-to-body
    >
      <div v-if="selectedLog" class="log-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务名称">
            {{ selectedLog.task_name }}
          </el-descriptions-item>
          <el-descriptions-item label="执行状态">
            <el-tag :type="getExecutionStatusTagType(selectedLog.status)">
              {{ getExecutionStatusLabel(selectedLog.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ formatDateTime(selectedLog.start_time) }}
          </el-descriptions-item>
          <el-descriptions-item label="结束时间">
            {{ selectedLog.end_time ? formatDateTime(selectedLog.end_time) : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="执行时长">
            {{ selectedLog.duration || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="总用例数">
            {{ selectedLog.total_cases }}
          </el-descriptions-item>
          <el-descriptions-item label="通过用例">
            {{ selectedLog.passed_cases }}
          </el-descriptions-item>
          <el-descriptions-item label="失败用例">
            {{ selectedLog.failed_cases }}
          </el-descriptions-item>
          <el-descriptions-item label="跳过用例">
            {{ selectedLog.skipped_cases }}
          </el-descriptions-item>
          <el-descriptions-item label="成功率">
            {{ selectedLog.success_rate }}%
          </el-descriptions-item>
        </el-descriptions>
        
        <div v-if="selectedLog.error_message" class="error-message">
          <h4>错误信息</h4>
          <el-alert
            :title="selectedLog.error_message"
            type="error"
            :closable="false"
            show-icon
          />
        </div>
        
        <div v-if="selectedLog.result_log" class="result-log">
          <h4>执行日志</h4>
          <el-input
            :model-value="selectedLog.result_log"
            type="textarea"
            :rows="10"
            readonly
            placeholder="暂无执行日志"
          />
        </div>
        
        <div v-if="selectedLog.report_url" class="report-link">
          <h4>测试报告</h4>
          <el-link :href="selectedLog.report_url" target="_blank" type="primary">
            <el-icon><Link /></el-icon>
            查看测试报告
          </el-link>
        </div>
      </div>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Link } from '@element-plus/icons-vue'
import { getTaskExecutionLogs } from '../../api/scheduledTasks'

// Props
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  task: {
    type: Object,
    default: null
  }
})

// Emits
const emit = defineEmits(['update:modelValue'])

// 响应式数据
const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const loadingLogs = ref(false)
const recentLogs = ref([])
const showLogDetail = ref(false)
const selectedLog = ref(null)

// 监听对话框显示
watch(visible, (newVal) => {
  if (newVal && props.task) {
    loadRecentLogs()
  }
})

// 方法
const loadRecentLogs = async () => {
  if (!props.task) return
  
  try {
    loadingLogs.value = true
    const response = await getTaskExecutionLogs(props.task.id, { page_size: 5 })
    recentLogs.value = response.results || response
  } catch (error) {
    console.error('Load recent logs error:', error)
  } finally {
    loadingLogs.value = false
  }
}

const viewAllLogs = () => {
  // 这里可以跳转到执行日志页面或打开新的对话框
  console.log('View all logs for task:', props.task.id)
}

const viewLogDetail = (log) => {
  selectedLog.value = log
  showLogDetail.value = true
}

const handleClose = () => {
  visible.value = false
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

const getNotificationTypeLabel = (type) => {
  const labels = {
    none: '不通知',
    email: '邮件通知',
    webhook: 'Webhook通知'
  }
  return labels[type] || type
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
</script>

<style scoped>
.task-detail {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.info-card {
  margin-bottom: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.task-description {
  margin-top: 16px;
}

.task-description h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #303133;
}

.task-description p {
  margin: 0;
  color: #606266;
  line-height: 1.6;
}

.stat-item {
  text-align: center;
  padding: 16px;
}

.stat-number {
  font-size: 24px;
  font-weight: bold;
  color: #409eff;
  margin-bottom: 8px;
}

.stat-label {
  font-size: 14px;
  color: #666;
}

.email-recipients {
  margin-top: 16px;
}

.email-recipients h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #303133;
}

.email-tag {
  margin-right: 8px;
  margin-bottom: 8px;
}

.log-detail {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.error-message h4,
.result-log h4,
.report-link h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #303133;
}

.rate-text {
  font-size: 12px;
  margin-left: 8px;
}
</style>
