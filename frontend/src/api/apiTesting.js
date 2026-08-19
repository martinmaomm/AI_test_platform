import api from './index'

/**
 * API测试相关的API服务
 */

// 获取API规范列表
export const getAPISpecifications = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/api-testing/api-specs/`, { params })
  return response.data
}

// 上传并解析API规范
export const uploadParseAPISpecification = async (projectId, formData) => {
  const response = await api.post(`/projects/${projectId}/api-testing/api-specs/`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

// 删除API规范
export const deleteAPISpecification = async (projectId, id) => {
  const response = await api.delete(`/projects/${projectId}/api-testing/api-specs/${id}/`)
  return response.data
}

// 更新API规范
export const updateAPISpecification = async (projectId, id, data) => {
  const response = await api.put(`/projects/${projectId}/api-testing/api-specs/${id}/`, data)
  return response.data
}

// 获取API端点列表
export const getAPIEndpoints = async (projectId, specId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/api-specs/${specId}/endpoints/`)
  return response.data
}

// 获取单个API端点详情（含 parameters / request_body / responses 完整规范）
export const getEndpointDetail = async (projectId, specId, endpointId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/api-specs/${specId}/endpoints/${endpointId}/`)
  return response.data
}

// 获取API测试统计信息
export const getAPITestStatistics = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/statistics/`)
  return response.data
}

// 获取API测试用例列表
export const getAPITestCases = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/api-testing/test-cases/`, { params })
  return response.data
}

// 获取指定端点的测试用例列表
export const getEndpointTestCases = async (projectId, specId, endpointId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/api-specs/${specId}/endpoints/${endpointId}/test-cases/`)
  return response.data
}

/**
 * 批量更新场景测试用例排序（拖拽后持久化）
 * 入参: { case_ids: [id1, id2, id3, ...] } 按新顺序排列的用例 ID 数组
 */
export const updateScenarioTestCasesOrder = async (projectId, caseIds) => {
  const response = await api.patch(
    `/projects/${projectId}/api-testing/test-cases/order/`,
    { case_ids: caseIds }
  )
  return response.data
}

/**
 * 批量更新端点测试用例排序（拖拽后持久化）
 * 入参: { case_ids: [id1, id2, id3, ...] } 按新顺序排列的用例 ID 数组
 */
export const updateEndpointTestCasesOrder = async (projectId, specId, endpointId, caseIds) => {
  const response = await api.patch(
    `/projects/${projectId}/api-testing/api-specs/${specId}/endpoints/${endpointId}/test-cases/order/`,
    { case_ids: caseIds }
  )
  return response.data
}

/**
 * 获取项目下模块列表（含排序）
 */
export const getAPIModules = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/modules/`)
  return response.data
}

/**
 * 批量更新模块排序（拖拽后持久化）
 * 入参: { module_names: ['用户相关操作', '订单', ...] } 或 { module_ids: [3, 1, 2] }
 */
export const updateModuleOrder = async (projectId, payload) => {
  const response = await api.patch(
    `/projects/${projectId}/api-testing/modules/order/`,
    payload
  )
  return response.data
}

/**
 * 批量更新端点排序（同一 spec 下拖拽后持久化）
 * 入参: { endpoint_ids: [5, 8, 4] } 按新顺序排列的端点 ID 数组
 */
export const updateEndpointOrder = async (projectId, specId, endpointIds) => {
  const response = await api.patch(
    `/projects/${projectId}/api-testing/api-specs/${specId}/endpoints/order/`,
    { endpoint_ids: endpointIds }
  )
  return response.data
}

// 创建API测试用例
export const createAPITestCase = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-cases/`, data)
  return response.data
}

// 更新API测试用例（全量 PUT）
export const updateAPITestCase = async (projectId, id, data) => {
  const response = await api.put(`/projects/${projectId}/api-testing/test-cases/${id}/`, data)
  return response.data
}

// 局部更新API测试用例（PATCH，只发送需要修改的字段）
export const patchAPITestCase = async (projectId, id, data) => {
  const response = await api.patch(`/projects/${projectId}/api-testing/test-cases/${id}/`, data)
  return response.data
}

// 删除API测试用例
export const deleteAPITestCase = async (projectId, id) => {
  const response = await api.delete(`/projects/${projectId}/api-testing/test-cases/${id}/`)
  return response.data
}

// 批量删除API测试用例
export const batchDeleteAPITestCases = async (projectId, caseIds) => {
  const response = await api.post(
    `/projects/${projectId}/api-testing/test-cases/batch-delete/`,
    { case_ids: caseIds }
  )
  return response.data
}

// 获取单个API测试用例详情
export const getAPITestCase = async (projectId, id) => {
  const response = await api.get(`/projects/${projectId}/api-testing/test-cases/${id}/`)
  return response.data
}

// 执行单个API测试用例
export const executeAPITestCase = async (projectId, id, data = {}) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-cases/${id}/execute/`, data)
  return response.data
}

// ============ 执行记录管理相关 ============

// 获取API测试执行记录列表
export const getAPITestExecutions = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/api-testing/executions/`, { params })
  return response.data
}

// 获取单用例执行详情
export const getAPITestCaseExecutionDetail = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/executions/case/${executionId}/`)
  return response.data
}

// 获取套件执行详情
export const getAPITestSuiteExecutionDetail = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/executions/suite/${executionId}/`)
  return response.data
}

// 获取套件执行的子用例详情
export const getAPITestExecutionCases = async (projectId, executionId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/executions/${executionId}/cases/`)
  return response.data
}

// 删除API测试执行记录
export const deleteAPITestExecution = async (projectId, executionId) => {
  const response = await api.delete(`/projects/${projectId}/api-testing/executions/${executionId}/delete/`)
  return response.data
}


// ============ API测试套件管理相关 ============

// 获取API测试套件列表
export const getAPITestSuites = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/api-testing/test-suites/`, { params })
  return response.data
}

// 创建API测试套件
export const createAPITestSuite = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-suites/`, data)
  return response.data
}

// 获取API测试套件详情
export const getAPITestSuite = async (projectId, id) => {
  const response = await api.get(`/projects/${projectId}/api-testing/test-suites/${id}/`)
  return response.data
}

// 更新API测试套件
export const updateAPITestSuite = async (projectId, id, data) => {
  const response = await api.put(`/projects/${projectId}/api-testing/test-suites/${id}/`, data)
  return response.data
}

// 删除API测试套件
export const deleteAPITestSuite = async (projectId, id) => {
  const response = await api.delete(`/projects/${projectId}/api-testing/test-suites/${id}/`)
  return response.data
}

// 添加测试用例到套件
export const addTestCasesToSuite = async (projectId, suiteId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-suites/${suiteId}/add-test-cases/`, data)
  return response.data
}

// 从套件移除测试用例
export const removeTestCaseFromSuite = async (projectId, suiteId, testCaseId) => {
  const response = await api.delete(`/projects/${projectId}/api-testing/test-suites/${suiteId}/remove-test-case/${testCaseId}/`)
  return response.data
}

// 执行API测试套件
export const executeAPITestSuite = async (projectId, suiteId, data = {}) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-suites/${suiteId}/execute/`, data)
  return response.data
}

// ============ AI测试用例生成相关 ============

// AI生成API规范测试用例
export const generateSpecTestCases = async (projectId, specId, data) => {
  const response = await api.post(`/projects/${projectId}/ai-core/generate-test-cases/${specId}/`, data)
  return response.data
}

// AI生成端点测试用例
export const generateEndpointTestCases = async (projectId, specId, endpointId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/generate-test-cases/${specId}/endpoint/${endpointId}/`, data)
  return response.data
}

// 获取端点测试用例生成任务状态（已废弃，使用统一的getTaskStatus）
export const getEndpointTestGenerationStatus = async (projectId, taskId) => {
  // 使用统一的任务状态查询接口
  return await getTaskStatus(projectId, taskId)
}

// ============ 任务状态管理相关 ============

// 获取任务状态
export const getTaskStatus = async (projectId, taskId) => {
  const response = await api.get(`/projects/${projectId}/api-testing/task-status/${taskId}/`)
  return response.data
}

// ============ 智能场景生成相关 ============

// 生成智能场景测试用例
export const generateScenario = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/generate-scenario/`, data)
  return response.data
}

// 调试场景步骤（截取前 N 步同步执行，返回逐步响应）
export const debugScenarioSteps = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/debug-scenario-steps/`, data)
  return response.data
}

// ===== 测试套件 =====

// 获取测试套件列表
export const getTestSuites = async (projectId, params = {}) => {
  const response = await api.get(`/projects/${projectId}/api-testing/test-suites/`, { params })
  return response.data
}

// 创建测试套件
export const createTestSuite = async (projectId, data) => {
  const response = await api.post(`/projects/${projectId}/api-testing/test-suites/`, data)
  return response.data
}

// 向套件中添加测试用例
export const addCasesToTestSuite = async (projectId, suiteId, testCaseIds) => {
  const response = await api.post(
    `/projects/${projectId}/api-testing/test-suites/${suiteId}/add-test-cases/`,
    { test_case_ids: testCaseIds }
  )
  return response.data
}

