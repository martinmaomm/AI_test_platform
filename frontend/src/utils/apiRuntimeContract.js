// These values are provided by the requests runtime, without executable hooks.
export const API_DYNAMIC_VARIABLES = [{
  group: '运行时变量',
  items: [
    { value: '${timestamp_ns}', label: '纳秒时间戳' },
    { value: '${uuid4}', label: 'UUID' },
  ],
}]

export function apiCompatibilityIssue(value) {
  if (!value || typeof value !== 'object') return ''
  const fields = ['setup_hooks', 'teardown_hooks', 'setup', 'teardown', 'functions']
  const has = (object, keys) => object && keys.some(key => Object.hasOwn(object, key))
  const steps = Array.isArray(value.teststeps) ? value.teststeps : Array.isArray(value.steps) ? value.steps : []
  const legacyFields = has(value, fields) || has(value.config, fields) || steps.some(step =>
    has(step, fields) || has(step?.request, ['hooks', 'setup_hooks', 'teardown_hooks', 'function']))
  const hasFunction = object => {
    if (typeof object === 'string') return /\$\{[^{}]*\([^{}]*\)[^{}]*\}/.test(object)
    if (Array.isArray(object)) return object.some(hasFunction)
    return object && typeof object === 'object' && Object.values(object).some(hasFunction)
  }
  return legacyFields || hasFunction(value)
    ? '当前用例含不支持的旧脚本钩子或函数表达式。请在 AI 工作区整理为请求、变量提取和断言后再保存或调试；现有内容会保留。'
    : ''
}
