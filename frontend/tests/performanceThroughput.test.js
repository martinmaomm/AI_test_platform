import test from "node:test";
import assert from "node:assert/strict";
import {
  endpointThroughput,
  formatMetric,
  throughputSeries,
  throughputValue,
} from "../src/views/perf-testing/performanceExecutionState.js";
import {
  analysisCheckLabel,
  analysisEvidenceLabel,
  analysisEvidenceUnit,
} from "../src/views/perf-testing/performanceAnalysisState.js";

test("throughput preserves zero and distinguishes missing values", () => {
  assert.equal(formatMetric(0), "0.00");
  assert.equal(formatMetric(null), "-");
  assert.equal(formatMetric(undefined), "-");
  assert.equal(throughputValue({ average_rps: 0 }, "average_rps"), 0);
  assert.equal(throughputValue({}, "average_rps"), null);
});

test("interval and endpoint throughput follow backend positions without front-end math", () => {
  const samples = [{ timestamp: "a" }, { timestamp: "b" }, { timestamp: "c" }];
  const throughput = {
    intervals: [{ rps: null }, { rps: 0 }, { rps: 12.5 }],
    endpoint_rps: [2.5, null],
  };
  assert.deepEqual(throughputSeries(samples, throughput), [null, 0, 12.5]);
  assert.equal(endpointThroughput(throughput, 0), 2.5);
  assert.equal(endpointThroughput(throughput, 1), null);
});

test("AI throughput targets and evidence use the user-facing throughput wording", () => {
  assert.equal(analysisCheckLabel("rps_min"), "平均请求吞吐量下限");
  assert.equal(
    analysisEvidenceLabel({ id: "overall.peak_interval_rps" }),
    "采样区间峰值",
  );
  assert.equal(analysisEvidenceLabel({ id: "overall.rps" }), "平均请求吞吐量");
  assert.equal(analysisEvidenceUnit({ id: "trend.throughput" }), "次/秒");
});
