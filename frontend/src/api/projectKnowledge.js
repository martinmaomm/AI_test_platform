import api from "./index";

const base = (projectId) => `/projects/${projectId}/knowledge`;

export const getProjectKnowledgeOptions = async (projectId) =>
  (await api.get(`${base(projectId)}/options/`)).data;

export const getProjectKnowledgeDocuments = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/documents/`, { params })).data;

export const createProjectKnowledgeDocument = async (projectId, formData) =>
  (
    await api.post(`${base(projectId)}/documents/`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    })
  ).data;

export const updateProjectKnowledgeDocument = async (
  projectId,
  documentId,
  payload,
) => {
  const config =
    payload instanceof FormData
      ? { headers: { "Content-Type": "multipart/form-data" } }
      : undefined;
  return (
    await api.patch(
      `${base(projectId)}/documents/${documentId}/`,
      payload,
      config,
    )
  ).data;
};

export const deleteProjectKnowledgeDocument = async (projectId, documentId) =>
  (await api.delete(`${base(projectId)}/documents/${documentId}/`)).data;

export const prepareProjectKnowledgeDocument = async (
  projectId,
  documentId,
  payload,
) =>
  (
    await api.post(
      `${base(projectId)}/documents/${documentId}/prepare/`,
      payload,
    )
  ).data;

export const getProjectKnowledgeSections = async (projectId, documentId) =>
  (await api.get(`${base(projectId)}/documents/${documentId}/sections/`)).data;

export const getProjectKnowledgeSource = async (projectId, chunkId) =>
  (await api.get(`${base(projectId)}/sources/${chunkId}/`)).data;

export const getProjectKnowledgeTasks = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/tasks/`, { params })).data;

export const getProjectKnowledgeTask = async (projectId, taskId) =>
  (await api.get(`${base(projectId)}/tasks/${taskId}/`)).data;

export const cancelProjectKnowledgeTask = async (projectId, taskId) =>
  (await api.post(`${base(projectId)}/tasks/${taskId}/cancel/`)).data;

export const retryProjectKnowledgeCleanupTask = async (projectId, taskId) =>
  (await api.post(`${base(projectId)}/tasks/${taskId}/retry-cleanup/`)).data;

export const createCaseGeneration = async (projectId, payload) =>
  (await api.post(`${base(projectId)}/case-generations/`, payload)).data;

export const saveGeneratedManualCases = async (
  projectId,
  generationId,
  payload,
) =>
  (
    await api.post(
      `${base(projectId)}/case-generations/${generationId}/save/`,
      payload,
    )
  ).data;

export const getManualCases = async (projectId, params = {}) =>
  (await api.get(`${base(projectId)}/manual-cases/`, { params })).data;

export const updateManualCase = async (projectId, caseId, payload) =>
  (await api.patch(`${base(projectId)}/manual-cases/${caseId}/`, payload)).data;

export const deleteManualCase = async (projectId, caseId) =>
  (await api.delete(`${base(projectId)}/manual-cases/${caseId}/`)).data;

export const exportManualCases = async (projectId, ids) =>
  (
    await api.post(
      `${base(projectId)}/manual-cases/export/`,
      { ids },
      { responseType: "blob" },
    )
  ).data;

export const getProjectKnowledgeConversations = async (
  projectId,
  params = {},
) => (await api.get(`${base(projectId)}/conversations/`, { params })).data;

export const createProjectKnowledgeConversation = async (projectId, payload) =>
  (await api.post(`${base(projectId)}/conversations/`, payload)).data;

export const getProjectKnowledgeMessages = async (projectId, conversationId) =>
  (
    await api.get(
      `${base(projectId)}/conversations/${conversationId}/messages/`,
    )
  ).data;

export const askProjectKnowledgeQuestion = async (
  projectId,
  conversationId,
  payload,
) =>
  (
    await api.post(
      `${base(projectId)}/conversations/${conversationId}/messages/`,
      payload,
    )
  ).data;
