/**
 * 邮件配置与项目邮件接收组 API
 */
import api from './index'
import { loadNotificationPages } from '../utils/notificationFeedback'

// ---------- 项目级接收对象（嵌套在 /projects/{projectId}/notification-receivers/ 下）----------
/** 获取项目接收对象列表 */
export function getNotificationReceivers(projectId, params = {}) {
  if (typeof projectId === 'object') {
    params = projectId
    projectId = params.project_id
  }
  if (!projectId) {
    return api.get('/notifications/receivers/', { params })
  }
  const path = `/projects/${projectId}/notification-receivers/`
  if (params.page) return api.get(path, { params })
  return loadNotificationPages(page => api.get(path, { params: { ...params, page } }))
}

/** 创建接收对象 */
export function createNotificationReceiver(projectId, data) {
  return api.post(`/projects/${projectId}/notification-receivers/`, data)
}

/** 更新接收对象（使用 PATCH 支持局部更新） */
export function updateNotificationReceiver(projectId, id, data) {
  return api.patch(`/projects/${projectId}/notification-receivers/${id}/`, data)
}

/** 删除接收对象 */
export function deleteNotificationReceiver(projectId, id) {
  return api.delete(`/projects/${projectId}/notification-receivers/${id}/`)
}

/** 向保存的邮件接收组发送测试邮件 */
export function testReceiverById(projectId, id) {
  return api.post(`/projects/${projectId}/notification-receivers/${id}/test/`)
}

// ---------- 邮件服务配置 ----------
export function getEmailConfigs(params = {}) {
  const path = '/notifications/email-configs/'
  if (params.page) return api.get(path, { params })
  return loadNotificationPages(page => api.get(path, { params: { ...params, page } }))
}

export function createEmailConfig(data) {
  return api.post('/notifications/email-configs/', data)
}

export function updateEmailConfig(id, data) {
  return api.put(`/notifications/email-configs/${id}/`, data)
}

export function deleteEmailConfig(id) {
  return api.delete(`/notifications/email-configs/${id}/`)
}

export function testEmailConfig(id) {
  return api.post(`/notifications/email-configs/${id}/test/`)
}
