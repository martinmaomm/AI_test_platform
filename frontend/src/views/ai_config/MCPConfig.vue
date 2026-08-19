<template>
  <div class="mcp-config-page">
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
            <h2>MCP配置</h2>
            <p>管理MCP (Model Context Protocol) 服务器配置，扩展AI模型的能力</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="addMCPConfiguration" class="add-config-btn">
            添加MCP配置
          </el-button>
        </div>
      </div>
    </div>

    <!-- MCP配置列表 -->
    <el-card class="config-list-card">
      <div class="card-header">
        <div class="card-header-left">
          <div class="card-title">
            <span>配置列表</span>
          </div>
        </div>
        <div class="card-header-right">
          <el-input v-model="searchQuery" placeholder="搜索MCP配置..." prefix-icon="Search"
            style="width: 300px; margin-right: 10px;" clearable />
          <el-select v-model="providerFilter" placeholder="提供商" clearable style="width: 120px;">
            <el-option label="自定义" value="custom" />
            <el-option label="文件系统" value="filesystem" />
            <el-option label="GitHub" value="github" />
            <el-option label="数据库" value="database" />
            <el-option label="网络搜索" value="web_search" />
            <el-option label="日历" value="calendar" />
            <el-option label="邮件" value="email" />
          </el-select>
        </div>
      </div>

      <el-table :data="filteredConfigurations" v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="配置名称" min-width="100">
          <template #default="scope">
            <div class="config-name">
              <div class="config-icon-wrapper mcp-icon-wrapper" :style="{ backgroundColor: getMCPIconColor(scope.row.name) }">
                <span class="mcp-icon-text">{{ getMCPIconText(scope.row.name) }}</span>
              </div>
              <div class="config-info">
                <div class="config-title">{{ scope.row.name }}</div>
                <div class="config-subtitle">MCP服务器配置</div>
              </div>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="is_active" label="状态" width="150" align="center">
          <template #default="scope">
            <el-switch
              v-model="mcpStatusLoading"
              @update:model-value="toggleMCPStatus(scope.row)"
              :loading="scope.row.statusLoading" 
            />
            
          </template>
        </el-table-column>

        <el-table-column label="工具" width="500">
          <template #default="scope">
            <div style="display: flex; align-items: center; gap: 8px;">
              <el-tag type="info" >已启用{{ scope.row.tools_count || 0 }}个Tools</el-tag>
              <el-popover
                v-if="scope.row.tools_count > 0"
                placement="right"
                :width="420"
                trigger="click"
                popper-class="mcp-tools-popover"
                @show="handlePopoverShow(scope.row)"
                @hide="handlePopoverHide(scope.row)"
              >
                <template #reference>
                  <el-icon 
                    :class="['tools-expand-icon', { 'is-expanded': scope.row.toolsPopoverVisible }]"
                    @click.stop
                  >
                    <ArrowDown />
                  </el-icon>
                </template>
                <template v-if="scope.row.tools && scope.row.tools.length > 0">
                  <div class="tools-popover-header">
                    <span style="font-weight: 500; color: #303133;">工具列表 ({{ scope.row.tools.length }})</span>
                  </div>
                  <div class="tools-popover-content">
                    <el-tooltip
                      v-for="tool in scope.row.tools"
                      :key="tool.id || tool.name"
                      :content="tool.description || '无描述'"
                      placement="top"
                      :disabled="!tool.description"
                      effect="dark"
                    >
                      <el-tag 
                        type="info" 
                        size="small"
                        effect="dark"
                        class="tool-tag"
                      >
                        {{ tool.name }}
                      </el-tag>
                    </el-tooltip>
                  </div>
                </template>
                <div v-else class="tools-popover-empty">
                  <el-icon style="font-size: 24px; color: #c0c4cc; margin-bottom: 8px;">
                    <Document />
                  </el-icon>
                  <div style="color: #909399; font-size: 13px;">暂无工具</div>
                </div>
              </el-popover>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="160" fixed="right">
          <template #default="scope">
            <el-button type="warning" size="small" @click="editMCPConfig(scope.row)">
              编辑
            </el-button>
            <el-button type="danger" size="small" @click="handleDeleteMCPConfiguration(scope.row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建/编辑MCP配置对话框 -->
    <el-dialog 
      v-model="showCreateMCPDialog" 
      :title="editingMCPConfig ? '编辑MCP配置' : '添加MCP配置'" 
      width="900px"
      :close-on-click-modal="false" 
      class="mcp-dialog"
      :modal="true"
      :append-to-body="true"
      :destroy-on-close="false"
      :center="false"
      :top="'5vh'"
    >
      <div class="mcp-config-dialog">
        <!-- JSON编辑器 -->
        <div class="mcp-editor-section">
          <div class="editor-header">
            <div class="editor-title-section">
              <h3 class="editor-title">MCP配置JSON</h3>
              <span class="editor-subtitle">粘贴您的MCP配置JSON到下方编辑器</span>
            </div>
            <div class="editor-actions">
              <el-button type="primary" size="small" @click="formatMCPConfig" :icon="Search">
                格式化
              </el-button>
              <el-button size="small" @click="clearMCPConfig">
                清空
              </el-button>
            </div>
          </div>
          <div class="mcp-editor-container">
            <MonacoEditor
              ref="mcpEditorRef"
              :value="mcpConfigForm.rawConfig"
              language="json"
              height="350px"
              :options="monacoOptions"
              @change="onMCPConfigChange"
            />
          </div>
          <div class="editor-footer">
            <span class="editor-tip">
              💡 提示：支持标准的MCP配置格式，编辑器会自动验证JSON语法。
              <span v-if="!editingMCPConfig">多个服务器配置会被自动拆分成多个独立的配置项</span>
              <span v-else>编辑现有配置，修改后保存即可</span>
            </span>
          </div>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="cancelMCPEdit">取消</el-button>
          <el-button type="primary" @click="saveMCPConfiguration" :loading="saving">
            {{ editingMCPConfig ? '更新' : '保存' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Search, Plus, ArrowDown, ArrowUp, Document } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import MonacoEditor from '@/components/MonacoEditor.vue'
import dayjs from 'dayjs'
import {
  getMCPConfigurations,
  createMCPConfiguration,
  updateMCPConfiguration,
  deleteMCPConfiguration,
  getMCPConfiguration,
  toggleMCPConfigurationActive,
} from '@/api/aiConfig'

// 响应式数据
const loading = ref(false)
const saving = ref(false)
const showCreateMCPDialog = ref(false)
const editingMCPConfig = ref(null)
const searchQuery = ref('')
const providerFilter = ref('')
const mcpStatusLoading = ref(true)

// Monaco Editor 配置
const monacoOptions = ref({
  theme: 'vs-dark',
  fontSize: 14,
  lineNumbers: 'on',
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  automaticLayout: true,
  wordWrap: 'on',
  formatOnPaste: true,
  formatOnType: true,
  tabSize: 2,
  insertSpaces: true,
  bracketPairColorization: { enabled: true },
  folding: true,
  foldingStrategy: 'indentation',
  placeholder: '请输入MCP配置JSON，例如：\n{\n  "mcpServers": {\n    "server-name": {\n      "command": "npx",\n      "args": ["-y", "@package/name"]\n    }\n  }\n}'
})

// 数据
const configurations = ref([])

// MCP表单数据
const mcpConfigForm = reactive({
  rawConfig: '' // 原始MCP配置JSON
})

// 计算属性
const filteredConfigurations = computed(() => {
  let result = configurations.value

  if (searchQuery.value) {
    result = result.filter(config =>
      config.name.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      (config.rawConfig && config.rawConfig.toLowerCase().includes(searchQuery.value.toLowerCase()))
    )
  }

  if (providerFilter.value) {
    result = result.filter(config => config.provider === providerFilter.value)
  }

  return result
})

// 表单引用
const mcpEditorRef = ref()

// 方法
const loadConfigurations = async () => {
  try {
    loading.value = true
    const response = await getMCPConfigurations()
    
    let configs = []
    if (response?.data) {
      if (Array.isArray(response.data)) {
        configs = response.data
      } else if (response.data.items && Array.isArray(response.data.items)) {
        configs = response.data.items
      } else if (response.data.success && response.data.data) {
        configs = Array.isArray(response.data.data) ? response.data.data : []
      }
    }
    
    // 为每个配置项初始化状态
    configurations.value = configs.map(config => ({
      ...config,
      statusLoading: false,
      tools: config.tools || [],
      toolsPopoverVisible: false
    }))
  } catch (error) {
    console.error('加载MCP配置失败:', error)
    ElMessage.error('加载MCP配置失败')
  } finally {
    loading.value = false
  }
}

const editMCPConfig = (config) => {
  console.log('编辑MCP配置，原始数据:', config)
  console.log('rawConfig字段值:', config.rawConfig)
  
  editingMCPConfig.value = config
  Object.assign(mcpConfigForm, {
    rawConfig: config.rawConfig || ''
  })
  
  editMCPConfiguration()
}

// 添加MCP配置
const addMCPConfiguration = () => {
  console.log('添加MCP配置')
  
  editingMCPConfig.value = null
  mcpConfigForm.rawConfig = ''
  
  showCreateMCPDialog.value = true
  
  setTimeout(() => {
    if (mcpEditorRef.value) {
      console.log('通过ref设置MonacoEditor内容: 空')
      mcpEditorRef.value.setValue('')
    } else {
      console.warn('MonacoEditor ref未找到')
    }
  }, 300)
}

// 编辑MCP配置
const editMCPConfiguration = () => {
  console.log('编辑MCP配置')
  
  console.log('当前rawConfig:', mcpConfigForm.rawConfig)
  
  showCreateMCPDialog.value = true
  
  setTimeout(() => {
    if (mcpEditorRef.value) {
      console.log('通过ref设置MonacoEditor内容:', mcpConfigForm.rawConfig)
      mcpEditorRef.value.setValue(mcpConfigForm.rawConfig || '')
    } else {
      console.warn('MonacoEditor ref未找到')
    }
  }, 300)
}

const saveMCPConfiguration = async () => {
  try {
    console.log('开始保存MCP配置，表单数据:', mcpConfigForm)
    
    if (!mcpConfigForm.rawConfig || mcpConfigForm.rawConfig.trim() === '') {
      ElMessage.error('请输入MCP配置JSON')
      return
    }
    
    try {
      const config = JSON.parse(mcpConfigForm.rawConfig)
      if (!config.mcpServers) {
        ElMessage.error('请提供完整的MCP配置格式，必须包含mcpServers字段')
        return
      }
      if (typeof config.mcpServers !== 'object' || Array.isArray(config.mcpServers)) {
        ElMessage.error('mcpServers必须是对象格式')
        return
      }
      if (Object.keys(config.mcpServers).length === 0) {
        ElMessage.error('MCP配置中至少需要一个服务器配置')
        return
      }
      for (const [serverName, serverConfig] of Object.entries(config.mcpServers)) {
        if (!serverConfig.command) {
          ElMessage.error(`服务器 '${serverName}' 必须包含command字段`)
          return
        }
      }
    } catch (e) {
      ElMessage.error('JSON格式不正确')
      return
    }
    
    console.log('验证通过')
    
    saving.value = true

    const data = { ...mcpConfigForm }
    console.log('发送的数据:', data)

    if (editingMCPConfig.value) {
      console.log('更新MCP配置，ID:', editingMCPConfig.value.id)
      const result = await updateMCPConfiguration(editingMCPConfig.value.id, data)
      console.log('更新结果:', result)
      ElMessage.success('MCP配置更新成功')
    } else {
      console.log('创建MCP配置')
      
      const config = JSON.parse(mcpConfigForm.rawConfig)
      const serverEntries = Object.entries(config.mcpServers)
      
      if (serverEntries.length > 1) {
        console.log(`检测到 ${serverEntries.length} 个MCP服务器，将拆分成多个配置`)
        
        let successCount = 0
        let errorCount = 0
        
        for (const [serverName, serverConfig] of serverEntries) {
          try {
            const singleServerData = {
              rawConfig: JSON.stringify({
                mcpServers: {
                  [serverName]: serverConfig
                }
              })
            }
            
            console.log(`创建单个MCP配置: ${serverName}`)
            const result = await createMCPConfiguration(singleServerData)
            console.log(`创建结果 ${serverName}:`, result)
            successCount++
          } catch (error) {
            console.error(`创建MCP配置 ${serverName} 失败:`, error)
            errorCount++
          }
        }
        
        if (errorCount === 0) {
          ElMessage.success(`成功创建 ${successCount} 个MCP配置`)
        } else {
          ElMessage.warning(`成功创建 ${successCount} 个MCP配置，${errorCount} 个失败`)
        }
      } else {
        const result = await createMCPConfiguration(data)
        console.log('创建结果:', result)
        ElMessage.success('MCP配置创建成功')
      }
    }

    showCreateMCPDialog.value = false
    await loadConfigurations()
  } catch (error) {
    console.error('保存MCP配置失败:', error)
    console.error('错误详情:', error.response?.data || error.message)
    ElMessage.error(`保存MCP配置失败: ${error.response?.data?.message || error.message}`)
  } finally {
    saving.value = false
  }
}

const cancelMCPEdit = () => {
  showCreateMCPDialog.value = false
  editingMCPConfig.value = null
  mcpConfigForm.rawConfig = ''
}

// 删除MCP配置
const handleDeleteMCPConfiguration = async (config) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除MCP配置 "${config.name}" 吗？`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteMCPConfiguration(config.id)
    ElMessage.success('MCP配置删除成功')
    await loadConfigurations()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除MCP配置失败:', error)
      ElMessage.error('删除MCP配置失败')
    }
  }
}

// Monaco Editor 相关方法
const onMCPConfigChange = (value) => {
  mcpConfigForm.rawConfig = value
}

const formatMCPConfig = () => {
  try {
    if (mcpConfigForm.rawConfig) {
      const parsed = JSON.parse(mcpConfigForm.rawConfig)
      mcpConfigForm.rawConfig = JSON.stringify(parsed, null, 2)
      ElMessage.success('JSON格式化成功')
    } else {
      ElMessage.warning('请先输入JSON内容')
    }
  } catch (error) {
    ElMessage.error('JSON格式不正确，无法格式化')
  }
}

const clearMCPConfig = () => {
  mcpConfigForm.rawConfig = ''
}

// 监听对话框打开，调整编辑器大小
watch(showCreateMCPDialog, (newVal) => {
  if (newVal) {
    setTimeout(() => {
      const editor = document.querySelector('.monaco-editor-container')
      if (editor) {
        window.dispatchEvent(new Event('resize'))
      }
    }, 300)
  }
})

// 切换MCP状态
const toggleMCPStatus = async (config) => {
  // 点击时立即显示loading
  config.statusLoading = true
  
  try {
    const originalStatus = config.is_active
    
    // 调用接口切换状态
    const response = await toggleMCPConfigurationActive(config.id)
    
    // 切换状态
    config.is_active = !originalStatus
    
    // 如果接口返回了工具信息，更新工具数量和工具列表
    if (response?.data?.data) {
      const responseData = response.data.data
      if (typeof responseData.tools_count === 'number') {
        config.tools_count = responseData.tools_count
      }
      if (Array.isArray(responseData.tools)) {
        config.tools = responseData.tools
      }
    }
    
    ElMessage.success(config.is_active ? 'MCP配置已启用' : 'MCP配置已禁用')
  } catch (error) {
    console.error('切换MCP状态失败:', error)
    ElMessage.error(`切换MCP状态失败: ${error.response?.data?.message || error.message}`)
  } finally {
    // 接口返回后关闭loading
    config.statusLoading = false
  }
}

// 处理弹出层显示
const handlePopoverShow = async (config) => {
  config.toolsPopoverVisible = true
  await loadToolsIfNeeded(config)
}

// 处理弹出层隐藏
const handlePopoverHide = (config) => {
  config.toolsPopoverVisible = false
}

// 加载工具列表（如果需要）
const loadToolsIfNeeded = async (config) => {
  // 如果已经加载过工具列表，直接返回
  if (config.tools && config.tools.length > 0) {
    return
  }
  
  // 加载工具列表
  try {
    const response = await getMCPConfiguration(config.id)
    if (response?.data?.data?.tools) {
      config.tools = response.data.data.tools
    } else if (response?.data?.tools) {
      config.tools = response.data.tools
    } else {
      config.tools = []
    }
  } catch (error) {
    console.error('获取工具列表失败:', error)
    ElMessage.warning('获取工具列表失败')
    config.tools = []
  }
}

// 工具方法
const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm:ss')
}

// 通用图标工具函数
const getIconText = (name, defaultChar = 'M') => {
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

const getMCPIconText = (name) => getIconText(name, 'M')
const getMCPIconColor = (name) => getIconColor(name, '#409EFF')

// 初始化
onMounted(() => {
  loadConfigurations()
})
</script>

<style scoped>
.mcp-config-page {
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
  gap: 20px;
  flex-grow: 1;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #46474a;
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

/* MCP图标样式 */
.mcp-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  min-width: 40px;
  height: 40px;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.mcp-icon-wrapper:hover {
  transform: scale(1.1);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.mcp-icon-text {
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

.config-subtitle {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
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

/* MCP配置对话框样式 */
.mcp-dialog .el-dialog__body {
  padding: 0;
  max-height: 70vh;
  overflow-y: auto;
}

.mcp-dialog .el-dialog {
  margin-top: 5vh !important;
  margin-bottom: 5vh !important;
}

.mcp-dialog .el-dialog__footer {
  padding: 15px 20px;
  border-top: 1px solid #ebeef5;
  background: #fafafa;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.mcp-config-dialog {
  padding: 0;
}

.mcp-editor-section {
  padding: 20px;
  background: #fff;
  max-height: 500px;
  overflow-y: auto;
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.editor-title-section {
  flex: 1;
}

.editor-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 4px 0;
}

.editor-subtitle {
  font-size: 13px;
  color: #909399;
  line-height: 1.4;
}

.editor-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.mcp-editor-container {
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
  height: 350px;
  min-height: 350px;
}

.mcp-editor-container:hover {
  border-color: #c0c4cc;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.mcp-editor-container:focus-within {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.1), 0 2px 8px rgba(0, 0, 0, 0.08);
}

.editor-footer {
  margin-top: 12px;
  text-align: center;
}

.editor-tip {
  font-size: 12px;
  color: #909399;
  background: #f8f9fa;
  padding: 8px 12px;
  border-radius: 4px;
  display: inline-block;
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

/* 工具展开图标样式 */
.tools-expand-icon {
  cursor: pointer;
  color: #409EFF;
  font-size: 24px;
  transition: all 0.3s ease;
  padding: 4px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}


.tools-expand-icon.is-expanded {
  transform: rotate(270deg);
}


/* 工具弹出层样式 */
:deep(.mcp-tools-popover) {
  padding: 0 !important;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.tools-popover-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
  background: #fafafa;
  border-radius: 8px 8px 0 0;
  font-size: 14px;
}

.tools-popover-content {
  padding: 12px 16px;
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tool-tag {
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.tool-tag:hover {
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
}

.tools-popover-empty {
  padding: 40px 20px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .mcp-config-page {
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
