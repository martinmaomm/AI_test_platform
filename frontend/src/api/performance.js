import api from './index'
export { performanceErrorMessage } from './performanceError'
import { performanceErrorMessage } from './performanceError'

const base = (projectId) => `/projects/${projectId}/performance`

const get = async (url, config) => (await api.get(url, config)).data
const post = async (url, data) => (await api.post(url, data)).data
const patch = async (url, data) => (await api.patch(url, data)).data
const remove = async (url) => (await api.delete(url)).data

export const getPerformanceConfig = (projectId) => get(`${base(projectId)}/config/`)

export const getPerformanceTargets = (projectId) => get(`${base(projectId)}/targets/`)
export const createPerformanceTarget = (projectId, data) => post(`${base(projectId)}/targets/`, data)
export const updatePerformanceTarget = (projectId, id, data) => patch(`${base(projectId)}/targets/${id}/`, data)
export const deletePerformanceTarget = (projectId, id) => remove(`${base(projectId)}/targets/${id}/`)

export const getPerformancePlans = (projectId) => get(`${base(projectId)}/plans/`)
export const createPerformancePlan = (projectId, data) => post(`${base(projectId)}/plans/`, data)
export const updatePerformancePlan = (projectId, id, data) => patch(`${base(projectId)}/plans/${id}/`, data)
export const deletePerformancePlan = (projectId, id) => remove(`${base(projectId)}/plans/${id}/`)

export const getPerformanceNodes = (projectId) => get(`${base(projectId)}/nodes/`)
// 安装指导不包含一次性凭证或安装命令；敏感命令仅来自创建/重新生成响应。
export const getPerformanceNodeInstallation = async (projectId, id) => {
  const response = await get(`${base(projectId)}/nodes/${id}/installation/`)
  return response?.data ?? response
}

// 执行接口只使用服务端公开的管理字段；运行命令和 TLS 材料不会经过前端。
export const getPerformanceRuns = (projectId) => get(`${base(projectId)}/runs/`)
export const getPerformanceRun = (projectId, runId) => get(`${base(projectId)}/runs/${runId}/`)
export const createPerformanceRun = (projectId, planId, data) => post(`${base(projectId)}/plans/${planId}/runs/`, data)
export const stopPerformanceRun = (projectId, runId) => post(`${base(projectId)}/runs/${runId}/stop/`, {})

// 仅这两个显式响应会返回 enrollment_token；调用方必须只保留在临时弹窗状态中。
export const createPerformanceNode = async (projectId, data) => {
  const response = await post(`${base(projectId)}/nodes/`, data)
  return response?.data ?? response
}

export const updatePerformanceNode = (projectId, id, data) => patch(`${base(projectId)}/nodes/${id}/`, data)

export const regeneratePerformanceNodeInstallation = async (projectId, id) => {
  const response = await post(`${base(projectId)}/nodes/${id}/enrollment/`, {})
  return response?.data ?? response
}

export const revokePerformanceNode = (projectId, id, data = {}) => post(`${base(projectId)}/nodes/${id}/revoke/`, data)
export const deletePerformanceNode = (projectId, id) => remove(`${base(projectId)}/nodes/${id}/`)
