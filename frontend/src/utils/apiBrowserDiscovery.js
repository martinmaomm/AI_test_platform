const unwrap = (response) => response?.data ?? response ?? {};

export const BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS = 60;
export const BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH = 10000;

export const browserDiscoveryData = (response) => {
  const body = unwrap(response);
  return body?.data ?? body;
};

export const browserDiscoveryItems = (response) => {
  const body = browserDiscoveryData(response);
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.items)) return body.items;
  if (Array.isArray(body?.results)) return body.results;
  return [];
};

export const browserDiscoveryConfig = (response) => {
  const value = browserDiscoveryData(response);
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : { enabled: false, limits: {}, models: [] };
};

export const browserDiscoveryModels = (config) =>
  Array.isArray(config?.models) ? config.models.filter((model) => model?.id != null) : [];

export const browserDiscoveryTimeoutDefault = (config) => {
  const value = Number(config?.limits?.timeout_seconds);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
};

export const isHttpUrl = (value) => {
  try {
    const url = new URL(String(value || "").trim());
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.host);
  } catch {
    return false;
  }
};

export const isApiOrigin = (value) => {
  if (!String(value || "").trim()) return true;
  try {
    const url = new URL(String(value).trim());
    return (
      ["http:", "https:"].includes(url.protocol) &&
      Boolean(url.host) &&
      !url.username &&
      !url.password &&
      url.pathname === "/" &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
};

export const buildBrowserDiscoveryPayload = (form, config) => {
  const targetUrl = String(form?.target_url || "").trim();
  const description = String(form?.description || "").trim();
  const apiOrigin = String(form?.api_origin || "").trim();
  const timeout = Number(form?.exploration_timeout_seconds);
  const modelId = Number(form?.model_id);
  if (!isHttpUrl(targetUrl)) throw new Error("页面 URL 必须是完整的 HTTP(S) 地址。");
  if (!description) throw new Error("请说明本次网页探索的目标。");
  if (description.length > BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH)
    throw new Error(`探索目标说明不能超过 ${BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH} 个字符。`);
  if (!isApiOrigin(apiOrigin))
    throw new Error("API origin 只能填写协议、主机和可选端口，不能包含路径或参数。");
  if (!Number.isSafeInteger(modelId) || modelId <= 0)
    throw new Error("请选择可用的 LLM 模型。");
  if (form?.allow_test_data_writes !== true)
    throw new Error("请明确确认仅在授权测试范围内允许修改测试数据。");
  const timeoutMaximum = Number(config?.limits?.timeout_seconds);
  if (!Number.isSafeInteger(timeout) || timeout < BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS)
    throw new Error(`探索总时限不能少于 ${BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS} 秒。`);
  if (Number.isSafeInteger(timeoutMaximum) && timeoutMaximum >= BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS && timeout > timeoutMaximum)
    throw new Error(`探索总时限不能超过当前配置的 ${timeoutMaximum} 秒。`);
  return {
    target_url: targetUrl,
    description,
    api_origin: apiOrigin || null,
    auto_approve_origins: form?.auto_approve_origins !== false,
    model_id: modelId,
    allow_test_data_writes: true,
    exploration_timeout_seconds: timeout,
  };
};

export const browserDiscoveryFormSnapshot = (form) =>
  JSON.stringify({
    target_url: String(form?.target_url || "").trim(),
    description: String(form?.description || "").trim(),
    api_origin: String(form?.api_origin || "").trim(),
    auto_approve_origins: form?.auto_approve_origins !== false,
    model_id: form?.model_id ?? null,
    allow_test_data_writes: form?.allow_test_data_writes === true,
    exploration_timeout_seconds: form?.exploration_timeout_seconds ?? null,
  });

export const isBrowserDiscoveryActive = (task) =>
  ["queued", "running", "finalizing"].includes(task?.status);

const browserDiscoveryTerminalStatuses = new Set([
  "completed",
  "partial",
  "failed",
  "cancelled",
]);

export const browserDiscoveryDeleteState = (task) => {
  const reason = String(task?.delete_block_reason || "").trim();
  if (!browserDiscoveryTerminalStatuses.has(task?.status))
    return {
      canDelete: false,
      reason: reason || "任务仍在执行或状态未知；请先取消并等待停止后再删除。",
    };
  if (task?.can_delete === true) return { canDelete: true, reason: "" };
  return {
    canDelete: false,
    reason: reason || "任务删除状态已过期，请刷新后重试。",
  };
};

export const browserDiscoveryBecameTerminal = (previousTask, nextTask) => {
  if (!isBrowserDiscoveryActive(previousTask)) return false;
  if (
    previousTask?.id != null &&
    nextTask?.id != null &&
    String(previousTask.id) !== String(nextTask.id)
  )
    return false;
  return ["completed", "partial", "failed", "cancelled"].includes(
    nextTask?.status,
  );
};

const originResolutionStates = new Set([
  "detecting",
  "resolved",
  "awaiting_confirmation",
  "awaiting_selection",
]);

const uniqueOrigins = (values) => [
  ...new Set(
    (Array.isArray(values) ? values : [])
      .map((value) => String(value || "").trim())
      .filter(Boolean),
  ),
];

export const browserDiscoveryOriginResolution = (task) => {
  const raw = task?.origin_resolution;
  const hasProtocol = raw && typeof raw === "object" && !Array.isArray(raw);
  const mode = raw?.mode === "manual" ? "manual" : "auto";
  const origins = uniqueOrigins(raw?.origins);
  const pending = (Array.isArray(raw?.pending) ? raw.pending : [])
    .map((candidate) => ({
      origin: String(candidate?.origin || "").trim(),
      method: String(candidate?.method || "").trim().toUpperCase(),
      path: String(candidate?.path || "").trim(),
    }))
    .filter((candidate) => candidate.origin && candidate.method && candidate.path);
  const selectedOrigin = String(raw?.selected_origin || "").trim() || null;
  const state = originResolutionStates.has(raw?.state)
    ? raw.state
    : mode === "manual" || origins.length
      ? "resolved"
      : "detecting";
  return {
    hasProtocol,
    mode,
    state,
    origins,
    pending,
    selectedOrigin,
    canConfirm: raw?.can_confirm === true,
  };
};

export const browserDiscoveryOriginStateLabel = (task) => {
  const resolution = browserDiscoveryOriginResolution(task);
  const terminal = ["completed", "partial", "failed", "cancelled"].includes(
    task?.status,
  );
  return (
    {
      detecting: terminal
        ? resolution.origins.length
          ? "未确认接口来源"
          : "未发现接口来源"
        : "正在自动识别接口来源",
      resolved: resolution.mode === "manual" ? "使用手动接口来源" : "接口来源已识别",
      awaiting_confirmation: "等待确认跨主机请求",
      awaiting_selection: "已采集多个来源，等待选择主来源",
    }[resolution.state] || "来源状态未知"
  );
};

export const browserDiscoveryOriginSummary = (task) => {
  const resolution = browserDiscoveryOriginResolution(task);
  const terminal = ["completed", "partial", "failed", "cancelled"].includes(
    task?.status,
  );
  if (resolution.mode === "manual") return task?.api_origin || "手动接口来源";
  if (resolution.state === "awaiting_confirmation")
    return `待确认：${resolution.pending[0]?.origin || "跨主机接口来源"}`;
  if (resolution.state === "awaiting_selection")
    return `待选择：${resolution.origins.length} 个来源`;
  if (resolution.selectedOrigin) return `已识别：${resolution.selectedOrigin}`;
  if (terminal && resolution.state === "detecting")
    return resolution.origins.length ? "未确认接口来源" : "未发现接口来源";
  if (resolution.origins.length === 1) return `已识别：${resolution.origins[0]}`;
  if (resolution.origins.length > 1) return `已识别：${resolution.origins.length} 个来源`;
  if (terminal) return "未发现接口来源";
  return "正在自动识别";
};

export const browserDiscoveryEvidenceCounts = (task) => {
  const summary = task?.evidence_summary && typeof task.evidence_summary === "object"
    ? task.evidence_summary
    : {};
  const count = (value) => {
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 0 ? number : 0;
  };
  return {
    collected: count(summary.records ?? task?.records_count),
    usable: count(summary.usable ?? task?.eligible_records_count),
  };
};

const browserDiscoveryDisplayId = (value) => {
  const id = String(value ?? "").trim();
  if (!id) return "未知";
  return id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
};

const browserDiscoveryWorkspaceId = (value) => {
  if (typeof value === "number")
    return Number.isSafeInteger(value) && value > 0 ? String(value) : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!/^[1-9]\d*$/.test(trimmed)) return null;
  const numeric = Number(trimmed);
  return Number.isSafeInteger(numeric) ? String(numeric) : null;
};

export const browserDiscoveryTaskLabel = (task) => {
  const suffix = `任务 #${browserDiscoveryDisplayId(task?.id)}`;
  try {
    const url = new URL(String(task?.target_url || "").trim());
    if (!["http:", "https:"].includes(url.protocol) || !url.host) return suffix;
    return `${url.origin}${url.pathname || "/"} · #${browserDiscoveryDisplayId(task?.id)}`;
  } catch {
    return suffix;
  }
};

export const formatBrowserDiscoveryTimestamp = (value) => {
  if (value == null || String(value).trim() === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

export const browserDiscoveryHandoffs = (task) => {
  const handoffs = Array.isArray(task?.handoffs) ? task.handoffs : [];
  const seenWorkspaceIds = new Set();
  return handoffs.reduce((items, handoff) => {
    const workspaceId = browserDiscoveryWorkspaceId(handoff?.workspace_id);
    if (!workspaceId || seenWorkspaceIds.has(workspaceId)) return items;
    seenWorkspaceIds.add(workspaceId);
    items.push({
      workspaceId,
      workspaceTitle: String(handoff?.workspace_title ?? "").trim() || `生成结果 #${browserDiscoveryDisplayId(workspaceId)}`,
      createdAt: handoff?.created_at ?? null,
      sourceVersion: handoff?.source_version ?? null,
    });
    return items;
  }, []);
};

export const browserDiscoveryErrorCategory = (task) => {
  const resolution = browserDiscoveryOriginResolution(task);
  if (resolution.state === "awaiting_confirmation") return "等待来源确认";
  if (resolution.state === "awaiting_selection") return "多来源待选";
  const code = String(task?.error_code || "").trim();
  if (!code) return "无";
  if (code.startsWith("MODEL_")) return browserDiscoveryErrorCodeLabel(code);
  if (["no_records", "no_usable_records"].includes(code)) return "未采集到有效接口证据";
  if (code === "capture_incomplete") return "证据采集不完整";
  if (["TOTAL_TIMEOUT", "timeout"].includes(code)) return "探索超时";
  if (code === "ORIGIN_CONFIRMATION_TIMEOUT") return "来源确认等待超时";
  if (["CANCELLED", "cancelled"].includes(code)) return "已取消";
  return `任务异常：${code}`;
};

export const canConfirmBrowserDiscoveryOrigin = (task) => {
  const resolution = browserDiscoveryOriginResolution(task);
  return (
    isBrowserDiscoveryActive(task) &&
    resolution.state === "awaiting_confirmation" &&
    resolution.canConfirm &&
    resolution.pending.length > 0
  );
};

export const canSelectBrowserDiscoveryOrigin = (task, origin) => {
  const resolution = browserDiscoveryOriginResolution(task);
  return (
    ["completed", "partial"].includes(task?.status) &&
    resolution.state === "awaiting_selection" &&
    resolution.origins.includes(String(origin || "").trim())
  );
};

export const canHandoffBrowserDiscovery = (task) => {
  if (!["completed", "partial"].includes(task?.status)) return false;
  const resolution = browserDiscoveryOriginResolution(task);
  if (!resolution.hasProtocol || resolution.mode === "manual") return true;
  return resolution.state === "resolved" && Boolean(resolution.selectedOrigin);
};

export const browserDiscoveryStatusMeta = (status) =>
  ({
    queued: { label: "已排队", type: "info" },
    running: { label: "探索中", type: "warning" },
    finalizing: { label: "正在整理证据", type: "warning" },
    completed: { label: "已完成", type: "success" },
    partial: { label: "部分完成", type: "warning" },
    failed: { label: "失败", type: "danger" },
    cancelled: { label: "已取消", type: "info" },
  })[status] || { label: status || "未知", type: "info" };

export const browserDiscoveryErrorCodeLabel = (code) => {
  const value = String(code || "").trim();
  const labels = {
    MODEL_OVERLOADED: "模型服务过载，请稍后重试或切换模型。",
    MODEL_RATE_LIMITED: "模型服务限流，请稍后重试。",
    MODEL_TIMEOUT: "模型请求超时，请稍后重试。",
    MODEL_UNAVAILABLE: "模型服务暂时不可用，请稍后重试。",
    MODEL_AUTH_ERROR: "模型认证或访问权限异常，请检查模型配置。",
    MODEL_CONFIG_ERROR: "模型名称或接口配置异常，请检查模型配置。",
    MODEL_QUOTA_EXHAUSTED: "模型服务额度不足，请检查账户额度后再试。",
    MCP_OTHER: "探索发生未分类异常，请向管理员提供任务 ID 核查后台日志。",
    CANCELLED: "用户已取消探索；已完成的网站操作不会撤销。",
    TOTAL_TIMEOUT: "网页探索达到本轮总时限，已停止后续操作。",
    STALE_TASK: "任务已取消或过期，已停止后续操作。",
    MCP_UNSUPPORTED: "当前 MCP 缺少网页采集所需浏览器工具。",
    TOOL_FAILURE: "连续页面操作失败，已停止探索。",
    TOOL_NOT_ALLOWED: "模型调用了本轮不允许的工具，已停止探索。",
    TOOL_BUDGET: "浏览器工具调用达到本轮上限，已保留已采集证据。",
    REPEATED_OPERATION: "相同操作反复执行且页面没有新证据，已停止探索。",
    TARGET_OUT_OF_SCOPE: "模型尝试打开授权范围以外的网址，已停止探索。",
    cancelled: "用户已取消探索；已完成的网站操作不会撤销。",
    timeout: "网页探索达到本轮总时限。",
    runner_failed: "浏览器探索执行失败，请查看任务诊断。",
    capture_incomplete: "浏览器证据采集不完整，不能作为完整接口契约。",
    no_records: "未观察到可保存的网络证据。",
    heartbeat_expired: "探索 worker 心跳已过期，已停止继续等待。",
  };
  if (labels[value]) return labels[value];
  if (value.startsWith("MCP_")) return "网页探索服务异常，已保留已采集证据。";
  return value ? `任务技术代码：${value}` : "";
};

export const browserDiscoveryElapsed = (task, now = Date.now()) => {
  const seconds = Number(task?.elapsed_seconds);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.floor(seconds);
  const startedAt = Date.parse(task?.started_at || task?.created_at || "");
  const finishedAt = Date.parse(task?.finished_at || task?.updated_at || "");
  if (!Number.isFinite(startedAt)) return null;
  return Math.max(0, Math.floor(((isBrowserDiscoveryActive(task) ? now : finishedAt) - startedAt) / 1000));
};

export const formatBrowserDiscoveryDuration = (seconds) => {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
};

export const shouldApplyBrowserDiscoveryResponse = ({
  requestProjectId,
  currentProjectId,
  requestEpoch,
  currentEpoch,
  requestSequence,
  latestSequence,
  expectedTaskId = null,
  currentTaskId = null,
}) =>
  requestProjectId === currentProjectId &&
  requestEpoch === currentEpoch &&
  requestSequence === latestSequence &&
  (expectedTaskId == null || String(expectedTaskId) === String(currentTaskId));

export const browserDiscoveryRecordLabel = (record) => {
  const method = String(record?.method || "?").toUpperCase();
  const path = record?.exact_path || record?.path || record?.url || "—";
  return `${method} ${path}`;
};

export const browserDiscoveryRecordIds = (records) =>
  (Array.isArray(records) ? records : [])
    .map((record) => Number(typeof record === "object" ? record?.id : record))
    .filter((id) => Number.isSafeInteger(id) && id > 0);

const browserDiscoveryPublicSummary = (record) =>
  record?.public_summary && typeof record.public_summary === "object" && !Array.isArray(record.public_summary)
    ? record.public_summary
    : null;

const browserDiscoveryNormalizeForComparison = (value) => {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(browserDiscoveryNormalizeForComparison);
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, browserDiscoveryNormalizeForComparison(value[key])]),
  );
};

const browserDiscoveryComparableSummary = (record) => {
  const summary = browserDiscoveryPublicSummary(record);
  if (!summary) return null;

  const requiredTopKeys = [
    "origin",
    "method",
    "path",
    "status_code",
    "content_type",
    "resource_type",
  ];
  const topValues = {
    origin: String(record?.origin ?? "").trim(),
    method: String(record?.method ?? "").trim().toUpperCase(),
    path: String(record?.path ?? "").trim(),
    status_code: record?.status_code,
    content_type: record?.content_type,
    resource_type: record?.resource_type,
  };
  for (const key of requiredTopKeys) {
    if (!Object.prototype.hasOwnProperty.call(record ?? {}, key)) return null;
    if (topValues[key] === null || topValues[key] === undefined) return null;
  }

  if (
    summary.capture_complete !== true ||
    summary.source_authorized !== true ||
    !Object.prototype.hasOwnProperty.call(summary, "capture_reason") ||
    !summary.observed_request ||
    !summary.observed_response
  ) return null;

  if (
    typeof summary.observed_request !== "object" ||
    Array.isArray(summary.observed_request) ||
    typeof summary.observed_response !== "object" ||
    Array.isArray(summary.observed_response)
  ) return null;

  if (
    typeof record?.is_eligible !== "boolean" ||
    typeof record?.exclusion_reason === "undefined"
  ) return null;

  const values = {
    ...topValues,
    is_eligible: record.is_eligible,
    exclusion_reason: record.exclusion_reason,
    capture_complete: true,
    capture_reason: summary.capture_reason,
    source_authorized: true,
    observed_request: summary.observed_request,
    observed_response: summary.observed_response,
  };
  return JSON.stringify(browserDiscoveryNormalizeForComparison(values));
};

const browserDiscoveryMergeSafeId = (value) => {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
};

export const browserDiscoveryExpandSelectedRecordIds = (selectedRepresentativeIds, groups) => {
  const selected = new Set(
    (Array.isArray(selectedRepresentativeIds) ? selectedRepresentativeIds : []).map((value) => String(value)),
  );
  const expanded = new Set();
  for (const group of Array.isArray(groups) ? groups : []) {
    for (const sample of Array.isArray(group?.records) ? group.records : []) {
      if (!selected.has(String(sample?.representativeId))) continue;
      for (const id of browserDiscoveryRecordIds(sample?.eligibleRecordIds)) expanded.add(id);
    }
  }
  return [...expanded];
};

export const browserDiscoveryRecordGroups = (records) => {
  const groups = new Map();
  const seenIds = new Set();
  // The earliest stored ID remains the representative across refreshes/pages.
  // Keep a copy so sorting never mutates the API response held by the parent.
  const normalizedRecords = (Array.isArray(records) ? [...records] : []).sort(
    (left, right) =>
      (browserDiscoveryMergeSafeId(left?.id) ?? Number.MAX_SAFE_INTEGER) -
      (browserDiscoveryMergeSafeId(right?.id) ?? Number.MAX_SAFE_INTEGER),
  );

  for (const record of normalizedRecords) {
    const id = browserDiscoveryMergeSafeId(record?.id);
    if (id != null) {
      if (seenIds.has(id)) continue;
      seenIds.add(id);
    }

    const method = String(record?.method || "?").toUpperCase();
    const path = record?.path || "—";
    const origin = record?.origin || "—";
    const statusCode = record?.status_code ?? "—";
    const key = [method, path].join("\u0000");
    const group = groups.get(key) || {
      key,
      method,
      path,
      origins: new Set(),
      statusCodes: new Set(),
      count: 0,
      recordIds: [],
      eligibleRecordIds: [],
      exclusionReasons: new Set(),
      dependencyRecordIds: new Set(),
      records: [],
      sampleSignatures: new Map(),
      sampleCount: 0,
    };
    group.origins.add(origin);
    group.statusCodes.add(statusCode);
    group.count += 1;
    if (Number.isSafeInteger(id) && id > 0) {
      group.recordIds.push(id);
      if (record?.is_eligible === true && !group.eligibleRecordIds.includes(id))
        group.eligibleRecordIds.push(id);
    }
    if (record?.exclusion_reason) group.exclusionReasons.add(record.exclusion_reason);
    for (const dependencyId of browserDiscoveryRecordIds(record?.dependency_record_ids))
      group.dependencyRecordIds.add(dependencyId);

    const summarySignature = browserDiscoveryComparableSummary(record);
    const existing = summarySignature == null ? null : group.sampleSignatures.get(summarySignature);
    if (existing) {
      if (id != null && !existing.sourceRecordIds.includes(id)) existing.sourceRecordIds.push(id);
      if (record?.sequence != null && !existing.sourceSequenceNumbers.includes(record.sequence))
        existing.sourceSequenceNumbers.push(record.sequence);
      if (record?.is_eligible === true && id != null && !existing.eligibleRecordIds.includes(id))
        existing.eligibleRecordIds.push(id);
      existing.duplicateCount += 1;
      continue;
    }

    const representativeId = id ?? `rep-${key}-${group.sampleCount}-${group.records.length}`;
    const sample = {
      ...record,
      representativeId,
      sourceRecordIds: id == null ? [] : [id],
      sourceSequenceNumbers: record?.sequence == null ? [] : [record.sequence],
      duplicateCount: 1,
      eligibleRecordIds: id != null && record?.is_eligible === true ? [id] : [],
    };
    if (summarySignature != null) group.sampleSignatures.set(summarySignature, sample);
    group.records.push(sample);
    group.sampleCount += 1;
    groups.set(key, group);
  }

  return [...groups.values()].map((group) => {
    const { sampleSignatures, ...publicGroup } = group;
    return {
      ...publicGroup,
      origins: [...group.origins],
      statusCodes: [...group.statusCodes],
      exclusionReasons: [...group.exclusionReasons],
      dependencyRecordIds: [...group.dependencyRecordIds],
      eligibleRepresentativeIds: group.records
        .filter((sample) => sample.eligibleRecordIds.length > 0)
        .map((sample) => sample.representativeId),
      mergedCount: group.count - group.sampleCount,
    };
  });
};
