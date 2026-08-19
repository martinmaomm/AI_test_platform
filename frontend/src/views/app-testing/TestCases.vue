<template>
  <div class="test-cases-container">
    <div class="page-header">
      <h1 class="page-title">测试用例管理</h1>
      <p class="page-description">管理App自动化测试用例，支持创建、编辑、执行和批量操作</p>
    </div>

    <!-- 批量操作栏 - 覆盖显示在工具栏上方 -->
    <div v-if="selectedCases.length > 0" class="batch-actions-overlay">
      <div class="batch-info">
        <span>已选择 {{ selectedCases.length }} 个测试用例</span>
      </div>
      <div class="batch-buttons">
        <el-button @click="batchExecute" type="primary">
          <el-icon><Right /></el-icon>
          批量执行
        </el-button>
        <el-button @click="exportCases" type="success">
          <el-icon><Download /></el-icon>
          导出用例
        </el-button>
        <el-button @click="batchDelete" type="danger">
          <el-icon><Delete /></el-icon>
          批量删除
        </el-button>
        <el-button @click="clearSelection">
          <el-icon><Close /></el-icon>
          取消选择
        </el-button>
      </div>
    </div>

    <!-- 原始工具栏 - 当没有选中项时显示 -->
    <div v-else class="toolbar">
      <div class="toolbar-left">
        <el-button type="primary" @click="createTestCase">
          <el-icon><Plus /></el-icon>
          新建用例
        </el-button>
        <el-button @click="batchExecute" :disabled="selectedCases.length === 0">
          <el-icon><Right /></el-icon>
          批量执行
        </el-button>
        <el-button @click="importCases">
          <el-icon><Upload /></el-icon>
          导入用例
        </el-button>
        <el-button @click="exportCases" :disabled="selectedCases.length === 0">
          <el-icon><Download /></el-icon>
          导出用例
        </el-button>
      </div>
      
      <div class="toolbar-right">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索测试用例..."
          style="width: 300px"
          @input="handleSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>
    </div>

    <!-- 测试用例表格 -->
    <el-card class="table-card" shadow="hover">
      <el-table
        :data="filteredTestCases"
        @selection-change="handleSelectionChange"
        stripe
        v-loading="loading"
      >
        <el-table-column type="selection" width="55" />
        <el-table-column prop="name" label="用例名称" min-width="200" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column prop="platform" label="平台" width="100">
          <template #default="{ row }">
            <el-tag :type="getPlatformType(row.platform)">
              {{ getPlatformText(row.platform) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="device" label="设备" width="120" />
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <el-tag :type="getPriorityType(row.priority)">
              {{ getPriorityText(row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lastRun" label="最后执行" width="150">
          <template #default="{ row }">
            {{ row.lastRun || '从未执行' }}
          </template>
        </el-table-column>
        <el-table-column prop="duration" label="执行时长" width="100">
          <template #default="{ row }">
            {{ row.duration || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="editTestCase(row)">编辑</el-button>
            <el-button size="small" type="primary" @click="runTestCase(row)">执行</el-button>
            <el-button size="small" type="danger" @click="deleteTestCase(row)">删除</el-button>
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
        :total="totalCases"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>

    <!-- 新建/编辑用例对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑测试用例' : '新建测试用例'"
      width="800px"
      :close-on-click-modal="false"
    >
      <el-form :model="testCaseForm" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="用例名称" prop="name">
          <el-input v-model="testCaseForm.name" placeholder="请输入用例名称" />
        </el-form-item>
        
        <el-form-item label="所属模块" prop="module">
          <el-select v-model="testCaseForm.module" placeholder="选择模块">
            <el-option label="登录模块" value="login" />
            <el-option label="用户管理" value="user" />
            <el-option label="商品管理" value="product" />
            <el-option label="订单管理" value="order" />
            <el-option label="支付模块" value="payment" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="测试平台" prop="platform">
          <el-radio-group v-model="testCaseForm.platform">
            <el-radio label="android">Android</el-radio>
            <el-radio label="ios">iOS</el-radio>
            <el-radio label="both">Android + iOS</el-radio>
          </el-radio-group>
        </el-form-item>
        
        <el-form-item label="设备类型" prop="device">
          <el-select v-model="testCaseForm.device" placeholder="选择设备">
            <el-option label="手机" value="phone" />
            <el-option label="平板" value="tablet" />
            <el-option label="通用" value="universal" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="优先级" prop="priority">
          <el-radio-group v-model="testCaseForm.priority">
            <el-radio label="high">高</el-radio>
            <el-radio label="medium">中</el-radio>
            <el-radio label="low">低</el-radio>
          </el-radio-group>
        </el-form-item>
        
        <el-form-item label="测试步骤" prop="steps">
          <el-input
            v-model="testCaseForm.steps"
            type="textarea"
            :rows="6"
            placeholder="请输入测试步骤"
          />
        </el-form-item>
        
        <el-form-item label="预期结果" prop="expected">
          <el-input
            v-model="testCaseForm.expected"
            type="textarea"
            :rows="3"
            placeholder="请输入预期结果"
          />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveTestCase">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Right, Upload, Download, Search, Delete, Close } from '@element-plus/icons-vue'

const loading = ref(false)
const searchKeyword = ref('')
const selectedCases = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const totalCases = ref(0)
const dialogVisible = ref(false)
const isEdit = ref(false)
const formRef = ref()

// 测试用例数据
const testCases = ref([
  {
    id: 1,
    name: 'App登录功能测试',
    module: 'login',
    platform: 'android',
    device: 'phone',
    priority: 'high',
    status: 'active',
    lastRun: '2024-01-15 10:30',
    duration: '3.2s',
    steps: '1. 打开App\n2. 输入用户名和密码\n3. 点击登录按钮\n4. 验证登录状态',
    expected: '登录成功，跳转到主页面'
  },
  {
    id: 2,
    name: '商品搜索功能测试',
    module: 'product',
    platform: 'ios',
    device: 'phone',
    priority: 'medium',
    status: 'active',
    lastRun: '2024-01-15 09:15',
    duration: '2.8s',
    steps: '1. 进入商品页面\n2. 输入搜索关键词\n3. 点击搜索按钮\n4. 查看搜索结果',
    expected: '显示相关商品列表'
  },
  {
    id: 3,
    name: '购物车添加商品测试',
    module: 'order',
    platform: 'both',
    device: 'universal',
    priority: 'high',
    status: 'inactive',
    lastRun: '2024-01-14 16:20',
    duration: '4.1s',
    steps: '1. 选择商品\n2. 点击加入购物车\n3. 查看购物车\n4. 验证商品数量',
    expected: '商品成功添加到购物车'
  },
  {
    id: 4,
    name: '支付功能测试',
    module: 'payment',
    platform: 'android',
    device: 'phone',
    priority: 'high',
    status: 'active',
    lastRun: '2024-01-15 11:45',
    duration: '5.2s',
    steps: '1. 选择支付方式\n2. 输入支付信息\n3. 确认支付\n4. 验证支付结果',
    expected: '支付成功，订单状态更新'
  }
])

// 表单数据
const testCaseForm = reactive({
  name: '',
  module: '',
  platform: 'android',
  device: 'phone',
  priority: 'medium',
  steps: '',
  expected: ''
})

// 表单验证规则
const formRules = {
  name: [{ required: true, message: '请输入用例名称', trigger: 'blur' }],
  module: [{ required: true, message: '请选择模块', trigger: 'change' }],
  platform: [{ required: true, message: '请选择测试平台', trigger: 'change' }],
  device: [{ required: true, message: '请选择设备类型', trigger: 'change' }],
  priority: [{ required: true, message: '请选择优先级', trigger: 'change' }],
  steps: [{ required: true, message: '请输入测试步骤', trigger: 'blur' }],
  expected: [{ required: true, message: '请输入预期结果', trigger: 'blur' }]
}

// 计算属性
const filteredTestCases = computed(() => {
  if (!searchKeyword.value) {
    return testCases.value
  }
  return testCases.value.filter(case_ => 
    case_.name.toLowerCase().includes(searchKeyword.value.toLowerCase()) ||
    case_.module.toLowerCase().includes(searchKeyword.value.toLowerCase()) ||
    case_.platform.toLowerCase().includes(searchKeyword.value.toLowerCase())
  )
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

const getPriorityType = (priority) => {
  const types = { high: 'danger', medium: 'warning', low: 'info' }
  return types[priority] || 'info'
}

const getPriorityText = (priority) => {
  const texts = { high: '高', medium: '中', low: '低' }
  return texts[priority] || '未知'
}

const getStatusType = (status) => {
  const types = { active: 'success', inactive: 'info' }
  return types[status] || 'info'
}

const getStatusText = (status) => {
  const texts = { active: '启用', inactive: '禁用' }
  return texts[status] || '未知'
}

const handleSelectionChange = (selection) => {
  selectedCases.value = selection
}

const handleSearch = () => {
  // 搜索逻辑已在计算属性中处理
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
}

const handleCurrentChange = (val) => {
  currentPage.value = val
}

const createTestCase = () => {
  isEdit.value = false
  resetForm()
  dialogVisible.value = true
}

const editTestCase = (row) => {
  isEdit.value = true
  Object.assign(testCaseForm, row)
  dialogVisible.value = true
}

const saveTestCase = async () => {
  if (!formRef.value) return
  
  try {
    await formRef.value.validate()
    
    if (isEdit.value) {
      // 编辑用例
      const index = testCases.value.findIndex(c => c.id === testCaseForm.id)
      if (index !== -1) {
        testCases.value[index] = { ...testCaseForm }
      }
      ElMessage.success('测试用例更新成功')
    } else {
      // 新建用例
      const newCase = {
        ...testCaseForm,
        id: Date.now(),
        status: 'active',
        lastRun: '从未执行',
        duration: '-'
      }
      testCases.value.unshift(newCase)
      ElMessage.success('测试用例创建成功')
    }
    
    dialogVisible.value = false
    resetForm()
  } catch (error) {
    console.error('表单验证失败:', error)
  }
}

const resetForm = () => {
  Object.assign(testCaseForm, {
    name: '',
    module: '',
    platform: 'android',
    device: 'phone',
    priority: 'medium',
    steps: '',
    expected: ''
  })
}

const runTestCase = async (row) => {
  ElMessage.info(`正在执行测试用例: ${row.name}`)
  // 模拟执行过程
  setTimeout(() => {
    // 找到对应的测试用例并更新
    const index = testCases.value.findIndex(tc => tc.id === row.id)
    if (index !== -1) {
      testCases.value[index].lastRun = new Date().toLocaleString()
      testCases.value[index].duration = (Math.random() * 6 + 2).toFixed(1) + 's'
    }
    ElMessage.success('测试用例执行完成')
  }, 2000)
}

const batchExecute = async () => {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请选择要执行的测试用例')
    return
  }
  
  ElMessage.info(`正在批量执行 ${selectedCases.value.length} 个测试用例`)
  // 模拟批量执行
  setTimeout(() => {
    selectedCases.value.forEach(selectedCase => {
      const index = testCases.value.findIndex(tc => tc.id === selectedCase.id)
      if (index !== -1) {
        testCases.value[index].lastRun = new Date().toLocaleString()
        testCases.value[index].duration = (Math.random() * 6 + 2).toFixed(1) + 's'
      }
    })
    ElMessage.success('批量执行完成')
  }, 3000)
}

const deleteTestCase = async (row) => {
  try {
    await ElMessageBox.confirm('确定要删除这个测试用例吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    const index = testCases.value.findIndex(c => c.id === row.id)
    if (index !== -1) {
      testCases.value.splice(index, 1)
      ElMessage.success('测试用例删除成功')
    }
  } catch (error) {
    // 用户取消删除
  }
}

const importCases = () => {
  ElMessage.info('导入功能开发中...')
}

const exportCases = () => {
  ElMessage.info('导出功能开发中...')
}

const batchDelete = async () => {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请选择要删除的测试用例')
    return
  }
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedCases.value.length} 个测试用例吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // 批量删除
    selectedCases.value.forEach(selectedCase => {
      const index = testCases.value.findIndex(c => c.id === selectedCase.id)
      if (index !== -1) {
        testCases.value.splice(index, 1)
      }
    })
    
    ElMessage.success(`成功删除 ${selectedCases.value.length} 个测试用例`)
    clearSelection()
  } catch (error) {
    // 用户取消删除
  }
}

const clearSelection = () => {
  selectedCases.value = []
}

onMounted(() => {
  totalCases.value = testCases.value.length
})
</script>

<style scoped>
.test-cases-container {
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

/* 批量操作栏覆盖样式 */
.batch-actions-overlay {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding: 15px;
  background-color: #f0f9ff;
  border: 1px solid #b3d8ff;
  border-radius: 8px;
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

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding: 15px;
  background: #f8f9fa;
  border-radius: 8px;
}

.toolbar-left {
  display: flex;
  gap: 12px;
}

.table-card {
  margin-bottom: 20px;
}

.pagination {
  display: flex;
  justify-content: center;
  margin-top: 20px;
}

@media (max-width: 768px) {
  .batch-actions-overlay {
    flex-direction: column;
    gap: 15px;
    text-align: center;
  }

  .batch-buttons {
    justify-content: center;
    flex-wrap: wrap;
  }

  .toolbar {
    flex-direction: column;
    gap: 15px;
    align-items: stretch;
  }
  
  .toolbar-left {
    flex-wrap: wrap;
  }
  
  .test-cases-container {
    padding: 15px;
  }
}
</style>
