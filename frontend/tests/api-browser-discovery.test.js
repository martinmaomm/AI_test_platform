import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { nextTick, ref, watch } from "vue";
import {
  BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH,
  BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS,
  browserDiscoveryBecameTerminal,
  browserDiscoveryConfig,
  browserDiscoveryDeleteState,
  browserDiscoveryElapsed,
  browserDiscoveryErrorCodeLabel,
  browserDiscoveryEvidenceCounts,
  browserDiscoveryFormSnapshot,
  browserDiscoveryItems,
  browserDiscoveryOriginResolution,
  browserDiscoveryOriginStateLabel,
  browserDiscoveryOriginSummary,
  browserDiscoveryExpandSelectedRecordIds,
  browserDiscoveryRecordIds,
  browserDiscoveryRecordGroups,
  browserDiscoveryTimeoutDefault,
  buildBrowserDiscoveryPayload,
  canConfirmBrowserDiscoveryOrigin,
  canHandoffBrowserDiscovery,
  canSelectBrowserDiscoveryOrigin,
  isApiOrigin,
  shouldApplyBrowserDiscoveryResponse,
} from "../src/utils/apiBrowserDiscovery.js";

const config = {
  enabled: true,
  limits: { timeout_seconds: 120 },
  models: [{ id: 8, model_name: "Test LLM" }],
};

test("browser discovery reads the established envelope and only enables configured models", () => {
  assert.deepEqual(browserDiscoveryConfig({ kind: "success", data: config }), config);
  assert.deepEqual(browserDiscoveryItems({ kind: "success", data: { items: [{ id: 1 }] } }), [{ id: 1 }]);
  assert.equal(isApiOrigin("https://api.example.test:8443"), true);
  assert.equal(isApiOrigin("https://api.example.test/v1"), false);
  assert.equal(browserDiscoveryTimeoutDefault(config), 120);
});

test("browser discovery creation requires explicit test-write confirmation and valid bounded input", () => {
  const form = { target_url: "https://example.test/login", description: "登录后查看订单", api_origin: "", model_id: 8, allow_test_data_writes: true, exploration_timeout_seconds: 120 };
  assert.deepEqual(buildBrowserDiscoveryPayload(form, config), { ...form, api_origin: null });
  assert.throws(() => buildBrowserDiscoveryPayload({ ...form, allow_test_data_writes: false }, config), /明确确认/);
  assert.throws(() => buildBrowserDiscoveryPayload({ ...form, api_origin: "https://api.example.test/v1" }, config), /API origin/);
  assert.throws(() => buildBrowserDiscoveryPayload({ ...form, exploration_timeout_seconds: BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS - 1 }, config), /不能少于/);
  assert.throws(() => buildBrowserDiscoveryPayload({ ...form, exploration_timeout_seconds: 121 }, config), /不能超过/);
  assert.throws(() => buildBrowserDiscoveryPayload({ ...form, description: "x".repeat(BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH + 1) }, config), /不能超过/);
  assert.equal(browserDiscoveryErrorCodeLabel("cancelled"), "用户已取消探索；已完成的网站操作不会撤销。");
});

test("browser discovery guard failures show actionable Chinese reasons", () => {
  const reasons = {
    TOOL_FAILURE: /连续页面操作失败/,
    TOOL_NOT_ALLOWED: /不允许的工具/,
    TOOL_BUDGET: /调用达到本轮上限/,
    REPEATED_OPERATION: /相同操作反复执行/,
    TARGET_OUT_OF_SCOPE: /授权范围以外/,
  };
  for (const [code, message] of Object.entries(reasons)) {
    assert.match(browserDiscoveryErrorCodeLabel(code), message);
    assert.doesNotMatch(browserDiscoveryErrorCodeLabel(code), /任务技术代码/);
  }
});

test("browser discovery form snapshots only become clean after the current values are submitted", () => {
  const initial = {
    target_url: "https://example.test/login",
    description: "登录后查看订单",
    api_origin: "",
    model_id: 8,
    allow_test_data_writes: false,
    exploration_timeout_seconds: 120,
  };
  const submitted = browserDiscoveryFormSnapshot(initial);
  assert.equal(submitted, browserDiscoveryFormSnapshot({ ...initial }));
  assert.notEqual(
    submitted,
    browserDiscoveryFormSnapshot({ ...initial, description: "登录后查看订单并创建测试数据" }),
  );
  assert.equal(
    submitted,
    browserDiscoveryFormSnapshot({ ...initial, target_url: " https://example.test/login " }),
  );
});

test("browser discovery rejects stale project, epoch, sequence, and task responses", () => {
  const current = { requestProjectId: 1, currentProjectId: 1, requestEpoch: 4, currentEpoch: 4, requestSequence: 7, latestSequence: 7, expectedTaskId: 9, currentTaskId: 9 };
  assert.equal(shouldApplyBrowserDiscoveryResponse(current), true);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentProjectId: 2 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentEpoch: 5 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, latestSequence: 8 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentTaskId: 10 }), false);
});

test("browser discovery refreshes records exactly on the selected task's active-to-terminal transition", () => {
  assert.equal(browserDiscoveryBecameTerminal({ id: "task-1", status: "running" }, { id: "task-1", status: "completed" }), true);
  assert.equal(browserDiscoveryBecameTerminal({ id: "task-1", status: "finalizing" }, { id: "task-1", status: "partial" }), true);
  assert.equal(browserDiscoveryBecameTerminal({ id: "task-1", status: "completed" }, { id: "task-1", status: "failed" }), false);
  assert.equal(browserDiscoveryBecameTerminal({ id: "task-1", status: "running" }, { id: "task-2", status: "completed" }), false);
});

test("browser discovery exposes only protocol-approved origin decisions and blocks ambiguous handoff", () => {
  const pending = {
    status: "running",
    origin_resolution: {
      mode: "auto",
      state: "awaiting_confirmation",
      origins: ["https://app.example.test"],
      pending: [{ origin: "https://api.example.test", method: "post", path: "/login" }],
      selected_origin: null,
      can_confirm: true,
    },
  };
  assert.deepEqual(browserDiscoveryOriginResolution(pending).pending, [{ origin: "https://api.example.test", method: "POST", path: "/login" }]);
  assert.equal(browserDiscoveryOriginSummary(pending), "待确认：https://api.example.test");
  assert.equal(canConfirmBrowserDiscoveryOrigin(pending), true);
  assert.equal(canConfirmBrowserDiscoveryOrigin({ ...pending, status: "completed" }), false);
  assert.equal(canConfirmBrowserDiscoveryOrigin({ ...pending, origin_resolution: { ...pending.origin_resolution, can_confirm: false } }), false);
  const selecting = {
    status: "completed",
    origin_resolution: { mode: "auto", state: "awaiting_selection", origins: ["https://app.example.test", "https://api.example.test"], pending: [], selected_origin: null, can_confirm: false },
  };
  assert.equal(canSelectBrowserDiscoveryOrigin(selecting, "https://api.example.test"), true);
  assert.equal(canSelectBrowserDiscoveryOrigin({ ...selecting, status: "running" }, "https://api.example.test"), false);
  assert.equal(canSelectBrowserDiscoveryOrigin(selecting, "https://other.example.test"), false);
  assert.equal(canHandoffBrowserDiscovery(selecting), false);
  const resolved = { ...selecting, origin_resolution: { ...selecting.origin_resolution, state: "resolved", selected_origin: "https://api.example.test" } };
  assert.equal(canHandoffBrowserDiscovery(resolved), true);
  assert.equal(browserDiscoveryOriginSummary(resolved), "已识别：https://api.example.test");
  assert.equal(browserDiscoveryOriginStateLabel({ status: "failed", origin_resolution: { mode: "auto", state: "detecting", origins: [], pending: [], selected_origin: null, can_confirm: false } }), "未发现接口来源");
  assert.equal(browserDiscoveryOriginSummary({ status: "cancelled", origin_resolution: { mode: "auto", state: "detecting", origins: ["https://api.example.test"], pending: [], selected_origin: null, can_confirm: false } }), "未确认接口来源");
  assert.equal(browserDiscoveryOriginStateLabel({ status: "cancelled", origin_resolution: { mode: "auto", state: "detecting", origins: ["https://api.example.test"], pending: [], selected_origin: null, can_confirm: false } }), "未确认接口来源");
  assert.deepEqual(browserDiscoveryEvidenceCounts({ records_count: 4, eligible_records_count: 3 }), { collected: 4, usable: 3 });
});

test("browser discovery handles elapsed fallback and only uses positive record ids", () => {
  assert.equal(browserDiscoveryElapsed({ started_at: "2026-09-09T00:00:00Z", finished_at: "2026-09-09T00:01:05Z" }), 65);
  assert.deepEqual(browserDiscoveryRecordIds([{ id: 1 }, { id: "2" }, { id: 0 }, { id: "x" }]), [1, 2]);
  const [group] = browserDiscoveryRecordGroups([{ id: 1, method: "get", path: "/orders", origin: "https://api.example.test", status_code: 200, is_eligible: true, dependency_record_ids: [3] }, { id: 2, method: "GET", path: "/orders", origin: "https://api.example.test", status_code: 200, is_eligible: false, exclusion_reason: "capture_incomplete" }]);
  assert.equal(group.count, 2);
  assert.deepEqual(group.eligibleRecordIds, [1]);
  assert.deepEqual(group.dependencyRecordIds, [3]);
});

test("browser discovery merges公开样本 only when authorized public summary and top-level contract are identical", () => {
  const makeSummary = () => ({
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: {
      headers: { a: "1", "content-type": "application/json" },
      body: { page: 1 },
      auth_hints: { header: "Authorization", location: "header" },
    },
    observed_response: {
      headers: { "content-type": "application/json" },
      body: { total: 2 },
    },
  });
  const records = [
    {
      id: 3,
      method: "get",
      path: "/orders",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 3,
      public_summary: makeSummary(),
      request_id: "r-3",
      captured_at: "2026-09-10T00:00:00Z",
      dependency_record_ids: [10],
      association: { node: "detail" },
    },
    {
      id: 6,
      method: "GET",
      path: "/orders",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 6,
      public_summary: {
        capture_complete: true,
        source_authorized: true,
        capture_reason: "ok",
        observed_response: {
          headers: { "content-type": "application/json" },
          body: { total: 2 },
        },
        observed_request: {
          auth_hints: { location: "header", header: "Authorization" },
          body: { page: 1 },
          headers: { "content-type": "application/json", a: "1" },
        },
      },
      request_id: "r-6",
      captured_at: "2026-09-10T00:00:10Z",
      dependency_record_ids: [11],
      association: { node: "list" },
    },
    {
      id: 4,
      method: "GET",
      path: "/orders",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "list",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 4,
      public_summary: makeSummary(),
    },
    {
      id: 7,
      method: "GET",
      path: "/orders",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "list",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 7,
      public_summary: makeSummary(),
    },
  ];
  const group = browserDiscoveryRecordGroups(records)[0];
  assert.equal(group.sampleCount, 2);
  assert.equal(group.count, 4);
  assert.equal(group.mergedCount, 2);
  const mergedSamples = group.records.filter((record) => record.duplicateCount > 1);
  assert.deepEqual(mergedSamples.length, 2);
  const first = mergedSamples.find((item) => item.representativeId === 3);
  assert.deepEqual(first?.sourceRecordIds.sort((a, b) => a - b), [3, 6]);
  const second = mergedSamples.find((item) => item.representativeId === 4);
  assert.deepEqual(second?.sourceRecordIds.sort((a, b) => a - b), [4, 7]);
  assert.equal(group.records[0].representativeId, 3);
  assert.equal(group.records[1].representativeId, 4);
});

test("browser discovery uses smallest legal representative id for stability", () => {
  const summary = {
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: {
      headers: { a: "1", "content-type": "application/json" },
      body: { page: 1 },
      auth_hints: { header: "Authorization", location: "header" },
    },
    observed_response: {
      headers: { "content-type": "application/json" },
      body: { total: 2 },
    },
  };
  const group = browserDiscoveryRecordGroups([
    {
      id: 11,
      method: "GET",
      path: "/stable",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      public_summary: summary,
    },
    {
      id: 5,
      method: "GET",
      path: "/stable",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      public_summary: summary,
    },
  ])[0];
  assert.equal(group.sampleCount, 1);
  assert.equal(group.records.length, 1);
  assert.equal(group.records[0].representativeId, 5);
  assert.deepEqual(group.records[0].sourceRecordIds.sort((a, b) => a - b), [5, 11]);
});

test("browser discovery keeps differences in public request/response/auth hints/order out of dedupe", () => {
  const base = {
    id: 21, sequence: 1, method: "POST", path: "/orders",
    origin: "https://api.example.test", status_code: 200,
    content_type: "application/json", resource_type: "fetch",
    is_eligible: true, exclusion_reason: "",
    public_summary: {
      capture_complete: true, source_authorized: true, capture_reason: "",
      observed_request: {
        query: [{ name: "tag", value: "first" }, { name: "tag", value: "second" }],
        headers: { "content-type": "application/json" },
        json: { page: 1 }, form: null,
        auth_hints: [{ name: "Authorization", scheme: "Bearer", value_sha256: "hash-a" }],
      },
      observed_response: {
        headers: { "content-type": "application/json" },
        auth_hints: [], body: { code: 200, data: [1, 2] },
      },
    },
  };
  const distinctSamples = (records) => browserDiscoveryRecordGroups(records)
    .reduce((total, group) => total + group.sampleCount, 0);
  const duplicate = { ...structuredClone(base), id: 22, sequence: 2 };
  // Control proves these fixtures actually reach the comparison path.
  assert.equal(distinctSamples([base, duplicate]), 1);
  const mutations = [
    ["query value", r => { r.public_summary.observed_request.query[0].value = "changed"; }],
    ["repeated query order", r => { r.public_summary.observed_request.query.reverse(); }],
    ["request number vs string", r => { r.public_summary.observed_request.json.page = "1"; }],
    ["request body", r => { r.public_summary.observed_request.json.page = 2; }],
    ["form body", r => { r.public_summary.observed_request.form = [{ name: "page", value: "1" }]; }],
    ["request header", r => { r.public_summary.observed_request.headers.accept = "application/json"; }],
    ["authentication", r => { r.public_summary.observed_request.auth_hints[0].value_sha256 = "hash-b"; }],
    ["response header", r => { r.public_summary.observed_response.headers["content-type"] = "text/plain"; }],
    ["response business status", r => { r.public_summary.observed_response.body.code = 500; }],
    ["response array order", r => { r.public_summary.observed_response.body.data.reverse(); }],
    ["HTTP status", r => { r.status_code = 500; }],
    ["origin", r => { r.origin = "https://other.example.test"; }],
    ["method", r => { r.method = "GET"; }],
    ["path", r => { r.path = "/other"; }],
    ["content type", r => { r.content_type = "application/x-www-form-urlencoded"; }],
    ["resource type", r => { r.resource_type = "xhr"; }],
    ["eligibility", r => { r.is_eligible = false; }],
    ["exclusion", r => { r.exclusion_reason = "origin_not_selected"; }],
    ["capture reason", r => { r.public_summary.capture_reason = "unknown"; }],
    ["missing evidence", r => { delete r.public_summary.observed_response; }],
    ["unauthorized", r => { r.public_summary.source_authorized = false; }],
    ["incomplete", r => { r.public_summary.capture_complete = false; }],
  ];
  for (const [label, mutate] of mutations) {
    const changed = structuredClone(duplicate);
    mutate(changed);
    assert.equal(distinctSamples([base, changed]), 2, label);
  }
});

test("browser discovery watch keeps selected representative ids when group ids are numeric and string-coerced", async () => {
  const summary = {
    origin: "https://api.example.test",
    method: "GET",
    path: "/watch",
    status_code: 200,
    content_type: "application/json",
    resource_type: "api",
    is_eligible: true,
    exclusion_reason: null,
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: { headers: {}, auth_hints: {} },
    observed_response: { headers: {}, body: {} },
  };

  const records = [
    { id: 60, method: "get", path: "/watch", origin: "https://api.example.test", status_code: 200, content_type: "application/json", resource_type: "api", is_eligible: true, exclusion_reason: null, sequence: 60, public_summary: summary },
    { id: 61, method: "get", path: "/watch", origin: "https://api.example.test", status_code: 200, content_type: "application/json", resource_type: "api", is_eligible: true, exclusion_reason: null, sequence: 61, public_summary: summary },
  ];

  const selectedRecordIds = ref([String(records[0].id)]);
  const groups = ref(browserDiscoveryRecordGroups(records));

  watch(
    groups,
    (nextGroups) => {
      const eligibleIds = new Set(
        nextGroups.flatMap((group) => group.eligibleRepresentativeIds ?? []).map((id) => String(id)),
      );
      selectedRecordIds.value = selectedRecordIds.value.filter((id) => eligibleIds.has(String(id)));
    },
    { immediate: true },
  );
  await nextTick();

  assert.deepEqual(selectedRecordIds.value, [String(records[0].id)]);

  groups.value = browserDiscoveryRecordGroups(records);
  await nextTick();
  assert.deepEqual(selectedRecordIds.value, [String(records[0].id)]);

  groups.value = browserDiscoveryRecordGroups([
    { id: 61, method: "get", path: "/watch", origin: "https://api.example.test", status_code: 200, content_type: "application/json", resource_type: "api", is_eligible: true, exclusion_reason: null, sequence: 61, public_summary: summary },
  ]);
  await nextTick();
  assert.deepEqual(selectedRecordIds.value, []);
});

test("browser discovery refuses to dedupe incomplete/unauthorized samples and does not mutate input records", () => {
  const makeSummary = (overrides = {}) => ({
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: {
      headers: { accept: "application/json" },
      auth_hints: { header: "Authorization" },
      body: { id: 1 },
    },
    observed_response: {
      headers: { "content-type": "application/json" },
      body: { ok: true },
      status: 200,
    },
    ...overrides,
  });
  const records = [
    {
      id: 31,
      method: "get",
      path: "/profile",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 31,
      public_summary: makeSummary(),
    },
    {
      id: 32,
      method: "GET",
      path: "/profile",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 32,
      public_summary: makeSummary({ source_authorized: false }),
    },
    {
      id: 33,
      method: "GET",
      path: "/profile",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 33,
      public_summary: makeSummary({ capture_complete: false }),
    },
    {
      id: 34,
      method: "GET",
      path: "/profile",
      origin: "https://api.example.test",
      status_code: 200,
      content_type: "application/json",
      resource_type: "api",
      is_eligible: true,
      exclusion_reason: null,
      sequence: 34,
    },
  ];
  const recordsCopy = structuredClone(records);
  const group = browserDiscoveryRecordGroups(records)[0];
  assert.equal(group.sampleCount, records.length);
  assert.equal(group.count, records.length);
  assert.deepEqual(records, recordsCopy);
});

test("browser discovery avoids duplicate counting from repeated pagination ids", () => {
  const makeSummary = () => ({
    origin: "https://api.example.test",
    method: "GET",
    path: "/paging",
    status_code: 200,
    content_type: "application/json",
    resource_type: "api",
    is_eligible: true,
    exclusion_reason: null,
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: { headers: {}, auth_hints: {} },
    observed_response: { headers: {}, body: {} },
  });
  const records = [
    { id: 41, method: "get", path: "/paging", origin: "https://api.example.test", status_code: 200, is_eligible: true, public_summary: makeSummary(), sequence: 1 },
    { id: 41, method: "get", path: "/paging", origin: "https://api.example.test", status_code: 200, is_eligible: true, public_summary: makeSummary(), sequence: 2 },
  ];
  const group = browserDiscoveryRecordGroups(records)[0];
  assert.equal(group.count, 1);
  assert.equal(group.recordIds.length, 1);
  assert.deepEqual(group.recordIds, [41]);
});

test("browser discovery helper expands selected representative ids to raw ids and excludes disabled sample", () => {
  const baseSummary = {
    origin: "https://api.example.test",
    method: "GET",
    path: "/health",
    status_code: 200,
    content_type: "application/json",
    resource_type: "api",
    is_eligible: true,
    exclusion_reason: null,
    capture_complete: true,
    source_authorized: true,
    capture_reason: "ok",
    observed_request: { headers: {}, auth_hints: {} },
    observed_response: { headers: {}, body: { ok: true } },
  };
  const disabledSummary = {
    ...baseSummary,
    is_eligible: false,
  };
  const groups = browserDiscoveryRecordGroups([
    { id: 51, method: "get", path: "/health", origin: "https://api.example.test", status_code: 200, is_eligible: true, public_summary: baseSummary, sequence: 1 },
    { id: 52, method: "get", path: "/health", origin: "https://api.example.test", status_code: 200, is_eligible: true, public_summary: baseSummary, sequence: 2 },
    { id: 53, method: "get", path: "/health", origin: "https://api.example.test", status_code: 200, is_eligible: false, public_summary: disabledSummary, sequence: 3 },
  ].map((record) => ({ ...record, content_type: "application/json", resource_type: "fetch", exclusion_reason: "" })));
  const group = groups[0];
  assert.equal(group.records.length, 2);
  assert.deepEqual(group.records[0].sourceRecordIds, [51, 52]);
  const expanded = browserDiscoveryExpandSelectedRecordIds(
    [group.records[0].representativeId, group.records[1].representativeId],
    groups,
  );
  assert.deepEqual(expanded.sort((a, b) => a - b), [51, 52]);
});

test("browser discovery requires an explicit server deletion grant for an ended task", () => {
  assert.deepEqual(
    browserDiscoveryDeleteState({ status: "failed", can_delete: true }),
    { canDelete: true, reason: "" },
  );
  assert.deepEqual(
    browserDiscoveryDeleteState({ status: "completed", can_delete: false, delete_block_reason: "任务已交接为 API 工作区来源，暂不能删除。" }),
    { canDelete: false, reason: "任务已交接为 API 工作区来源，暂不能删除。" },
  );
  assert.equal(browserDiscoveryDeleteState({ status: "running", can_delete: true }).canDelete, false);
  assert.match(browserDiscoveryDeleteState({ status: "unknown" }).reason, /先取消并等待停止/);
  assert.deepEqual(
    browserDiscoveryDeleteState({ status: "cancelled" }),
    { canDelete: false, reason: "任务删除状态已过期，请刷新后重试。" },
  );
});

test("browser discovery panel is feature-gated and hands off record ids with version", async () => {
  const [source, specs, workspace, router, documents, browser] = await Promise.all([
    readFile(new URL("../src/components/api-testing/BrowserDiscoveryPanel.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiSpecManage.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/router/index.js", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiWorkspaceDocuments.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiWorkspaceBrowser.vue", import.meta.url), "utf8"),
  ]);
  assert.match(source, /v-else-if="!enabled"/);
  assert.match(source, /API_BROWSER_DISCOVERY_ENABLED/);
  assert.match(source, /暂不能开始探索/);
  assert.match(source, /allow_test_data_writes/);
  assert.match(source, /browserDiscoveryExpandSelectedRecordIds\(selectedRecordIds\.value, recordGroups\.value\)/);
  assert.match(source, /version: props\.task\?\.version/);
  assert.match(source, /公开脱敏摘要/);
  assert.match(source, /load-more-records/);
  assert.match(source, /provider_name \|\| model\?\.provider/);
  assert.match(source, /v-for="record in group.records"/);
  assert.match(source, /task\.summary/);
  assert.match(source, /active\(task\) && task\.cancellation_requested/);
  assert.match(source, /BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS/);
  assert.match(source, /删除/);
  assert.match(source, /deleteState\(row\)/);
  assert.match(source, /data-testid="api-browser-discovery-create-form"/);
  assert.match(source, /aria-label="完整页面 URL"/);
  assert.match(source, /默认自动识别页面实际发起的接口来源/);
  assert.match(source, /自动确认仅覆盖页面直接发起的 fetch\/XHR/);
  assert.match(source, /HTTP 重定向和跨站登录暂不支持/);
  assert.match(source, /跨主机接口的当前直接请求/);
  assert.match(source, /高级设置/);
  assert.match(source, /aria-label="手动 API origin（可选）"/);
  assert.match(source, /awaiting_confirmation/);
  assert.match(source, /选择此来源/);
  assert.match(source, /resolve-origin/);
  assert.match(source, /browser-discovery-timeout-\$\{disabled \|\| creating\}/);
  assert.match(source, />开始探索</);
  assert.match(specs, /网页探索发现/);
  assert.match(workspace, /v-if="isBrowserSource"/);
  assert.match(workspace, /data-testid="api-workspace-source-documents"/);
  assert.match(workspace, /data-testid="api-workspace-source-browser"/);
  assert.match(workspace, /aria-selected/);
  assert.match(workspace, /data-testid="api-workspace-source-readonly"/);
  assert.match(workspace, /source_type: requestSourceType/);
  assert.match(workspace, /isDocumentSource\.value\n        \? getAPISpecifications/);
  assert.match(workspace, /isBrowserSource\.value\n        \? initializeBrowserDiscoveries/);
  assert.match(workspace, /requestSequence === reloadSequence/);
  assert.match(workspace, /requestViewEpoch === viewEpoch/);
  assert.match(workspace, /viewEpoch \+= 1/);
  assert.match(workspace, /browserDiscoveryFormDirty/);
  assert.match(workspace, /markCreateFormSubmitted/);
  assert.match(workspace, /resolveBrowserDiscoveryOrigin/);
  assert.match(workspace, /resolveSelectedBrowserDiscoveryOrigin/);
  assert.match(workspace, /loadBrowserDiscoveryRecords\(taskId\)/);
  assert.match(workspace, /browserDiscoveryBecameTerminal/);
  assert.match(workspace, /loadBrowserDiscoveryRecords\(taskId, \{ quiet: true \}\)/);
  assert.match(workspace, /browserDiscoveryOriginActionSequence/);
  assert.match(workspace, /deleteBrowserDiscoveryTask/);
  assert.match(workspace, /确认删除/);
  assert.match(workspace, /不会删除磁盘日志\/截图、其他任务、测试用例、工作区或已发布来源/);
  assert.match(workspace, /clearDeletedBrowserDiscovery/);
  assert.match(workspace, /browserDiscoveryDeleteSequence/);
  assert.match(source, /form-dirty-change/);
  assert.match(router, /path: 'workspace\/documents'/);
  assert.match(router, /path: 'workspace\/browser'/);
  assert.match(documents, /source-type="document"/);
  assert.match(browser, /source-type="browser_capture"/);
  assert.doesNotMatch(workspace, /selectedSpecValue|contextSpecs/);
});
