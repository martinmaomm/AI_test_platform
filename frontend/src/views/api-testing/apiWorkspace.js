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

export const completedApiSpecs = (response) =>
  listItems(response).filter((spec) => spec?.status === "completed");

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

export const rootWorkspaceBusy = (workspace) =>
  isBusyWorkspace(workspace) ||
  ["queued", "running"].includes(workspace?.generation?.status) ||
  ["planning", "scenarios"].includes(workspace?.generation?.phase) ||
  (Array.isArray(workspace?.scenarios) &&
    workspace.scenarios.some(
      (scenario) =>
        isBusyWorkspace(scenario) ||
        ["queued", "running"].includes(scenario?.generation?.status),
    ));

export const rootWorkspaceStatusMeta = (status) =>
  ({
    queued: { label: "已排队", type: "info" },
    running: { label: "场景执行中", type: "warning" },
    passed: { label: "已验证通过", type: "success" },
    partial: { label: "部分完成", type: "warning" },
    needs_review: { label: "需要人工处理", type: "warning" },
    failed: { label: "生成失败", type: "danger" },
  })[status] || statusMeta(status);

export const scenarioStatusMeta = (status) =>
  ({
    pending: { label: "待执行", type: "info" },
    queued: { label: "待执行", type: "info" },
    running: { label: "执行中", type: "warning" },
    generating: { label: "生成中", type: "warning" },
    passed: { label: "已验证", type: "success" },
    ready: { label: "待调试", type: "info" },
    stale: { label: "已修改待重验", type: "warning" },
    failed: { label: "失败", type: "danger" },
    needs_review: { label: "需人工处理", type: "warning" },
  })[status] || { label: status || "待规划", type: "info" };

export const rootGenerationStatusMeta = rootWorkspaceStatusMeta;

export const normalizeCoverage = (value) => {
  const coverage = value && typeof value === "object" ? value : {};
  const ids = (key) =>
    Array.isArray(coverage[key]) ? coverage[key] : [];
  return {
    total: Number(coverage.total) || 0,
    planned: Number(coverage.planned) || 0,
    generated: Number(coverage.generated) || 0,
    verified: Number(coverage.verified) || 0,
    uncovered_endpoint_ids: ids("uncovered_endpoint_ids"),
    planned_endpoint_ids: ids("planned_endpoint_ids"),
    generated_endpoint_ids: ids("generated_endpoint_ids"),
    verified_endpoint_ids: ids("verified_endpoint_ids"),
  };
};

export const currentScenarioState = (root) => {
  const scenarios = Array.isArray(root?.scenarios) ? root.scenarios : [];
  const scenarioIds = Array.isArray(root?.generation?.scenario_ids)
    ? new Set(root.generation.scenario_ids.map(String))
    : null;
  const current = scenarioIds
    ? scenarios.filter((scenario) => scenarioIds.has(String(scenario.id)))
    : scenarios;
  const statusOf = (scenario) =>
    scenario?.status === "stale"
      ? "stale"
      : scenario?.generation?.status || scenario?.status;
  if (current.some((scenario) => statusOf(scenario) === "stale"))
    return { label: "有场景已修改待重验", type: "warning", stale: true };
  if (current.some((scenario) => ["running", "queued", "generating"].includes(statusOf(scenario))))
    return { label: "场景仍在执行", type: "warning", stale: false };
  return null;
};

export const activeScenario = (root, scenarioId) => {
  const scenarios = Array.isArray(root?.scenarios) ? root.scenarios : [];
  const currentId = scenarioId ?? root?.generation?.active_scenario_id;
  const generatedIds = Array.isArray(root?.generation?.scenario_ids)
    ? new Set(root.generation.scenario_ids.map(String))
    : null;
  return (
    scenarios.find((scenario) => String(scenario.id) === String(currentId)) ||
    scenarios.find((scenario) => generatedIds?.has(String(scenario.id))) ||
    scenarios[0] ||
    null
  );
};

const endpointIdsFromDraft = (draft) =>
  Array.isArray(draft?.teststeps)
    ? draft.teststeps
        .map((step) => step?.endpoint_id)
        .filter((id) => Number.isSafeInteger(id) && id > 0)
    : [];

export const childEditorEndpointIds = (workspace) => {
  const targetIds = Array.isArray(workspace?.endpoint_ids)
    ? workspace.endpoint_ids
    : [];
  const context = workspace?.generation?.scenario_context;
  // Older child payloads did not freeze a root dependency scope. Keep their
  // original, target-only editor behavior rather than inventing new scope.
  if (!context || typeof context !== "object" || Array.isArray(context))
    return targetIds;
  return [
    ...new Set(
      [
        ...targetIds,
        ...(Array.isArray(context.target_endpoint_ids)
          ? context.target_endpoint_ids
          : []),
        ...(Array.isArray(context.available_endpoint_ids)
          ? context.available_endpoint_ids
          : []),
        ...(Array.isArray(context.dependency_endpoint_ids)
          ? context.dependency_endpoint_ids
          : []),
        ...endpointIdsFromDraft(workspace?.draft),
        ...endpointIdsFromDraft(workspace?.candidate?.draft),
      ].filter((id) => Number.isSafeInteger(id) && id > 0),
    ),
  ];
};

export const mergeScenario = (root, scenario) => {
  if (!root || !scenario?.id) return root;
  const scenarios = Array.isArray(root.scenarios) ? root.scenarios : [];
  return {
    ...root,
    scenarios: scenarios.map((item) =>
      String(item.id) === String(scenario.id) ? { ...item, ...scenario } : item,
    ),
  };
};

export const nextRootAfterDelete = (workspaces, deletedId) =>
  (Array.isArray(workspaces) ? workspaces : []).find(
    (workspace) => String(workspace?.id) !== String(deletedId),
  ) || null;

export const mergeRootWorkspaceMetadata = (root, update) => {
  if (!root || !update?.id || String(root.id) !== String(update.id)) return root;
  return {
    ...root,
    title: update.title ?? root.title,
    updated_at: update.updated_at ?? root.updated_at,
    revision: update.revision ?? root.revision,
  };
};

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

export const shouldClearRootGenerationPrompt = (
  accepted,
  submittedMessage,
  currentPrompt,
) =>
  accepted === true &&
  submittedMessage === String(currentPrompt ?? "").trim();

export const isHttpUrl = (value) => {
  try {
    const url = new URL(String(value || "").trim());
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.host);
  } catch {
    return false;
  }
};

export const generationContextMessage = ({
  specId,
  specAvailable = true,
  endpointIds,
  specsLoadFailed = false,
  endpointsLoadFailed = false,
}) => {
  if (specsLoadFailed) return "API 规范列表加载失败，不能生成并验证。";
  if (!specId) return "请选择 API 规范后再生成并验证。";
  if (!specAvailable) return "当前选择的 API 规范不可用，不能生成并验证。";
  if (endpointsLoadFailed) return "该 API 规范的接口加载失败，不能沿用旧范围生成。";
  if (!Array.isArray(endpointIds) || !endpointIds.length)
    return "请至少选择一个 API 接口后再生成并验证。";
  if (endpointIds.length > 50) return "一次最多选择 50 个 API 接口。";
  return "";
};

export const generationStatusMeta = (status) =>
  ({
    queued: { label: "已排队", type: "info" },
    running: { label: "验证中", type: "warning" },
    passed: { label: "已验证通过", type: "success" },
    needs_review: { label: "需要人工处理", type: "warning" },
    failed: { label: "验证失败", type: "danger" },
    stale: { label: "结果已过期", type: "info" },
  })[status] || { label: "尚未验证", type: "info" };

export const generationPhaseLabel = (phase) =>
  ({
    queued: "等待执行",
    generating: "正在生成候选",
    checking: "正在检查候选",
    running: "正在验证请求与断言",
    repairing: "正在修复并复验",
    finished: "流程已结束",
  })[phase] || "等待状态更新";

export const isGenerationStale = (generation, workspaceRevision, dirty) =>
  Boolean(
    dirty ||
      generation?.status === "stale" ||
      ((generation?.adopted_revision ?? generation?.source_revision) != null &&
        (generation?.adopted_revision ?? generation?.source_revision) !==
          workspaceRevision),
  );

const hasGeneratedSteps = (draft) =>
  Array.isArray(draft?.teststeps) && draft.teststeps.length > 0;

export const generationDraftSummary = (draft) => {
  const errors = [];
  if (!draft || typeof draft !== "object" || Array.isArray(draft)) {
    return {
      name: "候选场景尚未生成",
      steps: [],
      errors: ["候选草稿不是对象，无法解析为可编辑场景。"],
    };
  }
  const config = draft.config;
  if (config != null && (typeof config !== "object" || Array.isArray(config)))
    errors.push("候选 config 不是对象。");
  if (!Array.isArray(draft.teststeps)) {
    errors.push("候选 teststeps 不是数组。");
    return {
      name:
        config && typeof config === "object"
          ? config.name || "未命名候选场景"
          : "未命名候选场景",
      steps: [],
      errors,
    };
  }
  const steps = [];
  draft.teststeps.forEach((step, index) => {
    if (!step || typeof step !== "object" || Array.isArray(step)) {
      errors.push(`步骤 ${index + 1} 不是对象。`);
      return;
    }
    const request =
      step.request && typeof step.request === "object" && !Array.isArray(step.request)
        ? step.request
        : {};
    if (step.request != null && request !== step.request)
      errors.push(`步骤 ${index + 1} 的 request 不是对象。`);
    steps.push({
      name:
        typeof step.name === "string" && step.name.trim()
          ? step.name
          : `步骤 ${index + 1}`,
      method: String(request.method || "GET").toUpperCase(),
      url: typeof request.url === "string" ? request.url : "/",
    });
  });
  return {
    name:
      config && typeof config === "object"
        ? config.name || "未命名候选场景"
        : "未命名候选场景",
    steps,
    errors,
  };
};

export const latestGenerationDraft = (generation, candidate) => {
  const rounds = Array.isArray(generation?.rounds) ? generation.rounds : [];
  for (let index = rounds.length - 1; index >= 0; index -= 1) {
    if (hasGeneratedSteps(rounds[index]?.draft)) return rounds[index].draft;
  }
  return hasGeneratedSteps(candidate?.draft) ? candidate.draft : null;
};

export const generationRepairDefaults = ({
  generation,
  candidate,
  draft,
  useGenerationEvidence = false,
} = {}) => {
  const candidateConfig = candidate?.draft?.config || {};
  const draftConfig = draft?.config || {};
  return {
    base_url: useGenerationEvidence
      ? generation?.target_url || candidateConfig.base_url || draftConfig.base_url || ""
      : draftConfig.base_url || "",
    variables:
      useGenerationEvidence &&
      candidateConfig.variables &&
      typeof candidateConfig.variables === "object" &&
      !Array.isArray(candidateConfig.variables)
        ? clone(candidateConfig.variables)
        : draftConfig.variables &&
            typeof draftConfig.variables === "object" &&
            !Array.isArray(draftConfig.variables)
          ? clone(draftConfig.variables)
          : {},
  };
};

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

const addedStepSummary = (step) => {
  const assertions = Array.isArray(step?.validate) ? step.validate.length : 0;
  const extractions =
    step?.extract && typeof step.extract === "object"
      ? Object.keys(step.extract).length
      : 0;
  return `${step?.name || "未命名步骤"}（${String(
    step?.request?.method || "GET",
  ).toUpperCase()} ${step?.request?.url || "/"}；断言 ${assertions} 条，提取 ${extractions} 项）`;
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
      `新增步骤 ${current.teststeps.length + index + 1}：${addedStepSummary(step)}`,
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
  if (result.success === false || result.result?.success === false) return true;
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

export const hasCurrentGenerationFailure = (generation, workspaceRevision) =>
  ["failed", "needs_review"].includes(generation?.status) &&
  !isGenerationStale(generation, workspaceRevision, false) &&
  Array.isArray(generation?.rounds) &&
  generation.rounds.some((round) =>
    hasGeneratedSteps(round?.draft) && debugHasFailure(round?.result),
  );

export const hasReviewableGenerationRound = (round) => {
  if (!round || typeof round !== "object") return false;
  if (debugHasFailure(round.result)) return true;
  if (["failed", "failure", "error", "needs_review"].includes(round.status))
    return true;
  if (round.error || round.static_errors?.length) return true;
  return !round.result && Boolean(round.summary);
};

export const canRepairWorkspace = ({
  dirty,
  generation,
  workspaceRevision,
  debugResult,
  debugRevision,
} = {}) => {
  if (dirty) return false;
  const currentDebugFailure =
    debugRevision != null &&
    debugRevision === workspaceRevision &&
    debugHasFailure(debugResult);
  const currentGenerationFailure = hasCurrentGenerationFailure(generation, workspaceRevision);
  return currentDebugFailure || currentGenerationFailure;
};

export const failureActionState = ({
  editingScenario,
  generation,
  workspaceRevision,
  debugResult,
  debugRevision,
  dirty = false,
  busy = false,
  conflict = false,
  candidate,
  draft,
  modelAvailable = false,
  modelDirty = false,
  canRepair = false,
} = {}) => {
  const status = generation?.status;
  const hasCurrentDebugEvidence =
    debugRevision != null &&
    workspaceRevision != null &&
    debugRevision === workspaceRevision &&
    debugHasFailure(debugResult);
  const visible = Boolean(
    editingScenario &&
      (["failed", "needs_review"].includes(status) || hasCurrentDebugEvidence),
  );
  const rounds = Array.isArray(generation?.rounds) ? generation.rounds : [];
  const hasRoundEvidence = rounds.some(hasReviewableGenerationRound);
  const hasEvidence = hasRoundEvidence || hasCurrentDebugEvidence;
  const candidateCurrent =
    candidate?.draft &&
    candidate?.source_revision === workspaceRevision &&
    !isGenerationStale(generation, workspaceRevision, false);
  const hasEditableDraft =
    Array.isArray(draft?.teststeps) && draft.teststeps.length > 0;
  const busyReason = "当前场景仍在处理，完成后再操作。";

  const viewReason = busy
    ? busyReason
    : hasEvidence
      ? ""
      : "没有可展开的实际执行失败证据；当前仅有静态失败信息。";
  const repairReason = busy
    ? busyReason
    : conflict
      ? "草稿版本已冲突，请重新加载后再修复。"
      : dirty
        ? "当前草稿已修改，请先保存或处理本地编辑后再修复。"
        : modelDirty
          ? "子场景模型尚未保存，请保存后再修复。"
          : !modelAvailable
            ? "当前子场景没有可用聊天模型，无法发起 AI 修复。"
            : !canRepair
              ? "没有当前版本的实际执行失败证据；静态失败请手动编辑或重新生成。"
              : "";
  const usesCandidate = Boolean(candidateCurrent && !dirty);
  const manualReason = busy
    ? busyReason
    : conflict
      ? "草稿版本已冲突，请重新加载后再手动编辑。"
    : !usesCandidate && !hasEditableDraft
        ? candidate?.draft
          ? "候选已过期且当前草稿没有步骤，请重新生成后再编辑。"
          : "当前没有候选且草稿没有步骤，请重新生成后再编辑。"
        : "";
  const candidateNote = candidate?.draft
    ? candidateCurrent && !dirty
      ? "将先确认采用候选；不会保存正式测试用例。"
      : "候选已过期或本地草稿已修改；将直接打开当前草稿，不会覆盖本地编辑。"
    : hasEditableDraft
      ? "没有可采用候选；将直接打开当前草稿。"
      : "当前没有候选且草稿没有步骤，请重新生成。";

  return {
    visible,
    view: {
      disabled: Boolean(viewReason),
      reason: viewReason,
      hasRoundEvidence,
      hasCurrentDebugEvidence,
    },
    repair: { disabled: Boolean(repairReason), reason: repairReason },
    manual: {
      disabled: Boolean(manualReason),
      reason: manualReason,
      usesCandidate,
      note: candidateNote,
    },
  };
};

export const statusMeta = (status) =>
  ({
    idle: { label: "空闲", type: "info" },
    generating: { label: "AI 生成中", type: "warning" },
    debugging: { label: "调试执行中", type: "warning" },
    ready: { label: "就绪", type: "success" },
    failed: { label: "任务失败", type: "danger" },
  })[status] || { label: status || "未知", type: "info" };
