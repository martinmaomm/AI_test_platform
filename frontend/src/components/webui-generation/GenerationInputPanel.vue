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
        <div class="description-actions"><span>按“网址、测试账号、页面路径、探索操作、脚本验证要求”填写。可先插入示例，再替换为你的测试目标。</span><el-button text type="primary" :disabled="busy || paused" @click="insertExample">插入示例</el-button></div>
        <el-input v-model="form.description" type="textarea" :rows="11" resize="vertical" maxlength="2000" show-word-limit :disabled="busy || paused" placeholder="先填写完整 http(s) 网址；例如：使用 Playwright MCP 登录后进入“权限 > 菜单列表”，探索新增、编辑、删除流程，再生成逐步验证的 Python Playwright 脚本。" />
      </el-form-item>
      <el-collapse class="input-collapse">
        <el-collapse-item title="编写提示" name="tips">
          <ol>
            <li>第一行填写目标网站的完整 http(s) 网址；需要登录时，写明测试账号和密码。示例中的网址和账号请按实际情况替换，不要使用生产账号。</li>
            <li>说明登录后进入的页面路径，例如“权限 > 菜单列表”；可替换为其他网站的模块和页面。</li>
            <li>列出需要 AI 探索的操作，例如新增、编辑、删除。页面元素和定位方式由 Playwright MCP 探索，无需手动提供。</li>
            <li>涉及新增、编辑数据时，说明哪些字段需要唯一，例如菜单名；唯一数据使用 time.time_ns() 生成，默认只操作本轮测试数据。</li>
            <li>最后写明：探索完成后生成完整的 Python Playwright 脚本。按顺序列出验证要求，例如新增后验证存在、编辑后验证更新内容、删除后查询验证不存在；具体元素和断言可由 AI 根据页面观察确定。</li>
          </ol>
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

const EXAMPLE_DESCRIPTION = `http://192.168.31.188:9990/
使用 Playwright MCP 打开目标页面并登录权限模块。
登录账号：test，密码：123456。
登录后进入“权限 > 菜单列表”，
进行新增、编辑、删除菜单的探索操作。

探索完成后，生成完整的 Python Playwright 脚本：

1. 新增、编辑的菜单名需要确保唯一性，唯一数据使用 time.time_ns() 生成；
2. 执行新增并验证；
3. 执行编辑并验证更新内容；
4. 执行删除；
5. 查询并验证数据不存在。`

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
