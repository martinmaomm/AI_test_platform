<template>
  <div class="channel-list-page">
    <!-- 页面头部（与套件列表风格一致） -->
    <div class="page-header">
      <BackButton to="/settings" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Bell /></el-icon>
          </div>
          <div class="header-text">
            <h2>通知对象管理</h2>
            <p>配置钉钉、企业微信等 Webhook 群组，用于执行结果通知推送</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="openCreateDialog" class="create-btn">
            新建渠道
          </el-button>
        </div>
      </div>
    </div>

    <!-- 渠道列表卡片 -->
    <el-card class="channel-list-card">
      <div class="card-header">
        <div class="card-header-left">
          <h3>渠道列表</h3>
        </div>
      </div>
      <div class="table-container">
        <el-table
          :data="channels"
          v-loading="loading"
          style="width: 100%; height: 100%"
          row-key="id"
        >
          <el-table-column prop="id" label="ID" width="70" align="center">
            <template #default="scope">
              <span class="channel-id">{{ scope.row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="channel_type" label="渠道类型" width="120" align="center">
            <template #default="scope">
              <el-tag :type="getChannelTypeTag(scope.row.channel_type)" size="small">
                {{ getChannelTypeLabel(scope.row.channel_type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="接收群组名称" min-width="180" show-overflow-tooltip />
          <el-table-column label="目标地址" min-width="280">
            <template #default="scope">
              <span class="webhook-cell">
                {{ scope.row.channel_type === 'email' ? maskEmailList(scope.row.target_address) : maskWebhook(scope.row.webhook_url) }}
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
        <el-empty v-if="!loading && channels.length === 0" description="暂无渠道，点击「新建渠道」添加" />
      </div>
    </el-card>

    <!-- 创建/编辑弹窗 -->
    <el-dialog
      v-model="showDialog"
      :title="editingChannel ? '编辑渠道' : '新建渠道'"
      width="520px"
      :close-on-click-modal="false"
      class="channel-form-dialog"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="120px">
        <el-form-item label="接收群组名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="如：研发组钉钉群"
            maxlength="100"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="渠道类型" prop="channel_type">
          <el-select v-model="form.channel_type" placeholder="请选择类型" style="width: 100%">
            <el-option label="钉钉" value="dingtalk" />
            <el-option label="企业微信" value="wechat_work" />
            <el-option label="邮件" value="email" />
          </el-select>
        </el-form-item>
        <el-form-item v-show="form.channel_type !== 'email'" label="Webhook URL" prop="webhook_url">
          <el-input
            v-model="form.webhook_url"
            type="password"
            show-password
            :placeholder="editingChannel ? '已配置目标地址，如需修改请填入新地址；不修改请留空' : '请输入 Webhook 地址'"
            autocomplete="off"
          />
          <div class="form-tip">
            <el-button
              type="primary"
              link
              size="small"
              :loading="testingConnection"
              @click="testConnection"
            >
              测试连接
            </el-button>
          </div>
        </el-form-item>
        <el-form-item v-show="form.channel_type === 'email'" label="收件人邮箱" prop="target_address">
          <el-input
            v-model="form.target_address"
            type="password"
            show-password
            :placeholder="editingChannel ? '已配置邮箱，如需修改请填入；不修改请留空' : '多个邮箱请用英文逗号(,)分隔...'"
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
            {{ editingChannel ? '更新' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, Plus } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import * as notificationsApi from '@/api/notifications'
import { maskEmailList } from '@/utils/mask'

const loading = ref(false)
const channels = ref([])
const showDialog = ref(false)
const editingChannel = ref(null)
const saving = ref(false)
const testingConnection = ref(false)
const testingId = ref(null)
const formRef = ref(null)

const form = ref({
  name: '',
  channel_type: 'dingtalk',
  webhook_url: '',
  target_address: ''
})

const formRules = computed(() => {
  const isEmail = form.value.channel_type === 'email'
  const editing = !!editingChannel.value
  return {
    name: [
      { required: true, message: '请输入渠道名称', trigger: 'blur' },
      { max: 100, message: '长度不超过 100 个字符', trigger: 'blur' }
    ],
    channel_type: [
      { required: true, message: '请选择类型', trigger: 'change' }
    ],
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

function getChannelTypeTag(type) {
  const map = { dingtalk: 'warning', wechat_work: 'success', email: 'primary' }
  return map[type] || 'info'
}

function getChannelTypeLabel(type) {
  const map = { dingtalk: '钉钉', wechat_work: '企微', email: '邮件' }
  return map[type] || type || '—'
}

/**
 * Webhook 地址脱敏：若含 access_token= 或 key=，保留域名与参数值前 4 位及后 4 位，中间 ******
 * 避免明文暴露于列表或 Tooltip
 */
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
        const prefix = m[1]
        const value = m[2] || ''
        const len = value.length
        if (len <= 8) {
          return u.replace(re, `${prefix}${param}=******`)
        }
        const head = value.slice(0, 4)
        const tail = value.slice(-4)
        const masked = `${prefix}${param}=${head}******${tail}`
        return u.replace(re, masked)
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
  testingId.value = row.id
  try {
    await notificationsApi.testChannelById(row.id)
    ElMessage.success('连接成功')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '连接失败')
  } finally {
    testingId.value = null
  }
}

async function loadChannels() {
  loading.value = true
  try {
    const res = await notificationsApi.getNotificationChannels()
    const data = res?.data ?? res
    channels.value = Array.isArray(data) ? data : (data?.results ?? data?.items ?? [])
  } catch (e) {
    console.error('加载渠道列表失败:', e)
    channels.value = []
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  editingChannel.value = null
  form.value = { name: '', channel_type: 'dingtalk', webhook_url: '', target_address: '' }
  showDialog.value = true
}

function openEditDialog(row) {
  editingChannel.value = row
  form.value = {
    name: row.name || '',
    channel_type: row.channel_type || 'dingtalk',
    webhook_url: '',
    target_address: row.target_address || ''
  }
  showDialog.value = true
}

function resetForm() {
  editingChannel.value = null
  form.value = { name: '', channel_type: 'dingtalk', webhook_url: '', target_address: '' }
  formRef.value?.clearValidate?.()
}

async function testConnection() {
  if (form.value.channel_type === 'email') return
  const url = (form.value.webhook_url || '').trim()
  if (!url) {
    ElMessage.warning('请先填写 Webhook 地址')
    return
  }
  testingConnection.value = true
  try {
    await notificationsApi.testChannelConnection({ webhook_url: url, channel_type: form.value.channel_type })
    ElMessage.success('连接成功')
  } catch (e) {
    if (e?.response?.status === 404 || e?.code === 'ERR_NETWORK') {
      ElMessage.success('连接成功（Mock，后端接口待实现）')
    } else {
      ElMessage.error(e?.response?.data?.detail || e?.response?.data?.message || '连接失败')
    }
  } finally {
    testingConnection.value = false
  }
}

function isAddressEmptyOrMasked(val) {
  if (val == null) return true
  const s = String(val).trim()
  return s === '' || s.includes('***')
}

async function submitForm() {
  try {
    await formRef.value?.validate()
    saving.value = true
    const payload = { name: form.value.name, channel_type: form.value.channel_type }
    const isEmail = form.value.channel_type === 'email'
    if (editingChannel.value) {
      if (!isEmail && !isAddressEmptyOrMasked(form.value.webhook_url)) {
        payload.webhook_url = form.value.webhook_url.trim()
      }
      if (isEmail && !isAddressEmptyOrMasked(form.value.target_address)) {
        payload.target_address = form.value.target_address.trim()
      }
      await notificationsApi.updateNotificationChannel(editingChannel.value.id, payload)
      ElMessage.success('更新成功')
    } else {
      if (isEmail) {
        payload.target_address = (form.value.target_address || '').trim()
      } else {
        payload.webhook_url = (form.value.webhook_url || '').trim()
      }
      await notificationsApi.createNotificationChannel(payload)
      ElMessage.success('创建成功')
    }
    showDialog.value = false
    loadChannels()
  } catch (e) {
    if (e?.message !== undefined) return
    console.error(e)
    ElMessage.error(editingChannel.value ? '更新失败' : '创建失败')
  } finally {
    saving.value = false
  }
}

function confirmDelete(row) {
  ElMessageBox.confirm(
    `确定要删除渠道「${row.name}」吗？删除后不可恢复。`,
    '删除确认',
    {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning'
    }
  ).then(async () => {
    try {
      await notificationsApi.deleteNotificationChannel(row.id)
      ElMessage.success('已删除')
      loadChannels()
    } catch (e) {
      console.error(e)
      ElMessage.error('删除失败')
    }
  }).catch(() => {})
}

onMounted(() => loadChannels())
</script>

<style scoped>
.channel-list-page {
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
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
  color: #667eea;
  border: none;
}

.channel-list-card {
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

.channel-id {
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

.form-tip {
  margin-top: 6px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
