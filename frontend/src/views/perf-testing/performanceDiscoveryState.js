const text = (value) => (typeof value === "string" ? value.trim() : value);

const origin = (value) => {
  const raw = text(value);
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.origin : "";
  } catch {
    return "";
  }
};

const originLabel = (value) => origin(value) || text(value) || "未确认";

/**
 * The server only permits a draft when the approved performance target and
 * the confirmed discovery API endpoint are the same HTTP origin.
 */
export const performanceDiscoveryDraftTargetIssue = (task, target) => {
  if (!target) return "请先选择已批准的压测目标。";
  const taskOrigin = origin(task?.api_origin);
  const targetOrigin = origin(target?.base_url);
  const taskLabel = originLabel(task?.api_origin);
  const targetLabel = originLabel(target?.base_url);
  if (!taskOrigin || !targetOrigin || taskOrigin !== targetOrigin) {
    return `探索任务接口来源：${taskLabel}；所选压测目标来源：${targetLabel}。请选择或配置同来源的压测目标。`;
  }
  return "";
};

/**
 * Keep the create request deliberately separate from the editor-only target_id.
 * The discovery create serializer is strict and accepts exactly these fields.
 */
export const buildPerformanceDiscoveryCreatePayload = (form) => ({
  target_url: text(form.target_url),
  description: text(form.description),
  model_id: form.model_id,
  api_origin: text(form.api_origin) || null,
  auto_approve_origins: form.auto_approve_origins !== false,
  allow_test_data_writes: form.allow_test_data_writes === true,
  exploration_timeout_seconds: form.exploration_timeout_seconds,
});

/**
 * `public_summary` is the server-redacted view intended for the UI.  Keep the
 * request variants visible without ever reconstructing a raw captured request.
 */
export const publicDiscoverySamplePreview = (record) => {
  const summary = record?.public_summary || {};
  const request = summary.observed_request || {};
  const response = summary.observed_response || {};
  return {
    query: request.query ?? [],
    headers: request.headers ?? {},
    json: request.json,
    form: request.form,
    response: {
      headers: response.headers ?? {},
      body: response.body,
    },
  };
};

/** Build the display URL only from captured metadata and the public query. */
export const publicDiscoveryRequestUrl = (record) => {
  const requestOrigin = origin(record?.origin);
  const path = record?.path;
  if (!requestOrigin || typeof path !== "string" || !path.startsWith("/")) return "";
  // Query and fragment text must never be recovered from a raw/legacy path.
  const pathname = path.split(/[?#]/, 1)[0];
  const summary = record?.public_summary || {};
  const query = summary.source_authorized === false ? null : summary.observed_request?.query;
  const pairs = Array.isArray(query)
    ? query
    : Object.entries(query && typeof query === "object" ? query : {}).map(([name, value]) => ({ name, value }));
  const params = new URLSearchParams();
  for (const pair of pairs) {
    if (typeof pair?.name !== "string") continue;
    const values = Array.isArray(pair.value) ? pair.value : [pair.value];
    for (const value of values) params.append(pair.name, value == null ? "" : String(value));
  }
  const search = params.toString();
  return `${requestOrigin}${pathname}${search ? `?${search}` : ""}`;
};

export const discoveryCaptureIssue = (record) => {
  const summary = record?.public_summary || {};
  const reason = summary.capture_reason;
  if (!["body_read_failed", "body_read_timeout"].includes(reason)) return "";
  const diagnostic = summary.capture_diagnostic || {};
  const stage = diagnostic.stage === "request_body" ? "请求体" : "响应体";
  const kinds = {
    timeout: "读取超时",
    target_closed: "页面或浏览器已关闭",
    body_unavailable: "浏览器中的响应内容已不可读取",
    protocol_error: "浏览器通信异常",
  };
  const detail = kinds[diagnostic.kind] || (reason === "body_read_timeout" ? "读取超时" : "现有记录未包含底层原因");
  return `${stage}采集失败：${detail}。此样本不能导入计划。`;
};
