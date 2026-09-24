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
  canAnalyzePerformanceRun,
  displayAnalysisValue,
  displayEvidenceValue,
  isDefinitiveAnalysisCreateError,
  formatAnalysisDuration,
  isAnalysisActive,
} from "../src/views/perf-testing/performanceAnalysisState.js";

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
    targets: { p95_ms: 100 },
  });
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
