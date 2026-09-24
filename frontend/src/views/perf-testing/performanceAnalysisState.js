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
export const canAnalyzeRunType = (run, analysisType = "load_summary") => {
  if (!FINISHED_RUN_STATUSES.has(run?.status)) return false;
  if (analysisType === "validation_diagnosis")
    return run?.mode === "validation";
  return run?.mode === "load";
};
export const analysisTypeLabel = (analysisType) =>
  ({
    load_summary: "AI 分析结果",
    validation_diagnosis: "AI 验证诊断",
    load_comparison: "AI 对比解读",
  })[analysisType] || "AI 分析结果";
export const analysisEmptyLabel = (analysisType) =>
  ({
    validation_diagnosis: "尚无 AI 验证诊断记录",
    load_comparison: "尚无 AI 对比解读记录",
  })[analysisType] || "尚无 AI 分析记录";
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
export const analysisPhaseLabel = (phase) =>
  ({
    queued: "排队等待",
    preparing: "准备分析",
    waiting_response: "等待响应",
    receiving: "正在接收正文",
    retrying: "正在重试",
    validating: "正在核验结果",
    completed: "已完成",
  })[phase] ||
  phase ||
  "未记录阶段信息";
export const analysisRetryReasonLabel = (reason) =>
  ({
    timeout: "等待响应超时",
    connection_error: "连接暂时异常",
    rate_limited: "服务繁忙，请求受限",
    upstream_error: "服务暂时异常",
    auth_error: "模型服务认证异常",
    stream_interrupted: "响应中断",
    unknown: "未知原因",
  })[reason] ||
  reason ||
  "-";
export const analysisRetryDescription = (progress = {}) => {
  if (!progress.retry_reason) return "暂无";
  const delay = Number(progress.retry_delay_seconds);
  const suffix =
    Number.isFinite(delay) && delay > 0 ? `，上次重试等待 ${delay} 秒` : "";
  return `${analysisRetryReasonLabel(progress.retry_reason)}${suffix}`;
};
export const analysisProgress = (analysis) => {
  const progress = analysis?.progress;
  return progress &&
    typeof progress === "object" &&
    !Array.isArray(progress) &&
    progress.phase
    ? progress
    : null;
};
export const analysisElapsedSeconds = (analysis, now = Date.now()) => {
  const progressSeconds = Number(analysisProgress(analysis)?.elapsed_seconds);
  const savedSeconds =
    Number.isFinite(progressSeconds) && progressSeconds >= 0
      ? progressSeconds
      : 0;
  if (!isAnalysisActive(analysis)) return savedSeconds;
  const started = Date.parse(
    analysis?.started_at || analysis?.created_at || "",
  );
  if (!Number.isFinite(started)) return savedSeconds;
  return Math.max(savedSeconds, Math.floor(Math.max(0, now - started) / 1000));
};
export const formatAnalysisDuration = (seconds) => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
};
export const analysisBodyTextLabel = (progress) => {
  const characters = Math.max(0, Number(progress?.received_chars) || 0);
  if (characters > 0) return `已收到正文 ${characters} 字`;
  if (Number(progress?.stream_chunks) > 0)
    return "已收到内容片段，尚未收到正文";
  return "尚未收到正文";
};
export const analysisModelTimeoutSeconds = (progress) => {
  const seconds = Number(progress?.model_timeout_seconds);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
};
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
    rps_min: "平均请求吞吐量下限",
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

export const buildAnalysisRequest = (
  modelConfigId,
  values,
  requestId,
  analysisType = "load_summary",
  comparisonRunId = null,
) => {
  const payload = {
    request_id: requestId || createPerformanceRequestId(),
    model_config_id: modelConfigId,
    analysis_type: analysisType,
    targets:
      analysisType === "load_summary" ? buildAnalysisTargets(values) : {},
  };
  if (analysisType === "load_comparison" && comparisonRunId)
    payload.comparison_run_id = comparisonRunId;
  return payload;
};

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
export const formatComparisonNumber = (value, precision = 3) => {
  if (typeof value === "boolean" || value === "" || value == null) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Number(number.toFixed(precision)).toString();
};
export const formatComparisonMetric = (value, unit = "") => {
  const number = formatComparisonNumber(value);
  const text = number ?? displayAnalysisValue(value);
  return `${text}${value == null || !unit ? "" : ` ${unit}`}`;
};
export const formatComparisonDelta = (row = {}) => {
  const delta = formatComparisonNumber(row.delta);
  if (delta == null) return "-";
  const deltaNumber = Number(delta);
  const unit = row.unit === "%" ? " 个百分点" : row.unit ? ` ${row.unit}` : "";
  const percent = formatComparisonNumber(row.delta_percent, 2);
  const percentText = percent == null ? "" : `（${percent}%）`;
  return `${deltaNumber > 0 ? "+" : ""}${delta}${unit}${percentText}`;
};
export const isCurrentComparisonResponse = (
  responseSequence,
  currentSequence,
  requestedBaselineId,
  selectedBaselineId,
) =>
  responseSequence === currentSequence &&
  String(requestedBaselineId || "") === String(selectedBaselineId || "");

const EVIDENCE_VALUE_LABELS = {
  requests: "请求数",
  failures: "失败数",
  rps: "平均请求吞吐量",
  peak_interval_rps: "采样区间峰值",
  interval_seconds: "采样区间时长",
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
const comparisonMatchStatusLabel = (status) =>
  ({
    matched: "已匹配",
    baseline_only: "仅基准存在",
    current_only: "仅本次存在",
    ambiguous: "无法唯一匹配",
  })[status] || "未记录";
const comparisonStatusLabel = (status) =>
  ({
    comparable: "可比较",
    conditions_changed: "条件有变化",
    insufficient_data: "数据不足",
  })[status] || "未记录";
const isComparisonMetricValue = (value) =>
  Object.hasOwn(value, "baseline") &&
  Object.hasOwn(value, "current") &&
  Object.hasOwn(value, "delta");
const comparisonMetricSummary = (value) =>
  `基准 ${formatComparisonMetric(value.baseline, value.unit)}；本次 ${formatComparisonMetric(value.current, value.unit)}；变化 ${formatComparisonDelta(value)}`;
export const analysisEvidenceLabel = (evidence = {}) =>
  ({
    "overall.rps": "平均请求吞吐量",
    "overall.peak_interval_rps": "采样区间峰值",
    "trend.throughput": "采样区间吞吐量趋势",
  })[evidence.id] ||
  evidence.label ||
  evidence.id ||
  "-";
export const analysisEvidenceUnit = (evidence = {}) =>
  evidence.unit ||
  { "overall.peak_interval_rps": "次/秒", "trend.throughput": "次/秒" }[
    evidence.id
  ] ||
  "";
export const displayEvidenceValue = (value) => {
  if (!value || typeof value !== "object") return displayAnalysisValue(value);
  if (Array.isArray(value)) {
    const dependencies = value
      .filter(
        (item) =>
          item &&
          typeof item === "object" &&
          (Object.hasOwn(item, "producer_step") ||
            Object.hasOwn(item, "consumer_step")),
      )
      .slice(0, 12);
    if (!dependencies.length) return displayAnalysisValue(value);
    const availability = { true: "可用", false: "不可用", null: "未确定" };
    const rows = dependencies.map((item) => {
      const producer =
        item.producer_step == null ? "配置" : `步骤 ${item.producer_step}`;
      const consumer =
        item.consumer_step == null ? "未记录" : `步骤 ${item.consumer_step}`;
      return `${producer} → ${consumer}：${availability[String(item.available)] || "未确定"}`;
    });
    return `${rows.join("；")}${value.length > dependencies.length ? "；其余已省略" : ""}`;
  }
  if (isComparisonMetricValue(value)) return comparisonMetricSummary(value);
  if (Array.isArray(value.metrics) && Object.hasOwn(value, "match_status")) {
    const metrics = value.metrics
      .slice(0, 7)
      .map((item) => {
        if (!item || typeof item !== "object" || !isComparisonMetricValue(item))
          return null;
        return `${item.label || item.key || "指标"}：${formatComparisonMetric(item.baseline, item.unit)} → ${formatComparisonMetric(item.current, item.unit)}（${formatComparisonDelta(item)}）`;
      })
      .filter(Boolean);
    const match = comparisonMatchStatusLabel(value.match_status);
    return `接口匹配：${match}${metrics.length ? `；${metrics.join("；")}` : ""}${value.metrics.length > metrics.length ? "；其余指标已省略" : ""}`;
  }
  if (Object.hasOwn(value, "conditions") && Object.hasOwn(value, "status")) {
    const reasons = Array.isArray(value.reasons)
      ? value.reasons.filter((item) => typeof item === "string").slice(0, 5)
      : [];
    return `可比性：${comparisonStatusLabel(value.status)}${reasons.length ? `；${reasons.join("；")}` : ""}`;
  }
  if (Number.isInteger(value.step_index)) {
    const status =
      {
        passed: "通过",
        failed: "失败",
        skipped: "跳过",
        pending: "未执行",
        running: "执行中",
      }[value.status] || "未记录";
    const failedIndexes = (items) =>
      (Array.isArray(items) ? items : [])
        .filter((item) => item?.status === "failed")
        .map((item, index) => item?.index ?? index + 1);
    const assertionIndexes = failedIndexes(value.assertions);
    const extractionIndexes = failedIndexes(value.extractions);
    const parts = [`步骤状态：${status}`];
    if (value.response?.status_code != null)
      parts.push(`HTTP：${value.response.status_code}`);
    if (assertionIndexes.length)
      parts.push(`失败断言：${assertionIndexes.join("、")}`);
    if (extractionIndexes.length)
      parts.push(`失败提取：${extractionIndexes.join("、")}`);
    if (value.extractions_committed === true) parts.push("提取结果：已提交");
    else if (value.extractions_committed === false)
      parts.push("提取结果：未提交");
    return parts.join("；");
  }
  if (
    Object.hasOwn(value, "validation_passed") ||
    Object.hasOwn(value, "planned_steps")
  ) {
    const status =
      {
        completed: "已结束",
        failed: "执行失败",
        cancelled: "已取消",
        incomplete: "不完整",
      }[value.status] || "未记录";
    const complete =
      value.complete === true
        ? "完整"
        : value.complete === false
          ? "不完整"
          : "未记录";
    const passed =
      value.validation_passed === true
        ? "通过"
        : value.validation_passed === false
          ? "未通过"
          : "未记录";
    return `运行：${status}；统计：${complete}；验证：${passed}；步骤：${displayAnalysisValue(value.reported_steps)} / ${displayAnalysisValue(value.planned_steps)}`;
  }
  return Object.entries(value)
    .map(
      ([key, item]) =>
        `${EVIDENCE_VALUE_LABELS[key] || key}：${displayAnalysisValue(item)}`,
    )
    .join("；");
};
