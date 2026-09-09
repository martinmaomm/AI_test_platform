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
    model_id: modelId,
    allow_test_data_writes: true,
    exploration_timeout_seconds: timeout,
  };
};

export const isBrowserDiscoveryActive = (task) =>
  ["queued", "running", "finalizing"].includes(task?.status);

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
    CANCELLED: "用户已取消探索；已完成的网站操作不会撤销。",
    TOTAL_TIMEOUT: "网页探索达到本轮总时限，已停止后续操作。",
    STALE_TASK: "任务已取消或过期，已停止后续操作。",
    MCP_UNSUPPORTED: "当前 MCP 缺少网页采集所需浏览器工具。",
    TOOL_FAILURE: "连续页面操作失败，已停止探索。",
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

export const browserDiscoveryRecordGroups = (records) => {
  const groups = new Map();
  for (const record of Array.isArray(records) ? records : []) {
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
      samples: [],
      records: [],
    };
    group.origins.add(origin);
    group.statusCodes.add(statusCode);
    const id = Number(record?.id);
    group.count += 1;
    if (Number.isSafeInteger(id) && id > 0) {
      group.recordIds.push(id);
      if (record?.is_eligible === true) group.eligibleRecordIds.push(id);
    }
    if (record?.exclusion_reason) group.exclusionReasons.add(record.exclusion_reason);
    for (const dependencyId of browserDiscoveryRecordIds(
      record?.dependency_record_ids,
    ))
      group.dependencyRecordIds.add(dependencyId);
    if (record?.public_summary && typeof record.public_summary === "object")
      group.samples.push(record.public_summary);
    group.records.push(record);
    groups.set(key, group);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    origins: [...group.origins],
    statusCodes: [...group.statusCodes],
    exclusionReasons: [...group.exclusionReasons],
    dependencyRecordIds: [...group.dependencyRecordIds],
  }));
};
