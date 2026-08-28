<template>
  <div class="page-wrapper">
    <div class="main-content">
      <el-page-header content="Web 测试项目列表" class="page-header">
        <template #icon>
          <BackButton to="/dashboard" />
        </template>
        <template #title />
        <template #extra>
          <el-button type="primary" @click="showCreateDialog = true">
            <el-icon><Plus /></el-icon>
            新建Web项目
          </el-button>
        </template>
      </el-page-header>

      <div v-loading="loading" class="projects-content">
        <el-empty v-if="!loading && projectList.length === 0" description="暂无 Web 测试项目" />

        <el-row v-else :gutter="20" class="project-cards">
          <el-col v-for="project in projectList" :key="project.id" :xs="24" :sm="12" :md="8" :lg="6">
            <el-card shadow="hover" class="project-card">
              <div class="card-header">
                <div class="project-icon web">
                  <el-icon><Monitor /></el-icon>
                </div>
                <h3 class="project-name">{{ project.name }}</h3>
              </div>
              <div class="card-body">
                <p class="project-desc">{{ project.description || '暂无描述' }}</p>
              </div>
              <div class="card-footer">
                <span class="project-meta">{{ formatDate(project.created_at) }}</span>
                <div class="footer-actions">
                  <el-button type="warning" size="small" @click="openEditDialog(project)">编辑</el-button>
                  <el-button type="primary" size="small" @click="enterProject(project)">进入工作区</el-button>
                </div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </div>

      <el-dialog v-model="showCreateDialog" title="新建 Web 项目" width="500px" :close-on-click-modal="false" @close="resetCreateForm">
        <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="80px">
          <el-form-item label="项目名称" prop="name">
            <el-input v-model="createForm.name" placeholder="请输入项目名称" maxlength="50" show-word-limit />
          </el-form-item>
          <el-form-item label="描述" prop="description">
            <el-input v-model="createForm.description" type="textarea" :rows="3" placeholder="请输入项目描述（选填）" maxlength="500" show-word-limit />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showCreateDialog = false">取消</el-button>
          <el-button type="primary" :loading="creating" @click="submitCreate">确定</el-button>
        </template>
      </el-dialog>

      <ProjectEditDialog
        v-model="showEditDialog"
        :project="editingProject"
        project-type-label="Web"
        :saving="updating"
        @save="submitEdit"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Monitor } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import ProjectEditDialog from '@/components/project/ProjectEditDialog.vue'
import { getProjects, createProject, updateProject } from '@/api/projects'
import { useProjectStore } from '@/stores/project'
import dayjs from 'dayjs'

const router = useRouter()
const projectStore = useProjectStore()

const loading = ref(false)
const creating = ref(false)
const updating = ref(false)
const projectList = ref([])
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const createFormRef = ref(null)
const editingProject = ref(null)

const createForm = reactive({ name: '', description: '' })

const createRules = {
  name: [
    { required: true, message: '请输入项目名称', trigger: 'blur' },
    { min: 2, max: 50, message: '项目名称长度在 2 到 50 个字符', trigger: 'blur' }
  ]
}

const formatDate = (date) => {
  if (!date) return '-'
  return dayjs(date).format('YYYY-MM-DD')
}

// 加载项目列表（严格携带 project_type: 'web'）
const loadProjects = async () => {
  loading.value = true
  try {
    const response = await getProjects({ project_type: 'web' })
    if (response?.success && response?.data) {
      projectList.value = response.data.items || (Array.isArray(response.data) ? response.data : [])
    } else {
      projectList.value = Array.isArray(response) ? response : []
    }
  } catch {
    ElMessage.error('加载项目列表失败')
    projectList.value = []
  } finally {
    loading.value = false
  }
}

const enterProject = async (project) => {
  try {
    await projectStore.setCurrentProject(project)
    router.push('/web-testing/create/requirements')
  } catch {
    ElMessage.error('设置项目失败，请重试')
  }
}

const openEditDialog = (project) => {
  editingProject.value = { ...project }
  showEditDialog.value = true
}

const submitEdit = async (data) => {
  if (!editingProject.value) return
  updating.value = true
  try {
    const response = await updateProject(editingProject.value.id, data)
    const responseData = response?.data && typeof response.data === 'object'
      ? response.data
      : (response || {})
    const updatedProject = { ...editingProject.value, ...data, ...responseData }
    const projectIndex = projectList.value.findIndex(item => item.id === updatedProject.id)
    if (projectIndex >= 0) projectList.value[projectIndex] = updatedProject

    if (projectStore.currentProject?.id === updatedProject.id) {
      try {
        await projectStore.setCurrentProject(updatedProject)
      } catch {
        // 项目更新已成功；即使当前项目偏好同步失败，也先更新本地状态。
        projectStore.currentProject = updatedProject
      }
    }

    showEditDialog.value = false
    ElMessage.success('项目更新成功')
    await loadProjects()
  } catch (error) {
    const detail = error.response?.data
    ElMessage.error(
      detail?.message || detail?.error?.message ||
      (typeof detail?.detail === 'string' ? detail.detail : null) ||
      '项目更新失败'
    )
  } finally {
    updating.value = false
  }
}

const submitCreate = async () => {
  if (!createFormRef.value) return
  await createFormRef.value.validate(async (valid) => {
    if (!valid) return
    creating.value = true
    try {
      await createProject({
        name: createForm.name.trim(),
        description: createForm.description?.trim() || '',
        project_type: 'web'
      })
      ElMessage.success('项目创建成功')
      showCreateDialog.value = false
      resetCreateForm()
      await loadProjects()
    } catch (error) {
      const d = error.response?.data
      ElMessage.error(d?.message || (typeof d?.detail === 'string' ? d.detail : null) || '创建项目失败')
    } finally {
      creating.value = false
    }
  })
}

const resetCreateForm = () => {
  createForm.name = ''
  createForm.description = ''
  createFormRef.value?.resetFields()
}

onMounted(() => loadProjects())
</script>

<style scoped>
.page-wrapper {
  min-height: 100%;
  background-color: var(--page-wrapper-bg);
  padding: 30px 0;
}

.main-content {
  max-width: 1200px;
  margin: 0 auto;
  background-color: var(--page-content-bg);
  box-shadow: var(--page-content-shadow);
  border-radius: 12px;
  padding: 30px;
}

@media (max-width: 1200px) {
  .main-content {
    width: 95%;
  }
}

.page-header {
  margin-bottom: 28px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.page-header :deep(.el-page-header__content) {
  margin-left: 0;
}

.page-header :deep(.el-page-header__title) {
  display: none;
}

.projects-content {
  min-height: 300px;
}

.project-cards {
  margin-top: 0;
}

.project-card {
  border-radius: 12px;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  display: flex;
  flex-direction: column;
  height: 100%;
}

.project-card:hover {
  transform: translateY(-10px);
  box-shadow: var(--page-card-hover-shadow);
}

.project-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 24px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 0;
}

.project-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 24px;
}

.project-icon.web {
  background-color: #e8f5e9;
  color: #67c23a;
}

.project-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--app-text-primary);
  margin: 0;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-body {
  flex: 1;
  margin: 16px 0 20px;
  min-height: 0;
}

.project-desc {
  font-size: 14px;
  color: var(--app-text-muted);
  line-height: 1.6;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  border-top: 1px solid var(--app-border-light);
}

.footer-actions {
  display: flex;
  gap: 8px;
}

.project-meta {
  font-size: 13px;
  color: var(--app-text-muted);
}
</style>
