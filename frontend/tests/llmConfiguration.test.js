import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_LLM_PROVIDER,
  LLM_PROVIDER_OPTIONS,
  getLLMProviderBaseUrlHint,
  getLLMProviderInterfaceLabel,
  getLLMConnectionError,
} from '../src/utils/llmConfiguration.js'

test('LLM 接口选项仅包含 OpenAI/DeepSeek/Ollama 且默认值为 openai', () => {
  assert.equal(DEFAULT_LLM_PROVIDER, 'openai')
  const values = LLM_PROVIDER_OPTIONS.map(option => option.value)
  assert.deepEqual(values, ['openai', 'deepseek', 'ollama'])
  assert.deepEqual(LLM_PROVIDER_OPTIONS.map(option => option.label), ['OpenAI兼容接口', 'DeepSeek原生接口', 'Ollama本地接口'])
})

test('LLM 接口类型标签使用独立映射，不再复用 provider_name', () => {
  assert.equal(getLLMProviderInterfaceLabel('openai'), 'OpenAI兼容接口')
  assert.equal(getLLMProviderInterfaceLabel('deepseek'), 'DeepSeek原生接口')
  assert.equal(getLLMProviderInterfaceLabel('ollama'), 'Ollama本地接口')
  assert.equal(getLLMProviderInterfaceLabel('other'), '—')
})

test('API 地址提示会区分不同接口并给出基础地址示例', () => {
  assert.match(getLLMProviderBaseUrlHint('openai'), /\/v1/)
  assert.match(getLLMProviderBaseUrlHint('openai'), /不是完整\/chat\/completions/)
  assert.match(getLLMProviderBaseUrlHint('deepseek'), /deepseek\.com\/v1/)
  assert.match(getLLMProviderBaseUrlHint('ollama'), /127\.0\.0\.1:11434/)
  assert.equal(getLLMProviderBaseUrlHint('other'), '')
})

test('错误消息提取优先返回 response.data.message，并支持 nested error.message', () => {
  const responseMessage = getLLMConnectionError({
    response: {
      data: {
        message: '接口返回业务失败'
      }
    }
  })
  assert.equal(responseMessage.message, '接口返回业务失败')

  const nestedErrorMessage = getLLMConnectionError({
    response: {
      data: {
        error: {
          message: '字段校验失败'
        }
      }
    }
  })
  assert.equal(nestedErrorMessage.message, '字段校验失败')

  const topLevelFallback = getLLMConnectionError({
    message: '请求层面异常',
    error: {
      message: '不会被直接命中'
    }
  })
  assert.equal(topLevelFallback.message, '请求层面异常')
})

test('连接失败错误优先取 response.message，未命中时回退 error_detail', () => {
  const messagePriority = getLLMConnectionError({
    response: {
      data: {
        message: '上游接口返回业务错误',
        error_detail: '字段字段校验失败'
      }
    }
  })
  assert.equal(messagePriority.message, '上游接口返回业务错误')

  const fallback = getLLMConnectionError({
    response: {
      data: {
        error_detail: '字段字段校验失败'
      }
    }
  })
  assert.equal(fallback.message, '字段字段校验失败')
})
