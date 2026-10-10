<template>
  <el-dialog :model-value="modelValue" :title="plan ? '编辑安全测试计划' : '新建安全测试计划'" width="min(1040px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="close" @closed="reset">
    <el-form label-position="top" :disabled="saving">
      <el-form-item label="计划名称"><el-input v-model="form.name" maxlength="200" placeholder="例如：订单查询认证与权限检查" /></el-form-item>
      <el-row :gutter="20">
        <el-col :span="16"><el-form-item label="允许测试的来源地址（每行一个）"><el-input v-model="form.originsText" type="textarea" :rows="2" placeholder="https://test.example.com&#10;http://api.test.example.com:8080" /><div class="hint">只填写协议、域名和端口。页面地址和 API 地址不同，请填写实际接口的来源。</div></el-form-item></el-col>
        <el-col :span="8"><el-form-item label="访问范围"><el-checkbox v-model="form.allow_private_network">允许访问内网地址</el-checkbox><div class="hint">被测服务使用局域网 IP 或解析到内网时开启；仍只请求上方允许的来源。</div></el-form-item></el-col>
      </el-row>
      <el-divider content-position="left">测试账号</el-divider>
      <p class="hint">账号 A 用于正常请求，另一个账号 B 用于检查越权。填写已有测试账号的请求头，例如 Authorization、Cookie。凭据加密保存，保存后不会回显。第一版不支持 URL 中的 Token 等认证参数。</p>
      <el-alert v-if="plan?.identities?.unavailable" title="已保存账号暂时无法读取，请重新配置账号后执行。" type="warning" :closable="false" />
      <p v-if="plan" class="hint">账号 A：{{ plan.identities?.a?.configured ? `已配置（${plan.identities.a.name}）` : '未配置' }}；账号 B：{{ plan.identities?.b?.configured ? `已配置（${plan.identities.b.name}）` : '未配置' }}</p>
      <el-radio-group v-model="form.identityMode"><el-radio v-if="plan" label="keep">保留已保存账号</el-radio><el-radio label="replace">{{ plan ? '重新填写全部账号' : '填写测试账号' }}</el-radio><el-radio label="clear">清除账号，使用匿名请求</el-radio></el-radio-group>
      <el-alert v-if="plan && form.identityMode === 'replace'" title="本次填写将整体替换账号 A 和 B；留空的账号会被清除。" type="warning" :closable="false" class="space" />
      <el-row v-if="form.identityMode === 'replace'" :gutter="20" class="space">
        <el-col v-for="key in ['a', 'b']" :key="key" :span="12"><el-form-item :label="`账号 ${key.toUpperCase()} 名称`"><el-input v-model="form[`${key}Name`]" maxlength="80" /></el-form-item><el-form-item :label="`账号 ${key.toUpperCase()} 请求头 JSON（选填）`"><el-input v-model="form[`${key}Headers`]" type="textarea" :rows="3" :placeholder="'{\n  &quot;Authorization&quot;: &quot;Bearer 你的测试令牌&quot;\n}'" autocomplete="off" spellcheck="false" /></el-form-item></el-col>
      </el-row>
      <el-divider content-position="left">检查接口（{{ form.endpoints.length }} / 20）</el-divider>
      <div class="actions"><el-button :disabled="form.endpoints.length >= 20" @click="form.endpoints.push(newEndpoint())">添加接口</el-button><el-button :disabled="remaining === 0" @click="sourceOpen = true">从 API 项目导入</el-button></div>
      <el-collapse v-model="expanded">
        <el-collapse-item v-for="(endpoint, index) in form.endpoints" :key="index" :name="index"><template #title><strong>{{ index + 1 }}. {{ endpoint.name || endpoint.url || '未填写接口' }}</strong></template>
          <el-row :gutter="12"><el-col :span="6"><el-form-item label="名称"><el-input v-model="endpoint.name" maxlength="120" placeholder="接口名称" /></el-form-item></el-col><el-col :span="3"><el-form-item label="方法"><el-select v-model="endpoint.method" @change="changeMethod(endpoint)"><el-option label="GET" value="GET" /><el-option label="HEAD" value="HEAD" /></el-select></el-form-item></el-col><el-col :span="15"><el-form-item label="完整请求 URL（包含 Query 参数）"><el-input v-model="endpoint.url" placeholder="https://test.example.com/items/1?locale=zh-CN" /></el-form-item></el-col></el-row>
          <el-form-item label="通用请求头 JSON"><el-input v-model="endpoint.headersText" type="textarea" :rows="2" placeholder='{ "Accept-Language": "zh-CN" }' /><div class="hint">例如 Accept、Accept-Language。Authorization、Cookie 等凭据填写在账号中。</div></el-form-item>
          <el-checkbox v-model="endpoint.auth_required">此接口要求登录</el-checkbox><el-checkbox v-model="endpoint.owner_only_a">此 URL 的资源仅允许账号 A 访问</el-checkbox>
          <div class="hint">越权检查需要账号 B，以及明确的账号 A 专属资源；公开资源请勿勾选。</div>
          <el-row :gutter="12" class="space"><el-col :span="12"><el-form-item label="受保护资源的证据字段（选填）"><el-input v-model="endpoint.proofPath" :disabled="endpoint.method === 'HEAD'" placeholder="例如 body.id 或 body.data.orderId" /></el-form-item></el-col><el-col :span="12"><el-form-item label="字段预期值（JSON 标量）"><el-input v-model="endpoint.proofExpected" :disabled="endpoint.method === 'HEAD'" placeholder='例如 1、"order-1" 或 true' /></el-form-item></el-col></el-row>
          <p class="hint">用于确认响应确实包含目标资源，避免将 HTTP 200 的登录页或业务失败当成功。GET 的路径和预期值需同时填写（HEAD 不支持响应体证据）；未配置时，相似响应最多标记为“疑似”。</p>
          <el-button type="danger" plain size="small" @click="form.endpoints.splice(index, 1)">移除此接口</el-button>
        </el-collapse-item>
      </el-collapse>
      <el-divider content-position="left">检查方向与执行限额</el-divider>
      <el-checkbox-group v-model="form.checks"><el-checkbox v-for="check in CHECKS" :key="check.value" :label="check.value">{{ check.label }}</el-checkbox></el-checkbox-group>
      <el-row :gutter="20" class="space"><el-col :span="8"><el-form-item label="请求总预算（1–100）"><el-input-number v-model="form.max_requests" :min="1" :max="100" :precision="0" /><div class="hint">包括正常请求和检查变体；用尽后其余检查显示未执行。</div></el-form-item></el-col><el-col :span="8"><el-form-item label="每秒最多请求数（0.1–2）"><el-input-number v-model="form.rate_per_second" :min="0.1" :max="2" :step="0.1" :precision="1" /></el-form-item></el-col><el-col :span="8"><el-form-item label="单次请求等待（1–30 秒）"><el-input-number v-model="form.timeout_seconds" :min="1" :max="30" :precision="0" /></el-form-item></el-col></el-row>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </el-form>
    <template #footer><el-button :disabled="saving" @click="close(false)">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存计划</el-button></template>
    <SecuritySourceImport v-model="sourceOpen" :project-id="projectId" :remaining="remaining" @import="importSelected" />
  </el-dialog>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import SecuritySourceImport from './SecuritySourceImport.vue'
import { CHECKS, planForm, newEndpoint, buildPlanPayload, importEndpoints } from './securityState'
const props = defineProps({ modelValue: Boolean, plan: Object, projectId: [String, Number], saving: Boolean, serverError: String })
const emit = defineEmits(['update:modelValue', 'save'])
const form = ref(planForm()), expanded = ref([0]), sourceOpen = ref(false), localError = ref('')
const error = computed(() => localError.value || props.serverError)
const remaining = computed(() => 20 - form.value.endpoints.filter((item) => item.url.trim() || item.name.trim()).length)
function close(value) { if (!props.saving) emit('update:modelValue', value) }
function reset() { form.value = planForm(); sourceOpen.value = false; localError.value = ''; expanded.value = [0] }
function save() { localError.value = ''; try { emit('save', buildPlanPayload(form.value)) } catch (cause) { localError.value = cause.message } }
function importSelected(items) { try { form.value.endpoints = importEndpoints(form.value.endpoints, items); expanded.value = [form.value.endpoints.length - items.length]; localError.value = '' } catch (cause) { localError.value = cause.message } }
function changeMethod(endpoint) { if (endpoint.method === 'HEAD') { endpoint.proofPath = ''; endpoint.proofExpected = '' } }
watch([() => props.modelValue, () => props.plan], ([visible]) => { if (visible) { form.value = planForm(props.plan); localError.value = ''; expanded.value = [0] } })
watch(() => props.projectId, () => { reset(); emit('update:modelValue', false) })
</script>
<style scoped>.hint { color: var(--app-text-muted); font-size: 13px; line-height: 1.65; }.space { margin-top: 14px; }.actions { display: flex; gap: 8px; margin-bottom: 16px; }.el-collapse-item strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 90%; }.el-checkbox-group { display: flex; flex-wrap: wrap; }</style>
