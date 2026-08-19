import api from './index'

const handleRequest = async (request, errorMessage) => {
  try {
    const response = await request
    return response.data
  } catch (error) {
    throw error
  }
}

const updatePreferencesField = async (fields) => {
  // 使用handleRequest包装的getPreferences方法，避免嵌套
  const response = await usersApi.getPreferences()
  const currentPreferences = response.data || {}
  const updatedPreferences = {
    ...currentPreferences,
    ...fields,
  }
  return usersApi.updatePreferences(updatedPreferences)
}

export const usersApi = {
  // 用户登录 - 返回JWT token
  login: async (credentials) => {
    const response = await api.post('/users/login/', credentials)
    return response
  },

  // 用户注册
  register: (userData) => api.post('/users/register/', userData),

  // 用户登出 - 使用JWT token
  logout: async (refreshToken) => {
    const response = await api.post('/users/logout/', { refresh: refreshToken })
    return response
  },

  // 刷新JWT token
  refreshToken: async (refreshToken) => {
    if (!refreshToken) {
      throw new Error('No refresh token available')
    }
    const response = await api.post('/users/refresh-token/', { refresh: refreshToken })
    return response
  },

  // 验证JWT token
  verifyToken: async (token) => {
    if (!token) {
      throw new Error('No access token available')
    }
    return api.post('/users/verify-token/', { token })
  },

  // 获取当前用户信息
  getCurrentUser: () => api.get('/users/current-user/'),

  // 修改密码
  changePassword: (passwordData) =>
    api.post('/users/change-password/', passwordData),

  // 更新用户资料
  updateProfile: (profileData) =>
    api.put('/users/manage/profile/', profileData),

  // 获取用户偏好设置
  getPreferences: () =>
    handleRequest(api.get('/users/manage/preferences/'), '获取用户偏好设置失败'),

  // 更新用户偏好设置
  updatePreferences: (preferences) =>
    handleRequest(
      api.put('/users/manage/preferences/', preferences),
      '更新用户偏好设置失败'
    ),

  // 设置选中项目
  setSelectedProject: (project) =>
    updatePreferencesField({ selectedProject: project }),

  // 清除选中项目
  clearSelectedProject: () =>
    updatePreferencesField({ selectedProject: null }),

  // 更新偏好设置字段（内部方法，供store使用）
  updatePreferencesField: updatePreferencesField,
}
