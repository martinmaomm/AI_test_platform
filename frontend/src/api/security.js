import api from './index'

const base = (projectId) => `/projects/${encodeURIComponent(projectId)}/security`
const data = async (request) => (await request).data

export const getSecurityPlans = (projectId) => data(api.get(`${base(projectId)}/plans/`))
export const getSecurityPlan = (projectId, id) => data(api.get(`${base(projectId)}/plans/${id}/`))
export const createSecurityPlan = (projectId, payload) => data(api.post(`${base(projectId)}/plans/`, payload))
export const updateSecurityPlan = (projectId, id, payload) => data(api.patch(`${base(projectId)}/plans/${id}/`, payload))
export const deleteSecurityPlan = (projectId, id) => data(api.delete(`${base(projectId)}/plans/${id}/`))
export const generateSecurityCases = (projectId, id, payload) => data(api.post(`${base(projectId)}/plans/${id}/generate/`, payload))
export const runSecurityCases = (projectId, id, payload) => data(api.post(`${base(projectId)}/plans/${id}/runs/`, payload))
export const getSecurityTasks = (projectId) => data(api.get(`${base(projectId)}/tasks/`))
export const getSecurityTask = (projectId, id) => data(api.get(`${base(projectId)}/tasks/${id}/`))
export const stopSecurityTask = (projectId, id) => data(api.post(`${base(projectId)}/tasks/${id}/stop/`, {}))
export const getSecuritySources = (projectId, sourceProjectId) => data(api.get(`${base(projectId)}/sources/`, {
  params: sourceProjectId ? { source_project_id: sourceProjectId } : {},
}))
export async function downloadSecurityReport(projectId, id) {
  try {
    return await data(api.get(`${base(projectId)}/tasks/${id}/report.html`, { responseType: 'blob' }))
  } catch (error) {
    if (error.response?.data instanceof Blob) {
      try { error.response.data = JSON.parse(await error.response.data.text()) } catch { /* preserve HTTP failure */ }
    }
    throw error
  }
}
