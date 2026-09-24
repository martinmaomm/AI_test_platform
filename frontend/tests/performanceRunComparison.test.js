import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("comparison panel keeps baseline scopes, safe summary rendering and optional AI interpretation", async () => {
  const [comparison, analysis, detail] = await Promise.all([
    read("../src/views/perf-testing/PerformanceRunComparison.vue"),
    read("../src/views/perf-testing/PerformanceRunAnalysis.vue"),
    read("../src/views/perf-testing/PerfRunDetail.vue"),
  ]);
  assert.match(comparison, /getPerformanceRunComparisonCandidates/);
  assert.match(comparison, /String\(item\.id\) !== captured\.runId/);
  assert.match(comparison, /comparison\.comparability\?\.conditions/);
  assert.match(comparison, /analysis-type="load_comparison"/);
  assert.match(comparison, /v-if="baselineRunId"/);
  assert.match(comparison, /scopeCurrent\(captured\)/);
  assert.match(comparison, /let comparisonRequestEpoch = 0/);
  assert.match(comparison, /isCurrentComparisonResponse/);
  assert.doesNotMatch(
    comparison,
    /request_body|response_body|authorization|cookie/i,
  );
  assert.match(
    analysis,
    /analysisType: \{ type: String, default: "load_summary" \}/,
  );
  assert.match(analysis, /comparisonRunId/);
  assert.match(analysis, /comparisonData/);
  assert.match(analysis, /comparisonData\?\.endpoints/);
  assert.match(analysis, /validationMatch/);
  assert.match(analysis, /buildAnalysisRequest\([\s\S]*analysisType\.value/);
  assert.match(detail, /analysis-type="validation_diagnosis"/);
  assert.match(
    detail,
    /<PerformanceRunComparison[\s\S]*v-if="run\.mode === 'load'"/,
  );
});
