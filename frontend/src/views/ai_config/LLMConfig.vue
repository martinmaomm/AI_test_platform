<template>
  <div class="llm-config-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <BackButton to="/ai-config" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Connection />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>LLM模型配置</h2>
            <p>管理LLM大模型和视觉模型配置</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="addModelConfiguration" class="add-config-btn">
            添加AI模型配置
          </el-button>
        </div>
      </div>
    </div>

    <!-- LLM配置列表 -->
    <el-card class="config-list-card">
      <div class="card-header">
        <div class="card-header-left">
          <div class="card-title">
            配置列表
          </div>
        </div>
        <div class="card-header-right">
          <el-input v-model="searchQuery" placeholder="搜索模型配置..." prefix-icon="Search"
            style="width: 300px; margin-right: 10px;" clearable />
          <el-select v-model="modelTypeFilter" placeholder="模型类型" clearable style="width: 120px; margin-right: 10px;">
            <el-option label="LLM" value="llm" />
            <el-option label="视觉模型" value="vision" />
          </el-select>
        </div>
      </div>

      <el-table :data="filteredConfigurations" v-loading="loading" style="width: 100%">
        <el-table-column prop="model_name" label="配置信息" min-width="200">
          <template #default="scope">
            <div class="config-name">
              <div class="config-icon-wrapper model-icon-wrapper" :style="{ backgroundColor: getModelIconColor(scope.row.model_name) }">
                <span class="model-icon-text">{{ getModelIconText(scope.row.model_name) }}</span>
              </div>
              <div class="config-info">
                <div class="config-title">
                  {{ scope.row.model_name }}
                </div>
                <div class="config-model">提供商：{{ modelProviderLabel(scope.row) }} · 接口：{{ scope.row.provider || '—' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="model_type_display" label="模型类型" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.model_type === 'llm' ? 'primary' : 'success'" size="small">
              {{ scope.row.model_type_display }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100" align="center">
          <template #default="scope">
            <el-switch
              :model-value="scope.row.is_active"
              @update:model-value="toggleModelStatus(scope.row)"
              :loading="scope.row.statusLoading"
            />
          </template>
        </el-table-column>
        
        <el-table-column prop="base_url" label="API地址" min-width="200" show-overflow-tooltip />
        
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>
        
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="scope">
            <el-button type="primary" size="small" @click="testConnectionHandler(scope.row)"
              :loading="testingConnection === scope.row.id" :disabled="!scope.row.is_active"
              :title="scope.row.is_active ? '测试连接' : '请先启用配置'">
              测试连接
            </el-button>
            <el-button type="warning" size="small" @click="editConfiguration(scope.row)">
              编辑
            </el-button>
            <el-button type="danger" size="small" @click="handleDeleteModelConfiguration(scope.row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>


    <!-- 创建/编辑AI模型配置对话框 -->
    <el-dialog v-model="showCreateModelDialog" :title="editingModelConfig ? '编辑AI模型配置' : '添加AI模型配置'" width="600px"
      :close-on-click-modal="false">
      <el-form ref="modelConfigFormRef" :model="modelConfigForm" :rules="modelConfigRules" label-width="120px">
        <el-form-item label="模型类型" prop="model_type">
          <el-select v-model="modelConfigForm.model_type" placeholder="选择模型类型" @change="onModelTypeChange"
            style="width: 100%">
            <el-option label="LLM大模型" value="llm" />
            <el-option label="视觉模型" value="vision" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="模型接口类型" prop="provider">
          <el-select v-model="modelConfigForm.provider" placeholder="选择模型接口类型" style="width: 100%">
            <el-option label="OpenAI" value="openai" />
            <el-option label="DeepSeek" value="deepseek" />
            <el-option label="Ollama" value="ollama" />
            <el-option label="通义千问" value="qwen" />
            <el-option label="文心一言" value="ernie" />
            <el-option label="智谱AI" value="zhipu" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>

        <el-form-item label="模型提供商" prop="provider_name">
          <el-input v-model="modelConfigForm.provider_name" maxlength="100" show-word-limit placeholder="可选；留空时使用模型接口类型" />
        </el-form-item>
        
         <el-form-item label="API密钥" prop="api_key">
           <el-input 
             v-model="modelConfigForm.api_key" 
             type="password" 
             placeholder="请输入API密钥" 
             show-password 
             :disabled="modelConfigForm.provider === 'ollama'"
           />
           <div class="form-tip" v-if="modelConfigForm.provider === 'ollama'">
             <el-text type="info" size="small">ℹ️ Ollama本地部署，无需API密钥</el-text>
           </div>
         </el-form-item>
        
        <el-form-item label="API地址" prop="base_url">
          <el-input v-model="modelConfigForm.base_url" placeholder="请输入API地址" />
        </el-form-item>
        
        <el-form-item label="模型名称" prop="model_name">
          <el-input v-model="modelConfigForm.model_name" placeholder="请输入模型名称" />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="cancelModelEdit">取消</el-button>
          <el-button type="primary" @click="saveModelConfiguration" :loading="saving">
            保存
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, h } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { Connection, Search, Plus } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import dayjs from 'dayjs'
import { modelProviderLabel } from '@/composables/webUIScriptGenerationPresentation'
import {
  getLLMConfigurations,
  createLLMConfiguration,
  updateLLMConfiguration,
  deleteLLMConfiguration,
  testLLMConnection,
  toggleLLMConfigurationActive,
} from '@/api/aiConfig'

// 响应式数据
const loading = ref(false)
const saving = ref(false)
const testingConnection = ref(null)
const showCreateModelDialog = ref(false)
const editingModelConfig = ref(null)
const searchQuery = ref('')
const modelTypeFilter = ref('')

// 数据
const configurations = ref([])

// 模型表单数据
const modelConfigForm = reactive({
  model_type: 'llm',
  provider: '',
  provider_name: '',
  api_key: '',
  base_url: '',
  model_name: '',
  is_active: true
})

// 模型表单验证规则
const modelConfigRules = {
  model_type: [
    { required: true, message: '请选择模型类型', trigger: 'change' }
  ],
  provider: [
    { required: true, message: '请选择模型接口类型', trigger: 'change' }
  ],
  api_key: [
    { 
      validator: (rule, value, callback) => {
        // 编辑模式下，如果API密钥为空，允许通过（不修改现有密钥）
        if (editingModelConfig.value && !value) {
          callback()
          return
        }
        
        // Ollama提供商不需要API密钥
        const provider = modelConfigForm.provider
        if (provider === 'ollama') {
          callback()
          return
        }
        
        // 其他提供商需要API密钥
        if (provider && ['openai', 'deepseek'].includes(provider)) {
          if (!value || value.trim() === '') {
            callback(new Error(`${provider === 'openai' ? 'OpenAI' : 'DeepSeek'}需要提供API密钥`))
            return
          }
        }
        
        callback()
      },
      trigger: 'blur'
    }
  ],
  base_url: [
    { required: true, message: '请输入API地址', trigger: 'blur' },
    { type: 'url', message: '请输入有效的URL地址', trigger: 'blur' }
  ],
  model_name: [
    { required: true, message: '请输入模型名称', trigger: 'blur' }
  ]
}

// 计算属性
const filteredConfigurations = computed(() => {
  let result = configurations.value

  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(config => [config.model_name, config.provider_name, config.provider]
      .some(value => String(value || '').toLowerCase().includes(query)))
  }

  if (modelTypeFilter.value) {
    result = result.filter(config => config.model_type === modelTypeFilter.value)
  }

  return result
})

// 表单引用
const modelConfigFormRef = ref()

// 方法
const loadConfigurations = async () => {
  try {
    loading.value = true
    const response = await getLLMConfigurations()
    
    if (response?.data) {
      if (Array.isArray(response.data)) {
        configurations.value = response.data
      } else if (response.data.items && Array.isArray(response.data.items)) {
        configurations.value = response.data.items
      } else if (response.data.success && response.data.data) {
        configurations.value = Array.isArray(response.data.data) ? response.data.data : []
      } else {
        configurations.value = []
      }
    } else {
      configurations.value = []
    }
  } catch (error) {
    console.error('加载模型配置失败:', error)
    ElMessage.error('加载模型配置失败')
  } finally {
    loading.value = false
  }
}


const onModelTypeChange = () => {
  modelConfigForm.provider = ''
  modelConfigForm.provider_name = ''
  modelConfigForm.api_key = ''
  modelConfigForm.model_name = ''
  modelConfigForm.base_url = ''
}

// 通用表单重置函数
const resetForm = (formRef, defaultValues) => {
  Object.assign(formRef, defaultValues)
}

// 模型配置相关
const addModelConfiguration = () => {
  resetForm(modelConfigForm, {
    model_type: 'llm',
    provider: '',
    provider_name: '',
    api_key: '',
    base_url: '',
    model_name: '',
    is_active: true
  })
  editingModelConfig.value = null
  showCreateModelDialog.value = true
}

const editConfiguration = (config) => {
  editingModelConfig.value = config
  resetForm(modelConfigForm, {
    model_type: config.model_type,
    provider: config.provider || '',
    provider_name: config.provider_name || '',
    api_key: config.api_key || '',
    base_url: config.base_url,
    model_name: config.model_name,
    is_active: config.is_active
  })
  showCreateModelDialog.value = true
}

const saveModelConfiguration = async () => {
  try {
    await modelConfigFormRef.value.validate()
    saving.value = true

    const data = { ...modelConfigForm }
    
    if (editingModelConfig.value && !data.api_key) {
      delete data.api_key
    }

    if (editingModelConfig.value) {
      await updateLLMConfiguration(editingModelConfig.value.id, data)
      ElMessage.success('模型配置更新成功')
    } else {
      await createLLMConfiguration(data)
      ElMessage.success('模型配置创建成功')
    }

    showCreateModelDialog.value = false
    await loadConfigurations()
  } catch (error) {
    console.error('保存模型配置失败:', error)
    ElMessage.error('保存模型配置失败')
  } finally {
    saving.value = false
  }
}

const cancelModelEdit = () => {
  showCreateModelDialog.value = false
  editingModelConfig.value = null
  resetForm(modelConfigForm, {
    model_type: 'llm',
    provider: '',
    provider_name: '',
    api_key: '',
    base_url: '',
    model_name: '',
    is_active: true
  })
  modelConfigFormRef?.value?.resetFields()
}

const testConnectionHandler = async (config) => {
  try {
    testingConnection.value = config.id
    
    const response = await testLLMConnection({ config_id: config.id })
    
    console.log('模型连接测试API响应:', response)

    let data = null
    if (response && response.data) {
      if (response.data.success && response.data.data) {
        data = response.data.data
      } else {
        data = response.data
      }
    }

    if (data && data.success) {
      ElNotification({
        title: '连接测试成功',
        message: h('div', [
          h('div', { style: 'margin-bottom: 8px;' }, `${config.model_name} 连接测试成功！`),
          h('div', { style: 'font-size: 12px; color: #67c23a;' }, `⏱️ 响应时间: ${data.response_time}s`),
          data.response_content && h('div', { 
            style: 'font-size: 12px; color: #909399; border-top: 1px solid #ebeef5; padding-top: 8px;' 
          }, `📝 响应内容: ${data.response_content}`)
        ]),
        type: 'success',
        duration: 5000,
        showClose: true
      })
    } else {
      const errorMsg = (data && data.message) || response.error || '连接测试失败'
      ElMessage.error(`连接测试失败: ${errorMsg}`)
    }
  } catch (error) {
    console.error('连接测试失败:', error)
    
    if (error.response?.data) {
      const errorData = error.response.data
      const errorTitle = getErrorTitle(errorData.error_type)
      
      ElNotification({
        title: errorTitle,
        message: h('div', [
          h('div', { style: 'margin-bottom: 8px;' }, errorData.error_detail),
          errorData.suggestion && h('div', { 
            style: 'font-size: 12px; color: #909399; border-top: 1px solid #ebeef5; padding-top: 8px;' 
          }, `💡 建议: ${errorData.suggestion}`)
        ]),
        type: 'error',
        duration: 8000,
        showClose: true
      })
    } else {
      ElMessage.error('网络错误，请检查网络连接')
    }
  } finally {
    testingConnection.value = null
  }
}

// 切换模型状态
const toggleModelStatus = async (config) => {
  try {
    config.statusLoading = true
    const originalStatus = config.is_active
    const newStatus = !originalStatus
    
    await toggleLLMConfigurationActive(config.id)
    config.is_active = newStatus
    
    ElMessage.success(newStatus ? '模型配置已启用' : '模型配置已禁用')
  } catch (error) {
    console.error('切换模型状态失败:', error)
    ElMessage.error(`切换模型状态失败: ${error.response?.data?.message || error.message}`)
    config.is_active = !config.is_active
  } finally {
    config.statusLoading = false
  }
}

// 删除模型配置
const handleDeleteModelConfiguration = async (config) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除配置 "${config.model_name}" 吗？`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteLLMConfiguration(config.id)
    ElMessage.success('配置删除成功')
    await loadConfigurations()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除配置失败:', error)
      ElMessage.error('删除配置失败')
    }
  }
}

// 工具方法
const getErrorTitle = (errorType) => {
  const titles = {
    'VALIDATION_ERROR': '参数验证错误',
    'NOT_FOUND_ERROR': '配置不存在',
    'CONFIG_ERROR': '配置状态错误',
    'AUTH_ERROR': '认证失败',
    'CONNECTION_ERROR': '连接失败',
    'TIMEOUT_ERROR': '请求超时',
    'GENERAL_ERROR': '连接测试失败'
  }
  return titles[errorType] || '连接测试失败'
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm:ss')
}

// 通用图标工具函数
const getIconText = (name, defaultChar = 'A') => {
  if (!name) return defaultChar
  return name.charAt(0).toUpperCase()
}

const getIconColor = (name, defaultColor = '#409EFF') => {
  if (!name) return defaultColor
  
  const firstChar = name.charAt(0).toLowerCase()
  const colors = {
    'a': '#FF6B6B', 'b': '#4ECDC4', 'c': '#45B7D1', 'd': '#96CEB4', 'e': '#FFEAA7',
    'f': '#DDA0DD', 'g': '#98D8C8', 'h': '#F7DC6F', 'i': '#BB8FCE', 'j': '#85C1E9',
    'k': '#F8C471', 'l': '#82E0AA', 'm': '#F1948A', 'n': '#85C1E9', 'o': '#F7DC6F',
    'p': '#D7BDE2', 'q': '#A9DFBF', 'r': '#F9E79F', 's': '#AED6F1', 't': '#A3E4D7',
    'u': '#D5DBDB', 'v': '#FADBD8', 'w': '#D1F2EB', 'x': '#E8DAEF', 'y': '#FCF3CF',
    'z': '#D6EAF8'
  }
  
  return colors[firstChar] || defaultColor
}

const getModelIconText = (name) => getIconText(name, 'A')
const getModelIconColor = (name) => getIconColor(name, '#409EFF')

// 初始化
onMounted(() => {
  loadConfigurations()
})
</script>

<style scoped>
.llm-config-page {
  margin: 0 auto;
}

/* 页面头部样式 */
.page-header {
  margin-bottom: 20px;
}

.page-header :deep(.back-btn) {
  margin-bottom: 12px;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  position: relative;
  z-index: 1;
  overflow: hidden;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
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

.config-list-card {
  margin-bottom: 20px;
  border-radius: 0 0 16px 16px;
  border-top: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  gap: 20px;
}

.card-header-left {
  display: flex;
  align-items: center;
  flex-grow: 1;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.card-icon {
  font-size: 18px;
  color: #409eff;
}

.card-header-right {
  display: flex;
  gap: 10px;
  align-items: center;
}

/* 配置名称列样式 */
.config-name {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.config-icon-wrapper {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.config-icon-wrapper:hover {
  transform: scale(1.05);
}

/* AI模型图标样式 */
.model-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  min-width: 40px;
  height: 40px;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.model-icon-wrapper:hover {
  transform: scale(1.1);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.model-icon-text {
  color: white;
  font-weight: bold;
  font-size: 16px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
  user-select: none;
}

.config-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-title {
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
  display: flex;
  align-items: center;
}

.config-model {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 添加配置按钮样式 */
.add-config-btn {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  backdrop-filter: blur(10px);
  font-weight: 500;
  padding: 12px 24px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.add-config-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}


.dialog-footer {
  text-align: right;
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

/* 表单提示样式 */
.form-tip {
  margin-top: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.form-tip .el-text {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 信息类型提示样式 */
.form-tip .el-text[type="info"] {
  background-color: #f0f9ff;
  border: 1px solid #b3d8ff;
  color: #409eff;
}


/* 响应式设计 */
@media (max-width: 768px) {
  .llm-config-page {
    padding: 10px;
  }

  .page-header {
    padding: 20px;
    margin-bottom: 16px;
  }

  .header-content {
    flex-direction: column;
    gap: 20px;
    text-align: center;
  }

  .header-left h2 {
    font-size: 20px;
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
  }

  .config-name {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .config-icon-wrapper {
    width: 32px;
    height: 32px;
  }
}
</style>
