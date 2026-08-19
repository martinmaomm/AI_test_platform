<template>
  <el-container class="portal-layout">
    <!-- 顶部导航（无左侧业务菜单） -->
    <el-header class="portal-header">
      <div class="header-left">
        <router-link to="/dashboard" class="logo-link">
          <AITSBrand size="small" />
        </router-link>
      </div>

      <div class="header-right">
        <el-button-group class="theme-toggle" size="small">
          <el-tooltip content="亮色" placement="bottom">
            <el-button :type="appStore.themeMode === 'light' ? 'primary' : 'default'" @click="appStore.setThemeMode('light')">
              <el-icon><Sunny /></el-icon>
            </el-button>
          </el-tooltip>
          <el-tooltip content="深色极客" placement="bottom">
            <el-button :type="appStore.themeMode === 'dark' ? 'primary' : 'default'" @click="appStore.setThemeMode('dark')">
              <el-icon><Moon /></el-icon>
            </el-button>
          </el-tooltip>
          <el-tooltip content="跟随系统" placement="bottom">
            <el-button :type="appStore.themeMode === 'auto' ? 'primary' : 'default'" @click="appStore.setThemeMode('auto')">
              <el-icon><Monitor /></el-icon>
            </el-button>
          </el-tooltip>
        </el-button-group>
        <el-dropdown @command="handleCommand">
          <span class="user-info">
            <el-avatar :size="30" :src="authStore.user?.avatar">
              {{ authStore.user?.username?.charAt(0)?.toUpperCase() }}
            </el-avatar>
            <span class="username">{{ authStore.user?.username }}</span>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">个人资料</el-dropdown-item>
              <el-dropdown-item command="workspace">进入工作区</el-dropdown-item>
              <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <!-- 主内容区 -->
    <el-main class="portal-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useTabStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import { ArrowDown, Sunny, Moon, Monitor } from '@element-plus/icons-vue'
import AITSBrand from '@/components/AITSBrand.vue'

const router = useRouter()
const authStore = useAuthStore()
const tabStore = useTabStore()
const appStore = useAppStore()

const handleCommand = async (command) => {
  switch (command) {
    case 'profile':
      router.push('/profile')
      break
    case 'workspace':
      router.push('/dashboard')
      break
    case 'logout':
      try {
        await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          type: 'warning'
        })
        await authStore.logout()
        tabStore.reset()
        await router.push('/login')
      } catch (e) {
        if (e !== 'cancel') console.error('登出错误:', e)
      }
      break
  }
}
</script>

<style scoped>
.portal-layout {
  height: 100vh;
  flex-direction: column;
  overflow: hidden;
}

.portal-header {
  background: var(--layout-header-bg);
  border-bottom: 1px solid var(--layout-header-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  min-height: 100px;
  flex-shrink: 0;
}

.logo-link {
  text-decoration: none;
  color: inherit;
  display: block;
  min-width: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 6px;
  transition: background-color 0.2s;
}

.user-info:hover {
  background-color: var(--layout-hover-bg);
}

.username {
  font-size: 14px;
  color: var(--app-text-primary);
  font-weight: 500;
}

.portal-main {
  flex: 1;
  background-color: var(--layout-main-bg);
  padding: 24px;
  overflow-y: auto;
}
</style>
