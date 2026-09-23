import test from "node:test";
import assert from "node:assert/strict";
import {
  analysisCheckLabel,
  analysisFindingKindLabel,
  analysisItems,
  analysisSeverityLabel,
  analysisTargetsIssue,
  buildAnalysisRequest,
  buildAnalysisTargets,
  canAnalyzePerformanceRun,
  displayAnalysisValue,
  displayEvidenceValue,
  isDefinitiveAnalysisCreateError,
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
  assert.equal(analysisCheckLabel("rps_min"), "RPS 下限");
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
