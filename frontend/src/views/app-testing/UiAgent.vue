<template>
  <div class="ui-agent-container">
    <div class="page-header">
      <h1 class="page-title">UI智能体</h1>
      <p class="page-description">基于AI的App UI自动化测试智能体，自动生成移动端测试脚本和用例</p>
    </div>

    <div class="content-grid">
      <!-- 智能体配置 -->
      <el-card class="config-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Setting /></el-icon>
            <span>智能体配置</span>
          </div>
        </template>
        
        <el-form :model="agentConfig" label-width="120px">
          <el-form-item label="测试类型">
            <el-select v-model="agentConfig.testType" placeholder="选择测试类型">
              <el-option label="功能测试" value="functional" />
              <el-option label="回归测试" value="regression" />
              <el-option label="冒烟测试" value="smoke" />
              <el-option label="兼容性测试" value="compatibility" />
            </el-select>
          </el-form-item>
          
          <el-form-item label="平台">
            <el-checkbox-group v-model="agentConfig.platforms">
              <el-checkbox label="android">Android</el-checkbox>
              <el-checkbox label="ios">iOS</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          
          <el-form-item label="设备类型">
            <el-checkbox-group v-model="agentConfig.devices">
              <el-checkbox label="phone">手机</el-checkbox>
              <el-checkbox label="tablet">平板</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          
          <el-form-item label="测试环境">
            <el-radio-group v-model="agentConfig.environment">
              <el-radio label="dev">开发环境</el-radio>
              <el-radio label="test">测试环境</el-radio>
              <el-radio label="staging">预发布环境</el-radio>
            </el-radio-group>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 智能体状态 -->
      <el-card class="status-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Monitor /></el-icon>
            <span>智能体状态</span>
          </div>
        </template>
        
        <div class="status-content">
          <div class="status-item">
            <span class="status-label">运行状态：</span>
            <el-tag :type="agentStatus.running ? 'success' : 'info'">
              {{ agentStatus.running ? '运行中' : '已停止' }}
            </el-tag>
          </div>
          
          <div class="status-item">
            <span class="status-label">生成用例：</span>
            <span class="status-value">{{ agentStatus.generatedCases }} 个</span>
          </div>
          
          <div class="status-item">
            <span class="status-label">执行成功率：</span>
            <span class="status-value">{{ agentStatus.successRate }}%</span>
          </div>
          
          <div class="status-item">
            <span class="status-label">支持平台：</span>
            <span class="status-value">{{ agentStatus.platforms.join(', ') }}</span>
          </div>
          
          <div class="status-item">
            <span class="status-label">最后更新：</span>
            <span class="status-value">{{ agentStatus.lastUpdate }}</span>
          </div>
        </div>
      </el-card>

      <!-- 操作面板 -->
      <el-card class="action-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Star /></el-icon>
            <span>智能操作</span>
          </div>
        </template>
        
        <div class="action-buttons">
          <el-button type="primary" @click="startAgent" :loading="agentStatus.running">
            <el-icon><Right /></el-icon>
            启动智能体
          </el-button>
          
          <el-button @click="generateTestCases" :disabled="!agentStatus.running">
            <el-icon><Plus /></el-icon>
            生成测试用例
          </el-button>
          
          <el-button @click="runTests" :disabled="!agentStatus.running">
            <el-icon><Right /></el-icon>
            执行测试
          </el-button>
          
          <el-button @click="deviceManagement" :disabled="!agentStatus.running">
            <el-icon><Link /></el-icon>
            设备管理
          </el-button>
          
          <el-button type="danger" @click="stopAgent" :disabled="!agentStatus.running">
            <el-icon><Back /></el-icon>
            停止智能体
          </el-button>
        </div>
      </el-card>

      <!-- 测试结果 -->
      <el-card class="result-card" shadow="hover" v-if="testResults.length > 0">
        <template #header>
          <div class="card-header">
            <el-icon><Document /></el-icon>
            <span>测试结果</span>
          </div>
        </template>
        
        <el-table :data="testResults" stripe>
          <el-table-column prop="name" label="测试用例" />
          <el-table-column prop="platform" label="平台" />
          <el-table-column prop="device" label="设备" />
          <el-table-column prop="status" label="状态">
            <template #default="{ row }">
              <el-tag :type="row.status === 'passed' ? 'success' : 'danger'">
                {{ row.status === 'passed' ? '通过' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration" label="耗时" />
          <el-table-column label="操作">
            <template #default="{ row }">
              <el-button size="small" @click="viewDetails(row)">查看详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting, Monitor, Star, Right, Back, Plus, Document, Link } from '@element-plus/icons-vue'

const agentConfig = reactive({
  testType: 'functional',
  platforms: ['android'],
  devices: ['phone'],
  environment: 'test'
})

const agentStatus = reactive({
  running: false,
  generatedCases: 0,
  successRate: 0,
  platforms: ['Android'],
  lastUpdate: '从未运行'
})

const testResults = ref([])

const startAgent = async () => {
  agentStatus.running = true
  ElMessage.success('智能体已启动')
  
  // 模拟智能体运行
  setTimeout(() => {
    agentStatus.generatedCases = 8
    agentStatus.successRate = 87
    agentStatus.platforms = agentConfig.platforms.map(p => p.charAt(0).toUpperCase() + p.slice(1))
    agentStatus.lastUpdate = new Date().toLocaleString()
  }, 1000)
}

const stopAgent = () => {
  agentStatus.running = false
  ElMessage.info('智能体已停止')
}

const generateTestCases = async () => {
  ElMessage.info('正在生成测试用例...')
  // 模拟生成过程
  setTimeout(() => {
    agentStatus.generatedCases += 4
    ElMessage.success('测试用例生成完成')
  }, 2000)
}

const runTests = async () => {
  ElMessage.info('正在执行测试...')
  // 模拟测试执行
  setTimeout(() => {
    testResults.value = [
      { name: '登录功能测试', platform: 'Android', device: 'Pixel 6', status: 'passed', duration: '3.2s' },
      { name: '搜索功能测试', platform: 'iOS', device: 'iPhone 14', status: 'passed', duration: '2.8s' },
      { name: '支付功能测试', platform: 'Android', device: 'Samsung S23', status: 'failed', duration: '4.1s' },
      { name: '推送通知测试', platform: 'iOS', device: 'iPhone 13', status: 'passed', duration: '2.5s' }
    ]
    ElMessage.success('测试执行完成')
  }, 3000)
}

const deviceManagement = () => {
  ElMessage.info('设备管理功能开发中...')
}

const viewDetails = (row) => {
  ElMessage.info(`查看测试用例详情: ${row.name}`)
}
</script>

<style scoped>
.ui-agent-container {
  padding: 20px;
  max-width: 1200px;
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

.content-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

.config-card,
.status-card,
.action-card,
.result-card {
  min-height: 300px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #1a1a1a;
}

.status-content {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.status-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.status-item:last-child {
  border-bottom: none;
}

.status-label {
  font-weight: 500;
  color: #666;
}

.status-value {
  font-weight: 600;
  color: #1a1a1a;
}

.action-buttons {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

@media (max-width: 768px) {
  .content-grid {
    grid-template-columns: 1fr;
  }
  
  .ui-agent-container {
    padding: 15px;
  }
  
  .action-buttons {
    flex-direction: column;
  }
}
</style>
