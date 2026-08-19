<template>
  <div class="midscene-agent-page">
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
            <h2>MidScene脚本生成</h2>
            <p>使用AI视觉模型将自然语言描述转换为可执行的MidScene.js自动化脚本</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 主要内容区域 -->
    <div class="main-content">
      <!-- 执行进度和时间线 -->
      <div v-if="isGenerating || timelineNodes.length > 0" class="progress-timeline-container">
        <!-- 节点时间线 -->
        <el-card class="timeline-card">
          <template #header>
            <div class="card-header">
              <div class="header-left">
                <el-icon class="timeline-icon">
                  <Clock />
                </el-icon>
                <span>智能体执行时间线</span>
              </div>
              <el-button v-if="isGenerating" type="danger" size="small" @click="cancelGeneration">
                取消生成
              </el-button>
            </div>
          </template>

          <div class="timeline-content" ref="timelineContentRef">
            <!-- 横向时间线 -->
            <div class="horizontal-timeline">
              <div v-for="(node, index) in timelineNodes" :key="node.id" class="timeline-item">
                <div class="timeline-node" :class="{ 'current-node': node.status === 'executing' }">
                  <div class="node-header">
                    <div class="node-number-row">
                      <span class="node-number">{{ index + 1 }}</span>
                      <span v-if="node.duration" class="node-duration">
                        {{ formatDuration(node.duration) }}
                      </span>
                    </div>
                    <span class="node-name">{{ node.displayName }}</span>
                    <div class="node-status">
                      <el-tag :type="getNodeStatusType(node.status)" size="small">
                        {{ getNodeStatusText(node.status) }}
                      </el-tag>
                      <el-icon v-if="node.status === 'executing'" class="current-loading">
                        <Loading />
                      </el-icon>
                    </div>
                  </div>
                </div>
                <!-- 连接线 -->
                <div 
                  v-if="index < timelineNodes.length - 1" 
                  class="timeline-connector"
                  :class="{ 'active': node.status === 'completed' || node.status === 'executing' }"
                ></div>
              </div>
            </div>
          </div>
        </el-card>

      </div>

      <!-- 配置和脚本区域 -->
      <div class="config-script-container">
        <!-- 输入区域 -->
        <el-card class="input-card">
          <template #header>
            <div class="card-header">
              <div class="header-left">
                <span>脚本生成配置</span>
                <el-tag v-if="isWebSocketConnected" type="success" size="small">WebSocket已连接</el-tag>
                <el-tag v-else-if="isGenerating" type="warning" size="small">WebSocket未连接</el-tag>
              </div>
              <el-button type="primary" @click="generateScript" :loading="isGenerating" :disabled="!canGenerate">
                生成脚本
              </el-button>
            </div>
          </template>

          <el-form :model="formData" label-width="120px">
            <el-form-item label="需求描述" required>
              <el-input
                v-model="formData.description"
                type="textarea"
                :rows="4"
                placeholder="请描述您希望自动化的操作，例如：打开登录页面，输入用户名和密码，点击登录按钮"
                maxlength="1000"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="页面截图">
              <div class="screenshot-upload">
                <div 
                  class="screenshot-area"
                  @paste="handlePaste"
                  @dragover.prevent
                  @drop="handleDrop"
                  :class="{ 'has-screenshot': hasScreenshot }"
                >
                  <div v-if="!hasScreenshot" class="upload-placeholder">
                    <el-icon class="upload-icon"><Picture /></el-icon>
                    <p>直接粘贴截图或拖拽图片到此处</p>
                    <p class="upload-tip">支持 PNG、JPG、JPEG 格式</p>
                  </div>
                  <div v-else class="screenshot-preview">
                    <img :src="screenshotPreview" alt="页面截图" />
                    <div class="screenshot-actions">
                      <el-button size="small" @click="clearScreenshot">清除</el-button>
                    </div>
                  </div>
                </div>
              </div>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 生成的脚本 -->
        <el-card class="script-card">
          <template #header>
            <div class="card-header">
              <span>生成的MidScene.js脚本</span>
              <div class="script-actions">
                <el-button size="small" @click="copyScript" :disabled="!generatedScript">复制脚本</el-button>
                <el-button size="small" @click="downloadScript" :disabled="!generatedScript">下载脚本</el-button>
              </div>
            </div>
          </template>

          <div class="script-content">
            <MonacoEditor
              ref="monacoEditorRef"
              v-model="generatedScript"
              language="javascript"
              :read-only="true"
              height="400px"
            />
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VideoPlay, Picture, Loading, Warning, CircleClose, Check, Clock, Document } from '@element-plus/icons-vue'
import MonacoEditor from '@/components/MonacoEditor.vue'
import { generateMidSceneScript } from '@/api/midscene'
import { buildWebSocketUrl } from '@/config/websocket'

// 响应式数据
const formData = reactive({
  description: '',
  screenshot_b64: ''
})

const isGenerating = ref(false)
const generatedScript = ref('')
const timeline = ref([])
const screenshotPreview = ref('')
const currentStep = ref('')
const currentTaskId = ref(null)

// WebSocket相关状态
const websocket = ref(null)
const isWebSocketConnected = ref(false)

// MonacoEditor引用
const monacoEditorRef = ref(null)

// 时间线节点 - 预定义所有节点
const timelineNodes = ref([
  {
    id: 'load_model_config',
    nodeName: 'load_model_config',
    displayName: '加载模型配置',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'read_documentation',
    nodeName: 'read_documentation',
    displayName: '读取文档内容',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'script_generator',
    nodeName: 'script_generator',
    displayName: 'MidScene脚本生成',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'script_reviewer',
    nodeName: 'script_reviewer',
    displayName: '脚本审核',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  },
  {
    id: 'script_finalizer',
    nodeName: 'script_finalizer',
    displayName: '脚本最终化',
    status: 'pending',
    timestamp: null,
    startTime: null,
    endTime: null,
    duration: null
  }
])
const timelineContentRef = ref(null)

// 计算属性
const hasScreenshot = computed(() => !!formData.screenshot_b64)
const canGenerate = computed(() => formData.description.trim().length > 0)

// 方法
const handlePaste = (event) => {
  const items = event.clipboardData?.items
  if (!items) return

  for (let item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        processImageFile(file)
      }
      break
    }
  }
}

const handleDrop = (event) => {
  event.preventDefault()
  const files = event.dataTransfer?.files
  if (files && files.length > 0) {
    processImageFile(files[0])
  }
}

const processImageFile = (file) => {
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }

  const reader = new FileReader()
  reader.onload = (e) => {
    const result = e.target.result
    // 提取base64数据（去掉data:image/...;base64,前缀）
    const base64Data = result.split(',')[1]
    formData.screenshot_b64 = base64Data
    screenshotPreview.value = result
    ElMessage.success('截图已添加')
  }
  reader.readAsDataURL(file)
}

const clearScreenshot = () => {
  formData.screenshot_b64 = ''
  screenshotPreview.value = ''
}

const generateScript = async () => {
  if (!canGenerate.value) {
    ElMessage.warning('请输入需求描述')
    return
  }

  try {
    isGenerating.value = true
    generatedScript.value = ''
    currentStep.value = ''
    currentTaskId.value = null

    // 重置时间线节点状态
    resetTimeline()

    // 确保WebSocket连接正常
    if (!isWebSocketConnected.value) {
      initWebSocket()
    }

    // 发送生成请求
    const response = await generateMidSceneScript({
      description: formData.description,
      screenshot_b64: formData.screenshot_b64
    })

    if (response.success && response.data?.task_id) {
      currentTaskId.value = response.data.task_id
      ElMessage.success('脚本生成任务已启动')
    } else {
      ElMessage.error(response.error || '启动脚本生成失败')
      isGenerating.value = false
    }
  } catch (error) {
    console.error('生成脚本失败:', error)
    ElMessage.error('生成脚本失败')
    isGenerating.value = false
  }
}

const cancelGeneration = async () => {
  try {
    await ElMessageBox.confirm('确定要取消脚本生成吗？', '确认取消', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    isGenerating.value = false
    closeWebSocket()
    ElMessage.info('已取消脚本生成')
  } catch {
    // 用户取消
  }
}

const copyScript = async () => {
  try {
    await navigator.clipboard.writeText(generatedScript.value)
    ElMessage.success('脚本已复制到剪贴板')
  } catch (error) {
    console.error('复制失败:', error)
    ElMessage.error('复制失败')
  }
}

const downloadScript = () => {
  const blob = new Blob([generatedScript.value], { type: 'text/javascript' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'midscene-script.js'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('脚本已下载')
}

const getTimelineType = (status) => {
  const types = {
    'running': 'primary',
    'success': 'success',
    'error': 'danger',
    'warning': 'warning'
  }
  return types[status] || 'primary'
}

const getTimelineIcon = (status) => {
  const icons = {
    'running': Loading,
    'success': Check,
    'error': CircleClose,
    'warning': Warning
  }
  return icons[status] || Loading
}

// WebSocket管理
const initWebSocket = () => {
  // 如果已经连接，先关闭现有连接
  if (websocket.value && websocket.value.readyState === WebSocket.OPEN) {
    closeWebSocket()
  }

  try {
    // 获取JWT token
    const token = localStorage.getItem('access_token')
    if (!token) {
      console.error('未找到JWT token，无法连接WebSocket')
      isWebSocketConnected.value = false
      return
    }

    // 构建带认证参数的WebSocket URL
    const wsUrl = buildWebSocketUrl(`/ws/midscene_script_generation-streaming/?token=${encodeURIComponent(token)}`)

    websocket.value = new WebSocket(wsUrl)

    websocket.value.onopen = () => {
      isWebSocketConnected.value = true
      console.log('MidScene WebSocket连接成功')
    }

    websocket.value.onmessage = (event) => {
      handleWebSocketMessage(event)
    }

    websocket.value.onclose = (event) => {
      isWebSocketConnected.value = false
      console.log('MidScene WebSocket连接关闭:', event.code, event.reason)

      // 如果是认证失败，显示相应消息
      if (event.code === 4001) {
        ElMessage.error('WebSocket认证失败，请重新登录')
      } else if (event.code === 4000) {
        ElMessage.error('WebSocket连接错误')
      }
    }

    websocket.value.onerror = (error) => {
      isWebSocketConnected.value = false
      ElMessage.error('WebSocket连接错误')
      console.error('WebSocket错误:', error)
    }

  } catch (error) {
    console.error('初始化WebSocket失败:', error)
    ElMessage.error('WebSocket初始化失败')
    isWebSocketConnected.value = false
  }
}

const closeWebSocket = () => {
  if (websocket.value) {
    if (websocket.value.readyState === WebSocket.OPEN) {
      websocket.value.close()
    }
    websocket.value = null
  }
  isWebSocketConnected.value = false
}

// WebSocket消息处理
const handleWebSocketMessage = (event) => {
  try {
    const data = JSON.parse(event.data)

    if (data.type === 'streaming_output') {
      handleStreamingOutput(data.step, data.content)
    } else if (data.type === 'streaming_complete') {
      // 流式输出完成
      handleStreamingComplete(data.step)
    } else if (data.type === 'task_status') {
      handleTaskStatusUpdate(data)
    } else if (data.type === 'task_completed') {
      // 任务完成，通过WebSocket接收结果
      handleTaskCompleted(data)
    } else if (data.type === 'task_failed') {
      // 任务失败，通过WebSocket接收错误信息
      handleTaskFailed(data)
    } else if (data.type === 'node_start') {
      // 节点开始执行通知
      handleNodeStart(data)
    } else if (data.type === 'error') {
      ElMessage.error(`❌ ${data.message}`)
    }

  } catch (error) {
    console.error('解析WebSocket消息失败:', error)
  }
}

const handleStreamingOutput = (step, content) => {
  // 如果内容为空，表示流式输出完成
  if (!content || content.trim() === '') {
    handleStreamingComplete(step)
    return
  }

  // 流式输出主要用于显示进度，脚本内容从任务完成消息中获取
  //console.log('流式输出:', step, content.substring(0, 100) + '...')
}

// 处理流式输出完成
const handleStreamingComplete = (step) => {
  
  // 将步骤名称映射到节点名称
  const stepToNodeMap = {
    'MidScene脚本生成': 'script_generator',
    '加载模型配置': 'load_model_config',
    '读取文档内容': 'read_documentation',
    '脚本审核': 'script_reviewer',
    '脚本最终化': 'script_finalizer',
    '视觉分析': 'vision_analysis',
    '脚本合成': 'script_synthesis',
    '代码生成': 'code_generation'
  }
  
  const nodeName = stepToNodeMap[step] || step
  
  // 更新对应的节点状态为已完成
  updateNodeExecutionStatus(nodeName, false)
}

const handleTaskStatusUpdate = (data) => {
  if (data.status) {
    currentStep.value = data.status
  }
}

// 处理任务完成
const handleTaskCompleted = (data) => {
  console.log('收到任务完成消息:', data)
  
  // 结束加载状态
  isGenerating.value = false

  // 将所有执行中的节点标记为已完成
  timelineNodes.value.forEach(node => {
    if (node.status === 'executing') {
      node.status = 'completed'
      node.endTime = new Date()
      node.duration = node.endTime - node.startTime
    }
  })

  // 处理任务结果 - 修复数据结构访问
  const result = data.result || data
  console.log('处理任务结果:', result)
  
  if (result && result.success) {
    // 更新生成的脚本
    if (result.script) {
      console.log('找到脚本内容，长度:', result.script.length)
      console.log('脚本内容预览:', result.script.substring(0, 200) + '...')
      
      // 后端现在应该直接返回纯JavaScript代码，但为了兼容性，仍然检查代码块标记
      let scriptContent = result.script
      
      // 如果包含代码块标记，提取其中的内容（兼容旧版本）
      const scriptMatch = scriptContent.match(/```javascript\n([\s\S]*?)\n```/)
      if (scriptMatch) {
        console.log('检测到代码块标记，提取纯代码')
        scriptContent = scriptMatch[1]
      } else {
        console.log('未检测到代码块标记，直接使用内容')
      }
      
      // 使用nextTick确保DOM更新后再设置脚本内容
      nextTick(() => {
        generatedScript.value = scriptContent
        console.log('最终设置的脚本长度:', scriptContent.length)
        console.log('最终脚本预览:', scriptContent.substring(0, 100) + '...')
        
        // 直接调用MonacoEditor的setValue方法
        if (monacoEditorRef.value) {
          console.log('直接调用MonacoEditor setValue方法')
          monacoEditorRef.value.setValue(scriptContent)
        } else {
          console.warn('MonacoEditor引用未找到')
        }
        
        // 强制触发MonacoEditor更新
        setTimeout(() => {
          console.log('延迟检查generatedScript值:', generatedScript.value ? generatedScript.value.substring(0, 100) + '...' : '空值')
          if (monacoEditorRef.value) {
            console.log('MonacoEditor当前内容:', monacoEditorRef.value.getValue().substring(0, 100) + '...')
          }
        }, 100)
      })
    } else {
      console.warn('任务完成但未找到脚本内容')
    }
    
    // 显示成功消息，使用后端返回的消息或默认消息
    const successMessage = result.message || data.message || '脚本生成完成'
    ElMessage.success(successMessage)
  } else {
    // 任务完成但结果异常
    console.warn('任务完成但结果异常:', result)
    const warningMessage = result?.message || data.message || '脚本生成完成，但结果异常，请重新生成'
    ElMessage.warning(warningMessage)
  }
  
  // 关闭WebSocket连接
  closeWebSocket()
}

// 处理任务失败
const handleTaskFailed = (data) => {
  // 结束加载状态
  isGenerating.value = false

  // 将所有执行中的节点标记为失败
  timelineNodes.value.forEach(node => {
    if (node.status === 'executing') {
      node.status = 'failed'
      node.endTime = new Date()
      node.duration = node.endTime - node.startTime
    }
  })

  // 显示错误消息，优先使用后端返回的错误信息
  const errorMsg = data.error || data.message || '脚本生成失败'
  ElMessage.error(errorMsg)
  
  // 关闭WebSocket连接
  closeWebSocket()
}

// 处理节点开始执行
const handleNodeStart = (data) => {
  const nodeName = data.node_name || 'unknown'
  
  // 更新对应节点状态为执行中
  updateTimelineNode(nodeName, 'executing')
  
  // 更新当前步骤
  currentStep.value = nodeName
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
      node.duration = node.endTime - node.startTime
    }
  }
}

// 更新节点执行状态
const updateNodeExecutionStatus = (nodeName, isExecuting) => {
  updateTimelineNode(nodeName, isExecuting ? 'executing' : 'completed')
}

// 清空时间线（现在使用重置）
const clearTimeline = () => {
  resetTimeline()
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

// 格式化时间戳
const formatTimestamp = (timestamp) => {
  return timestamp.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
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
    'failed': 'CircleClose'
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

// 生命周期
onMounted(() => {
  // 页面加载时立即初始化WebSocket连接
  initWebSocket()
})

onUnmounted(() => {
  closeWebSocket()
})
</script>

<style scoped>
.midscene-agent-page {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

/* 页面头部样式 */
.page-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  padding: 32px;
  margin-bottom: 24px;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
}

.header-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-icon {
  width: 48px;
  height: 48px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 12px;
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
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px 0;
  color: white;
  line-height: 1.2;
}

.header-text p {
  font-size: 14px;
  margin: 0;
  opacity: 0.9;
  color: white;
  line-height: 1.4;
}

/* 主要内容区域 */
.main-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 配置和脚本容器 */
.config-script-container {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

/* 卡片样式 */
.input-card,
.progress-card,
.script-card {
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

/* 输入卡片样式 */
.input-card {
  flex: 1;
  min-width: 400px;
}

/* 脚本卡片样式 */
.script-card {
  flex: 1;
  min-width: 400px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  color: #303133;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 截图上传区域 */
.screenshot-upload {
  width: 100%;
}

.screenshot-area {
  border: 2px dashed #dcdfe6;
  border-radius: 8px;
  padding: 40px;
  text-align: center;
  transition: all 0.3s ease;
  cursor: pointer;
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.screenshot-area:hover {
  border-color: #409eff;
  background-color: #f5f7fa;
}

.screenshot-area.has-screenshot {
  border-style: solid;
  border-color: #67c23a;
  background-color: #f0f9ff;
}

.upload-placeholder {
  color: #909399;
}

.upload-icon {
  font-size: 48px;
  margin-bottom: 16px;
  color: #c0c4cc;
}

.upload-tip {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 8px;
}

.screenshot-preview {
  position: relative;
  max-width: 100%;
  max-height: 300px;
}

.screenshot-preview img {
  max-width: 100%;
  max-height: 300px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.screenshot-actions {
  position: absolute;
  top: 8px;
  right: 8px;
}

/* 进度和时间线容器 */
.progress-timeline-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 时间线卡片 */
.timeline-card {
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.timeline-icon {
  font-size: 16px;
  color: #409eff;
  margin-right: 8px;
}

.timeline-content {
  max-height: 400px;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.timeline-content::-webkit-scrollbar {
  height: 6px;
}

.timeline-content::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 3px;
}

.timeline-content::-webkit-scrollbar-thumb {
  background: linear-gradient(90deg, #409eff, #67c23a);
  border-radius: 3px;
}

.timeline-content::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(90deg, #337ecc, #529b2e);
}

/* 横向时间线样式 */
.horizontal-timeline {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 16px 0;
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
  background: linear-gradient(90deg, #e4e7ed, #c0c4cc);
  margin: 0 8px;
  position: relative;
  flex-shrink: 0;
  transition: all 0.3s ease;
}

.timeline-connector.active {
  background: linear-gradient(90deg, #409eff, #67c23a);
}

.timeline-connector::after {
  content: '';
  position: absolute;
  right: -3px;
  top: -2px;
  width: 6px;
  height: 6px;
  background: #c0c4cc;
  border-radius: 50%;
  transition: all 0.3s ease;
}

.timeline-connector.active::after {
  background: #67c23a;
  box-shadow: 0 0 0 2px #fff, 0 0 0 3px #67c23a;
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
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border: 1px solid #e4e7ed;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-width: 140px;
  max-width: 160px;
  text-align: center;
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
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transform: translateY(-1px);
  border-color: #409eff;
}

.timeline-node:hover::before {
  opacity: 0.5;
}

/* 当前执行节点高亮样式 */
.timeline-node.current-node {
  border-color: #409eff;
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.2);
  transform: scale(1.05);
  animation: currentNodePulse 2s infinite ease-in-out;
}

.timeline-node.current-node::before {
  opacity: 1;
  background: linear-gradient(90deg, #409eff, #67c23a);
  height: 3px;
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

/* 已完成节点样式 */
.timeline-node:has(.node-status .el-tag--success) {
  border-color: #67c23a;
  background: linear-gradient(135deg, #f0f9ff 0%, #e8f5e8 100%);
}

.timeline-node:has(.node-status .el-tag--success) .node-number {
  background: linear-gradient(135deg, #67c23a, #85ce61);
}

/* 失败节点样式 */
.timeline-node:has(.node-status .el-tag--danger) {
  border-color: #f56c6c;
  background: linear-gradient(135deg, #fef0f0 0%, #fde2e2 100%);
}

.timeline-node:has(.node-status .el-tag--danger) .node-number {
  background: linear-gradient(135deg, #f56c6c, #f78989);
}


.node-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
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
  width: 20px;
  height: 20px;
  background: linear-gradient(135deg, #409eff, #67c23a);
  color: white;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.node-name {
  font-weight: 600;
  color: #303133;
  font-size: 13px;
  line-height: 1.3;
  text-align: center;
  word-break: break-word;
}

.node-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.current-loading {
  font-size: 16px;
  color: #409eff;
  animation: rotate 1.5s linear infinite;
}


.node-duration {
  color: #909399;
  font-size: 9px;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
  background-color: #f5f7fa;
  padding: 1px 4px;
  border-radius: 2px;
  display: inline-block;
  white-space: nowrap;
  line-height: 1.2;
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


/* 脚本内容样式 */
.script-content {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  min-height: 400px;
}

/* 脚本占位符样式 */
.script-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 400px;
  color: #909399;
  text-align: center;
  background-color: #fafafa;
  border-radius: 8px;
}

.placeholder-icon {
  font-size: 48px;
  margin-bottom: 16px;
  color: #c0c4cc;
}

.script-placeholder p {
  margin: 0;
  font-size: 14px;
  color: #606266;
}

.placeholder-tip {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 8px;
}

.script-actions {
  display: flex;
  gap: 8px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .midscene-agent-page {
    padding: 10px;
  }

  .page-header {
    padding: 20px;
  }

  .header-content {
    flex-direction: column;
    text-align: center;
  }

  .screenshot-area {
    padding: 20px;
    min-height: 150px;
  }

  .upload-icon {
    font-size: 32px;
  }

  /* 移动端横向时间线调整 */
  .horizontal-timeline {
    padding: 12px 0;
    justify-content: flex-start;
  }

  .timeline-node {
    min-width: 120px;
    max-width: 140px;
    padding: 10px 12px;
  }

  .node-name {
    font-size: 12px;
  }

  .timeline-connector {
    width: 30px;
    margin: 0 6px;
  }

  /* 移动端配置和脚本区域调整 */
  .config-script-container {
    flex-direction: column;
    gap: 15px;
  }

  .input-card,
  .script-card {
    min-width: unset;
    width: 100%;
  }

  .script-content {
    min-height: 300px;
  }

  .script-placeholder {
    height: 300px;
  }
}
</style>