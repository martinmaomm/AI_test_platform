import { createPerformanceRequestId } from "./performanceExecutionState.js";

export const ANALYSIS_ACTIVE_STATUSES = new Set(["queued", "running"]);
export const ANALYSIS_TERMINAL_STATUSES = new Set(["completed", "failed"]);
const FINISHED_RUN_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "incomplete",
]);

export const canAnalyzePerformanceRun = (run) =>
  run?.mode === "load" && FINISHED_RUN_STATUSES.has(run?.status);
export const isAnalysisActive = (analysis) =>
  ANALYSIS_ACTIVE_STATUSES.has(analysis?.status);
export const isAnalysisCompleted = (analysis) =>
  analysis?.status === "completed";
export const analysisStatusLabel = (status) =>
  ({
    queued: "排队中",
    running: "分析中",
    completed: "已完成",
    failed: "分析失败",
  })[status] ||
  status ||
  "-";
export const analysisStatusType = (status) =>
  ({
    queued: "warning",
    running: "primary",
    completed: "success",
    failed: "danger",
  })[status] || "info";
export const analysisFindingKindLabel = (kind) =>
  ({ observation: "已观测", hypothesis: "待验证推测" })[kind] || kind || "-";
export const analysisSeverityLabel = (severity) =>
  ({ info: "提示", warning: "警告", critical: "严重" })[severity] ||
  severity ||
  "提示";
export const analysisCheckLabel = (key) =>
  ({
    p95_ms: "P95 上限",
    error_rate_percent: "错误率上限",
    rps_min: "RPS 下限",
  })[key] ||
  key ||
  "-";
export const isDefinitiveAnalysisCreateError = (status) =>
  Number.isInteger(status) && status >= 400 && status < 500 && status !== 409;

const finiteNumber = (value) => {
  if (typeof value === "boolean" || value === "" || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

export const buildAnalysisTargets = (values = {}) => {
  const targets = {};
  const p95 = finiteNumber(values.p95_ms);
  const errorRate = finiteNumber(values.error_rate_percent);
  const rps = finiteNumber(values.rps_min);
  if (p95 != null && p95 > 0) targets.p95_ms = p95;
  if (errorRate != null && errorRate >= 0 && errorRate <= 100)
    targets.error_rate_percent = errorRate;
  if (rps != null && rps > 0) targets.rps_min = rps;
  return targets;
};

export const analysisTargetsIssue = (values = {}) => {
  const p95 = finiteNumber(values.p95_ms);
  const errorRate = finiteNumber(values.error_rate_percent);
  const rps = finiteNumber(values.rps_min);
  if (values.p95_ms != null && values.p95_ms !== "" && p95 == null)
    return "P95 目标必须是有效数字";
  if (p95 != null && p95 <= 0) return "P95 目标必须大于 0";
  if (
    values.error_rate_percent != null &&
    values.error_rate_percent !== "" &&
    errorRate == null
  )
    return "错误率目标必须是有效数字";
  if (errorRate != null && (errorRate < 0 || errorRate > 100))
    return "错误率目标应在 0 到 100 之间";
  if (values.rps_min != null && values.rps_min !== "" && rps == null)
    return "RPS 目标必须是有效数字";
  if (rps != null && rps <= 0) return "RPS 目标必须大于 0";
  return "";
};

export const buildAnalysisRequest = (modelConfigId, values, requestId) => ({
  request_id: requestId || createPerformanceRequestId(),
  model_config_id: modelConfigId,
  targets: buildAnalysisTargets(values),
});

export const analysisItems = (response) => {
  const body = response?.data ?? response;
  if (Array.isArray(body?.items)) return body.items;
  if (Array.isArray(body?.data?.items)) return body.data.items;
  return [];
};
export const analysisRecord = (response) => response?.data ?? response;
export const modelLabel = (model = {}) =>
  model.name ||
  [model.provider_name || model.provider, model.model_name]
    .filter(Boolean)
    .join(" · ") ||
  `模型 ${model.id}`;
export const displayAnalysisValue = (value) => {
  if (value == null) return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const EVIDENCE_VALUE_LABELS = {
  requests: "请求数",
  failures: "失败数",
  rps: "平均 RPS",
  avg_response_time: "平均响应时间",
  p95: "P95",
  p99: "P99",
  users: "用户数",
  error_rate_percent: "错误率",
  assigned_users: "分配用户数",
  status: "状态",
  complete: "统计完整",
  peak_worker_cpu_percent: "Worker CPU 峰值",
  peak_worker_memory_mib: "Worker 内存峰值",
};
export const displayEvidenceValue = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return displayAnalysisValue(value);
  return Object.entries(value)
    .map(
      ([key, item]) =>
        `${EVIDENCE_VALUE_LABELS[key] || key}：${displayAnalysisValue(item)}`,
    )
    .join("；");
};
