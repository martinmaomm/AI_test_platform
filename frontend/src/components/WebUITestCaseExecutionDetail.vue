<template>
  <div class="test-report-container">
    <!-- 主要内容区域 -->
    <div class="report-content">
      <!-- 主内容区域 -->
      <div class="main-content">
        <div v-if="execution.error_message" class="execution-error">
          <strong>执行失败：</strong>
          <span>{{ execution.error_message }}</span>
        </div>
        <!-- 概览内容 -->
        <div class="overview-section">
          <div class="overview-grid">
            <div class="info-card overview-card">
              <div class="info-header">
                <h3>Execution Information</h3>
                <el-tag :type="getStatusType(execution.status)" size="small" class="status-tag">
                  {{ getStatusText(execution.status) }}
                </el-tag>
              </div>
              <div class="info-grid">
                <div class="info-item">
                  <span class="label">Test Record ID:</span>
                  <span class="value">#{{ execution.id }}</span>
                </div>
                <div class="info-item">
                  <span class="label">Test Case:</span>
                  <span class="value">{{ execution.test_case_title || 'N/A' }}</span>
                </div>
                <div class="info-item">
                  <span class="label">Description:</span>
                  <span class="value">{{ execution.test_case_description || 'N/A' }}</span>
                </div>
                <div class="info-item">
                  <span class="label">Browser:</span>
                  <span class="value">Chrome</span>
                </div>
                  <div class="info-item">
                    <span class="label">Start Time:</span>
                    <span class="value">{{ formatTime(execution.start_time) }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Environment:</span>
                    <span class="value">{{ getEnvironmentText(execution.environment_name, execution.environment_base_url) }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Duration:</span>
                    <span class="value">{{ formatDuration(execution.duration) }}</span>
                  </div>
              </div>
            </div>

            <!-- Execution Logs -->
            <div class="log-container chart-card">
              <div class="log-header">
                <h3>Execution Logs</h3>
                <el-button size="small" @click="copyLogs" class="copy-btn">
                  <i class="el-icon-copy-document"></i>
                  Copy
                </el-button>
              </div>
              <div class="log-content">
                <pre>{{ execution.log || 'No logs available' }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  execution: {
    type: Object,
    required: true,
    default: () => ({})
  },
  visible: {
    type: Boolean,
    default: false
  }
})

// 添加调试信息
console.log('WebUITestSuiteExecutionDetail接收到的execution:', props.execution)
console.log('allure_report_url字段值:', props.execution.allure_report_url)

const emit = defineEmits(['close'])

// 方法
const formatTime = (timeStr) => {
  if (!timeStr) return 'N/A'
  return new Date(timeStr).toLocaleString()
}

const formatDuration = (duration) => {
  if (!duration) return '0s'
  if (typeof duration === 'number') {
    return `${duration.toFixed(2)}s`
  }
  return duration
}

const getEnvironmentText = (name, baseUrl) => {
  if (!name && !baseUrl) return 'N/A'
  if (name && baseUrl) {
    return `${name} (${baseUrl})`
  }
  return name || baseUrl || 'N/A'
}

// 状态相关方法
const getStatusType = (status) => {
  const statusMap = {
    'pending': 'info',
    'running': 'warning', 
    'passed': 'success',
    'failed': 'danger',
    'error': 'danger',
    'stopped': 'warning'
  }
  return statusMap[status] || 'info'
}

const getStatusText = (status) => {
  const statusMap = {
    'pending': '待执行',
    'running': '执行中',
    'passed': '执行通过',
    'failed': '执行失败', 
    'error': '执行错误',
    'stopped': '已停止'
  }
  return statusMap[status] || '未知状态'
}

const copyLogs = () => {
  const logContent = props.execution.log || 'No logs available'
  navigator.clipboard.writeText(logContent).then(() => {
    ElMessage.success('Logs copied to clipboard')
  }).catch(() => {
    ElMessage.error('Failed to copy logs')
  })
}

</script>

<style scoped>
.test-report-container {
  height: 470px;
  background: #fafbfc;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}




/* 主要内容区域 */
.report-content {
  height: 100%;
  overflow: hidden;
}

/* 主内容区域 */
.main-content {
  background: #ffffff;
  padding: 24px;
  height: 100%;
  overflow-y: auto;
}

.execution-error {
  margin-bottom: 16px;
  padding: 12px 16px;
  color: #b42318;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #fef3f2;
  border: 1px solid #fecdca;
  border-radius: 6px;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 动画效果 */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes scaleIn {
  from {
    opacity: 0;
    transform: scale(0.8);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes gradientShift {
  0%, 100% {
    background-position: 0% 50%;
  }
  50% {
    background-position: 100% 50%;
  }
}

/* 概览区域 */
.overview-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

/* 卡片通用样式 */
.info-card,
.log-container {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
}

.info-card {
  border-left: 4px solid #409eff;
}

.info-card {
  padding: 20px;
}

.info-card h3,
.log-header h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}

/* 信息卡片头部样式 */
.info-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.info-header h3 {
  margin: 0;
}

.status-tag {
  font-weight: 500;
  font-size: 12px;
}

/* 悬停效果 */
.info-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
}

/* chart-card样式 */
.chart-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
  border: 1px solid #f0f0f0;
  transition: all 0.3s ease;
  position: relative;
  overflow: hidden;
}

.chart-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #409eff, #67c23a, #e6a23c, #f56c6c);
  background-size: 400% 100%;
  animation: gradientShift 3s ease-in-out infinite;
}

.chart-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
}

/* 动画类 */
.overview-card {
  animation: fadeInUp 0.6s ease-out;
}

.chart-card {
  animation: scaleIn 0.6s ease-out;
}

.info-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.info-item:last-child {
  border-bottom: none;
}

.info-item .label {
  font-weight: 500;
  color: #666;
  font-size: 14px;
}

.info-item .value {
  font-weight: 600;
  color: #1a1a1a;
  font-size: 14px;
}



/* 日志部分 */
.log-container {
  overflow: hidden;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #f8f9fa;
  border-bottom: 1px solid #e8eaed;
  margin: -20px -20px 20px -20px;
  padding: 16px 20px;
}

.copy-btn {
  font-size: 12px;
  padding: 6px 12px;
  height: auto;
}

.log-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 15px;
  height: 300px;
  overflow-y: auto;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  border-radius: 6px;
  margin-top: 0;
}

.log-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 响应式设计 */
@media (max-width: 1200px) {
  .overview-grid {
    grid-template-columns: 1fr;
    gap: 20px;
  }
}

@media (max-width: 768px) {
  .overview-grid {
    gap: 16px;
  }

  .main-content {
    padding: 16px;
  }
}
</style>
