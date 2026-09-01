<template>
  <section class="generation-card input-panel">
    <div class="card-heading">
      <h4>测试流程配置</h4>
      <p>选择环境并描述业务场景。系统会在确认的目标范围内探索并验证测试流程。</p>
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
      <el-form-item label="业务模块">
        <el-select v-model="form.moduleId" :loading="loadingModules" :disabled="busy || paused" placeholder="未选择时保存到默认模块" clearable>
          <el-option v-for="module in flatModules" :key="module.id" :label="module.label" :value="module.id" />
        </el-select>
        <div class="field-help">业务模块只用于分类，不参与页面探索、元素定位或脚本复用。</div>
      </el-form-item>
      <el-form-item label="起始相对路径" required>
        <el-input v-model.trim="form.startPath" :disabled="busy || paused" placeholder="/" maxlength="500" />
        <div class="field-help">只填写环境内路径，例如 <code>/permission/users</code>；不需要填写完整域名。</div>
      </el-form-item>
      <el-form-item label="页面探索总超时时间（秒）">
        <el-input-number v-model="form.explorationTimeoutSeconds" :min="explorationTimeoutMin" :max="explorationTimeoutMax" :step="60" :disabled="busy || paused" placeholder="服务器默认值" />
        <div v-if="explorationSettings" class="field-help">有效范围 {{ explorationTimeoutMin }}-{{ explorationTimeoutMax }} 秒；仅影响本次任务，默认值来自服务器 env。</div>
        <div v-else class="field-help">服务器默认值暂不可用；可留空，创建时由服务器 env 决定。</div>
      </el-form-item>
      <el-form-item label="测试描述" required>
        <div class="description-actions"><span>请写清目标、主要步骤、成功标准、目标数据范围和清理约束；系统不依赖固定按钮名称或业务词语。</span><el-button text type="primary" :disabled="busy || paused" @click="insertExample">插入示例</el-button></div>
        <el-input v-model="form.description" type="textarea" :rows="11" resize="vertical" maxlength="2000" show-word-limit :disabled="busy || paused" placeholder="例如：登录后进入目标页面，完成指定流程并验证最终页面状态。" />
      </el-form-item>
      <el-collapse class="input-collapse">
        <el-collapse-item title="编写提示" name="tips"><ol><li>说明测试目标和要进入的页面。</li><li>按顺序列出主要操作，并写明每一步如何判断成功。</li><li>涉及改变页面数据时，说明唯一测试数据和清理要求；未特别说明时，默认只操作本轮测试数据。</li><li>如目标要求仅只读，请在描述中明确；系统会尊重该限制，不执行写操作。审批、付款、发布、上传等额外动作目前不在授权范围，请修改测试目标。</li></ol></el-collapse-item>
        <el-collapse-item title="本轮测试登录信息（按需填写）" name="credentials">
          <el-alert type="warning" :closable="false" show-icon title="仅供本轮测试流程使用；提交后立即从页面内存清除，不会写入脚本、生成记录或本地存储。" />
          <div class="credential-grid"><el-input v-model="form.username" autocomplete="off" placeholder="用户名" :disabled="busy || paused" /><el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="密码" :disabled="busy || paused" /></div>
        </el-collapse-item>
      </el-collapse>
      <el-form-item label="本次使用模型">
        <el-select v-model="form.modelConfigId" :loading="loadingModels" :disabled="busy || paused" placeholder="请选择启用的 LLM">
          <el-option v-for="model in modelConfigs" :key="model.id" :label="modelLabel(model)" :value="model.id" />
        </el-select>
        <div class="field-help">只显示已启用的语言模型；平台会锁定本次选择，生成中不会自动切换模型。</div>
      </el-form-item>
      <el-alert class="execution-scope-alert" type="warning" :closable="false" show-icon title="开始后会按测试目标在页面中执行真实操作。默认只操作本轮测试数据并尝试清理；清理失败或发现残留会明确告知，并保留已获得的证据及已有草稿供人工处理。测试描述明确仅只读时，不执行写操作。审批、付款、发布、上传等额外高风险动作目前不在授权范围。" />
      <div class="form-actions"><el-button v-if="paused" type="warning" size="large" disabled>请先处理当前暂停任务</el-button><el-button v-else-if="!busy" type="primary" size="large" :disabled="!formValid" :loading="submitting" native-type="submit">分析并生成脚本</el-button><el-button v-else type="danger" size="large" :loading="cancelling" :disabled="cancelling" @click="emit('cancel')">取消生成</el-button></div>
    </el-form>
  </section>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { modelConfigurationLabel } from '@/composables/webUIScriptGenerationPresentation'
import { EXPLORATION_TIMEOUT_MAX_SECONDS, EXPLORATION_TIMEOUT_MIN_SECONDS, explorationTimeoutPayload, isExplorationTimeoutValid } from '@/composables/webuiExplorationTimeout'

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
- 清理失败或发现残留时，保留草稿并明确报告；
- 登录密码不得写入脚本、日志、截图或报告。`

const props = defineProps({ projectId: { type: [Number, String], default: null }, environments: { type: Array, default: () => [] }, modules: { type: Array, default: () => [] }, modelConfigs: { type: Array, default: () => [] }, explorationSettings: { type: Object, default: null }, loadingEnvironments: Boolean, loadingModules: Boolean, loadingModels: Boolean, busy: Boolean, paused: Boolean, submitting: Boolean, cancelling: Boolean, credentialClearVersion: { type: Number, default: 0 } })
const emit = defineEmits(['submit', 'cancel'])
const form = reactive({ environmentId: null, moduleId: null, startPath: '/', description: '', username: '', password: '', modelConfigId: null, explorationTimeoutSeconds: null })
const flattenModules = (items, depth = 0) => items.flatMap(item => [
  { ...item, label: `${'　'.repeat(depth)}${item.name}${item.is_default ? '（默认）' : ''}` },
  ...flattenModules(item.children || [], depth + 1)
])
const flatModules = computed(() => flattenModules(props.modules))
const selectedEnvironment = computed(() => props.environments.find(item => item.id === form.environmentId) || null)
const selectedEnvironmentBaseUrl = computed(() => selectedEnvironment.value?.config?.base_url || selectedEnvironment.value?.base_url || '')
const explorationTimeoutMin = computed(() => props.explorationSettings?.min ?? EXPLORATION_TIMEOUT_MIN_SECONDS)
const explorationTimeoutMax = computed(() => props.explorationSettings?.max ?? EXPLORATION_TIMEOUT_MAX_SECONDS)
const formValid = computed(() => form.environmentId !== null && form.modelConfigId !== null && Number.isInteger(Number(form.environmentId)) && form.startPath.startsWith('/') && form.description.trim().length > 0 && form.description.length <= 2000 && Number.isInteger(Number(form.modelConfigId)) && isExplorationTimeoutValid(form.explorationTimeoutSeconds, props.explorationSettings))
const modelLabel = modelConfigurationLabel
watch(() => props.environments, (items) => { if (!items.some(item => item.id === form.environmentId)) form.environmentId = items[0]?.id || null }, { immediate: true, deep: true })
watch(() => props.modelConfigs, (items) => { if (!items.some(item => item.id === form.modelConfigId)) form.modelConfigId = items[0]?.id || null }, { immediate: true, deep: true })
watch(() => props.modules, (items) => {
  const all = flattenModules(items)
  if (!all.some(item => item.id === form.moduleId)) form.moduleId = all.find(item => item.is_default)?.id || null
}, { immediate: true, deep: true })
watch(() => props.projectId, () => { form.environmentId = null; form.moduleId = null; form.username = ''; form.password = ''; form.explorationTimeoutSeconds = null })
watch(() => props.explorationSettings, (settings) => { if (form.explorationTimeoutSeconds === null && settings) form.explorationTimeoutSeconds = settings.timeout }, { immediate: true })
watch(() => props.credentialClearVersion, () => { form.username = ''; form.password = '' })
const insertExample = () => { form.description = EXAMPLE_DESCRIPTION }
const submit = () => {
  if (!form.startPath.startsWith('/')) return ElMessage.warning('起始路径必须以 / 开头，不能填写完整 URL。')
  if (!formValid.value) return ElMessage.warning('请先选择启用的 WebUI 环境和模型，并填写测试描述。')
  if ((form.username && !form.password) || (!form.username && form.password)) return ElMessage.warning('如需登录探索，请同时填写用户名和密码。')
  const temporaryCredentials = form.username && form.password ? { username: form.username, password: form.password } : undefined
  emit('submit', { description: form.description.trim(), environment_id: Number(form.environmentId), ...(form.moduleId ? { module_id: Number(form.moduleId) } : {}), start_path: form.startPath, model_config_id: Number(form.modelConfigId), ...explorationTimeoutPayload(form.explorationTimeoutSeconds, props.explorationSettings), ...(temporaryCredentials ? { temporary_credentials: temporaryCredentials } : {}) })
}
</script>

<style scoped>
.generation-card { padding: 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }
.card-heading h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }
.card-heading p, .field-help, .base-url, .description-actions { color: var(--app-text-secondary); font-size: 13px; }
.card-heading p { margin: 6px 0 16px; }.paused-alert { margin-top: 12px; }.generation-form { margin-top: 18px; }.base-url, .field-help { margin-top: 7px; word-break: break-all; }
.description-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; margin-bottom: 6px; }.input-collapse { margin: 4px 0 18px; }.execution-scope-alert { margin: 0 0 16px; }
.input-collapse ol { margin: 0; padding-left: 20px; line-height: 1.8; color: var(--app-text-secondary); }.credential-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }.form-actions { display: flex; justify-content: flex-end; padding-top: 4px; }
@media (max-width: 700px) { .credential-grid { grid-template-columns: 1fr; }.description-actions { align-items: flex-start; flex-direction: column; }.form-actions :deep(.el-button) { width: 100%; } }
</style>
