<template>
  <div class="project-detail-page">
    <div class="page-header">
      <div class="header-left">
        <BackButton @click="(e) => { e.preventDefault(); goBack() }" />
        <h1>{{ project?.name || '项目详情' }}</h1>
      </div>
      <div class="header-actions">
        <el-button type="warning" @click="editProject">编辑项目</el-button>
        <el-button type="danger" @click="deleteProject">删除项目</el-button>
      </div>
    </div>
    
    <!-- 项目概览卡片 -->
    <el-card class="overview-card">
      <template #header>
        <div class="card-header">
          <span>项目概览</span>
          <div class="header-actions">
            <el-button type="primary" @click="editProject">编辑项目</el-button>
            <el-button type="success" @click="goToKnowledgeBase">知识库管理</el-button>
          </div>
        </div>
      </template>
      
      <el-row :gutter="20">
        <el-col :span="8">
          <div class="stat-item">
            <div class="stat-number">{{ projectStats.total_files || 0 }}</div>
            <div class="stat-label">知识库文件</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-item">
            <div class="stat-number">{{ projectStats.total_members || 0 }}</div>
            <div class="stat-label">项目成员</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-item">
            <div class="stat-number">{{ projectStats.parsed_files || 0 }}</div>
            <div class="stat-label">已解析文件</div>
          </div>
        </el-col>
      </el-row>
    </el-card>
    
    <!-- 项目信息卡片 -->
    <el-card class="info-card">
      <template #header>
        <span>项目信息</span>
      </template>
      
      <el-descriptions :column="2" border>
        <el-descriptions-item label="项目名称">{{ project?.name }}</el-descriptions-item>
        <el-descriptions-item label="项目状态">
          <el-tag :type="getStatusType(project?.status)">
            {{ getStatusText(project?.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="项目类型">
          <el-tag type="info">{{ getTypeText(project?.project_type) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="版本">{{ project?.version }}</el-descriptions-item>
        <el-descriptions-item label="负责人">
          <div class="owner-info">
            <el-avatar :size="24" class="owner-avatar">
              {{ project?.owner_username?.charAt(0)?.toUpperCase() }}
            </el-avatar>
            <span>{{ project?.owner_username }}</span>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="创建者">
          <div class="creator-info">
            <el-avatar :size="24" class="creator-avatar">
              {{ project?.created_by_username?.charAt(0)?.toUpperCase() }}
            </el-avatar>
            <span>{{ project?.created_by_username }}</span>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间" :span="2">
          {{ formatDate(project?.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="最后更新" :span="2">
          {{ formatDate(project?.updated_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="项目描述" :span="2">
          {{ project?.description || '暂无描述' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
    
    <!-- 快速操作卡片 -->
    <el-card class="actions-card">
      <template #header>
        <span>快速操作</span>
      </template>
      
      <div class="quick-actions">
        <el-button type="primary" icon="Upload" @click="goToKnowledgeBase">
          上传文档
        </el-button>
        <el-button type="success" icon="Document" @click="goToKnowledgeBase">
          查看知识库
        </el-button>
        <el-button type="warning" icon="Setting" @click="manageMembers">
          管理成员
        </el-button>
        <el-button type="info" icon="Connection" @click="manageEnvironments">
          环境配置
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BackButton from '@/components/BackButton.vue'
import { useProjectStore } from '@/stores/project'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { getProject, getProjectStatistics } from '@/api/projects'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = route.params.id

// 响应式数据
const project = ref(null)
const projectStats = ref({})
const loading = ref(false)

// 获取项目详情
const loadProject = async () => {
  loading.value = true
  try {
    const response = await getProject(projectId)
    project.value = response
  } catch (error) {
    console.error('加载项目详情失败:', error)
    ElMessage.error('加载项目详情失败')
  } finally {
    loading.value = false
  }
}

// 获取项目统计
const loadProjectStats = async () => {
  try {
    const response = await getProjectStatistics(projectId)
    // 处理统一响应格式
    if (response && response.success && response.data) {
      projectStats.value = response.data
    } else {
      projectStats.value = response
    }
  } catch (error) {
    console.error('加载项目统计失败:', error)
  }
}

// 编辑项目（当前无独立编辑页，暂用项目列表）
const editProject = () => {
  router.push('/project/project-list')
}

// 导航到知识库管理：先设为当前项目，再跳转扁平路由
const goToKnowledgeBase = async () => {
  if (project.value) {
    await projectStore.setCurrentProject(project.value)
  }
  router.push('/project/knowledge-base')
}

// 管理成员（当前无独立成员页，暂用项目列表）
const manageMembers = () => {
  router.push('/project/project-list')
}

// 环境配置：先设为当前项目，再跳转扁平路由 /project/environments
const manageEnvironments = async () => {
  if (project.value) {
    await projectStore.setCurrentProject(project.value)
  }
  router.push('/project/environments')
}

// 获取状态类型
const getStatusType = (status) => {
  const statusMap = {
    active: 'success',
    completed: 'info',
    paused: 'warning',
    cancelled: 'danger',
    archived: 'info'
  }
  return statusMap[status] || 'info'
}

// 获取状态文本
const getStatusText = (status) => {
  const statusMap = {
    active: '进行中',
    completed: '已完成',
    paused: '已暂停',
    cancelled: '已取消',
    archived: '已归档'
  }
  return statusMap[status] || status
}

// 获取项目类型文本
const getTypeText = (type) => {
  const typeMap = {
    web: 'Web应用',
    mobile: '移动应用',
    api: 'API服务',
    desktop: '桌面应用',
    other: '其他'
  }
  return typeMap[type] || type
}

// 格式化日期
const formatDate = (date) => {
  if (!date) return '-'
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

// 返回上一页
const goBack = () => {
  router.go(-1)
}

// 删除项目
const deleteProject = async () => {
  try {
    await ElMessageBox.confirm(
      `确认删除项目 "${project.value?.name}" 吗？该操作不可恢复，项目下所有测试数据将被清空。`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // The original code had deleteProjectAPI, but it's not imported.
    // Assuming it's a placeholder for a future API call or removed.
    // For now, we'll just show an error message.
    ElMessage.error('项目删除功能待实现')
    // await deleteProjectAPI(projectId)
    // ElMessage.success('项目删除成功')
    // router.push('/projects')
  } catch (error) {
    if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else if (error.message) {
      ElMessage.error(error.message)
    }
  }
}

// 初始化
onMounted(() => {
  if (projectId) {
    loadProject()
    loadProjectStats()
  }
})
</script>

<style scoped>
.project-detail-page {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.page-header h1 {
  margin: 0;
  color: #303133;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.overview-card,
.info-card,
.actions-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.stat-item {
  text-align: center;
  padding: 20px;
}

.stat-number {
  font-size: 32px;
  font-weight: bold;
  color: #409eff;
  margin-bottom: 8px;
}

.stat-label {
  color: #606266;
  font-size: 14px;
}

.owner-info,
.creator-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.owner-avatar,
.creator-avatar {
  background-color: #409eff;
  color: white;
  font-weight: bold;
}

.quick-actions {
  display: flex;
  gap: 15px;
  flex-wrap: wrap;
}

.quick-actions .el-button {
  min-width: 120px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }
  
  .header-actions {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }
  
  .quick-actions {
    flex-direction: column;
  }
  
  .quick-actions .el-button {
    width: 100%;
  }
}
</style>
