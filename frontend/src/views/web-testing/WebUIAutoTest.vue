<template>
  <div class="webui-agent">
    <!-- 主要内容区域 -->
    <div v-if="selectedProject" class="main-container">
      <!-- 页面头部 -->
      <div class="page-header">
        <div class="header-content">
          <div class="header-left">
            <div class="header-icon">
              <el-icon>
                <VideoPlay />
              </el-icon>
            </div>
            <div class="header-text">
              <h3>WebUI自动化测试智能体</h3>
              <p>探索性智能自动化脚本生成工具 (草稿箱)</p>
            </div>
          </div>
          <div class="header-actions">
            <div class="connection-status" :class="{ connected: isConnected }">
              <div class="status-indicator"></div>
              <span class="status-text">{{ isConnected ? '已连接' : '未连接' }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 执行进度和时间线 -->
      <div v-if="isCreating || timelineNodes.length > 0" class="timeline-section">
        <div class="timeline-card">
          <div class="timeline-content" ref="timelineContentRef">
            <div class="horizontal-timeline">
              <div v-for="(node, index) in timelineNodes" :key="node.id" class="timeline-item">
                <div class="timeline-node" :class="{ 'current-node': node.status === 'executing' }">
                  <div class="node-header">
                    <div class="node-number-status-row">
                      <div class="node-number-row">
                        <span class="node-number">{{ index + 1 }}</span>

                      </div>
                      <div class="node-status">
                        <el-tag :type="getNodeStatusType(node.status)" size="small">
                          {{ getNodeStatusText(node.status) }}
                        </el-tag>
                        <el-icon v-if="node.status === 'executing'" class="current-loading">
                          <Loading />
                        </el-icon>
                      </div>
                    </div>
                    <div class="node-name">{{ node.displayName }}</div>
                  </div>
                </div>
                <div v-if="index < timelineNodes.length - 1" class="timeline-connector"
                  :class="{ 'active': node.status === 'completed' || node.status === 'executing' }"></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 创建和输出区域 -->
      <div class="content-section">
        <!-- 创建测试脚本表单 -->
        <div class="form-card">
          <form @submit.prevent="createTestScript" class="compact-form">
            <!-- 内容区域 -->
            <div class="form-content-section">
              <el-alert 
                title="⚠️ 实验性功能提示：此工具基于自然语言自由推理生成探索性脚本，仅供语法参考与本地调试。如需编写包含 POM 规范与智能断言的企业级流水线，请前往【测试用例管理】模块。" 
                type="warning" 
                :closable="false" 
                show-icon 
                style="margin-bottom: 16px;" 
              />
              <!-- 输入配置 -->
              <div class="content-item">
                <div class="input-section">
                  <div class="input-group">
                    <label for="targetUrl" class="input-label">目标URL</label>
                    <input 
                      id="targetUrl" 
                      v-model="manualFormData.url" 
                      type="url" 
                      placeholder="https://example.com" 
                      required
                      :disabled="isCreating" 
                      class="compact-input" 
                      :class="{ 'invalid': manualFormData.url && !isValidUrl(manualFormData.url) }" 
                    />
                  </div>
                  <div class="input-group">
                    <label for="description" class="input-label">测试描述</label>
                    <textarea 
                      id="description" 
                      v-model="manualFormData.description" 
                      placeholder="描述测试操作..." 
                      rows="8"
                      :maxlength="MAX_DESCRIPTION_LENGTH"
                      required 
                      :disabled="isCreating" 
                      class="compact-textarea" 
                      :class="{ 'invalid': manualFormData.description && manualFormData.description.trim().length === 0 }"
                    ></textarea>
                    <div class="description-counter">
                      {{ manualFormData.description.length }}/{{ MAX_DESCRIPTION_LENGTH }}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 提交按钮 -->
            <div class="form-actions">
              <el-button 
                v-if="!isCreating"
                type="primary"
                size="large"
                :disabled="!isFormValid"
                @click="createTestScript"
                class="create-script-btn"
              >
                <el-icon class="btn-icon"><VideoPlay /></el-icon>
                <span>生成草稿脚本</span>
              </el-button>
              <el-button 
                v-else
                type="danger"
                size="large"
                :loading="isStopping"
                :disabled="isStopping"
                @click="stopTestScript"
                class="stop-script-btn"
              >
                <el-icon class="btn-icon"><CircleClose /></el-icon>
                <span>{{ isStopping ? '正在停止...' : '停止任务' }}</span>
              </el-button>
            </div>
          </form>
        </div>

        <!-- 输出面板 -->
        <div class="output-card">
          <div class="output-header">
            <div class="view-switch">
              <el-button-group>
                <el-button 
                  size="small" 
                  :type="currentView === 'streaming' ? 'primary' : 'default'"
                  @click="currentView = 'streaming'"
                  :disabled="!streamingContent">
                  实时输出
                </el-button>
                <el-button 
                  size="small" 
                  :type="currentView === 'script' ? 'primary' : 'default'"
                  @click="currentView = 'script'"
                  :disabled="!finalScript">
                  最终脚本
                </el-button>
              </el-button-group>
            </div>
            <el-button
              v-if="finalScript"
              type="success"
              size="small"
              :loading="isSaving"
              :disabled="isSaving || !!savedTestCaseId"
              @click="saveGeneratedScript"
            >
              {{ savedTestCaseId ? '已保存到测试用例' : '保存到测试用例' }}
            </el-button>
          </div>
          
          <!-- 实时输出视图 -->
          <div v-if="currentView === 'streaming'" class="streaming-output" ref="cursorOutputRef">
            <div class="cursor-style-output">
              <div v-for="(line, index) in formattedStreamingContent" :key="index" 
                   class="output-line">
                <div class="line-content">
                  {{ line.content }}
                  <span v-if="index === formattedStreamingContent.length - 1 && streamingContent" class="typing-cursor"></span>
                </div>
                <div class="line-actions">
                  <el-button size="small" text @click="copyLine(line.content)" class="copy-line-btn">
                    <el-icon><DocumentCopy /></el-icon>
                  </el-button>
                </div>
              </div>
            </div>
          </div>
          
          <!-- 最终脚本视图 -->
          <div v-else-if="currentView === 'script'" class="script-output">
            <div class="script-content">
              <div class="script-editor">
                <MonacoEditor
                  :value="finalScript"
                  language="python"
                  theme="vs-dark"
                  :read-only="true"
                  height="100%"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 项目选择提示 -->
    <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon style="margin-bottom: 20px;">
      <template #default>
        <div>
          <p>您还没有选择当前工作项目，请前往项目管理页面选择项目。</p>
          <el-button type="primary" size="small" @click="goToProjects" style="margin-top: 10px;">
            前往项目管理
          </el-button>
        </div>
      </template>
    </el-alert>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { Loading, VideoPlay, Refresh, DocumentCopy, Download, Warning, Setting, Link, CircleClose } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createWebUITestScript, saveGeneratedWebUITestScript, stopWebUITestScript } from '@/api/webTesting'
import { 
  WebSocketManager, 
  WebSocketMessageHandler
} from '@/config/websocket'
import MonacoEditor from '@/components/MonacoEditor.vue'

const router = useRouter()
const projectStore = useProjectStore()
const authStore = useAuthStore()

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 格式化流式输出内容
const formattedStreamingContent = computed(() => {
  if (!streamingContent.value) return []
  
  return streamingContent.value.split('\n').map(line => ({
    content: line,
    type: 'normal'
  }))
})

// URL验证函数
const isValidUrl = (url) => {
  if (!url || !url.trim()) return false
  try {
    const urlObj = new URL(url)
    return urlObj.protocol === 'http:' || urlObj.protocol === 'https:'
  } catch {
    return false
  }
}

const MAX_DESCRIPTION_LENGTH = 2000

// 表单验证计算属性
const isFormValid = computed(() => {
  return isValidUrl(manualFormData.url) &&
    manualFormData.description.trim().length > 0 &&
    manualFormData.description.length <= MAX_DESCRIPTION_LENGTH
})

// WebSocket相关状态
const websocketManager = ref(null)
const messageHandler = ref(null)
const isConnected = ref(false)
const isCreating = ref(false)
const isGenerating = ref(false)
const streamingContent = ref('')
const cursorOutputRef = ref(null)
const currentView = ref('streaming')
const finalScript = ref('')
const currentTaskId = ref(null) // 当前任务的ID
const isStopping = ref(false) // 是否正在停止任务
const isSaving = ref(false)
const savedTestCaseId = ref(null)


// 时间线相关
const timelineNodes = ref([
  {
    id: 'load_mcp_config',
    nodeName: 'load_mcp_config',
    displayName: '加载MCP配置智能体',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'initialize_mcp',
    nodeName: 'initialize_mcp',
    displayName: '初始化MCP智能体',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'call_mcp',
    nodeName: 'call_mcp',
    displayName: 'MCP生成脚本智能体',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'save_script',
    nodeName: 'save_script',
    displayName: '数据库保存智能体',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  }
])
const timelineContentRef = ref(null)

// 表单数据
const manualFormData = reactive({
  url: '',
  description: ''
})


// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// WebSocket管理
const initWebSocket = () => {
  const token = authStore.accessToken
  if (!token) {
    console.error('❌ 缺少 token，无法连接 WebSocket')
    return
  }

  // 创建WebSocket管理器和消息处理器
  websocketManager.value = new WebSocketManager()
  messageHandler.value = new WebSocketMessageHandler()

  // 注册消息处理器
  messageHandler.value.registerHandler('streaming_output', (data, ctx) => {
    handleStreamingOutput(data)
  })

  messageHandler.value.registerHandler('node_start', (data, ctx) => {
    handleNodeStart(data)
  })

  messageHandler.value.registerHandler('task_completed', (data, ctx) => {
    handleTaskCompleted(data)
  })

  messageHandler.value.registerHandler('task_failed', (data, ctx) => {
    handleTaskFailed(data)
  })

  // 创建WebSocket配置
  const config = {
    onOpen: () => {
      isConnected.value = true
    },
    onMessage: (event) => {
      messageHandler.value.handleMessage(event, {})
    },
    onClose: (event) => {
      isConnected.value = false
    },
    onError: (error) => {
      isConnected.value = false
      console.error('WebSocket连接错误:', error)
    },
    autoReconnect: true
  }

  websocketManager.value.initWebSocket('/ws/webui_auto_test-streaming/', token, config)
}

const closeWebSocket = () => {
  if (websocketManager.value) {
    websocketManager.value.closeWebSocket()
    websocketManager.value = null
  }
  messageHandler.value = null
  isConnected.value = false
}


// 滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    if (cursorOutputRef.value) {
      cursorOutputRef.value.scrollTop = cursorOutputRef.value.scrollHeight
    }
  })
}

// 处理流式输出
const handleStreamingOutput = (data) => {
  const content = data.content || ''
  
  if (content) {
    streamingContent.value += content
  }
  
  // 自动滚动到底部
  nextTick(() => {
    if (cursorOutputRef.value) {
      cursorOutputRef.value.scrollTop = cursorOutputRef.value.scrollHeight
    }
  })
}

// 处理节点开始
const handleNodeStart = (data) => {
  const nodeName = data.node_name || ''
  
  if (nodeName && updateTimelineNodeFromStep) {
    updateTimelineNodeFromStep(nodeName)
  }
}

// 处理任务完成
const handleTaskCompleted = (data) => {
  // 结束加载状态
  isGenerating.value = false
  savedTestCaseId.value = null

  // 确保save_script节点被标记为执行中，然后完成
  updateTimelineNode('save_script', 'executing')
  
  // 等待一小段时间，让save_script节点有时间计算
  setTimeout(() => {
    // 将所有执行中的节点标记为已完成
    timelineNodes.value.forEach(node => {
      if (node.status === 'executing') {
        node.status = 'completed'
        node.endTime = new Date()
        node.duration = node.endTime - node.startTime
      }
    })
  }, 100)

  // 处理任务结果
  if (data && data.result && data.result.test_script) {
    finalScript.value = data.result.test_script
    // 任务完成后自动切换到脚本视图
    currentView.value = 'script'
  }
  
  // 结束创建状态
  isCreating.value = false
  currentTaskId.value = null
  
}

// 处理任务失败
const handleTaskFailed = (data) => {
  // 结束加载状态
  isGenerating.value = false

  // 检查是否是取消状态
  const isCancelled = data.status === 'cancelled' || 
                      (data.error && data.error.includes('任务已被取消')) ||
                      (data.message && data.message.includes('任务已被取消'))

  // 将所有执行中的节点标记为失败（如果是取消，不标记为失败，直接结束）
  if (!isCancelled) {
    timelineNodes.value.forEach(node => {
      if (node.status === 'executing') {
        node.status = 'failed'
        node.endTime = new Date()
        node.duration = node.endTime - node.startTime
      }
    })
  } else {
    // 如果是取消，将所有执行中的节点标记为已完成（因为用户主动取消）
    timelineNodes.value.forEach(node => {
      if (node.status === 'executing') {
        node.status = 'completed'
        node.endTime = new Date()
        node.duration = node.endTime - node.startTime
      }
    })
  }

  // 结束创建状态
  isCreating.value = false
  currentTaskId.value = null
  
  // 如果不是取消状态，才显示错误消息（取消状态已经在停止按钮点击时提示过了）
  if (!isCancelled) {
    const errorMsg = data.error || data.message || '任务执行失败'
    alert(`任务执行失败: ${errorMsg}`)
  }
}

// 根据步骤名称更新时间线节点
const updateTimelineNodeFromStep = (step) => {
  // 步骤名称到时间线节点ID的映射
  const stepToNodeMap = {
    'load_mcp_config': 'load_mcp_config',
    'initialize_mcp': 'initialize_mcp', 
    'call_mcp': 'call_mcp',
    'save_script': 'save_script',
    '保存脚本': 'save_script',
    '数据库保存': 'save_script',
    '脚本保存': 'save_script'
  }

  const nodeId = stepToNodeMap[step]
  if (nodeId) {
    updateTimelineNode(nodeId, 'executing')
  }
}

// 更新时间线节点状态
const updateTimelineNode = (nodeName, status) => {
  const node = timelineNodes.value.find(n => n.nodeName === nodeName)
  if (node) {
    // 如果新节点开始执行，将之前所有执行中的节点标记为已完成
    if (status === 'executing') {
      timelineNodes.value.forEach(n => {
        if (n.status === 'executing' && n.nodeName !== nodeName) {
          n.status = 'completed'
          n.endTime = new Date()
          n.duration = n.endTime - n.startTime
        }
      })
      // 设置当前节点开始时间
      node.startTime = new Date()
    }

    node.status = status
    node.timestamp = new Date()

    if (status === 'completed' || status === 'failed') {
      node.endTime = new Date()
      if (node.startTime) {
        node.duration = node.endTime - node.startTime
      }
    }
  }
}

// 重置时间线节点状态
const resetTimeline = () => {
  timelineNodes.value.forEach(node => {
    node.status = 'pending'
    node.timestamp = null
    node.startTime = null
    node.endTime = null
    node.duration = null
  })
}

// 获取节点状态类型
const getNodeStatusType = (status) => {
  switch (status) {
    case 'pending': return 'info'
    case 'executing': return 'warning'
    case 'completed': return 'success'
    case 'failed': return 'danger'
    default: return 'info'
  }
}

// 获取节点状态文本
const getNodeStatusText = (status) => {
  switch (status) {
    case 'pending': return '等待中'
    case 'executing': return '执行中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return '未知'
  }
}

// 格式化持续时间
const formatDuration = (duration) => {
  if (!duration) return ''
  const seconds = Math.floor(duration / 1000)
  if (seconds < 60) {
    return `${seconds}s`
  } else {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}m${remainingSeconds}s`
  }
}

// 复制单行内容
const copyLine = async (lineContent) => {
  try {
    await navigator.clipboard.writeText(lineContent)
    console.log('行内容已复制到剪贴板')
  } catch (error) {
    console.error('复制失败:', error)
    // 降级方案
    const textArea = document.createElement('textarea')
    textArea.value = lineContent
    document.body.appendChild(textArea)
    textArea.select()
    document.execCommand('copy')
    document.body.removeChild(textArea)
  }
}

// 取消创建
const cancelCreation = () => {
  isCreating.value = false
  streamingContent.value = ''
  finalScript.value = ''
  currentView.value = 'streaming'
  currentTaskId.value = null
  resetTimeline()
}

// 停止测试脚本生成任务
const stopTestScript = async () => {
  if (!currentTaskId.value || isStopping.value) return

  try {
    // 显示确认对话框
    await ElMessageBox.confirm(
      '确定要停止当前任务吗？停止后无法恢复当前进度。',
      '确认停止',
      {
        confirmButtonText: '确定停止',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    // 用户取消操作
    return
  }

  try {
    isStopping.value = true
    
    const response = await stopWebUITestScript(projectStore.currentProjectId, currentTaskId.value)

    if (response.success) {
      // 停止成功，重置状态
      isCreating.value = false
      currentTaskId.value = null
      streamingContent.value = ''
      finalScript.value = ''
      currentView.value = 'streaming'
      resetTimeline()
      
      // 不显示成功消息，避免与后端WebSocket消息重复
      // 后端会通过WebSocket发送task_failed消息，前端会在handleTaskFailed中处理
    } else {
      alert(`停止任务失败: ${response.message || '未知错误'}`)
    }
  } catch (error) {
    console.error('停止任务失败:', error)
    alert(`停止任务失败: ${error.message || '未知错误'}`)
  } finally {
    isStopping.value = false
  }
}



// 创建测试脚本
const createTestScript = async () => {
  if (isCreating.value) return

  if (!selectedProject.value) {
    alert('请先选择一个项目')
    return
  }

  // 验证表单数据
  if (!isFormValid.value) {
    if (!isValidUrl(manualFormData.url)) {
      alert('请输入有效的URL地址')
    } else if (!manualFormData.description.trim()) {
      alert('请填写测试描述')
    }
    return
  }

  try {
    isCreating.value = true
    streamingContent.value = ''
    finalScript.value = ''
    savedTestCaseId.value = null
    currentView.value = 'streaming'
    currentTaskId.value = null
    resetTimeline()
    
    // 确保滚动到底部
    nextTick(() => {
      scrollToBottom()
    })

    const response = await createWebUITestScript(projectStore.currentProjectId, {
      description: manualFormData.description,
      url: manualFormData.url,
      project_id: selectedProject.value.id
    })

    if (response.success) {
      // 保存任务ID
      if (response.data && response.data.task_id) {
        currentTaskId.value = response.data.task_id
      }
    } else {
      alert(`创建失败: ${response.message}`)
      isCreating.value = false
      currentTaskId.value = null
    }
  } catch (error) {
    console.error('创建测试脚本失败:', error)
    alert(`创建失败: ${error.message}`)
    isCreating.value = false
    currentTaskId.value = null
  }
}

// 将当前生成的脚本保存为可在“测试用例管理”中继续编辑和执行的测试用例
const saveGeneratedScript = async () => {
  if (!finalScript.value || isSaving.value || savedTestCaseId.value) return

  const defaultTitle = `WebUI脚本_${new Date().toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(/[/:]/g, '').replace(/\s/g, '_')}`

  let title
  try {
    const promptResult = await ElMessageBox.prompt(
      '保存后可以在“WebUI测试用例管理”中继续编辑、生成代码和执行。',
      '保存到测试用例',
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: defaultTitle,
        inputPlaceholder: '请输入测试用例标题',
        inputValidator: (value) => {
          const normalized = value?.trim() || ''
          if (!normalized) return '测试用例标题不能为空'
          if (normalized.length > 200) return '测试用例标题不能超过200个字符'
          return true
        }
      }
    )
    title = promptResult.value.trim()
  } catch {
    return
  }

  try {
    isSaving.value = true
    const response = await saveGeneratedWebUITestScript(projectStore.currentProjectId, {
      title,
      description: manualFormData.description.trim(),
      url: manualFormData.url.trim(),
      test_script_content: finalScript.value
    })

    if (!response.success) {
      throw new Error(response.message || '保存脚本失败')
    }

    savedTestCaseId.value = response.data?.id || response.data?.test_case_id
    ElMessage.success(`脚本已保存，可在测试用例管理中查看（ID: ${savedTestCaseId.value}）`)
  } catch (error) {
    console.error('保存生成脚本失败:', error)
    ElMessage.error(error.response?.data?.message || error.message || '保存脚本失败')
  } finally {
    isSaving.value = false
  }
}

// 监听token变化，重新连接WebSocket
watch(() => authStore.accessToken, (newToken, oldToken) => {
  if (newToken && newToken !== oldToken) {
    // token更新时重新连接WebSocket
    initWebSocket()
  } else if (!newToken && oldToken) {
    // token被清除时关闭WebSocket
    closeWebSocket()
  }
}, { immediate: false })

// 组件挂载
onMounted(() => {
  if (authStore.isAuthenticated) {
    initWebSocket()
  }
})

// 组件卸载
onUnmounted(() => {
  closeWebSocket()
})
</script>

<style scoped>
.webui-agent {
  height: calc(100vh - 80px);
  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.no-project-warning {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  max-width: 500px;
}

/* 页面头部和执行进度统一容器 */
.main-container {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  margin: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
}

/* 页面头部 */
.page-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  margin-bottom: 0;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  position: relative;
  overflow: hidden;
}

.page-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.header-content {
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  z-index: 1;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
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

.header-text h3 {
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

.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.3);
  font-size: 14px;
  font-weight: 500;
}

.connection-status.connected {
  background: rgba(34, 197, 94, 0.2);
  border-color: rgba(34, 197, 94, 0.3);
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #ef4444;
  transition: all 0.3s ease;
}

.connection-status.connected .status-indicator {
  background-color: #22c55e;
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.5);
}

/* 时间线部分 */
.timeline-section {
  margin-bottom: 0;
  margin-top: 0;
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
  backdrop-filter: blur(10px);
  flex-shrink: 0;
  max-height: 200px;
  overflow-y: auto;
  position: relative;
}

.timeline-section::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.03) 100%);
  pointer-events: none;
}

/* 内容区域 */
.content-section {
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px);
  margin-top: 0;
  flex: 1;
  display: flex;
  flex-direction: row;
  min-height: 0;
  overflow: hidden;
  height: 100%;
}

.timeline-card {
  border-radius: 0;
  box-shadow: none;
  background: transparent;
  position: relative;
  z-index: 1;
}



.timeline-content {
  max-height: 400px;
  overflow-y: auto;
  scroll-behavior: smooth;
  background: transparent;
  position: relative;
  z-index: 1;
}

.timeline-content::-webkit-scrollbar {
  height: 6px;
}

.timeline-content::-webkit-scrollbar-track {
  background: rgba(148, 163, 184, 0.1);
  border-radius: 3px;
}

.timeline-content::-webkit-scrollbar-thumb {
  background: linear-gradient(90deg, rgba(102, 126, 234, 0.3), rgba(118, 75, 162, 0.3));
  border-radius: 3px;
}

.timeline-content::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(90deg, rgba(102, 126, 234, 0.5), rgba(118, 75, 162, 0.5));
}

/* 卡片通用样式 */
.form-card {
  background: white;
  border: 1px solid #e2e8f0;
  overflow: hidden;
  backdrop-filter: blur(10px);
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 2;
  height: 100%;
}

.output-card {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid #e2e8f0;
  overflow: hidden;
  backdrop-filter: blur(10px);
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 3;
  height: 100%;
}

/* 输出面板头部 */
.output-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid #e2e8f0;
  background: rgba(248, 250, 252, 0.8);
  flex-shrink: 0;
}

.output-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.view-switch {
  display: flex;
  align-items: center;
}

/* 脚本输出样式 */
.script-output {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.script-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.script-editor {
  flex: 1;
  margin: 0;
  border-radius: 0;
  overflow: hidden;
  border: none;
}

/* 紧凑表单样式 */
.compact-form {
  padding: 16px;
  background: white;
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  overflow: hidden;
}

.form-content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.content-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px;
  border: 1px solid #e2e8f0;
}

.mode-selector {
  margin-bottom: 12px;
}

.input-section {
  margin-top: 12px;
}

.input-group {
  margin-bottom: 12px;
}

.input-group:last-child {
  margin-bottom: 0;
}

.input-label {
  display: block;
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.compact-select {
  width: 100%;
}

.compact-select .el-input__inner {
  height: 32px;
  font-size: 13px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
}

.compact-select .el-input__inner:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

.content-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.content-label {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  margin: 0;
}

/* 输入框样式 */
.compact-input,
.compact-textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
  background: white;
  transition: all 0.3s ease;
}

.compact-input:focus,
.compact-textarea:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

.compact-input.invalid,
.compact-textarea.invalid {
  border-color: #f56c6c;
  background-color: #fef0f0;
}

.compact-textarea {
  resize: vertical;
  min-height: 180px;
  max-height: 480px;
  line-height: 1.6;
  font-family: inherit;
}

.description-counter {
  margin-top: 4px;
  color: #9ca3af;
  font-size: 12px;
  text-align: right;
}

.selected-info {
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border: 1px solid #bae6fd;
  border-radius: 8px;
  padding: 12px;
  margin-top: 12px;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.1);
}

.selected-info-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #bae6fd;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.info-icon {
  font-size: 16px;
  color: #059669;
}

.info-title {
  font-size: 14px;
  font-weight: 600;
  color: #0369a1;
}

.card-tags {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.selected-info-content {
  margin-top: 8px;
}

.info-card {
  background: white;
  border-radius: 6px;
  padding: 12px;
  border: 1px solid #e0f2fe;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

/* 旧的卡片头部样式已移除 */

.info-details {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
}

.detail-icon {
  font-size: 14px;
  color: #6b7280;
  margin-top: 2px;
  flex-shrink: 0;
}

.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  min-width: 60px;
  flex-shrink: 0;
}

.detail-value {
  font-size: 12px;
  color: #6b7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.4;
  flex: 1;
}

.form-actions {
  padding: 20px 0;
  text-align: center;
  margin: 0 -16px -16px -16px;
}

.create-script-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  border-radius: 12px;
  padding: 16px 32px;
  font-size: 16px;
  font-weight: 600;
  color: white;
  box-shadow: 0 4px 16px rgba(102, 126, 234, 0.3);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-width: 200px;
}

.create-script-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
  transition: left 0.5s;
}

.create-script-btn:hover:not(.executing):not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  background: linear-gradient(135deg, #5a67d8 0%, #6b46c1 100%);
}

.create-script-btn:hover:not(.executing):not(:disabled)::before {
  left: 100%;
}

.create-script-btn:active:not(.executing):not(:disabled) {
  transform: translateY(0);
  box-shadow: 0 4px 16px rgba(102, 126, 234, 0.3);
}

.create-script-btn:disabled {
  background: linear-gradient(135deg, #9ca3af 0%, #6b7280 100%);
  box-shadow: 0 2px 8px rgba(156, 163, 175, 0.2);
  transform: none;
  cursor: not-allowed;
  opacity: 0.6;
}

.create-script-btn:disabled:hover {
  transform: none;
  box-shadow: 0 2px 8px rgba(156, 163, 175, 0.2);
  background: linear-gradient(135deg, #9ca3af 0%, #6b7280 100%);
  cursor: not-allowed;
}

.create-script-btn:disabled:hover::before {
  display: none;
}

/* 停止按钮样式 */
.stop-script-btn {
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  border: none;
  border-radius: 12px;
  padding: 16px 32px;
  font-size: 16px;
  font-weight: 600;
  color: white;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.3);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-width: 200px;
}

.stop-script-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
  transition: left 0.5s;
}

.stop-script-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(239, 68, 68, 0.4);
  background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
}

.stop-script-btn:hover:not(:disabled)::before {
  left: 100%;
}

.stop-script-btn:active:not(:disabled) {
  transform: translateY(0);
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.3);
}

.stop-script-btn:disabled {
  background: linear-gradient(135deg, #9ca3af 0%, #6b7280 100%);
  box-shadow: 0 2px 8px rgba(156, 163, 175, 0.2);
  transform: none;
  cursor: not-allowed;
  opacity: 0.6;
}

.stop-script-btn:disabled:hover {
  transform: none;
  box-shadow: 0 2px 8px rgba(156, 163, 175, 0.2);
  background: linear-gradient(135deg, #9ca3af 0%, #6b7280 100%);
  cursor: not-allowed;
}

.stop-script-btn:disabled:hover::before {
  display: none;
}

.btn-icon {
  margin-right: 8px;
  font-size: 18px;
}

.executing-icon {
  animation: spin 1s linear infinite;
}

@keyframes executingPulse {
  0%, 100% {
    box-shadow: 0 4px 16px rgba(245, 158, 11, 0.3);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 6px 20px rgba(245, 158, 11, 0.5);
    transform: scale(1.02);
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* 旧的表单模式选择器样式已移除 */

/* 环境选项样式 */
.environment-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
  min-height: auto;
}

.environment-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 20px;
}

.environment-name-inline {
  font-weight: 600;
  color: #303133;
  flex-shrink: 0;
  line-height: 1.2;
}

.environment-url-inline {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  background: #f0f9eb;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #c2e7b0;
  flex-shrink: 0;
  line-height: 1.2;
}

/* 确保下拉选项有足够的高度 */
:deep(.el-select-dropdown__item) {
  height: auto !important;
  min-height: 40px;
  padding: 8px 20px;
  line-height: 1.4;
}

:deep(.el-select-dropdown__item .environment-option) {
  width: 100%;
}

/* 无环境选项样式 */
.no-environments-option {
  cursor: not-allowed !important;
}

.no-environments-content {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  color: #e6a23c;
}

.warning-icon {
  font-size: 14px;
  color: #e6a23c;
  flex-shrink: 0;
}

.no-environments-text {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #e6a23c;
  line-height: 1.2;
}

.no-environments-title {
  font-weight: 500;
}

.no-environments-desc {
  color: #909399;
}

.form-select {
  width: 100%;
}

.form-select .el-input__inner {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fafafa;
}

.form-select .el-input__inner:focus {
  border-color: #3b82f6;
  background: white;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

/* 测试用例选项样式 */
.test-case-option {
  padding: 6px 0;
}

.test-case-title {
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 3px;
  font-size: 13px;
}

.test-case-meta {
  display: flex;
  gap: 4px;
  align-items: center;
}

/* 已选择测试用例信息 */


/* 流式输出样式 */
.streaming-output {
  width: 100%;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* 输出样式 */
.cursor-style-output {
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  padding: 16px;
  border: 1px solid #333;
  min-height: 100%;
  position: relative;
}

.output-line {
  display: flex;
  align-items: flex-start;
  margin-bottom: 2px;
  position: relative;
  transition: all 0.2s ease;
}

.output-line:hover {
  background: rgba(255, 255, 255, 0.05);
}

.output-line:hover .line-actions {
  opacity: 1;
}

.line-content {
  flex: 1;
  word-break: break-word;
  white-space: pre-wrap;
}

.line-actions {
  opacity: 0;
  transition: opacity 0.2s ease;
  margin-left: 8px;
  display: flex;
  align-items: center;
}

.copy-line-btn {
  padding: 2px 4px !important;
  min-height: auto !important;
  color: #6a6a6a !important;
}

.copy-line-btn:hover {
  color: #3b82f6 !important;
  background: rgba(59, 130, 246, 0.1) !important;
}


/* 打字光标效果 */
.typing-cursor {
  display: inline-block;
  width: 2px;
  height: 1.2em;
  background: #3b82f6;
  animation: blink 1s infinite;
  margin-left: 2px;
  vertical-align: text-bottom;
}




/* 按钮样式 */

/* 横向时间线样式 */
.horizontal-timeline {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 24px 0;
  overflow-x: auto;
  scroll-behavior: smooth;
  justify-content: center;
}

.timeline-item {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.timeline-connector {
  width: 40px;
  height: 2px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.4), rgba(148, 163, 184, 0.2));
  margin: 0 8px;
  position: relative;
  flex-shrink: 0;
  transition: all 0.3s ease;
  border-radius: 1px;
}

.timeline-connector.active {
  background: linear-gradient(90deg, rgba(102, 126, 234, 0.6), rgba(118, 75, 162, 0.6));
  box-shadow: 0 0 8px rgba(102, 126, 234, 0.2);
}

.timeline-connector::after {
  content: '';
  position: absolute;
  right: -3px;
  top: -2px;
  width: 6px;
  height: 6px;
  background: rgba(148, 163, 184, 0.5);
  border-radius: 50%;
  transition: all 0.3s ease;
}

.timeline-connector.active::after {
  background: rgba(102, 126, 234, 0.8);
  box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.1), 0 0 0 3px rgba(102, 126, 234, 0.05);
}

/* 时间线节点样式 */
.timeline-node {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.9) 100%);
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(148, 163, 184, 0.2);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-width: 140px;
  max-width: 180px;
  text-align: center;
  backdrop-filter: blur(10px);
}

.timeline-node::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #667eea, #764ba2);
  opacity: 0;
  transition: opacity 0.3s ease;
}

.timeline-node:hover {
  box-shadow: 0 6px 25px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
  border-color: rgba(102, 126, 234, 0.3);
}

.timeline-node:hover::before {
  opacity: 0.7;
}

/* 当前执行节点高亮样式 */
.timeline-node.current-node {
  border-color: rgba(102, 126, 234, 0.5);
  box-shadow: 0 6px 30px rgba(102, 126, 234, 0.2);
  transform: scale(1.05);
  animation: currentNodePulse 2s infinite ease-in-out;
}

.timeline-node.current-node::before {
  opacity: 1;
  background: linear-gradient(90deg, #667eea, #764ba2);
  height: 4px;
}

.timeline-node.current-node .node-number {
  background: linear-gradient(135deg, #667eea, #764ba2);
  box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
  animation: currentNodePulse 2s infinite ease-in-out;
}

.timeline-node.current-node .node-name {
  color: #667eea;
  font-weight: 700;
}

/* 已完成节点样式 */
.timeline-node:has(.node-status .el-tag--success) {
  border-color: rgba(34, 197, 94, 0.6);
  background: linear-gradient(135deg, rgba(34, 197, 94, 0.1) 0%, rgba(34, 197, 94, 0.05) 100%);
}

.timeline-node:has(.node-status .el-tag--success) .node-number {
  background: linear-gradient(135deg, #22c55e, #16a34a);
}

/* 失败节点样式 */
.timeline-node:has(.node-status .el-tag--danger) {
  border-color: rgba(239, 68, 68, 0.6);
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(239, 68, 68, 0.05) 100%);
}

.timeline-node:has(.node-status .el-tag--danger) .node-number {
  background: linear-gradient(135deg, #ef4444, #dc2626);
}

.node-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.node-number-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}

.node-number-row {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: center;
}

.node-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: white;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
}

.node-name {
  font-weight: 600;
  color: #374151;
  font-size: 14px;
  line-height: 1.3;
  text-align: center;
  word-break: break-word;
}

.node-status {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.current-loading {
  font-size: 18px;
  color: #667eea;
  animation: rotate 1.5s linear infinite;
}

.node-duration {
  color: #6b7280;
  font-size: 10px;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
  background-color: rgba(255, 255, 255, 0.8);
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  white-space: nowrap;
  line-height: 1.2;
  font-weight: 500;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

@keyframes currentNodePulse {
  0%,
  100% {
    box-shadow: 0 6px 30px rgba(102, 126, 234, 0.2);
  }
  50% {
    box-shadow: 0 8px 35px rgba(102, 126, 234, 0.3);
  }
}

@keyframes blink {
  0%, 50% {
    opacity: 1;
  }
  51%, 100% {
    opacity: 0;
  }
}


/* 响应式设计 */
@media (max-width: 768px) {
  .content-section {
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }

  .page-header {
    padding: 16px 20px;
  }

  .header-content {
    flex-direction: column;
    text-align: center;
  }

  .timeline-node {
    min-width: 120px;
    max-width: 140px;
    padding: 10px 12px;
  }

  .timeline-connector {
    width: 30px;
    margin: 0 6px;
  }
}

@media (max-width: 480px) {
  .page-header {
    padding: 12px 16px;
  }

  .timeline-node {
    min-width: 100px;
    max-width: 120px;
    padding: 8px 10px;
  }

  .timeline-connector {
    width: 20px;
    margin: 0 4px;
  }

  .node-number {
    width: 16px;
    height: 16px;
    font-size: 10px;
  }

  .node-name {
    font-size: 11px;
  }
}
</style>
