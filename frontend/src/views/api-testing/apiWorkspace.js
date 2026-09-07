export const clone = (value) => JSON.parse(JSON.stringify(value ?? {}));

export const unwrap = (response) => response?.data ?? response ?? {};

export const listItems = (response) => {
  const body = unwrap(response);
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.items)) return body.items;
  if (Array.isArray(body?.results)) return body.results;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.data?.items)) return body.data.items;
  return Array.isArray(body?.data?.results) ? body.data.results : [];
};

const positiveQueryInteger = (value) => {
  if (value == null || value === "" || Array.isArray(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
};

export const workspaceInitializationPlan = (query = {}, workspaces = []) => {
  const hasWorkspaceId = query.workspace_id != null;
  if (hasWorkspaceId) {
    const workspaceId = positiveQueryInteger(query.workspace_id);
    return workspaceId
      ? { action: "load", workspaceId, explicit: true }
      : { action: "invalid", message: "workspace_id 必须是正整数。" };
  }

  const hasCaseId = query.case_id != null;
  const hasEndpointId = query.endpoint_id != null;
  if (hasCaseId || hasEndpointId) {
    const caseId = positiveQueryInteger(query.case_id);
    const endpointId = positiveQueryInteger(query.endpoint_id);
    if ((hasCaseId && !caseId) || (hasEndpointId && !endpointId)) {
      return { action: "invalid", message: "工作区入口参数必须是正整数。" };
    }
    return { action: "create", caseId, endpointId, explicit: true };
  }

  const workspaceId = workspaces[0]?.id;
  return workspaceId
    ? { action: "load", workspaceId, explicit: false }
    : { action: "create", caseId: null, endpointId: null, explicit: false };
};

export const savedCaseDescription = (workspace) =>
  typeof workspace?.saved_case_description === "string"
    ? workspace.saved_case_description
    : "";

export const updateWorkspaceListItem = (workspaces, workspace) =>
  Array.isArray(workspaces) && workspace?.id != null
    ? workspaces.map((item) =>
        String(item?.id) === String(workspace.id)
          ? { ...item, ...workspace }
          : item,
      )
    : workspaces;

export const shouldApplyWorkspaceReload = ({
  requestProjectId,
  currentProjectId,
  requestSequence,
  latestSequence,
  dirty,
  confirmedSnapshot = null,
  currentSnapshot = null,
}) => {
  if (
    requestProjectId !== currentProjectId ||
    requestSequence !== latestSequence
  )
    return false;
  return confirmedSnapshot != null
    ? confirmedSnapshot === currentSnapshot
    : !dirty;
};

export const shouldClearSubmittedMessage = (
  accepted,
  submittedMessage,
  currentMessage,
) => accepted === true && submittedMessage === currentMessage;

export const isAvailableChatModel = (model) =>
  model?.is_active === true && model?.model_type === "llm";

export const availableChatModels = (response) =>
  listItems(response).filter(isAvailableChatModel);

export const hasAvailableChatModel = (models, modelId) =>
  Array.isArray(models) &&
  models.some((model) => String(model.id) === String(modelId));

export const reconcileWorkspaceModel = (
  models,
  modelId,
  modelsLoaded,
  wasUnavailable = false,
) => {
  if (!modelsLoaded || modelId == null)
    return { modelId, unavailable: wasUnavailable };
  return hasAvailableChatModel(models, modelId)
    ? { modelId, unavailable: false }
    : { modelId: null, unavailable: true };
};

export const canGenerateWithModel = (
  models,
  modelId,
  modelsLoaded,
  modelsLoadFailed,
) =>
  modelsLoaded &&
  !modelsLoadFailed &&
  hasAvailableChatModel(models, modelId);

export const errorMessage = (error, fallback) =>
  error?.response?.data?.error?.message ||
  error?.response?.data?.message ||
  error?.message ||
  fallback;

export const isBusyWorkspace = (workspace) =>
  ["generating", "debugging"].includes(workspace?.status);

export const defaultDraft = () => ({
  version: 1,
  config: {
    name: "未命名 API 用例",
    base_url: "",
    variables: {},
    verify: true,
  },
  teststeps: [],
});

export const defaultStep = (index = 1) => ({
  name: `步骤 ${index}`,
  request: { method: "GET", url: "/", params: {}, headers: {} },
  extract: {},
  validate: [],
});

export const normalizeDraft = (value) => {
  const draft = clone(value);
  const fallback = defaultDraft();
  const config = { ...fallback.config, ...(draft.config || {}) };
  if (
    !config.variables ||
    typeof config.variables !== "object" ||
    Array.isArray(config.variables)
  )
    config.variables = {};
  const steps = Array.isArray(draft.teststeps)
    ? draft.teststeps
    : fallback.teststeps;
  return {
    version: Number.isInteger(draft.version) ? draft.version : 1,
    config,
    teststeps: steps.map((step, index) => normalizeStep(step, index + 1)),
  };
};

export const normalizeStep = (value, index) => {
  const step = clone(value);
  const request = { method: "GET", url: "/", ...(step.request || {}) };
  request.method = String(request.method || "GET").toUpperCase();
  request.url = String(request.url || "/");
  for (const key of ["headers", "params"]) {
    if (
      !request[key] ||
      typeof request[key] !== "object" ||
      Array.isArray(request[key])
    )
      request[key] = {};
  }
  return {
    ...step,
    name: String(step.name || `步骤 ${index}`),
    request,
    extract:
      step.extract &&
      typeof step.extract === "object" &&
      !Array.isArray(step.extract)
        ? step.extract
        : {},
    validate: Array.isArray(step.validate) ? step.validate : [],
  };
};

export const bodyKind = (request = {}) => {
  if (Object.hasOwn(request, "json")) return "json";
  if (Object.hasOwn(request, "data")) return "form";
  if (Object.hasOwn(request, "raw")) return "raw";
  return "none";
};

const preview = (value) => {
  if (value === undefined) return "未设置";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
};

const changed = (changes, label, before, after) => {
  if (JSON.stringify(before) !== JSON.stringify(after)) {
    changes.push(`${label}：${preview(before)} → ${preview(after)}`);
  }
};

export const candidateDiff = (draft, candidate) => {
  const current = normalizeDraft(draft);
  const proposed = normalizeDraft(candidate?.draft);
  const changes = [];
  changed(changes, "配置名称", current.config.name, proposed.config.name);
  changed(
    changes,
    "基础地址",
    current.config.base_url,
    proposed.config.base_url,
  );
  changed(
    changes,
    "配置变量",
    current.config.variables,
    proposed.config.variables,
  );
  changed(changes, "TLS 校验", current.config.verify, proposed.config.verify);
  if (current.teststeps.length !== proposed.teststeps.length)
    changes.push(
      `步骤数量：${current.teststeps.length} → ${proposed.teststeps.length}`,
    );
  current.teststeps.forEach((step, index) => {
    const next = proposed.teststeps[index];
    if (!next) return;
    const prefix = `步骤 ${index + 1}`;
    changed(changes, `${prefix} 名称`, step.name, next.name);
    changed(changes, `${prefix} 关联端点`, step.endpoint_id, next.endpoint_id);
    changed(
      changes,
      `${prefix} 方法`,
      step.request.method,
      next.request.method,
    );
    changed(changes, `${prefix} URL`, step.request.url, next.request.url);
    changed(
      changes,
      `${prefix} Query 参数`,
      step.request.params,
      next.request.params,
    );
    changed(
      changes,
      `${prefix} Headers`,
      step.request.headers,
      next.request.headers,
    );
    changed(
      changes,
      `${prefix} 请求体类型`,
      bodyKind(step.request),
      bodyKind(next.request),
    );
    changed(
      changes,
      `${prefix} JSON 请求体`,
      step.request.json,
      next.request.json,
    );
    changed(
      changes,
      `${prefix} 表单请求体`,
      step.request.data,
      next.request.data,
    );
    changed(
      changes,
      `${prefix} 原始请求体`,
      step.request.raw,
      next.request.raw,
    );
    changed(changes, `${prefix} 提取规则`, step.extract, next.extract);
    changed(changes, `${prefix} 断言`, step.validate, next.validate);
  });
  proposed.teststeps.slice(current.teststeps.length).forEach((step, index) => {
    changes.push(
      `新增步骤 ${current.teststeps.length + index + 1}：${preview(step)}`,
    );
  });
  current.teststeps.slice(proposed.teststeps.length).forEach((step, index) => {
    changes.push(
      `删除步骤 ${proposed.teststeps.length + index + 1}：${preview(step)}`,
    );
  });
  return changes.length ? changes : ["未检测到结构化差异"];
};

export const debugHasFailure = (result) => {
  if (!result || typeof result !== "object") return false;
  const status = String(
    result.status || result.result?.status || "",
  ).toLowerCase();
  if (["failed", "failure", "error"].includes(status)) return true;
  const steps =
    result.step_datas ||
    result.steps ||
    result.results ||
    result.debug_steps ||
    [];
  return (
    Array.isArray(steps) &&
    steps.some((step) =>
      ["failed", "failure", "error"].includes(
        String(step?.status || "").toLowerCase(),
      ),
    )
  );
};

export const statusMeta = (status) =>
  ({
    idle: { label: "空闲", type: "info" },
    generating: { label: "AI 生成中", type: "warning" },
    debugging: { label: "调试执行中", type: "warning" },
    ready: { label: "就绪", type: "success" },
    failed: { label: "任务失败", type: "danger" },
  })[status] || { label: status || "未知", type: "info" };
