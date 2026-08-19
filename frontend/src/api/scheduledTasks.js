import api from './index'

/**
 * 定时任务中心 API 服务
 */

// ============ 定时任务管理相关 ============

// 获取任务列表
export const getScheduledTasks = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/tasks/`, { params })
  return response.data
}

// 获取任务详情
export const getScheduledTask = async (projectId, id) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/tasks/${id}/`)
  return response.data
}

// 创建任务
export const createScheduledTask = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/scheduled-tasks/tasks/`, data)
  return response.data
}

// 更新任务
export const updateScheduledTask = async (projectId, id, data) => {
  const response = await api.put(`/projects/${projectId}/scheduled-tasks/tasks/${id}/`, data)
  return response.data
}

// 删除任务
export const deleteScheduledTask = async (projectId, id) => {
  const response = await api.delete(`/projects/${projectId}/scheduled-tasks/tasks/${id}/`)
  return response.data
}

// 手动执行任务
export const runScheduledTask = async (projectId, id) => {
  const response = await api.post(`/projects/${projectId}/scheduled-tasks/tasks/${id}/run/`)
  return response.data
}

// ============ 执行日志管理相关 ============

// 获取执行日志列表
export const getExecutionLogs = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/execution-logs/`, { params })
  return response.data
}

// 获取执行日志详情
export const getExecutionLog = async (projectId, id) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/execution-logs/${id}/`)
  return response.data
}

// 删除单条执行日志
export const deleteExecutionLog = async (projectId, id) => {
  const response = await api.delete(`/projects/${projectId}/scheduled-tasks/execution-logs/${id}/`)
  return response.data
}

// 获取指定任务的执行日志
export const getTaskExecutionLogs = async (projectId, taskId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/tasks/${taskId}/execution-logs/`, { params })
  return response.data
}

// ============ 辅助接口相关 ============

// 获取测试套件选择列表
export const getSuiteChoices = async (projectId, suiteType) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/suite-choices/`, {
    params: { suite_type: suiteType }
  })
  return response.data
}

// 获取统计信息
export const getScheduledTaskStatistics = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/scheduled-tasks/statistics/`)
  return response.data
}
