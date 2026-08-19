<template>
  <div class="knowledge-base-container" v-if="selectedProject">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Document />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>知识库管理</h2>
            <p>管理项目的知识库文件，支持多种格式文档的解析和RAG入库</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Upload" @click="showUploadDialog = true" :disabled="!selectedProject" class="upload-btn">
            上传文件
          </el-button>
        </div>
      </div>
    </div>

    <!-- 文件列表 -->
    <el-card class="files-card">
    <div class="card-header">
      <div class="card-header-left">
        <span>知识库文件列表</span>
      </div>
      <div class="card-header-right">
        <el-select v-model="statusFilter" placeholder="解析状态" clearable style="width: 120px;">
          <el-option label="全部" value="" />
          <el-option label="待解析" value="pending" />
          <el-option label="解析中" value="parsing" />
          <el-option label="解析成功" value="success" />
          <el-option label="解析失败" value="failed" />
        </el-select>
        <el-select v-model="ragFilter" placeholder="RAG状态" clearable style="width: 120px;">
          <el-option label="全部" value="" />
          <el-option label="待入库" value="pending" />
          <el-option label="同步中" value="syncing" />
          <el-option label="已入库" value="success" />
          <el-option label="入库失败" value="failed" />
        </el-select>
        <el-input v-model="searchQuery" placeholder="搜索文件名..." style="width: 200px;" clearable @input="handleSearch">
          <template #prefix>
            <el-icon>
              <Search />
            </el-icon>
          </template>
        </el-input>

        <!-- 布局切换按钮 -->
        <div class="layout-toggle">
          <el-button-group>
            <el-button :type="viewMode === 'grid' ? 'primary' : 'default'" size="small" @click="viewMode = 'grid'"
              title="网格视图">
              <el-icon>
                <Grid />
              </el-icon>
            </el-button>
            <el-button :type="viewMode === 'table' ? 'primary' : 'default'" size="small" @click="viewMode = 'table'"
              title="表格视图">
              <el-icon>
                <Document />
              </el-icon>
            </el-button>
          </el-button-group>
        </div>

      </div>
    </div>

    <!-- 网格布局的文件卡片 -->
    <div v-if="viewMode === 'grid'" class="files-grid" v-loading="loading">
      <!-- 知识库文件卡片 -->
      <el-card v-for="file in filteredFiles" :key="file.id" class="file-card" shadow="hover">
        <template #header>
          <div class="file-card-header">
            <div class="file-icon-wrapper" :class="getFileIconWrapperClass(file.file_type)">
              <img :src="getFileIconSrc(file.file_type)" :alt="file.file_type" class="file-icon" />
            </div>
            <!-- 任务状态显示 -->
            <div class="task-status-header">
              <!-- 未开始处理 -->
              <div v-if="['uploaded', 'pending'].includes(file.status) && !file.task_id" class="task-waiting">
                <el-tag type="info" size="small">等待处理</el-tag>
              </div>

              <!-- 处理中 -->
              <div v-else-if="file.status === 'running'" class="task-processing">
                <el-progress :percentage="100" :stroke-width="10" status="success" striped striped-flow :duration="3"
                  style="width: 150px;" :show-text="false" />
                <span class="progress-text-header" style="margin-right: 10px;">{{ file.task_message || '正在处理...'
                }}</span>
              </div>

              <!-- 处理完成 -->
              <div v-else-if="file.status === 'completed'" class="task-completed">
                <el-tag type="success" size="small">已入库</el-tag>
              </div>

              <!-- 处理失败 -->
              <div v-else-if="file.status === 'failed'" class="task-failed">
                <div class="tag-with-error">
                  <el-tag type="danger" size="small">处理失败</el-tag>
                  <div class="error-tooltip-header" v-if="file.error_message">
                    <el-tooltip :content="file.error_message" placement="top" :show-after="500">
                      <el-icon class="error-icon-header">
                        <Warning />
                      </el-icon>
                    </el-tooltip>
                  </div>
                </div>

              </div>

            <!-- 删除中 -->
            <div v-if="file.deleting" class="task-deleting">
              <el-progress :percentage="100" :stroke-width="10" status="warning" striped striped-flow :duration="3"
                style="width: 150px;" :show-text="false" />
              <span class="progress-text-header" style="margin-right: 10px;">{{ file.delete_message || '正在删除...' }}</span>
            </div>
            </div>
          </div>
        </template>

        <div class="file-card-content">
          <h4 class="file-name" :title="file.file_name">{{ file.file_name }}</h4>

          <div class="file-meta">
            <div class="meta-item">
              <el-icon>
                <DataLine />
              </el-icon>
              <span>{{ file.file_size_mb }} MB</span>
            </div>
            <div class="meta-item">
              <el-icon>
                <User />
              </el-icon>
              <div class="uploader-info">
                <el-avatar :size="20" class="uploader-avatar">
                  {{ file.uploaded_by_username?.charAt(0)?.toUpperCase() }}
                </el-avatar>
                <span>{{ file.uploaded_by_username }}</span>
              </div>
            </div>
            <div class="meta-item">
              <el-icon>
                <Clock />
              </el-icon>
              <span>{{ formatDate(file.created_at) }}</span>
            </div>
          </div>
        </div>

        <template #footer>
          <div class="file-card-actions">
            <!-- 预览按钮 - 当文件处理成功时显示 -->
            <el-button v-if="file.status === 'completed'" type="primary" size="small" plain
              @click.stop="previewFile(file)" class="action-btn">
              预览
            </el-button>

            <!-- 开始入库按钮 - 只在文件上传后且未开始处理时显示 -->
            <el-button v-if="['uploaded', 'pending'].includes(file.status) && !file.task_id" type="primary" size="small" plain
              @click.stop="startProcessing(file)" class="action-btn">
              开始入库
            </el-button>

            <!-- 处理中按钮 - 当任务正在运行时显示 -->
            <el-button v-if="file.status === 'running'" type="primary" size="small" plain :loading="true" disabled
              class="action-btn">
              处理中...
            </el-button>

            <!-- 重新解析按钮 - 已入库成功时 -->
            <el-button v-if="file.status === 'completed'" type="success" size="small" plain
              @click.stop="reprocessFile(file)" class="action-btn">
              重新解析
            </el-button>
            <!-- 重新处理按钮 - 失败时 -->
            <el-button v-if="file.status === 'failed'" type="warning" size="small" plain
              @click.stop="reprocessFile(file)" class="action-btn">
              重新处理
            </el-button>

            <el-button 
              v-if="!file.deleting" 
              type="danger" 
              size="small" 
              plain 
              @click.stop="deleteFile(file)" 
              class="action-btn">
              删除
            </el-button>
            <el-button 
              v-else 
              type="danger" 
              size="small" 
              plain 
              :loading="true" 
              disabled 
              class="action-btn">
              删除中...
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
            {{ selectedProject ? '上传知识库文件' : '请先选择项目' }}
          </h3>
          <p class="upload-hint">
            {{ selectedProject ? '支持 PDF、Word、Excel、Markdown、Text 格式' : '点击前往项目管理页面选择项目' }}
          </p>
        </div>
      </div>
    </div>

    <!-- 表格布局 -->
    <el-table v-else :data="filteredFiles" style="width: 100%" v-loading="loading">
      <el-table-column prop="file_name" label="文件名" width="250">
        <template #default="scope">
          <div class="file-name">
            <div class="file-icon-wrapper" :class="getFileIconWrapperClass(scope.row.file_type)">
              <img :src="getFileIconSrc(scope.row.file_type)" :alt="scope.row.file_type" class="file-icon" width="20"
                height="20" />
            </div>
            <span class="file-name-text" @click="previewFile(scope.row)">
              {{ scope.row.file_name }}
            </span>
          </div>
        </template>
      </el-table-column>

      <el-table-column prop="file_type" label="文件类型" width="100">
        <template #default="scope">
          <el-tag type="info">
            {{ scope.row.file_type }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="uploaded_by_username" label="上传人" width="120">
        <template #default="scope">
          <div class="uploader-info">
            <el-avatar :size="24" class="uploader-avatar">
              {{ scope.row.uploaded_by_username?.charAt(0)?.toUpperCase() }}
            </el-avatar>
            <span>{{ scope.row.uploaded_by_username }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column prop="file_size_mb" label="文件大小" width="100">
        <template #default="scope">
          {{ scope.row.file_size_mb }} MB
        </template>
      </el-table-column>

      <el-table-column prop="created_at" label="上传时间" width="160">
        <template #default="scope">
          {{ formatDate(scope.row.created_at) }}
        </template>
      </el-table-column>



      <!-- 任务状态列 -->
      <el-table-column label="任务状态" width="150">
        <template #default="scope">
          <!-- 未开始处理 -->
          <div v-if="['uploaded', 'pending'].includes(scope.row.status) && !scope.row.task_id">
            <el-tag type="info">等待处理</el-tag>
          </div>

          <!-- 处理中 -->
          <div v-else-if="scope.row.status === 'running'">
            <div class="task-progress">
              <el-progress :indeterminate="true" :duration="1" :stroke-width="8" :show-text="false"
                style="width: 60px; margin-right: 10px;" />
              <span class="progress-text">
                {{ scope.row.task_message || '正在处理...' }}
              </span>
            </div>
            <div class="task-message" v-if="scope.row.task_message">
              <el-tag type="warning" size="small">{{ scope.row.task_message }}</el-tag>
            </div>
          </div>

          <!-- 处理完成 -->
          <div v-else-if="scope.row.status === 'completed'">
            <el-tag type="success">处理完成</el-tag>
          </div>

          <!-- 处理失败 -->
          <div v-else-if="scope.row.status === 'failed'">
            <el-tag type="danger">处理失败</el-tag>
            <div class="error-tooltip" v-if="scope.row.error_message">
              <el-tooltip :content="scope.row.error_message" placement="top" :show-after="500">
                <el-icon class="error-icon">
                  <Warning />
                </el-icon>
              </el-tooltip>
            </div>
          </div>

          <!-- 删除中 -->
          <div v-if="scope.row.deleting" class="task-deleting-table">
            <el-progress :indeterminate="true" :duration="1" :stroke-width="8" :show-text="false"
              style="width: 60px; margin-right: 10px;" />
            <span class="progress-text">{{ scope.row.delete_message || '正在删除...' }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="操作" min-width="280" fixed="right">
        <template #default="scope">
          <!-- 预览按钮 - 当文件处理成功时显示 -->
          <el-button v-if="scope.row.status === 'completed'" type="primary" size="small"
            @click="previewFile(scope.row)">
            预览
          </el-button>

          <!-- 开始入库按钮 - 只在文件上传后且未开始处理时显示 -->
          <el-button v-if="['uploaded', 'pending'].includes(scope.row.status) && !scope.row.task_id" type="success" size="small"
            @click="startProcessing(scope.row)">
            开始入库
          </el-button>

          <!-- 处理中按钮 - 当任务正在运行时显示 -->
          <el-button v-if="scope.row.status === 'running'" type="warning" size="small" :loading="true" disabled>
            处理中...
          </el-button>

          <!-- 重新解析按钮 - 已入库成功时 -->
          <el-button v-if="scope.row.status === 'completed'" type="success" size="small"
            @click="reprocessFile(scope.row)">
            重新解析
          </el-button>
          <!-- 重新处理按钮 - 失败时 -->
          <el-button v-if="scope.row.status === 'failed'" type="warning" size="small" @click="reprocessFile(scope.row)">
            重新处理
          </el-button>

          <el-button 
            v-if="!scope.row.deleting" 
            type="danger" 
            size="small" 
            @click="deleteFile(scope.row)">
            删除
          </el-button>
          <el-button 
            v-else 
            type="danger" 
            size="small" 
            :loading="true" 
            disabled>
            删除中...
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="pagination-wrapper">
      <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[10, 20, 50, 100]"
        :total="total" layout="total, sizes, prev, pager, next, jumper" @size-change="handleSizeChange"
        @current-change="handleCurrentChange" />
    </div>
    </el-card>

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

  <!-- 文件上传对话框 -->
  <el-dialog v-model="showUploadDialog" title="上传文件" width="600px" :close-on-click-modal="false">
    <el-upload ref="uploadRef" class="upload-area" drag multiple :auto-upload="false" :on-change="onFileChange"
      :file-list="uploadFileList" accept=".pdf,.doc,.docx,.xls,.xlsx,.md,.txt">
      <el-icon class="el-icon--upload"><upload-filled /></el-icon>
      <div class="el-upload__text">
        将文件拖到此处，或<em>点击上传</em>
      </div>
      <template #tip>
        <div class="el-upload__tip">
          支持 PDF、Word、Excel、Markdown、Text 格式文件，单个文件不超过 50MB
        </div>
      </template>
    </el-upload>

    <template #footer>
      <span class="dialog-footer">
        <el-button @click="clearFileList">清空列表</el-button>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" @click="submitUpload" :loading="uploading">
          开始上传
        </el-button>
      </span>
    </template>
  </el-dialog>

  <!-- 文件预览对话框 -->
  <el-dialog v-model="showPreviewDialog" :title="`文件预览 - ${previewFileData?.file_name || '未知文件'}`" width="80%"
    :close-on-click-modal="false">
    <div class="file-preview">
      <div v-if="previewFileData?.file_type === 'pdf'" class="pdf-preview">
        <!-- PDF预览区域 -->
        <div class="pdf-preview-header"
          style="margin-bottom: 15px; padding: 10px; background-color: #f5f5f5; border-radius: 4px;">
          <el-alert title="PDF预览提示" type="info" :closable="false" show-icon>
            <template #default>
              <p>由于浏览器安全策略，PDF无法在页面中直接预览。请使用下方按钮查看或下载PDF文件。</p>
            </template>
          </el-alert>
          <div style="margin-top: 10px;">
            <el-button type="primary" @click="openPdfInNewWindow(previewFileData.file_path)"
              style="margin-right: 10px;">
              在新窗口打开
            </el-button>
            <el-button type="success" @click="downloadPdf(previewFileData.file_path, previewFileData.file_name)">
              下载PDF
            </el-button>
          </div>
        </div>

        <!-- PDF预览占位符 -->
        <div class="pdf-preview-placeholder"
          style="text-align: center; padding: 60px 20px; background-color: #fafafa; border: 2px dashed #ddd; border-radius: 8px;">
          <el-icon size="64" style="color: #909399; margin-bottom: 20px;">
            <Document />
          </el-icon>
          <h3 style="color: #606266; margin-bottom: 15px;">PDF文件预览</h3>
          <p style="color: #909399; margin-bottom: 20px;">
            文件名：{{ previewFileData?.file_name || '未知文件' }}
          </p>
          <p style="color: #909399; font-size: 14px;">
            点击上方按钮在新窗口中打开或下载PDF文件
          </p>
        </div>

        <!-- 注释掉iframe预览，避免X-Frame-Options错误 -->
        <!--
          <iframe 
            :src="getFileUrl(previewFileData.file_path)" 
            width="100%" 
            height="600"
            style="border: 1px solid #ddd; border-radius: 4px;"
            title="PDF预览"
          ></iframe>
          -->
      </div>
      <div v-else-if="['doc', 'docx'].includes(previewFileData?.file_type)" class="doc-preview">
        <div class="preview-placeholder">
          <el-icon size="48">
            <Document />
          </el-icon>
          <p>Word 文档预览功能开发中...</p>
        </div>
      </div>
      <div v-else-if="['xls', 'xlsx'].includes(previewFileData?.file_type)" class="excel-preview">
        <div class="preview-placeholder">
          <el-icon size="48">
            <Grid />
          </el-icon>
          <p>Excel 表格预览功能开发中...</p>
        </div>
      </div>
      <div v-else-if="previewFileData?.file_type === 'md'" class="markdown-preview">
        <div class="markdown-content" v-html="renderMarkdown(previewFileData.parsed_content || '暂无内容')"></div>
      </div>
      <div v-else class="text-preview">
        <pre>{{ previewFileData?.parsed_content || '文件内容为空' }}</pre>
      </div>

      <!-- 调试信息 -->
      <div v-if="!previewFileData?.parsed_content && !previewFileData?.file_path" class="debug-info"
        style="margin-top: 20px; padding: 20px; background-color: #f5f5f5; border-radius: 4px;">
        <h4>调试信息</h4>
        <p><strong>文件类型:</strong> {{ previewFileData?.file_type || '未知' }}</p>
        <p><strong>文件路径:</strong> {{ previewFileData?.file_path || '无' }}</p>
        <p><strong>完整URL:</strong> {{ getFileUrl(previewFileData?.file_path) || '无' }}</p>
        <p><strong>解析内容:</strong> {{ previewFileData?.parsed_content || '无' }}</p>
        <p><strong>解析状态:</strong> {{ previewFileData?.parsing_status || '未知' }}</p>
        <p><strong>RAG状态:</strong> {{ previewFileData?.rag_status || '未知' }}</p>
      </div>

      <!-- 文件信息 -->
      <div v-if="previewFileData?.file_path || previewFileData?.parsed_content" class="file-info"
        style="margin-top: 20px; padding: 20px; background-color: #f0f9ff; border-radius: 4px; border-left: 4px solid #409eff;">
        <h4>文件信息</h4>
        <p><strong>文件名:</strong> {{ previewFileData?.file_name || '未知' }}</p>
        <p><strong>文件类型:</strong> {{ previewFileData?.file_type || '未知' }}</p>
        <p><strong>文件路径:</strong> {{ previewFileData?.file_path || '无' }}</p>
        <p><strong>完整URL:</strong> <a :href="getFileUrl(previewFileData?.file_path)" target="_blank"
            style="color: #409eff;">{{ getFileUrl(previewFileData?.file_path) || '无' }}</a></p>

      </div>
    </div>
  </el-dialog>

</template>

<script setup>
import { ref, reactive, computed, onMounted, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Grid, UploadFilled, Star, Warning, DataLine, Clock, Connection, Plus, User } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { useProjectStore } from '@/stores/project'
import {
  getProjectKnowledgeFiles,
  uploadKnowledgeFile,
  deleteKnowledgeFile,
  reprocessKnowledgeFile,
  startKnowledgeFileProcessing,
  getKnowledgeFileTaskStatus
} from '@/api/projects'

// 路由实例
const router = useRouter()

// 项目状态管理
const projectStore = useProjectStore()

// 响应式数据
const loading = ref(false)
const uploading = ref(false)
const showUploadDialog = ref(false)
const showPreviewDialog = ref(false)
const searchQuery = ref('')
const statusFilter = ref('')
const ragFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)
const viewMode = ref('grid') // 默认使用网格视图

// 文件列表
const files = ref([])

// 上传相关
const uploadRef = ref(null)
const uploadFileList = ref([])

// 预览相关
const previewFileData = ref(null)

// 任务状态跟踪
const taskStatusIntervals = ref(new Map()) // 存储任务状态查询的定时器

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

const filteredFiles = computed(() => {
  // 确保 files.value 是数组
  let result = Array.isArray(files.value) ? files.value : []

  if (statusFilter.value) {
    result = result.filter(file => file.parsing_status === statusFilter.value)
  }

  if (ragFilter.value) {
    result = result.filter(file => file.rag_status === ragFilter.value)
  }

  if (searchQuery.value) {
    result = result.filter(file =>
      file.file_name.toLowerCase().includes(searchQuery.value.toLowerCase())
    )
  }

  return result
})



// 格式化日期
const formatDate = (date) => {
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

// 文件类型图标映射
const getFileIconSrc = (fileType) => {
  const iconMap = {
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
  return iconMap[fileType] || '/src/assert/icons/Swagger.svg'
}

// 获取文件图标包装器样式类
const getFileIconWrapperClass = (fileType) => {
  const wrapperClassMap = {
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
  return wrapperClassMap[fileType] || 'icon-other'
}

// 处理搜索
const handleSearch = () => {
  currentPage.value = 1
  loadFiles()
}

// 预览文件
const previewFile = (file) => {
  console.log('预览文件数据:', file)
  console.log('文件类型:', file.file_type)
  console.log('文件路径:', file.file_path)
  console.log('解析内容:', file.parsed_content)

  // 确保预览数据正确设置
  previewFileData.value = {
    ...file,
    // 确保字段名一致
    file_type: file.file_type,
    file_path: file.file_path,
    parsed_content: file.parsed_content,
    file_name: file.file_name
  }

  showPreviewDialog.value = true
}

// 开始处理文件
const startProcessing = async (file) => {
  try {
    await ElMessageBox.confirm(
      `确认开始处理文件 "${file.file_name}" 吗？\n这将开始解析文件内容并执行RAG入库操作。`,
      '确认开始处理',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 设置加载状态
    file.processingLoading = true

    // 调用开始处理API
    const response = await startKnowledgeFileProcessing(projectStore.currentProjectId, file.id)

    if (response.success && response.data?.task_id) {
      // 保存任务ID到文件对象
      file.task_id = response.data.task_id

      // 根据后端返回的状态设置前端状态
      if (response.data.status === 'running') {
        file.status = 'running'
        file.task_message = response.data.message || '任务处理已开始'
      } else {
        // 如果后端没有返回running状态，保持为uploaded状态
        file.status = 'uploaded'
      }

      // 开始跟踪任务状态
      startTaskStatusTracking(file)

      ElMessage.success(response.data.message || '文件处理已开始，正在解析中...')
      // 成功启动任务后，保持加载状态，直到任务完成
    } else {
      ElMessage.error(response.error?.message || '开始处理失败')
      // 如果失败，重置加载状态
      file.processingLoading = false
    }
  } catch (error) {
    console.error('开始处理文件失败:', error)
    if (error.response?.data?.error?.message) {
      ElMessage.error(error.response.data.error.message)
    } else if (error.message) {
      ElMessage.error(error.message)
    } else {
      ElMessage.error('开始处理失败')
    }
    // 如果出错，重置加载状态
    file.processingLoading = false
  }
}

// 重新处理文件
const reprocessFile = async (file) => {
  try {
    await ElMessageBox.confirm(
      `确认重新处理文件 "${file.file_name}" 吗？`,
      '确认操作',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    const response = await reprocessKnowledgeFile(projectStore.currentProjectId, file.id)

    if (response.success && response.data?.task_id) {
      // 保存任务ID到文件对象
      file.task_id = response.data.task_id

      // 根据后端返回的状态设置前端状态
      if (response.data.status === 'running') {
        file.status = 'running'
        file.task_message = response.data.message || '任务处理已开始'
      } else {
        // 如果后端没有返回running状态，保持为uploaded状态
        file.status = 'uploaded'
      }

      file.error_message = ''

      // 开始跟踪任务状态
      startTaskStatusTracking(file)

      ElMessage.success(response.data.message || '文件已加入重新处理队列')
    } else {
      ElMessage.error(response.error?.message || '重新处理失败')
    }
  } catch (error) {
    if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else if (error.message) {
      ElMessage.error(error.message)
    } else {
      ElMessage.error('重新处理失败')
    }
  }
}

// 删除文件
const deleteFile = async (file) => {
  try {
    await ElMessageBox.confirm(
      `删除后该文档将无法用于 AI 用例生成，确认删除文件 "${file.file_name}" 吗？`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 调用删除API，返回任务ID
    const response = await deleteKnowledgeFile(projectStore.currentProjectId, file.id)
    
    // 如果返回了task_id，说明是异步删除，需要跟踪任务状态
    if (response.task_id) {
      file.delete_task_id = response.task_id
      file.deleting = true
      file.delete_message = response.message || '正在删除文件...'
      
      // 开始跟踪删除任务状态
      startDeleteTaskTracking(file)
      
      ElMessage.info(response.message || '文件已加入删除队列，正在后台删除...')
    } else {
      // 同步删除（兼容旧版本）
      ElMessage.success('文件删除成功')
      await loadFiles()
    }
  } catch (error) {
    // 用户取消操作，不显示错误信息
    if (error === 'cancel' || error?.action === 'cancel') {
      return
    }
    
    if (error.response?.status === 202) {
      // 202 Accepted 表示异步任务已启动
      const response = error.response.data || {}
      if (response.task_id) {
        file.delete_task_id = response.task_id
        file.deleting = true
        file.delete_message = response.message || '正在删除文件...'
        startDeleteTaskTracking(file)
        ElMessage.info(response.message || '文件已加入删除队列，正在后台删除...')
      }
    } else if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else if (error.message && error.message !== 'cancel') {
      ElMessage.error(error.message)
    } else if (error !== 'cancel') {
      ElMessage.error('删除文件失败')
    }
  }
}

// 开始跟踪删除任务状态
const startDeleteTaskTracking = (file) => {
  if (!file.delete_task_id) return

  // 清除之前的定时器
  if (taskStatusIntervals.value.has(file.delete_task_id)) {
    clearInterval(taskStatusIntervals.value.get(file.delete_task_id))
  }

  // 设置定时查询任务状态
  const interval = setInterval(async () => {
    try {
      const status = await getKnowledgeFileTaskStatus(projectStore.currentProjectId, file.delete_task_id)

      if (status.success) {
        let taskData = status.data || status

        if (taskData.status === 'completed') {
          // 删除成功
          file.deleting = false
          file.delete_message = taskData.message || '删除完成'

          // 清除定时器
          clearInterval(interval)
          taskStatusIntervals.value.delete(file.delete_task_id)

          // 刷新文件列表
          await loadFiles()
          ElMessage.success(`文件 "${file.file_name}" 删除成功`)

        } else if (taskData.status === 'failed') {
          // 删除失败
          file.deleting = false
          file.delete_message = taskData.message || '删除失败'

          // 清除定时器
          clearInterval(interval)
          taskStatusIntervals.value.delete(file.delete_task_id)

          // 刷新文件列表
          await loadFiles()
          ElMessage.error(`文件 "${file.file_name}" 删除失败: ${taskData.message || '未知错误'}`)

        } else if (taskData.status === 'running' || taskData.status === 'PROGRESS') {
          // 删除进行中
          file.deleting = true
          file.delete_message = taskData.message || taskData.meta?.message || '正在删除...'
        }
      } else {
        // 查询失败
        console.error('查询删除任务状态失败:', status)
        clearInterval(interval)
        taskStatusIntervals.value.delete(file.delete_task_id)
        file.deleting = false
      }
    } catch (error) {
      console.error('查询删除任务状态失败:', error)
      clearInterval(interval)
      taskStatusIntervals.value.delete(file.delete_task_id)
      file.deleting = false
    }
  }, 2000) // 每2秒查询一次

  // 保存定时器引用
  taskStatusIntervals.value.set(file.delete_task_id, interval)
}

// 文件选择变化
const onFileChange = (file, fileList) => {
  console.log('文件变化:', file, fileList)
  uploadFileList.value = fileList || []
}

// 提交上传
const submitUpload = async () => {
  console.log('当前文件列表:', uploadFileList.value)

  if (!Array.isArray(uploadFileList.value) || uploadFileList.value.length === 0) {
    ElMessage.warning('请选择要上传的文件')
    return
  }

  uploading.value = true

  try {
    // 使用新的API函数上传文件
    for (const file of uploadFileList.value) {
      const formData = new FormData()
      formData.append('file', file.raw || file)

      // 使用统一的API函数上传文件
      const response = await uploadKnowledgeFile(projectStore.currentProjectId, formData)

      if (response.success) {
        ElMessage.success(`文件 ${file.name} 上传成功，请点击"开始入库"按钮开始处理`)
        console.log('上传成功:', response)
      } else {
        ElMessage.error(`文件 ${file.name} 上传失败: ${response.error?.message || '未知错误'}`)
      }
    }

    // 刷新文件列表
    await loadFiles()

    // 关闭对话框
    showUploadDialog.value = false

  } catch (error) {
    ElMessage.error(error.response?.data?.error?.message || '上传过程中发生错误')
  } finally {
    uploading.value = false
    // 清空文件列表
    uploadFileList.value = []
  }
}

// 清空文件列表
const clearFileList = () => {
  uploadFileList.value = []
  console.log('文件列表已清空')
}

// 开始跟踪任务状态
const startTaskStatusTracking = (file) => {
  if (!file.task_id) return

  // 清除之前的定时器
  if (taskStatusIntervals.value.has(file.task_id)) {
    clearInterval(taskStatusIntervals.value.get(file.task_id))
  }

  // 设置定时查询任务状态
  const interval = setInterval(async () => {
    try {
      const status = await getKnowledgeFileTaskStatus(projectStore.currentProjectId, file.task_id)

      if (status.success) {
        // 获取任务数据，优先使用 status.data
        let taskData = status.data || status

        // 根据后端的status字段判断任务状态
        if (taskData.status === 'completed') {
          // 任务成功完成
          file.status = 'completed'
          file.task_message = taskData.message || '处理完成'

          // 重置加载状态
          file.processingLoading = false

          // 清除定时器
          clearInterval(interval)
          taskStatusIntervals.value.delete(file.task_id)

          // 刷新文件列表以获取最新状态
          await loadFiles()
          ElMessage.success(`文件 "${file.file_name}" 处理完成`)

        } else if (taskData.status === 'failed') {
          // 任务失败
          file.status = 'failed'
          file.task_message = taskData.message || '处理失败'
          file.error_message = taskData.message || '处理失败'

          // 重置加载状态
          file.processingLoading = false

          // 清除定时器
          clearInterval(interval)
          taskStatusIntervals.value.delete(file.task_id)

          // 刷新文件列表以获取最新状态
          await loadFiles()
          ElMessage.error(`文件 "${file.file_name}" 处理失败`)

        } else if (taskData.status === 'running') {
          // 任务还在运行中
          file.status = 'running'
          file.task_message = taskData.message || '正在处理...'

          // 确保进度条显示
          console.log(`任务运行中: ${file.file_name}, 后端状态: ${taskData.status}, 前端状态: ${file.status}`)
        }
      } else {
        // 查询失败或状态异常
        console.error('查询任务状态失败:', status)
        // 重置加载状态
        file.processingLoading = false
        // 如果查询失败，停止跟踪
        clearInterval(interval)
        taskStatusIntervals.value.delete(file.task_id)
      }
    } catch (error) {
      console.error('查询任务状态失败:', error)
      // 重置加载状态
      file.processingLoading = false
      // 如果查询失败，停止跟踪
      clearInterval(interval)
      taskStatusIntervals.value.delete(file.task_id)
    }
  }, 2000) // 每2秒查询一次

  // 保存定时器引用
  taskStatusIntervals.value.set(file.task_id, interval)
}

// 停止任务状态跟踪
const stopTaskStatusTracking = (taskId) => {
  if (taskStatusIntervals.value.has(taskId)) {
    clearInterval(taskStatusIntervals.value.get(taskId))
    taskStatusIntervals.value.delete(taskId)
  }
}

// 渲染Markdown
const renderMarkdown = (content) => {
  if (!content) return '暂无内容'

  // 简单的Markdown渲染（实际项目中建议使用专业的Markdown渲染库）
  return content
    .replace(/^### (.*$)/gim, '<h3>$1</h3>')
    .replace(/^## (.*$)/gim, '<h2>$1</h2>')
    .replace(/^# (.*$)/gim, '<h1>$1</h1>')
    .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
    .replace(/\*(.*)\*/gim, '<em>$1</em>')
    .replace(/\n/gim, '<br>')
}

// 获取文件URL
const getFileUrl = (filePath) => {
  if (!filePath) return ''

  // 构建完整的文件URL
  // 如果filePath已经是完整URL，直接返回
  if (filePath.startsWith('http://') || filePath.startsWith('https://')) {
    return filePath
  }

  // 构建相对于后端的文件URL
  const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
  return `${backendUrl}/media/${filePath}`
}

// 分页处理
const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  loadFiles()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadFiles()
}

// 加载文件列表
const loadFiles = async () => {
  if (!selectedProject.value) return

  loading.value = true
  try {
    const response = await getProjectKnowledgeFiles(projectStore.currentProjectId, {
      page: currentPage.value,
      page_size: pageSize.value
    })

    // 处理统一响应格式
    if (response && response.success && response.data) {
      if (response.data.items) {
        // 新的统一响应格式 - 使用 items 和 pagination
        files.value = response.data.items
        total.value = response.data.pagination ? response.data.pagination.total : 0
      } else {
        // 非分页数据
        files.value = response.data
        total.value = Array.isArray(response.data) ? response.data.length : 0
      }
    } else {
      // 直接返回数组的情况
      files.value = Array.isArray(response) ? response : []
      total.value = files.value.length
    }
  } catch (error) {
    console.error('加载文件列表失败:', error)
    ElMessage.error('加载文件失败')
    files.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 初始化
onMounted(async () => {
  // 知识库管理页面不需要加载项目列表和验证项目
  // 这些逻辑应该在父组件或路由守卫中处理
})

// 组件卸载时清理定时器
onUnmounted(() => {
  // 清理所有任务状态跟踪定时器
  taskStatusIntervals.value.forEach((interval) => {
    clearInterval(interval)
  })
  taskStatusIntervals.value.clear()
})

// 监听项目选择变化
watch(selectedProject, async (newProjectId, oldProjectId) => {
  if (newProjectId && newProjectId !== oldProjectId) {
    currentPage.value = 1
    await loadFiles()
  } else if (!newProjectId) {
    files.value = []
    total.value = 0
  }
}, { immediate: true })

// 在新窗口打开PDF
const openPdfInNewWindow = (filePath) => {
  if (filePath) {
    const url = getFileUrl(filePath);
    window.open(url, '_blank');
  } else {
    ElMessage.warning('文件路径不存在，无法在新窗口打开。');
  }
};

// 下载PDF
const downloadPdf = async (filePath, fileName) => {
  if (filePath) {
    try {
      const url = getFileUrl(filePath);
      const response = await fetch(url);
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = fileName || 'document';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
      ElMessage.success('PDF下载成功！');
    } catch (error) {
      console.error('PDF下载失败:', error);
      ElMessage.error('PDF下载失败，请检查文件路径或网络。');
    }
  } else {
    ElMessage.warning('文件路径不存在，无法下载。');
  }
};

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

</script>

<style scoped>
.knowledge-base-container {
  margin: 0 auto;
}

/* 页面头部样式 */
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
  justify-content: space-between;
  align-items: center;
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

.upload-btn {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  backdrop-filter: blur(10px);
  font-weight: 500;
  padding: 12px 24px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.upload-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.upload-btn:disabled {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
  opacity: 0.6;
  cursor: not-allowed;
}

.knowledge-base-page {
  padding: 20px;
}

.table-container {
  background-color: #ffffff;
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  border: 1px solid #e4e7ed;
  overflow: hidden;
}

.custom-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 20px 24px;
  background-color: #fafafa;
  border-bottom: 1px solid #e4e7ed;
  margin-bottom: 0;
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

.files-card {
  margin-bottom: 20px;
  border-radius: 0 0 16px 16px;
  border-top: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
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

.layout-toggle {
  margin-left: 10px;
}

.file-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-name-text {
  cursor: pointer;
  color: #409eff;
}

.file-name-text:hover {
  text-decoration: underline;
}

.uploader-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.uploader-avatar {
  background-color: #409eff;
  color: white;
  font-weight: bold;
}

.upload-area {
  width: 100%;
}

.pagination-wrapper {
  margin-top: 20px;
  text-align: right;
}

.dialog-footer {
  text-align: right;
}

.file-preview {
  min-height: 400px;
}

.preview-placeholder {
  text-align: center;
  padding: 40px;
  color: #909399;
}

.preview-placeholder .el-icon {
  margin-bottom: 16px;
}

.markdown-content {
  padding: 20px;
  background-color: #fafafa;
  border-radius: 4px;
  line-height: 1.6;
}

.markdown-content h1,
.markdown-content h2,
.markdown-content h3 {
  margin-top: 20px;
  margin-bottom: 10px;
  color: #303133;
}

.markdown-content strong {
  font-weight: bold;
}

.markdown-content em {
  font-style: italic;
}

.text-preview pre {
  background-color: #f5f5f5;
  padding: 16px;
  border-radius: 4px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* 网格布局样式 */
.files-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 24px;
  padding: 8px;
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

.meta-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #f8f9fa;
  border-radius: 6px;
  transition: background-color 0.3s ease;
}

.meta-item .el-icon {
  margin-right: 8px;
  color: #409EFF;
  font-size: 16px;
}

.file-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  color: white;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  flex-shrink: 0;
}

.file-icon {
  width: 42px;
  height: 42px;
  filter: brightness(0) invert(1);
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

.icon-other {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.file-type-badge {
  margin-left: auto;
}

.file-card-content {
  flex: 1;
  margin-bottom: 15px;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 15px;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-height: 20px;
}

.file-meta {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 13px;
  color: #909399;
}

.meta-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #f8f9fa;
  border-radius: 6px;
  transition: background-color 0.3s ease;
}

.meta-item .el-icon {
  margin-right: 8px;
  color: #409EFF;
  font-size: 16px;
}

.uploader-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.uploader-avatar {
  background-color: #409eff;
  color: white;
  font-weight: bold;
  font-size: 12px;
}

/* 头部任务状态样式 */
.task-status-header {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  min-width: 140px;
  padding: 4px 0;
}

.task-waiting,
.task-processing,
.task-completed,
.task-failed,
.task-partial-success {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.task-waiting .el-tag {
  background: linear-gradient(135deg, #f0f2f5 0%, #e4e7ed 100%);
  border: 1px solid #d9dce0;
  color: #606266;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.task-completed .el-tag {
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border: 1px solid #7dd3fc;
  color: #0369a1;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(59, 130, 246, 0.1);
}

.task-failed .el-tag {
  background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
  border: 1px solid #fca5a5;
  color: #dc2626;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(239, 68, 68, 0.1);
}

.task-partial-success .el-tag {
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
  border: 1px solid #fcd34d;
  color: #d97706;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(245, 158, 11, 0.1);
}

.task-progress-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
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

.task-message-header {
  margin-top: 4px;
}

.task-message-header .el-tag {
  font-size: 11px;
  padding: 4px 8px;
  height: 22px;
  line-height: 14px;
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  border: 1px solid #f59e0b;
  color: #92400e;
  font-weight: 500;
}

.partial-status-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}

.tag-with-error {
  display: flex;
  align-items: center;
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

/* 删除中状态样式 */
.task-deleting {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.task-deleting-table {
  display: flex;
  align-items: center;
  gap: 10px;
}


/* 紧凑的标签样式 */
.compact-tag {
  font-size: 10px;
  padding: 2px 6px;
  height: 20px;
  line-height: 16px;
}

/* 卡片内容中的任务状态样式 */
.task-status {
  margin-top: 12px;
  padding: 12px;
  background-color: #f8f9fa;
  border-radius: 8px;
  border: 1px solid #e9ecef;
}

.task-waiting,
.task-processing,
.task-completed,
.task-failed {
  text-align: center;
}

.task-progress {
  margin-bottom: 8px;
}

.progress-text {
  font-size: 12px;
  color: #606266;
  text-align: center;
  display: block;
}

.task-message {
  margin-top: 8px;
}

.error-tooltip {
  display: inline-block;
  margin-left: 8px;
}

.error-icon {
  color: #f56c6c;
  cursor: help;
  font-size: 14px;
}

.file-card-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.action-btn {
  height: 36px;
  font-size: 14px;
  border-radius: 8px;
  transition: all 0.3s ease;
  position: relative;
}

/* .action-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.action-btn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
} */


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

@keyframes pulse {

  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.8;
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

/* 响应式设计 */
@media (max-width: 1400px) {
  .files-grid {
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
  }
}

@media (max-width: 1000px) {
  .files-grid {
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
  .knowledge-base-container {
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
    font-size: 18px;
  }

  .files-grid {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 16px 0;
  }

  .file-card {
    margin: 0 8px;
    min-height: 260px;
  }

  .file-card-actions {
    grid-template-columns: 1fr;
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

  .layout-toggle {
    margin-left: 0;
  }
}
</style>
