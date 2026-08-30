import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  // ========== 顶层入口 ==========
  { path: '/', name: 'Root', component: () => import('@/views/RootRedirect.vue'), meta: { requiresAuth: false } },
  { path: '/login', name: 'Login', component: () => import('@/views/Login.vue'), meta: { requiresAuth: false } },
  { path: '/register', name: 'Register', component: () => import('@/views/Register.vue'), meta: { requiresAuth: false } },
  { path: '/reports/detail/:id', name: 'TestReportDetail', component: () => import('@/views/reports/TestReportDetail.vue'), meta: { requiresAuth: false } },

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
    meta: { requiresAuth: true, layout: 'portal' },
    children: [
      { path: '', name: 'SystemSettings', component: () => import('@/views/settings/SystemSettings.vue'), meta: { title: '全局系统配置' } },
      { path: 'channel-config', name: 'ChannelConfig', component: () => import('@/views/settings/ChannelConfig.vue'), meta: { title: '消息通道配置' } },
      { path: 'email-config', name: 'EmailConfig', component: () => import('@/views/notifications/EmailConfigList.vue'), meta: { title: '邮件服务配置' } },
      { path: 'general-params', name: 'GeneralSystemParams', component: () => import('@/views/settings/GeneralSystemParams.vue'), meta: { title: '通用系统参数' } },
      { path: 'notification-channels', redirect: '/settings/channel-config' }
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

  // AI配置管理
  {
    path: '/ai-config',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal' },
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
    path: '/app-testing/projects',
    component: () => import('@/layouts/PortalLayout.vue'),
    meta: { requiresAuth: true, layout: 'portal', title: 'App 测试项目列表' },
    children: [
      { path: '', name: 'AppProjectList', component: () => import('@/views/app-testing/AppProjectList.vue') }
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
      { path: '', redirect: '/api-testing/function-navigation' },
      { path: 'function-navigation', name: 'FunctionNavigation', component: () => import('@/views/api-testing/FunctionNavigation.vue') },
      { path: 'api-specs', name: 'APITesting', component: () => import('@/views/api-testing/ApiSpecManage.vue') },
      { path: 'specs/:id', name: 'APISpecDetail', component: () => import('@/views/api-testing/ApiSpecDetail.vue') },
      { path: 'scenario-generator', name: 'ScenarioGenerator', component: () => import('@/views/api-testing/ScenarioGenerator.vue'), meta: { title: '智能场景生成器' } },
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
      { path: '', redirect: '/web-testing/create/requirements' },
      {
        path: 'create',
        component: () => import('@/views/web-testing/SmartCreate.vue'),
        meta: { title: '智能创建' },
        children: [
          { path: '', redirect: '/web-testing/create/requirements' },
          { path: 'requirements', name: 'WebRequirementCreate', component: () => import('@/views/web-testing/WebUITestCaseGenerator.vue'), meta: { title: '智能创建' } },
          { path: 'explore', name: 'WebExploreCreate', component: () => import('@/views/web-testing/WebUIAutoTest.vue'), meta: { title: 'AI 脚本生成' } }
        ]
      },
      { path: 'webui-auto-test', redirect: '/web-testing/create/explore' },
      { path: 'test-case-generator', redirect: '/web-testing/create/requirements' },
      { path: 'test-cases', name: 'WebTestCases', component: () => import('@/views/web-testing/TestCases.vue'), meta: { title: '测试用例管理' } },
      { path: 'test-suites', name: 'WebTestSuites', component: () => import('@/views/web-testing/TestSuites.vue'), meta: { title: '测试套件管理' } },
      { path: 'test-executions', name: 'WebTestExecutions', component: () => import('@/views/web-testing/TestExecutions.vue'), meta: { title: '测试执行记录' } },
      { path: 'scheduled-tasks', name: 'WebScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'environments', name: 'WebEnvironments', component: () => import('@/views/project/ProjectEnvironments.vue'), meta: { title: '环境管理' } },
      { path: 'page-objects', name: 'PageObjects', component: () => import('@/views/web-testing/PageObjects.vue'), meta: { title: '元素库管理' } },
      { path: 'notification-receivers', name: 'WebNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } },
      { path: 'knowledge-base', name: 'WebKnowledgeBase', component: () => import('@/views/project/KnowledgeBase.vue'), meta: { title: '知识库管理' } }
    ]
  },

  // App测试管理
  {
    path: '/app-testing',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'app', title: 'App 自动化测试' },
    children: [
      { path: '', redirect: '/app-testing/pom-parser' },
      { path: 'pom-parser', name: 'AppPomParser', component: () => import('@/views/app-testing/PomParser.vue'), meta: { title: 'POM智能解析' } },
      { path: 'app-auto-test', name: 'AppAutoTest', component: () => import('@/views/app-testing/AppAutoTest.vue'), meta: { title: 'App自动测试' } },
      { path: 'ui-agent', name: 'AppUiAgent', component: () => import('@/views/app-testing/UiAgent.vue'), meta: { title: 'UI智能体' } },
      { path: 'test-cases', name: 'AppTestCases', component: () => import('@/views/app-testing/TestCases.vue'), meta: { title: '测试用例管理' } },
      { path: 'test-executions', redirect: '/app-testing/test-runs' },
      { path: 'test-runs', name: 'AppTestRuns', component: () => import('@/views/app-testing/TestRuns.vue'), meta: { title: '测试执行记录' } },
      { path: 'scheduled-tasks', name: 'AppScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'environments', name: 'AppEnvironments', component: () => import('@/views/project/ProjectEnvironments.vue'), meta: { title: '环境管理' } },
      { path: 'notification-receivers', name: 'AppNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } },
      { path: 'knowledge-base', name: 'AppKnowledgeBase', component: () => import('@/views/project/KnowledgeBase.vue'), meta: { title: '知识库管理' } }
    ]
  },

  // 性能专项测试
  {
    path: '/perf-testing',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true, layout: 'workspace', module: 'perf', title: '性能专项测试' },
    children: [
      { path: '', redirect: '/perf-testing/workspace' },
      { path: 'workspace', name: 'PerfWorkspace', component: () => import('@/views/perf-testing/PerfWorkspacePlaceholder.vue'), meta: { title: '性能测试工作区' } },
      { path: 'scheduled-tasks', name: 'PerfScheduledTasks', component: () => import('@/views/scheduledTasks/ScheduledTasksPage.vue'), meta: { title: '定时任务' } },
      { path: 'environments', name: 'PerfEnvironments', component: () => import('@/views/project/ProjectEnvironments.vue'), meta: { title: '环境管理' } },
      { path: 'notification-receivers', name: 'PerfNotificationReceivers', component: () => import('@/views/project/NotificationReceivers.vue'), meta: { title: '通知接收管理' } }
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
    return next('/login')
  }

  if (to.path === '/login' && authStore.isAuthenticated) {
    return next('/dashboard')
  }

  next()
})

export default router
