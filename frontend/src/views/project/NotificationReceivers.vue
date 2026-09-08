<template>
  <div class="receivers-page">
    <el-empty v-if="!currentProjectId" description="请先选择项目" />
    <template v-else>
      <div class="page-header">
        <div><h2>邮件通知</h2><p>管理本项目的邮件接收组，用于计划任务执行结果通知。</p></div>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建邮件接收组</el-button>
      </div>
      <el-alert type="info" :closable="false" show-icon class="setup-guide">
        <template #title>配置顺序：管理员配置邮件服务 → 添加邮件接收组 → 在计划任务中选择接收组及通知条件。</template>
        <p>邮件服务位于「首页 → 系统配置 → 邮件服务配置」。普通用例或套件手动运行不会自动发邮件。</p>
      </el-alert>
      <el-card class="list-card">
        <el-alert v-if="loadError" :title="loadError" type="error" :closable="false">
          <el-button link type="primary" @click="loadReceivers">重新加载</el-button>
        </el-alert>
        <el-table :data="receivers" v-loading="loading" row-key="id" empty-text="暂无邮件接收组，请点击右上方添加">
          <el-table-column prop="name" label="接收组名称" min-width="180" show-overflow-tooltip />
          <el-table-column prop="target_address" label="收件人邮箱" min-width="280" show-overflow-tooltip />
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag :type="canSend(row) ? 'success' : 'info'">{{ row.channel_is_active === false ? '邮件通道已停用' : row.is_active ? '已启用' : '已停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="250" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" :loading="testingId === row.id" :disabled="testingId !== null || !canSend(row)" @click="sendTestEmail(row)">发送测试邮件</el-button>
              <el-button link type="primary" @click="openEditDialog(row)">编辑</el-button>
              <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
    <el-dialog v-model="showDialog" :title="editingReceiver ? '编辑邮件接收组' : '新建邮件接收组'" width="560px" :close-on-click-modal="false" @closed="resetForm">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="110px">
        <el-form-item label="接收组名称" prop="name">
          <el-input v-model="form.name" placeholder="如：测试结果接收组" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="收件人邮箱" prop="target_address">
          <el-input v-model="form.target_address" type="textarea" :rows="4" placeholder="如 qa@example.com；多个邮箱可用逗号、分号或换行分隔" maxlength="1000" show-word-limit />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">{{ editingReceiver ? '更新' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import * as notificationsApi from '@/api/notifications'
import { emailNotificationReceivers, notificationErrorMessage } from '@/utils/notificationFeedback'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()
const currentProjectId = computed(() => projectStore.currentProjectId ?? route.params.project_id ?? route.query.project_id)
const loading = ref(false)
const loadError = ref('')
const receivers = ref([])
const showDialog = ref(false)
const editingReceiver = ref(null)
const saving = ref(false)
const testingId = ref(null)
const formRef = ref(null)
const formProjectId = ref(null)
let receiverRequestVersion = 0
const emptyForm = () => ({ name: '', target_address: '', is_active: true })
const form = ref(emptyForm())
const formRules = {
  name: [{ required: true, whitespace: true, message: '请输入接收组名称', trigger: 'blur' }],
  target_address: [{ required: true, whitespace: true, message: '请填写收件人邮箱', trigger: 'blur' }]
}
const canSend = row => row.is_active !== false && row.channel_is_active !== false
const sameProject = id => String(id) === String(currentProjectId.value)

async function loadReceivers() {
  const projectId = currentProjectId.value
  const version = ++receiverRequestVersion
  loadError.value = ''
  if (!projectId) { receivers.value = []; loading.value = false; return }
  loading.value = true
  try {
    const res = await notificationsApi.getNotificationReceivers(projectId)
    if (version !== receiverRequestVersion) return
    const data = res?.data ?? res
    receivers.value = emailNotificationReceivers(Array.isArray(data) ? data : data?.results)
  } catch (e) {
    if (version !== receiverRequestVersion) return
    receivers.value = []
    loadError.value = notificationErrorMessage(e, '加载邮件接收组失败')
  } finally {
    if (version === receiverRequestVersion) loading.value = false
  }
}

function openCreateDialog() {
  formProjectId.value = currentProjectId.value
  editingReceiver.value = null
  form.value = emptyForm()
  showDialog.value = true
}
function openEditDialog(row) {
  formProjectId.value = currentProjectId.value
  editingReceiver.value = row
  form.value = { name: row.name, target_address: row.target_address, is_active: row.is_active }
  showDialog.value = true
}
function resetForm() {
  editingReceiver.value = null
  form.value = emptyForm()
  formRef.value?.clearValidate()
}
async function submitForm() {
  if (saving.value) return
  try { await formRef.value?.validate() } catch { return }
  const projectId = formProjectId.value
  if (!projectId || !sameProject(projectId)) { ElMessage.warning('项目已变化，请重新打开配置'); return }
  saving.value = true
  try {
    const payload = { ...form.value, name: form.value.name.trim(), target_address: form.value.target_address.trim(), project: projectId }
    if (editingReceiver.value) await notificationsApi.updateNotificationReceiver(projectId, editingReceiver.value.id, payload)
    else await notificationsApi.createNotificationReceiver(projectId, payload)
    if (!sameProject(projectId)) return
    ElMessage.success('邮件接收组已保存')
    showDialog.value = false
    await loadReceivers()
  } catch (e) {
    if (sameProject(projectId)) ElMessage.error(notificationErrorMessage(e, '保存失败'))
  } finally { saving.value = false }
}
async function sendTestEmail(row) {
  if (testingId.value !== null || !canSend(row)) return
  const projectId = currentProjectId.value
  testingId.value = row.id
  try {
    try {
      await ElMessageBox.confirm('将向 ' + row.target_address + ' 发送一封测试邮件。', '发送测试邮件', { confirmButtonText: '确认发送', cancelButtonText: '取消', type: 'info' })
    } catch { return }
    if (!sameProject(projectId)) return
    await notificationsApi.testReceiverById(projectId, row.id)
    if (sameProject(projectId)) ElMessage.success('SMTP 已接受测试邮件，请检查收件箱或垃圾邮件；最终送达以实际收信为准')
  } catch (e) {
    if (sameProject(projectId)) ElMessage.error(notificationErrorMessage(e, '测试邮件发送失败'))
  } finally { testingId.value = null }
}
async function confirmDelete(row) {
  const projectId = currentProjectId.value
  try {
    await ElMessageBox.confirm('确定删除邮件接收组「' + row.name + '」吗？关联计划任务将不再向该组发信。', '删除确认', { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  if (!sameProject(projectId)) return
  try {
    await notificationsApi.deleteNotificationReceiver(projectId, row.id)
    if (!sameProject(projectId)) return
    ElMessage.success('已删除')
    await loadReceivers()
  } catch (e) { if (sameProject(projectId)) ElMessage.error(notificationErrorMessage(e, '删除失败')) }
}
watch(currentProjectId, () => { showDialog.value = false; receivers.value = []; loadReceivers() }, { immediate: true })
onMounted(async () => { await projectStore.initializeUserPreferences() })
</script>

<style scoped>
.receivers-page { padding: 20px; min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
.page-header h2 { margin: 0 0 8px; }
.page-header p { margin: 0; color: var(--el-text-color-secondary); }
.setup-guide { margin-bottom: 20px; }
.setup-guide p { margin: 8px 0 0; }
.list-card { min-width: 0; }
@media (max-width: 700px) { .page-header { align-items: flex-start; flex-direction: column; } }
</style>
