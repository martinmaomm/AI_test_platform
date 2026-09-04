<template>
  <section class="generation-card input-panel">
    <div class="card-heading">
      <h4>测试流程配置</h4>
      <p>在描述中提供完整网址和测试目标；需要登录时，再填写测试账号和密码。</p>
    </div>
    <el-alert title="仅供测试使用，账号密码可能出现在生成记录、日志、截图或脚本，请勿使用生产账号。" type="warning" :closable="false" show-icon />
    <el-alert v-if="paused" title="当前任务已暂停，请先在右侧“需要你处理”区域补充信息。" type="warning" :closable="false" show-icon class="paused-alert" />

    <el-form class="generation-form" label-position="top" @submit.prevent="submit">
      <el-form-item label="业务模块">
        <el-select v-model="form.moduleId" :loading="loadingModules" :disabled="busy || paused" placeholder="未选择时保存到默认模块" clearable>
          <el-option v-for="module in flatModules" :key="module.id" :label="module.label" :value="module.id" />
        </el-select>
        <div class="field-help">业务模块只用于分类，不参与页面探索、元素定位或脚本复用。</div>
      </el-form-item>
      <el-form-item label="页面探索总超时时间（秒）">
        <el-input-number v-model="form.explorationTimeoutSeconds" :min="explorationTimeoutMin" :max="explorationTimeoutMax" :step="60" :disabled="busy || paused" placeholder="服务器默认值" />
        <div v-if="explorationSettings" class="field-help">有效范围 {{ explorationTimeoutMin }}-{{ explorationTimeoutMax }} 秒；仅影响本次任务，默认值来自服务器 env。</div>
        <div v-else class="field-help">服务器默认值暂不可用；可留空，创建时由服务器 env 决定。</div>
      </el-form-item>
      <el-form-item label="测试描述" required>
        <div class="description-actions"><span>必须写明一个完整 http(s) URL；如需登录，请在此写入测试账号和密码。再说明目标、主要步骤、成功标准、数据范围和清理约束。</span><el-button text type="primary" :disabled="busy || paused" @click="insertExample">插入示例</el-button></div>
        <el-input v-model="form.description" type="textarea" :rows="11" resize="vertical" maxlength="2000" show-word-limit :disabled="busy || paused" placeholder="例如：访问 https://example.test/login，使用测试账号登录后完成指定流程并验证最终页面状态。" />
      </el-form-item>
      <el-collapse class="input-collapse">
        <el-collapse-item title="编写提示" name="tips"><ol><li>说明测试目标和要进入的页面。</li><li>按顺序列出主要操作，并写明每一步如何判断成功。</li><li>涉及改变页面数据时，说明唯一测试数据和清理要求；未特别说明时，默认只操作本轮测试数据。</li><li>如目标要求仅只读，请在描述中明确；系统会尊重该限制，不执行写操作。审批、付款、发布、上传等额外动作目前不在授权范围，请修改测试目标。</li></ol></el-collapse-item>
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

目标地址：https://example.test/admin/users
测试账号：username=webui_demo_user，password=webui_demo_password（仅示例，非真实账号）。

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
- 测试环境凭据可能出现在生成记录、日志、截图或脚本，请勿使用生产账号。`

const props = defineProps({ projectId: { type: [Number, String], default: null }, modules: { type: Array, default: () => [] }, modelConfigs: { type: Array, default: () => [] }, explorationSettings: { type: Object, default: null }, loadingModules: Boolean, loadingModels: Boolean, busy: Boolean, paused: Boolean, submitting: Boolean, cancelling: Boolean })
const emit = defineEmits(['submit', 'cancel'])
const form = reactive({ moduleId: null, description: '', modelConfigId: null, explorationTimeoutSeconds: null })
const flattenModules = (items, depth = 0) => items.flatMap(item => [
  { ...item, label: `${'　'.repeat(depth)}${item.name}${item.is_default ? '（默认）' : ''}` },
  ...flattenModules(item.children || [], depth + 1)
])
const flatModules = computed(() => flattenModules(props.modules))
const explorationTimeoutMin = computed(() => props.explorationSettings?.min ?? EXPLORATION_TIMEOUT_MIN_SECONDS)
const explorationTimeoutMax = computed(() => props.explorationSettings?.max ?? EXPLORATION_TIMEOUT_MAX_SECONDS)
const formValid = computed(() => form.modelConfigId !== null && form.description.trim().length > 0 && form.description.length <= 2000 && Number.isInteger(Number(form.modelConfigId)) && isExplorationTimeoutValid(form.explorationTimeoutSeconds, props.explorationSettings))
const modelLabel = modelConfigurationLabel
watch(() => props.modelConfigs, (items) => { if (!items.some(item => item.id === form.modelConfigId)) form.modelConfigId = items[0]?.id || null }, { immediate: true, deep: true })
watch(() => props.modules, (items) => {
  const all = flattenModules(items)
  if (!all.some(item => item.id === form.moduleId)) form.moduleId = all.find(item => item.is_default)?.id || null
}, { immediate: true, deep: true })
watch(() => props.projectId, () => { form.moduleId = null; form.explorationTimeoutSeconds = null })
watch(() => props.explorationSettings, (settings) => { if (form.explorationTimeoutSeconds === null && settings) form.explorationTimeoutSeconds = settings.timeout }, { immediate: true })
const insertExample = () => { form.description = EXAMPLE_DESCRIPTION }
const submit = () => {
  if (!formValid.value) return ElMessage.warning('请先选择模型，并填写包含完整 URL 的测试描述。')
  emit('submit', { description: form.description.trim(), ...(form.moduleId ? { module_id: Number(form.moduleId) } : {}), model_config_id: Number(form.modelConfigId), ...explorationTimeoutPayload(form.explorationTimeoutSeconds, props.explorationSettings) })
}
</script>

<style scoped>
.generation-card { padding: 20px; background: var(--page-content-bg); border: 1px solid var(--app-border); border-radius: 10px; }
.card-heading h4 { margin: 0; color: var(--app-text-primary); font-size: 16px; }
.card-heading p, .field-help, .description-actions { color: var(--app-text-secondary); font-size: 13px; }
.card-heading p { margin: 6px 0 16px; }.paused-alert { margin-top: 12px; }.generation-form { margin-top: 18px; }.field-help { margin-top: 7px; word-break: break-all; }
.description-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; margin-bottom: 6px; }.input-collapse { margin: 4px 0 18px; }.execution-scope-alert { margin: 0 0 16px; }
.input-collapse ol { margin: 0; padding-left: 20px; line-height: 1.8; color: var(--app-text-secondary); }.form-actions { display: flex; justify-content: flex-end; padding-top: 4px; }
@media (max-width: 700px) { .description-actions { align-items: flex-start; flex-direction: column; }.form-actions :deep(.el-button) { width: 100%; } }
</style>
