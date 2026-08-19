/**
 * 消息渠道管理 API（钉钉/企微 Webhook）
 */
import api from './index'

// ---------- 全局渠道（管理员） ----------
/** 获取全局渠道列表 */
export function getNotificationChannels(params = {}) {
  return api.get('/notifications/channels/', { params })
}

/** 创建全局渠道 */
export function createNotificationChannel(data) {
  return api.post('/notifications/channels/', data)
}

/** 更新全局渠道 */
export function updateNotificationChannel(id, data) {
  return api.put(`/notifications/channels/${id}/`, data)
}

/** 删除全局渠道 */
export function deleteNotificationChannel(id) {
  return api.delete(`/notifications/channels/${id}/`)
}

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
  return api.get(`/projects/${projectId}/notification-receivers/`, { params })
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

/** 测试连接（校验 Webhook URL 是否有效，需传 body） */
export function testReceiverConnection(projectIdOrPayload, idOrPayload) {
  if (typeof projectIdOrPayload === 'number' || typeof projectIdOrPayload === 'string') {
    return api.post(`/projects/${projectIdOrPayload}/notification-receivers/test_connection/`, idOrPayload)
  }
  if (typeof projectIdOrPayload === 'object') {
    return api.post('/notifications/receivers/test_connection/', projectIdOrPayload)
  }
  return api.post('/notifications/receivers/test_connection/', idOrPayload)
}

/** 按接收对象 ID 测试（后端从数据库读取 Webhook 发送，不传明文，防抓包） */
export function testReceiverById(projectId, id) {
  return api.post(`/projects/${projectId}/notification-receivers/${id}/test/`)
}

// ---------- 邮件服务配置 ----------
export function getEmailConfigs(params = {}) {
  return api.get('/notifications/email-configs/', { params })
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
