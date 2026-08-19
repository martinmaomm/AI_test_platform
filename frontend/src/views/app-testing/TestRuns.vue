<template>
  <div class="test-runs-container">
    <div class="page-header">
      <h1 class="page-title">测试执行记录</h1>
      <p class="page-description">查看和管理App自动化测试的执行记录和结果</p>
    </div>

    <!-- 统计概览 -->
    <div class="stats-overview">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon total">
            <el-icon><Document /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-number">{{ stats.totalRuns }}</div>
            <div class="stat-label">总执行次数</div>
          </div>
        </div>
      </el-card>
      
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon success">
            <el-icon><Check /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-number">{{ stats.passedRuns }}</div>
            <div class="stat-label">成功次数</div>
          </div>
        </div>
      </el-card>
      
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon failed">
            <el-icon><Close /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-number">{{ stats.failedRuns }}</div>
            <div class="stat-label">失败次数</div>
          </div>
        </div>
      </el-card>
      
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon rate">
            <el-icon><DataBoard /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-number">{{ stats.successRate }}%</div>
            <div class="stat-label">成功率</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 筛选工具栏 -->
    <div class="filter-toolbar">
      <div class="filter-left">
        <el-select v-model="filters.status" placeholder="执行状态" clearable style="width: 120px">
          <el-option label="全部" value="" />
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failed" />
          <el-option label="执行中" value="running" />
        </el-select>
        
        <el-select v-model="filters.platform" placeholder="平台" clearable style="width: 120px">
          <el-option label="全部" value="" />
          <el-option label="Android" value="android" />
          <el-option label="iOS" value="ios" />
        </el-select>
        
        <el-select v-model="filters.device" placeholder="设备类型" clearable style="width: 120px">
          <el-option label="全部" value="" />
          <el-option label="手机" value="phone" />
          <el-option label="平板" value="tablet" />
        </el-select>
        
        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 240px"
        />
      </div>
      
      <div class="filter-right">
        <el-button @click="refreshData">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button @click="exportReport">
          <el-icon><Download /></el-icon>
          导出报告
        </el-button>
      </div>
    </div>

    <!-- 执行记录表格 -->
    <el-card class="table-card" shadow="hover">
      <el-table
        :data="filteredTestRuns"
        v-loading="loading"
        @row-click="viewDetails"
        style="cursor: pointer"
      >
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="执行名称" min-width="200" />
        <el-table-column prop="platform" label="平台" width="100">
          <template #default="{ row }">
            <el-tag :type="getPlatformType(row.platform)">
              {{ getPlatformText(row.platform) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="device" label="设备" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="totalCases" label="用例总数" width="100" />
        <el-table-column prop="passedCases" label="通过" width="80" />
        <el-table-column prop="failedCases" label="失败" width="80" />
        <el-table-column prop="duration" label="执行时长" width="120" />
        <el-table-column prop="startTime" label="开始时间" width="180" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click.stop="viewDetails(row)">查看详情</el-button>
            <el-button size="small" type="danger" @click.stop="deleteRun(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 分页 -->
    <div class="pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="totalRuns"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>

    <!-- 详情对话框 -->
    <el-dialog
      v-model="detailDialogVisible"
      title="执行详情"
      width="90%"
      :close-on-click-modal="false"
    >
      <div v-if="selectedRun" class="run-details">
        <div class="detail-header">
          <h3>{{ selectedRun.name }}</h3>
          <div class="detail-meta">
            <el-tag :type="getStatusType(selectedRun.status)">
              {{ getStatusText(selectedRun.status) }}
            </el-tag>
            <span>平台: {{ getPlatformText(selectedRun.platform) }}</span>
            <span>设备: {{ selectedRun.device }}</span>
            <span>执行时长: {{ selectedRun.duration }}</span>
          </div>
        </div>
        
        <div class="detail-content">
          <el-tabs v-model="activeTab">
            <el-tab-pane label="执行概览" name="overview">
              <div class="overview-stats">
                <div class="stat-item">
                  <span class="label">用例总数:</span>
                  <span class="value">{{ selectedRun.totalCases }}</span>
                </div>
                <div class="stat-item">
                  <span class="label">通过数量:</span>
                  <span class="value success">{{ selectedRun.passedCases }}</span>
                </div>
                <div class="stat-item">
                  <span class="label">失败数量:</span>
                  <span class="value failed">{{ selectedRun.failedCases }}</span>
                </div>
                <div class="stat-item">
                  <span class="label">成功率:</span>
                  <span class="value">{{ ((selectedRun.passedCases / selectedRun.totalCases) * 100).toFixed(1) }}%</span>
                </div>
              </div>
            </el-tab-pane>
            
            <el-tab-pane label="用例详情" name="cases">
              <el-table :data="selectedRun.cases" stripe>
                <el-table-column prop="name" label="用例名称" />
                <el-table-column prop="platform" label="平台" width="100" />
                <el-table-column prop="device" label="设备" width="120" />
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'passed' ? 'success' : 'danger'">
                      {{ row.status === 'passed' ? '通过' : '失败' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="duration" label="执行时长" width="120" />
                <el-table-column prop="error" label="错误信息" show-overflow-tooltip />
              </el-table>
            </el-tab-pane>
            
            <el-tab-pane label="执行日志" name="logs">
              <div class="log-content">
                <pre>{{ selectedRun.logs }}</pre>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Check, Close, DataBoard, Refresh, Download } from '@element-plus/icons-vue'

const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const totalRuns = ref(0)
const detailDialogVisible = ref(false)
const selectedRun = ref(null)
const activeTab = ref('overview')

// 统计数据
const stats = reactive({
  totalRuns: 0,
  passedRuns: 0,
  failedRuns: 0,
  successRate: 0
})

// 筛选条件
const filters = reactive({
  status: '',
  platform: '',
  device: '',
  dateRange: null
})

// 测试执行记录数据
const testRuns = ref([
  {
    id: 1,
    name: 'App登录功能回归测试',
    platform: 'android',
    device: 'Pixel 6',
    status: 'success',
    totalCases: 6,
    passedCases: 6,
    failedCases: 0,
    duration: '3m 45s',
    startTime: '2024-01-15 10:30:00',
    cases: [
      { name: '正常登录测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '2.1s', error: '' },
      { name: '错误密码测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '1.8s', error: '' },
      { name: '空用户名测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '1.5s', error: '' },
      { name: '记住密码测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '2.3s', error: '' },
      { name: '自动登录测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '2.0s', error: '' },
      { name: '生物识别登录测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '3.2s', error: '' }
    ],
    logs: '开始执行App登录功能回归测试...\n[10:30:01] 启动Android模拟器\n[10:30:02] 安装测试App\n[10:30:03] 执行正常登录测试 - 通过\n[10:30:04] 执行错误密码测试 - 通过\n[10:30:05] 执行空用户名测试 - 通过\n[10:30:06] 执行记住密码测试 - 通过\n[10:30:07] 执行自动登录测试 - 通过\n[10:30:08] 执行生物识别登录测试 - 通过\n[10:30:09] 测试执行完成，所有用例通过'
  },
  {
    id: 2,
    name: 'iOS商品搜索功能测试',
    platform: 'ios',
    device: 'iPhone 14',
    status: 'failed',
    totalCases: 4,
    passedCases: 3,
    failedCases: 1,
    duration: '2m 20s',
    startTime: '2024-01-15 09:15:00',
    cases: [
      { name: '关键词搜索测试', platform: 'iOS', device: 'iPhone 14', status: 'passed', duration: '2.1s', error: '' },
      { name: '分类搜索测试', platform: 'iOS', device: 'iPhone 14', status: 'passed', duration: '1.9s', error: '' },
      { name: '语音搜索测试', platform: 'iOS', device: 'iPhone 14', status: 'passed', duration: '3.2s', error: '' },
      { name: '高级搜索测试', platform: 'iOS', device: 'iPhone 14', status: 'failed', duration: '2.8s', error: '语音识别超时' }
    ],
    logs: '开始执行iOS商品搜索功能测试...\n[09:15:01] 启动iOS模拟器\n[09:15:02] 安装测试App\n[09:15:03] 执行关键词搜索测试 - 通过\n[09:15:04] 执行分类搜索测试 - 通过\n[09:15:05] 执行语音搜索测试 - 通过\n[09:15:06] 执行高级搜索测试 - 失败: 语音识别超时\n[09:15:07] 测试执行完成，3个用例通过，1个用例失败'
  },
  {
    id: 3,
    name: '跨平台支付功能测试',
    platform: 'both',
    device: 'Universal',
    status: 'running',
    totalCases: 8,
    passedCases: 4,
    failedCases: 0,
    duration: '4m 10s',
    startTime: '2024-01-15 11:00:00',
    cases: [
      { name: 'Android支付宝测试', platform: 'Android', device: 'Samsung S23', status: 'passed', duration: '3.2s', error: '' },
      { name: 'iOS支付宝测试', platform: 'iOS', device: 'iPhone 13', status: 'passed', duration: '2.8s', error: '' },
      { name: 'Android微信支付测试', platform: 'Android', device: 'Pixel 7', status: 'passed', duration: '4.1s', error: '' },
      { name: 'iOS微信支付测试', platform: 'iOS', device: 'iPhone 14', status: 'passed', duration: '3.5s', error: '' },
      { name: 'Android银行卡测试', platform: 'Android', device: 'OnePlus 11', status: 'running', duration: '0s', error: '' },
      { name: 'iOS银行卡测试', platform: 'iOS', device: 'iPhone 12', status: 'pending', duration: '0s', error: '' },
      { name: 'Android Apple Pay测试', platform: 'Android', device: 'Samsung S22', status: 'pending', duration: '0s', error: '' },
      { name: 'iOS Apple Pay测试', platform: 'iOS', device: 'iPhone 15', status: 'pending', duration: '0s', error: '' }
    ],
    logs: '开始执行跨平台支付功能测试...\n[11:00:01] 启动多设备测试环境\n[11:00:02] 安装测试App到各设备\n[11:00:03] 执行Android支付宝测试 - 通过\n[11:00:04] 执行iOS支付宝测试 - 通过\n[11:00:05] 执行Android微信支付测试 - 通过\n[11:00:06] 执行iOS微信支付测试 - 通过\n[11:00:07] 执行Android银行卡测试 - 执行中...'
  }
])

// 计算属性
const filteredTestRuns = computed(() => {
  let filtered = testRuns.value

  if (filters.status) {
    filtered = filtered.filter(run => run.status === filters.status)
  }

  if (filters.platform) {
    filtered = filtered.filter(run => run.platform === filters.platform)
  }

  if (filters.device) {
    filtered = filtered.filter(run => run.device.toLowerCase().includes(filters.device.toLowerCase()))
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

// 方法
const getPlatformType = (platform) => {
  const types = { android: 'success', ios: 'warning', both: 'info' }
  return types[platform] || 'info'
}

const getPlatformText = (platform) => {
  const texts = { android: 'Android', ios: 'iOS', both: 'Android + iOS' }
  return texts[platform] || platform
}

const getStatusType = (status) => {
  const types = { success: 'success', failed: 'danger', running: 'warning' }
  return types[status] || 'info'
}

const getStatusText = (status) => {
  const texts = { success: '成功', failed: '失败', running: '执行中' }
  return texts[status] || status
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
}

const handleCurrentChange = (val) => {
  currentPage.value = val
}

const viewDetails = (row) => {
  selectedRun.value = row
  activeTab.value = 'overview'
  detailDialogVisible.value = true
}

const deleteRun = async (row) => {
  try {
    await ElMessageBox.confirm('确定要删除这个执行记录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    const index = testRuns.value.findIndex(r => r.id === row.id)
    if (index !== -1) {
      testRuns.value.splice(index, 1)
      updateStats()
      ElMessage.success('执行记录删除成功')
    }
  } catch (error) {
    // 用户取消删除
  }
}

const refreshData = () => {
  loading.value = true
  setTimeout(() => {
    loading.value = false
    ElMessage.success('数据刷新成功')
  }, 1000)
}

const exportReport = () => {
  ElMessage.info('导出报告功能开发中...')
}

const updateStats = () => {
  stats.totalRuns = testRuns.value.length
  stats.passedRuns = testRuns.value.filter(r => r.status === 'success').length
  stats.failedRuns = testRuns.value.filter(r => r.status === 'failed').length
  stats.successRate = stats.totalRuns > 0 ? Math.round((stats.passedRuns / stats.totalRuns) * 100) : 0
}

onMounted(() => {
  totalRuns.value = testRuns.value.length
  updateStats()
})
</script>

<style scoped>
.test-runs-container {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 30px;
}

.page-title {
  font-size: 28px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 10px 0;
}

.page-description {
  font-size: 16px;
  color: #666;
  margin: 0;
}

.stats-overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  margin-bottom: 30px;
}

.stat-card {
  border-radius: 12px;
  transition: transform 0.2s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 15px;
}

.stat-icon {
  width: 50px;
  height: 50px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: white;
}

.stat-icon.total {
  background: linear-gradient(135deg, #409eff, #66b1ff);
}

.stat-icon.success {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

.stat-icon.failed {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}

.stat-icon.rate {
  background: linear-gradient(135deg, #e6a23c, #ebb563);
}

.stat-info {
  flex: 1;
}

.stat-number {
  font-size: 24px;
  font-weight: 700;
  color: #1a1a1a;
  line-height: 1;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 14px;
  color: #666;
  font-weight: 500;
}

.filter-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding: 15px;
  background: #f8f9fa;
  border-radius: 8px;
}

.filter-left {
  display: flex;
  gap: 12px;
  align-items: center;
}

.table-card {
  margin-bottom: 20px;
}

.pagination {
  display: flex;
  justify-content: center;
  margin-top: 20px;
}

.run-details {
  padding: 20px;
}

.detail-header {
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #e8eaed;
}

.detail-header h3 {
  margin: 0 0 10px 0;
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
}

.detail-meta {
  display: flex;
  gap: 20px;
  align-items: center;
  font-size: 14px;
  color: #666;
}

.overview-stats {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
  padding: 20px;
  background: #f8f9fa;
  border-radius: 8px;
}

.stat-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid #e8eaed;
}

.stat-item:last-child {
  border-bottom: none;
}

.stat-item .label {
  font-weight: 500;
  color: #666;
}

.stat-item .value {
  font-weight: 600;
  color: #1a1a1a;
}

.stat-item .value.success {
  color: #67c23a;
}

.stat-item .value.failed {
  color: #f56c6c;
}

.log-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 20px;
  border-radius: 8px;
  max-height: 400px;
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

@media (max-width: 768px) {
  .stats-overview {
    grid-template-columns: repeat(2, 1fr);
  }
  
  .filter-toolbar {
    flex-direction: column;
    gap: 15px;
    align-items: stretch;
  }
  
  .filter-left {
    flex-wrap: wrap;
  }
  
  .overview-stats {
    grid-template-columns: 1fr;
  }
  
  .test-runs-container {
    padding: 15px;
  }
}
</style>
