<template>
  <div class="channel-config-page">
    <div class="page-header">
      <BackButton to="/settings" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Bell /></el-icon>
          </div>
          <div class="header-text">
            <h2>消息通道配置</h2>
            <p>配置各通道类型的全局默认参数；具体接收对象请在项目内「通知接收管理」中创建</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>
            新建渠道
          </el-button>
        </div>
      </div>
    </div>

    <div class="channel-cards" v-loading="loading">
      <el-card
        v-for="ch in channelTypes"
        :key="ch.id"
        class="channel-card glass-card"
        shadow="hover"
      >
        <div class="card-glow-bar" :class="getGlowClass(ch.channel_code || ch.id)" />
        <div class="card-body">
          <div class="channel-icon" :class="getGlowClass(ch.channel_code || ch.id)">
            <el-icon><component :is="getChannelIcon(ch.channel_code || ch.id)" /></el-icon>
          </div>
          <div class="channel-info">
            <h3 class="channel-name">{{ ch.channel_name || ch.name }}</h3>
            <p class="channel-desc">{{ ch.description || ch.desc }}</p>
            <div class="channel-guide">
              <span class="guide-label">接入方式：</span>
              <p class="guide-steps">{{ ch.description || ch.guide }}</p>
            </div>
          </div>
        </div>
        <div class="card-actions">
          <el-button type="primary" link size="small" @click.stop="openEditDialog(ch)">
            <el-icon><Edit /></el-icon>
            编辑
          </el-button>
          <el-button
            v-if="!isPresetChannel(ch.channel_code || ch.id)"
            type="danger"
            link
            size="small"
            @click.stop="confirmDelete(ch)"
          >
            <el-icon><Delete /></el-icon>
            删除
          </el-button>
        </div>
      </el-card>
    </div>
    <el-empty v-if="!loading && channelTypes.length === 0" description="暂无渠道，点击「新建渠道」添加" class="channel-empty" />

    <el-dialog
      v-model="showDialog"
      :title="editingChannel ? '编辑消息渠道' : '新建消息渠道'"
      width="520px"
      :close-on-click-modal="false"
      class="channel-form-dialog"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-form-item label="渠道名称" prop="channel_name">
          <el-input
            v-model="form.channel_name"
            placeholder="如：飞书、Slack"
            maxlength="64"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="渠道标识" prop="channel_code">
          <el-input
            v-model="form.channel_code"
            placeholder="英文标识，如 feishu、slack"
            maxlength="32"
            show-word-limit
            :disabled="!!editingChannel"
          />
          <div v-if="editingChannel" class="form-tip">渠道标识创建后不可修改</div>
        </el-form-item>
        <el-form-item label="接入说明" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            placeholder="填写配置引导说明，供业务人员在项目内创建接收对象时参考"
          />
        </el-form-item>
        <el-form-item label="状态" prop="is_active">
          <el-switch v-model="form.is_active" active-text="启用" inactive-text="禁用" />
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, Plus, Edit, Delete } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'
import * as notificationsApi from '@/api/notifications'

const PRESET_CHANNELS = ['dingtalk', 'wechat_work', 'email']

const channelTypes = ref([])
const loading = ref(false)
const showDialog = ref(false)
const editingChannel = ref(null)
const saving = ref(false)
const formRef = ref(null)

const form = ref({
  channel_name: '',
  channel_code: '',
  description: '',
  is_active: true
})

const formRules = {
  channel_name: [
    { required: true, message: '请输入渠道名称', trigger: 'blur' },
    { max: 64, message: '长度不超过 64 个字符', trigger: 'blur' }
  ],
  channel_code: [
    { required: true, message: '请输入渠道标识', trigger: 'blur' },
    { max: 32, message: '长度不超过 32 个字符', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]*$/, message: '需以小写字母开头，仅允许小写字母、数字和下划线', trigger: 'blur' }
  ],
  description: [
    { max: 2000, message: '长度不超过 2000 个字符', trigger: 'blur' }
  ]
}

function isPresetChannel(code) {
  return code && PRESET_CHANNELS.includes(String(code).toLowerCase())
}

function getChannelIcon(code) {
  return Bell
}

function getGlowClass(code) {
  const known = ['dingtalk', 'wechat_work', 'email', 'feishu', 'slack']
  return known.includes(code) ? code : 'default'
}

async function loadChannels() {
  loading.value = true
  try {
    const res = await notificationsApi.getNotificationChannels()
    const data = res?.data ?? res
    channelTypes.value = Array.isArray(data) ? data : (data?.results ?? data?.items ?? [])
  } catch (e) {
    console.error('加载渠道列表失败:', e)
    channelTypes.value = []
    ElMessage.error('加载渠道列表失败')
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  editingChannel.value = null
  form.value = { channel_name: '', channel_code: '', description: '', is_active: true }
  showDialog.value = true
}

function openEditDialog(ch) {
  editingChannel.value = ch
  form.value = {
    channel_name: ch.channel_name || '',
    channel_code: ch.channel_code || '',
    description: ch.description || '',
    is_active: ch.is_active !== false
  }
  showDialog.value = true
}

function resetForm() {
  editingChannel.value = null
  form.value = { channel_name: '', channel_code: '', description: '', is_active: true }
  formRef.value?.clearValidate?.()
}

async function submitForm() {
  try {
    await formRef.value?.validate()
    saving.value = true
    if (editingChannel.value) {
      await notificationsApi.updateNotificationChannel(editingChannel.value.id, {
        channel_name: form.value.channel_name,
        description: form.value.description,
        is_active: form.value.is_active
      })
      ElMessage.success('更新成功')
    } else {
      await notificationsApi.createNotificationChannel({
        channel_name: form.value.channel_name,
        channel_code: form.value.channel_code.trim().toLowerCase(),
        description: form.value.description,
        is_active: form.value.is_active
      })
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

function confirmDelete(ch) {
  if (isPresetChannel(ch.channel_code || ch.id)) return
  ElMessageBox.confirm(
    `确定要删除渠道「${ch.channel_name || ch.name}」吗？删除后不可恢复。`,
    '删除确认',
    { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
  )
    .then(async () => {
      try {
        await notificationsApi.deleteNotificationChannel(ch.id)
        ElMessage.success('已删除')
        loadChannels()
      } catch (e) {
        console.error(e)
        ElMessage.error('删除失败')
      }
    })
    .catch(() => {})
}

onMounted(() => loadChannels())
</script>

<style scoped>
.channel-config-page {
  min-height: 100%;
  padding: 24px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header :deep(.back-btn) {
  margin-bottom: 12px;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, #67c23a 0%, #85ce61 100%);
  border-radius: 12px;
  padding: 24px;
  color: #fff;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-actions {
  flex-shrink: 0;
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

.channel-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 20px;
  min-height: 120px;
}

.channel-empty {
  margin-top: 24px;
}

.channel-card {
  position: relative;
  background: var(--cockpit-card-bg);
  backdrop-filter: blur(var(--cockpit-blur));
  -webkit-backdrop-filter: blur(var(--cockpit-blur));
  border: 1px solid var(--cockpit-card-border);
  border-radius: 12px;
  overflow: hidden;
}

.channel-card:hover {
  border-color: rgba(64, 158, 255, 0.4);
  box-shadow: 0 8px 32px rgba(64, 158, 255, 0.15);
}

.card-glow-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  border-radius: 4px 0 0 4px;
}

.card-glow-bar.dingtalk {
  background: linear-gradient(180deg, #0089ff, #00c6ff);
}

.card-glow-bar.wechat_work {
  background: linear-gradient(180deg, #07c160, #2aae67);
}

.card-glow-bar.email {
  background: linear-gradient(180deg, #409eff, #66b1ff);
}

.card-glow-bar.feishu,
.card-glow-bar.slack,
.card-glow-bar.default {
  background: linear-gradient(180deg, #64748b, #94a3b8);
}

.card-body {
  display: flex;
  gap: 20px;
  padding: 24px 24px 16px 20px;
}

.channel-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  flex-shrink: 0;
}

.channel-icon.dingtalk {
  background: rgba(0, 137, 255, 0.15);
  color: #0089ff;
}

.channel-icon.wechat_work {
  background: rgba(7, 193, 96, 0.15);
  color: #07c160;
}

.channel-icon.email {
  background: rgba(64, 158, 255, 0.15);
  color: #409eff;
}

.channel-icon.feishu,
.channel-icon.slack,
.channel-icon.default {
  background: rgba(100, 116, 139, 0.15);
  color: #64748b;
}

.channel-info {
  flex: 1;
  min-width: 0;
}

.channel-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--app-text-primary);
  margin: 0 0 8px 0;
}

.channel-desc {
  font-size: 14px;
  color: var(--app-text-secondary);
  margin: 0 0 12px 0;
  line-height: 1.5;
}

.channel-guide {
  background: rgba(0, 0, 0, 0.03);
  border-radius: 8px;
  padding: 12px 14px;
}

.guide-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.guide-steps {
  font-size: 13px;
  color: var(--app-text-secondary);
  line-height: 1.6;
  margin: 6px 0 0 0;
}

.card-actions {
  display: flex;
  gap: 8px;
  padding: 0 24px 20px 20px;
  justify-content: flex-end;
}

.form-tip {
  font-size: 12px;
  color: var(--app-text-muted);
  margin-top: 4px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
