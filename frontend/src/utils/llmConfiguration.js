export const DEFAULT_LLM_PROVIDER = 'openai'

export const LLM_PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI兼容接口' },
  { value: 'deepseek', label: 'DeepSeek原生接口' },
  { value: 'ollama', label: 'Ollama本地接口' }
]

const PROVIDER_LABEL_MAP = LLM_PROVIDER_OPTIONS.reduce((acc, item) => {
  acc[item.value] = item.label
  return acc
}, {})

export const getLLMProviderInterfaceLabel = (provider) => {
  return PROVIDER_LABEL_MAP[provider] || '—'
}

export const getLLMProviderBaseUrlHint = (provider) => {
  const hints = {
    openai: 'OpenAI兼容接口请填写基础地址，例如 https://api.example.com/v1，不是完整/chat/completions。',
    deepseek: 'DeepSeek原生接口请填写基础地址，例如 https://api.deepseek.com/v1。',
    ollama: 'Ollama本地接口请填写服务地址，例如 http://127.0.0.1:11434。'
  }
  return hints[provider] || ''
}

export const getLLMConnectionError = (error, fallback = '连接测试失败') => {
  const body = error?.response?.data || error || {}
  const message = body?.message || body?.error_detail || body?.detail || body?.error?.message || error?.message || fallback
  return {
    message,
    suggestion: body?.suggestion || ''
  }
}
