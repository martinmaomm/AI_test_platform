<template>
  <div class="scenario-generator">

    <!-- 主要内容区域 -->
    <div v-if="selectedProject" class="main-container">
      <!-- 聊天和时间线容器区域 -->
      <div class="chat-timeline-container">
        <!-- 聊天容器 -->
        <div class="chat-container">
          <!-- 聊天头部 -->
          <div class="chat-header">
            <div class="header-content">
              <div class="header-subtitle">
                <el-tag v-if="isWebSocketConnected" type="success" size="small">已连接</el-tag>
                <el-tag v-else type="danger" size="small">未连接</el-tag>
              </div>
              <div class="header-title">
                <span v-if="currentStep">当前智能体:{{ getStepDisplayName(currentStep) }}</span>
                <span v-else>AI智能体助手</span>
              </div>

            </div>
            <div class="header-actions">
              <el-button size="small" @click="clearChat" :disabled="chatMessages.length === 0">
                <el-icon>
                  <Delete />
                </el-icon>
                清空对话
              </el-button>
              <el-button size="small" @click="exportChat" :disabled="chatMessages.length === 0">
                <el-icon>
                  <Download />
                </el-icon>
                导出对话
              </el-button>
            </div>
          </div>

          <!-- 聊天消息区域 -->
          <div class="chat-messages" ref="chatMessagesRef">
            <!-- 欢迎消息 -->
            <div v-if="chatMessages.length === 0" class="welcome-message">
              <div class="welcome-content">
                <div class="welcome-icon">
                  <img src="@/assert/icons/robot.svg" alt="Robot" class="robot-icon" />
                </div>
                <h3>欢迎使用API智能体场景生成助手</h3>
                <p>请描述您的业务场景，我将帮您生成完整的和业务映射的场景测试用例。</p>
                <div class="welcome-examples">
                  <h4>示例场景：</h4>
                  <ul>
                    <li>用户注册、登录、搜索商品、加入购物车的完整流程</li>
                    <li>订单创建、支付、发货、收货的电商流程</li>
                    <li>用户管理、权限分配、角色管理的后台流程</li>
                  </ul>
                </div>
              </div>
            </div>

            <!-- 聊天消息列表 -->
            <div v-for="(message, index) in chatMessages" :key="index" class="message-item" :class="message.role">
              <div class="message-avatar">
                <div v-if="message.role === 'user'" class="user-avatar">👤</div>
                <div v-else class="ai-avatar">
                  <img src="@/assert/icons/robot.svg" alt="AI" class="robot-icon" />
                </div>
              </div>
              <div class="message-content">
                <div class="message-header">
                  <span class="message-role">{{ message.role === 'user' ? '用户' : 'AI助手' }}</span>
                  <span class="message-time">{{ formatTimestamp(message.timestamp) }}</span>
                </div>
                <div class="message-text" v-html="formatMessageContent(message.content)"></div>


                <!-- 消息状态 -->
                <div v-if="message.status" class="message-status">
                  <el-tag :type="getStatusType(message.status)" size="small">
                    {{ getStatusText(message.status) }}
                  </el-tag>
                </div>


                <!-- 结果展示 -->
                <div v-if="message.result" class="message-result">
                  <div v-if="message.result.scenario_plan" class="result-section">
                    <h4>📝 场景计划</h4>
                    <div class="scenario-plan">
                      <p class="plan-overview">
                        <span>概述:</span> {{ message.result.scenario_plan.scenario_overview }}
                      </p>
                    </div>
                  </div>
                  <div v-if="message.result.business_steps" class="result-section">
                    <h4>🎯 业务步骤</h4>
                    <div class="business-steps">
                      <div v-for="(step, stepIndex) in message.result.business_steps" :key="stepIndex"
                        class="step-item">
                        <div class="step-header">
                          <span class="step-number">{{ stepIndex + 1 }}</span>
                          <span class="step-name">{{ step.step_name }}</span>
                        </div>
                        <p class="step-description">{{ step.step_description }}</p>
                        <div class="step-meta">
                          <el-tag size="small" type="info">{{ step.business_role }}</el-tag>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>

        <!-- 时间线容器 -->
        <div class="timeline-container">
          <div class="timeline-header">
            <el-icon class="timeline-icon">
              <Clock />
            </el-icon>
            <span>智能体执行时间线</span>
          </div>
          <div class="timeline-content" ref="timelineContentRef">
            <el-timeline>
              <el-timeline-item v-for="(node, index) in timelineNodes" :key="node.id"
                :timestamp="formatNodeTime(node.timestamp)" :type="getNodeTimelineType(node.status)"
                :icon="getNodeIcon(node.status)" placement="top">
                <div class="timeline-node" :class="{ 'current-node': node.status === 'executing' }">
                  <div class="node-header">
                    <span class="node-number">{{ index + 1 }}</span>
                    <span class="node-name">智能体:{{ node.displayName }}</span>
                    <div class="node-status">
                      <el-tag :type="getNodeStatusType(node.status)" size="small">
                        {{ getNodeStatusText(node.status) }}
                      </el-tag>
                      <el-icon v-if="node.status === 'executing'" class="current-loading">
                        <Loading />
                      </el-icon>
                    </div>
                  </div>
                  <div class="node-description">{{ node.description }}</div>
                  <div v-if="node.duration" class="node-duration">
                    耗时: {{ formatDuration(node.duration) }}
                  </div>
                </div>
              </el-timeline-item>
            </el-timeline>

            <!-- 空状态 -->
            <div v-if="timelineNodes.length === 0 && !isGenerating" class="timeline-empty">
              <div class="empty-state">
                <el-icon class="empty-icon">
                  <Clock />
                </el-icon>
                <p>等待智能体开始执行...</p>
              </div>
            </div>

            <!-- 加载状态 -->
            <div v-if="timelineNodes.length === 0 && isGenerating" class="timeline-empty">
              <div class="loading-animation">
                <div class="loading-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
                <p>智能体正在启动中...</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input">
        <div class="input-container">
          <!-- 输入框区域 -->
          <div class="input-wrapper">
            <div class="input-with-button">
              <!-- 输入框（自适应高度） -->
              <el-input v-model="scenarioDescription" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }"
                placeholder="请详细描述您的业务场景，例如：一个新注册的用户，登录后搜索商品，并将商品加入购物车..." show-word-limit :disabled="isGenerating"
                @keydown.ctrl.enter="generateScenarioLocal" class="scenario-input" resize="none"
                :style="{ paddingRight: scenarioDescription.trim().length > 0 ? '60px' : '20px' }" />

              <!-- 发送图标 -->
              <div v-show="scenarioDescription.trim().length > 0" @click="generateScenarioLocal" class="send-icon"
                :class="{ 'loading': isGenerating, 'disabled': isGenerating }">
                <el-icon v-if="!isGenerating">
                  <Top />
                </el-icon>
                <el-icon v-else class="rotating">
                  <Loading />
                </el-icon>
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

    <!-- 错误提示 -->
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon closable @close="errorMessage = ''"
      class="error-alert" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, Download, Delete, Loading, InfoFilled, ArrowUp, Top, Promotion, Clock } from '@element-plus/icons-vue'
import { generateScenario } from '@/api/apiTesting'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { WebSocketManager, WebSocketMessageHandler } from '@/config/websocket'

// 使用项目状态管理
const projectStore = useProjectStore()
const authStore = useAuthStore()
const router = useRouter()

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 响应式数据
const scenarioDescription = ref('')
const isGenerating = ref(false)
const errorMessage = ref('')
const generationProgress = ref(0)
const currentStep = ref('')
const currentTaskId = ref(null)
const chatMessagesRef = ref(null)
const timelineContentRef = ref(null)

// WebSocket相关状态
const websocketManager = ref(null)
const messageHandler = ref(null)
const isWebSocketConnected = ref(false)

// 聊天消息
const chatMessages = ref([])

// 时间线节点
const timelineNodes = ref([])

// 计算属性
const canGenerate = computed(() => {
  return projectStore.currentProject &&
    scenarioDescription.value.trim().length >= 10 &&
    !isGenerating.value
})



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

// 生命周期
onMounted(async () => {
  // 只有在用户已认证时才初始化WebSocket
  if (authStore.isAuthenticated) {
    initWebSocket()
  }
})

onUnmounted(() => {
  closeWebSocket()
})

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 统一错误处理
const handleError = (error, defaultMessage = '操作失败') => {
  const message = error?.message || error || defaultMessage
  ElMessage.error(message)
  return message
}


// 添加聊天消息
const addChatMessage = (role, content, result = null, status = null) => {
  const message = {
    role,
    content,
    timestamp: new Date(),
    result,
    status
  }
  chatMessages.value.push(message)
  
  // 滚动到底部
  nextTick(() => {
    scrollToBottom()
  })
}

// WebSocket管理
const initWebSocket = () => {
  const token = authStore.accessToken
  if (!token) {
    addChatMessage('ai', '🔌 未找到认证token，请先登录')
    return
  }

  // 创建WebSocket管理器和消息处理器
  websocketManager.value = new WebSocketManager()
  messageHandler.value = new WebSocketMessageHandler()

  // 注册消息处理器
  messageHandler.value.registerHandler('streaming_output', (data, ctx) => {
    handleStreamingOutput(data)
  })

  messageHandler.value.registerHandler('streaming_complete', (data, ctx) => {
    handleStreamingComplete(data.step)
  })

  messageHandler.value.registerHandler('task_status', (data, ctx) => {
    handleTaskStatusUpdate(data)
  })

  messageHandler.value.registerHandler('task_completed', (data, ctx) => {
    handleTaskCompleted(data)
  })

  messageHandler.value.registerHandler('task_failed', (data, ctx) => {
    handleTaskFailed(data)
  })

  messageHandler.value.registerHandler('node_start', (data, ctx) => {
    handleNodeStart(data)
  })

  // 创建WebSocket配置
  const config = {
    onOpen: () => {
      isWebSocketConnected.value = true
    },
    onMessage: (event) => {
      messageHandler.value.handleMessage(event, {})
    },
    onClose: (event) => {
      isWebSocketConnected.value = false
    },
    onError: (error) => {
      isWebSocketConnected.value = false
      console.error('WebSocket连接错误:', error)
    },
    autoReconnect: true
  }

  websocketManager.value.initWebSocket('/ws/scenario_generation-streaming/', token, config)
}

const closeWebSocket = () => {
  if (websocketManager.value) {
    websocketManager.value.closeWebSocket()
    websocketManager.value = null
  }
  messageHandler.value = null
  isWebSocketConnected.value = false
}

// 处理流式输出
const handleStreamingOutput = (data) => {
  const content = data.content || ''
  const step = data.step || ''
  
  if (!content) return
  
  // 查找或创建当前步骤的消息
  let currentMessage = chatMessages.value.find(msg =>
    msg.role === 'ai' && msg.currentStep === step
  )

  if (!currentMessage) {
    currentMessage = {
      role: 'ai',
      timestamp: new Date(),
      content: '',
      isStreaming: true,
      currentStep: step,
      stepName: getStepDisplayName(step)
    }
    chatMessages.value.push(currentMessage)
  } else {
    // 如果消息已存在，确保isStreaming为true
    currentMessage.isStreaming = true
  }

  // 更新对应的节点状态为执行中
  updateNodeExecutionStatus(step, true)

  // 更新内容
  currentMessage.content += content

  // 自动滚动到底部
  scrollToBottom()

  // 设置自动隐藏思考指示器的定时器
  if (currentMessage.streamingTimer) {
    clearTimeout(currentMessage.streamingTimer)
  }

  // 如果3秒内没有新的流式输出，自动隐藏思考指示器
  currentMessage.streamingTimer = setTimeout(() => {
    currentMessage.isStreaming = false
    currentMessage.streamingTimer = null
  }, 3000)
}

// 处理流式输出完成
const handleStreamingComplete = (step) => {
  const currentMessage = chatMessages.value.find(msg =>
    msg.role === 'ai' && msg.currentStep === step
  )

  if (currentMessage) {
    // 清除定时器
    if (currentMessage.streamingTimer) {
      clearTimeout(currentMessage.streamingTimer)
      currentMessage.streamingTimer = null
    }
    // 隐藏思考指示器
    currentMessage.isStreaming = false
  }

  // 更新对应的节点状态为已完成
  updateNodeExecutionStatus(step, false)
}

// 处理任务状态更新
const handleTaskStatusUpdate = (data) => {
  if (data.status) {
    currentStep.value = data.status
  }
  if (data.progress !== undefined) {
    generationProgress.value = data.progress
  }
  if (data.message) {
    addChatMessage('ai', data.message)
  }
}

// 处理任务完成
const handleTaskCompleted = (data) => {
  // 结束加载状态
  isGenerating.value = false
  generationProgress.value = 100

  // 隐藏所有流式输出指示器
  hideAllStreamingIndicators()

  // 将所有执行中的节点标记为已完成
  timelineNodes.value.forEach(node => {
    if (node.status === 'executing') {
      node.status = 'completed'
      node.endTime = new Date()
      node.duration = node.endTime - node.startTime
    }
  })

  // 处理任务结果
  if (data.result) {
    // 显示完成消息和结果，使用后端返回的消息
    const successMessage = data.message || '🎉 场景生成完成！'
    addChatMessage('ai', successMessage, data.result, 'success')
    ElMessage.success(data.message || '场景生成成功！')
  } else {
    // 任务完成但结果异常
    const warningMessage = data.message || '⚠️ 场景生成完成，但结果已过期，请重新生成'
    addChatMessage('ai', warningMessage)
    ElMessage.warning(data.message || '场景生成完成，但结果已过期，请重新生成')
  }
}

// 处理任务失败
const handleTaskFailed = (data) => {
  // 结束加载状态
  isGenerating.value = false

  // 隐藏所有流式输出指示器
  hideAllStreamingIndicators()

  // 将所有执行中的节点标记为失败
  timelineNodes.value.forEach(node => {
    if (node.status === 'executing') {
      node.status = 'failed'
      node.endTime = new Date()
      node.duration = node.endTime - node.startTime
    }
  })

  // 显示错误消息，优先使用后端返回的错误信息
  const errorMsg = data.error || data.message || '场景生成失败'
  addChatMessage('ai', `❌ 任务执行失败: ${errorMsg}`, null, 'error')
  errorMessage.value = handleError(errorMsg)
}

// 处理节点开始执行
const handleNodeStart = (data) => {
  const nodeDisplayName = data.node_display_name || data.node_name || '未知节点'
  const nodeName = data.node_name || 'unknown'
  
  // 直接从后端获取节点描述，如果没有则使用默认值
  const nodeDescription = data.node_description || '正在执行节点操作...'

  // 添加到时间线
  addTimelineNode(nodeName, nodeDisplayName, nodeDescription, 'executing')

  // 更新当前步骤
  currentStep.value = nodeName
}


// 隐藏所有流式输出指示器
const hideAllStreamingIndicators = () => {
  let count = 0
  chatMessages.value.forEach(msg => {
    if (msg.isStreaming) {
      if (msg.streamingTimer) {
        clearTimeout(msg.streamingTimer)
        msg.streamingTimer = null
      }
      msg.isStreaming = false
      count++
    }
  })
}

// 滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    if (chatMessagesRef.value) {
      chatMessagesRef.value.scrollTop = chatMessagesRef.value.scrollHeight
    }
  })
}

// 从时间线节点获取后端返回的显示名称
const getStepDisplayName = (step) => {
  const node = timelineNodes.value.find(n => n.nodeName === step)
  return node?.displayName || ''
}

// 更新节点执行状态
const updateNodeExecutionStatus = (nodeName, isExecuting) => {
  // 查找对应的节点消息
  const nodeMessage = chatMessages.value.find(msg =>
    msg.nodeInfo && msg.nodeInfo.nodeName === nodeName
  )

  if (nodeMessage && nodeMessage.nodeInfo) {
    nodeMessage.nodeInfo.isExecuting = isExecuting
  }

  // 更新时间线节点状态
  updateTimelineNode(nodeName, isExecuting ? 'executing' : 'completed')
}


// 滚动到时间线底部
const scrollTimelineToBottom = () => {
  nextTick(() => {
    if (timelineContentRef.value) {
      timelineContentRef.value.scrollTop = timelineContentRef.value.scrollHeight
    }
  })
}

// 清空聊天
const clearChat = () => {
  ElMessageBox.confirm('确定要清空所有对话记录和时间线吗？', '确认清空', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    chatMessages.value = []
    clearTimeline()
    ElMessage.success('对话记录和时间线已清空')
  })
}

// 导出聊天记录
const exportChat = () => {
  if (chatMessages.value.length === 0) return

  const chatText = chatMessages.value.map(msg => {
    const timestamp = formatTimestamp(msg.timestamp)
    const role = msg.role === 'user' ? '用户' : 'AI助手'
    const content = msg.content
    const result = msg.result ? `\n结果: ${JSON.stringify(msg.result, null, 2)}` : ''
    return `[${timestamp}] ${role}: ${content}${result}`
  }).join('\n\n')

  const blob = new Blob([chatText], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `ai-scenario-chat-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)

  ElMessage.success('聊天记录导出成功')
}

// 格式化消息内容
const formatMessageContent = (content) => {
  if (!content) return ''

  // 将换行符转换为HTML换行
  return content.replace(/\n/g, '<br>')
}

// 状态映射
const statusMap = {
  success: { type: 'success', text: '成功' },
  error: { type: 'danger', text: '失败' },
  warning: { type: 'warning', text: '警告' },
  info: { type: 'info', text: '信息' }
}

// 节点状态映射
const nodeStatusMap = {
  pending: { type: 'info', text: '等待中', timelineType: 'info', icon: 'Clock' },
  executing: { type: 'primary', text: '执行中', timelineType: 'primary', icon: 'Loading' },
  completed: { type: 'success', text: '已完成', timelineType: 'success', icon: 'Check' },
  failed: { type: 'danger', text: '失败', timelineType: 'danger', icon: 'Close' }
}

const getStatusType = (status) => {
  return statusMap[status]?.type || 'info'
}

const getStatusText = (status) => {
  return statusMap[status]?.text || status
}

// 格式化时间戳
const formatTimestamp = (timestamp) => {
  return timestamp.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

// 生成场景
const generateScenarioLocal = async () => {
  // 检查是否有文字内容和项目选择
  if (!scenarioDescription.value.trim() || !projectStore.currentProject) return

  try {
    // 添加用户消息
    addChatMessage('user', scenarioDescription.value.trim())

    isGenerating.value = true
    generationProgress.value = 0
    errorMessage.value = ''
    currentTaskId.value = null

    // 添加AI开始消息
    addChatMessage('ai', '🚀 开始分析您的业务场景，请稍候...')

    // 清空时间线
    clearTimeline()

    // 隐藏之前的流式输出指示器
    hideAllStreamingIndicators()

    // 检查WebSocket连接状态
    if (!isWebSocketConnected.value) {
      addChatMessage('ai', '⚠️ WebSocket未连接，将无法显示实时输出')
      initWebSocket()
    }

    // 调用API启动异步任务
    const response = await generateScenario(projectStore.currentProjectId, {
      user_request: scenarioDescription.value.trim()
    })

    if (response.success && response.data?.task_id) {
      currentTaskId.value = response.data.task_id

      // 处理初始状态信息
      if (response.data.status) {
        currentStep.value = response.data.status
      }
      if (response.data.progress !== undefined) {
        generationProgress.value = response.data.progress
      }

      ElMessage.success('场景生成任务已启动，正在处理中...')

      // 注意：不再使用轮询，完全依赖WebSocket接收状态更新和结果
    } else {
      const errorMsg = response.message || '启动场景生成任务失败'
      addChatMessage('ai', `❌ ${errorMsg}`)
      errorMessage.value = handleError(errorMsg)
      // 启动失败时结束加载状态
      isGenerating.value = false
    }

  } catch (error) {
    const errorMsg = error.message || '启动场景生成任务时发生错误'
    addChatMessage('ai', `❌ ${errorMsg}`)
    errorMessage.value = handleError(errorMsg)
    // 发生错误时结束加载状态
    isGenerating.value = false
    setTimeout(() => {
      generationProgress.value = 0
    }, 2000)
  }
  // 注意：成功启动任务后，isGenerating 状态由 WebSocket 消息管理
}

// 注意：已移除轮询逻辑，完全依赖WebSocket接收任务状态和结果

const getRelevanceTagType = (score) => {
  if (score >= 0.8) return 'success'
  if (score >= 0.6) return 'warning'
  return 'info'
}

// 时间线相关方法
const addTimelineNode = (nodeName, displayName, description, status = 'pending') => {
  // 如果新节点开始执行，将之前所有执行中的节点标记为已完成
  if (status === 'executing') {
    timelineNodes.value.forEach(n => {
      if (n.status === 'executing') {
        n.status = 'completed'
        n.endTime = new Date()
        n.duration = n.endTime - n.startTime
      }
    })
  }

  const node = {
    id: `${nodeName}_${Date.now()}`,
    nodeName,
    displayName,
    description,
    status,
    timestamp: new Date(),
    startTime: new Date(),
    endTime: null,
    duration: null
  }

  timelineNodes.value.push(node)
  scrollTimelineToBottom()
  return node
}

const updateTimelineNode = (nodeName, status, description = null) => {
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
    }

    node.status = status
    if (description) {
      node.description = description
    }

    if (status === 'completed' || status === 'failed') {
      node.endTime = new Date()
      node.duration = node.endTime - node.startTime
    }

    scrollTimelineToBottom()
  }
}

const clearTimeline = () => {
  timelineNodes.value = []
}

// 格式化节点时间
const formatNodeTime = (timestamp) => {
  return timestamp.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

// 格式化持续时间
const formatDuration = (duration) => {
  const seconds = Math.floor(duration / 1000)
  const milliseconds = duration % 1000

  if (seconds > 0) {
    return `${seconds}.${Math.floor(milliseconds / 100)}s`
  } else {
    return `${milliseconds}ms`
  }
}

// 获取节点时间线类型
const getNodeTimelineType = (status) => {
  return nodeStatusMap[status]?.timelineType || 'info'
}

// 获取节点图标
const getNodeIcon = (status) => {
  return nodeStatusMap[status]?.icon || 'Clock'
}

// 获取节点状态类型
const getNodeStatusType = (status) => {
  return nodeStatusMap[status]?.type || 'info'
}

// 获取节点状态文本
const getNodeStatusText = (status) => {
  return nodeStatusMap[status]?.text || status
}

</script>

<style scoped>
.scenario-generator {
  height: calc(100vh - 80px);
  background-color: #f5f7fa;
  display: flex;
  flex-direction: column;
}

.no-project-warning {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  max-width: 500px;
}

/* 主容器 */
.main-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  height: calc(100vh - 80px);
  gap: 0;
}

/* 聊天和时间线容器区域 */
.chat-timeline-container {
  flex: 1;
  display: flex;
  gap: 0;
  min-height: 0;
  margin-bottom: 0;
}

.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border-right: 1px solid #e4e7ed;
  overflow: hidden;
  position: relative;
}

.timeline-container {
  width: 400px;
  background-color: #ffffff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 聊天头部 */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e4e7ed;
  background-color: #ffffff;
  position: sticky;
  top: 0;
  z-index: 10;
  height: 56px;
  box-sizing: border-box;
  /* 确保sticky定位正确工作 */
  background-clip: padding-box;
}

.header-actions {
  min-width: 120px;
}

.header-content {
  display: flex;
  flex-direction: row;
  gap: 8px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.header-icon {
  font-size: 20px;
  color: #409eff;
}

.header-subtitle {
  display: flex;
  gap: 8px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

/* 聊天消息区域 */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background-color: #ffffff;
  /* 确保滚动内容不会超出header边界 */
  scroll-padding-top: 0;
  /* 设置滚动容器的边界 */
  scroll-behavior: smooth;
  /* 防止内容超出容器边界 */
  position: relative;
  /* 设置滚动容器的边界，确保不会超出header */
  scroll-snap-type: y proximity;
  /* 确保滚动内容完全在容器内 */
  box-sizing: border-box;
}



.welcome-message {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 300px;
}

.welcome-content {
  text-align: center;
  max-width: 600px;
}

.welcome-icon {
  font-size: 64px;
  margin-bottom: 10px;
  display: flex;
  justify-content: center;
  align-items: center;
}

.robot-icon {
  width: 64px;
  height: 64px;
  filter: drop-shadow(0 2px 8px rgba(102, 126, 234, 0.3));
}

.welcome-content h3 {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 16px;
}

.welcome-content p {
  font-size: 14px;
  color: #606266;
  margin-bottom: 24px;
  line-height: 1.6;
}

.welcome-examples {
  text-align: left;
  background-color: #f8f9fa;
  padding: 20px;
  border-radius: 12px;
  border: 1px solid #e4e7ed;
}

.welcome-examples h4 {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
}

.welcome-examples ul {
  margin: 0;
  padding-left: 20px;
}

.welcome-examples li {
  color: #606266;
  margin-bottom: 8px;
  line-height: 1.5;
  font-size: 13px;
}

/* 消息项 */
.message-item {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
  animation: messageSlideIn 0.3s ease-out;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
}

.user-avatar,
.ai-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.user-avatar {
  background-color: #409eff;
  color: white;
}

.ai-avatar {
  background-color: #67c23a;
  color: white;
  overflow: hidden;
}

.ai-avatar .robot-icon {
  width: 24px;
  height: 24px;
  filter: brightness(0) invert(1);
}

.message-content {
  flex: 1;
  max-width: calc(100% - 112px);
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.message-role {
  font-weight: 600;
  color: #303133;
  font-size: 14px;
}

.message-time {
  font-size: 12px;
  color: #909399;
}

.message-text {
  background-color: #ffffff;
  font-size: 13px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-item.user .message-text {
  background-color: #409eff;
  color: white;
  border-color: #409eff;
}


/* 消息状态 */
.message-status {
  margin-top: 12px;
}




/* 结果展示 */
.message-result {
  margin-top: 16px;
}

.result-section {
  margin-bottom: 20px;
}

.result-section h4 {
  margin: 0 0 16px 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.business-steps {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-item {
  padding: 16px;
  background-color: #f8f9fa;
  border-radius: 8px;
  border-left: 4px solid #409eff;
}

.step-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  background-color: #409eff;
  color: white;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  margin-right: 8px;
}

.step-name {
  font-weight: 500;
  font-size: 14px;
  color: #303133;
}

.step-description {
  margin: 8px 0;
  color: #606266;
  line-height: 1.5;
  font-size: 13px;
}

.step-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-order {
  font-size: 12px;
  color: #909399;
}

.mapped-apis {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.api-item {
  padding: 12px;
  background-color: #f8f9fa;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}

.api-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.api-name {
  font-weight: 500;
  color: #303133;
}

.api-step {
  margin: 0;
  font-size: 12px;
  color: #909399;
}

.scenario-plan {
  padding: 16px;
  background-color: #f8f9fa;
  border-radius: 8px;
}

.plan-overview,
.plan-steps {
  margin: 8px 0;
  color: #606266;
  line-height: 1.5;
  font-size: 14px;
}

/* 时间线容器样式已在上面定义 */

.timeline-header {
  padding: 20px 24px;
  background-color: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #303133;
  font-size: 14px;
  height: 56px;
  box-sizing: border-box;
}

.timeline-icon {
  font-size: 16px;
  color: #409eff;
}

.timeline-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  scroll-behavior: smooth;
}

.timeline-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: #909399;
  text-align: center;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.empty-icon {
  font-size: 48px;
  color: #c0c4cc;
  opacity: 0.6;
}

.loading-animation {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.loading-dots {
  display: flex;
  gap: 6px;
}

.loading-dots span {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: linear-gradient(135deg, #409eff, #67c23a);
  animation: timelineLoading 1.4s infinite ease-in-out;
  box-shadow: 0 2px 4px rgba(64, 158, 255, 0.3);
}

.loading-dots span:nth-child(1) {
  animation-delay: -0.32s;
}

.loading-dots span:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes timelineLoading {

  0%,
  80%,
  100% {
    transform: scale(0.8);
    opacity: 0.6;
  }

  40% {
    transform: scale(1.3);
    opacity: 1;
  }
}

.timeline-empty p {
  margin: 0;
  font-size: 14px;
  color: #606266;
  font-weight: 500;
  animation: textPulse 2s infinite ease-in-out;
}

@keyframes textPulse {

  0%,
  100% {
    opacity: 0.8;
  }

  50% {
    opacity: 1;
  }
}

/* 时间线节点样式 */
.timeline-node {
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border: 1px solid #e4e7ed;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.timeline-node::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #409eff, #67c23a);
  opacity: 0;
  transition: opacity 0.3s ease;
}

.timeline-node:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
  border-color: #409eff;
}

.timeline-node:hover::before {
  opacity: 1;
}

/* 当前执行节点高亮样式 */
.timeline-node.current-node {
  /* background: linear-gradient(135deg, #e6f7ff 0%, #f0f9ff 100%); */
  border-color: #409eff;
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.2);
  transform: scale(1.02);
  animation: currentNodePulse 2s infinite ease-in-out;
}

.timeline-node.current-node::before {
  opacity: 1;
  background: linear-gradient(90deg, #409eff, #67c23a);
  height: 4px;
}

.timeline-node.current-node .node-number {
  background: linear-gradient(135deg, #409eff, #67c23a);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
  animation: currentNodePulse 2s infinite ease-in-out;
}

.timeline-node.current-node .node-name {
  color: #409eff;
  font-weight: 700;
}

.timeline-node.current-node .node-description {
  background-color: rgba(64, 158, 255, 0.1);
  border-left-color: #409eff;
}

.node-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.node-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  color: white;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  margin-right: 12px;
  flex-shrink: 0;
}

.node-name {
  font-weight: 600;
  color: #303133;
  font-size: 15px;
  flex: 1;
  margin-right: 12px;
  line-height: 1.4;
}

.node-status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.current-loading {
  font-size: 16px;
  color: #409eff;
  animation: rotate 1.5s linear infinite;
}

.node-description {
  color: #606266;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 8px;
  padding: 8px 12px;
  background-color: rgba(64, 158, 255, 0.05);
  border-radius: 6px;
  border-left: 3px solid #409eff;
}

.node-duration {
  color: #909399;
  font-size: 12px;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
  background-color: #f5f7fa;
  padding: 4px 8px;
  border-radius: 4px;
  display: inline-block;
}

/* 时间线自定义样式 */
.timeline-content :deep(.el-timeline) {
  padding: 0;
}

.timeline-content :deep(.el-timeline-item__timestamp) {
  font-size: 11px;
  color: #909399;
  font-family: monospace;
}

.timeline-content :deep(.el-timeline-item__node) {
  width: 12px;
  height: 12px;
}

.timeline-content :deep(.el-timeline-item__tail) {
  left: 5px;
  border-left: 2px solid #e4e7ed;
}

.timeline-content :deep(.el-timeline-item__content) {
  padding-left: 20px;
}

/* 输入区域 */
.chat-input {
  padding: 16px;
  background-color: #ffffff;
  border-top: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.input-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 输入框包装器 */
.input-wrapper {
  position: relative;
  display: flex;
  align-items: stretch;
}

/* 输入框和按钮容器 */
.input-with-button {
  position: relative;
  display: flex;
  align-items: center;
  flex: 1;
  border-radius: 25px;
  border: 1px solid #d1d1d2;
  background-color: #ffffff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;
  overflow: hidden;
  min-height: 48px;
}

.input-with-button:focus-within {
  border-color: #409eff;
  box-shadow: 0 4px 20px rgba(64, 158, 255, 0.15);
  transform: translateY(-1px);
}

/* .input-with-button:focus-within {
  border-color: #409eff;
  box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.1);
} */

/* 场景输入框 */
.scenario-input {
  flex: 1;
}

.scenario-input :deep(.el-textarea__inner) {
  border: none;
  border-radius: 0;
  padding: 16px 20px;
  padding-right: 60px;
  font-size: 14px;
  line-height: 1.6;
  resize: none;
  transition: all 0.3s ease;
  background: transparent;
  box-shadow: none;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  /* 隐藏滚动条但保持滚动功能 */
  scrollbar-width: none;
  /* Firefox */
  -ms-overflow-style: none;
  /* IE and Edge */
}

.scenario-input :deep(.el-textarea__inner)::-webkit-scrollbar {
  display: none;
  /* Chrome, Safari, Opera */
}

.scenario-input :deep(.el-textarea__inner):focus {
  outline: none;
  box-shadow: none;
}

.scenario-input :deep(.el-textarea__inner)::placeholder {
  color: #a8abb2;
}

/* .scenario-input :deep(.el-textarea__inner):disabled {
  background-color: #f5f7fa;
  color: #c0c4cc;
  cursor: not-allowed;
} */

/* 发送图标样式 */
.send-icon {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #409eff 0%, #337ecc 100%);
  box-shadow: 0 3px 10px rgba(64, 158, 255, 0.3);
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  color: white;
  font-size: 16px;
  border: none;
}

.send-icon:hover:not(.disabled) {
  background: linear-gradient(135deg, #337ecc 0%, #2b6cb0 100%);
  transform: scale(1.1) translateY(-1px);
  box-shadow: 0 6px 20px rgba(64, 158, 255, 0.4);
}

.send-icon:active:not(.disabled) {
  transform: scale(1.05) translateY(0);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
}

.send-icon.disabled {
  cursor: not-allowed;
  opacity: 0.5;
  background: #c0c4cc;
  box-shadow: none;
}

.send-icon .rotating {
  animation: rotate 1.5s linear infinite;
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
    box-shadow: 0 4px 16px rgba(64, 158, 255, 0.2);
  }

  50% {
    box-shadow: 0 6px 20px rgba(64, 158, 255, 0.3);
  }
}

/* 输入框聚焦动画 */
.scenario-input :deep(.el-textarea__inner):focus {
  animation: inputFocus 0.3s ease-out;
}


/* 错误提示 */
.error-alert {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 9999;
  max-width: 400px;
}

/* 响应式设计 */
@media (max-width: 1024px) {
  .timeline-container {
    width: 280px;
  }
}

@media (max-width: 768px) {
  .main-container {
    padding: 0;
    gap: 0;
  }

  .chat-timeline-container {
    flex-direction: column;
  }

  .timeline-container {
    width: 100%;
    height: 200px;
    order: 2;
    border-top: 1px solid #e4e7ed;
  }

  .chat-container {
    order: 1;
    border-right: none;
  }

  .chat-header {
    padding: 16px;
  }

  .chat-messages {
    padding: 16px;
  }

  .chat-input {
    padding: 16px 20px;
  }

  .input-wrapper {
    flex-direction: column;
    align-items: stretch;
  }

  .input-with-button {
    min-height: 48px;
  }

  .welcome-content h3 {
    font-size: 20px;
  }
}

@media (max-width: 480px) {
  .chat-input {
    padding: 12px;
  }

  .input-with-button {
    min-height: 44px;
  }

  .scenario-input :deep(.el-textarea__inner) {
    padding: 12px 16px;
    padding-right: 60px;
    font-size: 13px;
  }
}
</style>
