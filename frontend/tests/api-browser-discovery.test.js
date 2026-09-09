import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH,
  BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS,
  browserDiscoveryConfig,
  browserDiscoveryElapsed,
  browserDiscoveryErrorCodeLabel,
  browserDiscoveryItems,
  browserDiscoveryRecordIds,
  browserDiscoveryRecordGroups,
  browserDiscoveryTimeoutDefault,
  buildBrowserDiscoveryPayload,
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

test("browser discovery rejects stale project, epoch, sequence, and task responses", () => {
  const current = { requestProjectId: 1, currentProjectId: 1, requestEpoch: 4, currentEpoch: 4, requestSequence: 7, latestSequence: 7, expectedTaskId: 9, currentTaskId: 9 };
  assert.equal(shouldApplyBrowserDiscoveryResponse(current), true);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentProjectId: 2 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentEpoch: 5 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, latestSequence: 8 }), false);
  assert.equal(shouldApplyBrowserDiscoveryResponse({ ...current, currentTaskId: 10 }), false);
});

test("browser discovery handles elapsed fallback and only uses positive record ids", () => {
  assert.equal(browserDiscoveryElapsed({ started_at: "2026-09-09T00:00:00Z", finished_at: "2026-09-09T00:01:05Z" }), 65);
  assert.deepEqual(browserDiscoveryRecordIds([{ id: 1 }, { id: "2" }, { id: 0 }, { id: "x" }]), [1, 2]);
  const [group] = browserDiscoveryRecordGroups([{ id: 1, method: "get", path: "/orders", origin: "https://api.example.test", status_code: 200, is_eligible: true, dependency_record_ids: [3] }, { id: 2, method: "GET", path: "/orders", origin: "https://api.example.test", status_code: 200, is_eligible: false, exclusion_reason: "capture_incomplete" }]);
  assert.equal(group.count, 2);
  assert.deepEqual(group.eligibleRecordIds, [1]);
  assert.deepEqual(group.dependencyRecordIds, [3]);
});

test("browser discovery panel is feature-gated and hands off record ids with version", async () => {
  const [source, specs, workspace] = await Promise.all([
    readFile(new URL("../src/components/api-testing/BrowserDiscoveryPanel.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiSpecManage.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url), "utf8"),
  ]);
  assert.match(source, /v-if="enabled"/);
  assert.match(source, /allow_test_data_writes/);
  assert.match(source, /recordIds: selectedRecordIds\.value/);
  assert.match(source, /version: props\.task\?\.version/);
  assert.match(source, /公开脱敏摘要/);
  assert.match(source, /load-more-records/);
  assert.match(source, /provider_name \|\| model\?\.provider/);
  assert.match(source, /v-for="record in group.records"/);
  assert.match(source, /task\.summary/);
  assert.match(source, /active\(task\) && task\.cancellation_requested/);
  assert.match(source, /BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS/);
  assert.match(specs, /网页探索发现/);
  assert.match(workspace, /v-model="selectedSpecId"/);
  assert.doesNotMatch(workspace, /selectedSpecValue|contextSpecs/);
});
