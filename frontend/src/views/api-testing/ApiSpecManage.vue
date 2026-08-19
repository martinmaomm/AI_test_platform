<template>
  <el-card v-if="selectedProject" class="files-card">
    <!-- API规范列表 -->
    <div class="card-header">
      <div class="card-header-left">
        <span>API规范列表</span>
      </div>
      <div class="card-header-right">
        <el-input v-model="searchQuery" placeholder="搜索API规范..." style="width: 300px" clearable>
          <template #prefix>
            <el-icon>
              <Search />
            </el-icon>
          </template>
        </el-input>
        <el-button @click="refreshData">
          <el-icon>
            <Refresh />
          </el-icon>刷新
        </el-button>
      </div>
    </div>

    <!-- 卡片式布局 -->
    <div v-loading="loading" class="specs-grid">
      <!-- API规范卡片 -->
      <el-card v-for="spec in filteredSpecs" :key="spec.id" class="file-card" @click="viewSpec(spec)" shadow="hover">
        <template #header>
          <div class="file-card-header">
            <div class="spec-icon-wrapper" :class="getFileIconWrapperClass(spec.spec_type, spec.file_type)">
              <img :src="getFileIconSrc(spec.spec_type, spec.file_type)" :alt="spec.spec_type || spec.file_type"
                class="spec-icon" width="24" height="24" />
            </div>
            <!-- 规范状态显示 -->
            <div class="spec-status-header">
              <!-- 等待处理状态 -->
              <div v-if="spec.status === 'pending'" class="spec-waiting">
                <el-tag type="info" size="small">待处理</el-tag>
              </div>

              <!-- 处理中状态 -->
              <div v-else-if="spec.status === 'running'" class="spec-processing">
                <el-progress :percentage="100" :stroke-width="8" status="warning" striped striped-flow :duration="2"
                  style="width: 120px;" :show-text="false" />
                <span class="progress-text-header">处理中...</span>
              </div>

              <!-- 处理完成状态 -->
              <div v-else-if="spec.status === 'completed'" class="spec-completed">
                <el-tag type="success" size="small">处理完成</el-tag>
              </div>

              <!-- 处理失败状态 -->
              <div v-else-if="spec.status === 'failed'" class="spec-failed">
                <el-tag type="danger" size="small">处理失败</el-tag>
                <div class="error-tooltip-header" v-if="spec.error_message">
                  <el-tooltip :content="spec.error_message" placement="top" :show-after="500">
                    <el-icon class="error-icon-header">
                      <Warning />
                    </el-icon>
                  </el-tooltip>
                </div>
              </div>
            </div>
          </div>
        </template>

        <div class="file-card-content">
          <h3 class="spec-name">{{ spec.spec_name || spec.file_name }}</h3>

          <div v-if="spec.description" class="spec-description">
            {{ spec.description }}
          </div>

          <div class="spec-meta">
            <div class="meta-item">
              <el-icon>
                <Connection />
              </el-icon>
              <span>{{ spec.spec_type || 'swagger' }}</span>
            </div>
            <div class="meta-item">
              <el-icon>
                <DataLine />
              </el-icon>
              <span>{{ spec.endpoints_count || 0 }} 个端点</span>
            </div>
            <div class="meta-item">
              <el-icon>
                <Clock />
              </el-icon>
              <span>{{ formatDate(spec.created_at) }}</span>
            </div>
          </div>
        </div>

        <template #footer>
          <div class="file-card-actions">
            <!-- 等待处理状态：显示AI生成测试按钮 -->
            <el-button v-if="spec.status === 'pending'" type="primary" size="small" plain
              @click.stop="generateTests(spec)" :disabled="aiGeneratingStates.get(spec.id)"
              :loading="aiGeneratingStates.get(spec.id)" class="action-btn">
              <el-icon v-if="!aiGeneratingStates.get(spec.id)">
                <Star />
              </el-icon>
              <span>{{ aiGeneratingStates.get(spec.id) ? '正在加载中...' : 'AI生成测试' }}</span>
            </el-button>

            <!-- 处理完成状态：显示AI生成测试按钮 -->
            <!-- <el-button v-else-if="spec.status === 'completed'" type="primary" size="small" plain
              @click.stop="generateTests(spec)" :disabled="aiGeneratingStates.get(spec.id)"
              :loading="aiGeneratingStates.get(spec.id)" class="action-btn">
              <el-icon v-if="!aiGeneratingStates.get(spec.id)">
                <Star />
              </el-icon>
              <span>{{ aiGeneratingStates.get(spec.id) ? '生成中...' : 'AI生成测试' }}</span>
            </el-button> -->

            <!-- 其他状态：显示查看详情按钮 -->
            <el-button v-else type="primary" size="small" plain @click.stop="viewSpec(spec)" class="action-btn">
              <el-icon>
                <Document />
              </el-icon>
              <span>查看详情</span>
            </el-button>

            <el-button type="danger" size="small" plain @click.stop="deleteSpec(spec)" class="action-btn">
              删除
            </el-button>
          </div>
        </template>
      </el-card>

      <!-- 上传卡片 -->
      <div class="file-card upload-file-card" @click="selectedProject ? showUploadDialog = true : goToProjects()">
        <div class="upload-card-content">
          <div class="upload-icon-wrapper">
            <el-icon class="upload-icon" size="32" :class="{ 'disabled-icon': !selectedProject }">
              <Plus />
            </el-icon>
          </div>
          <h3 class="upload-title">
            {{ selectedProject ? '上传Swagger API文档' : '请先选择项目' }}
          </h3>
          <p class="upload-hint">
            {{ selectedProject ? '支持 .json, .yaml, .yml 格式' : '点击前往项目管理页面选择项目' }}
          </p>
        </div>
      </div>
    </div>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUploadDialog" title="上传API规范文档" width="500px">
      <!-- 项目选择提示 -->
      <div v-if="!selectedProject" class="project-warning" style="margin-bottom: 20px;">
        <el-alert title="请先选择一个项目" type="warning" :closable="false" show-icon>
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

      <el-form ref="uploadFormRef" :model="uploadForm" :rules="uploadRules" label-width="100px"
        :disabled="!selectedProject">
        <el-form-item label="规范名称" prop="spec_name">
          <el-input v-model="uploadForm.spec_name" placeholder="请输入API规范名称（可选）" />
        </el-form-item>
        <el-form-item label="规范描述" prop="description">
          <el-input v-model="uploadForm.description" type="textarea" :rows="3" placeholder="请输入API规范描述（可选）" />
        </el-form-item>
        <el-form-item label="规范类型" prop="spec_type">
          <el-select v-model="uploadForm.spec_type" placeholder="请选择规范类型" style="width: 100%">
            <el-option label="Swagger/OpenAPI" value="swagger" />
            <el-option label="Postman Collection" value="postman" />
            <el-option label="RAML" value="raml" />
            <el-option label="API Blueprint" value="api_blueprint" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="规范文件" prop="spec_file">
          <el-upload ref="uploadRef" :auto-upload="false" :on-change="handleFileChange" :file-list="fileList"
            accept=".json,.yaml,.yml" drag>
            <el-icon class="el-icon--upload">
              <Upload />
            </el-icon>
            <div class="el-upload__text">
              将文件拖到此处，或<em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 JSON、YAML 格式文件，文件大小不超过 10MB
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showUploadDialog = false">取消</el-button>
          <el-button type="primary" @click="handleUploadParseSpec" :loading="uploading" :disabled="!selectedProject">
            上传并解析
          </el-button>
        </span>
      </template>
    </el-dialog>

  </el-card>

  <!-- 项目选择提示 -->
  <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon
    style="margin-bottom: 20px;">
    <template #default>
      <div>
        <p>您还没有选择当前工作项目，请前往项目管理页面选择项目。</p>
        <el-button type="primary" size="small" @click="goToProjects" style="margin-top: 10px;">
          前往项目管理
        </el-button>
      </div>
    </template>
  </el-alert>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Document,
  Upload,
  Refresh,
  Search,
  DataLine,
  Star,
  Connection,
  Clock,
  Plus,
  Warning,
  MagicStick
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getAPISpecifications, uploadParseAPISpecification, deleteAPISpecification, getTaskStatus}
  from '@/api/apiTesting'
import { useProjectStore } from '@/stores/project'

const router = useRouter()

// 项目状态管理
const projectStore = useProjectStore()

// 状态
const loading = ref(false)
const uploading = ref(false)
const showUploadDialog = ref(false)
const showGeneratorDialog = ref(false)
const searchQuery = ref('')
const specs = ref([])
const fileList = ref([])
const selectedSpec = ref(null)

// AI生成测试状态管理
const aiGeneratingStates = ref(new Map()) // 存储每个spec的AI生成状态
const activeTaskIds = ref(new Map()) // 存储每个spec对应的任务ID
const taskPollingIntervals = ref(new Map()) // 存储轮询定时器

// 表单引用
const uploadFormRef = ref(null)

// 统计数据
const stats = ref({
  totalSpecs: 0,
  totalCases: 0,
  totalSuites: 0,
  successRate: 0
})

// 上传表单
const uploadForm = reactive({
  spec_name: '',
  description: '',
  spec_type: 'swagger',
  spec_file: null
})

const uploadRules = {
  spec_type: [
    { required: true, message: '请选择规范类型', trigger: 'change' }
  ],
  spec_file: [
    { required: true, message: '请选择规范文件', trigger: 'change' }
  ]
}

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

const filteredSpecs = computed(() => {
  if (!searchQuery.value) return specs.value

  return specs.value.filter(spec =>
    spec.file_name.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
    (spec.spec_name && spec.spec_name.toLowerCase().includes(searchQuery.value.toLowerCase())) ||
    (spec.description && spec.description.toLowerCase().includes(searchQuery.value.toLowerCase())) ||
    spec.spec_type.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
    spec.status.toLowerCase().includes(searchQuery.value.toLowerCase())
  )
})

// 规范类型图标映射（优先使用spec_type）
const getFileIconSrc = (specType, fileType) => {
  // 优先使用spec_type，如果没有则使用file_type
  const primaryType = specType || fileType

  const iconMap = {
    // 规范类型图标
    swagger: '/src/assert/icons/Swagger.svg',
    postman: '/src/assert/icons/Postman.svg',
    raml: '/src/assert/icons/Swagger.svg',
    api_blueprint: '/src/assert/icons/Swagger.svg',
    other: '/src/assert/icons/Swagger.svg',

    // 文件类型图标（作为备选）
    pdf: '/src/assert/icons/Pdf.svg',
    doc: '/src/assert/icons/Word.svg',
    docx: '/src/assert/icons/Word.svg',
    xls: '/src/assert/icons/Excel.svg',
    xlsx: '/src/assert/icons/Excel.svg',
    md: '/src/assert/icons/Markdown.svg',
    txt: '/src/assert/icons/Text.svg',
    json: '/src/assert/icons/Swagger.svg',
    yaml: '/src/assert/icons/Swagger.svg',
    yml: '/src/assert/icons/Swagger.svg',
    xml: '/src/assert/icons/Swagger.svg'
  }
  return iconMap[primaryType] || '/src/assert/icons/Swagger.svg'
}

// 获取文件图标包装器样式类（优先使用spec_type）
const getFileIconWrapperClass = (specType, fileType) => {
  // 优先使用spec_type，如果没有则使用file_type
  const primaryType = specType || fileType

  const wrapperClassMap = {
    // 规范类型样式类
    swagger: 'icon-swagger',
    postman: 'icon-postman',
    raml: 'icon-swagger',
    api_blueprint: 'icon-swagger',
    other: 'icon-other',

    // 文件类型样式类（作为备选）
    pdf: 'icon-pdf',
    doc: 'icon-word',
    docx: 'icon-word',
    xls: 'icon-excel',
    xlsx: 'icon-excel',
    md: 'icon-markdown',
    txt: 'icon-text',
    json: 'icon-json',
    yaml: 'icon-yaml',
    yml: 'icon-yaml',
    xml: 'icon-xml'
  }
  return wrapperClassMap[primaryType] || 'icon-other'
}

// 方法
const loadData = async () => {
  if (!selectedProject.value) {
    specs.value = []
    updateStats()
    return
  }

  try {
    loading.value = true
    const response = await getAPISpecifications(projectStore.currentProjectId)
    // 处理统一响应格式
    if (response && response.success && response.data) {
      if (response.data.items) {
        specs.value = response.data.items
      } else {
        specs.value = response.data
      }
    } else {
      specs.value = []
    }

    // 更新统计数据
    updateStats()
  } catch (error) {
    console.error('加载数据失败:', error)
    ElMessage.error('加载数据失败')
  } finally {
    loading.value = false
  }
}

// 移除 loadProjects 方法，现在使用 projectStore

const updateStats = () => {
  stats.value = {
    totalSpecs: specs.value.length,
    totalCases: specs.value.reduce((sum, spec) => sum + (spec.endpoints?.length || 0), 0),
    totalSuites: Math.floor(specs.value.length / 2), // 模拟数据
    successRate: 85 // 模拟数据
  }
}

const refreshData = () => {
  loadData()
}

// 编辑规范
const editSpec = (spec) => {
  // 这里应该打开编辑对话框，暂时用生成器对话框代替
  selectedSpec.value = spec
  //showGeneratorDialog.value = true
}

// 查看规范详情
const viewSpec = (spec) => {
  router.push(`/api-testing/specs/${spec.id}`)
}

// 跳转函数
const goToScenarioGenerator = () => {
  ElMessage.info('跳转到智能场景生成器')
  router.push('/api-testing/scenario-generator')
}

const goToAITestGenerator = () => {
  ElMessage.info('跳转到AI测试用例生成器')
  router.push('/ai-test-case-generator')
}

const goToWorkflow = () => {
  ElMessage.info('跳转到AI测试工作流')
  router.push('/api-testing/workflow')
}

// 生成测试用例
const generateTests = async (spec) => {
  try {
    // 立即设置该spec的AI生成状态为true，禁用按钮并显示加载状态
    aiGeneratingStates.value.set(spec.id, true)

    // 显示成功消息
    ElMessage.success('AI生成测试已启动，请稍候...')

    selectedSpec.value = spec
    showGeneratorDialog.value = true

    // 监听任务开始事件，获取任务ID并开始轮询
    const handleTaskStart = (event) => {
      const taskId = event.detail?.taskId
      const specId = event.detail?.specId

      if (taskId && specId === spec.id) {
        console.log(`开始轮询任务 ${taskId} for spec ${specId}`)
        activeTaskIds.value.set(specId, taskId)
        pollTaskStatus(specId, taskId)

        // 移除事件监听器
        document.removeEventListener('ai-task-started', handleTaskStart)
      }
    }

    // 监听任务开始事件
    document.addEventListener('ai-task-started', handleTaskStart)

    // 设置超时，防止事件监听器泄漏
    setTimeout(() => {
      document.removeEventListener('ai-task-started', handleTaskStart)
    }, 30000) // 30秒后清除监听器

  } catch (error) {
    console.error('启动AI生成测试失败:', error)
    // 发生错误时也要重置状态
    aiGeneratingStates.value.set(spec.id, false)
    ElMessage.error('启动AI生成测试失败')
  }
}

// 删除规范
const deleteSpec = async (spec) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除API规范 "${spec?.file_name}" 吗？此操作不可恢复。`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 调用删除API
    await deleteAPISpecification(projectStore.currentProjectId, spec?.id)
    ElMessage.success('API规范删除成功')
    loadData() // 刷新列表
  } catch (error) {
    if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else if (error.message) {
      ElMessage.error(error.message)
    } else {
      // 用户取消
    }
  }
}

// 更新规范
const updateSpec = async () => {
  try {
    await editFormRef.value.validate()
    updating.value = true

    // 调用更新API
    const updatedSpec = await updateAPISpecification(editForm.id, editForm)
    ElMessage.success('API规范更新成功')

    // 更新本地数据
    Object.assign(apiSpec.value, updatedSpec)

    showEditDialog.value = false
  } catch (error) {
    if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else if (error.message) {
      ElMessage.error(error.message)
    } else {
      ElMessage.error('更新失败，请检查输入信息')
    }
  } finally {
    updating.value = false
  }
}

const onTestCasesGenerated = (testCases) => {
  ElMessage.success(`成功生成 ${testCases.length} 个测试用例`)

  // 只有在没有活跃任务的情况下才重置状态
  // 如果有活跃任务，让轮询机制来处理状态重置
  const specId = selectedSpec.value?.id
  if (specId && !activeTaskIds.value.has(specId)) {
    // 没有活跃任务，说明是同步生成，直接重置状态
    aiGeneratingStates.value.set(specId, false)
    console.log(`同步生成完成，重置spec ${specId} 状态`)
  } else if (specId) {
    console.log(`异步任务仍在进行中，保持spec ${specId} 的加载状态`)
  }

  // 可以在这里刷新数据或进行其他操作
}

const handleFileChange = (file) => {
  // 检查文件类型
  const allowedTypes = ['.json', '.yaml', '.yml']
  const fileExtension = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()

  if (!allowedTypes.includes(fileExtension)) {
    ElMessage.error('不支持的文件格式，请上传 JSON 或 YAML 文件')
    // 清空文件选择
    uploadForm.spec_file = null
    fileList.value = []
    return
  }

  // 检查文件大小（10MB）
  const maxSize = 10 * 1024 * 1024
  if (file.size > maxSize) {
    ElMessage.error('文件大小不能超过 10MB')
    // 清空文件选择
    uploadForm.spec_file = null
    fileList.value = []
    return
  }

  // 检查是否已存在同名文件
  const existingFile = specs.value.find(spec =>
    spec.file_name === file.name ||
    (spec.uploaded_file && spec.uploaded_file.original_name === file.name)
  )

  if (existingFile) {
    ElMessage.warning('检测到同名文件，请确认是否要覆盖或重命名文件')
  }

  uploadForm.spec_file = file.raw
}

const handleUploadParseSpec = async () => {
  try {
    await uploadFormRef.value.validate()

    if (!uploadForm.spec_file) {
      ElMessage.error('请选择要上传的文件')
      return
    }

    uploading.value = true

    // 创建FormData对象
    const formData = new FormData()
    formData.append('spec_type', uploadForm.spec_type)
    formData.append('spec_file', uploadForm.spec_file)

    // 调用API上传文件并解析
    await uploadParseAPISpecification(projectStore.currentProjectId, formData)

    ElMessage.success('API规范上传解析成功')
    showUploadDialog.value = false
    loadData()

    // 重置表单
    uploadForm.spec_name = ''
    uploadForm.description = ''
    uploadForm.spec_type = 'swagger'
    uploadForm.spec_file = null
    fileList.value = []

  } catch (error) {
    console.error('上传失败:', error)

    // 处理不同类型的错误
    if (error.response?.status === 400) {
      ElMessage.error(error.response?.data?.error?.message || '文件已存在，请勿重复上传！')
    } else if (error.response?.status === 403) {
      ElMessage.error('没有权限执行此操作')
    } else if (error.response?.status === 500) {
      ElMessage.error('服务器内部错误，请稍后重试')
    } else {
      ElMessage.error(error.response?.data?.error?.message || '上传失败，请检查输入信息')
    }
  } finally {
    uploading.value = false
  }
}

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

const formatDate = (dateStr) => {
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

// 轮询任务状态
const pollTaskStatus = async (specId, taskId) => {
  const pollInterval = setInterval(async () => {
    try {
      const statusResponse = await getTaskStatus(projectStore.currentProjectId, taskId)

      if (statusResponse.success) {
        const taskStatus = statusResponse.status

        if (taskStatus === 'SUCCESS' || taskStatus === 'completed') {
          // 任务完成，重置状态
          clearInterval(pollInterval)
          taskPollingIntervals.value.delete(specId)
          aiGeneratingStates.value.set(specId, false)
          activeTaskIds.value.delete(specId)
          console.log(`任务 ${taskId} 完成，重置spec ${specId} 状态`)

          // 触发完成事件，通知其他组件
          document.dispatchEvent(new CustomEvent('test-generation-complete', {
            detail: { specId: specId }
          }))
        } else if (taskStatus === 'FAILURE' || taskStatus === 'failed') {
          // 任务失败，重置状态
          clearInterval(pollInterval)
          taskPollingIntervals.value.delete(specId)
          aiGeneratingStates.value.set(specId, false)
          activeTaskIds.value.delete(specId)
          console.log(`任务 ${taskId} 失败，重置spec ${specId} 状态`)

          // 触发完成事件，通知其他组件
          document.dispatchEvent(new CustomEvent('test-generation-complete', {
            detail: { specId: specId }
          }))
        }
        // 其他状态继续轮询
      } else {
        // 查询失败，重置状态
        clearInterval(pollInterval)
        taskPollingIntervals.value.delete(specId)
        aiGeneratingStates.value.set(specId, false)
        activeTaskIds.value.delete(specId)
        console.log(`查询任务 ${taskId} 状态失败，重置spec ${specId} 状态`)

        // 触发完成事件，通知其他组件
        document.dispatchEvent(new CustomEvent('test-generation-complete', {
          detail: { specId: specId }
        }))
      }
    } catch (err) {
      // 发生错误，重置状态
      clearInterval(pollInterval)
      taskPollingIntervals.value.delete(specId)
      aiGeneratingStates.value.set(specId, false)
      activeTaskIds.value.delete(specId)
      console.error(`轮询任务 ${taskId} 状态出错:`, err)

      // 触发完成事件，通知其他组件
      document.dispatchEvent(new CustomEvent('test-generation-complete', {
        detail: { specId: specId }
      }))
    }
  }, 3000) // 每3秒轮询一次

  // 存储轮询定时器
  taskPollingIntervals.value.set(specId, pollInterval)

  // 设置超时时间（10分钟）
  setTimeout(() => {
    if (taskPollingIntervals.value.has(specId)) {
      clearInterval(pollInterval)
      taskPollingIntervals.value.delete(specId)
      aiGeneratingStates.value.set(specId, false)
      activeTaskIds.value.delete(specId)
      console.log(`任务 ${taskId} 超时，重置spec ${specId} 状态`)

      // 触发完成事件，通知其他组件
      document.dispatchEvent(new CustomEvent('test-generation-complete', {
        detail: { specId: specId }
      }))
    }
  }, 600000) // 10分钟超时
}

// 监听AI生成完成事件
const handleGenerationComplete = (event) => {
  // 从事件中获取spec ID，如果没有则清除所有状态
  const specId = event.detail?.specId
  if (specId) {
    // 清除对应的轮询定时器
    if (taskPollingIntervals.value.has(specId)) {
      clearInterval(taskPollingIntervals.value.get(specId))
      taskPollingIntervals.value.delete(specId)
    }

    // 只有在没有活跃任务的情况下才重置状态
    if (!activeTaskIds.value.has(specId)) {
      aiGeneratingStates.value.set(specId, false)
      console.log(`收到完成事件，重置spec ${specId} 状态`)
    } else {
      console.log(`收到完成事件，但spec ${specId} 仍有活跃任务，保持加载状态`)
    }
  } else {
    // 如果没有具体的spec ID，清除所有状态（向后兼容）
    // 清除所有轮询定时器
    taskPollingIntervals.value.forEach((interval) => {
      clearInterval(interval)
    })
    taskPollingIntervals.value.clear()
    aiGeneratingStates.value.clear()
    activeTaskIds.value.clear()
    console.log('收到完成事件，清除所有状态')
  }
}

// 监听对话框关闭
const handleGeneratorDialogClose = (isVisible) => {
  // 对话框关闭时不重置状态，因为任务可能还在后台执行
  // 只有在生成完成事件触发时才重置状态
  if (!isVisible) {
    // 对话框关闭，但保持加载状态直到任务真正完成
    console.log('对话框已关闭，但AI生成任务可能仍在后台进行...')
  }
}

// 生命周期
onMounted(async () => {
  // 初始化项目
  await projectStore.initializeUserPreferences()
  
  // 添加事件监听器
  document.addEventListener('test-generation-complete', handleGenerationComplete)
})

// 监听项目选择变化
watch(selectedProject, async (newProjectId, oldProjectId) => {
  if (newProjectId && newProjectId !== oldProjectId) {
    // 项目变化时重新加载数据
    await loadData()
  }
}, { immediate: true })

// 组件卸载时清理事件监听器和定时器
onUnmounted(() => {
  document.removeEventListener('test-generation-complete', handleGenerationComplete)

  // 清理所有轮询定时器
  taskPollingIntervals.value.forEach((interval) => {
    clearInterval(interval)
  })
  taskPollingIntervals.value.clear()
  aiGeneratingStates.value.clear()
  activeTaskIds.value.clear()
})
</script>

<style scoped>
.files-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header-left {
  display: flex;
  align-items: center;
}

.card-header-left span {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.card-header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.spec-icon {
  color: #409EFF;
}

/* 规范状态显示样式 */
.spec-status-header {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  min-width: 140px;
  padding: 4px 0;
}

.spec-waiting,
.spec-processing,
.spec-completed,
.spec-failed {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.spec-waiting .el-tag {
  background: linear-gradient(135deg, #f0f2f5 0%, #e4e7ed 100%);
  border: 1px solid #d9dce0;
  color: #606266;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.spec-completed .el-tag {
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border: 1px solid #7dd3fc;
  color: #0369a1;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(59, 130, 246, 0.1);
}

.spec-failed .el-tag {
  background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
  border: 1px solid #fca5a5;
  color: #dc2626;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(239, 68, 68, 0.1);
}

.spec-processing {
  background: #f8f9fa;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid #e9ecef;
}

.progress-text-header {
  font-size: 12px;
  color: #b3b4b5;
  font-weight: 600;
  min-width: 40px;
  text-align: right;
  white-space: nowrap;
}

.error-tooltip-header {
  display: inline-block;
  margin-left: 6px;
}

.error-icon-header {
  color: #dc2626;
  cursor: help;
  font-size: 14px;
  background: #fef2f2;
  padding: 2px;
  border-radius: 50%;
  transition: all 0.2s ease;
}

.error-icon-header:hover {
  background: #fee2e2;
  transform: scale(1.1);
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.specs-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 24px;
  padding: 8px;
}

.empty-state {
  grid-column: 1 / -1;
  text-align: center;
  padding: 60px 20px;
  color: #909399;
  background: #fafafa;
  border-radius: 8px;
  border: 2px dashed #e4e7ed;
}

.empty-icon {
  margin-bottom: 20px;
  color: #c0c4cc;
}

.empty-state h3 {
  font-size: 22px;
  color: #303133;
  margin-bottom: 12px;
  font-weight: 600;
}

.empty-state p {
  font-size: 16px;
  color: #909399;
  line-height: 1.6;
}

.file-card {
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  min-height: 280px;
  position: relative;
  overflow: hidden;
}

.file-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
  border-color: #409eff;
}

.file-card :deep(.el-card__header) {
  padding: 15px 20px 15px 20px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.file-card :deep(.el-card__body) {
  padding: 15px 20px 0px 20px;
  flex: 1;
}

.file-card :deep(.el-card__footer) {
  padding: 16px 20px;
  border-top: 1px solid #f0f0f0;
  background: #fafafa;
}

.file-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.spec-icon-wrapper {
  width: 48px;
  height: 48px;
  background: linear-gradient(135deg, #ecf5ff 0%, #d9ecff 100%);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* 不同文件类型的图标样式 */
.icon-pdf {
  background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%);
}

.icon-word {
  background: linear-gradient(135deg, #2e86de 0%, #1e3a8a 100%);
}

.icon-excel {
  background: linear-gradient(135deg, #10ac84 0%, #006266 100%);
}

.icon-markdown {
  background: linear-gradient(135deg, #5f27cd 0%, #341f97 100%);
}

.icon-text {
  background: linear-gradient(135deg, #54a0ff 0%, #2e86de 100%);
}

.icon-json {
  background: linear-gradient(135deg, #ff9ff3 0%, #f368e0 100%);
}

.icon-yaml {
  background: linear-gradient(135deg, #ff9ff3 0%, #f368e0 100%);
}

.icon-xml {
  background: linear-gradient(135deg, #ff9ff3 0%, #f368e0 100%);
}

.icon-swagger {
  background: linear-gradient(135deg, #85ea2d 0%, #5cb85c 100%);
}

.icon-postman {
  background: linear-gradient(135deg, #ff6c37 0%, #e74c3c 100%);
}

.icon-other {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.spec-icon {
  width: 24px;
  height: 24px;
  filter: brightness(0) invert(1);
}

.file-card-content {
  flex: 1;
  margin-bottom: 15px;
}

.spec-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  min-height: 20px;
}

.spec-description {
  font-size: 13px;
  color: #606266;
  margin-bottom: 12px;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  background: #f8f9fa;
  padding: 8px 12px;
  border-radius: 6px;
  border-left: 3px solid #409eff;
}



.spec-meta {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 13px;
  color: #909399;
  margin-top: 8px;
}

.meta-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #f8f9fa;
  border-radius: 6px;
  transition: background-color 0.3s ease;
  border: 1px solid #f1f3f4;
}

.meta-item:hover {
  background: #f1f5f9;
  border-color: #e2e8f0;
}

.meta-item .el-icon {
  margin-right: 8px;
  color: #409EFF;
  font-size: 16px;
}

.file-card-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.action-btn {
  height: 36px;
  font-size: 13px;
  border-radius: 8px;
  transition: all 0.3s ease;
  position: relative;
}

.action-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.action-btn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

/* 加载中状态的按钮样式 */
.action-btn:loading {
  background: linear-gradient(135deg, #409EFF 0%, #67C23A 100%);
  color: #ffffff;
  border-color: transparent;
}

/* 不同状态按钮的样式 */
.action-btn[type="info"] {
  background: linear-gradient(135deg, #909399 0%, #606266 100%);
  border-color: transparent;
  color: #ffffff;
}

.action-btn[type="info"]:hover {
  background: linear-gradient(135deg, #606266 0%, #303133 100%);
}

/* 响应式设计 */
@media (max-width: 1400px) {
  .specs-grid {
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
  }
}

@media (max-width: 1000px) {
  .specs-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
  }

  .upload-file-card {
    padding: 28px 20px;
  }

  .upload-icon-wrapper {
    width: 64px;
    height: 64px;
  }

  .upload-icon {
    font-size: 24px;
  }
}

@media (max-width: 768px) {
  .specs-grid {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 16px 0;
  }

  .file-card {
    min-height: 260px;
  }

  .action-btn {
    height: 40px;
    font-size: 14px;
  }
}

/* 上传卡片样式 */
.upload-file-card {
  border: 2px dashed #cbd5e1;
  border-radius: 16px;
  padding: 40px 32px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  position: relative;
  overflow: hidden;
  background: linear-gradient(135deg, #fafbfc 0%, #f8fafc 100%);
}

.upload-file-card:hover {
  border-color: #3b82f6;
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  transform: translateY(-2px);
  box-shadow: 0 8px 32px rgba(59, 130, 246, 0.1);
}

.upload-card-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
  width: 100%;
}

.upload-icon-wrapper {
  width: 80px;
  height: 80px;
  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 20px rgba(59, 130, 246, 0.15);
  transition: all 0.2s ease;
  border: 2px solid #dbeafe;
}

.upload-file-card:hover .upload-icon-wrapper {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  transform: scale(1.05);
  box-shadow: 0 8px 32px rgba(59, 130, 246, 0.25);
}

.upload-file-card:hover .upload-icon {
  color: #ffffff;
}

.upload-icon {
  color: #3b82f6;
  font-size: 32px;
  transition: color 0.2s ease;
}

.disabled-icon {
  color: #94a3b8;
}

.upload-file-card:not(:hover) .disabled-icon {
  color: #94a3b8;
}

.upload-title {
  font-size: 18px;
  font-weight: 700;
  color: #303133;
  margin: 0;
  line-height: 1.3;
}

.upload-description {
  font-size: 14px;
  color: #606266;
  margin: 0;
  line-height: 1.5;
}

.upload-hint {
  font-size: 12px;
  color: #909399;
  margin: 0;
  line-height: 1.4;
  background: rgba(64, 158, 255, 0.1);
  padding: 6px 12px;
  border-radius: 12px;
  border: 1px solid rgba(64, 158, 255, 0.2);
}

.upload-btn {
  width: 100%;
  height: 44px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 8px;
  background: linear-gradient(135deg, #409EFF 0%, #67C23A 100%);
  border: none;
  color: #ffffff;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
}

.upload-btn:hover {
  background: linear-gradient(135deg, #1890ff 0%, #52c41a 100%);
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(64, 158, 255, 0.4);
}

.upload-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
}

.upload-btn .el-icon {
  margin-right: 8px;
  font-size: 16px;
}

/* 功能区域样式 */
.function-area {
  margin-bottom: 20px;
}

.function-card {
  padding: 20px;
  border-radius: 12px;
  background: linear-gradient(135deg, #f8f9fa 0%, #f0f2f5 100%);
  border: 1px solid #e9ecef;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.function-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 15px;
}

.function-header h3 {
  font-size: 20px;
  color: #303133;
  margin: 0;
  font-weight: 600;
}

.function-header p {
  font-size: 14px;
  color: #909399;
  margin: 0;
  line-height: 1.4;
}

.function-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 15px;
}

.function-item {
  display: flex;
  align-items: center;
  padding: 15px 20px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #e9ecef;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.function-item:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.1);
  border-color: #409eff;
}

.function-icon {
  width: 60px;
  height: 60px;
  background: linear-gradient(135deg, #ecf5ff 0%, #d9ecff 100%);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 15px;
  flex-shrink: 0;
}

.function-content {
  flex: 1;
}

.function-content h4 {
  font-size: 16px;
  color: #303133;
  margin-bottom: 5px;
  font-weight: 600;
}

.function-content p {
  font-size: 13px;
  color: #606266;
  margin-bottom: 8px;
  line-height: 1.4;
}

.function-item .el-tag {
  margin-left: 8px;
}
</style>
