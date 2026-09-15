import axios from 'axios'

// Public reports intentionally bypass the authenticated API client. In
// particular, an expired token must not be attached, refreshed, or used to
// clear the viewer's existing local session while they open a shared report.
const publicReportApi = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  },
  withCredentials: false
})

const segment = value => encodeURIComponent(String(value))

export const getPublicScheduledReport = async reportId =>
  (await publicReportApi.get(`/reports/detail/${segment(reportId)}/`)).data

export const getPublicWebUITestExecutionReport = async (projectId, executionId) =>
  (await publicReportApi.get(
    `/projects/${segment(projectId)}/web-testing/executions/${segment(executionId)}/report/`
  )).data

export const getPublicAPITestExecutionReport = async (projectId, executionId) =>
  (await publicReportApi.get(
    `/projects/${segment(projectId)}/api-testing/executions/${segment(executionId)}/report/`
  )).data

export const getPublicWebUITestExecutionScreenshot = async (projectId, executionId, caseExecutionId = null) => {
  const execution = segment(executionId)
  const url = caseExecutionId == null
    ? `/projects/${segment(projectId)}/web-testing/executions/${execution}/screenshot/`
    : `/projects/${segment(projectId)}/web-testing/executions/${execution}/cases/${segment(caseExecutionId)}/screenshot/`
  return (await publicReportApi.get(url, { responseType: 'blob' })).data
}
