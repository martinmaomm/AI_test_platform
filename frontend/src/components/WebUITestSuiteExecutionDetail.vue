<template>
  <div class="test-report-container">
    <!-- 主要内容区域 -->
    <div class="report-content">
      <!-- 标签页导航 -->
      <div class="tab-navigation">
        <div v-for="tab in tabs" :key="tab.key" :class="['tab-item', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key">
          <i :class="tab.icon"></i>
          <span>{{ tab.label }}</span>
        </div>
      </div>

      <!-- 主内容区域 -->
      <div class="main-content">
        <!-- 概览标签页 -->
        <div v-show="activeTab === 'overview'" class="tab-content">
          <div class="overview-section">
            <div class="overview-grid">
              <div class="info-card overview-card">
                <h3>Execution Information</h3>
                <div class="info-grid">
                  <div class="info-item">
                    <span class="label">Test Record ID:</span>
                    <span class="value">#{{ execution.id }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Test Suite:</span>
                    <span class="value">{{ execution.test_suite_name || 'N/A' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Browser:</span>
                    <span class="value">{{ getBrowserText(execution.browser) }}</span>
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
                  <div class="info-item">
                    <span class="label">Pass Rate:</span>
                    <span class="value">{{ execution.pass_rate || 0 }}%</span>
                  </div>
                </div>
              </div>

              <div class="chart-card">
                <h3>Execution Summary</h3>
                <!-- 统计概览 -->
                <div class="stats-overview">
                  <div class="stat-item">
                    <div class="stat-number">{{ execution.total_cases || 0 }}</div>
                    <div class="stat-label">Total</div>
                  </div>
                  <div class="stat-item success">
                    <div class="stat-number">{{ execution.passed_cases || 0 }}</div>
                    <div class="stat-label">Passed</div>
                  </div>
                  <div class="stat-item failed">
                    <div class="stat-number">{{ execution.failed_cases || 0 }}</div>
                    <div class="stat-label">Failed</div>
                  </div>
                  <div class="stat-item error">
                    <div class="stat-number">{{ execution.skipped_cases || 0 }}</div>
                    <div class="stat-label">Skipped</div>
                  </div>
                </div>
                <div class="chart-container">
                  <v-chart :option="executionPieChartOption" style="height: 180px;" autoresize />
                </div>
              </div>
            </div>
          </div>
        </div>


        <!-- 执行日志标签页 -->
        <div v-show="activeTab === 'logs'" class="tab-content">
          <div class="logs-section">
            <div class="log-container">
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

        <!-- 测试报告标签页 -->
        <div v-show="activeTab === 'report'" class="tab-content">
          <div class="report-section">
            <div v-if="execution.allure_report_url" class="report-container">
              <div class="report-header">
                <h3>Allure Test Report</h3>
                <el-button type="primary" @click="openReportInNewTab" class="open-report-btn">
                  <i class="el-icon-view"></i>
                  Open in New Tab
                </el-button>
              </div>
            </div>
            <div v-else class="no-report">
              <el-empty description="No test report available" />
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
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent
} from 'echarts/components'
import VChart from 'vue-echarts'

// 注册必要的组件
use([
  CanvasRenderer,
  PieChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent
])

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

const activeTab = ref('overview')

// 标签页配置
const tabs = [
  { key: 'overview', label: 'Overview', icon: 'el-icon-data-board' },
  { key: 'logs', label: 'Logs', icon: 'el-icon-document-copy' },
  { key: 'report', label: 'Report', icon: 'el-icon-view' }
]

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

const getBrowserText = (browser) => {
  const texts = { chrome: 'Chrome', firefox: 'Firefox', safari: 'Safari', chromium: 'Chromium' }
  return texts[browser] || browser || 'Unknown'
}

const getEnvironmentText = (name, baseUrl) => {
  if (!name && !baseUrl) return 'N/A'
  if (name && baseUrl) {
    return `${name} (${baseUrl})`
  }
  return name || baseUrl || 'N/A'
}

const getSuccessRate = () => {
  if (!props.execution.total_cases || props.execution.total_cases === 0) return 0
  return Math.round((props.execution.passed_cases / props.execution.total_cases) * 100)
}

// 执行结果饼图配置
const executionPieChartOption = computed(() => {
  const total = props.execution.total_cases || 0
  const passed = props.execution.passed_cases || 0
  const failed = props.execution.failed_cases || 0
  const skipped = props.execution.skipped_cases || 0

  return {
    title: {
      text: '执行结果分布',
      left: 'center',
      textStyle: { fontSize: 16, fontWeight: 'bold', color: '#303133' }
    },
    tooltip: { trigger: 'item', formatter: '{a} <br/>{b}: {c} ({d}%)' },
    legend: {
      orient: 'vertical',
      left: 'left',
      top: 'middle',
      textStyle: { fontSize: 12 }
    },
    series: [{
      name: '执行结果',
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['60%', '50%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 8, borderColor: '#fff', borderWidth: 2 },
      label: { show: false, position: 'center' },
      emphasis: { label: { show: true, fontSize: '18', fontWeight: 'bold' } },
      labelLine: { show: false },
      data: [
        { value: passed, name: '成功', itemStyle: { color: '#67c23a' } },
        { value: failed, name: '失败', itemStyle: { color: '#f56c6c' } },
        ...(skipped > 0 ? [{ value: skipped, name: '跳过', itemStyle: { color: '#909399' } }] : [])
      ]
    }]
  }
})


const openReportInNewTab = () => {
  if (props.execution.allure_report_url) {
    // 如果是相对路径，转换为完整的后端URL
    let reportUrl = props.execution.allure_report_url
    if (reportUrl.startsWith('/')) {
      const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
      reportUrl = `${apiBaseUrl}${reportUrl}`
    }
    window.open(reportUrl, '_blank')
  }
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



/* 统计概览 */
.stats-overview {
  display: flex;
  gap: 0;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 8px 0;
  margin-bottom: 16px;
}

.stat-item {
  flex: 1;
  text-align: center;
  padding: 12px 16px;
  border-right: 1px solid #e8eaed;
  transition: background-color 0.2s ease;
}

.stat-item:last-child {
  border-right: none;
}

.stat-item:hover {
  background: #f1f3f4;
}

.stat-item.success {
  border-left: 3px solid #34a853;
}

.stat-item.failed {
  border-left: 3px solid #ea4335;
}

.stat-item.error {
  border-left: 3px solid #fbbc04;
}

.stat-number {
  font-size: 18px;
  font-weight: 600;
  color: #1a1a1a;
  line-height: 1.2;
  margin-bottom: 2px;
}

.stat-label {
  font-size: 11px;
  color: #5f6368;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* 主要内容区域 */
.report-content {
  height: 100%;
  overflow: hidden;
}

/* 标签页导航 */
.tab-navigation {
  display: flex;
  background: #ffffff;
  border-bottom: 1px solid #e8eaed;
  padding: 0 24px;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  color: #5f6368;
  font-size: 13px;
  font-weight: 500;
  border-bottom: 2px solid transparent;
}

.tab-item:hover {
  color: #1a73e8;
  background: #f8f9fa;
}

.tab-item.active {
  color: #1a73e8;
  background: #f8f9fa;
  border-bottom-color: #1a73e8;
}

.tab-item i {
  font-size: 14px;
}

/* 主内容区域 */
.main-content {
  background: #ffffff;
  padding: 24px;
  height: calc(100% - 35px); /* 减去标签页导航的高度 */
  overflow-y: auto;
}

.tab-content {
  animation: fadeIn 0.3s ease-in-out;
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
.chart-card,
.log-container,
.report-container {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
}

.info-card {
  border-left: 4px solid #409eff;
}

.info-card,
.chart-card {
  padding: 20px;
}

.info-card h3,
.chart-card h3,
.log-header h3,
.report-header h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}

/* 悬停效果 */
.info-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
}

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

.log-header h3,
.report-header h3 {
  margin: 0;
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

.chart-container {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 8px;
}



/* 日志和报告部分 */
.logs-section,
.report-section {
  margin-bottom: 24px;
}

.log-container,
.report-container {
  overflow: hidden;
}

.log-header,
.report-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: #f8f9fa;
  border-bottom: 1px solid #e8eaed;
}

.copy-btn,
.open-report-btn {
  font-size: 12px;
  padding: 6px 12px;
  height: auto;
}

.log-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 20px;
  max-height: 300px;
  overflow-y: auto;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.log-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.no-report {
  text-align: center;
  padding: 40px;
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
  
  .stats-overview {
    flex-direction: column;
    gap: 0;
  }

  .stat-item {
    border-right: none;
    border-bottom: 1px solid #e8eaed;
  }

  .stat-item:last-child {
    border-bottom: none;
  }

  .tab-navigation {
    padding: 0 16px;
    overflow-x: auto;
  }

  .tab-item {
    padding: 12px 16px;
    white-space: nowrap;
  }

  .main-content {
    padding: 16px;
  }
}
</style>
