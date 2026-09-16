import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { canAccessRoute } from '@/utils/accessControl'
import { currentUserFailureLocation } from '@/utils/routeAuth'

const routes = [
  // ========== 顶层入口 ==========
  { path: '/', name: 'Root', component: () => import('@/views/RootRedirect.vue'), meta: { requiresAuth: false } },
  { path: '/login', name: 'Login', component: () => import('@/views/Login.vue'), meta: { requiresAuth: false } },
  { path: '/register', name: 'Register', component: () => import('@/views/Register.vue'), meta: { requiresAuth: false } },
  { path: '/permission-load-failure', name: 'PermissionLoadFailure', component: () => import('@/views/PermissionLoadFailure.vue'), meta: { requiresAuth: false } },
  { path: '/reports/detail/:id', name: 'TestReportDetail', component: () => import('@/views/reports/TestReportDetail.vue'), meta: { requiresAuth: false, publicReport: true } },
  { path: '/reports/web/:projectId/:executionId', name: 'WebExecutionReport', component: () => import('@/views/reports/ExecutionReportPage.vue'), props: { kind: 'web' }, meta: { requiresAuth: false, publicReport: true } },
  { path: '/reports/api/:projectId/:executionId', name: 'ApiExecutionReport', component: () => import('@/views/reports/ExecutionReportPage.vue'), props: { kind: 'api' }, meta: { requiresAuth: false, publicReport: true } },

  // ========== 门户模块 (PortalLayout - 无业务侧边栏) ==========
  {
    path: '/dashboard',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal' },
    children: [
      { path: '', name: 'Dashboard', component: () => import('@/views/Dashboard.vue') }
    ]
  },

  // 系统设置
  {
    path: '/settings',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', requiresPlatformAdmin: true },
    children: [
      { path: '', name: 'SystemSettings', component: () => import('@/views/settings/SystemSettings.vue'), meta: { title: '全局系统配置' } },
      { path: 'channel-config', redirect: '/settings/email-config' },
      { path: 'email-config', name: 'EmailConfig', component: () => import('@/views/notifications/EmailConfigList.vue'), meta: { title: '邮件服务配置' } },
      { path: 'notification-channels', redirect: '/settings/email-config' },
      { path: 'users', name: 'UserManagement', component: () => import('@/views/settings/UserManagement.vue'), meta: { title: '用户管理', requiresPlatformAdmin: true } }
    ]
  },

  // 个人资料
  {
    path: '/profile',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal' },
    children: [
      { path: '', name: 'Profile', component: () => import('@/views/Profile.vue') }
    ]
  },

  // 个人登录记录
  {
    path: '/login-records',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal' },
    children: [
      { path: '', name: 'LoginRecords', component: () => import('@/views/LoginRecords.vue'), meta: { title: '登录记录' } }
    ]
  },

  // AI配置管理
  {
    path: '/ai-config',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', requiresPlatformAdmin: true },
    children: [
      { path: '', name: 'AIConfig', component: () => import('@/views/ai_config/AIConfig.vue'), meta: { title: 'AI 实验室配置' } },
      { path: 'llm', name: 'LLMConfig', component: () => import('@/views/ai_config/LLMConfig.vue'), meta: { title: 'LLM模型配置' } },
      { path: 'rag', name: 'RAGConfig', component: () => import('@/views/ai_config/RAGConfig.vue'), meta: { title: 'RAG向量数据库配置' } },
      { path: 'mcp', name: 'MCPConfig', component: () => import('@/views/ai_config/MCPConfig.vue'), meta: { title: 'MCP配置' } }
    ]
  },

  // LLM设置 (保持向后兼容)
  {
    path: '/llm-settings',
    redirect: '/ai-config/llm'
  },

  // ========== L2 业务线项目列表 (PortalLayout - 无侧边栏，领域门户) ==========
  {
    path: '/api-testing/projects',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', title: 'API 测试项目列表' },
    children: [
      { path: '', name: 'APIProjectList', component: () => import('@/views/api-testing/APIProjectList.vue') }
    ]
  },
  {
    path: '/web-testing/projects',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', title: 'Web 测试项目列表' },
    children: [
      { path: '', name: 'WebProjectList', component: () => import('@/views/web-testing/WebProjectList.vue') }
    ]
  },
  {
    path: '/perf-testing/projects',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', title: '性能专项测试项目列表' },
    children: [
      { path: '', name: 'PerfProjectList', component: () => import('@/views/perf-testing/PerfProjectList.vue') }
    ]
  },

  // ========== 工作区模块 (WorkspaceLayout / MainLayout - 有动态侧边栏) ==========

  // 项目管理
  {
    path: '/project',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'project', title: '项目管理' },
    children: [
      { path: '', redirect: '/project/project-list' },
      { path: 'project-list', name: 'ProjectList', component: () => import('@/views/project/ProjectList.vue') },
      { path: 'project-detail/:id', name: 'ProjectDetail', component: () => import('@/views/project/ProjectDetail.vue') },
      { path: 'knowledge-base', name: 'KnowledgeBase', component: () => import('@/views/project/KnowledgeBase.vue') },
      { path: 'environments', name: 'ProjectEnvironments', component: () => import('@/views/project/ProjectEnvironments.vue'), meta: { title: '环境管理' } },
      { path: 'scheduled-tasks', name: 'ProjectScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'notification-receivers', name: 'ProjectNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } }
    ]
  },

  // API测试管理
  {
    path: '/api-testing',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'api', title: 'API 测试' },
    children: [
      { path: '', redirect: '/api-testing/workspace' },
      { path: 'function-navigation', redirect: '/api-testing/workspace' },
      { path: 'api-specs', name: 'APITesting', component: () => import('@/views/api-testing/ApiSpecManage.vue') },
      { path: 'specs/:id', name: 'APISpecDetail', component: () => import('@/views/api-testing/ApiSpecDetail.vue') },
      { path: 'workspace', name: 'ApiWorkspace', redirect: (to) => ({ path: '/api-testing/workspace/documents', query: to.query }) },
      { path: 'workspace/documents', name: 'ApiWorkspaceDocuments', component: () => import('@/views/api-testing/ApiWorkspaceDocuments.vue'), meta: { title: 'API 对话工作区 · 接口文档', cache: false } },
      { path: 'workspace/browser', name: 'ApiWorkspaceBrowser', component: () => import('@/views/api-testing/ApiWorkspaceBrowser.vue'), meta: { title: 'API 对话工作区 · 网页探索', cache: false } },
      { path: 'scenario-generator', redirect: '/api-testing/workspace' },
      { path: 'test-cases', redirect: '/api-testing/test-cases/endpoint' },
      { path: 'test-cases/endpoint', name: 'EndpointTestCases', component: () => import('@/views/api-testing/EndpointTestCases.vue'), meta: { title: '端点测试用例' } },
      { path: 'test-cases/scenario', name: 'ScenarioTestCases', component: () => import('@/views/api-testing/ScenarioOrchestratorPage.vue'), meta: { title: '场景测试用例' } },
      { path: 'test-suites', name: 'ApiTestSuites', component: () => import('@/views/api-testing/TestSuites.vue'), meta: { title: '测试套件管理' } },
      { path: 'test-executions', name: 'ApiTestExecutions', component: () => import('@/views/api-testing/TestExecutions.vue'), meta: { title: '测试执行记录' } },
      { path: 'scheduled-tasks', name: 'ApiScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'environments', name: 'ApiEnvironments', component: () => import('@/views/project/ProjectEnvironments.vue'), meta: { title: '环境管理' } },
      { path: 'notification-receivers', name: 'ApiNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } },
      { path: 'knowledge-base', name: 'ApiKnowledgeBase', component: () => import('@/views/project/KnowledgeBase.vue'), meta: { title: '知识库管理' } }
    ]
  },

  // Web测试管理
  {
    path: '/web-testing',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'web', title: 'Web 测试' },
    children: [
      { path: '', redirect: '/web-testing/create' },
      { path: 'create', name: 'WebScriptCreate', component: () => import('@/views/web-testing/WebUIAutoTest.vue'), meta: { title: 'AI 脚本生成' } },
      { path: 'test-cases', name: 'WebTestCases', component: () => import('@/views/web-testing/TestCases.vue'), meta: { title: '测试用例管理' } },
      { path: 'test-suites', name: 'WebTestSuites', component: () => import('@/views/web-testing/TestSuites.vue'), meta: { title: '测试套件管理' } },
      { path: 'test-executions', name: 'WebTestExecutions', component: () => import('@/views/web-testing/TestExecutions.vue'), meta: { title: '测试执行记录' } },
      { path: 'scheduled-tasks', name: 'WebScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'notification-receivers', name: 'WebNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } },
      { path: 'knowledge-base', name: 'WebKnowledgeBase', component: () => import('@/views/project-knowledge/ProjectKnowledgeWorkspace.vue'), meta: { title: '项目知识库' } }
    ]
  },

  // 性能专项测试
  {
    path: '/perf-testing',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'perf', title: '性能测试' },
    children: [
      { path: '', redirect: '/perf-testing/plans' },
      { path: 'workspace', redirect: '/perf-testing/plans' },
      { path: 'plans', name: 'PerfPlans', component: () => import('@/views/perf-testing/PerfWorkspace.vue'), meta: { title: '压测计划' } },
      { path: 'runs', name: 'PerfRuns', component: () => import('@/views/perf-testing/PerfRunList.vue'), meta: { title: '执行记录' } },
      { path: 'runs/:runId', name: 'PerfRunDetail', component: () => import('@/views/perf-testing/PerfRunDetail.vue'), meta: { title: '执行详情' } },
      { path: 'nodes', name: 'PerfNodes', component: () => import('@/views/perf-testing/PerfWorkspace.vue'), meta: { title: '节点管理' } },
      { path: 'targets', name: 'PerfTargets', component: () => import('@/views/perf-testing/PerfWorkspace.vue'), meta: { title: '压测目标' } }
    ]
  }
]

// 创建路由实例
const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  if (to.path === '/') {
    return next()
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return next({ path: '/login', query: { redirect: to.fullPath } })
  }

  if (to.path === '/login' && authStore.isAuthenticated) {
    return next('/dashboard')
  }

  if (to.meta.requiresAuth) {
    const refreshed = await authStore.refreshCurrentUser()
    if (!refreshed.ok) return next(currentUserFailureLocation(to, refreshed.authenticationFailed))
    if (!canAccessRoute(authStore.user, to)) return next('/dashboard')
  }

  next()
})

export default router
