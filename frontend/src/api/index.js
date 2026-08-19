import axios from 'axios'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

/** 防弹窗风暴锁：防止并发 401/400 触发多个重新登录确认框 */
let isReloginShow = false

/** 免弹窗白名单：退出登录等接口返回 401/400 时直接 reject，不弹出会话过期提示 */
const SESSION_EXPIRE_SKIP_URLS = ['/logout', 'users/logout']

function isSkipSessionExpirePopup(config) {
  const url = config?.url ?? ''
  return SESSION_EXPIRE_SKIP_URLS.some((s) => url.includes(s))
}

/** 判断错误是否为会话失效（401 或 400 且含认证相关关键词） */
function isSessionExpiredError(error) {
  const status = error.response?.status
  if (status === 401) return true
  if (status !== 400) return false
  const data = error.response?.data
  if (!data) return false
  const keywords = ['token', '认证', '过期', '登录', '未授权', 'invalid', 'expired', 'unauthorized']
  const toStr = (v) => (v == null ? '' : String(v).toLowerCase())
  const msg = toStr(
    typeof data === 'string' ? data : (data.msg ?? data.message ?? data.detail ?? data.error?.message ?? '')
  )
  return keywords.some((k) => msg.includes(k.toLowerCase()))
}

/** 执行登出提示与清理，带防抖锁 */
async function handleSessionExpired(error) {
  if (isReloginShow) return Promise.reject(error)
  isReloginShow = true
  try {
    await ElMessageBox.confirm('登录会话已过期，请重新登录', '系统提示', {
      confirmButtonText: '重新登录',
      showCancelButton: false,
      closeOnClickModal: false,
      type: 'warning'
    })
    const authStore = useAuthStore()
    await authStore.logout()
    router.push('/login')
  } catch {
    // 用户关闭（极少见，因无取消按钮）
  } finally {
    isReloginShow = false
  }
  return Promise.reject(error)
}

// 创建axios实例
const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  },
  withCredentials: false  // JWT不需要cookies
})

// JWT认证相关函数
const getStoredUser = () => {
  try {
    const userStr = localStorage.getItem('user')
    return userStr ? JSON.parse(userStr) : null
  } catch (error) {
    return null
  }
}

const isTokenExpired = (token) => {
  if (!token) return true
  
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    const now = Math.floor(Date.now() / 1000)
    return payload.exp < now
  } catch (error) {
    return true
  }
}

// 请求拦截器
api.interceptors.request.use(
  async (config) => {
    // 定义不需要携带token的接口
    const noAuthEndpoints = [
      '/users/login/',
      '/users/register/',
      '/users/csrf-token/',
      '/users/refresh-token/',
      '/reports/execution/',
      '/reports/detail/'
    ]
    
    // 检查当前请求是否需要跳过token
    const shouldSkipAuth = noAuthEndpoints.some(endpoint => 
      config.url?.includes(endpoint)
    )
    
    // 只有在需要认证且存在token时才添加Authorization头
    if (!shouldSkipAuth) {
      const authStore = useAuthStore()
      if (authStore.accessToken) {
        config.headers.Authorization = `Bearer ${authStore.accessToken}`
      }
    }
    
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    // 免弹窗白名单：退出登录等接口的 401/400 直接透传，避免连环弹窗
    if (isSkipSessionExpirePopup(error.config)) {
      return Promise.reject(error)
    }

    // 会话失效：401 或 400 且含认证相关关键词
    if (isSessionExpiredError(error)) {
      const status = error.response?.status
      const originalRequest = error.config

      // 401：先尝试刷新 token
      if (status === 401 && originalRequest && !originalRequest._retry) {
        originalRequest._retry = true
        const authStore = useAuthStore()
        if (authStore.refreshToken) {
          try {
            const response = await axios.post('/api/v1/users/refresh-token/', {
              refresh: authStore.refreshToken
            }, {
              headers: { 'Content-Type': 'application/json' }
            })
            if (response.data?.success && response.data?.data) {
              const { access, refresh } = response.data.data
              authStore.accessToken = access
              authStore.refreshToken = refresh
              originalRequest.headers.Authorization = `Bearer ${access}`
              return api(originalRequest)
            }
          } catch {
            // 刷新失败，进入登出流程
          }
        }
      }

      // 401 刷新失败 或 400 认证错误：统一走防抖登出提示
      return handleSessionExpired(error)
    }

    return Promise.reject(error)
  }
)

export default api
