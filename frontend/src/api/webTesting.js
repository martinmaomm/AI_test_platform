import api from './index'

/**
 * Web测试相关的API服务
 */

// ============ POM 页面对象管理 ============

// 获取 WebUI 动作配置字典（从后端动态下发）
export const getWebUIActionsDict = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/dict/web-actions/`)
  return response.data
}

// 获取 Web 页面列表
export const getWebPages = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/pages/`)
  return response.data
}

// 创建 Web 页面
export const createWebPage = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/pages/`, data)
  return response.data
}

// 更新 Web 页面
export const updateWebPage = async (projectId, pageId, data) => {
  const response = await api.put(`/projects/${projectId}/web-testing/pages/${pageId}/`, data)
  return response.data
}

// 删除 Web 页面
export const deleteWebPage = async (projectId, pageId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/pages/${pageId}/`)
  return response.data
}

// 获取 Web 元素列表（支持 page_id 过滤）
export const getWebElements = async (projectId, pageId = null) => {
  const params = pageId ? { page_id: pageId } : {}
  const response = await api.get(`/projects/${projectId}/web-testing/elements/`, { params })
  return response.data
}

// 创建 Web 元素
export const createWebElement = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/elements/`, data)
  return response.data
}

// 更新 Web 元素
export const updateWebElement = async (projectId, elementId, data) => {
  const response = await api.put(`/projects/${projectId}/web-testing/elements/${elementId}/`, data)
  return response.data
}

// 删除 Web 元素
export const deleteWebElement = async (projectId, elementId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/elements/${elementId}/`)
  return response.data
}

// 一键清空项目测试资产（调试专用）
export const clearProjectWebAssets = async (projectId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/clear-assets/`)
  return response.data
}

// 从知识库文档提取 POM 骨架
export const extractPomFromDoc = async (projectId, fileId) => {
  const response = await api.post(`/projects/${projectId}/web-testing/extract-pom/`, { file_id: fileId })
  return response.data
}

// 从 HTML 源码智能回填定位器
export const fillLocatorsFromHtml = async (projectId, pageId, htmlSource) => {
  const response = await api.post(
    `/projects/${projectId}/web-testing/pages/${pageId}/fill-locators/`,
    { html_source: htmlSource }
  )
  return response.data
}

// 批量删除 Web 页面
export const batchDeleteWebPages = async (projectId, ids) => {
  const response = await api.post(`/projects/${projectId}/web-testing/pages/batch-delete/`, { ids })
  return response.data
}

// 批量删除 Web 元素
export const batchDeleteWebElements = async (projectId, ids) => {
  const response = await api.post(`/projects/${projectId}/web-testing/elements/batch-delete/`, { ids })
  return response.data
}

// ============ 任务管理 ============

// 获取任务状态（使用统一的任务状态查询接口）
export const getTaskStatus = async (projectId, taskId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/task-status/${taskId}/`)
  return response.data
}


// ============ 统计信息 ============

// 获取WebUI测试执行统计信息
export const getWebUITestExecutionStatistics = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/execution-statistics/`)
  return response.data
}

// ============ WebUI测试模块树管理 (RESTful) ============

// 获取WebUI测试模块树（当前项目下，树状结构）
export const getWebUITestModules = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/modules/`)
  return response.data
}

// 创建WebUI测试模块
export const createWebUITestModule = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/modules/`, data)
  return response.data
}

// 更新WebUI测试模块
export const updateWebUITestModule = async (projectId, moduleId, data) => {
  const response = await api.put(`/projects/${projectId}/web-testing/modules/${moduleId}/`, data)
  return response.data
}

// 删除WebUI测试模块
export const deleteWebUITestModule = async (projectId, moduleId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/modules/${moduleId}/`)
  return response.data
}

// ============ WebUI测试用例管理 (RESTful) ============

// 生成WebUI测试用例
export const generateWebUITestCases = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-cases/generate/`, data)
  return response.data
}

// 获取WebUI测试用例列表
export const getWebUITestCases = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/web-testing/test-cases/`, { params })
  return response.data
}

// 创建WebUI测试用例
export const createWebUITestCase = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-cases/`, data)
  return response.data
}

// 获取WebUI测试用例详情
export const getWebUITestCase = async (projectId, testCaseId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/test-cases/${testCaseId}/`)
  return response.data
}

// 更新WebUI测试用例 (完整更新)
export const updateWebUITestCase = async (projectId, testCaseId, data) => {
  const response = await api.put(`/projects/${projectId}/web-testing/test-cases/${testCaseId}/`, data)
  return response.data
}

// 部分更新WebUI测试用例
export const patchWebUITestCase = async (projectId, testCaseId, data) => {
  const response = await api.patch(`/projects/${projectId}/web-testing/test-cases/${testCaseId}/`, data)
  return response.data
}

// 删除WebUI测试用例
export const deleteWebUITestCase = async (projectId, testCaseId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/test-cases/${testCaseId}/`)
  return response.data
}

// 批量删除WebUI测试用例
export const batchDeleteWebUITestCases = async (projectId, caseIds) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-cases/batch-delete/`, { case_ids: caseIds })
  return response.data
}

// 批量更新WebUI测试用例（支持 priority, module_id）
export const batchUpdateWebUITestCases = async (projectId, caseIds, updateData) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-cases/batch-update/`, {
    case_ids: caseIds,
    update_data: updateData
  })
  return response.data
}

// ============ WebUI测试脚本管理 ============

// 创建WebUI测试脚本
export const createWebUITestScript = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/scripts/`, data)
  return response.data
}

// 保存 AI 脚本实验室生成的脚本为测试用例
export const saveGeneratedWebUITestScript = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/scripts/save/`, data)
  return response.data
}

// 基于测试用例创建WebUI测试脚本
export const createWebUITestScriptFromTestCase = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/scripts/from-testcase/`, data)
  return response.data
}

// 停止WebUI测试脚本生成任务
export const stopWebUITestScript = async (projectId, taskId) => {
  const response = await api.post(`/projects/${projectId}/web-testing/scripts/stop/`, { task_id: taskId })
  return response.data
}

// 停止基于测试用例的WebUI测试脚本生成任务
export const stopWebUITestScriptFromTestCase = async (projectId, taskId) => {
  const response = await api.post(`/projects/${projectId}/web-testing/scripts/from-testcase/stop/`, { task_id: taskId })
  return response.data
}

// ============ WebUI测试执行管理 ============

// 执行WebUI测试用例
export const executeWebUITestCase = async (projectId, testCaseId, data = {}) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-cases/${testCaseId}/execute/`, data)
  return response.data
}

// 生成 Playwright 代码（单条）
export const generateTestCaseCode = async (projectId, testCaseId, framework = 'playwright') => {
  const response = await api.post(
    `/projects/${projectId}/web-testing/test-cases/${testCaseId}/generate-code/`,
    { framework }
  )
  return response.data
}

// 批量生成 Playwright 代码
export const batchGenerateTestCaseCode = async (projectId, caseIds, framework = 'playwright') => {
  const response = await api.post(
    `/projects/${projectId}/web-testing/test-cases/batch-generate-code/`,
    { case_ids: caseIds, framework }
  )
  return response.data
}

// 保存测试用例脚本（POM 单点维护：test_function 含 import，page_classes 存 WebPage.pom_code）
export const saveTestCaseScript = async (projectId, testCaseId, payload, framework = 'playwright') => {
  const body = typeof payload === 'string'
    ? { script_content: payload, script_source: 'manual', framework }
    : {
        script_content: payload.script_content,
        page_classes: payload.page_classes,
        test_function: payload.test_function,
        script_source: payload.script_source || payload.source || 'manual',
        framework
      }
  const response = await api.post(
    `/projects/${projectId}/web-testing/test-cases/${testCaseId}/save-script/`,
    body
  )
  return response.data
}

// ============ WebUI测试套件管理 ============

// 获取WebUI测试套件列表
export const getTestSuites = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/web-testing/test-suites/`, { params })
  return response.data
}

// 创建WebUI测试套件
export const createTestSuite = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-suites/`, data)
  return response.data
}

// 获取WebUI测试套件详情
export const getTestSuite = async (projectId, suiteId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/test-suites/${suiteId}/`)
  return response.data
}

// 更新WebUI测试套件
export const updateTestSuite = async (projectId, suiteId, data) => {
  const response = await api.put(`/projects/${projectId}/web-testing/test-suites/${suiteId}/`, data)
  return response.data
}

// 删除WebUI测试套件
export const deleteTestSuite = async (projectId, suiteId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/test-suites/${suiteId}/`)
  return response.data
}

// 向测试套件添加测试用例
export const addTestCasesToSuite = async (projectId, suiteId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-suites/${suiteId}/add-test-cases/`, data)
  return response.data
}

// 从测试套件移除测试用例
export const removeTestCaseFromSuite = async (projectId, suiteId, testCaseId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/test-suites/${suiteId}/remove-test-case/${testCaseId}/`)
  return response.data
}

// 重新排序测试套件中的测试用例
export const reorderTestCasesInSuite = async (projectId, suiteId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-suites/${suiteId}/reorder/`, data)
  return response.data
}

// 切换测试套件中测试用例的激活状态
export const toggleTestCaseInSuite = async (projectId, suiteId, testCaseId) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-suites/${suiteId}/toggle-test-case/${testCaseId}/`)
  return response.data
}

// 执行测试套件
export const executeTestSuite = async (projectId, suiteId, data) => {
  const response = await api.post(`/projects/${projectId}/web-testing/test-suites/${suiteId}/execute/`, data)
  return response.data
}

// 获取测试套件统计信息
export const getTestSuiteStatistics = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/test-suite-statistics/`)
  return response.data
}

// ============ 统一执行记录管理 ============

// 获取所有执行记录（包含类型）
export const getTestExecutions = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/`, { params })
  return response.data
}

// 获取单次执行详情，根据exec_type返回不同结构
// 如果是套件执行，返回子用例执行详情
export const getTestExecutionCases = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/${executionId}/cases/`)
  return response.data
}

// ============ WebUI测试执行记录管理（旧接口，保持兼容） ============

// 获取WebUI测试执行记录列表（旧接口，保持兼容）
export const getWebUITestExecutions = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/`, { params })
  return response.data
}

// 获取WebUI测试执行记录详情（旧接口，保持兼容）
export const getWebUITestExecution = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/${executionId}/`)
  return response.data
}

// 获取单用例执行详情
export const getWebUITestCaseExecution = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/case/${executionId}/`)
  return response.data.data
}

// 获取套件执行详情
export const getWebUITestSuiteExecution = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/web-testing/executions/suite/${executionId}/`)
  return response.data.data
}

// 获取失败截图二进制数据。api 实例会通过请求拦截器自动携带当前 JWT。
export const getWebUITestExecutionScreenshot = async (projectId, executionId, caseExecutionId = null) => {
  const path = caseExecutionId == null
    ? `/projects/${projectId}/web-testing/executions/${executionId}/screenshot/`
    : `/projects/${projectId}/web-testing/executions/${executionId}/cases/${caseExecutionId}/screenshot/`
  const response = await api.get(path, { responseType: 'blob' })
  return response.data
}

// 删除执行记录
export const deleteTestExecution = async (projectId, executionId) => {
  const response = await api.delete(`/projects/${projectId}/web-testing/executions/${executionId}/delete/`)
  return response.data
}

// ============ WebSocket相关 ============

// 构建WebSocket URL（用于实时通信）
export const buildWebSocketUrl = (path) => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}${path}`
}


// ============ 工具函数 ============

// 解析WebSocket消息
const parseWebSocketMessage = (message) => {
  try {
    return JSON.parse(message)
  } catch (error) {
    return null
  }
}

// 处理WebSocket流式输出
export const handleStreamingOutput = (message, onUpdate) => {
  const data = parseWebSocketMessage(message)
  if (data && data.type === 'streaming_output') {
    onUpdate(data.content)
  }
}

// 处理节点开始通知
export const handleNodeStart = (message, onNodeStart) => {
  const data = parseWebSocketMessage(message)
  if (data && data.type === 'node_start') {
    onNodeStart(data.node_name, data.description)
  }
}

// 处理任务状态更新
export const handleTaskStatusUpdate = (message, onStatusUpdate) => {
  const data = parseWebSocketMessage(message)
  if (data && data.type === 'task_status_update') {
    onStatusUpdate(data.status, data.progress, data.message)
  }
}

// 处理任务完成
export const handleTaskCompleted = (message, onCompleted) => {
  const data = parseWebSocketMessage(message)
  if (data && data.type === 'task_completed') {
    onCompleted(data.result)
  }
}

// 处理任务失败
export const handleTaskFailed = (message, onFailed) => {
  const data = parseWebSocketMessage(message)
  if (data && data.type === 'task_failed') {
    onFailed(data.error)
  }
}
