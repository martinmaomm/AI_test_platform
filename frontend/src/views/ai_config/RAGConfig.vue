<template>
  <div class="rag-config-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <BackButton to="/ai-config" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <DataAnalysis />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>RAG向量数据库配置</h2>
            <p>管理RAG向量数据库配置，支持Chroma和Milvus等向量数据库</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button 
            v-if="configurations.length === 0"
            type="success" 
            icon="Plus" 
            @click="addRAGConfiguration" 
            class="add-config-btn">
            创建RAG配置
          </el-button>
        </div>
      </div>
    </div>

    <!-- RAG配置卡片 -->
    <el-card class="config-card" v-loading="loading" v-if="currentConfig">
      <div class="config-card-header">
        <div class="config-header-left">
          <div class="config-icon-wrapper" :style="{ backgroundColor: getRAGIconColor(currentConfig.name) }">
            <span class="rag-icon-text">{{ getRAGIconText(currentConfig.name) }}</span>
          </div>
          <div class="config-header-info">
            <h3 class="config-name-title">{{ currentConfig.name }}</h3>
            <div class="config-meta">
              <el-tag :type="currentConfig.is_active ? 'success' : 'info'" size="small" class="status-tag">
                {{ currentConfig.is_active ? '已启用' : '已禁用' }}
              </el-tag>
              <span class="config-time">创建于 {{ formatDate(currentConfig.created_at) }}</span>
            </div>
          </div>
        </div>
        <div class="config-header-actions">
          <el-button 
            type="primary" 
            @click="testRAGConnectionHandler(currentConfig)"
            :loading="testingConnection === currentConfig.id" 
            :disabled="!currentConfig.is_active"
            :title="currentConfig.is_active ? '测试连接' : '请先启用配置'">
            测试连接
          </el-button>
          <el-button type="warning" @click="editRAGConfiguration(currentConfig)">
            编辑配置
          </el-button>
          <el-dropdown @command="handleRAGAction" trigger="click">
            <el-button>
              更多<el-icon class="el-icon--right"><arrow-down /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item :command="{ action: 'toggleActive', data: currentConfig }">
                  {{ currentConfig.is_active ? '禁用' : '启用' }}
                </el-dropdown-item>
                <el-dropdown-item :command="{ action: 'delete', data: currentConfig }" divided>
                  删除配置
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <div class="config-content">
        <div class="config-section">
          <h4 class="section-title">配置详情</h4>
          <div class="config-item">
            <span class="config-label">向量数据库类型</span>
            <el-tag :type="getRAGDBTagType(currentConfig.vector_db_type)" class="config-value-tag">
              {{ currentConfig.vector_db_type_display }}
            </el-tag>
          </div>
          <div class="config-item">
            <span class="config-label">嵌入模型</span>
            <span class="config-value">{{ currentConfig.embedding_model }}</span>
          </div>
          <div class="config-item">
            <span class="config-label">文本分块大小</span>
            <span class="config-value">{{ currentConfig.chunk_size }}</span>
          </div>
          <div class="config-item">
            <span class="config-label">分块重叠大小</span>
            <span class="config-value">{{ currentConfig.chunk_overlap }}</span>
          </div>
          <template v-if="currentConfig.vector_db_type === 'milvus'">
            <div class="config-item">
              <span class="config-label">主机地址</span>
              <span class="config-value">{{ currentConfig.milvus_host }}</span>
            </div>
            <div class="config-item">
              <span class="config-label">端口</span>
              <span class="config-value">{{ currentConfig.milvus_port }}</span>
            </div>
            <div class="config-item">
              <span class="config-label">集合名称</span>
              <span class="config-value">{{ currentConfig.milvus_collection_name }}</span>
            </div>
            <div class="config-item">
              <span class="config-label">向量维度</span>
              <span class="config-value">{{ currentConfig.milvus_dim }}</span>
            </div>
          </template>
        </div>
      </div>
    </el-card>

    <!-- 空状态 -->
    <el-card class="empty-config-card" v-else-if="!loading">
      <el-empty description="暂无RAG配置">
        <el-button type="success" icon="Plus" @click="addRAGConfiguration">
          创建RAG配置
        </el-button>
      </el-empty>
    </el-card>

    <!-- 创建/编辑RAG配置对话框 -->
    <el-dialog v-model="showCreateRAGDialog" :title="editingRAGConfig ? '编辑RAG配置' : '添加RAG配置'" width="700px"
      :close-on-click-modal="false">
      <el-form ref="ragConfigFormRef" :model="ragConfigForm" :rules="ragConfigRules" label-width="140px">
        <el-form-item label="配置名称" prop="name">
          <el-input v-model="ragConfigForm.name" placeholder="请输入配置名称" />
        </el-form-item>
        
        <el-form-item label="向量数据库类型" prop="vector_db_type">
          <el-select v-model="ragConfigForm.vector_db_type" placeholder="选择向量数据库类型" @change="onRAGDBTypeChange"
            style="width: 100%">
            <el-option label="Chroma (调试模式)" value="chroma" />
            <el-option label="Milvus (生产模式)" value="milvus" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="嵌入模型" prop="embedding_model">
          <el-select v-model="ragConfigForm.embedding_model" placeholder="选择嵌入模型" 
            style="width: 100%">
            <el-option label="BAAI/bge-large-zh-v1.5 (推荐，1024维)" value="BAAI/bge-large-zh-v1.5" />
            <el-option label="BAAI/bge-m3 (1024维)" value="BAAI/bge-m3" />
            <el-option label="BAAI/bge-small-zh-v1.5 (512维)" value="BAAI/bge-small-zh-v1.5" />
          </el-select>
        </el-form-item>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="文本分块大小" prop="chunk_size">
              <el-input-number v-model="ragConfigForm.chunk_size" :min="100" :max="10000" :step="100"
                style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分块重叠大小" prop="chunk_overlap">
              <el-input-number v-model="ragConfigForm.chunk_overlap" :min="0" :max="5000" :step="50"
                style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <!-- Milvus配置 -->
        <div v-if="ragConfigForm.vector_db_type === 'milvus'">
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="Milvus主机" prop="milvus_host">
                <el-input v-model="ragConfigForm.milvus_host" placeholder="localhost" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Milvus端口" prop="milvus_port">
                <el-input-number v-model="ragConfigForm.milvus_port" :min="1" :max="65535" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="集合名称" prop="milvus_collection_name">
                <el-input v-model="ragConfigForm.milvus_collection_name" placeholder="knowledge_base" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="向量维度" prop="milvus_dim">
                <el-input-number 
                  v-model="ragConfigForm.milvus_dim" 
                  :min="1" 
                  :max="10000" 
                  :disabled="true"
                  style="width: 100%" />
                <div style="font-size: 12px; color: #909399; margin-top: 4px;">
                  根据嵌入模型自动设置
                </div>
              </el-form-item>
            </el-col>
          </el-row>
        </div>
        
        <el-form-item>
          <el-checkbox v-model="ragConfigForm.is_active">启用此配置</el-checkbox>
        </el-form-item>
      </el-form>
      
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="cancelRAGEdit">取消</el-button>
          <el-button type="primary" @click="saveRAGConfiguration" :loading="saving">
            保存
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataAnalysis, Plus, ArrowDown } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import dayjs from 'dayjs'
import {
  getRAGConfigurations,
  createRAGConfiguration,
  updateRAGConfiguration,
  deleteRAGConfiguration,
  testRAGConnection,
  setDefaultRAGConfiguration,
  toggleRAGConfigurationActive,
} from '@/api/aiConfig'

// 响应式数据
const loading = ref(false)
const saving = ref(false)
const testingConnection = ref(null)
const showCreateRAGDialog = ref(false)
const editingRAGConfig = ref(null)

// 数据
const configurations = ref([])

// 当前配置（只有一个）
const currentConfig = computed(() => {
  return configurations.value.length > 0 ? configurations.value[0] : null
})

// RAG表单数据
const ragConfigForm = reactive({
  name: '',
  vector_db_type: 'chroma',
  embedding_model: 'BAAI/bge-large-zh-v1.5',
  chunk_size: 1000,
  chunk_overlap: 200,
  milvus_host: 'localhost',
  milvus_port: 19530,
  milvus_collection_name: 'knowledge_base',
  milvus_dim: 1024,
  is_active: true,
  is_default: false
})

// RAG表单验证规则
const ragConfigRules = {
  name: [
    { required: true, message: '请输入配置名称', trigger: 'blur' }
  ],
  vector_db_type: [
    { required: true, message: '请选择向量数据库类型', trigger: 'change' }
  ],
  embedding_model: [
    { required: true, message: '请选择嵌入模型', trigger: 'change' }
  ],
  chunk_size: [
    { required: true, message: '请输入分块大小', trigger: 'blur' },
    { type: 'number', min: 100, max: 10000, message: '分块大小必须在100-10000之间', trigger: 'blur' }
  ],
  chunk_overlap: [
    { required: true, message: '请输入分块重叠大小', trigger: 'blur' },
    { type: 'number', min: 0, max: 5000, message: '分块重叠大小必须在0-5000之间', trigger: 'blur' }
  ],
  milvus_host: [
    { 
      validator: (rule, value, callback) => {
        if (ragConfigForm.vector_db_type === 'milvus' && !value) {
          callback(new Error('请输入Milvus主机地址'))
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  milvus_port: [
    { 
      validator: (rule, value, callback) => {
        if (ragConfigForm.vector_db_type === 'milvus' && (!value || value < 1 || value > 65535)) {
          callback(new Error('请输入有效的端口号(1-65535)'))
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ]
}


// 表单引用
const ragConfigFormRef = ref()

// 方法
const loadConfigurations = async () => {
  try {
    loading.value = true
    const response = await getRAGConfigurations()
    
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
    console.error('加载RAG配置失败:', error)
    ElMessage.error('加载RAG配置失败')
  } finally {
    loading.value = false
  }
}

const onRAGDBTypeChange = () => {
  if (ragConfigForm.vector_db_type === 'chroma') {
    ragConfigForm.milvus_host = 'localhost'
    ragConfigForm.milvus_port = 19530
    ragConfigForm.milvus_collection_name = 'knowledge_base'
    // 根据嵌入模型设置维度
    updateMilvusDimByModel()
  }
}

// 根据嵌入模型更新向量维度
const updateMilvusDimByModel = () => {
  const modelDimensions = {
    'BAAI/bge-large-zh-v1.5': 1024,
    'BAAI/bge-m3': 1024,
    'BAAI/bge-small-zh-v1.5': 512,
  }
  ragConfigForm.milvus_dim = modelDimensions[ragConfigForm.embedding_model] || 1024
}

// 监听嵌入模型变化，自动更新维度
watch(() => ragConfigForm.embedding_model, () => {
  updateMilvusDimByModel()
})

// 通用表单重置函数
const resetForm = (formRef, defaultValues) => {
  Object.assign(formRef, defaultValues)
}

// RAG配置相关
const addRAGConfiguration = () => {
  resetForm(ragConfigForm, {
    name: '',
    vector_db_type: 'chroma',
    embedding_model: 'BAAI/bge-large-zh-v1.5',
    chunk_size: 1000,
    chunk_overlap: 200,
    milvus_host: 'localhost',
    milvus_port: 19530,
    milvus_collection_name: 'knowledge_base',
    milvus_dim: 1024,
    is_active: true,
    is_default: false
  })
  editingRAGConfig.value = null
  showCreateRAGDialog.value = true
}

const editRAGConfiguration = (config) => {
  editingRAGConfig.value = config
  resetForm(ragConfigForm, {
    name: config.name,
    vector_db_type: config.vector_db_type,
    embedding_model: config.embedding_model,
    chunk_size: config.chunk_size,
    chunk_overlap: config.chunk_overlap,
    milvus_host: config.milvus_host,
    milvus_port: config.milvus_port,
    milvus_collection_name: config.milvus_collection_name,
    milvus_dim: config.milvus_dim,
    is_active: config.is_active,
    is_default: config.is_default
  })
  showCreateRAGDialog.value = true
}

const saveRAGConfiguration = async () => {
  try {
    await ragConfigFormRef.value.validate()
    saving.value = true

    const data = { ...ragConfigForm }
    // 根据嵌入模型自动设置向量维度
    updateMilvusDimByModel()
    data.milvus_dim = ragConfigForm.milvus_dim
    
    if (editingRAGConfig.value) {
      await updateRAGConfiguration(editingRAGConfig.value.id, data)
      ElMessage.success('RAG配置更新成功')
    } else {
      try {
        await createRAGConfiguration(data)
        ElMessage.success('RAG配置创建成功')
      } catch (error) {
        if (error.response?.data?.message?.includes('只允许存在一个RAG配置')) {
          ElMessage.error('系统只允许存在一个RAG配置，请编辑现有配置')
          return
        }
        throw error
      }
    }

    showCreateRAGDialog.value = false
    await loadConfigurations()
  } catch (error) {
    console.error('保存RAG配置失败:', error)
    if (error !== 'cancel') {
      ElMessage.error(error.response?.data?.message || '保存RAG配置失败')
    }
  } finally {
    saving.value = false
  }
}

const cancelRAGEdit = () => {
  showCreateRAGDialog.value = false
  editingRAGConfig.value = null
  resetForm(ragConfigForm, {
    name: '',
    vector_db_type: 'chroma',
    embedding_model: 'BAAI/bge-large-zh-v1.5',
    chunk_size: 1000,
    chunk_overlap: 200,
    milvus_host: 'localhost',
    milvus_port: 19530,
    milvus_collection_name: 'knowledge_base',
    milvus_dim: 1024,
    is_active: true,
    is_default: false
  })
  ragConfigFormRef?.value?.resetFields()
}

const testRAGConnectionHandler = async (config) => {
  try {
    testingConnection.value = config.id
    
    const response = await testRAGConnection({ config_id: config.id })
    console.log('RAG连接测试API响应:', response)

    let data = null
    if (response && response.data) {
      if (response.data.success && response.data.data) {
        data = response.data.data
      } else {
        data = response.data
      }
    }

    if (data && data.success) {
      ElMessage.success('RAG连接测试成功')
    } else {
      const errorMsg = (data && data.message) || response.error || '未知错误'
      ElMessage.error(`RAG连接测试失败: ${errorMsg}`)
    }
    
  } catch (error) {
    console.error('RAG连接测试失败:', error)
    ElMessage.error('RAG连接测试失败')
  } finally {
    testingConnection.value = null
  }
}

const handleRAGAction = async ({ action, data }) => {
  try {
    switch (action) {
      case 'setDefault':
        await setDefaultRAGConfiguration(data.id)
        ElMessage.success('RAG默认配置设置成功')
        await loadConfigurations()
        break
        
      case 'toggleActive':
        await toggleRAGConfigurationActive(data.id)
        ElMessage.success(`RAG配置已${data.is_active ? '禁用' : '启用'}`)
        await loadConfigurations()
        break
        
      case 'delete':
        await ElMessageBox.confirm(
          `确定要删除RAG配置 "${data.name}" 吗？删除后需要重新创建配置才能使用RAG功能。`,
          '确认删除',
          {
            confirmButtonText: '确定',
            cancelButtonText: '取消',
            type: 'warning'
          }
        )
        
        await deleteRAGConfiguration(data.id)
        ElMessage.success('RAG配置删除成功')
        await loadConfigurations()
        break
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('操作失败:', error)
      ElMessage.error('操作失败')
    }
  }
}

// 工具方法
const getRAGDBTagType = (dbType) => {
  const typeMaps = {
    chroma: 'info',
    milvus: 'success'
  }
  return typeMaps[dbType] || 'default'
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm:ss')
}

// 通用图标工具函数
const getIconText = (name, defaultChar = 'R') => {
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

const getRAGIconText = (name) => getIconText(name, 'R')
const getRAGIconColor = (name) => getIconColor(name, '#409EFF')

// 初始化
onMounted(() => {
  loadConfigurations()
})
</script>

<style scoped>
.rag-config-page {
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
  background: linear-gradient(135deg, #67c23a 0%, #85ce61 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  color: white;
  box-shadow: 0 8px 32px rgba(103, 194, 58, 0.3);
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

/* 配置卡片样式 */
.config-card {
  margin-bottom: 20px;
  border-radius: 0 0 16px 16px;
  border-top: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.config-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 20px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 24px;
}

.config-header-left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
}

.config-icon-wrapper {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.3s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.config-icon-wrapper:hover {
  transform: scale(1.05);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.rag-icon-text {
  color: white;
  font-weight: bold;
  font-size: 24px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
  user-select: none;
}

.config-header-info {
  flex: 1;
  min-width: 0;
}

.config-name-title {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 8px 0;
  line-height: 1.2;
}

.config-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.status-tag {
  font-weight: 500;
}

.config-time {
  font-size: 13px;
  color: #909399;
}

.config-header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.config-content {
  padding-top: 8px;
}

.config-section {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 20px;
  height: 100%;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 16px 0;
  padding-bottom: 12px;
  border-bottom: 2px solid #e4e7ed;
}

.config-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;
}

.config-item:last-child {
  border-bottom: none;
}

.config-label {
  font-size: 14px;
  color: #606266;
  font-weight: 500;
}

.config-value {
  font-size: 14px;
  color: #303133;
  font-weight: 600;
}

.config-value-tag {
  font-weight: 500;
}

/* 空状态卡片 */
.empty-config-card {
  margin-bottom: 20px;
  border-radius: 0 0 16px 16px;
  border-top: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
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

/* 响应式设计 */
@media (max-width: 768px) {
  .rag-config-page {
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

  .config-card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }

  .config-header-actions {
    width: 100%;
    flex-direction: column;
    align-items: stretch;
  }

  .config-header-actions .el-button {
    width: 100%;
  }

  .config-content .el-col {
    margin-bottom: 16px;
  }
}
</style>

