<template>
  <el-container class="layout-container">
    <!-- 侧边栏 -->
    <el-aside width="220px" class="sidebar">
      <div class="sidebar-header">
        <PlatformBrand size="small" />
      </div>

      <!-- 工作区头部：返回链接 + 项目身份区（项目名 + 模块名） -->
      <div class="workspace-header">
        <a class="back-link" @click.prevent="goBackToProjectList">
          <el-icon><Back /></el-icon>
          <span>返回列表</span>
        </a>
        <div class="identity-strip">
          <div v-if="currentProject" class="project-row">
            <el-icon class="project-icon"><FolderOpened /></el-icon>
            <span class="project-name">{{ currentProject.name }}</span>
          </div>
          <div class="module-row">{{ currentMenuName }}</div>
        </div>
      </div>

      <!-- 动态上下文菜单：仅渲染当前模块的子菜单 -->
      <el-menu
        v-if="dynamicMenus.length > 0"
        :default-active="activeMenuIndex"
        class="sidebar-menu"
        router
        background-color="transparent"
        text-color="var(--layout-sidebar-text)"
        active-text-color="var(--layout-sidebar-active)"
        @select="handleMenuSelect"
      >
        <template v-for="item in dynamicMenus" :key="item.path || item.label">
          <el-menu-item-group v-if="item.group" :title="item.label">
            <el-menu-item
              v-for="child in item.children"
              :key="child.path"
              :index="child.path"
            >
              {{ child.label }}
            </el-menu-item>
          </el-menu-item-group>
          <el-sub-menu v-else-if="item.children" :index="item.path">
            <template #title>{{ item.label }}</template>
            <el-menu-item
              v-for="child in item.children"
              :key="child.path"
              :index="child.path"
            >
              {{ child.label }}
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="item.path">
            <span>{{ item.label }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <!-- 右侧内容区 -->
    <el-container class="main-wrapper">
      <!-- 顶部导航 -->
      <el-header class="header">
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
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 多标签页栏 -->
      <div class="tab-bar">
        <div class="tab-list" ref="tabListRef">
          <div
            v-for="tab in tabStore.tabs"
            :key="tab.path"
            class="tab-item"
            :class="{ 'tab-item--active': tabStore.activeTab === tab.path }"
            @click="switchTab(tab.path)"
            @contextmenu.prevent="openContextMenu($event, tab)"
          >
            <span class="tab-title">{{ tab.title }}</span>
            <!-- 刷新图标：仅在当前激活 Tab 上显示 -->
            <el-icon
              v-if="tabStore.activeTab === tab.path"
              class="tab-refresh"
              title="刷新当前页面"
              @click.stop="reload(route.fullPath)"
            >
              <RefreshRight />
            </el-icon>
            <el-icon
              v-if="tab.closable"
              class="tab-close"
              @click.stop="closeTab(tab.path)"
            >
              <Close />
            </el-icon>
          </div>
        </div>
      </div>

      <!-- 右键菜单 -->
      <div
        v-if="contextMenu.visible"
        class="context-menu"
        :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
        @click.stop
      >
        <div class="context-menu-item" @click="handleContextMenuAction('refresh')">
          <el-icon><RefreshRight /></el-icon> 刷新当前
        </div>
        <div class="context-menu-divider" />
        <div class="context-menu-item" @click="handleContextMenuAction('close')">
          <el-icon><Close /></el-icon> 关闭当前
        </div>
        <div class="context-menu-item" @click="handleContextMenuAction('closeOthers')">
          <el-icon><Remove /></el-icon> 关闭其他
        </div>
        <div class="context-menu-item" @click="handleContextMenuAction('closeAll')">
          <el-icon><CircleClose /></el-icon> 关闭所有
        </div>
      </div>

      <!-- 主内容 -->
      <el-main class="main-content">
        <router-view v-slot="{ Component, route: currentRoute }">
          <component
            v-if="currentRoute.meta.cache === false"
            :is="Component"
            :key="componentCacheKey(currentRoute)"
          />
          <!-- 保持缓存容器常驻：来源工作区只是不进入缓存，不能清空其他页面实例。 -->
          <keep-alive :max="15">
            <component
              v-if="currentRoute.meta.cache !== false"
              :is="Component"
              :key="componentCacheKey(currentRoute)"
            />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { useTabStore, getRouteTitle } from '@/stores/tabs'
import {
  ArrowDown, Back,
  RefreshRight, Close, Remove, CircleClose,
  Sunny, Moon, Monitor, FolderOpened
} from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import PlatformBrand from '@/components/PlatformBrand.vue'

// -------- 全量菜单配置（按 module 分组，供动态过滤）--------
const MENU_CONFIG = {
  project: {
    title: '项目管理',
    items: [
      { path: '/project/project-list', label: '项目列表' },
      { path: '/project/environments', label: '环境管理' },
      { path: '/project/knowledge-base', label: '知识库管理' },
      { path: '/project/scheduled-tasks', label: '定时任务' },
      { path: '/project/notification-receivers', label: '通知接收管理' }
    ]
  },
  api: {
    title: 'API 测试',
    items: [
      { path: '/api-testing/api-specs', label: 'API规范管理' },
      { path: '/api-testing/workspace', label: 'API对话工作区' },
      {
        path: '/api-testing/test-cases',
        label: '测试用例管理',
        children: [
          { path: '/api-testing/test-cases/endpoint', label: '端点测试用例' },
          { path: '/api-testing/test-cases/scenario', label: '场景测试用例' }
        ]
      },
      { path: '/api-testing/test-suites', label: '测试套件管理' },
      { path: '/api-testing/test-executions', label: '测试执行记录' },
      { path: '/api-testing/scheduled-tasks', label: '定时任务' },
      { path: '/api-testing/environments', label: '环境管理' },
      { path: '/api-testing/knowledge-base', label: '知识库管理' },
      { path: '/api-testing/notification-receivers', label: '邮件通知' }
    ]
  },
  web: {
    title: 'Web 测试',
    items: [
      {
        group: true,
        label: '脚本生成',
        children: [
          { path: '/web-testing/create', label: 'AI 脚本生成' }
        ]
      },
      {
        group: true,
        label: '测试资产',
        children: [
          { path: '/web-testing/test-cases', label: '测试用例' },
          { path: '/web-testing/test-suites', label: '测试套件' }
        ]
      },
      {
        group: true,
        label: '测试执行',
        children: [
          { path: '/web-testing/test-executions', label: '执行记录' },
          { path: '/web-testing/scheduled-tasks', label: '定时任务' }
        ]
      },
      {
        group: true,
        label: '项目配置',
        children: [
          { path: '/web-testing/knowledge-base', label: '知识库' },
          { path: '/web-testing/notification-receivers', label: '邮件通知' }
        ]
      }
    ]
  },
  perf: {
    title: '性能测试',
    items: [
      { path: '/perf-testing/plans', label: '压测计划' },
      { path: '/perf-testing/runs', label: '执行记录' },
      { path: '/perf-testing/nodes', label: '节点管理' },
      { path: '/perf-testing/targets', label: '压测目标' }
    ]
  }
}

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const tabStore = useTabStore()
const appStore = useAppStore()

const tabListRef = ref(null)

// -------- 局部刷新机制（动态 Key 击穿 keep-alive 缓存）--------
// 未缓存页面按 path 计数，避免 query（例如 workspace_id）产生并存实例。
const refreshKeyMap = ref({})

const refreshScope = (target) => {
  const resolved = router.resolve(target || route.fullPath)
  return resolved.meta?.cache === false ? resolved.path : resolved.fullPath
}

const componentCacheKey = (currentRoute) => {
  const scope = currentRoute.meta?.cache === false
    ? currentRoute.path
    : currentRoute.fullPath
  return `${scope}:${refreshKeyMap.value[scope] || 0}`
}

const reload = (path) => {
  const key = refreshScope(path)
  refreshKeyMap.value[key] = (refreshKeyMap.value[key] || 0) + 1
}
// 向所有子组件提供 reload 方法，子组件可通过 inject('reload') 主动触发刷新
provide('reload', reload)

// 右键菜单状态
const contextMenu = ref({ visible: false, x: 0, y: 0, tab: null })

// -------- 路由监听：自动添加 Tab --------
watch(() => route.path, (path) => {
  if (!path || path === '/') return
  const title = getRouteTitle(path, route.meta)
  tabStore.addTab(path, title)
  nextTick(() => scrollActiveTabIntoView())
}, { immediate: true })

// -------- Tab 操作 --------
const switchTab = (path) => {
  tabStore.setActiveTab(path)
  router.push(path)
}

const closeTab = (path) => {
  tabStore.removeTab(path, router)
}

const openContextMenu = (e, tab) => {
  contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, tab }
}

const handleContextMenuAction = (action) => {
  const { tab } = contextMenu.value
  contextMenu.value.visible = false
  if (!tab) return
  switch (action) {
    case 'refresh':
      if (tab.path === tabStore.activeTab) {
        reload(route.fullPath)
      } else {
        switchTab(tab.path)
        nextTick(() => reload(tab.path))
      }
      break
    case 'close':
      if (tab.closable) closeTab(tab.path)
      break
    case 'closeOthers':
      tabStore.closeOtherTabs(tab.path, router)
      break
    case 'closeAll':
      tabStore.closeAllTabs(router)
      break
  }
}

// 左侧菜单 select 事件：点击当前已激活路由时触发刷新
const handleMenuSelect = (index) => {
  if (index === route.path) reload(route.fullPath)
}

// 点击其他区域关闭右键菜单
const hideContextMenu = () => { contextMenu.value.visible = false }
onMounted(() => document.addEventListener('click', hideContextMenu))
onBeforeUnmount(() => document.removeEventListener('click', hideContextMenu))

// 让激活的 Tab 滚动到可视区域
const scrollActiveTabIntoView = () => {
  if (!tabListRef.value) return
  const activeEl = tabListRef.value.querySelector('.tab-item--active')
  if (activeEl) activeEl.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
}

// -------- 动态菜单：根据当前路由 meta.module 过滤 --------
const currentModule = computed(() => {
  const fromMeta = route.meta?.module
  if (fromMeta) return fromMeta
  // 从路径推断模块（兜底，确保工作区路由总能正确显示菜单）
  if (route.path.startsWith('/api-testing')) return 'api'
  if (route.path.startsWith('/web-testing')) return 'web'
  if (route.path.startsWith('/perf-testing')) return 'perf'
  if (route.path.startsWith('/project')) return 'project'
  return null
})

const dynamicMenus = computed(() => {
  const module = currentModule.value
  if (!module || !MENU_CONFIG[module]) return []
  return MENU_CONFIG[module].items
})

// 从 MENU_CONFIG 获取当前激活菜单的 label，与 el-menu-item 渲染文字完全一致
function findMenuLabelByPath(path) {
  for (const module of Object.values(MENU_CONFIG)) {
    for (const item of module.items) {
      if (item.path === path) return item.label
      if (item.children) {
        const child = item.children.find(c => c.path === path)
        if (child) return child.label
      }
    }
  }
  return null
}

const currentMenuName = computed(() => {
  const label = findMenuLabelByPath(activeMenuIndex.value)
  return label || route.meta?.title || '工作区'
})

// -------- 返回项目列表：按业务线跳转到对应 L2 项目列表页 --------
const MODULE_PROJECT_LIST_PATH = {
  api: '/api-testing/projects',
  web: '/web-testing/projects',
  perf: '/perf-testing/projects',
  project: '/project/project-list'
}

const backToProjectListPath = computed(() => {
  const module = currentModule.value
  return (module && MODULE_PROJECT_LIST_PATH[module]) || '/dashboard'
})

const goBackToProjectList = () => {
  router.push(backToProjectListPath.value)
}

// -------- 菜单激活状态 --------
const activeMenuIndex = computed(() => {
  const p = route.path
  // 动态路由兜底：详情页高亮对应父级菜单
  if (/^\/api-testing\/specs\/[^/]+/.test(p)) return '/api-testing/api-specs'
  if (p.startsWith('/api-testing/workspace/')) return '/api-testing/workspace'
  if (/^\/project\/project-detail\/[^/]+/.test(p)) return '/project/project-list'
  // 模块基础路径 -> 默认子页
  if (p === '/api-testing') return '/api-testing/workspace'
  if (p === '/web-testing' || p === '/web-testing/create' || p.startsWith('/web-testing/create/')) return '/web-testing/create'
  if (p === '/perf-testing' || p === '/perf-testing/workspace') return '/perf-testing/plans'
  if (p === '/project') return '/project/project-list'
  return p
})

// -------- 其他 --------
const currentProject = computed(() => projectStore.currentProject)

const handleCommand = async (command) => {
  switch (command) {
    case 'profile':
      router.push('/profile')
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

onMounted(async () => {
  if (!projectStore.currentProject) {
    await projectStore.initializeUserPreferences()
  }
})
</script>

<style scoped>
.layout-container {
  height: 100vh;
  overflow: hidden;
}

/* 侧边栏 */
.sidebar {
  background-color: var(--layout-sidebar-bg);
  color: var(--layout-sidebar-text);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
}

.sidebar-header {
  padding: 20px 20px 24px;
  text-align: center;
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
  overflow: visible;
}

/* 工作区头部：返回链接 + 项目身份区 */
.workspace-header {
  padding: 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
  text-align: left;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.55);
  text-decoration: none;
  cursor: pointer;
  margin-bottom: 14px;
  transition: color 0.2s;
}

.back-link:hover {
  color: rgba(255, 255, 255, 0.9);
}

.back-link .el-icon {
  font-size: 14px;
}

.identity-strip {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  padding: 12px 14px;
}

.project-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--layout-sidebar-text);
  opacity: 0.9;
  margin-bottom: 6px;
}

.project-row .project-icon {
  font-size: 14px;
  flex-shrink: 0;
  color: var(--layout-sidebar-active);
}

.project-row .project-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.module-row {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  line-height: 1.3;
}

.sidebar-menu {
  border: none;
  flex: 1;
  background-color: transparent !important;
}

/* 右侧主区域 */
.main-wrapper {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* 顶部 Header */
.header {
  background: var(--layout-header-bg);
  border-bottom: 1px solid var(--layout-header-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  height: 52px;
  flex-shrink: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 6px;
  transition: background-color 0.2s;
}

.user-info:hover {
  background-color: var(--layout-hover-bg);
}

.username {
  font-size: 13px;
  color: var(--app-text-primary);
  font-weight: 500;
}

/* 多标签页栏 */
.tab-bar {
  background: var(--layout-tab-bg);
  border-bottom: 1px solid var(--layout-tab-border);
  flex-shrink: 0;
  overflow: hidden;
}

.tab-list {
  display: flex;
  align-items: stretch;
  overflow-x: auto;
  scrollbar-width: none;
  height: 38px;
}

.tab-list::-webkit-scrollbar {
  display: none;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 14px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-text-secondary);
  white-space: nowrap;
  border-right: 1px solid var(--layout-tab-border);
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  user-select: none;
  flex-shrink: 0;
  min-width: 80px;
  max-width: 160px;
}

.tab-item:hover {
  color: var(--layout-sidebar-active);
  background-color: var(--layout-hover-bg);
}

.tab-item--active {
  color: var(--layout-sidebar-active);
  background-color: var(--layout-tab-active-bg);
  border-bottom-color: var(--layout-sidebar-active);
  font-weight: 500;
}

.tab-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.tab-close,
.tab-refresh {
  font-size: 12px;
  flex-shrink: 0;
  color: var(--app-text-muted);
  border-radius: 50%;
  padding: 1px;
  transition: all 0.15s;
}

.tab-close:hover {
  color: #fff;
  background-color: var(--layout-sidebar-active);
}

.tab-refresh:hover {
  color: var(--layout-sidebar-active);
  background-color: var(--layout-tab-active-bg);
}

/* 右键菜单 */
.context-menu {
  position: fixed;
  z-index: 9999;
  background: var(--layout-header-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  min-width: 140px;
  overflow: hidden;
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  font-size: 13px;
  color: var(--app-text-primary);
  cursor: pointer;
  transition: background-color 0.15s;
}

.context-menu-item:hover {
  background-color: var(--layout-hover-bg);
  color: var(--layout-sidebar-active);
}

.context-menu-divider {
  height: 1px;
  background-color: var(--app-border-light);
  margin: 3px 0;
}

/* 主内容 */
.main-content {
  flex: 1;
  background-color: var(--layout-main-bg);
  padding: 12px;
  overflow-y: auto;
  overflow-x: hidden;
}
</style>
