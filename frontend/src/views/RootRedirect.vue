<template>
  <div class="root-redirect">
    <div class="loading-container">
      <el-icon class="loading-icon" :size="40">
        <Loading />
      </el-icon>
      <p>正在跳转...</p>
      <p class="debug-info">{{ debugInfo }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Loading } from '@element-plus/icons-vue'

const router = useRouter()
const authStore = useAuthStore()
const debugInfo = ref('初始化中...')
let redirectTimeout = null

const performRedirect = async () => {
  try {
    console.log('RootRedirect: 开始执行重定向逻辑')
    debugInfo.value = '检查认证状态...'
    
    // 等待一下确保认证状态已更新
    await new Promise(resolve => setTimeout(resolve, 500))
    
    const isAuth = authStore.isAuthenticated
    const token = authStore.token
    
    console.log('RootRedirect: 认证状态检查结果:', { isAuth, token })
    debugInfo.value = `认证状态: ${isAuth ? '已认证' : '未认证'}, Token: ${token ? '存在' : '不存在'}`
    
    if (isAuth && token) {
      console.log('RootRedirect: 用户已认证，重定向到dashboard')
      debugInfo.value = '重定向到仪表盘...'
      
      // 使用 push 而不是 replace，并添加错误处理
      await router.push('/dashboard')
      console.log('RootRedirect: 重定向到dashboard成功')
    } else {
      console.log('RootRedirect: 用户未认证，重定向到登录页')
      debugInfo.value = '重定向到登录页...'
      
      await router.push('/login')
      console.log('RootRedirect: 重定向到登录页成功')
    }
  } catch (error) {
    console.error('RootRedirect: 重定向失败:', error)
    debugInfo.value = `重定向失败: ${error.message}`
    
    // 如果重定向失败，尝试强制跳转
    setTimeout(() => {
      try {
        if (authStore.isAuthenticated) {
          window.location.href = '/dashboard'
        } else {
          window.location.href = '/login'
        }
      } catch (fallbackError) {
        console.error('RootRedirect: 强制跳转也失败:', fallbackError)
        debugInfo.value = '所有跳转方式都失败，请手动刷新页面'
      }
    }, 2000)
  }
}

onMounted(() => {
  console.log('RootRedirect组件挂载')
  debugInfo.value = '组件已挂载，准备重定向...'
  
  // 立即执行重定向
  performRedirect()
  
  // 设置超时保护，如果5秒内没有跳转成功，显示错误信息
  redirectTimeout = setTimeout(() => {
    console.warn('RootRedirect: 重定向超时')
    debugInfo.value = '重定向超时，请检查网络连接或手动刷新页面'
  }, 5000)
})

onUnmounted(() => {
  if (redirectTimeout) {
    clearTimeout(redirectTimeout)
  }
})
</script>

<style scoped>
.root-redirect {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.loading-container {
  text-align: center;
  color: white;
}

.loading-icon {
  animation: rotate 1s linear infinite;
  margin-bottom: 20px;
}

.loading-container p {
  font-size: 16px;
  margin: 0;
  margin-bottom: 10px;
}

.debug-info {
  font-size: 12px;
  color: #e6e6e6;
  background: rgba(0, 0, 0, 0.3);
  padding: 8px 12px;
  border-radius: 4px;
  margin-top: 20px;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
