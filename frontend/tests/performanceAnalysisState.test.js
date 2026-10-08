import test from "node:test";
import assert from "node:assert/strict";
import {
  analysisCheckLabel,
  analysisBodyTextLabel,
  analysisElapsedSeconds,
  analysisFindingKindLabel,
  analysisItems,
  analysisModelTimeoutSeconds,
  analysisPhaseLabel,
  analysisProgress,
  analysisRetryDescription,
  analysisRetryReasonLabel,
  analysisSeverityLabel,
  analysisTargetsIssue,
  buildAnalysisRequest,
  buildAnalysisTargets,
  canAnalyzeRunType,
  canAnalyzePerformanceRun,
  displayAnalysisValue,
  displayEvidenceValue,
  formatComparisonDelta,
  formatComparisonMetric,
  isDefinitiveAnalysisCreateError,
  isCurrentComparisonResponse,
  formatAnalysisDuration,
  isAnalysisActive,
} from "../src/views/perf-testing/performanceAnalysisState.js";

test("formal failure evidence identifies sample counts and preserves uncertain value types", () => {
  const text = displayEvidenceValue({
    step_index: 1, rule_kind: "assertion", sample_count: 2,
    expected_meta: { type: "number" }, actual_meta: { type: "missing_marker" },
    suggestion: "检查单用户验证", unexpected_secret: "never-render-this",
  });
  assert.match(text, /非失败请求次数/);
  assert.match(text, /缺失标记形态（需核对）/);
  assert.match(text, /检查单用户验证/);
  assert.doesNotMatch(text, /never-render-this/);
});

test("only finished formal load runs can display AI analysis", () => {
  assert.equal(
    canAnalyzePerformanceRun({ mode: "load", status: "completed" }),
    true,
  );
  assert.equal(
    canAnalyzePerformanceRun({ mode: "load", status: "incomplete" }),
    true,
  );
  assert.equal(
    canAnalyzePerformanceRun({ mode: "validation", status: "completed" }),
    false,
  );
  assert.equal(
    canAnalyzePerformanceRun({ mode: "load", status: "running" }),
    false,
  );
});

test("analysis types keep validation diagnosis and load comparison in their own terminal run scopes", () => {
  const validation = { mode: "validation", status: "completed" };
  const load = { mode: "load", status: "completed" };
  assert.equal(canAnalyzeRunType(validation, "validation_diagnosis"), true);
  assert.equal(canAnalyzeRunType(validation, "load_summary"), false);
  assert.equal(canAnalyzeRunType(load, "load_comparison"), true);
  assert.equal(
    canAnalyzeRunType({ mode: "load", status: "running" }, "load_comparison"),
    false,
  );
});

test("analysis targets omit empty values and retain valid zero error rate", () => {
  assert.deepEqual(
    buildAnalysisTargets({
      p95_ms: "",
      error_rate_percent: 0,
      rps_min: undefined,
    }),
    { error_rate_percent: 0 },
  );
  assert.deepEqual(
    buildAnalysisTargets({ p95_ms: 200, error_rate_percent: 1.5, rps_min: 30 }),
    { p95_ms: 200, error_rate_percent: 1.5, rps_min: 30 },
  );
  assert.equal(analysisTargetsIssue({ p95_ms: 0 }), "P95 目标必须大于 0");
  assert.equal(
    analysisTargetsIssue({ error_rate_percent: 101 }),
    "错误率目标应在 0 到 100 之间",
  );
});

test("analysis request retains request id for uncertain network retries", () => {
  assert.deepEqual(buildAnalysisRequest(8, { p95_ms: 100 }, "request-1"), {
    request_id: "request-1",
    model_config_id: 8,
    analysis_type: "load_summary",
    targets: { p95_ms: 100 },
  });
});

test("non-summary analysis requests retain their type and only comparisons carry a baseline", () => {
  assert.deepEqual(
    buildAnalysisRequest(
      8,
      { p95_ms: 100 },
      "request-2",
      "validation_diagnosis",
    ),
    {
      request_id: "request-2",
      model_config_id: 8,
      analysis_type: "validation_diagnosis",
      targets: {},
    },
  );
  assert.deepEqual(
    buildAnalysisRequest(8, {}, "request-3", "load_comparison", "baseline-1"),
    {
      request_id: "request-3",
      model_config_id: 8,
      analysis_type: "load_comparison",
      comparison_run_id: "baseline-1",
      targets: {},
    },
  );
});

test("comparison presentation rounds deltas while preserving zero and missing values", () => {
  assert.equal(formatComparisonMetric(0, "ms"), "0 ms");
  assert.equal(formatComparisonMetric(null, "ms"), "-");
  assert.equal(formatComparisonMetric(0.0009, "requests/s"), "0.001 requests/s");
  assert.equal(formatComparisonDelta({ delta: -0.0001, unit: "ms" }), "0 ms");
  assert.equal(
    formatComparisonDelta({
      delta: 1.23456,
      delta_percent: 12.3456,
      unit: "ms",
    }),
    "+1.235 ms（12.35%）",
  );
  assert.equal(
    formatComparisonDelta({ delta: 0, delta_percent: null, unit: "%" }),
    "0 个百分点",
  );
  assert.equal(formatComparisonDelta({ delta: null, unit: "ms" }), "-");
});

test("comparison evidence summarizes metrics, endpoints and comparability without JSON arrays", () => {
  assert.equal(
    displayEvidenceValue({
      key: "p95",
      label: "P95",
      unit: "ms",
      baseline: 123.4567,
      current: 130,
      delta: 6.5433,
      delta_percent: 5.3,
    }),
    "基准 123.457 ms；本次 130 ms；变化 +6.543 ms（5.3%）",
  );
  assert.equal(
    displayEvidenceValue({
      method: "GET",
      match_status: "baseline_only",
      baseline_index: 2,
      current_index: null,
      metrics: [
        {
          key: "requests",
          label: "请求数",
          unit: "",
          baseline: 0,
          current: null,
          delta: null,
          delta_percent: null,
        },
      ],
    }),
    "接口匹配：仅基准存在；请求数：0 → -（-）",
  );
  assert.equal(
    displayEvidenceValue({
      status: "conditions_changed",
      reasons: ["用户数不同"],
      conditions: [{ key: "users" }],
    }),
    "可比性：条件有变化；用户数不同",
  );
});

test("A-to-B-to-A baseline switching rejects the first A response by request sequence", () => {
  assert.equal(isCurrentComparisonResponse(1, 3, "A", "A"), false);
  assert.equal(isCurrentComparisonResponse(2, 3, "B", "A"), false);
  assert.equal(isCurrentComparisonResponse(3, 3, "A", "A"), true);
});

test("analysis state reads wrapped history and safely displays evidence values", () => {
  assert.deepEqual(analysisItems({ data: { items: [{ id: 1 }] } }), [
    { id: 1 },
  ]);
  assert.equal(isAnalysisActive({ status: "queued" }), true);
  assert.equal(isAnalysisActive({ status: "completed" }), false);
  assert.equal(displayAnalysisValue({ missing: true }), '{"missing":true}');
});

test("analysis presentation labels program conclusions and evidence in Chinese", () => {
  assert.equal(analysisCheckLabel("p95_ms"), "P95 上限");
  assert.equal(analysisCheckLabel("error_rate_percent"), "错误率上限");
  assert.equal(analysisCheckLabel("rps_min"), "平均请求吞吐量下限");
  assert.equal(analysisFindingKindLabel("observation"), "已观测");
  assert.equal(analysisFindingKindLabel("hypothesis"), "待验证推测");
  assert.equal(analysisSeverityLabel("critical"), "严重");
  assert.equal(
    displayEvidenceValue({ requests: 12, error_rate_percent: 1.5 }),
    "请求数：12；错误率：1.5",
  );
});

test("diagnosis evidence stays concise and does not serialize check metadata", () => {
  assert.equal(
    displayEvidenceValue({
      step_index: 7,
      status: "failed",
      response: { status_code: 500 },
      assertions: [{ index: 2, status: "failed", actual: "secret" }],
      extractions: [{ index: 1, status: "passed" }],
      extractions_committed: false,
    }),
    "步骤状态：失败；HTTP：500；失败断言：2；提取结果：未提交",
  );
  assert.equal(
    displayEvidenceValue([
      { producer_step: 2, consumer_step: 4, available: false },
      { producer_step: null, consumer_step: 5, available: true },
    ]),
    "步骤 2 → 步骤 4：不可用；配置 → 步骤 5：可用",
  );
});

test("only confirmed client rejection clears an uncertain create request", () => {
  assert.equal(isDefinitiveAnalysisCreateError(400), true);
  assert.equal(isDefinitiveAnalysisCreateError(403), true);
  assert.equal(isDefinitiveAnalysisCreateError(404), true);
  assert.equal(isDefinitiveAnalysisCreateError(409), false);
  assert.equal(isDefinitiveAnalysisCreateError(500), false);
});

test("analysis progress uses safe phase labels and never treats chunks as body text", () => {
  const analysis = {
    status: "running",
    started_at: "2026-09-23T00:00:00Z",
    progress: {
      phase: "waiting_response",
      elapsed_seconds: 3,
      stream_chunks: 2,
      received_chars: 0,
      retry_reason: "rate_limited",
    },
  };
  assert.equal(analysisPhaseLabel(analysis.progress.phase), "等待响应");
  assert.equal(
    analysisRetryReasonLabel(analysis.progress.retry_reason),
    "服务繁忙，请求受限",
  );
  assert.equal(
    analysisRetryDescription({
      retry_reason: "connection_error",
      retry_delay_seconds: 1,
    }),
    "连接暂时异常，上次重试等待 1 秒",
  );
  assert.equal(
    analysisBodyTextLabel(analysis.progress),
    "已收到内容片段，尚未收到正文",
  );
  assert.equal(
    analysisElapsedSeconds(analysis, Date.parse("2026-09-23T00:00:08Z")),
    8,
  );
  assert.equal(formatAnalysisDuration(68), "1 分 8 秒");
  assert.equal(
    analysisModelTimeoutSeconds({ model_timeout_seconds: 300 }),
    300,
  );
  assert.equal(analysisModelTimeoutSeconds({ model_timeout_seconds: 0 }), null);
});

test("old records with an empty progress object report no stage information", () => {
  assert.equal(analysisProgress({ progress: {} }), null);
  assert.equal(
    analysisProgress({ progress: { phase: "completed" } }).phase,
    "completed",
  );
});
