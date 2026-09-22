import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPerformanceDiscoveryCreatePayload,
  discoveryCaptureIssue,
  performanceDiscoveryDraftTargetIssue,
  publicDiscoveryRequestUrl,
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

test("full performance evidence preserves URL encoding and repeated header entries", () => {
  const url = "https://api.example.test/search?name=RIO%20%E9%85%92&empty=&tag=a&tag=b";
  const cookies = [{ name: "Set-Cookie", value: "first=one" }, { name: "Set-Cookie", value: "second=two" }];
  const record = {
    origin: "https://api.example.test", path: "/search",
    public_summary: {
      source_authorized: true,
      observed_request: { url, headers: { "accept-language": "zh-CN", cookie: "session=fixture" }, headers_complete: true },
      observed_response: { headers_array: cookies, headers_complete: true, body: { total: 1 } },
    },
  };
  assert.equal(publicDiscoveryRequestUrl(record), url);
  const preview = publicDiscoverySamplePreview(record);
  assert.equal(preview.headers["accept-language"], "zh-CN");
  assert.equal(preview.headers.cookie, "session=fixture");
  assert.deepEqual(preview.response.headers_array, cookies);
  record.public_summary.source_authorized = false;
  assert.equal(publicDiscoveryRequestUrl(record), "https://api.example.test/search");
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

test("sample URL uses its captured API origin and preserves repeated public query parameters", () => {
  assert.equal(publicDiscoveryRequestUrl({
    origin: "http://api.example.test:8107", path: "/search/products",
    public_summary: { observed_request: { query: [
      { name: "tag", value: "手机 配件" }, { name: "tag", value: "a+b" },
      { name: "empty", value: "" }, { name: "token", value: "<redacted>" },
    ] } },
  }), "http://api.example.test:8107/search/products?tag=%E6%89%8B%E6%9C%BA+%E9%85%8D%E4%BB%B6&tag=a%2Bb&empty=&token=%3Credacted%3E");
  assert.equal(publicDiscoveryRequestUrl({
    origin: "https://api.example.test", path: "/login",
  }), "https://api.example.test/login");
});

test("sample URL never falls back to the page URL or raw captured URL", () => {
  assert.equal(publicDiscoveryRequestUrl({
    path: "/login", target_url: "https://frontend.example.test", url: "https://api.example.test/?token=raw-secret",
  }), "");
  assert.equal(publicDiscoveryRequestUrl({ origin: "javascript:alert(1)", path: "/login" }), "");
  assert.equal(publicDiscoveryRequestUrl({
    origin: "https://user:secret@api.example.test", path: "/login?token=raw-secret#fragment",
  }), "https://api.example.test/login");
  assert.equal(publicDiscoveryRequestUrl({ origin: "https://api.example.test", path: "" }), "");
  assert.equal(publicDiscoveryRequestUrl({
    origin: "https://api.example.test", path: "/login",
    public_summary: { source_authorized: false, observed_request: { query: [{ name: "token", value: "hidden-secret" }] } },
  }), "https://api.example.test/login");
  assert.equal(publicDiscoveryRequestUrl({
    origin: "https://api.example.test", path: "/items/a%2Fb",
    public_summary: { observed_request: { query: { offset: 0, active: false } } },
  }), "https://api.example.test/items/a%2Fb?offset=0&active=false");
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
