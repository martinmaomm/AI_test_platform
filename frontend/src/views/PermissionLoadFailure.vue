<template>
  <main class="permission-load-failure">
    <el-result icon="warning" title="权限信息加载失败" sub-title="当前登录会话已保留。请检查网络或服务状态后重试。">
      <template #extra>
        <el-button type="primary" @click="retry">重试</el-button>
        <el-button @click="relogin">重新登录</el-button>
      </template>
    </el-result>
  </main>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const redirect = computed(() => {
  const value = String(route.query.redirect || '/dashboard')
  return value.startsWith('/') && !value.startsWith('//') ? value : '/dashboard'
})
const retry = () => router.replace(redirect.value)
const relogin = async () => {
  // logout clears local credentials before its best-effort API call completes.
  await authStore.logout()
  await router.replace('/login')
}
</script>

<style scoped>
.permission-load-failure { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
</style>
