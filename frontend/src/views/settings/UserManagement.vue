<template>
  <div class="user-management">
    <el-page-header content="用户管理" class="page-header">
      <template #icon><BackButton to="/settings" /></template>
      <template #title />
      <template #extra><el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>创建账号</el-button></template>
    </el-page-header>

    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="search" clearable placeholder="搜索用户名或邮箱" @keyup.enter="loadUsers(1)" @clear="loadUsers(1)">
          <template #append><el-button @click="loadUsers(1)">搜索</el-button></template>
        </el-input>
      </div>
      <el-table v-loading="loading" :data="users" row-key="id">
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }"><el-tag :type="roleTagType(row)">{{ roleLabel(row) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '禁用' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canEdit(row)" type="primary" link @click="openEdit(row)">编辑/重置密码</el-button>
            <el-button v-if="canAssignProjects(row)" type="primary" link @click="openProjects(row)">分配项目</el-button>
            <span v-if="!canEdit(row) && !canAssignProjects(row)" class="readonly-note">受保护账号</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10, 20, 50, 100]" layout="total, sizes, prev, pager, next" @current-change="loadUsers" @size-change="loadUsers(1)" />
      </div>
    </el-card>

    <el-dialog v-model="editorVisible" :title="editingUser ? '修改账号' : '创建账号'" width="520px" :close-on-click-modal="false" @closed="resetEditor">
      <el-alert v-if="editingUser" title="密码留空则不修改；本人、超级管理员及管理员账号受服务端权限保护。" type="info" :closable="false" class="dialog-alert" />
      <el-form ref="editorRef" :model="editor" :rules="rules" label-width="90px">
        <el-form-item label="用户名" prop="username"><el-input v-model.trim="editor.username" autocomplete="off" /></el-form-item>
        <el-form-item label="邮箱" prop="email"><el-input v-model.trim="editor.email" autocomplete="off" /></el-form-item>
        <el-form-item :label="editingUser ? '新密码' : '密码'" prop="password"><el-input v-model="editor.password" type="password" show-password autocomplete="new-password" :placeholder="editingUser ? '留空不修改' : '至少 8 个字符'" /></el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="editor.role" :disabled="!canChangeRole"><el-option label="管理员" value="admin" /><el-option label="普通用户" value="user" /></el-select>
        </el-form-item>
        <el-form-item v-if="editingUser" label="账号状态"><el-switch v-model="editor.is_active" :disabled="!canChangeStatus" active-text="启用" inactive-text="禁用" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveUser">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="projectsVisible" :title="`分配项目：${projectUser?.username || ''}`" width="620px" :close-on-click-modal="false" @closed="resetProjectDialog">
      <el-alert title="仅普通用户需要项目分配；管理员默认拥有全部项目。" type="info" :closable="false" class="dialog-alert" />
      <el-alert v-if="projectLoadError" :title="projectLoadError" type="error" :closable="false" class="dialog-alert"><template #default><el-button link type="primary" @click="retryProjects">重新加载</el-button></template></el-alert>
      <el-checkbox-group v-model="selectedProjectIds" v-loading="projectsLoading" class="project-checks">
        <el-checkbox v-for="project in allProjects" :key="project.id" :label="project.id">{{ project.name }}</el-checkbox>
      </el-checkbox-group>
      <el-empty v-if="!projectsLoading && !allProjects.length" description="暂无可分配项目" />
      <template #footer><el-button @click="projectsVisible = false">取消</el-button><el-button type="primary" :loading="savingProjects" :disabled="projectsLoading || !projectAssignmentsReady" @click="saveProjects">保存分配</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import { usersApi } from '@/api/users'
import { getProjects } from '@/api/projects'
import { useAuthStore } from '@/stores/auth'
import { canEditManagedUser, isSuperuser } from '@/utils/accessControl'

const authStore = useAuthStore()
const users = ref([])
const loading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const editorVisible = ref(false)
const editorRef = ref(null)
const editingUser = ref(null)
const saving = ref(false)
const projectsVisible = ref(false)
const projectUser = ref(null)
const allProjects = ref([])
const selectedProjectIds = ref([])
const projectsLoading = ref(false)
const savingProjects = ref(false)
const projectAssignmentsReady = ref(false)
const projectLoadError = ref('')
let projectDialogVersion = 0
const editor = reactive({ username: '', email: '', password: '', role: 'user', is_active: true })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '请输入有效邮箱', trigger: 'blur' }],
  password: [{ validator: (_rule, value, callback) => (!value || value.length >= 8 ? callback() : callback(new Error('密码至少 8 个字符'))), trigger: 'blur' }]
}
const canChangeRole = computed(() => isSuperuser(authStore.user) && (!editingUser.value || (!editingUser.value.is_superuser && String(editingUser.value.id) !== String(authStore.user?.id))))
const canChangeStatus = computed(() => Boolean(editingUser.value && canEdit(editingUser.value)))

const unwrap = (response) => response?.data?.data ?? response?.data ?? response ?? {}
const roleLabel = (item) => item.is_superuser ? '超级管理员' : item.role === 'admin' || item.is_staff ? '管理员' : '普通用户'
const roleTagType = (item) => item.is_superuser ? 'danger' : (item.role === 'admin' || item.is_staff ? 'warning' : 'info')
const canEdit = (item) => canEditManagedUser(authStore.user, item)
const canAssignProjects = (item) => canEdit(item) && !item.is_staff && !item.is_superuser

async function loadUsers(targetPage = page.value) {
  page.value = targetPage
  loading.value = true
  try {
    const data = unwrap(await usersApi.listManagedUsers({ page: page.value, page_size: pageSize.value, search: search.value.trim() }))
    users.value = Array.isArray(data.items) ? data.items : []
    total.value = Number(data.pagination?.total || 0)
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '加载用户列表失败')
  } finally { loading.value = false }
}

function resetEditor() {
  editingUser.value = null
  Object.assign(editor, { username: '', email: '', password: '', role: 'user', is_active: true })
  editorRef.value?.clearValidate()
}
function openCreate() { resetEditor(); editorVisible.value = true }
function openEdit(item) {
  if (!canEdit(item)) return
  editingUser.value = item
  Object.assign(editor, { username: item.username || '', email: item.email || '', password: '', role: item.role === 'admin' || item.is_staff ? 'admin' : 'user', is_active: item.is_active !== false })
  editorVisible.value = true
}
async function saveUser() {
  try {
    await editorRef.value?.validate()
    saving.value = true
    const payload = { username: editor.username, email: editor.email }
    if (!editingUser.value || canChangeRole.value) payload.role = editor.role
    if (editor.password) payload.password = editor.password
    if (editingUser.value) {
      if (canChangeStatus.value) payload.is_active = editor.is_active
      await usersApi.updateManagedUser(editingUser.value.id, payload)
      ElMessage.success('账号已更新')
    } else {
      if (!editor.password) { ElMessage.error('创建账号必须设置密码'); return }
      payload.is_active = editor.is_active
      await usersApi.createManagedUser(payload)
      ElMessage.success('账号已创建')
    }
    editorVisible.value = false
    await loadUsers(page.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '保存账号失败')
  } finally { saving.value = false }
}

async function fetchAllProjects() {
  const collected = []
  let current = 1
  let hasNext = true
  while (hasNext) {
    const response = await getProjects({ page: current, page_size: 100 })
    const data = response?.data ?? response ?? {}
    const items = Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : [])
    collected.push(...items)
    hasNext = data.pagination?.has_next === true
    current += 1
  }
  return collected
}
async function openProjects(item) {
  if (!canAssignProjects(item)) return
  const version = ++projectDialogVersion
  projectUser.value = item
  projectsVisible.value = true
  selectedProjectIds.value = []
  allProjects.value = []
  projectAssignmentsReady.value = false
  projectLoadError.value = ''
  projectsLoading.value = true
  try {
    const [projects, response] = await Promise.all([fetchAllProjects(), usersApi.getManagedUserProjects(item.id)])
    if (version !== projectDialogVersion || !projectsVisible.value || String(projectUser.value?.id) !== String(item.id)) return
    allProjects.value = projects
    selectedProjectIds.value = unwrap(response).project_ids || []
    projectAssignmentsReady.value = true
  } catch (error) {
    if (version === projectDialogVersion && projectsVisible.value) projectLoadError.value = error.response?.data?.message || '读取项目或当前分配失败，请重新加载后再保存。'
  } finally { if (version === projectDialogVersion) projectsLoading.value = false }
}
function resetProjectDialog() {
  projectDialogVersion += 1
  projectUser.value = null
  allProjects.value = []
  selectedProjectIds.value = []
  projectAssignmentsReady.value = false
  projectLoadError.value = ''
  projectsLoading.value = false
}
function retryProjects() { if (projectUser.value) openProjects(projectUser.value) }
async function saveProjects() {
  if (!projectUser.value || projectsLoading.value || !projectAssignmentsReady.value) return
  const userId = projectUser.value.id
  const version = projectDialogVersion
  savingProjects.value = true
  try {
    await usersApi.updateManagedUserProjects(userId, selectedProjectIds.value)
    if (version !== projectDialogVersion) return
    ElMessage.success('项目分配已保存')
    projectsVisible.value = false
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '保存项目分配失败')
  } finally { savingProjects.value = false }
}

onMounted(() => loadUsers())
</script>

<style scoped>
.user-management { padding: 24px; }
.page-header { margin-bottom: 24px; }
.page-header :deep(.el-page-header__title) { display: none; }
.toolbar { width: min(420px, 100%); margin-bottom: 18px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 18px; }
.dialog-alert { margin-bottom: 18px; }
.project-checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; min-height: 80px; }
.readonly-note { color: var(--el-text-color-secondary); font-size: 13px; }
@media (max-width: 700px) { .project-checks { grid-template-columns: 1fr; } }
</style>
