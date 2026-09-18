import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPerformanceDiscoveryCreatePayload,
  publicDiscoverySamplePreview,
} from "../src/views/perf-testing/performanceDiscoveryState.js";

test("discovery create payload is the strict six-field contract", () => {
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
    allow_test_data_writes: true,
    exploration_timeout_seconds: 300,
  });
  assert.equal("target_id" in payload, false);
  assert.equal("version" in payload, false);
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
