import api from "./index";

const base = (projectId) => `/projects/${projectId}/performance/discovery`;
const get = async (url, config) => (await api.get(url, config)).data;
const post = async (url, data) => (await api.post(url, data)).data;
const remove = async (url) => (await api.delete(url)).data;

export const getPerformanceDiscoveryConfig = (projectId) =>
  get(`${base(projectId)}/config/`);
export const getPerformanceDiscoveryTasks = (projectId) =>
  get(`${base(projectId)}/tasks/`);
export const createPerformanceDiscoveryTask = (projectId, data) =>
  post(`${base(projectId)}/tasks/`, data);
export const getPerformanceDiscoveryTask = (projectId, id) =>
  get(`${base(projectId)}/tasks/${id}/`);
export const deletePerformanceDiscoveryTask = (projectId, id) =>
  remove(`${base(projectId)}/tasks/${id}/`);
export const getPerformanceDiscoveryRecords = (projectId, id, config) =>
  get(`${base(projectId)}/tasks/${id}/records/`, config);
export const cancelPerformanceDiscoveryTask = (projectId, id, data) =>
  post(`${base(projectId)}/tasks/${id}/cancel/`, data);
export const retryPerformanceDiscoveryTask = (projectId, id, data) =>
  post(`${base(projectId)}/tasks/${id}/retry/`, data);
export const resolvePerformanceDiscoveryOrigin = (projectId, id, data) =>
  post(`${base(projectId)}/tasks/${id}/origin/`, data);
export const createPerformanceDiscoveryDraft = (projectId, id, data) =>
  post(`${base(projectId)}/tasks/${id}/draft/`, data);
