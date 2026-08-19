import { defineStore } from 'pinia'
import { ref } from 'vue'

// 路由路径 → 显示标题的完整映射
const ROUTE_TITLE_MAP = {
  '/dashboard': '仪表盘',
  '/api-testing/projects': 'API 测试项目列表',
  '/web-testing/projects': 'Web 测试项目列表',
  '/app-testing/projects': 'App 测试项目列表',
  '/perf-testing/projects': '性能专项测试项目列表',
  '/perf-testing/workspace': '性能测试工作区',
  '/project/project-list': '项目列表',
  '/project/environments': '环境管理',
  '/api-testing/environments': '环境管理',
  '/web-testing/environments': '环境管理',
  '/app-testing/environments': '环境管理',
  '/perf-testing/environments': '环境管理',
  '/project/knowledge-base': '知识库管理',
  '/api-testing/knowledge-base': '知识库管理',
  '/web-testing/knowledge-base': '知识库管理',
  '/app-testing/knowledge-base': '知识库管理',
  '/api-testing/function-navigation': 'API功能导航',
  '/api-testing/api-specs': 'API规范管理',
  '/api-testing/scenario-generator': 'AI场景智能体',
  '/api-testing/test-cases/endpoint': '端点测试用例',
  '/api-testing/test-cases/scenario': '场景测试用例',
  '/api-testing/test-suites': '测试套件管理',
  '/api-testing/test-executions': '测试执行记录',
  '/web-testing/test-case-generator': '测试用例生成智能体',
  '/web-testing/webui-auto-test': 'AI 脚本实验室',
  '/web-testing/test-cases': '测试用例管理',
  '/web-testing/test-suites': '测试套件管理',
  '/web-testing/test-executions': '测试执行记录',
  '/app-testing/pom-parser': 'POM智能解析',
  '/app-testing/app-auto-test': 'App自动测试',
  '/app-testing/ui-agent': 'UI智能体',
  '/app-testing/test-cases': '测试用例管理',
  '/app-testing/test-runs': '测试执行记录',
  '/api-testing/scheduled-tasks': '定时任务',
  '/web-testing/scheduled-tasks': '定时任务',
  '/app-testing/scheduled-tasks': '定时任务',
  '/perf-testing/scheduled-tasks': '定时任务',
  '/project/scheduled-tasks': '定时任务',
  '/ai-config/llm': 'LLM模型配置',
  '/ai-config/rag': 'RAG向量数据库',
  '/ai-config/mcp': 'MCP配置',
  '/settings/channel-config': '消息通道配置',
  '/project/notification-receivers': '通知接收管理',
  '/api-testing/notification-receivers': '通知接收管理',
  '/web-testing/notification-receivers': '通知接收管理',
  '/app-testing/notification-receivers': '通知接收管理',
  '/perf-testing/notification-receivers': '通知接收管理',
  '/settings/email-config': '邮件服务配置',
  '/profile': '个人资料',
}

export const getRouteTitle = (path, routeMeta) => {
  if (ROUTE_TITLE_MAP[path]) return ROUTE_TITLE_MAP[path]
  if (routeMeta?.title) return routeMeta.title
  // 动态路由：如 /api-testing/specs/123
  if (path.startsWith('/api-testing/specs/')) return 'API规范详情'
  if (path.startsWith('/project/project-detail/')) return '项目详情'
  return path.split('/').filter(Boolean).pop() || '页面'
}

const HOME_TAB = { path: '/dashboard', title: '仪表盘', closable: false }

export const useTabStore = defineStore('tabs', () => {
  const tabs = ref([{ ...HOME_TAB }])
  const activeTab = ref('/dashboard')

  const addTab = (path, title) => {
    const exists = tabs.value.find(t => t.path === path)
    if (!exists) {
      tabs.value.push({ path, title, closable: true })
    }
    activeTab.value = path
  }

  const removeTab = (path, router) => {
    const idx = tabs.value.findIndex(t => t.path === path)
    if (idx === -1 || !tabs.value[idx].closable) return

    tabs.value.splice(idx, 1)

    // 如果关闭的是当前激活的 tab，跳转到相邻 tab
    if (activeTab.value === path) {
      const next = tabs.value[idx] || tabs.value[idx - 1] || tabs.value[0]
      if (next) {
        activeTab.value = next.path
        router.push(next.path)
      }
    }
  }

  const setActiveTab = (path) => {
    activeTab.value = path
  }

  const closeOtherTabs = (path, router) => {
    tabs.value = tabs.value.filter(t => !t.closable || t.path === path)
    activeTab.value = path
    router.push(path)
  }

  const closeAllTabs = (router) => {
    tabs.value = [{ ...HOME_TAB }]
    activeTab.value = '/dashboard'
    router.push('/dashboard')
  }

  const reset = () => {
    tabs.value = [{ ...HOME_TAB }]
    activeTab.value = '/dashboard'
  }

  return { tabs, activeTab, addTab, removeTab, setActiveTab, closeOtherTabs, closeAllTabs, reset }
})
