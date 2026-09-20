import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPerformanceDiscoveryCreatePayload,
  discoveryCaptureIssue,
  performanceDiscoveryDraftTargetIssue,
  publicDiscoverySamplePreview,
} from "../src/views/perf-testing/performanceDiscoveryState.js";

test("discovery create payload defaults to automatic origin approval and excludes editor fields", () => {
  const payload = buildPerformanceDiscoveryCreatePayload({
    target_url: " https://example.test/catalog ",
    description: " 搜索商品 ",
    model_id: 7,
    api_origin: " ",
    allow_test_data_writes: true,
    exploration_timeout_seconds: 300,
    target_id: 42,
    version: 9,
  });

  assert.deepEqual(payload, {
    target_url: "https://example.test/catalog",
    description: "搜索商品",
    model_id: 7,
    api_origin: null,
    auto_approve_origins: true,
    allow_test_data_writes: true,
    exploration_timeout_seconds: 300,
  });
  assert.equal("target_id" in payload, false);
  assert.equal("version" in payload, false);
  assert.equal(buildPerformanceDiscoveryCreatePayload({
    target_url: "https://example.test", description: "探索", model_id: 7,
    allow_test_data_writes: true, exploration_timeout_seconds: 900, auto_approve_origins: false,
  }).auto_approve_origins, false);
});

test("sample preview retains server-public query, body and response variants", () => {
  const preview = publicDiscoverySamplePreview({
    public_summary: {
      observed_request: {
        query: { tag: ["手机 配件", "a+b"], empty: "" },
        headers: { accept: "application/json" },
        json: { active: true },
      },
      observed_response: { body: { items: [] } },
    },
  });

  assert.deepEqual(preview.query, {
    tag: ["手机 配件", "a+b"],
    empty: "",
  });
  assert.deepEqual(preview.json, { active: true });
  assert.deepEqual(preview.response.body, { items: [] });
});

test("capture failure distinguishes classified errors from missing historic diagnostics", () => {
  assert.match(discoveryCaptureIssue({ public_summary: {
    capture_reason: "body_read_failed",
    capture_diagnostic: { stage: "response_body", kind: "body_unavailable" },
  } }), /响应体采集失败：浏览器中的响应内容已不可读取/);
  assert.match(discoveryCaptureIssue({ public_summary: {
    capture_reason: "body_read_failed",
  } }), /现有记录未包含底层原因/);
  assert.equal(discoveryCaptureIssue({ public_summary: { capture_complete: true } }), "");
});

test("draft target requires the confirmed API origin, normalized across paths and default ports", () => {
  assert.equal(performanceDiscoveryDraftTargetIssue(
    { api_origin: "http://api.example.test:80/v1" },
    { base_url: "http://api.example.test/service" },
  ), "");
  assert.match(performanceDiscoveryDraftTargetIssue(
    { api_origin: "https://api.example.test:8107" },
    { base_url: "http://api.example.test:8107/" },
  ), /探索任务接口来源：https:\/\/api\.example\.test:8107；所选压测目标来源：http:\/\/api\.example\.test:8107/);
  assert.match(performanceDiscoveryDraftTargetIssue(
    { api_origin: "" },
    { base_url: "http://api.example.test" },
  ), /探索任务接口来源：未确认/);
  assert.match(performanceDiscoveryDraftTargetIssue(
    { api_origin: "http://api.example.test" },
    { base_url: "" },
  ), /所选压测目标来源：未确认/);
});
