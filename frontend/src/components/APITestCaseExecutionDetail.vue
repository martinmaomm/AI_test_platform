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
                    <span class="label">Test Name:</span>
                    <span class="value">{{ result.name || 'N/A' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Status:</span>
                    <el-tag 
                      :type="getStatusType(result.status)" 
                      :effect="result.status === 'passed' ? 'light' : 'dark'"
                      size="small">
                      <i :class="getStatusIcon(result.status)"></i>
                      {{ getStatusText(result.status) }}
                    </el-tag>
                  </div>
                  <div class="info-item">
                    <span class="label">Start Time:</span>
                    <span class="value">{{ formatTime(result.start_time) || 'N/A' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Duration:</span>
                    <span class="value">{{ result.duration || 'N/A' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Environment:</span>
                    <span class="value">{{ getEnvironmentText(result.environment_name, result.environment_base_url) }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Triggered By:</span>
                    <span class="value">{{ result.triggered_by_username || 'N/A' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="label">Retry Count:</span>
                    <span class="value">{{ result.retry_count || 0 }}</span>
                  </div>
                </div>
              </div>

              <div class="chart-card">
                <div class="log-header">
                  <h3>Execution Logs</h3>
                  <el-button size="small" @click="copyLog" class="copy-btn">
                    <i class="el-icon-copy-document"></i>
                    Copy
                  </el-button>
                </div>
                <div class="log-content">
                  <pre>{{ getHttpRunnerLog() || 'No logs available' }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 测试步骤标签页 -->
        <div v-show="activeTab === 'steps'" class="tab-content">
          <div class="steps-section">
            <div v-if="getHttpRunnerStepDatas() && getHttpRunnerStepDatas().length > 0" class="steps-container">
              <div v-for="(step, index) in getHttpRunnerStepDatas()" :key="index" class="step-item">
                <!-- 请求响应详情 - Postman Console风格 -->
                <div v-if="step.data && step.data.req_resps && step.data.req_resps.length > 0">
                  <div v-for="(reqResp, reqIndex) in step.data.req_resps" :key="reqIndex" class="console-entry">
                    <!-- 合并的步骤头部和Console Entry Header -->
                    <div class="unified-console-header" :class="step.success ? 'success' : 'failed'" @click="toggleConsoleEntry(index, reqIndex)">
                      <div class="console-header-left">
                        <div class="step-number">{{ index + 1 }}</div>
                        <div class="console-info">
                          <div class="console-title">
                            <span class="step-name">{{ step.name || `Step ${index + 1}` }}</span>
                            <span class="meta-item">
                              <i class="el-icon-time"></i>
                              {{ step.data?.stat?.response_time_ms || 'N/A' }}ms
                            </span>
                            <span class="meta-item">
                              <i class="el-icon-document"></i>
                              {{ step.data?.stat?.content_size || 0 }} bytes
                            </span>
                            <span class="meta-item">
                              <i class="el-icon-check"></i>
                              {{ reqResp.response?.status_code || 'N/A' }}
                            </span>
                          </div>
                          <div class="console-request-info">
                            <span class="method-badge" :class="reqResp.request?.method?.toLowerCase()">
                              {{ reqResp.request?.method || 'N/A' }}
                            </span>
                            <span class="url-text">{{ reqResp.request?.url || 'N/A' }}</span>
                          </div>
                        </div>
                      </div>
                      <div class="console-header-right">
                        <span class="status-badge" :class="step.success ? 'passed' : 'failed'">
                          <i :class="step.success ? 'el-icon-check' : 'el-icon-close'"></i>
                          {{ step.success ? 'PASSED' : 'FAILED' }}
                        </span>
                        <div class="toggle-indicator">
                          <i :class="isConsoleEntryExpanded(index, reqIndex) ? 'el-icon-arrow-up' : 'el-icon-arrow-down'"></i>
                        </div>
                      </div>
                    </div>

                    <!-- Console Entry Details -->
                    <div v-show="isConsoleEntryExpanded(index, reqIndex)" class="console-details">
                      <!-- Request/Response/Validators Tabs -->
                      <div class="console-section req-resp-tabs">
                        <el-tabs v-model="activeReqRespTab[`${index}-${reqIndex}`]" type="card" class="req-resp-tabs-container">
                          <!-- Request Tab -->
                          <el-tab-pane label="Request" name="request">
                            <template #label>
                              <span class="tab-label">
                                <i class="el-icon-upload2"></i>
                                Request
                              </span>
                            </template>
                            <div class="tab-content">
                              <!-- Headers -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-document"></i>
                                  Headers
                                  <span class="count-badge">{{ reqResp.request?.headers ?
                                    Object.keys(reqResp.request.headers).length : 0 }}</span>
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.request?.headers && Object.keys(reqResp.request.headers).length > 0"
                                    class="key-value-list">
                                    <div v-for="(value, key) in reqResp.request.headers" :key="key"
                                      class="key-value-item">
                                      <span class="key">{{ key }}</span>
                                      <span class="value">{{ value }}</span>
                                    </div>
                                  </div>
                                  <div v-else class="empty-value">
                                    <i class="el-icon-warning"></i>
                                    No headers
                                  </div>
                                </div>
                              </div>

                              <!-- Cookies -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-cookie"></i>
                                  Cookies
                                  <span class="count-badge">{{ reqResp.request?.cookies ?
                                    Object.keys(reqResp.request.cookies).length : 0 }}</span>
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.request?.cookies && Object.keys(reqResp.request.cookies).length > 0"
                                    class="key-value-list">
                                    <div v-for="(value, key) in reqResp.request.cookies" :key="key"
                                      class="key-value-item">
                                      <span class="key">{{ key }}</span>
                                      <span class="value">{{ value }}</span>
                                    </div>
                                  </div>
                                  <div v-else class="empty-value">
                                    <i class="el-icon-warning"></i>
                                    No cookies
                                  </div>
                                </div>
                              </div>

                              <!-- Body -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-document-copy"></i>
                                  Body
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.request?.body !== null && reqResp.request?.body !== undefined && reqResp.request?.body !== ''"
                                    class="code-block">
                                    <pre class="code-content">{{ formatJson(reqResp.request.body) }}</pre>
                                  </div>
                                   <div v-else class="empty-value">
                                     <i class="el-icon-warning"></i>
                                     {{ reqResp.request?.body === null ? 'null' : reqResp.request?.body === '' ? 'Empty string' : 'No body content' }}
                                   </div>
                                </div>
                              </div>
                            </div>
                          </el-tab-pane>

                          <!-- Response Tab -->
                          <el-tab-pane label="Response" name="response">
                            <template #label>
                              <span class="tab-label">
                                <i class="el-icon-download"></i>
                                Response
                              </span>
                            </template>
                            <div class="tab-content">
                              <!-- Headers -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-document"></i>
                                  Headers
                                  <span class="count-badge">{{ reqResp.response?.headers ?
                                    Object.keys(reqResp.response.headers).length : 0
                                    }}</span>
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.response?.headers && Object.keys(reqResp.response.headers).length > 0"
                                    class="key-value-list">
                                    <div v-for="(value, key) in reqResp.response.headers" :key="key"
                                      class="key-value-item">
                                      <span class="key">{{ key }}</span>
                                      <span class="value">{{ value }}</span>
                                    </div>
                                  </div>
                                  <div v-else class="empty-value">
                                    <i class="el-icon-warning"></i>
                                    No headers
                                  </div>
                                </div>
                              </div>

                              <!-- Cookies -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-cookie"></i>
                                  Cookies
                                  <span class="count-badge">{{ reqResp.response?.cookies ?
                                    Object.keys(reqResp.response.cookies).length : 0
                                    }}</span>
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.response?.cookies && Object.keys(reqResp.response.cookies).length > 0"
                                    class="key-value-list">
                                    <div v-for="(value, key) in reqResp.response.cookies" :key="key"
                                      class="key-value-item">
                                      <span class="key">{{ key }}</span>
                                      <span class="value">{{ value }}</span>
                                    </div>
                                  </div>
                                  <div v-else class="empty-value">
                                    <i class="el-icon-warning"></i>
                                    No cookies
                                  </div>
                                </div>
                              </div>

                              <!-- Body -->
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-document-copy"></i>
                                  Body
                                </div>
                                <div class="detail-value">
                                  <div
                                    v-if="reqResp.response?.body !== null && reqResp.response?.body !== undefined && reqResp.response?.body !== ''"
                                    class="code-block">
                                    <pre class="code-content">{{ reqResp.response.body }}</pre>
                                  </div>
                                  <div v-else class="empty-value">
                                    <i class="el-icon-warning"></i>
                                    {{ reqResp.response?.body === '' ? 'Empty string' : reqResp.response?.body === null ? 'null' : 'No response body' }}
                                  </div>
                                </div>
                              </div>
                            </div>
                          </el-tab-pane>

                          <!-- Validators Tab -->
                          <el-tab-pane v-if="step.data?.validators && Object.keys(step.data.validators).length > 0"
                            label="Validators" name="validators">
                            <template #label>
                              <span class="tab-label">
                                <i class="el-icon-check"></i>
                                Validators
                                <span class="validators-count">{{ getValidatorsCount(step.data.validators, 'pass') +
                                  getValidatorsCount(step.data.validators, 'fail') }}</span>
                              </span>
                            </template>
                            <div class="tab-content">

                              <!-- Validators by Type -->
                              <div v-for="(validators, validatorType) in step.data.validators" :key="validatorType"
                                class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-document"></i>
                                  {{ getValidatorTypeTitle(validatorType) }}
                                  <span class="count-badge">{{ validators.length }}</span>
                                </div>
                                <div class="detail-value">
                                  <div class="validator-table-container">
                                    <el-table :data="validators" size="small" stripe :show-header="true"
                                      class="validator-table" :row-class-name="getValidatorRowClassName">
                                      <el-table-column prop="comparator" label="Comparator" width="100" align="center">
                                        <template #default="{ row }">
                                          <el-tag size="small" type="info">{{ row.comparator }}</el-tag>
                                        </template>
                                      </el-table-column>

                                      <el-table-column prop="check" label="Check" min-width="120" show-overflow-tooltip>
                                        <template #default="{ row }">
                                          <code class="check-field">{{ row.check }}</code>
                                        </template>
                                      </el-table-column>

                                      <el-table-column prop="check_value" label="Actual" min-width="100"
                                        show-overflow-tooltip>
                                        <template #default="{ row }">
                                          <span v-if="row.check_value !== null && row.check_value !== undefined"
                                            class="actual-value">
                                            {{ row.check_value }}
                                          </span>
                                          <span v-else class="null-value">
                                            <i class="el-icon-warning"></i>
                                            null
                                          </span>
                                        </template>
                                      </el-table-column>

                                      <el-table-column prop="expect_value" label="Expected" min-width="100"
                                        show-overflow-tooltip>
                                        <template #default="{ row }">
                                          <span v-if="row.expect_value !== null && row.expect_value !== undefined"
                                            class="expected-value">
                                            {{ row.expect_value }}
                                          </span>
                                          <span v-else-if="row.expect !== null && row.expect !== undefined"
                                            class="expected-value">
                                            {{ row.expect }}
                                          </span>
                                          <span v-else class="null-value">
                                            <i class="el-icon-warning"></i>
                                            null
                                          </span>
                                        </template>
                                      </el-table-column>

                                      <el-table-column prop="check_result" label="Result" width="80" align="center">
                                        <template #default="{ row }">
                                          <el-tag size="small"
                                            :type="row.check_result === 'pass' ? 'success' : 'danger'"
                                            :effect="row.check_result === 'pass' ? 'light' : 'dark'">
                                            <i
                                              :class="row.check_result === 'pass' ? 'el-icon-check' : 'el-icon-close'"></i>
                                            {{ row.check_result.toUpperCase() }}
                                          </el-tag>
                                        </template>
                                      </el-table-column>

                                      <el-table-column prop="message" label="Message" min-width="120"
                                        show-overflow-tooltip v-if="hasValidatorMessage(validators)">
                                        <template #default="{ row }">
                                          <span v-if="row.message" class="message-text">{{ row.message }}</span>
                                          <span v-else class="empty-message">-</span>
                                        </template>
                                      </el-table-column>
                                    </el-table>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </el-tab-pane>

                          <!-- Export Variables Tab -->
                          <el-tab-pane v-if="step.export_vars && Object.keys(step.export_vars).length > 0"
                            label="Export Variables" name="export_vars">
                            <template #label>
                              <span class="tab-label">
                                <i class="el-icon-download"></i>
                                Export Variables
                                <span class="count-badge">{{ Object.keys(step.export_vars).length }}</span>
                              </span>
                            </template>
                            <div class="tab-content">
                              <div class="detail-group">
                                <div class="detail-label">
                                  <i class="el-icon-download"></i>
                                  Variables
                                </div>
                                <div class="detail-value">
                                  <div class="key-value-list">
                                    <div v-for="(value, key) in step.export_vars" :key="key" class="key-value-item">
                                      <span class="key">{{ key }}</span>
                                      <span class="value">{{ value }}</span>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </el-tab-pane>
                        </el-tabs>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-else class="step-error-placeholder">
                  <div class="step-error-header">
                    <div class="step-error-title">
                      <span class="step-error-number">{{ index + 1 }}</span>
                      <span>{{ step.name || `Step ${index + 1}` }}</span>
                    </div>
                    <span class="status-badge failed">
                      <i class="el-icon-close"></i>
                      FAILED
                    </span>
                  </div>
                  <div class="step-error-message">
                    {{ step.error || '该步骤未生成响应快照，可能在变量替换、提取或断言阶段失败，请查看执行日志。' }}
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="no-steps">
              <el-empty description="No test steps available" />
            </div>
          </div>
        </div>


      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart, BarChart, LineChart, GaugeChart, RadarChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  RadarComponent
} from 'echarts/components'
import VChart from 'vue-echarts'
import * as echarts from 'echarts'
import dayjs from 'dayjs'

// 注册ECharts组件
use([
  CanvasRenderer,
  PieChart,
  BarChart,
  LineChart,
  GaugeChart,
  RadarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  RadarComponent
])

const props = defineProps({
  result: {
    type: Object,
    required: true
  }
})

// 计算属性：为了在模板中直接使用result
const result = computed(() => {
  // 如果result是统一响应格式，提取data字段
  if (props.result && props.result.success && props.result.data) {
    return props.result.data
  }
  // 否则直接返回result
  return props.result
})

// 响应式数据
const activeSteps = ref([])
const chartInstances = ref({})
const activeTab = ref('overview')
const collapsedSections = ref(new Set())
const expandedConsoleEntries = ref(new Set())
const collapsedConsoleItems = ref(new Set())
const activeReqRespTab = ref({})

// 标签页配置
const tabs = ref([
  { key: 'overview', label: 'Overview', icon: 'el-icon-data-analysis' },
  { key: 'steps', label: 'Test Steps', icon: 'el-icon-list' }
])

// 格式化时间
const formatTime = (timeStr) => {
  if (!timeStr) return 'N/A'
  return dayjs(timeStr).format('YYYY-MM-DD HH:mm:ss')
}

// 获取状态类型
const getStatusType = (status) => {
  const statusMap = {
    'passed': 'success',
    'failed': 'danger',
    'error': 'danger',
    'running': 'warning',
    'pending': 'info'
  }
  return statusMap[status] || 'info'
}

// 获取状态文本
const getStatusText = (status) => {
  const statusMap = {
    'passed': '通过',
    'failed': '失败',
    'error': '错误',
    'running': '执行中',
    'pending': '等待中'
  }
  return statusMap[status] || '未知'
}

// 获取环境文本（包含环境名和base_url）
const getEnvironmentText = (name, baseUrl) => {
  if (!name && !baseUrl) return 'N/A'
  if (name && baseUrl) {
    return `${name} (${baseUrl})`
  }
  return name || baseUrl || 'N/A'
}

// 获取状态码类型
const getStatusCodeType = (statusCode) => {
  if (!statusCode) return 'info'
  if (statusCode >= 200 && statusCode < 300) return 'success'
  if (statusCode >= 300 && statusCode < 400) return 'warning'
  if (statusCode >= 400) return 'danger'
  return 'info'
}

// 获取HTTP方法类型
const getMethodType = (method) => {
  const methodMap = {
    'GET': 'success',
    'POST': 'primary',
    'PUT': 'warning',
    'DELETE': 'danger',
    'PATCH': 'info'
  }
  return methodMap[method] || 'info'
}

// 格式化JSON
const formatJson = (obj) => {
  if (!obj) return ''
  if (typeof obj === 'string') {
    try {
      return JSON.stringify(JSON.parse(obj), null, 2)
    } catch {
      return obj
    }
  }
  return JSON.stringify(obj, null, 2)
}

// 获取状态样式类
const getStatusClass = (status) => {
  const statusMap = {
    'passed': 'passed',
    'failed': 'failed',
    'error': 'broken',
    'running': 'running',
    'pending': 'pending'
  }
  return statusMap[status] || 'unknown'
}

// 获取状态码样式类
const getStatusCodeClass = (statusCode) => {
  if (!statusCode) return 'unknown'
  if (statusCode >= 200 && statusCode < 300) return 'success'
  if (statusCode >= 300 && statusCode < 400) return 'redirect'
  if (statusCode >= 400 && statusCode < 500) return 'client-error'
  if (statusCode >= 500) return 'server-error'
  return 'unknown'
}

// 获取HTTP方法图标
const getMethodIcon = (method) => {
  const methodMap = {
    'GET': 'el-icon-download',
    'POST': 'el-icon-upload2',
    'PUT': 'el-icon-edit',
    'DELETE': 'el-icon-delete',
    'PATCH': 'el-icon-edit-outline',
    'HEAD': 'el-icon-view',
    'OPTIONS': 'el-icon-setting'
  }
  return methodMap[method?.toUpperCase()] || 'el-icon-document'
}

// 获取状态码图标
const getStatusCodeIcon = (statusCode) => {
  if (!statusCode) return 'el-icon-question'
  if (statusCode >= 200 && statusCode < 300) return 'el-icon-check'
  if (statusCode >= 300 && statusCode < 400) return 'el-icon-refresh'
  if (statusCode >= 400 && statusCode < 500) return 'el-icon-warning'
  if (statusCode >= 500) return 'el-icon-close'
  return 'el-icon-question'
}

// 获取状态图标
const getStatusIcon = (status) => {
  const iconMap = {
    'passed': 'el-icon-check',
    'failed': 'el-icon-close',
    'error': 'el-icon-warning',
    'running': 'el-icon-loading',
    'pending': 'el-icon-time'
  }
  return iconMap[status] || 'el-icon-question'
}

// 计算成功率
const getSuccessRate = () => {
  const actualResult = result.value
  const total = actualResult?.total_steps || 0
  const success = actualResult?.success_steps || 0
  if (total === 0) return 0
  return Math.round((success / total) * 100)
}

// 获取成功率颜色
const getSuccessRateColor = () => {
  const rate = getSuccessRate()
  if (rate >= 90) return '#67c23a'
  if (rate >= 70) return '#e6a23c'
  return '#f56c6c'
}

// 导出报告
const exportReport = () => {
  // 这里可以实现导出功能
  ElMessage.success('报告导出功能开发中...')
}

// 复制日志
const copyLog = async () => {
  try {
    await navigator.clipboard.writeText(getHttpRunnerLog())
    ElMessage.success('日志已复制到剪贴板')
  } catch (err) {
    ElMessage.error('复制失败')
  }
}

// 下载日志
const downloadLog = () => {
  const logContent = getHttpRunnerLog()
  const blob = new Blob([logContent], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `test-log-${props.result.id || 'unknown'}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('日志下载成功')
}

// 显示配置变量
const showConfigVars = () => {
  const configVars = getHttpRunnerConfigVars()
  const configText = JSON.stringify(configVars, null, 2)
  ElMessageBox.alert(configText, 'Configuration Variables', {
    confirmButtonText: 'OK',
    type: 'info'
  })
}

// 显示导出变量
const showExportVars = () => {
  const exportVars = getHttpRunnerExportVars()
  const exportText = JSON.stringify(exportVars, null, 2)
  ElMessageBox.alert(exportText, 'Export Variables', {
    confirmButtonText: 'OK',
    type: 'info'
  })
}

// 刷新数据
const refreshData = () => {
  ElMessage.info('数据刷新功能开发中...')
}

// 获取整体状态类型
const getOverallStatusType = () => {
  const actualResult = result.value
  const success = actualResult?.success_steps || 0
  const total = actualResult?.total_steps || 0
  if (total === 0) return 'info'
  const rate = (success / total) * 100
  if (rate >= 90) return 'success'
  if (rate >= 70) return 'warning'
  return 'danger'
}

// 获取整体状态文本
const getOverallStatusText = () => {
  const actualResult = result.value
  const success = actualResult?.success_steps || 0
  const total = actualResult?.total_steps || 0
  if (total === 0) return '无数据'
  const rate = (success / total) * 100
  if (rate >= 90) return '优秀'
  if (rate >= 70) return '良好'
  return '需改进'
}

// 获取validators统计数量
const getValidatorsCount = (validators, resultType) => {
  if (!validators) return 0
  
  // 如果validators是对象，转换为数组
  let validatorsArray = []
  if (Array.isArray(validators)) {
    validatorsArray = validators
  } else if (typeof validators === 'object') {
    // 如果是对象，可能是按类型分组的validators
    validatorsArray = Object.values(validators).flat()
  }
  
  // 根据resultType过滤
  if (resultType === 'pass') {
    return validatorsArray.filter(v => v.check_result === 'pass' || v.status === 'success').length
  } else if (resultType === 'fail') {
    return validatorsArray.filter(v => v.check_result === 'fail' || v.status === 'failed').length
  }
  
  return validatorsArray.length
}

// 获取validator表格行样式类名
const getValidatorRowClassName = ({ row }) => {
  return row.status === 'success' ? 'validator-row-pass' : 'validator-row-fail'
}

// 检查是否有validator消息
const hasValidatorMessage = (validators) => {
  return validators.some(v => v.message && v.message.trim())
}

// 获取validator类型标题
const getValidatorTypeTitle = (validatorType) => {
  const typeMap = {
    'validate_extractor': '断言验证',
    'validate_status_code': '状态码验证',
    'validate_headers': '响应头验证',
    'validate_response': '响应内容验证',
    'validate_json': 'JSON验证',
    'validate_schema': 'Schema验证',
    'validate_contains': '包含验证',
    'validate_not_contains': '不包含验证',
    'validate_regex': '正则验证',
    'validate_length': '长度验证',
    'validate_type': '类型验证'
  }
  return typeMap[validatorType] || validatorType
}

// 折叠功能
const toggleCollapse = (sectionType, reqIndex) => {
  const key = `${sectionType}-${reqIndex}`
  if (collapsedSections.value.has(key)) {
    collapsedSections.value.delete(key)
  } else {
    collapsedSections.value.add(key)
  }
}

const isCollapsed = (sectionType, reqIndex) => {
  const key = `${sectionType}-${reqIndex}`
  return collapsedSections.value.has(key)
}

// Console Entry 折叠功能
const toggleConsoleEntry = (stepIndex, reqIndex) => {
  const key = `${stepIndex}-${reqIndex}`
  if (expandedConsoleEntries.value.has(key)) {
    expandedConsoleEntries.value.delete(key)
  } else {
    expandedConsoleEntries.value.add(key)
  }
}

const isConsoleEntryExpanded = (stepIndex, reqIndex) => {
  const key = `${stepIndex}-${reqIndex}`
  return expandedConsoleEntries.value.has(key)
}

// Console Item 折叠功能
const toggleConsoleItem = (itemType, reqIndex) => {
  const key = `${itemType}-${reqIndex}`
  if (collapsedConsoleItems.value.has(key)) {
    collapsedConsoleItems.value.delete(key)
  } else {
    collapsedConsoleItems.value.add(key)
  }
}

const isConsoleItemCollapsed = (itemType, reqIndex) => {
  const key = `${itemType}-${reqIndex}`
  return collapsedConsoleItems.value.has(key)
}

// HttpRunner原始结果数据处理方法
const getHttpRunnerRawResult = () => {
  // 获取实际的数据对象（处理统一响应格式）
  const actualResult = result.value
  const httprunnerResult = actualResult?.httprunner_result || {}
  
  // 新数据结构：httprunner_result.results[0] 包含主要信息
  if (httprunnerResult.results && httprunnerResult.results.length > 0) {
    return httprunnerResult.results[0] || {}
  }
  // 兼容旧数据结构
  return httprunnerResult
}

const getHttpRunnerName = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.name || ''
}

const getHttpRunnerCaseId = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.case_id || ''
}

const getHttpRunnerDuration = () => {
  const rawResult = getHttpRunnerRawResult()
  const timeInfo = rawResult.time || {}
  return timeInfo.duration ? timeInfo.duration.toFixed(3) : 'N/A'
}

const getHttpRunnerStartTime = () => {
  const rawResult = getHttpRunnerRawResult()
  const timeInfo = rawResult.time || {}
  if (timeInfo.start_at_iso_format) {
    return dayjs(timeInfo.start_at_iso_format).format('YYYY-MM-DD HH:mm:ss')
  }
  return 'N/A'
}

const getHttpRunnerStepDatas = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.step_datas || []
}

const getHttpRunnerConfigVars = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.in_out?.config_vars || {}
}

const getHttpRunnerExportVars = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.in_out?.export_vars || {}
}

const getHttpRunnerLog = () => {
  const rawResult = getHttpRunnerRawResult()
  const actualResult = result.value
  
  // 优先返回log，如果log存在且不为空
  if (rawResult.log && rawResult.log.trim()) {
    return rawResult.log
  }
  
  // 如果log为空，优先返回httprunner_result中的error
  if (rawResult.error && rawResult.error.trim()) {
    return rawResult.error
  }
  
  // 如果都没有，返回顶层的error_message
  if (actualResult?.error_message && actualResult.error_message.trim()) {
    return actualResult.error_message
  }
  
  // 最后返回空字符串
  return ''
}

const getHttpRunnerSuccess = () => {
  const rawResult = getHttpRunnerRawResult()
  return rawResult.success || false
}

// 迷你饼图配置
const miniPieOption = computed(() => {
  const success = props.result.success_steps || 0
  const failure = props.result.failure_steps || 0
  const error = props.result.error_steps || 0

  return {
    series: [
      {
        type: 'pie',
        radius: ['50%', '70%'],
        center: ['50%', '50%'],
        data: [
          { value: success, name: 'Passed', itemStyle: { color: '#67c23a' } },
          { value: failure, name: 'Failed', itemStyle: { color: '#f56c6c' } },
          { value: error, name: 'Broken', itemStyle: { color: '#e6a23c' } }
        ],
        label: {
          show: false
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0, 0, 0, 0.5)'
          }
        }
      }
    ]
  }
})

// 执行统计饼图配置
const executionPieChartOption = computed(() => {
  const total = props.result.total_steps || 0
  const success = props.result.success_steps || 0
  const failure = props.result.failure_steps || 0
  const error = props.result.error_steps || 0

  return {
    title: {
      text: '执行结果分布',
      left: 'center',
      textStyle: {
        fontSize: 16,
        fontWeight: 'bold',
        color: '#303133'
      }
    },
    tooltip: {
      trigger: 'item',
      formatter: '{a} <br/>{b}: {c} ({d}%)'
    },
    legend: {
      orient: 'vertical',
      left: 'left',
      top: 'middle',
      textStyle: {
        fontSize: 12
      }
    },
    series: [
      {
        name: '执行结果',
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['60%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 8,
          borderColor: '#fff',
          borderWidth: 2
        },
        label: {
          show: false,
          position: 'center'
        },
        emphasis: {
          label: {
            show: true,
            fontSize: '18',
            fontWeight: 'bold'
          }
        },
        labelLine: {
          show: false
        },
        data: [
          { value: success, name: '成功', itemStyle: { color: '#67c23a' } },
          { value: failure, name: '失败', itemStyle: { color: '#f56c6c' } },
          { value: error, name: '错误', itemStyle: { color: '#e6a23c' } }
        ]
      }
    ]
  }
})

// 成功率仪表盘配置
const successRateGaugeOption = computed(() => {
  const rate = getSuccessRate()
  return {
    series: [
      {
        name: '成功率',
        type: 'gauge',
        center: ['50%', '60%'],
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 100,
        splitNumber: 10,
        itemStyle: {
          color: getSuccessRateColor()
        },
        progress: {
          show: true,
          width: 18
        },
        pointer: {
          show: false
        },
        axisLine: {
          lineStyle: {
            width: 18
          }
        },
        axisTick: {
          distance: -30,
          splitNumber: 5,
          lineStyle: {
            width: 2,
            color: '#999'
          }
        },
        splitLine: {
          distance: -30,
          length: 30,
          lineStyle: {
            width: 4,
            color: '#999'
          }
        },
        axisLabel: {
          distance: -20,
          color: '#999',
          fontSize: 12
        },
        detail: {
          valueAnimation: true,
          formatter: '{value}%',
          color: 'inherit',
          fontSize: 20,
          fontWeight: 'bold',
          offsetCenter: [0, '70%']
        },
        data: [
          {
            value: rate
          }
        ]
      }
    ]
  }
})

// 响应时间分布图配置
const responseTimeChartOption = computed(() => {
  const stepData = getHttpRunnerStepDatas()
  const responseTimes = stepData
    .filter(step => step.data?.stat?.response_time_ms)
    .map((step, index) => ({
      name: `步骤${index + 1}`,
      value: step.data.stat.response_time_ms
    }))

  return {
    title: {
      text: '响应时间分布',
      left: 'center',
      textStyle: {
        fontSize: 16,
        fontWeight: 'bold',
        color: '#303133'
      }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      },
      formatter: function (params) {
        return `${params[0].name}<br/>响应时间: ${params[0].value}ms`
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: responseTimes.map(item => item.name),
      axisLabel: {
        rotate: 45,
        fontSize: 10
      }
    },
    yAxis: {
      type: 'value',
      name: '响应时间(ms)',
      nameTextStyle: {
        fontSize: 12
      }
    },
    series: [
      {
        name: '响应时间',
        type: 'bar',
        data: responseTimes.map(item => item.value),
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#83bff6' },
            { offset: 0.5, color: '#188df0' },
            { offset: 1, color: '#188df0' }
          ])
        },
        emphasis: {
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#2378f7' },
              { offset: 0.7, color: '#2378f7' },
              { offset: 1, color: '#83bff6' }
            ])
          }
        }
      }
    ]
  }
})

// 执行时间线图配置
const timelineChartOption = computed(() => {
  const stepData = getHttpRunnerStepDatas()
  const timeline = stepData.map((step, index) => ({
    name: `步骤${index + 1}`,
    start: index * 1000, // 模拟开始时间
    end: (index + 1) * 1000 + (step.data?.stat?.response_time_ms || 0), // 模拟结束时间
    status: step.success ? 'success' : 'failed'
  }))

  return {
    title: {
      text: '执行时间线',
      left: 'center',
      textStyle: {
        fontSize: 16,
        fontWeight: 'bold',
        color: '#303133'
      }
    },
    tooltip: {
      trigger: 'axis',
      formatter: function (params) {
        const data = params[0]
        return `${data.name}<br/>状态: ${data.data.status === 'success' ? '成功' : '失败'}<br/>耗时: ${data.data.end - data.data.start}ms`
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'value',
      name: '时间(ms)',
      nameTextStyle: {
        fontSize: 12
      }
    },
    yAxis: {
      type: 'category',
      data: timeline.map(item => item.name),
      axisLabel: {
        fontSize: 12
      }
    },
    series: [
      {
        name: '执行时间',
        type: 'bar',
        data: timeline.map(item => ({
          value: [item.start, item.end],
          itemStyle: {
            color: item.status === 'success' ? '#67c23a' : '#f56c6c'
          }
        })),
        barWidth: '60%'
      }
    ]
  }
})

// 性能指标雷达图配置
const performanceRadarOption = computed(() => {
  const total = props.result.total_steps || 0
  const success = props.result.success_steps || 0
  const duration = props.result.httprunner_duration_seconds || 0

  return {
    title: {
      text: '性能指标',
      left: 'center',
      textStyle: {
        fontSize: 16,
        fontWeight: 'bold',
        color: '#303133'
      }
    },
    radar: {
      indicator: [
        { name: '成功率', max: 100 },
        { name: '执行速度', max: 100 },
        { name: '稳定性', max: 100 },
        { name: '覆盖率', max: 100 },
        { name: '可靠性', max: 100 }
      ],
      center: ['50%', '50%'],
      radius: '70%',
      name: {
        textStyle: {
          fontSize: 12
        }
      }
    },
    series: [
      {
        name: '性能指标',
        type: 'radar',
        data: [
          {
            value: [
              getSuccessRate(),
              Math.max(0, 100 - duration * 10), // 执行速度
              getSuccessRate(), // 稳定性
              total > 0 ? 100 : 0, // 覆盖率
              getSuccessRate() // 可靠性
            ],
            name: '当前测试',
            itemStyle: {
              color: '#409eff'
            },
            areaStyle: {
              color: 'rgba(64, 158, 255, 0.2)'
            }
          }
        ]
      }
    ]
  }
})

// 初始化请求响应标签页状态
const initializeReqRespTabs = () => {
  const stepDatas = getHttpRunnerStepDatas()
  stepDatas.forEach((step, stepIndex) => {
    if (step.data?.req_resps) {
      step.data.req_resps.forEach((reqResp, reqIndex) => {
        const key = `${stepIndex}-${reqIndex}`
        if (!activeReqRespTab.value[key]) {
          // 根据可用的标签页设置默认值
          if (step.export_vars && Object.keys(step.export_vars).length > 0) {
            activeReqRespTab.value[key] = 'export_vars'
          } else if (step.data?.validators && Object.keys(step.data.validators).length > 0) {
            activeReqRespTab.value[key] = 'validators'
          } else {
            activeReqRespTab.value[key] = 'request'
          }
        }
      })
    }
  })
}

// 组件挂载后初始化图表
onMounted(() => {
  nextTick(() => {
    // 图表会在模板中自动渲染
    initializeReqRespTabs()
  })
})
</script>

<style scoped>
/* 测试报告主容器 */
.test-report-container {
  height: 500px;
  background: #fafbfc;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
  position: relative;
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

.tab-content h2 {
  margin: 0 0 20px 0;
  font-size: 20px;
  font-weight: 600;
  color: #2c3e50;
}

/* 概览区域 */
.overview-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.info-card,
.chart-card {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
}

.info-card h3,
.chart-card h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
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
  border-bottom: 1px solid #f1f3f4;
}

.info-item:last-child {
  border-bottom: none;
}

.info-item .label {
  font-weight: 500;
  color: #5f6368;
  font-size: 13px;
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

/* 日志部分 */
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

/* 悬停效果 */
.info-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
}

/* 测试步骤区域 */
.steps-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.step-item {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  transition: all 0.2s ease;
}

.step-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transform: translateY(-1px);
}

.step-error-placeholder {
  padding: 12px 14px;
  background: #fff8f8;
  border-left: 3px solid #ea4335;
}

.step-error-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #2c3e50;
  font-size: 13px;
  font-weight: 600;
}

.step-error-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.step-error-number {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ea4335;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 11px;
}

.step-error-message {
  margin: 8px 0 0 28px;
  color: #b42318;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.step-header {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid #e8eaed;
}

.step-header.passed {
  background: #f8f9fa;
  border-bottom-color: #34a853;
}

.step-header.failed {
  background: #f8f9fa;
  border-bottom-color: #ea4335;
}

.step-number {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #5f6368;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 11px;
  margin-right: 10px;
}

.step-header.passed .step-number {
  background: #34a853;
}

.step-header.failed .step-number {
  background: #ea4335;
}

.step-info {
  flex: 1;
}

.step-name {
  font-size: 13px;
  font-weight: 500;
  color: #1a1a1a;
  margin-bottom: 2px;
}

.step-status {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.step-header.passed .step-status {
  color: #34a853;
}

.step-header.failed .step-status {
  color: #ea4335;
}

.step-duration {
  font-size: 11px;
  color: #5f6368;
  font-weight: 400;
}

/* step-details样式已移除，布局已简化 */


/* Postman Console 风格样式 */

.console-entry {
  background: #ffffff;
  border: 1px solid #e1e5e9;
  border-radius: 6px;
  margin-bottom: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.console-entry:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  border-color: #409eff;
}

.console-entry:last-child {
  margin-bottom: 0;
}

/* 合并的步骤头部和Console Entry Header */
.unified-console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e1e5e9;
  cursor: pointer;
  transition: all 0.2s ease;
  background: #ffffff;
  border-radius: 6px 6px 0 0;
}

.unified-console-header:hover {
  background: #f8f9fa;
}

.unified-console-header.success {
  border-left: 4px solid #67c23a;
}

.unified-console-header.failed {
  border-left: 4px solid #f56c6c;
}

.unified-console-header .console-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.unified-console-header .step-number {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #409eff;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.unified-console-header .toggle-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #606266;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.unified-console-header .console-info {
  flex: 1;
  min-width: 0;
}

.unified-console-header .console-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.unified-console-header .step-name {
  font-weight: 600;
  color: #2c3e50;
  font-size: 13px;
  margin-right: 8px;
  flex-shrink: 0;
}

.unified-console-header .console-request-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.unified-console-header .method-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  color: white;
  flex-shrink: 0;
}

.unified-console-header .url-text {
  color: #606266;
  font-size: 12px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  word-break: break-all;
  flex: 1;
  min-width: 0;
  max-width: 600px;
}

.unified-console-header .console-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.unified-console-header .meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #606266;
  white-space: nowrap;
}

.unified-console-header .meta-item i {
  font-size: 12px;
  color: #909399;
}

.unified-console-header .status-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
}

.unified-console-header .status-badge.passed {
  background: #f0f9ff;
  color: #67c23a;
  border: 1px solid #b3e19d;
}

.unified-console-header .status-badge.failed {
  background: #fef0f0;
  color: #f56c6c;
  border: 1px solid #fbc4c4;
}

.unified-console-header .console-header-right .status-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.unified-console-header .console-header-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  flex-shrink: 0;
}

.unified-console-header .toggle-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #606266;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

/* 保持原有的console-header样式作为备用 */
.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e1e5e9;
  cursor: pointer;
  transition: all 0.2s ease;
}

.console-header:hover {
  background: #f8f9fa;
}

.console-header.success {
  border-left: 4px solid #67c23a;
}

.console-header.failed {
  border-left: 4px solid #f56c6c;
}

.console-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}


.console-info {
  flex: 1;
  min-width: 0;
}

.console-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.method-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  color: white;
  min-width: 40px;
  text-align: center;
}

.method-badge.get {
  background: #67c23a;
}

.method-badge.post {
  background: #409eff;
}

.method-badge.put {
  background: #e6a23c;
}

.method-badge.delete {
  background: #f56c6c;
}

.method-badge.patch {
  background: #909399;
}

.url-text {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.console-meta {
  display: flex;
  gap: 16px;
  font-size: 11px;
  color: #909399;
}

.console-meta .meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.console-header-right {
  display: flex;
  align-items: center;
}


.console-details {
  background: #fafbfc;
  border-top: 1px solid #e1e5e9;
}

.console-section {
  border-bottom: 1px solid #e1e5e9;
}

.console-section:last-child {
  border-bottom: none;
}

.console-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #f8f9fa;
  font-weight: 600;
  font-size: 13px;
  color: #303133;
  border-bottom: 1px solid #e1e5e9;
}

.console-section-header i {
  font-size: 14px;
}

.request-section .console-section-header {
  color: #409eff;
}

.response-section .console-section-header {
  color: #67c23a;
}

.validators-section .console-section-header {
  color: #e6a23c;
}

/* Request/Response Tabs Styles */
.req-resp-tabs {
  background: #ffffff;
  border-radius: 8px;
  overflow: hidden;
}

.req-resp-tabs-container {
  margin: 0;
}

.req-resp-tabs-container :deep(.el-tabs__header) {
  margin: 0;
  background: #f8f9fa;
  border-bottom: 1px solid #e1e5e9;
}

.req-resp-tabs-container :deep(.el-tabs__nav-wrap) {
  padding: 0 16px;
}

.req-resp-tabs-container :deep(.el-tabs__nav) {
  border: none;
}

.req-resp-tabs-container :deep(.el-tabs__item) {
  border: none;
  background: transparent;
  color: #606266;
  font-weight: 500;
  font-size: 13px;
  padding: 12px 16px;
  margin-right: 4px;
  border-radius: 6px 6px 0 0;
  transition: all 0.2s ease;
}

.req-resp-tabs-container :deep(.el-tabs__item:hover) {
  color: #409eff;
  background: #f0f9ff;
}

.req-resp-tabs-container :deep(.el-tabs__item.is-active) {
  color: #409eff;
  background: #ffffff;
  border-bottom: 2px solid #409eff;
  font-weight: 600;
}

.req-resp-tabs-container :deep(.el-tabs__content) {
  padding: 0;
}

.req-resp-tabs-container :deep(.el-tab-pane) {
  padding: 16px;
}

.tab-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tab-label i {
  font-size: 14px;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Count Badge Styles */
.count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 2px 6px;
  background: #409eff;
  color: white;
  border-radius: 10px;
  font-size: 10px;
  font-weight: 600;
  margin-left: 8px;
}

.validators-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 1px 5px;
  background: #67c23a;
  color: white;
  border-radius: 9px;
  font-size: 9px;
  font-weight: 600;
  margin-left: 6px;
}

/* Value Text Styles */
.value-text {
  color: #303133;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #f8f9fa;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  font-size: 12px;
}

.console-section-content {
  padding: 0;
}

.console-item {
  border-bottom: 1px solid #f0f0f0;
}

.console-item:last-child {
  border-bottom: none;
}

.console-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 12px;
  color: #606266;
}

.console-item-header:hover {
  background: #f8f9fa;
  color: #303133;
}

.console-item-header i:first-child {
  font-size: 14px;
  color: #909399;
}

.console-item-header i:last-child {
  margin-left: auto;
  font-size: 12px;
  transition: transform 0.2s ease;
}

.console-item-content {
  padding: 0 16px 12px 16px;
  background: #ffffff;
}

.key-value-list {
  background: #f8f9fa;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  max-height: 200px;
  overflow-y: auto;
}

.key-value-item {
  display: flex;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 11px;
}

.key-value-item:last-child {
  border-bottom: none;
}

.key-value-item:hover {
  background: #f0f9ff;
}

.key-value-item .key {
  font-weight: 600;
  color: #606266;
  min-width: 120px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

.key-value-item .value {
  color: #303133;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  word-break: break-all;
  flex: 1;
}

.code-block {
  background: #1e1e1e;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  max-height: 300px;
  overflow-y: auto;
}

.code-content {
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  line-height: 1.4;
  padding: 12px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  color: white;
}

.status-badge.success {
  background: #67c23a;
}

.status-badge.redirect {
  background: #409eff;
}

.status-badge.client-error {
  background: #e6a23c;
}

.status-badge.server-error {
  background: #f56c6c;
}

.status-badge.unknown {
  background: #909399;
}

/* Validators Console Styles */

.validator-list {
  background: #f8f9fa;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  max-height: 500px;
  overflow-y: auto;
}

.validator-item {
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #ffffff;
  margin-bottom: 1px;
  min-height: 80px;
}

.validator-item:last-child {
  border-bottom: none;
  margin-bottom: 0;
}

.validator-item:hover {
  background: #f0f9ff;
}

.validator-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.validator-comparator {
  display: flex;
  align-items: center;
  gap: 8px;
}

.comparator-label {
  font-size: 11px;
  font-weight: 600;
  color: #7f8c8d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.comparator-value {
  font-size: 12px;
  font-weight: 600;
  color: #2c3e50;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #f8f9fa;
  padding: 2px 6px;
  border-radius: 3px;
}

.validator-result {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.validator-result.pass {
  background: #d4edda;
  color: #155724;
}

.validator-result.fail {
  background: #f8d7da;
  color: #721c24;
}

.validator-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  font-size: 11px;
}

.validator-check,
.validator-expectation,
.validator-actual,
.validator-message {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.validator-message {
  grid-column: 1 / -1;
}

.check-label,
.expect-label,
.actual-label,
.message-label {
  font-weight: 600;
  color: #606266;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.check-value,
.expect-value,
.actual-value,
.message-value {
  color: #303133;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  word-break: break-all;
  background: #f8f9fa;
  padding: 4px 6px;
  border-radius: 3px;
  border: 1px solid #e1e5e9;
}

/* 断言表格样式 */
.validator-table-container {
  background: #ffffff;
  border-radius: 6px;
  border: 1px solid #e1e5e9;
  overflow: hidden;
}

.validator-table {
  font-size: 12px;
}

.validator-table .el-table__header {
  background: #f8f9fa;
}

.validator-table .el-table__header th {
  background: #f8f9fa !important;
  color: #606266;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  padding: 8px 0;
  border-bottom: 2px solid #e1e5e9;
}

.validator-table .el-table__body tr {
  height: 40px;
}

.validator-table .el-table__body td {
  padding: 6px 8px;
  border-bottom: 1px solid #f0f0f0;
  vertical-align: middle;
}

.validator-row-pass {
  background-color: #f0f9ff;
}

.validator-row-fail {
  background-color: #fef0f0;
}

.validator-table .el-table__body tr:hover {
  background-color: #f5f7fa !important;
}

.check-field {
  background: #f5f7fa;
  color: #409eff;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  border: 1px solid #e1e5e9;
}

.actual-value,
.expected-value {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  color: #303133;
  background: #f8f9fa;
  padding: 2px 6px;
  border-radius: 3px;
  border: 1px solid #e1e5e9;
  display: inline-block;
  max-width: 100%;
  word-break: break-all;
}

.null-value {
  color: #909399;
  font-style: italic;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}

.null-value i {
  font-size: 12px;
  color: #e6a23c;
}

.message-text {
  color: #606266;
  font-size: 11px;
  font-style: italic;
}

.empty-message {
  color: #c0c4cc;
  font-size: 11px;
}

/* 旧的断言样式已移除，现在使用表格形式 */

.req-resp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f8f9fa;
  border-bottom: 1px solid #e8eaed;
}

.req-resp-title {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
}

.req-resp-tags {
  display: flex;
  gap: 8px;
}

.method-tag {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: white;
}

.method-tag.get {
  background: #28a745;
}

.method-tag.post {
  background: #007bff;
}

.method-tag.put {
  background: #ffc107;
  color: #212529;
}

.method-tag.delete {
  background: #dc3545;
}

.status-tag {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  color: white;
}

.status-tag.success {
  background: #28a745;
}

.status-tag.redirect {
  background: #17a2b8;
}

.status-tag.client-error {
  background: #ffc107;
  color: #212529;
}

.status-tag.server-error {
  background: #dc3545;
}

.status-tag.unknown {
  background: #6c757d;
}

.req-resp-content {
  padding: 16px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.request-section,
.response-section {
  background: #f8f9fa;
  border-radius: 4px;
  padding: 12px;
}

.request-section h4,
.response-section h4 {
  margin: 0 0 12px 0;
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
}

.detail-item {
  margin-bottom: 8px;
}

.detail-item:last-child {
  margin-bottom: 0;
}

.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: #7f8c8d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: block;
  margin-bottom: 4px;
}

.detail-value {
  font-size: 13px;
  color: #2c3e50;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #ffffff;
  padding: 8px;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 附件区域 */
.attachments-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.attachment-item {
  display: flex;
  align-items: center;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #e1e5e9;
  border-radius: 8px;
}

.attachment-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: #e3f2fd;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 16px;
  color: #1976d2;
  font-size: 18px;
}

.attachment-info {
  flex: 1;
}

.attachment-name {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
  margin-bottom: 4px;
}

.attachment-size {
  font-size: 12px;
  color: #7f8c8d;
}

.attachment-actions {
  display: flex;
  gap: 8px;
}

/* 时间线区域 */
.timeline-charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.no-steps {
  text-align: center;
  padding: 40px 20px;
  color: #7f8c8d;
}

.info-card {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 20px;
  border-left: 4px solid #409eff;
}

.info-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  font-weight: 600;
  color: #303133;
}

.info-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.info-item label {
  font-weight: 500;
  color: #606266;
  min-width: 80px;
}

.info-item span {
  color: #303133;
  font-weight: 500;
}

.duration {
  color: #409eff;
  font-weight: 600;
}

/* 统计信息区域 */
.statistics-section {
  margin-bottom: 24px;
}

.statistics-section h4 {
  margin-bottom: 20px;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.stat-card {
  background: white;
  border-radius: 8px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.05);
  transition: transform 0.2s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: white;
}

.stat-card.total .stat-icon {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.stat-card.success .stat-icon {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.stat-card.failure .stat-icon {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.stat-card.error .stat-icon {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.stat-content {
  flex: 1;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
  line-height: 1;
}

.stat-label {
  font-size: 14px;
  color: #909399;
  margin-top: 4px;
}

/* 成功率展示 */
.success-rate {
  margin-top: 20px;
  padding: 20px;
  background: #f8f9fa;
  border-radius: 8px;
}

.rate-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.rate-label {
  font-weight: 600;
  color: #303133;
}

.rate-value {
  font-size: 18px;
  font-weight: 700;
  color: #409eff;
}

/* 图表展示区域 */
.charts-section {
  margin-bottom: 24px;
}

.charts-section h4 {
  margin-bottom: 20px;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
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

@keyframes gradientShift {

  0%,
  100% {
    background-position: 0% 50%;
  }

  50% {
    background-position: 100% 50%;
  }
}

/* 配置信息区域 */
.config-section {
  margin-bottom: 0;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}

.config-card {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: all 0.2s ease;
}

.config-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.config-card h3 {
  margin: 0 0 12px 0;
  color: #1a1a1a;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

.config-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.config-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f8f9fa;
  border-radius: 6px;
  border-left: 3px solid #1a73e8;
  transition: all 0.2s ease;
}

.config-item:hover {
  background: #f1f3f4;
  border-left-color: #4285f4;
}

.config-key {
  font-weight: 500;
  color: #5f6368;
  font-size: 12px;
  min-width: 100px;
}

.config-value {
  color: #1a1a1a;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  background: #ffffff;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #e8eaed;
  word-break: break-all;
}

/* 日志区域样式 */
.logs-section {
  margin-bottom: 24px;
}

.log-container {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: #f8f9fa;
  border-bottom: 1px solid #e8eaed;
}

.log-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}

.copy-btn {
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


/* 步骤详情区域 */
.step-summary {
  color: #909399;
  font-size: 14px;
}

.step-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-number {
  background: #409eff;
  color: white;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
}

.step-name {
  font-weight: 500;
  color: #303133;
}

.step-basic-info {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.info-row {
  display: flex;
  gap: 24px;
}

.info-row .info-item {
  flex: 1;
}

/* 请求响应区域 */
.request-response {
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 12px;
  margin-bottom: 20px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;
}

.request-response:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  transform: translateY(-1px);
}

.req-resp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: linear-gradient(135deg, #f8f9fa, #e9ecef);
  border-bottom: 1px solid #e4e7ed;
}

.req-resp-title-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.req-resp-title {
  margin: 0;
  color: #303133;
  font-size: 14px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 6px;
}

.req-resp-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #606266;
}

.req-resp-meta .meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}

.req-resp-tags {
  display: flex;
  gap: 8px;
}

.method-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: white;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}

.method-tag.get {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.method-tag.post {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.method-tag.put {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.method-tag.delete {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.method-tag.patch {
  background: linear-gradient(135deg, #909399, #a6a9ad);
}

.status-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  color: white;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}

.status-tag.success {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.status-tag.redirect {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.status-tag.client-error {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.status-tag.server-error {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.status-tag.unknown {
  background: linear-gradient(135deg, #909399, #a6a9ad);
}

.req-resp-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  min-height: 200px;
}

.request-section,
.response-section {
  padding: 12px;
  border-right: 1px solid #e8eaed;
}

.response-section {
  border-right: none;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-weight: 700;
  color: #303133;
  font-size: 14px;
  padding-bottom: 8px;
  border-bottom: 2px solid #e4e7ed;
}

.section-badge {
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-left: auto;
}

.request-badge {
  background: linear-gradient(135deg, #409eff, #66b1ff);
  color: white;
}

.response-badge {
  background: linear-gradient(135deg, #67c23a, #85ce61);
  color: white;
}

.request-section .section-header {
  color: #409eff;
  border-bottom-color: #409eff;
}

.response-section .section-header {
  color: #67c23a;
  border-bottom-color: #67c23a;
}

.request-details,
.response-details {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-group {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 10px;
  border-left: 3px solid #e4e7ed;
  transition: all 0.2s ease;
}

.detail-group:hover {
  background: #f0f9ff;
  border-left-color: #409eff;
}

.detail-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #606266;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 6px;
}

.collapse-btn {
  margin-left: auto;
  padding: 2px 6px;
  font-size: 10px;
  color: #409eff;
  border: none;
  background: none;
  cursor: pointer;
  transition: all 0.2s ease;
}

.collapse-btn:hover {
  color: #66b1ff;
  background: #f0f9ff;
  border-radius: 4px;
}

.body-content {
  position: relative;
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.detail-value {
  color: #303133;
  font-size: 12px;
  word-break: break-all;
}

.url-value {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #ffffff;
  padding: 8px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  color: #409eff;
  font-weight: 500;
  font-size: 11px;
}

.status-code {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-weight: 700;
  font-size: 11px;
  color: white;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}

.status-code.success {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.status-code.redirect {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.status-code.client-error {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.status-code.server-error {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.status-code.unknown {
  background: linear-gradient(135deg, #909399, #a6a9ad);
}

.headers-list,
.cookies-list {
  background: #ffffff;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
  max-height: 200px;
  overflow-y: auto;
}

.header-item,
.cookie-item {
  display: flex;
  padding: 6px 8px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s ease;
}

.header-item:last-child,
.cookie-item:last-child {
  border-bottom: none;
}

.header-item:hover,
.cookie-item:hover {
  background: #f8f9fa;
}

.header-key,
.cookie-key {
  font-weight: 600;
  color: #606266;
  min-width: 100px;
  font-size: 11px;
}

.header-value,
.cookie-value {
  color: #303133;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  word-break: break-all;
}

.json-content {
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 10px;
  line-height: 1.4;
  padding: 12px;
  border-radius: 4px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
  position: relative;
}

.body-content .json-content {
  max-height: 350px;
  background: #f8f9fa;
  color: #303133;
  border: 1px solid #e4e7ed;
}

.empty-value {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #909399;
  font-style: italic;
  padding: 8px;
  background: #f8f9fa;
  border-radius: 4px;
  border: 1px dashed #e4e7ed;
  font-size: 11px;
}

.info-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-grid .info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.info-grid .info-item:last-child {
  border-bottom: none;
}

.info-grid .info-item label {
  font-weight: 500;
  color: #606266;
  min-width: 100px;
}

.url-text {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  color: #409eff;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time-text {
  color: #67c23a;
  font-weight: 600;
}

.body-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-weight: 600;
  color: #606266;
  font-size: 13px;
}

.code-textarea {
  font-family: 'Courier New', monospace;
  font-size: 12px;
}

.code-textarea :deep(.el-textarea__inner) {
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
}

/* 验证器表格 */
.validators {
  margin-top: 16px;
}

.validator-table {
  border-radius: 6px;
  overflow: hidden;
}

.validator-table :deep(.el-table__header) {
  background: #f5f7fa;
}

.log-text {
  color: #d4d4d4;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.no-steps {
  text-align: center;
  padding: 40px 0;
}

/* 响应式设计 */
@media (max-width: 1200px) {
  .header-main {
    flex-direction: column;
    gap: 16px;
    align-items: stretch;
  }

  .stats-overview {
    min-width: auto;
    order: 2;
  }

  .header-left {
    order: 1;
  }

  .header-right {
    order: 3;
    justify-content: center;
  }

  .allure-content {
    flex-direction: column;
  }

  .allure-sidebar {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid #e1e5e9;
  }

  .nav-list {
    display: flex;
    overflow-x: auto;
    gap: 8px;
  }

  .nav-list li {
    white-space: nowrap;
    margin-bottom: 0;
  }

  .overview-grid,
  .summary-charts,
  .timeline-charts {
    grid-template-columns: 1fr;
  }

  .req-resp-content {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .header-top {
    flex-direction: column;
    gap: 16px;
    align-items: flex-start;
  }

  .status-overview {
    flex-direction: column;
    gap: 16px;
    align-items: flex-start;
  }

  .stats-grid {
    margin-left: 0;
    flex-wrap: wrap;
  }

  .allure-main {
    padding: 20px;
  }

  .step-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }

  .step-duration {
    align-self: flex-end;
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

@keyframes slideInLeft {
  from {
    opacity: 0;
    transform: translateX(-30px);
  }

  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes slideInRight {
  from {
    opacity: 0;
    transform: translateX(30px);
  }

  to {
    opacity: 1;
    transform: translateX(0);
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

.overview-card {
  animation: fadeInUp 0.6s ease-out;
}

.steps-card {
  animation: slideInLeft 0.8s ease-out;
}

.log-card {
  animation: slideInRight 0.8s ease-out;
}

.chart-card {
  animation: scaleIn 0.6s ease-out;
}

/* 悬停效果增强 */
.stat-card:hover .stat-icon {
  transform: scale(1.1) rotate(5deg);
}

.info-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
}

/* 加载状态动画 */
.loading-shimmer {
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% {
    background-position: -200% 0;
  }

  100% {
    background-position: 200% 0;
  }
}

/* 滚动条样式 */
.log-content::-webkit-scrollbar,
.json-content::-webkit-scrollbar,
.headers-list::-webkit-scrollbar,
.cookies-list::-webkit-scrollbar,
.body-content::-webkit-scrollbar,
.key-value-list::-webkit-scrollbar,
.code-block::-webkit-scrollbar,
.validator-list::-webkit-scrollbar {
  width: 6px;
}

.log-content::-webkit-scrollbar-track,
.json-content::-webkit-scrollbar-track,
.headers-list::-webkit-scrollbar-track,
.cookies-list::-webkit-scrollbar-track,
.body-content::-webkit-scrollbar-track,
.key-value-list::-webkit-scrollbar-track,
.code-block::-webkit-scrollbar-track,
.validator-list::-webkit-scrollbar-track {
  background: #2d2d2d;
  border-radius: 3px;
}

.log-content::-webkit-scrollbar-thumb,
.json-content::-webkit-scrollbar-thumb,
.headers-list::-webkit-scrollbar-thumb,
.cookies-list::-webkit-scrollbar-thumb,
.body-content::-webkit-scrollbar-thumb,
.key-value-list::-webkit-scrollbar-thumb,
.code-block::-webkit-scrollbar-thumb,
.validator-list::-webkit-scrollbar-thumb {
  background: #555;
  border-radius: 3px;
}

.log-content::-webkit-scrollbar-thumb:hover,
.json-content::-webkit-scrollbar-thumb:hover,
.headers-list::-webkit-scrollbar-thumb:hover,
.cookies-list::-webkit-scrollbar-thumb:hover,
.body-content::-webkit-scrollbar-thumb:hover,
.key-value-list::-webkit-scrollbar-thumb:hover,
.code-block::-webkit-scrollbar-thumb:hover,
.validator-list::-webkit-scrollbar-thumb:hover {
  background: #777;
}

/* Body内容的滚动条样式 */
.body-content .json-content::-webkit-scrollbar-track {
  background: #f0f0f0;
}

.body-content .json-content::-webkit-scrollbar-thumb {
  background: #c0c4cc;
}

.body-content .json-content::-webkit-scrollbar-thumb:hover {
  background: #a8abb2;
}

/* Console 滚动条样式 */
.key-value-list::-webkit-scrollbar-track,
.validator-list::-webkit-scrollbar-track {
  background: #f0f0f0;
}

.key-value-list::-webkit-scrollbar-thumb,
.validator-list::-webkit-scrollbar-thumb {
  background: #c0c4cc;
}

.key-value-list::-webkit-scrollbar-thumb:hover,
.validator-list::-webkit-scrollbar-thumb:hover {
  background: #a8abb2;
}

/* 验证器样式 */
.validators-container {
  background: #ffffff;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  border: 1px solid #e8eaed;
}

.validators-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid #f0f0f0;
}

.validators-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 6px;
}


.validators-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.validator-type-group {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 10px;
  border-left: 3px solid #409eff;
}

.validator-type-title {
  margin: 0 0 10px 0;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 6px;
}

.validator-type-title::before {
  content: '';
  width: 3px;
  height: 12px;
  background: #409eff;
  border-radius: 2px;
}

.validator-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.step-validators {
  background: #ffffff;
  border: 1px solid #e1e5e9;
  border-radius: 8px;
  overflow: hidden;
}

.step-validator-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: #f8f9fa;
  border-bottom: 1px solid #e1e5e9;
}

.step-validator-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.step-validator-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.step-validator-status.passed {
  background: #d4edda;
  color: #155724;
  border: 1px solid #c3e6cb;
}

.step-validator-status.failed {
  background: #f8d7da;
  color: #721c24;
  border: 1px solid #f5c6cb;
}

.validators-list {
  padding: 20px;
}

.validator-type-group {
  margin-bottom: 20px;
}

.validator-type-group:last-child {
  margin-bottom: 0;
}

.validator-type-group h4 {
  margin: 0 0 12px 0;
  font-size: 14px;
  font-weight: 600;
  color: #7f8c8d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.validator-item {
  background: #f8f9fa;
  border: 1px solid #e1e5e9;
  border-radius: 6px;
  margin-bottom: 12px;
  overflow: hidden;
}

.validator-item:last-child {
  margin-bottom: 0;
}

.validator-item.pass {
  border-left: 4px solid #28a745;
}

.validator-item.fail {
  border-left: 4px solid #dc3545;
}

.validator-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #ffffff;
  border-bottom: 1px solid #e1e5e9;
}

.validator-comparator {
  display: flex;
  align-items: center;
  gap: 8px;
}

.comparator-label {
  font-size: 12px;
  font-weight: 600;
  color: #7f8c8d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.comparator-value {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

.validator-result {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.validator-result.pass {
  background: #d4edda;
  color: #155724;
}

.validator-result.fail {
  background: #f8d7da;
  color: #721c24;
}

.validator-details {
  padding: 16px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.validator-check,
.validator-expectation,
.validator-actual,
.validator-message {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.validator-message {
  grid-column: 1 / -1;
}

.check-label,
.expect-label,
.actual-label,
.message-label {
  font-size: 11px;
  font-weight: 600;
  color: #7f8c8d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.check-value,
.expect-value,
.actual-value,
.message-value {
  font-size: 13px;
  color: #2c3e50;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #ffffff;
  padding: 6px 8px;
  border-radius: 4px;
  border: 1px solid #e1e5e9;
  word-break: break-all;
}

.actual-value {
  color: #dc3545;
  font-weight: 600;
}

.expect-value {
  color: #28a745;
  font-weight: 600;
}

.no-validators {
  padding: 40px 20px;
  text-align: center;
  color: #7f8c8d;
}

/* 导出变量样式 */
.export-vars-container {
  background: #ffffff;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  border: 1px solid #e8eaed;
}

.export-vars-header {
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 2px solid #f0f0f0;
}

.export-vars-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 6px;
}

.export-vars-content {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 10px;
}

.vars-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.var-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  background: #ffffff;
  border-radius: 4px;
  border-left: 3px solid #67c23a;
  transition: all 0.2s ease;
}

.var-item:hover {
  background: #f0f9ff;
  border-left-color: #409eff;
  transform: translateX(2px);
}

.var-key {
  font-weight: 600;
  color: #606266;
  font-size: 11px;
  min-width: 80px;
}

.var-value {
  color: #303133;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 11px;
  background: #f8f9fa;
  padding: 3px 6px;
  border-radius: 3px;
  border: 1px solid #e8eaed;
  word-break: break-all;
}

/* 响应式设计 */
@media (max-width: 1200px) {
  .req-resp-content {
    grid-template-columns: 1fr;
  }

  .request-section {
    border-right: none;
    border-bottom: 1px solid #e4e7ed;
  }

  .response-section {
    border-right: none;
  }

}

@media (max-width: 768px) {

  /* Console 响应式设计 */

  .console-entry {
    margin-bottom: 6px;
  }

  .console-header {
    padding: 8px 12px;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .console-header-left {
    width: 100%;
    gap: 8px;
  }


  /* 合并头部的响应式样式 */
  .unified-console-header {
    padding: 8px 12px;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .unified-console-header .console-header-left {
    width: 100%;
    gap: 8px;
  }

  .unified-console-header .step-number {
    width: 20px;
    height: 20px;
    font-size: 10px;
  }


  .unified-console-header .console-title {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .unified-console-header .console-title .step-name {
    margin-right: 0;
    margin-bottom: 4px;
  }

  .unified-console-header .console-meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .unified-console-header .console-request-info {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .unified-console-header .url-text {
    max-width: 100%;
    font-size: 11px;
  }

  .unified-console-header .console-header-right {
    width: 100%;
    flex-direction: row;
    justify-content: space-between;
    align-items: center;
    margin-top: 8px;
  }

  .console-title {
    flex-direction: column;
    gap: 4px;
    align-items: flex-start;
  }

  .method-badge {
    font-size: 9px;
    padding: 1px 6px;
    min-width: 35px;
  }

  .url-text {
    font-size: 11px;
    word-break: break-all;
    white-space: normal;
  }

  .console-meta {
    flex-direction: column;
    gap: 4px;
    font-size: 10px;
  }

  .console-header-right {
    width: 100%;
    justify-content: flex-end;
  }


  .console-section-header {
    padding: 8px 12px;
    font-size: 12px;
  }

  .console-item-header {
    padding: 6px 12px;
    font-size: 11px;
  }

  .console-item-content {
    padding: 0 12px 8px 12px;
  }

  .key-value-list {
    max-height: 150px;
  }

  .key-value-item {
    padding: 6px 8px;
    font-size: 10px;
  }

  .key-value-item .key {
    min-width: 80px;
    font-size: 9px;
  }

  .code-block {
    max-height: 200px;
  }

  .code-content {
    font-size: 10px;
    padding: 8px;
  }

  .status-badge {
    font-size: 10px;
    padding: 2px 6px;
  }

  /* Request/Response Tabs 移动端样式 */
  .req-resp-tabs-container :deep(.el-tabs__nav-wrap) {
    padding: 0 8px;
  }

  .req-resp-tabs-container :deep(.el-tabs__item) {
    font-size: 12px;
    padding: 8px 12px;
    margin-right: 2px;
  }

  .req-resp-tabs-container :deep(.el-tab-pane) {
    padding: 12px;
  }

  .tab-label {
    font-size: 12px;
  }

  .tab-label i {
    font-size: 12px;
  }

  .tab-content {
    gap: 12px;
  }

  /* Count Badge 移动端样式 */
  .count-badge {
    min-width: 16px;
    height: 16px;
    padding: 1px 4px;
    font-size: 9px;
    margin-left: 6px;
  }

  .validators-count {
    min-width: 14px;
    height: 14px;
    padding: 1px 3px;
    font-size: 8px;
    margin-left: 4px;
  }

  .value-text {
    font-size: 11px;
    padding: 3px 6px;
  }

  /* Validators 移动端样式 */

  .validator-details {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .validator-item {
    padding: 8px;
  }

  /* 断言部分移动端样式 */
  .validator-assertion {
    flex-direction: column;
    gap: 8px;
    align-items: stretch;
  }

  .assertion-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    min-width: auto;
  }

  .assertion-label {
    font-size: 10px;
    min-width: auto;
  }

  .assertion-value {
    font-size: 10px;
    padding: 3px 6px;
    width: 100%;
  }

  .validator-header {
    flex-direction: column;
    gap: 6px;
    align-items: flex-start;
  }

  .validator-comparator {
    font-size: 10px;
  }

  .comparator-label {
    font-size: 9px;
  }

  .comparator-value {
    font-size: 10px;
    padding: 1px 4px;
  }

  .validator-result {
    font-size: 9px;
    padding: 2px 6px;
  }

  .check-label,
  .expect-label,
  .actual-label,
  .message-label {
    font-size: 9px;
  }

  .check-value,
  .expect-value,
  .actual-value,
  .message-value {
    font-size: 10px;
    padding: 3px 4px;
  }

  .req-resp-header {
    flex-direction: column;
    gap: 16px;
    align-items: flex-start;
  }

  .req-resp-tags {
    flex-wrap: wrap;
    gap: 8px;
  }

  .method-tag,
  .status-tag {
    font-size: 12px;
    padding: 6px 12px;
  }

  .req-resp-content {
    min-height: auto;
  }

  .request-section,
  .response-section {
    padding: 16px;
  }

  .detail-group {
    padding: 12px;
  }

  .json-content {
    font-size: 11px;
    padding: 12px;
    max-height: 200px;
  }

  .headers-list,
  .cookies-list {
    font-size: 12px;
  }

  .header-key,
  .cookie-key {
    min-width: 80px;
    font-size: 12px;
  }

  .validator-details {
    grid-template-columns: 1fr;
  }

  .validator-header {
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .step-validator-header {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }

  .validators-header {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }


  .export-vars-container,
  .validators-container {
    padding: 10px;
  }
}
</style>
