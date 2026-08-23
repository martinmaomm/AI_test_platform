import api from './index'

/**
 * 项目相关的API服务
 */

// 获取项目列表
export const getProjects = async (params = {}) => {
  const response = await api.get('/projects/', { params })
  return response.data
}

// 获取项目详情
export const getProject = async (id) => {
  const response = await api.get(`/projects/${id}/`)
  return response.data
}

// 创建项目
export const createProject = async (data) => {
  const response = await api.post('/projects/', data)
  return response.data
}

// 更新项目
export const updateProject = async (id, data) => {
  const response = await api.patch(`/projects/${id}/`, {
    name: data?.name,
    description: data?.description,
  })
  return response.data
}

// 删除项目
export const deleteProject = async (id) => {
  const response = await api.delete(`/projects/${id}/`)
  return response.data
}

// ============ 环境管理相关 ============

// 获取项目环境列表
export const getProjectEnvironments = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/environments/`, { params })
  return response.data
}

// 创建项目环境
export const createProjectEnvironment = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/environments/`, data)
  return response.data
}

// 更新项目环境
export const updateProjectEnvironment = async (projectId, environmentId, data) => {
  const response = await api.put(`/projects/${projectId}/environments/${environmentId}/`, data)
  return response.data
}

// 删除项目环境
export const deleteProjectEnvironment = async (projectId, environmentId) => {
  const response = await api.delete(`/projects/${projectId}/environments/${environmentId}/`)
  return response.data
}

// 获取项目统计数据
export const getProjectStatistics = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/statistics/`)
  return response.data
}

// ============ 知识库文件管理相关 ============

// 获取项目的知识库文件列表
export const getProjectKnowledgeFiles = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/knowledge-base/`, { params })
  return response.data
}

// 获取知识库文件详情
export const getKnowledgeFileDetail = async (projectId, fileId) => {
  const response = await api.get(`/projects/${projectId}/knowledge-base/${fileId}/`)
  return response.data
}

// 上传知识库文件
export const uploadKnowledgeFile = async (projectId, formData) => {
  const response = await api.post(`/projects/${projectId}/knowledge-base/`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

// 删除知识库文件
export const deleteKnowledgeFile = async (projectId, fileId) => {
  const response = await api.delete(`/projects/${projectId}/knowledge-base/${fileId}/`)
  return response.data
}

// 重新处理知识库文件（解析和RAG入库）
export const reprocessKnowledgeFile = async (projectId, fileId) => {
  const response = await api.post(`/projects/${projectId}/knowledge-base/${fileId}/reprocess/`)
  return response.data
}

// 手动触发知识库文件处理（解析和RAG入库）
export const startKnowledgeFileProcessing = async (projectId, fileId) => {
  const response = await api.post(`/projects/${projectId}/knowledge-base/${fileId}/start-processing/`)
  return response.data
}

// 获取知识库文件处理任务状态
export const getKnowledgeFileTaskStatus = async (projectId, taskId) => {
  const response = await api.get(`/projects/${projectId}/knowledge-base/task-status/${taskId}/`)
  return response.data
}
