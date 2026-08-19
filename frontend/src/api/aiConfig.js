/**
 * LLM、RAG和视觉模型配置统一API
 */
import api from './index'

// ============ LLM配置相关 ============

/**
 * 获取LLM配置列表
 */
export function getLLMConfigurations(params = {}) {
  return api.get('/ai-core/llm-configs/', { params })
}

/**
 * 创建LLM配置
 */
export function createLLMConfiguration(data) {
  return api.post('/ai-core/llm-configs/', data)
}

/**
 * 更新LLM配置
 */
export function updateLLMConfiguration(id, data) {
  return api.put(`/ai-core/llm-configs/${id}/`, data)
}

/**
 * 删除LLM配置
 */
export function deleteLLMConfiguration(id) {
  return api.delete(`/ai-core/llm-configs/${id}/`)
}

/**
 * 获取LLM配置详情
 */
export function getLLMConfiguration(id) {
  return api.get(`/ai-core/llm-configs/${id}/`)
}

/**
 * 获取支持的LLM提供商信息
 */
export function getLLMProviders() {
  return api.get('/ai-core/llm-configs/providers/')
}

/**
 * 切换LLM配置启用状态
 */
export function toggleLLMConfigurationActive(id) {
  return api.post(`/ai-core/llm-configs/${id}/toggle_active/`)
}

/**
 * 测试LLM连接
 */
export function testLLMConnection(data) {
  return api.post('/ai-core/llm-configs/test_connection/', data)
}

/**
 * 获取LLM使用日志
 */
export function getLLMUsageLogs(params = {}) {
  return api.get('/ai-core/llm-usage-logs/', { params })
}

// ============ RAG配置相关 ============

/**
 * 获取RAG配置列表
 */
export function getRAGConfigurations(params = {}) {
  return api.get('/ai-core/rag-configs/', { params })
}

/**
 * 创建RAG配置
 */
export function createRAGConfiguration(data) {
  return api.post('/ai-core/rag-configs/', data)
}

/**
 * 更新RAG配置
 */
export function updateRAGConfiguration(id, data) {
  return api.put(`/ai-core/rag-configs/${id}/`, data)
}

/**
 * 删除RAG配置
 */
export function deleteRAGConfiguration(id) {
  return api.delete(`/ai-core/rag-configs/${id}/`)
}

/**
 * 测试RAG连接
 */
export function testRAGConnection(data) {
  return api.post('/ai-core/rag-configs/test_connection/', data)
}

/**
 * 切换RAG配置启用状态
 */
export function toggleRAGConfigurationActive(id) {
  return api.post(`/ai-core/rag-configs/${id}/toggle_active/`)
}

/**
 * 设置默认RAG配置
 */
export function setDefaultRAGConfiguration(id) {
  return api.post(`/ai-core/rag-configs/${id}/set_default/`)
}

// ============ 视觉模型配置相关 ============

/**
 * 获取视觉模型配置列表
 */
export function getVisionConfigurations() {
  return api.get('/ai-core/vision-configs/')
}

/**
 * 创建视觉模型配置
 */
export function createVisionConfiguration(data) {
  return api.post('/ai-core/vision-configs/', data)
}

/**
 * 获取视觉模型配置详情
 */
export function getVisionConfiguration(id) {
  return api.get(`/ai-core/vision-configs/${id}/`)
}

/**
 * 更新视觉模型配置
 */
export function updateVisionConfiguration(id, data) {
  return api.put(`/ai-core/vision-configs/${id}/`, data)
}

/**
 * 删除视觉模型配置
 */
export function deleteVisionConfiguration(id) {
  return api.delete(`/ai-core/vision-configs/${id}/`)
}


// ============ 多模态配置相关 ============

/**
 * 获取多模态配置列表
 */
export function getMultimodalConfigurations(params = {}) {
  return api.get('/ai-core/multimodal-configs/', { params })
}

/**
 * 创建多模态配置
 */
export function createMultimodalConfiguration(data) {
  return api.post('/ai-core/multimodal-configs/', data)
}

/**
 * 更新多模态配置
 */
export function updateMultimodalConfiguration(id, data) {
  return api.put(`/ai-core/multimodal-configs/${id}/`, data)
}

/**
 * 删除多模态配置
 */
export function deleteMultimodalConfiguration(id) {
  return api.delete(`/ai-core/multimodal-configs/${id}/`)
}


// ============ MCP配置相关 ============

/**
 * 获取MCP配置列表
 */
export function getMCPConfigurations(params = {}) {
  return api.get('/ai-core/mcp-configs/', { params })
}

/**
 * 创建MCP配置
 */
export function createMCPConfiguration(data) {
  return api.post('/ai-core/mcp-configs/', data)
}

/**
 * 更新MCP配置
 */
export function updateMCPConfiguration(id, data) {
  return api.put(`/ai-core/mcp-configs/${id}/`, data)
}

/**
 * 删除MCP配置
 */
export function deleteMCPConfiguration(id) {
  return api.delete(`/ai-core/mcp-configs/${id}/`)
}

/**
 * 获取MCP配置详情
 */
export function getMCPConfiguration(id) {
  return api.get(`/ai-core/mcp-configs/${id}/`)
}

/**
 * 切换MCP配置启用状态
 */
export function toggleMCPConfigurationActive(id) {
  return api.post(`/ai-core/mcp-configs/${id}/toggle_active/`)
}

