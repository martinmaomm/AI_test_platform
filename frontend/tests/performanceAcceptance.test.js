import test from "node:test";
import assert from "node:assert/strict";
import {
  serializeAcceptanceTargets,
  acceptanceLabel,
} from "../src/views/perf-testing/performanceAcceptanceState.js";
import { buildAnalysisRequest } from "../src/views/perf-testing/performanceAnalysisState.js";

test("optional acceptance goals retain zero errors and reject nonnumeric or invalid goals", () => {
  assert.deepEqual(
    serializeAcceptanceTargets({
      p95_ms: null,
      error_rate_percent: "",
      rps_min: undefined,
    }).value,
    {},
  );
  assert.deepEqual(
    serializeAcceptanceTargets({ error_rate_percent: 0, p95_ms: 500 }).value,
    { p95_ms: 500, error_rate_percent: 0 },
  );
  for (const input of [
    { p95_ms: 0 },
    { error_rate_percent: 101 },
    { rps_min: "10" },
    { p95_ms: NaN },
    { rps_min: Infinity },
  ]) {
    assert.ok(serializeAcceptanceTargets(input).error);
  }
  assert.equal(acceptanceLabel("not_configured"), "未配置验收标准");
  assert.equal(acceptanceLabel("pending"), "等待运行结束");
});

test("new summary requests use frozen run goals while diagnosis/comparison stay separate", () => {
  const frozen = { p95_ms: 500, error_rate_percent: 0 };
  assert.deepEqual(
    buildAnalysisRequest(1, frozen, "fixture", "load_summary").targets,
    frozen,
  );
  assert.deepEqual(
    buildAnalysisRequest(1, frozen, "fixture", "validation_diagnosis").targets,
    {},
  );
});
