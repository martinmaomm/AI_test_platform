import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { modelFailure, modelFailureStageLabel, canRetryBrowserModelFailure, browserModelRetryForm, workspaceModelRetryState } from "../src/utils/modelFailure.js";
import { browserDiscoveryErrorCategory, browserDiscoveryErrorCodeLabel } from "../src/utils/apiBrowserDiscovery.js";

const failure = { code: "MODEL_OVERLOADED", message: "模型服务过载", retryable: true, stage: "generating" };
test("only classified provider errors offer retry, not website failures or running tasks", () => {
  assert.equal(modelFailure({ error: "HTTP 503" }), null);
  assert.equal(modelFailure({ model_failure: { ...failure, code: "TOOL_FAILURE" } }), null);
  assert.equal(canRetryBrowserModelFailure({ status: "failed", model_failure: failure }), true);
  assert.equal(canRetryBrowserModelFailure({ status: "partial", model_failure: failure }), true);
  for (const status of ["running", "queued", "completed", "cancelled"])
    assert.equal(canRetryBrowserModelFailure({ status, model_failure: failure }), false);
  assert.equal(canRetryBrowserModelFailure({ status: "failed", model_failure: { ...failure, retryable: false } }), false);
  assert.equal(modelFailureStageLabel("initial_model"), "尚未开始网页操作");
  assert.match(browserDiscoveryErrorCategory({ error_code: "MODEL_OVERLOADED" }), /模型服务过载/);
  assert.match(browserDiscoveryErrorCodeLabel("MCP_OTHER"), /未分类/);
});

test("browser retry keeps original intent, budget and model but not auto-origin approvals", () => {
  const original = { target_url: "https://example.test", description: "测试描述", model_id: 3, exploration_timeout_seconds: 900, api_origin: "https://api.example.test", limits: { origin_mode: "auto" } };
  const values = browserModelRetryForm(original);
  assert.equal(values.description, original.description);
  assert.equal(values.model_id, 3);
  assert.equal(values.exploration_timeout_seconds, 900);
  assert.equal(values.api_origin, "");
  assert.equal(browserModelRetryForm({ ...original, limits: { origin_mode: "manual" } }).api_origin, original.api_origin);
});

test("workspace retry eligibility comes from server and does not depend on new prompt or candidate", () => {
  const workspace = { status: "failed", candidate: null, model_failure: failure, retry: { available: true, mode: "generate", scope: "scenario" } };
  assert.equal(workspaceModelRetryState(workspace).available, true);
  assert.match(workspaceModelRetryState(workspace).confirmation, /不会重新探索网页或重跑其他已通过场景/);
  assert.equal(workspaceModelRetryState({ ...workspace, retry: { available: false, reason: "请先选择失败子场景" } }).available, false);
  assert.equal(workspaceModelRetryState({ ...workspace, status: "generating" }).available, false);
  assert.equal(workspaceModelRetryState({ ...workspace, retry: { available: true, mode: "unknown" } }).available, false);
  assert.match(workspaceModelRetryState({ ...workspace, retry: { available: true, mode: "repair" } }).confirmation, /再次发送当前场景/);
});

test("retry uses explicit confirmation and revision, without resending messages or auto-saving", async () => {
  const view = await readFile(new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url), "utf8");
  const retry = view.split("const retryProviderGeneration = async () => {")[1].split("const prepareRootGeneration")[0];
  assert.match(retry, /await ElMessageBox.confirm/);
  assert.match(retry, /retryApiWorkspaceGeneration\(request.projectId, request.id, request.revision\)/);
  assert.doesNotMatch(retry, /sendApiWorkspaceMessage|createBrowserDiscovery|saveApiWorkspace/);
  const api = await readFile(new URL("../src/api/apiWorkspace.js", import.meta.url), "utf8");
  assert.match(api, /retry-generation/);
  assert.match(api, /revision, execution_confirmed: true/);
});
