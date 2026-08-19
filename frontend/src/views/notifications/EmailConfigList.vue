<template>
  <div class="email-config-page">
    <div class="page-header">
      <BackButton to="/settings" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Message /></el-icon>
          </div>
          <div class="header-text">
            <h2>邮件服务配置</h2>
            <p>配置 SMTP 服务器，供通知对象中「邮件」渠道发送执行报告</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="openCreateDialog" class="create-btn">
            新建配置
          </el-button>
        </div>
      </div>
    </div>

    <el-card class="config-list-card">
      <div class="card-header">
        <h3>配置列表</h3>
      </div>
      <div class="table-container">
        <el-table :data="configs" v-loading="loading" style="width: 100%" row-key="id">
          <el-table-column prop="id" label="ID" width="70" align="center" />
          <el-table-column prop="name" label="配置名称" min-width="140" show-overflow-tooltip />
          <el-table-column prop="smtp_server" label="SMTP 服务器" min-width="180" show-overflow-tooltip />
          <el-table-column prop="port" label="端口" width="80" align="center" />
          <el-table-column prop="sender_email" label="发件邮箱" min-width="180" show-overflow-tooltip />
          <el-table-column label="授权码" width="100" align="center">
            <template #default="scope">
              <span class="password-cell">{{ scope.row.smtp_password === '***' ? '已配置' : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="use_ssl" label="SSL" width="80" align="center">
            <template #default="scope">
              <el-tag :type="scope.row.use_ssl ? 'success' : 'info'" size="small">
                {{ scope.row.use_ssl ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="is_active" label="启用" width="80" align="center">
            <template #default="scope">
              <el-tag :type="scope.row.is_active ? 'success' : 'info'" size="small">
                {{ scope.row.is_active ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="180" align="center">
            <template #default="scope">
              <span class="created-time">{{ formatDateTime(scope.row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="scope">
              <el-button type="success" size="small" link :loading="testingId === scope.row.id" @click="testConfig(scope.row)">
                测试
              </el-button>
              <el-button type="primary" size="small" link @click="openEditDialog(scope.row)">
                编辑
              </el-button>
              <el-button type="danger" size="small" link @click="confirmDelete(scope.row)">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && configs.length === 0" description="暂无配置，点击「新建配置」添加" />
      </div>
    </el-card>

    <el-dialog
      v-model="showDialog"
      :title="editingConfig ? '编辑配置' : '新建配置'"
      width="520px"
      :close-on-click-modal="false"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="120px">
        <el-form-item label="配置名称" prop="name">
          <el-input v-model="form.name" placeholder="如：公司邮件服务器" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="SMTP 服务器" prop="smtp_server">
          <el-input v-model="form.smtp_server" placeholder="如 smtp.qq.com、smtp.163.com" />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number v-model="form.port" :min="1" :max="65535" placeholder="465(SSL) 或 587(TLS)" style="width: 100%" />
        </el-form-item>
        <el-form-item label="发件邮箱" prop="sender_email">
          <el-input v-model="form.sender_email" placeholder="发件人邮箱地址" />
        </el-form-item>
        <el-form-item label="SMTP 授权码" prop="smtp_password">
          <el-input
            v-model="form.smtp_password"
            type="password"
            show-password
            :placeholder="editingConfig ? '已配置，如需修改请填入新授权码；不修改请留空' : '请输入 SMTP 授权码（非登录密码）'"
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="使用 SSL" prop="use_ssl">
          <el-switch v-model="form.use_ssl" />
          <span class="form-tip-inline">465 端口通常为 true，587 端口为 false</span>
        </el-form-item>
        <el-form-item label="启用" prop="is_active">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" @click="submitForm" :loading="saving">
          {{ editingConfig ? '更新' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Message, Plus } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import * as notificationsApi from '@/api/notifications'

const loading = ref(false)
const configs = ref([])
const showDialog = ref(false)
const editingConfig = ref(null)
const saving = ref(false)
const testingId = ref(null)
const formRef = ref(null)

const form = ref({
  name: '',
  smtp_server: '',
  port: 465,
  sender_email: '',
  smtp_password: '',
  use_ssl: true,
  is_active: true
})

const formRules = computed(() => ({
  name: [
    { required: true, message: '请输入配置名称', trigger: 'blur' },
    { max: 100, message: '长度不超过 100 个字符', trigger: 'blur' }
  ],
  smtp_server: [
    { required: true, message: '请输入 SMTP 服务器', trigger: 'blur' },
    { max: 255, message: '长度不超过 255 个字符', trigger: 'blur' }
  ],
  port: [
    { required: true, message: '请输入端口', trigger: 'blur' },
    { type: 'number', min: 1, max: 65535, message: '端口范围 1-65535', trigger: 'blur' }
  ],
  sender_email: [
    { required: true, message: '请输入发件邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' }
  ],
  smtp_password: editingConfig.value
    ? [{ max: 255, message: '长度不超过 255 个字符', trigger: 'blur' }]
    : [
        { required: true, message: '请输入 SMTP 授权码', trigger: 'blur' },
        { max: 255, message: '长度不超过 255 个字符', trigger: 'blur' }
      ]
}))

function formatDateTime(val) {
  if (!val) return '—'
  return new Date(val).toLocaleString('zh-CN')
}

function isPasswordEmptyOrMasked(val) {
  if (val == null) return true
  const s = String(val).trim()
  return s === '' || s.includes('***')
}

async function loadConfigs() {
  loading.value = true
  try {
    const res = await notificationsApi.getEmailConfigs()
    const data = res?.data ?? res
    configs.value = Array.isArray(data) ? data : (data?.results ?? data?.items ?? [])
  } catch (e) {
    console.error('加载邮件配置失败:', e)
    configs.value = []
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  editingConfig.value = null
  form.value = {
    name: '',
    smtp_server: '',
    port: 465,
    sender_email: '',
    smtp_password: '',
    use_ssl: true,
    is_active: true
  }
  showDialog.value = true
}

function openEditDialog(row) {
  editingConfig.value = row
  form.value = {
    name: row.name || '',
    smtp_server: row.smtp_server || '',
    port: row.port ?? 465,
    sender_email: row.sender_email || '',
    smtp_password: '',
    use_ssl: row.use_ssl ?? true,
    is_active: row.is_active ?? true
  }
  showDialog.value = true
}

function resetForm() {
  editingConfig.value = null
  form.value = {
    name: '',
    smtp_server: '',
    port: 465,
    sender_email: '',
    smtp_password: '',
    use_ssl: true,
    is_active: true
  }
  formRef.value?.clearValidate?.()
}

async function testConfig(row) {
  if (!row?.id) return
  testingId.value = row.id
  try {
    await notificationsApi.testEmailConfig(row.id)
    ElMessage.success('连接成功')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '连接失败')
  } finally {
    testingId.value = null
  }
}

async function submitForm() {
  try {
    await formRef.value?.validate()
    saving.value = true
    const payload = {
      name: form.value.name,
      smtp_server: form.value.smtp_server,
      port: form.value.port,
      sender_email: form.value.sender_email,
      use_ssl: form.value.use_ssl,
      is_active: form.value.is_active
    }
    if (editingConfig.value) {
      if (!isPasswordEmptyOrMasked(form.value.smtp_password)) {
        payload.smtp_password = form.value.smtp_password
      }
      await notificationsApi.updateEmailConfig(editingConfig.value.id, payload)
      ElMessage.success('更新成功')
    } else {
      payload.smtp_password = (form.value.smtp_password || '').trim()
      await notificationsApi.createEmailConfig(payload)
      ElMessage.success('创建成功')
    }
    showDialog.value = false
    loadConfigs()
  } catch (e) {
    if (e?.message !== undefined) return
    console.error(e)
    ElMessage.error(editingConfig.value ? '更新失败' : '创建失败')
  } finally {
    saving.value = false
  }
}

function confirmDelete(row) {
  ElMessageBox.confirm(
    `确定要删除配置「${row.name}」吗？删除后不可恢复。`,
    '删除确认',
    {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning'
    }
  ).then(async () => {
    try {
      await notificationsApi.deleteEmailConfig(row.id)
      ElMessage.success('已删除')
      loadConfigs()
    } catch (e) {
      console.error(e)
      ElMessage.error('删除失败')
    }
  }).catch(() => {})
}

onMounted(() => loadConfigs())
</script>

<style scoped>
.email-config-page {
  padding: 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-header {
  margin-bottom: 20px;
  flex-shrink: 0;
}

.page-header :deep(.back-btn) {
  margin-bottom: 12px;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
  border-radius: 12px;
  padding: 24px;
  color: #fff;
}

.header-left {
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
  font-size: 24px;
}

.header-text h2 {
  margin: 0 0 4px 0;
  font-size: 20px;
  font-weight: 600;
}

.header-text p {
  margin: 0;
  font-size: 13px;
  opacity: 0.9;
}

.header-actions :deep(.el-button) {
  background: rgba(255, 255, 255, 0.95);
  color: #11998e;
  border: none;
}

.config-list-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-radius: 8px;
}

.card-header {
  margin-bottom: 16px;
}

.card-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.table-container {
  flex: 1;
  min-height: 200px;
}

.password-cell {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.created-time {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.form-tip-inline {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
