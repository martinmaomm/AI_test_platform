import api from './index'

const base = (projectId) => `/projects/${projectId}/web-testing`

export const getTaskStatus = async (projectId, taskId) =>
  (await api.get(`${base(projectId)}/task-status/${taskId}/`)).data

export const getWebUITestExecutionStatistics = async (projectId) =>
  (await api.get(`${base(projectId)}/execution-statistics/`)).data

export const getWebUITestModules = async (projectId) =>
  (await api.get(`${base(projectId)}/modules/`)).data

export const createWebUITestModule = async (projectId, data) =>
  (await api.post(`${base(projectId)}/modules/`, data)).data

export const updateWebUITestModule = async (projectId, moduleId, data) =>
  (await api.put(`${base(projectId)}/modules/${moduleId}/`, data)).data

export const deleteWebUITestModule = async (projectId, moduleId) =>
  (await api.delete(`${base(projectId)}/modules/${moduleId}/`)).data

export const getWebUITestCases = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/test-cases/`, { params })).data

export const createWebUITestCase = async (projectId, data) =>
  (await api.post(`${base(projectId)}/test-cases/`, data)).data

export const getWebUITestCase = async (projectId, testCaseId) =>
  (await api.get(`${base(projectId)}/test-cases/${testCaseId}/`)).data

export const updateWebUITestCase = async (projectId, testCaseId, data) =>
  (await api.put(`${base(projectId)}/test-cases/${testCaseId}/`, data)).data

export const patchWebUITestCase = async (projectId, testCaseId, data) =>
  (await api.patch(`${base(projectId)}/test-cases/${testCaseId}/`, data)).data

export const deleteWebUITestCase = async (projectId, testCaseId) =>
  (await api.delete(`${base(projectId)}/test-cases/${testCaseId}/`)).data

export const batchDeleteWebUITestCases = async (projectId, caseIds) =>
  (await api.post(`${base(projectId)}/test-cases/batch-delete/`, { case_ids: caseIds })).data

export const batchUpdateWebUITestCases = async (projectId, caseIds, updateData) =>
  (await api.post(`${base(projectId)}/test-cases/batch-update/`, {
    case_ids: caseIds,
    update_data: updateData
  })).data

export const createWebUIScriptGeneration = async (projectId, data) =>
  (await api.post(`${base(projectId)}/script-generations/`, data)).data

export const getWebUIScriptGenerationSettings = async (projectId) =>
  (await api.get(`${base(projectId)}/script-generation-settings/`)).data

export const getWebUIScriptGeneration = async (projectId, generationId) =>
  (await api.get(`${base(projectId)}/script-generations/${generationId}/`)).data

export const cancelWebUIScriptGeneration = async (projectId, generationId) =>
  (await api.post(`${base(projectId)}/script-generations/${generationId}/cancel/`)).data

export const resolveWebUIScriptGeneration = async (projectId, generationId, data) =>
  (await api.post(`${base(projectId)}/script-generations/${generationId}/resolve/`, data)).data

export const updateWebUIScriptGenerationDraft = async (projectId, generationId, data) =>
  (await api.patch(`${base(projectId)}/script-generations/${generationId}/draft/`, data)).data

export const debugWebUIScriptGeneration = async (projectId, generationId, data) =>
  (await api.post(`${base(projectId)}/script-generations/${generationId}/debug/`, data)).data

export const repairWebUIScriptGeneration = async (projectId, generationId, data) =>
  (await api.post(`${base(projectId)}/script-generations/${generationId}/repair/`, data)).data

export const saveWebUIScriptGeneration = async (projectId, generationId, data = {}) =>
  (await api.post(`${base(projectId)}/script-generations/${generationId}/save/`, data)).data

export const executeWebUITestCase = async (projectId, testCaseId, data = {}) =>
  (await api.post(`${base(projectId)}/test-cases/${testCaseId}/execute/`, data)).data

export const getWebUITestSuites = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/test-suites/`, { params })).data

export const createWebUITestSuite = async (projectId, data) =>
  (await api.post(`${base(projectId)}/test-suites/`, data)).data

export const getWebUITestSuite = async (projectId, suiteId) =>
  (await api.get(`${base(projectId)}/test-suites/${suiteId}/`)).data

export const updateWebUITestSuite = async (projectId, suiteId, data) =>
  (await api.put(`${base(projectId)}/test-suites/${suiteId}/`, data)).data

export const deleteWebUITestSuite = async (projectId, suiteId) =>
  (await api.delete(`${base(projectId)}/test-suites/${suiteId}/`)).data

export const addTestCasesToSuite = async (projectId, suiteId, data) =>
  (await api.post(`${base(projectId)}/test-suites/${suiteId}/add-test-cases/`, data)).data

export const removeTestCaseFromSuite = async (projectId, suiteId, testCaseId) =>
  (await api.delete(`${base(projectId)}/test-suites/${suiteId}/remove-test-case/${testCaseId}/`)).data

export const reorderTestSuiteCases = async (projectId, suiteId, testCaseIds) =>
  (await api.post(`${base(projectId)}/test-suites/${suiteId}/reorder/`, {
    test_case_ids: testCaseIds
  })).data

export const executeWebUITestSuite = async (projectId, suiteId, data = {}) =>
  (await api.post(`${base(projectId)}/test-suites/${suiteId}/execute/`, data)).data

export const getWebUITestSuiteStatistics = async (projectId) =>
  (await api.get(`${base(projectId)}/test-suite-statistics/`)).data

export const getTestExecutions = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/executions/`, { params })).data

export const getTestExecutionCases = async (projectId, executionId) =>
  (await api.get(`${base(projectId)}/executions/${executionId}/cases/`)).data

export const getWebUITestCaseExecution = async (projectId, executionId) =>
  (await api.get(`${base(projectId)}/executions/case/${executionId}/`)).data

export const getWebUITestSuiteExecution = async (projectId, executionId) =>
  (await api.get(`${base(projectId)}/executions/suite/${executionId}/`)).data

export const getWebUITestExecutionScreenshot = async (projectId, executionId, caseExecutionId = null) => {
  const url = caseExecutionId == null
    ? `${base(projectId)}/executions/${executionId}/screenshot/`
    : `${base(projectId)}/executions/${executionId}/cases/${caseExecutionId}/screenshot/`
  return (await api.get(url, { responseType: 'blob' })).data
}

export const deleteTestExecution = async (projectId, executionId) =>
  (await api.delete(`${base(projectId)}/executions/${executionId}/delete/`)).data
