<template>
  <section class="generation-card input-panel">
    <div class="card-heading">
      <h4>生成配置</h4>
      <p>选择环境并描述业务场景。页面探索默认只读，不会提交业务数据。</p>
    </div>
    <el-alert title="正式脚本不会保存本次密码；需要登录时仅会引用环境变量，例如 UI_TEST_USERNAME 和 UI_TEST_PASSWORD。" type="info" :closable="false" show-icon />
    <el-alert v-if="paused" title="当前任务已暂停，请先在右侧“需要你处理”区域补充信息。" type="warning" :closable="false" show-icon class="paused-alert" />

    <el-form class="generation-form" label-position="top" @submit.prevent="submit">
      <el-form-item label="WebUI 测试环境" required>
        <el-select v-model="form.environmentId" :loading="loadingEnvironments" :disabled="busy || paused" placeholder="请选择启用的 WebUI 环境">
          <el-option v-for="environment in environments" :key="environment.id" :label="environment.name" :value="environment.id" />
        </el-select>
        <div class="base-url">Base URL：{{ selectedEnvironmentBaseUrl || '该环境未配置 Base URL' }}</div>
      </el-form-item>
      <el-form-item label="起始相对路径" required>
        <el-input v-model.trim="form.startPath" :disabled="busy || paused" placeholder="/" maxlength="500" />
        <div class="field-help">只填写环境内路径，例如 <code>/permission/users</code>；不需要填写完整域名。</div>
      </el-form-item>
      <el-form-item label="测试描述" required>
        <div class="description-actions"><span>请写清目标、主要步骤、成功标准和清理约束。</span><el-button text type="primary" :disabled="busy || paused" @click="insertExample">插入示例</el-button></div>
        <el-input v-model="form.description" type="textarea" :rows="11" resize="vertical" maxlength="2000" show-word-limit :disabled="busy || paused" placeholder="例如：验证用户列表中的新增、查询、编辑、删除流程。" />
      </el-form-item>
      <el-collapse class="input-collapse">
        <el-collapse-item title="编写提示" name="tips"><ol><li>说明测试目标和要进入的页面。</li><li>按顺序列出主要操作，并写明每一步如何判断成功。</li><li>涉及创建数据时，说明唯一数据和清理要求。</li><li>明确探索阶段不得提交的操作，例如新增、编辑或删除。</li></ol></el-collapse-item>
        <el-collapse-item title="本次探索登录信息（按需填写）" name="credentials">
          <el-alert type="warning" :closable="false" show-icon title="仅供本次页面探索使用；提交后立即从页面内存清除，不会写入脚本、生成记录或本地存储。" />
          <div class="credential-grid"><el-input v-model="form.username" autocomplete="off" placeholder="用户名" :disabled="busy || paused" /><el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="密码" :disabled="busy || paused" /></div>
        </el-collapse-item>
      </el-collapse>
      <el-form-item label="本次使用模型">
        <el-select v-model="form.modelConfigId" :loading="loadingModels" :disabled="busy || paused" placeholder="请选择启用的 LLM">
          <el-option v-for="model in modelConfigs" :key="model.id" :label="modelLabel(model)" :value="model.id" />
        </el-select>
        <div class="field-help">只显示已启用的语言模型；平台会锁定本次选择，生成中不会自动切换模型。</div>
      </el-form-item>
      <div class="form-actions"><el-button v-if="paused" type="warning" size="large" disabled>请先处理当前暂停任务</el-button><el-button v-else-if="!busy" type="primary" size="large" :disabled="!formValid" :loading="submitting" native-type="submit">分析并生成脚本</el-button><el-button v-else type="danger" size="large" :loading="cancelling" :disabled="cancelling" @click="emit('cancel')">取消生成</el-button></div>
    </el-form>
  </section>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'

const EXAMPLE_DESCRIPTION = `目标：验证“权限 > 用户列表”的新增、查询、编辑和删除流程。

前置条件：使用测试账号登录；登录信息从本次任务的临时登录信息或环境变量读取。

步骤：
1. 登录后进入“权限 > 用户列表”；
2. 使用唯一名称和账号新增用户，并验证列表中出现该用户；
3. 编辑本轮新增用户的昵称，并验证更新成功；
4. 删除本轮新增用户，并查询验证该用户不存在。

约束：
- 唯一数据使用 time.time_ns() 生成；
- 不操作已有业务数据；
- 使用 try/finally 清理本轮创建的数据；
- 探索阶段只查看页面和打开表单，不提交新增、编辑或删除；
- 登录密码不得写入脚本、日志、截图或报告。`

const props = defineProps({ projectId: { type: [Number, String], default: null }, environments: { type: Array, default: () => [] }, modelConfigs: { type: Array, default: () => [] }, loadingEnvironments: Boolean, loadingModels: Boolean, busy: Boolean, paused: Boolean, submitting: Boolean, cancelling: Boolean })
const emit = defineEmits(['submit', 'cancel'])
const form = reactive({ environmentId: null, startPath: '/', description: '', username: '', password: '', modelConfigId: null })
const selectedEnvironment = computed(() => props.environments.find(item => item.id === form.environmentId) || null)
const selectedEnvironmentBaseUrl = computed(() => selectedEnvironment.value?.config?.base_url || selectedEnvironment.value?.base_url || '')
const formValid = computed(() => form.environmentId !== null && form.modelConfigId !== null && Number.isInteger(Number(form.environmentId)) && form.startPath.startsWith('/') && form.description.trim().length > 0 && form.description.length <= 2000 && Number.isInteger(Number(form.modelConfigId)))
const modelLabel = (model) => `${model.provider || 'LLM'} · ${model.model_name || '未命名模型'}`
watch(() => props.environments, (items) => { if (!items.some(item => item.id === form.environmentId)) form.environmentId = items[0]?.id || null }, { immediate: true, deep: true })
watch(() => props.modelConfigs, (items) => { if (!items.some(item => item.id === form.modelConfigId)) form.modelConfigId = items[0]?.id || null }, { immediate: true, deep: true })
watch(() => props.projectId, () => { form.environmentId = null; form.username = ''; form.password = '' })
const insertExample = () => { form.description = EXAMPLE_DESCRIPTION }
const submit = () => {
  if (!form.startPath.startsWith('/')) return ElMessage.warning('起始路径必须以 / 开头，不能填写完整 URL。')
  if (!formValid.value) return ElMessage.warning('请先选择启用的 WebUI 环境和模型，并填写测试描述。')
  if ((form.username && !form.password) || (!form.username && form.password)) return ElMessage.warning('如需登录探索，请同时填写用户名和密码。')
  const temporaryCredentials = form.username && form.password ? { username: form.username, password: form.password } : undefined
  emit('submit', { description: form.description.trim(), environment_id: Number(form.environmentId), start_path: form.startPath, model_config_id: Number(form.modelConfigId), ...(temporaryCredentials ? { temporary_credentials: temporaryCredentials } : {}) })
  form.username = ''
  form.password = ''
}
</script>

<style scoped>
.generation-card { padding: 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }
.card-heading h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }
.card-heading p, .field-help, .base-url, .description-actions { color: var(--app-text-secondary); font-size: 13px; }
.card-heading p { margin: 6px 0 16px; }.paused-alert { margin-top: 12px; }.generation-form { margin-top: 18px; }.base-url, .field-help { margin-top: 7px; word-break: break-all; }
.description-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; margin-bottom: 6px; }.input-collapse { margin: 4px 0 18px; }
.input-collapse ol { margin: 0; padding-left: 20px; line-height: 1.8; color: var(--app-text-secondary); }.credential-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }.form-actions { display: flex; justify-content: flex-end; padding-top: 4px; }
@media (max-width: 700px) { .credential-grid { grid-template-columns: 1fr; }.description-actions { align-items: flex-start; flex-direction: column; }.form-actions :deep(.el-button) { width: 100%; } }
</style>
