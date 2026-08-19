/**
 * MidScene API 模块
 * 用于MidScene脚本生成相关的API调用
 */

import api from './index'

const API_BASE = '/web-testing/midscene'

// ============ 生成 MidScene 脚本 ============
export const generateMidSceneScript = async (data) => {
  const response = await api.post(`${API_BASE}/generate/`, data)
  return response.data
}

// ============ 获取 MidScene 脚本详情 ============
export const getMidSceneScript = async (scriptId) => {
  const response = await api.get(`${API_BASE}/scripts/${scriptId}/`)
  return response.data
}

// ============ 获取 MidScene 脚本列表 ============
export const listMidSceneScripts = async (params = {}) => {
  const response = await api.get(`${API_BASE}/scripts/`, { params })
  return response.data
}
