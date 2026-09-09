import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
  assert.match(source, /recordIds: selectedRecordIds\.value/);
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
