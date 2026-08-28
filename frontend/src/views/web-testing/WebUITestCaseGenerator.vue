<template>
  <div class="webui-generator">
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
                 <span v-if="currentStep">{{ getStepDisplayName(currentStep) }}</span>
                 <span v-else>从需求生成测试用例</span>
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
                <div class="welcome-header">
                  <el-icon class="robot-icon"><Platform /></el-icon>
                  <h2>从需求生成测试用例</h2>
                  <p class="subtitle">根据业务模块、知识库和已维护的页面元素生成可编辑的结构化用例；本模式不会操作真实浏览器，也不会直接生成可执行脚本。</p>
                </div>

                <div class="intro-card">
                  <div class="intro-title">💡 V1.0 智能生成指南</div>

                  <div class="feature-list">
                    <div class="feature-item highlight-item">
                      <div class="feature-icon bg-blue">📦</div>
                      <div class="feature-content">
                        <h4>第一步：锁定业务模块 (必选)</h4>
                        <p>在下方输入框输入 <kbd>@</kbd> 呼出模块列表，选择您要测试的业务模块（如：@登录模块）。系统会自动在后台挂载该模块关联的所有需求与页面资产。</p>
                      </div>
                    </div>
                    <div class="feature-item">
                      <div class="feature-icon bg-green">🎯</div>
                      <div class="feature-content">
                        <h4>第二步：补充特殊场景 (可选)</h4>
                        <p>如果您想指定测试范围，可附加简单描述（如："只生成密码长度不足的边界异常测试"）。如果不填，我将默认生成该模块的全量用例。</p>
                      </div>
                    </div>
                  </div>
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
                <!-- 模式 A：解析成功且非流式接收时，表格渲染 -->
                <template v-if="message.role !== 'user' && getParsedTestCase(message) && !message.isStreaming">
                  <div class="message-text message-text-with-table">
                    <div v-for="(group, gIdx) in normalizeStepsForTable(getParsedTestCase(message))" :key="gIdx" class="test-case-card-wrap">
                      <el-card class="test-case-card" shadow="never">
                        <template #header>
                          <span class="card-title">{{ group.case?.title || group.case?.name || `测试用例 ${gIdx + 1}` }}</span>
                        </template>
                        <div v-if="getPreconditionsList(group.case).length" class="preconditions">
                          <span class="label">测试前置条件：</span>
                          <ul>
                            <li v-for="(p, pi) in getPreconditionsList(group.case)" :key="pi">{{ p }}</li>
                          </ul>
                        </div>
                        <el-table :data="group.steps" size="small" stripe class="steps-table">
                          <el-table-column type="index" label="序号" width="60" align="center" />
                          <el-table-column prop="action" label="操作动作" min-width="100" show-overflow-tooltip />
                          <el-table-column prop="target" label="目标元素" min-width="140" show-overflow-tooltip>
                            <template #default="{ row }">{{ typeof row.target === 'string' ? row.target : (row.target?.targetSelector ?? row.target?.selector ?? '—') }}</template>
                          </el-table-column>
                          <el-table-column prop="value" label="输入值" min-width="100" show-overflow-tooltip>
                            <template #default="{ row }">{{ row.value || row.inputValue || '—' }}</template>
                          </el-table-column>
                          <el-table-column prop="description" label="步骤描述/预期" min-width="180" show-overflow-tooltip>
                            <template #default="{ row }">{{ row.description || row.expected || '—' }}</template>
                          </el-table-column>
                        </el-table>
                      </el-card>
                    </div>
                  </div>
                </template>
                <!-- 模式 B：普通文本或解析失败，降级为 v-html -->
                <div v-else class="message-text" v-html="formatMessageContent(message.content)"></div>

                <!-- 消息状态 -->
                <div v-if="message.status" class="message-status">
                  <el-tag :type="getStatusType(message.status)" size="small">
                    {{ getStatusText(message.status) }}
                  </el-tag>
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

      <!-- 输入区域：资产联想交互舱 -->
      <div class="chat-input">
        <div class="input-container">
          <div class="input-cockpit">
            <!-- 输入框 + 健康度 -->
            <div class="input-with-health">
              <div class="input-wrapper">
                <div class="input-with-button">
                  <!-- 输入框（自适应高度） -->
                  <el-input
                    ref="testInputRef"
                    v-model="testDescription"
                    type="textarea"
                    :autosize="{ minRows: 1, maxRows: 6 }"
                    placeholder="输入 @ 选择业务模块，可附加场景描述（如：只生成边界异常测试）..."
                    show-word-limit
                    :disabled="isGenerating"
                    @keydown="handleInputKeydown"
                    @input="handlePromptInput"
                    class="test-input"
                    resize="none"
                    :style="{ paddingRight: testDescription.trim().length > 0 ? '60px' : '20px' }"
                  />

                  <!-- 发送图标 -->
                  <div v-show="selectedModule || testDescription.trim().length > 0" @click="generateTestCases" class="send-icon"
                    :class="{ 'loading': isGenerating, 'disabled': !canGenerate }">
                    <el-icon v-if="!isGenerating"><Top /></el-icon>
                    <el-icon v-else class="rotating"><Loading /></el-icon>
                  </div>
                </div>
              </div>

              <!-- 上下文健康度指示器 -->
              <div class="prompt-health">
                <span class="health-label">上下文完善度：</span>
                <el-progress :percentage="promptScore" :status="promptScore > 0 ? 'success' : ''" :stroke-width="8" />
                <div class="health-tips" :class="{ success: promptScore > 0 }" v-if="promptScore === 0">
                  💡 提示：请先输入 <kbd>@</kbd> 选择要测试的业务模块
                </div>
                <div class="health-tips success" v-else>
                  ✅ 资产已锁定，可直接发送或补充具体场景
                </div>
              </div>
            </div>

            <!-- @ 业务模块联想浮窗（横向宫格卡片） -->
            <div v-show="showAtPopover" class="mention-panel" ref="atPopoverRef">
              <div class="mention-panel-header">
                <span>请选择业务模块</span>
                <span class="mention-hint">支持键盘上下左右选中，回车确认</span>
              </div>
              <div class="module-grid">
                <div
                  class="module-card"
                  v-for="(mod, index) in filteredModules"
                  :key="mod.id"
                  :class="{ 'is-active': activeModuleIndex === index }"
                  @click="selectModule(mod)"
                >
                  <el-icon><Folder /></el-icon>
                  <span class="module-name">{{ mod.name }}</span>
                </div>
              </div>
            </div>

            <!-- 资产提示栏（V1.0 单模块） -->
            <div class="asset-chips-bar">
              <el-tag v-if="selectedModule" type="primary" closable size="small" @close="clearModule">
                @{{ selectedModule.name }}
              </el-tag>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, Download, Delete, Loading, InfoFilled, ArrowUp, Top, Promotion, Clock, Platform, Folder } from '@element-plus/icons-vue'
import { generateWebUITestCases, getWebUITestModules } from '@/api/webTesting'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { 
  WebSocketManager, 
  WebSocketMessageHandler
} from '@/config/websocket'
import { useRouter } from 'vue-router'

// 使用项目状态管理
const projectStore = useProjectStore()
const authStore = useAuthStore()
const router = useRouter()

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 响应式数据
const testDescription = ref('')
const isGenerating = ref(false)
const testInputRef = ref(null)
const atPopoverRef = ref(null)

// V1.0 单模块选择
const selectedModule = ref(null)
const showAtPopover = ref(false)
const activeModuleIndex = ref(0)

// 模块列表（从 API 拉取，扁平化树结构）
const moduleList = ref([])

// 扁平化模块树为列表
const flattenModules = (nodes) => {
  const result = []
  const walk = (arr) => {
    for (const node of arr || []) {
      result.push({ id: node.id, name: node.name })
      if (node.children?.length) walk(node.children)
    }
  }
  walk(nodes)
  return result
}

// 根据 @ 后的输入过滤模块（支持拼音/汉字前缀匹配）
const mentionFilter = computed(() => {
  const text = testDescription.value
  if (!text.includes('@')) return ''
  const afterAt = text.slice(text.lastIndexOf('@') + 1)
  return afterAt.trim().toLowerCase()
})

const filteredModules = computed(() => {
  const list = moduleList.value
  const q = mentionFilter.value
  if (!q) return list
  return list.filter(m => (m.name || '').toLowerCase().includes(q))
})

// 上下文完善度：V1.0 只要选中模块即 100%
const promptScore = computed(() => {
  return selectedModule.value ? 100 : 0
})

// 监听输入，检测 @ 触发模块联想
const handlePromptInput = () => {
  const text = testDescription.value
  showAtPopover.value = text.includes('@')
  if (showAtPopover.value) {
    activeModuleIndex.value = 0
  }
}

// 过滤结果变化时，确保 activeIndex 不越界
watch(filteredModules, (list) => {
  if (list.length > 0 && activeModuleIndex.value >= list.length) {
    activeModuleIndex.value = list.length - 1
  }
})

// 键盘导航：上下左右、回车
const GRID_COLS = 4
const handleInputKeydown = (e) => {
  if (e.ctrlKey && e.key === 'Enter') {
    generateTestCases()
    return
  }
  if (!showAtPopover.value || filteredModules.value.length === 0) return
  const len = filteredModules.value.length
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeModuleIndex.value = Math.min(activeModuleIndex.value + GRID_COLS, len - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeModuleIndex.value = Math.max(activeModuleIndex.value - GRID_COLS, 0)
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    activeModuleIndex.value = Math.min(activeModuleIndex.value + 1, len - 1)
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    activeModuleIndex.value = Math.max(activeModuleIndex.value - 1, 0)
  } else if (e.key === 'Enter' && activeModuleIndex.value >= 0 && filteredModules.value[activeModuleIndex.value]) {
    e.preventDefault()
    selectModule(filteredModules.value[activeModuleIndex.value])
  } else if (e.key === 'Escape') {
    showAtPopover.value = false
  }
}

const selectModule = (m) => {
  selectedModule.value = m
  const text = testDescription.value
  const atIdx = text.lastIndexOf('@')
  testDescription.value = text.slice(0, atIdx) // 移除 @ 及之后的输入
  showAtPopover.value = false
}

const clearModule = () => {
  selectedModule.value = null
}
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

// 计算属性：V1.0 选中模块或输入描述即可发送
const canGenerate = computed(() => {
  return projectStore.currentProject &&
    (selectedModule.value || testDescription.value.trim().length > 0) &&
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

// 加载模块列表（从 API 拉取，扁平化后存入 moduleList）
const loadModuleList = async () => {
  const projectId = projectStore.currentProject?.id
  if (!projectId) {
    moduleList.value = []
    return
  }
  try {
    const res = await getWebUITestModules(projectId)
    if (res?.success && res?.data) {
      moduleList.value = flattenModules(res.data)
    } else {
      moduleList.value = []
    }
  } catch (error) {
    console.error('获取模块列表失败', error)
    // 降级：使用假数据便于查看 UI 效果
    moduleList.value = [
      { id: 1, name: '注册模块' }, { id: 2, name: '登录模块' },
      { id: 3, name: '商品搜索模块' }, { id: 4, name: '购物车模块' },
      { id: 5, name: '下单模块' }, { id: 6, name: '支付模块' },
      { id: 7, name: '个人中心模块' }, { id: 8, name: '订单管理模块' }
    ]
  }
}

// 生命周期
onMounted(async () => {
  if (authStore.isAuthenticated) {
    initWebSocket()
  }
  await loadModuleList()
})

// 项目切换时重新加载模块
watch(() => projectStore.currentProject?.id, () => {
  loadModuleList()
})

onUnmounted(() => {
  closeWebSocket()
})

// 统一错误处理
const handleError = (errorMsg, showMessage = true) => {
  errorMessage.value = errorMsg
  if (showMessage) {
    ElMessage.error(errorMsg)
  }
}

// 添加错误消息
const addErrorMessage = (message) => {
  addChatMessage('ai', `❌ ${message}`)
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

  websocketManager.value.initWebSocket('/ws/webui_test_generation-streaming/', token, config)
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
    const successMessage = data.message || '🎉 测试用例生成完成！'
    addChatMessage('ai', successMessage, data.result, 'success')
    ElMessage.success(data.message || '测试用例生成成功！')
  } else {
    // 任务完成但结果异常
    const warningMessage = data.message || '⚠️ 测试用例生成完成，但结果已过期，请重新生成'
    addChatMessage('ai', warningMessage)
    ElMessage.warning(data.message || '测试用例生成完成，但结果已过期，请重新生成')
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
  const errorMsg = data.error || data.message || '测试用例生成失败'
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

// 添加聊天消息
const addChatMessage = (role, content, result = null, status = null) => {
  const message = {
    role,
    timestamp: new Date(),
    content,
    result,
    status,
    isStreaming: false
  }

  chatMessages.value.push(message)
  scrollToBottom()
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
  a.download = `webui-test-chat-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`
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

/**
 * 智能解析测试用例 JSON：从 ```json ... ``` 中提取并解析
 * @param {string} content - 消息内容
 * @returns {object|array|null} 解析成功返回对象/数组，否则返回 null
 */
const parseTestCaseData = (content) => {
  if (!content || typeof content !== 'string') return null
  try {
    let jsonStr = ''
    const match = content.match(/```(?:json)?\s*([\s\S]*?)```/i)
    if (match) {
      jsonStr = match[1].trim()
    } else {
      // 尝试直接解析整段内容
      const trimmed = content.trim()
      if ((trimmed.startsWith('[') && trimmed.endsWith(']')) || (trimmed.startsWith('{') && trimmed.endsWith('}'))) {
        jsonStr = trimmed
      } else {
        return null
      }
    }
    if (!jsonStr) return null
    const parsed = JSON.parse(jsonStr)
    // 支持 { test_cases: [...] } 包装格式
    const arr = parsed?.test_cases ?? (Array.isArray(parsed) ? parsed : [parsed])
    const hasSteps = arr.some(item => {
      const s = item?.steps || item?.test_steps
      return Array.isArray(s) && s.length > 0
    })
    const hasStepsOrAction = arr.some(item => {
      const s = item?.steps || item?.test_steps
      if (Array.isArray(s)) {
        return s.some(step => step?.action != null || step?.target != null || step?.targetSelector != null)
      }
      return item?.action != null || item?.target != null
    })
    if (hasSteps || hasStepsOrAction) return parsed
    return null
  } catch {
    return null
  }
}

/** 获取消息解析后的测试用例数据（供模板使用） */
const getParsedTestCase = (message) => parseTestCaseData(message?.content)

/** 将前置条件规范化为数组（支持 string 或 string[]） */
const getPreconditionsList = (tc) => {
  const p = tc?.preconditions
  if (!p) return []
  if (Array.isArray(p)) return p.filter(Boolean)
  if (typeof p === 'string') return p.trim() ? [p.trim()] : []
  return []
}

/** 将解析结果规范化为表格展示用的数组 */
const normalizeStepsForTable = (data) => {
  if (!data) return []
  // 支持 { test_cases: [...] } 包装格式
  let arr = data?.test_cases ?? data
  arr = Array.isArray(arr) ? arr : [arr]
  const result = []
  for (const item of arr) {
    if (!item) continue
    const steps = item?.steps || item?.test_steps || []
    if (Array.isArray(steps) && steps.length > 0) {
      result.push({ case: item, steps })
    }
  }
  return result
}

// 获取状态类型
const getStatusType = (status) => {
  const statusMap = {
    'success': 'success',
    'error': 'danger',
    'warning': 'warning',
    'info': 'info'
  }
  return statusMap[status] || 'info'
}

// 获取状态文本
const getStatusText = (status) => {
  const statusMap = {
    'success': '成功',
    'error': '失败',
    'warning': '警告',
    'info': '信息'
  }
  return statusMap[status] || status
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

// 生成测试用例
const generateTestCases = async () => {
  // V1.0：选中模块或输入描述即可发送
  if (!canGenerate.value) return

  const modulePrefix = selectedModule.value ? `@${selectedModule.value.name} ` : ''
  const userInput = modulePrefix + testDescription.value.trim()

  try {
    // 添加用户消息
    addChatMessage('user', userInput || '生成该模块全量用例')

    isGenerating.value = true
    generationProgress.value = 0
    errorMessage.value = ''
    currentTaskId.value = null

    // 添加AI开始消息
    addChatMessage('ai', '🚀 开始分析您的Web测试需求，请稍候...')

    // 清空时间线
    clearTimeline()

    // 隐藏之前的流式输出指示器
    hideAllStreamingIndicators()

    // 检查WebSocket连接状态
    if (!isWebSocketConnected.value) {
      addChatMessage('ai', '⚠️ WebSocket未连接，将无法显示实时输出')
      initWebSocket()
    }

    // 调用API启动异步任务（V1.0 传入模块前缀 + 可选描述）
    const response = await generateWebUITestCases(projectStore.currentProjectId, {
      user_input: userInput || (selectedModule.value ? `@${selectedModule.value.name}` : ''),
      project_id: projectStore.currentProject.id,
      module_id: selectedModule.value?.id ?? null
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

      ElMessage.success('WebUI测试用例生成任务已启动，正在处理中...')

      // 注意：不再使用轮询，完全依赖WebSocket接收状态更新和结果
    } else {
      const errorMsg = response.message || '启动WebUI测试用例生成任务失败'
      addErrorMessage(errorMsg)
      handleError(errorMsg)
      // 启动失败时结束加载状态
      isGenerating.value = false
    }

  } catch (error) {
    console.error('启动WebUI测试用例生成任务失败:', error)
    const errorMsg = error.message || '启动WebUI测试用例生成任务时发生错误'
    addErrorMessage(errorMsg)
    handleError(errorMsg)
    // 发生错误时结束加载状态
    isGenerating.value = false
    setTimeout(() => {
      generationProgress.value = 0
    }, 2000)
  }
  // 注意：成功启动任务后，isGenerating 状态由 WebSocket 消息管理
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
  const typeMap = {
    'pending': 'info',
    'executing': 'primary',
    'completed': 'success',
    'failed': 'danger'
  }
  return typeMap[status] || 'info'
}

// 获取节点图标
const getNodeIcon = (status) => {
  const iconMap = {
    'pending': 'Clock',
    'executing': 'Loading',
    'completed': 'Check',
    'failed': 'Close'
  }
  return iconMap[status] || 'Clock'
}

// 获取节点状态类型
const getNodeStatusType = (status) => {
  const typeMap = {
    'pending': 'info',
    'executing': 'primary',
    'completed': 'success',
    'failed': 'danger'
  }
  return typeMap[status] || 'info'
}

// 获取节点状态文本
const getNodeStatusText = (status) => {
  const textMap = {
    'pending': '等待中',
    'executing': '执行中',
    'completed': '已完成',
    'failed': '失败'
  }
  return textMap[status] || status
}

</script>

<style scoped>
.webui-generator {
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
  scroll-padding-top: 0;
  scroll-behavior: smooth;
  position: relative;
  scroll-snap-type: y proximity;
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
  max-width: 720px;
}

.welcome-header {
  text-align: center;
  margin-bottom: 40px;
}

.welcome-header .robot-icon {
  font-size: 48px;
  color: #409eff;
  margin-bottom: 16px;
}

.welcome-header h2 {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px 0;
}

.welcome-header .subtitle {
  color: #606266;
  font-size: 15px;
  margin: 0;
  line-height: 1.6;
}

.welcome-header .subtitle strong {
  color: #409eff;
}

.intro-card {
  max-width: 680px;
  margin: 0 auto;
  background: #ffffff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  padding: 30px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
}

.intro-title {
  font-size: 16px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 24px;
  text-align: center;
}

.feature-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.feature-item {
  display: flex;
  align-items: flex-start;
  padding: 16px;
  border-radius: 8px;
  background: #f8f9fa;
  transition: all 0.3s ease;
}

.feature-item.highlight-item {
  background: #f0f7ff;
  border: 1px solid #cce5ff;
}

.feature-icon {
  font-size: 24px;
  margin-right: 16px;
  flex-shrink: 0;
}

.feature-icon.bg-blue {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  padding: 8px;
  border-radius: 8px;
}

.feature-icon.bg-green {
  background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
  padding: 8px;
  border-radius: 8px;
}

.feature-icon.bg-purple {
  background: linear-gradient(135deg, #f3e5f5 0%, #e1bee7 100%);
  padding: 8px;
  border-radius: 8px;
}

.feature-content h4 {
  margin: 0 0 8px 0;
  font-size: 15px;
  color: #303133;
}

.feature-content p {
  margin: 0;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
}

.feature-content kbd {
  background-color: #ffffff;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  box-shadow: 0 2px 0 #dcdfe6;
  color: #333;
  display: inline-block;
  font-family: monospace;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  padding: 2px 6px;
  margin: 0 2px;
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

/* 测试用例表格渲染样式 */
.message-text-with-table {
  margin-top: 4px;
}

.test-case-card-wrap {
  margin-top: 12px;
}

.test-case-card-wrap:first-child {
  margin-top: 0;
}

.test-case-card {
  border-radius: 12px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
}

.test-case-card :deep(.el-card__header) {
  padding: 10px 16px;
  font-weight: 600;
  color: #303133;
  background-color: #fafafa;
  border-bottom: 1px solid #e4e7ed;
}

.test-case-card :deep(.el-card__body) {
  padding: 12px 16px;
}

.preconditions {
  margin-bottom: 12px;
  font-size: 13px;
  color: #606266;
}

.preconditions .label {
  font-weight: 500;
  color: #303133;
}

.preconditions ul {
  margin: 4px 0 0 0;
  padding-left: 20px;
}

.steps-table {
  margin-top: 8px;
  border-radius: 8px;
  overflow: hidden;
}

.steps-table :deep(.el-table__header th) {
  background-color: #f5f7fa;
  color: #606266;
  font-weight: 500;
}

/* 消息状态 */
.message-status {
  margin-top: 12px;
}


/* 时间线容器样式 */
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
  0%, 80%, 100% {
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
  0%, 100% {
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

/* 资产联想交互舱 */
.input-cockpit {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  position: relative;
}

.input-with-health {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  width: 100%;
}

.input-with-health .input-wrapper {
  flex: 1;
  min-width: 0;
}

.prompt-health {
  flex-shrink: 0;
  width: 180px;
  padding: 12px 16px;
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
  border-radius: 10px;
  border: 1px solid #e2e8f0;
}

.prompt-health .health-label {
  font-size: 12px;
  color: #64748b;
  display: block;
  margin-bottom: 8px;
}

.prompt-health .el-progress {
  margin-bottom: 8px;
}

.prompt-health .health-tips {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}

.prompt-health .health-tips.success {
  color: #67c23a;
}

.prompt-health .health-tips kbd {
  background-color: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 12px;
}

.asset-chips-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px dashed #e2e8f0;
  min-height: 36px;
}

.asset-chips-bar .el-tag {
  margin: 0;
}

/* @ 模块选择器 - 横向宫格卡片 */
.mention-panel {
  position: absolute;
  bottom: 100%;
  left: 0;
  width: 600px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 16px;
  margin-bottom: 12px;
  z-index: 2000;
}

.mention-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-weight: bold;
  color: #303133;
}

.mention-hint {
  font-size: 12px;
  font-weight: normal;
  color: #909399;
}

.module-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
  max-height: 240px;
  overflow-y: auto;
  padding-right: 4px;
}

.module-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  background: #f8f9fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 13px;
  color: #606266;
}

.module-card:hover,
.module-card.is-active {
  background: #ecf5ff;
  border-color: #b3d8ff;
  color: #409eff;
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.15);
}

.module-card .el-icon {
  font-size: 16px;
}

.module-card .module-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 测试输入框 */
.test-input {
  flex: 1;
}

.test-input :deep(.el-textarea__inner) {
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
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.test-input :deep(.el-textarea__inner)::-webkit-scrollbar {
  display: none;
}

.test-input :deep(.el-textarea__inner):focus {
  outline: none;
  box-shadow: none;
}

.test-input :deep(.el-textarea__inner)::placeholder {
  color: #a8abb2;
}

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
  0%, 100% {
    box-shadow: 0 4px 16px rgba(64, 158, 255, 0.2);
  }
  50% {
    box-shadow: 0 6px 20px rgba(64, 158, 255, 0.3);
  }
}

@keyframes messageSlideIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
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

  .welcome-content .welcome-header h2 {
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

  .test-input :deep(.el-textarea__inner) {
    padding: 12px 16px;
    padding-right: 60px;
    font-size: 13px;
  }

  .input-with-health {
    flex-direction: column;
  }

  .prompt-health {
    width: 100%;
  }
}
</style>
