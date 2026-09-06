import api from "./index";

const basePath = (projectId) =>
  `/projects/${projectId}/api-testing/workspaces/`;

export const listApiWorkspaces = async (projectId) => {
  const response = await api.get(basePath(projectId));
  return response.data;
};

export const createApiWorkspace = async (projectId, payload = {}) => {
  const response = await api.post(basePath(projectId), payload);
  return response.data;
};

export const getApiWorkspace = async (projectId, workspaceId) => {
  const response = await api.get(`${basePath(projectId)}${workspaceId}/`);
  return response.data;
};

export const updateApiWorkspace = async (projectId, workspaceId, payload) => {
  const response = await api.patch(
    `${basePath(projectId)}${workspaceId}/`,
    payload,
  );
  return response.data;
};

export const sendApiWorkspaceMessage = async (
  projectId,
  workspaceId,
  payload,
) => {
  const response = await api.post(
    `${basePath(projectId)}${workspaceId}/messages/`,
    payload,
  );
  return response.data;
};

export const debugApiWorkspace = async (projectId, workspaceId, payload) => {
  const response = await api.post(
    `${basePath(projectId)}${workspaceId}/debug/`,
    payload,
  );
  return response.data;
};

export const saveApiWorkspace = async (projectId, workspaceId, payload) => {
  const response = await api.post(
    `${basePath(projectId)}${workspaceId}/save/`,
    payload,
  );
  return response.data;
};

export const getApiWorkspacePython = async (projectId, workspaceId) => {
  const response = await api.get(
    `${basePath(projectId)}${workspaceId}/python/`,
  );
  return response.data;
};
