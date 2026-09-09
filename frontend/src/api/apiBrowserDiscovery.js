import api from "./index";

const basePath = (projectId) =>
  `/projects/${projectId}/api-testing/browser-discoveries/`;

export const getBrowserDiscoveryConfig = async (projectId) => {
  const response = await api.get(`${basePath(projectId)}config/`);
  return response.data;
};

export const listBrowserDiscoveries = async (projectId) => {
  const response = await api.get(basePath(projectId));
  return response.data;
};

export const createBrowserDiscovery = async (projectId, payload) => {
  const response = await api.post(basePath(projectId), payload);
  return response.data;
};

export const getBrowserDiscovery = async (projectId, taskId) => {
  const response = await api.get(`${basePath(projectId)}${taskId}/`);
  return response.data;
};

export const getBrowserDiscoveryRecords = async (
  projectId,
  taskId,
  params = {},
) => {
  const response = await api.get(`${basePath(projectId)}${taskId}/records/`, {
    params,
  });
  return response.data;
};

export const cancelBrowserDiscovery = async (projectId, taskId) => {
  const response = await api.post(`${basePath(projectId)}${taskId}/cancel/`);
  return response.data;
};

export const resolveBrowserDiscoveryOrigin = async (
  projectId,
  taskId,
  payload,
) => {
  const response = await api.post(
    `${basePath(projectId)}${taskId}/origins/`,
    payload,
  );
  return response.data;
};

export const handoffBrowserDiscovery = async (projectId, taskId, payload) => {
  const response = await api.post(
    `${basePath(projectId)}${taskId}/handoff/`,
    payload,
  );
  return response.data;
};
