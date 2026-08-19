import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { usersApi } from '@/api/users'
import { useProjectStore } from './project'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  const user = ref(null)
  const accessToken = ref(null)
  const refreshToken = ref(null)
  const loading = ref(false)

  // 计算属性
  const isAuthenticated = computed(() => {
    return !!accessToken.value && !isTokenExpired(accessToken.value)
  })
  const userRole = computed(() => user.value?.role || 'user')

  // JWT token过期检查
  const isTokenExpired = (token) => {
    if (!token) return true
    
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      const now = Math.floor(Date.now() / 1000)
      return payload.exp < now
    } catch {
      return true
    }
  }

  // 通用错误处理函数
  const handleApiError = (error, defaultMessage) => {
    const errorData = error.response?.data
    if (errorData?.error) {
      // 处理验证错误
      if (errorData.error.code === 'validation_error' && errorData.error.details) {
        const details = errorData.error.details
        if (details.non_field_errors?.length > 0) {
          return details.non_field_errors[0]
        }
        const firstError = Object.values(details)[0]
        if (Array.isArray(firstError) && firstError.length > 0) {
          return firstError[0]
        }
      }
      return errorData.error.message || errorData.message || defaultMessage
    }
    return defaultMessage
  }

  // 动作
  const login = async (credentials) => {
    try {
      loading.value = true
      const response = await usersApi.login(credentials)
      
      if (response.data?.success && response.data.data) {
        const { access, refresh, user: userData } = response.data.data
        
        accessToken.value = access
        refreshToken.value = refresh
        user.value = userData
        
        return { success: true, message: response.data.message || '登录成功' }
      }
      return { success: false, error: '登录失败，服务器未返回token' }
    } catch (error) {
      return { success: false, error: handleApiError(error, '登录失败，请检查用户名和密码') }
    } finally {
      loading.value = false
    }
  }

  const register = async (userData) => {
    try {
      loading.value = true
      const response = await usersApi.register(userData)
      
      if (response.data?.success) {
        return { success: true, message: response.data.message || '注册成功，请登录' }
      }
      return { success: false, error: '注册失败，请检查输入信息' }
    } catch (error) {
      return { success: false, error: handleApiError(error, '注册失败，请检查输入信息') }
    } finally {
      loading.value = false
    }
  }

  const logout = async () => {
    // 先清除本地状态，避免循环调用
    const currentRefreshToken = refreshToken.value
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    
    // 清除项目状态
    const projectStore = useProjectStore()
    projectStore?.reset()
    
    // 尝试调用登出API（不阻塞）
    if (currentRefreshToken) {
      try {
        await usersApi.logout(currentRefreshToken)
      } catch (error) {
        // 登出API失败不影响本地状态清除
      }
    }
  }


  // 刷新JWT token
  const refreshAccessToken = async () => {
    try {
      if (!refreshToken.value) {
        throw new Error('No refresh token available')
      }
      
      const response = await usersApi.refreshToken(refreshToken.value)
      if (response.data?.success && response.data.data) {
        const { access, refresh } = response.data.data
        accessToken.value = access
        refreshToken.value = refresh
        return true
      }
      return false
    } catch (error) {
      // 刷新失败，直接清除状态，不调用logout避免循环
      accessToken.value = null
      refreshToken.value = null
      user.value = null
      return false
    }
  }



  // 初始化时检查token状态
  // pinia-plugin-persistedstate 会自动从 localStorage 恢复状态
  if (accessToken.value && isTokenExpired(accessToken.value)) {
    // token过期，尝试刷新
    refreshAccessToken()
  }

  return {
    // 状态
    user,
    accessToken,
    refreshToken,
    loading,
    
    // 计算属性
    isAuthenticated,
    userRole,
    
    // 动作
    login,
    register,
    logout,
    refreshAccessToken
  }
}, {
  // 持久化配置
  persist: {
    key: 'auth-store',
    storage: localStorage,
    paths: ['accessToken', 'refreshToken', 'user']
  }
})
