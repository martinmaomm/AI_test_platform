<template>
  <div class="test-run-detail">
    <el-card class="run-card">
      <template #header>
        <div class="card-header">
          <span class="title">测试运行详情</span>
          <el-tag :type="getStatusType(run.status)" size="large">
            {{ getStatusText(run.status) }}
          </el-tag>
        </div>
      </template>

      <!-- 基本信息 -->
      <div class="run-section">
        <h4>基本信息</h4>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="运行名称">
            {{ run.name }}
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ run.description || '无描述' }}
          </el-descriptions-item>
          <el-descriptions-item label="执行环境">
            {{ run.environment_name || '未指定' }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ formatTime(run.started_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="结束时间">
            {{ formatTime(run.started_at) }} (基于开始时间)
          </el-descriptions-item>
          <el-descriptions-item label="执行时长">
            {{ run.duration || 'N/A' }}
          </el-descriptions-item>
          <el-descriptions-item label="触发者">
            {{ run.triggered_by_username || 'N/A' }}
          </el-descriptions-item>
          <el-descriptions-item label="重试次数">
            {{ run.retry_count || 0 }}
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 统计信息 -->
      <div class="run-section">
        <h4>执行统计</h4>
        <el-row :gutter="20">
          <el-col :span="6">
            <el-statistic title="总步骤数" :value="run.total_steps || 0" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="成功步骤" :value="run.success_steps || 0" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="失败步骤" :value="run.failure_steps || 0" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="错误步骤" :value="run.error_steps || 0" />
          </el-col>
        </el-row>
      </div>

      <!-- 测试结果列表 -->
      <div class="run-section" v-if="httprunnerResults && httprunnerResults.length > 0">
        <h4>测试结果</h4>
        <el-table :data="httprunnerResults" style="width: 100%">
          <el-table-column prop="name" label="测试用例" width="200" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.success ? 'success' : 'failed')" size="small">
                {{ getStatusText(row.success ? 'success' : 'failed') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="执行时长" width="120">
            <template #default="{ row }">
              {{ formatDuration(row.time?.duration) }}
            </template>
          </el-table-column>
          <el-table-column label="步骤数" width="100">
            <template #default="{ row }">
              {{ row.step_datas ? row.step_datas.length : 0 }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button
                type="primary"
                size="small"
                @click="viewTestResult(row)"
              >
                查看详情
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 执行摘要 -->
      <div class="run-section" v-if="httprunnerSummary">
        <h4>执行摘要</h4>
        <el-input
          v-model="summaryContent"
          type="textarea"
          :rows="6"
          readonly
          placeholder="无执行摘要"
        />
      </div>

      <!-- 错误信息 -->
      <div class="run-section" v-if="run.error_message">
        <h4>错误信息</h4>
        <el-alert
          :title="run.error_message"
          type="error"
          :closable="false"
          show-icon
        />
      </div>

      <!-- 执行日志 -->
      <div class="run-section" v-if="run.execution_log">
        <h4>执行日志</h4>
        <el-input
          v-model="run.execution_log"
          type="textarea"
          :rows="8"
          readonly
          placeholder="无执行日志"
        />
      </div>
    </el-card>

    <!-- 测试结果详情对话框 -->
    <el-dialog
      v-model="showResultDialog"
      title="测试结果详情"
      width="80%"
      :close-on-click-modal="false"
    >
      <APITestCaseExecutionDetail v-if="selectedResult" :result="selectedResult" />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import dayjs from 'dayjs'
import APITestCaseExecutionDetail from './APITestCaseExecutionDetail.vue'

const props = defineProps({
  run: {
    type: Object,
    required: true
  }
})

const showResultDialog = ref(false)
const selectedResult = ref(null)

// 计算成功率
const successRate = computed(() => {
  if (!props.run.total_steps || props.run.total_steps === 0) {
    return 0
  }
  return Math.round((props.run.success_steps / props.run.total_steps) * 100)
})

// 获取HttpRunner结果数据
const httprunnerResults = computed(() => {
  if (props.run.httprunner_result && props.run.httprunner_result.results) {
    return props.run.httprunner_result.results
  }
  return []
})

// 获取HttpRunner摘要数据
const httprunnerSummary = computed(() => {
  if (props.run.httprunner_result && props.run.httprunner_result.summary) {
    return props.run.httprunner_result.summary
  }
  return null
})

// 格式化时间
const formatTime = (timeStr) => {
  if (!timeStr) return 'N/A'
  return dayjs(timeStr).format('YYYY-MM-DD HH:mm:ss')
}

// 格式化时长
const formatDuration = (duration) => {
  if (!duration) return 'N/A'
  
  // 如果已经是格式化的时间字符串 (HH:MM:SS.mmm)，直接返回
  if (typeof duration === 'string' && duration.includes(':')) {
    return duration
  }
  
  // 如果是数字（秒数），进行格式化
  if (typeof duration === 'number') {
    return `${duration.toFixed(3)}s`
  }
  
  return duration
}

// 获取状态类型
const getStatusType = (status) => {
  const statusMap = {
    'completed': 'success',
    'failed': 'danger',
    'running': 'warning',
    'pending': 'info',
    'cancelled': 'info',
    'success': 'success',
    'failure': 'danger',
    'error': 'danger'
  }
  return statusMap[status] || 'info'
}

// 获取状态文本
const getStatusText = (status) => {
  const statusMap = {
    'completed': '已完成',
    'failed': '失败',
    'running': '执行中',
    'pending': '等待中',
    'cancelled': '已取消',
    'success': '成功',
    'failure': '失败',
    'error': '错误'
  }
  return statusMap[status] || '未知'
}

// 处理摘要内容
const summaryContent = computed(() => {
  if (!httprunnerSummary.value) {
    return ''
  }
  return JSON.stringify(httprunnerSummary.value, null, 2)
})

// 查看测试结果详情
const viewTestResult = (result) => {
  selectedResult.value = result
  showResultDialog.value = true
}
</script>

<style scoped>
.test-run-detail {
  max-width: 1200px;
  margin: 0 auto;
}

.run-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title {
  font-size: 18px;
  font-weight: bold;
}

.run-section {
  margin-bottom: 24px;
}

.run-section h4 {
  margin-bottom: 16px;
  color: #303133;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 8px;
}
</style>
