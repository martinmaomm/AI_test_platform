const text = (value) => (typeof value === "string" ? value.trim() : value);

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
