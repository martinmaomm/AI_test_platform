<template>
  <div class="receivers-page">
    <div class="page-header">
      <div v-if="!projectStore.currentProject" class="header-content no-project">
        <el-empty description="请先选择项目" />
      </div>
      <div v-else class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Bell /></el-icon>
          </div>
          <div class="header-text">
            <h2>通知接收管理</h2>
            <p>管理本项目的通知接收对象（Webhook、邮件组），用于执行结果推送</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="openCreateDialog" class="create-btn">
            新建消息接收对象
          </el-button>
        </div>
      </div>
    </div>

    <el-card v-if="projectStore.currentProject" class="list-card">
      <div class="card-header">
        <h3>接收对象列表</h3>
      </div>
      <div class="table-container">
        <el-table
          :data="receivers"
          v-loading="loading"
          style="width: 100%; height: 100%"
          row-key="id"
        >
          <el-table-column prop="id" label="ID" width="70" align="center">
            <template #default="scope">
              <span class="receiver-id">{{ scope.row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="channel_code" label="通道类型" width="120" align="center">
            <template #default="scope">
              <el-tag :type="getChannelTypeTag(resolveChannelCode(scope.row))" size="small">
                {{ resolveChannelLabel(scope.row) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="通知组名称" min-width="180" show-overflow-tooltip />
          <el-table-column label="目标地址" min-width="280">
            <template #default="scope">
              <span class="webhook-cell">
                {{ resolveChannelCode(scope.row) === 'email' ? maskEmailList(scope.row.target_address) : maskWebhook(scope.row.webhook_url) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="180" align="center">
            <template #default="scope">
              <span class="created-time">{{ formatDateTime(scope.row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="scope">
              <el-button type="success" size="small" link :loading="testingId === scope.row.id" @click="testChannelById(scope.row)">
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
        <el-empty v-if="!loading && receivers.length === 0" description="暂无接收对象，点击「新建消息接收对象」添加" />
      </div>
    </el-card>

    <el-dialog
      v-model="showDialog"
      :title="editingReceiver ? '编辑接收对象' : '新建消息接收对象'"
      width="520px"
      :close-on-click-modal="false"
      class="receiver-form-dialog"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="120px">
        <el-form-item label="通知组名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="如：研发组钉钉群"
            maxlength="100"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="通道类型" prop="channel">
          <el-select v-model="form.channel" placeholder="请选择类型" style="width: 100%">
            <el-option
              v-for="item in channelList"
              :key="item.id"
              :label="item.channel_name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-show="selectedChannel?.channel_code !== 'email'" label="Webhook URL" prop="webhook_url">
          <el-input
            v-model="form.webhook_url"
            type="password"
            show-password
            :placeholder="editingReceiver ? '已配置目标地址，如需修改请填入新地址；不修改请留空' : '请输入 Webhook 地址'"
            autocomplete="off"
          />
        </el-form-item>
        <el-form-item v-show="selectedChannel?.channel_code === 'email'" label="收件人邮箱" prop="target_address">
          <el-input
            v-model="form.target_address"
            type="password"
            show-password
            :placeholder="editingReceiver ? '已配置邮箱，如需修改请填入；不修改请留空' : '多个邮箱请用英文逗号(,)分隔...'"
            autocomplete="off"
            maxlength="1000"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showDialog = false">取消</el-button>
          <el-button type="primary" @click="submitForm" :loading="saving">
            {{ editingReceiver ? '更新' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, Plus } from '@element-plus/icons-vue'
import * as notificationsApi from '@/api/notifications'
import { maskEmailList } from '@/utils/mask'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()

const loading = ref(false)
const receivers = ref([])
const channelList = ref([])
const showDialog = ref(false)
const editingReceiver = ref(null)
const saving = ref(false)
const testingId = ref(null)
const formRef = ref(null)

const form = ref({
  name: '',
  channel: null,
  webhook_url: '',
  target_address: ''
})

/** 当前选中的渠道（用于判断是否邮件类型） */
const selectedChannel = computed(() => {
  const id = form.value.channel
  if (id == null) return null
  return channelList.value.find((c) => c.id === id) || null
})

const formRules = computed(() => {
  const isEmail = selectedChannel.value?.channel_code === 'email'
  const editing = !!editingReceiver.value
  return {
    name: [
      { required: true, message: '请输入通知组名称', trigger: 'blur' },
      { max: 100, message: '长度不超过 100 个字符', trigger: 'blur' }
    ],
    channel: [{ required: true, message: '请选择通道类型', trigger: 'change' }],
    webhook_url: isEmail
      ? []
      : editing
        ? [{ max: 2000, message: '长度不超过 2000 个字符', trigger: 'blur' }]
        : [
            { required: true, message: '请输入 Webhook 地址', trigger: 'blur' },
            { max: 2000, message: '长度不超过 2000 个字符', trigger: 'blur' }
          ],
    target_address: !isEmail
      ? []
      : editing
        ? [{ max: 1000, message: '长度不超过 1000 个字符', trigger: 'blur' }]
        : [
            { required: true, message: '请填写收件人邮箱，多个邮箱用英文逗号分隔', trigger: 'blur' },
            { max: 1000, message: '长度不超过 1000 个字符', trigger: 'blur' }
          ]
  }
})

/** 解析渠道编码：支持 channel_code、channel_type、channel.channel_code 等嵌套结构 */
function resolveChannelCode(row) {
  return row?.channel_code ?? row?.channel_type ?? row?.channel?.channel_code ?? ''
}

/** 解析渠道显示名称：优先使用 channel_name（后端序列化后返回） */
function resolveChannelLabel(row) {
  const name = row?.channel_name ?? row?.channel?.channel_name
  if (name) return name
  const code = resolveChannelCode(row)
  const map = { dingtalk: '钉钉', wechat_work: '企微', email: '邮件' }
  return map[code] || code || '—'
}

function getChannelTypeTag(type) {
  const map = { dingtalk: 'warning', wechat_work: 'success', email: 'primary' }
  return map[type || ''] || 'info'
}

function maskWebhook(url) {
  if (url == null || typeof url !== 'string') return '—'
  const u = url.trim()
  if (!u) return '—'
  try {
    const sensitiveParams = ['access_token', 'key']
    for (const param of sensitiveParams) {
      const re = new RegExp(`([?&])${param}=([^&]*)`, 'i')
      const m = u.match(re)
      if (m) {
        const value = m[2] || ''
        const len = value.length
        if (len <= 8) return u.replace(re, `${m[1]}${param}=******`)
        return u.replace(re, `${m[1]}${param}=${value.slice(0, 4)}******${value.slice(-4)}`)
      }
    }
    if (u.length <= 24) return u.slice(0, 8) + '***'
    return u.slice(0, 12) + '******' + u.slice(-8)
  } catch (_) {
    return '—'
  }
}

function formatDateTime(val) {
  if (!val) return '—'
  return new Date(val).toLocaleString('zh-CN')
}

async function testChannelById(row) {
  if (!row?.id) return
  const projectId = projectStore.currentProjectId ?? route.params.project_id ?? route.query.project_id
  if (!projectId) return
  testingId.value = row.id
  try {
    await notificationsApi.testReceiverById(projectId, row.id)
    ElMessage.success('连接成功')
  } catch (e) {
    const d = e?.response?.data
    const msg = d?.detail ?? d?.message ?? d?.error?.message ?? e?.message ?? '连接失败'
    ElMessage.error(msg)
  } finally {
    testingId.value = null
  }
}

async function loadChannels() {
  try {
    const res = await notificationsApi.getNotificationChannels({ is_active: true })
    const data = res?.data ?? res
    channelList.value = Array.isArray(data) ? data : (data?.results ?? data?.items ?? [])
  } catch (e) {
    console.error('加载渠道列表失败:', e)
    channelList.value = []
  }
}

async function loadReceivers() {
  const projectId =
    projectStore.currentProjectId ??
    (route.params.project_id ? Number(route.params.project_id) : null) ??
    (route.query.project_id ? Number(route.query.project_id) : null)
  if (!projectId) {
    receivers.value = []
    return
  }
  loading.value = true
  try {
    const res = await notificationsApi.getNotificationReceivers(projectId)
    const data = res?.data ?? res
    receivers.value = Array.isArray(data) ? data : (data?.results ?? data?.items ?? [])
  } catch (e) {
    console.error('加载接收对象列表失败:', e)
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载接收对象列表失败')
    receivers.value = []
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  editingReceiver.value = null
  form.value = {
    name: '',
    channel: channelList.value[0]?.id ?? null,
    webhook_url: '',
    target_address: ''
  }
  showDialog.value = true
}

function openEditDialog(row) {
  const channelId = row.channel_id ?? (typeof row.channel === 'object' && row.channel ? row.channel.id : row.channel) ?? null
  editingReceiver.value = row
  form.value = {
    name: row.name || '',
    channel: channelId,
    webhook_url: '',
    target_address: row.target_address || ''
  }
  showDialog.value = true
}

function resetForm() {
  editingReceiver.value = null
  form.value = { name: '', channel: null, webhook_url: '', target_address: '' }
  formRef.value?.clearValidate?.()
}

function isAddressEmptyOrMasked(val) {
  if (val == null) return true
  const s = String(val).trim()
  return s === '' || s.includes('***')
}

/** 清洗 payload：移除空值字段，确保 PATCH 时后端保留原数据库值 */
function sanitizeUpdatePayload(payload, isEdit) {
  const cleaned = { ...payload }
  if (cleaned.webhook_url === '' || cleaned.webhook_url == null || cleaned.webhook_url === undefined || (typeof cleaned.webhook_url === 'string' && cleaned.webhook_url.trim() === '') || (isEdit && isAddressEmptyOrMasked(cleaned.webhook_url))) {
    delete cleaned.webhook_url
  } else if (typeof cleaned.webhook_url === 'string') {
    cleaned.webhook_url = cleaned.webhook_url.trim()
  }
  if (cleaned.target_address === '' || cleaned.target_address == null || cleaned.target_address === undefined || (typeof cleaned.target_address === 'string' && cleaned.target_address.trim() === '') || (isEdit && isAddressEmptyOrMasked(cleaned.target_address))) {
    delete cleaned.target_address
  } else if (typeof cleaned.target_address === 'string') {
    cleaned.target_address = cleaned.target_address.trim()
  }
  return cleaned
}

async function submitForm() {
  try {
    await formRef.value?.validate()
    saving.value = true
    const projectId = projectStore.currentProjectId ?? route.params.project_id ?? route.query.project_id
    if (!projectId) {
      ElMessage.warning('请先选择项目')
      return
    }
    const isEmail = selectedChannel.value?.channel_code === 'email'
    let payload = { name: form.value.name, channel: form.value.channel, project: projectId }
    if (editingReceiver.value) {
      if (!isEmail) {
        payload.webhook_url = form.value.webhook_url ?? ''
      } else {
        payload.target_address = form.value.target_address ?? ''
      }
      payload = sanitizeUpdatePayload(payload, true)
      await notificationsApi.updateNotificationReceiver(projectId, editingReceiver.value.id, payload)
      ElMessage.success('更新成功')
    } else {
      if (isEmail) {
        payload.target_address = (form.value.target_address || '').trim()
      } else {
        payload.webhook_url = (form.value.webhook_url || '').trim()
      }
      await notificationsApi.createNotificationReceiver(projectId, payload)
      ElMessage.success('创建成功')
    }
    showDialog.value = false
    loadReceivers()
  } catch (e) {
    if (e?.message !== undefined) return
    console.error(e)
    ElMessage.error(editingReceiver.value ? '更新失败' : '创建失败')
  } finally {
    saving.value = false
  }
}

function confirmDelete(row) {
  ElMessageBox.confirm(
    `确定要删除接收对象「${row.name}」吗？删除后不可恢复。`,
    '删除确认',
    { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
  )
    .then(async () => {
      try {
        const projectId = projectStore.currentProjectId ?? route.params.project_id ?? route.query.project_id
        await notificationsApi.deleteNotificationReceiver(projectId, row.id)
        ElMessage.success('已删除')
        loadReceivers()
      } catch (e) {
        console.error(e)
        ElMessage.error('删除失败')
      }
    })
    .catch(() => {})
}

watch(
  () => projectStore.currentProjectId ?? route.params.project_id ?? route.query.project_id,
  () => loadReceivers(),
  { immediate: true }
)
onMounted(async () => {
  await projectStore.initializeUserPreferences()
  loadChannels()
  loadReceivers()
})
</script>

<style scoped>
.receivers-page {
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

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  padding: 24px;
  color: #fff;
}

.header-content.no-project {
  justify-content: center;
  min-height: 120px;
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
  color: #667eea;
  border: none;
}

.list-card {
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

.receiver-id {
  font-family: 'Monaco', 'Menlo', monospace;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 2px 8px;
  border-radius: 4px;
}

.webhook-cell {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.created-time {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
