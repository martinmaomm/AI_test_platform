export const CHECKS = [
  { value: 'authentication', label: '登录与认证', description: '比较账号 A、匿名和无效凭据请求。' },
  { value: 'authorization', label: '权限与越权', description: '使用账号 B 检查账号 A 独有的资源。' },
  { value: 'information', label: '信息泄露', description: '检查响应中的敏感字段与内部错误。' },
  { value: 'configuration', label: '网站基础配置', description: '检查 Cookie、安全响应头和 CORS。' },
]
export const checkLabel = (value) => CHECKS.find((item) => item.value === value)?.label || value || '—'
export const statusLabel = (value) => ({ queued: '排队中', running: '执行中', completed: '已完成', failed: '执行失败', cancelled: '已取消', passed: '检查通过', confirmed: '已确认风险', suspected: '疑似风险', inconclusive: '数据不足', error: '检查异常', skipped: '未执行' })[value] || value || '—'
export const statusType = (value) => ({ completed: 'success', passed: 'success', confirmed: 'danger', failed: 'danger', error: 'danger', suspected: 'warning', inconclusive: 'warning' })[value] || 'info'
export const isActiveTask = (task) => ['queued', 'running'].includes(task?.status)
export const canExportTask = (task) => task?.kind === 'execution' && ['completed', 'failed', 'cancelled'].includes(task?.status)
export const unwrap = (value) => value?.data ?? value
export const listItems = (value) => { const result = unwrap(value); return Array.isArray(result) ? result : result?.items || [] }
// 先读规划状态，再读计划，避免任务已完成但页面保留旧检查项并停止轮询。
export async function loadSecurityWorkspaceData(loadTasks, loadPlans, current = () => true) {
  const tasks = await loadTasks()
  if (!current()) return null
  const plans = await loadPlans()
  return current() ? { tasks: listItems(tasks), plans: listItems(plans) } : null
}
export const shouldRenewSubmission = (error) => error?.response?.data?.error?.code === 'queue_unavailable' || (error?.response?.status >= 400 && error.response.status < 500)
export const securityPermissions = (user, project) => {
  const admin = user?.is_staff === true || user?.is_superuser === true
  const member = project?.members?.find((item) => item.username === user?.username)
  return { read: admin || !!member, edit: admin || member?.can_edit === true, execute: admin || member?.can_execute_tests === true, report: admin || member?.can_view_reports === true }
}
export const sameSecurityScope = (left, right) => left.epoch === right.epoch && String(left.projectId) === String(right.projectId) && left.route === right.route
export function createSecurityScope() {
  let epoch = 0
  let projectId = null
  let route = ''
  return {
    reset(id, path) { projectId = id; route = path; epoch += 1; return this.capture() },
    capture() { return { epoch, projectId, route } },
    current(value) { return sameSecurityScope(value, { epoch, projectId, route }) },
    dispose() { epoch += 1 },
  }
}

function fieldError(value) {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(fieldError).filter(Boolean).join('；')
  if (value && typeof value === 'object') return Object.entries(value).map(([key, detail]) => `${key}：${fieldError(detail)}`).join('；')
  return ''
}
export function securityError(error, fallback = '操作失败，请重试') {
  const value = error?.response?.data
  return value?.error?.message || value?.message || fieldError(value?.detail ?? value) || error?.message || fallback
}

export function newEndpoint(source = {}) {
  return { name: (source.name || '').slice(0, 120), url: source.url || '', method: source.method || 'GET', headersText: JSON.stringify(source.headers || {}, null, 2), auth_required: source.auth_required === true, owner_only_a: source.owner_only_a === true, proofPath: source.proof?.path || '', proofExpected: source.proof ? JSON.stringify(source.proof.expected) : '' }
}
export function planForm(plan = null) {
  const config = plan?.config || {}
  return { name: plan?.name || '', originsText: (config.allowed_origins || []).join('\n'), allow_private_network: config.allow_private_network === true,
    max_requests: config.max_requests ?? 40, rate_per_second: config.rate_per_second ?? 1, timeout_seconds: config.timeout_seconds ?? 10,
    checks: [...(config.checks || CHECKS.map((item) => item.value))], endpoints: config.endpoints?.map(newEndpoint) || [newEndpoint()],
    identityMode: plan ? 'keep' : 'replace', aName: plan?.identities?.a?.name || '账号 A', aHeaders: '', bName: plan?.identities?.b?.name || '账号 B', bHeaders: '',
  }
}
function headers(text, label) {
  let value
  try { value = JSON.parse(text || '{}') } catch { throw new Error(`${label}必须是合法 JSON 对象`) }
  if (!value || Array.isArray(value) || typeof value !== 'object' || Object.values(value).some((item) => typeof item !== 'string')) throw new Error(`${label}必须是键值均为字符串的 JSON 对象`)
  return value
}
function approvedOrigins(text) {
  const values = [...new Set(text.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))]
  if (!values.length) throw new Error('请填写至少一个允许测试的来源地址')
  return values.map((value) => {
    let url
    try { url = new URL(value) } catch { throw new Error(`来源地址无效：${value}`) }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) throw new Error('允许来源只填写协议、域名和端口，例如 https://test.example')
    return url.origin
  })
}
export function buildPlanPayload(form) {
  if (!form.name.trim()) throw new Error('请输入计划名称')
  if (form.name.trim().length > 200) throw new Error('计划名称不能超过 200 个字符')
  if (!form.endpoints.length || form.endpoints.length > 20) throw new Error('每个计划需要 1–20 个接口')
  if (!form.checks.length) throw new Error('请选择至少一种检查方向')
  const allowed_origins = approvedOrigins(form.originsText)
  const endpoints = form.endpoints.map((item, index) => {
    const prefix = `接口 ${index + 1}`
    let url
    try { url = new URL(item.url.trim()) } catch { throw new Error(`${prefix} URL 无效`) }
    if (!allowed_origins.includes(url.origin) || url.username || url.password || url.hash || /[{}<>]/.test(item.url)) throw new Error(`${prefix}必须使用允许来源中的完整 URL，不能包含凭据、片段或占位符`)
    if (item.name.trim().length > 120) throw new Error(`${prefix}名称不能超过 120 个字符`)
    if (!['GET', 'HEAD'].includes(item.method)) throw new Error(`${prefix}仅支持 GET 或 HEAD`)
    const common = headers(item.headersText, `${prefix}通用请求头`)
    if (Object.keys(common).some((name) => /^(authorization|proxy-authorization|cookie|x-api-key|api-key|x-auth-token)$/i.test(name))) throw new Error(`${prefix}认证请求头请填写到账号配置中`)
    let proof = null
    if (item.proofPath.trim() || item.proofExpected.trim()) {
      if (!item.proofPath.trim() || !item.proofExpected.trim()) throw new Error(`${prefix}资源证据需要同时填写字段路径和预期值`)
      if (!/^body(?:\.[A-Za-z_][A-Za-z0-9_-]{0,63}|\[(?:0|[1-9][0-9]{0,3})\]){0,12}$/.test(item.proofPath.trim())) throw new Error(`${prefix}证据路径格式无效，请使用 body.id、body.data.id 或 body.items[0].id`)
      let expected
      try { expected = JSON.parse(item.proofExpected) } catch { throw new Error(`${prefix}预期值必须是 JSON 标量，例如 1、"order-1" 或 true`) }
      if (expected === null || expected === '' || typeof expected === 'object' || (typeof expected === 'number' && !Number.isFinite(expected))) throw new Error(`${prefix}预期值只能是非空字符串、有限数字或布尔值`)
      if ((typeof expected === 'string' && expected.length > 1024) || (typeof expected === 'number' && Math.abs(expected) > 1e100)) throw new Error(`${prefix}预期值过长或超出数值范围`)
      if (item.method === 'HEAD') throw new Error(`${prefix}使用 HEAD，不返回响应体，不能设置资源证据`)
      proof = { path: item.proofPath.trim(), expected }
    }
    return { name: item.name.trim() || `${item.method} ${url.pathname}`.slice(0, 120), url: item.url.trim(), method: item.method, headers: common, auth_required: item.auth_required === true, owner_only_a: item.owner_only_a === true, proof }
  })
  for (const [key, min, max] of [['max_requests', 1, 100], ['rate_per_second', 0.1, 2], ['timeout_seconds', 1, 30]]) {
    if (typeof form[key] !== 'number' || !Number.isFinite(form[key]) || form[key] < min || form[key] > max || (key !== 'rate_per_second' && !Number.isInteger(form[key]))) throw new Error('请检查请求预算、速率和等待时间的取值范围')
  }
  const payload = { name: form.name.trim(), config: { allowed_origins, endpoints, checks: [...form.checks], allow_private_network: form.allow_private_network === true, max_requests: form.max_requests, rate_per_second: form.rate_per_second, timeout_seconds: form.timeout_seconds } }
  if (form.identityMode !== 'keep') {
    payload.identities = {}
    if (form.identityMode === 'replace') for (const key of ['a', 'b']) {
      if (form[`${key}Headers`].trim()) {
        const accountHeaders = headers(form[`${key}Headers`], `账号 ${key.toUpperCase()} 请求头`)
        if (!Object.values(accountHeaders).some((value) => value.trim())) throw new Error(`账号 ${key.toUpperCase()} 至少需要一个非空请求头；不配置此账号请清空输入框`)
        payload.identities[key] = { name: form[`${key}Name`].trim() || `账号 ${key.toUpperCase()}`, headers: accountHeaders }
      }
    }
  }
  return payload
}
export function importEndpoints(existing, selected) {
  if (!selected.length) throw new Error('请先勾选要导入的接口')
  const kept = existing.filter((item) => item.url.trim() || item.name.trim())
  if (kept.length + selected.length > 20) throw new Error(`每个计划最多 20 个接口，目前已有 ${kept.length} 个，请减少导入数量`)
  return [...kept, ...selected.map(newEndpoint)]
}
export function createRequestId(cryptoApi = globalThis.crypto) {
  if (cryptoApi?.randomUUID) return cryptoApi.randomUUID()
  const values = new Uint8Array(16)
  if (!cryptoApi?.getRandomValues) throw new Error('当前浏览器不支持安全随机数，请使用 HTTPS 或本机地址')
  cryptoApi.getRandomValues(values); values[6] = (values[6] & 15) | 64; values[8] = (values[8] & 63) | 128
  const hex = [...values].map((item) => item.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
export function newSubmission(previous, action, payload) {
  const signature = JSON.stringify({ action, payload })
  return previous?.signature === signature ? previous : { signature, requestId: createRequestId() }
}
