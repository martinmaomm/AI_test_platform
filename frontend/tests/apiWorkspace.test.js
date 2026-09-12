import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  failureEvidence,
  normalizeDebugSteps,
} from "../src/components/api-workspace/debugResult.js";
import {
  availableChatModels,
  bodyKind,
  canGenerateWithModel,
  canRepairWorkspace,
  candidateAssertionReview,
  candidateDiff,
  childEditorEndpointIds,
  cleanupVariablesBeforeStep,
  completedApiSpecs,
  completedDocumentApiSpecs,
  debugHasFailure,
  defaultDraft,
  failureActionState,
  hasAvailableChatModel,
  generationContextMessage,
  generationDraftSummary,
  generationPhaseLabel,
  generationRepairDefaults,
  generationStatusMeta,
  isHttpUrl,
  isGenerationStale,
  latestGenerationDraft,
  listItems,
  normalizeDraft,
  normalizeCoverage,
  currentScenarioState,
  currentScenarioStatus,
  recoverableScenarioDraft,
  activeScenario,
  mergeRootWorkspaceMetadata,
  rootWorkspaceBusy,
  reconcileWorkspaceModel,
  savedCaseDescription,
  shouldApplyWorkspaceReload,
  shouldApplyWorkspaceModeResponse,
  shouldClearRootGenerationPrompt,
  shouldClearSubmittedMessage,
  updateWorkspaceListItem,
  workspaceNavigationSnapshot,
  workspaceMatchesSource,
  workspaceInitializationPlan,
  workspaceRouteForSource,
  workspaceSourceType,
  workspaceExecutionHistory,
} from "../src/views/api-testing/apiWorkspace.js";

test("current-revision debug evidence takes precedence over stale generation history", () => {
  const scenario = { id: 2, revision: 3, status: "ready", generation: { status: "stale", source_revision: 0 } };
  assert.equal(currentScenarioStatus(scenario), "stale");
  const passed = { ...scenario, debug_revision: 3, debug_result: { success: true } };
  assert.equal(currentScenarioStatus(passed), "passed");
  assert.equal(currentScenarioState({ scenarios: [passed] }).type, "success");
  const failed = { ...passed, debug_result: { success: false, status: "failed" } };
  assert.equal(currentScenarioStatus(failed), "failed");
  assert.equal(currentScenarioState({ scenarios: [failed] }).type, "danger");
  assert.equal(currentScenarioStatus({ ...passed, debug_revision: 2 }), "stale");
  assert.equal(currentScenarioStatus({ ...passed, status: "debugging", debug_result: { success: false, status: "partial" } }), "running");
  assert.equal(currentScenarioStatus({ ...passed, debug_result: { success: false, error_type: "Cancelled" } }), "cancelled");
});

test("empty editor recovery preserves generated steps and user config but never transfers verification", () => {
  const generated = {
    config: { name: "登录", base_url: "https://example.test", variables: { from_ai: 1, shared: "old" }, headers: { "X-Test": "kept" }, verify: true },
    teststeps: [{ request: { method: "GET", url: "/health", verify_ssl: true }, validate: [{ eq: ["status_code", 200] }] }],
  };
  const workspace = { generation: { rounds: [
    { attempt: 1, status: "passed", draft: generated, result: { success: true } },
    { attempt: 2, status: "needs_review", runnable: false, draft: { ...generated, teststeps: [{ request: { url: "/bad" } }] } },
  ] } };
  const draft = normalizeDraft({ config: { variables: { shared: "new" }, verify: false } });
  const recovery = recoverableScenarioDraft(workspace, draft);
  assert.equal(recovery.attempt, 1);
  assert.equal(recovery.draft.config.base_url, "https://example.test");
  assert.equal(recovery.draft.config.name, "登录");
  assert.deepEqual(recovery.draft.config.variables, { from_ai: 1, shared: "new" });
  assert.deepEqual(recovery.draft.config.headers, { "X-Test": "kept" });
  assert.equal(recovery.draft.teststeps[0].request.url, "/health");
  assert.ok(!("requires" in JSON.parse(JSON.stringify(recovery.draft)).teststeps[0]), "ordinary steps must not carry cleanup-only fields");
  assert.ok(!("verify" in recovery.draft.config));
  assert.ok(!("verify_ssl" in recovery.draft.teststeps[0].request));
  assert.ok(!("result" in recovery));
  assert.equal(draft.teststeps.length, 0);
  assert.equal(generated.config.verify, true, "reading recovery must not mutate historical evidence");
  assert.equal(recoverableScenarioDraft(workspace, normalizeDraft(generated)), null);
  assert.equal(recoverableScenarioDraft({ generation: { rounds: [{ draft: generated, runnable: false }] } }, draft), null);
});

test("visual editor explains filter uniqueness, explicit indexes, and length_gt assertions", async () => {
  const selector = 'body.data[?(@.name == ${unique_name})]';
  const draft = normalizeDraft({teststeps: [{
    request: {method: "GET", url: "/items"},
    extract: {id: `${selector}[0].id`},
    validate: [{length: [selector, 0]}],
  }]});
  assert.equal(draft.teststeps[0].extract.id, `${selector}[0].id`);
  assert.deepEqual(draft.teststeps[0].validate, [{length: [selector, 0]}]);
  const editor = await readFile(new URL("../src/components/api-workspace/VisualStepEditor.vue", import.meta.url), "utf8");
  for (const value of ["eq", "ne", "contains", "not_contains", "gt", "ge", "lt", "le", "type", "length", "length_gt"]) {
    assert.ok(editor.includes(`value="${value}"`), `missing comparator: ${value}`);
  }
  assert.ok(editor.includes("条件筛选提取必须恰好匹配一条"));
  assert.ok(editor.includes("零条或多条会停止"));
  assert.ok(editor.includes("body.data[0].id"));
  assert.ok(editor.includes("body.data.0.id"));
  assert.ok(editor.includes("即使后续用于 POST 等写请求也不要求列表只有一条"));
  assert.ok(editor.includes("列表顺序可能变化"));
  assert.ok(editor.includes("针对指定业务对象优先使用唯一条件筛选"));
  assert.ok(editor.includes("不要直接提取未指定索引的整个列表用于后续写请求"));
  assert.ok(editor.includes("长度等于 0"));
  assert.ok(editor.includes("阈值 0 表示非空"));
  assert.ok(editor.includes("type</code> 为 <code>list"));
  assert.match(editor, /aria-label="`断言 \$\{assertionIndex \+ 1\} 操作符`"/);
  assert.match(editor, /aria-label="`断言 \$\{assertionIndex \+ 1\} 目标`"/);
  assert.match(editor, /:label="`断言 \$\{assertionIndex \+ 1\} 期望值`"/);
  assert.match(editor, /<AssertionExpectedInput/);
  const expectedInput = await readFile(new URL("../src/components/api-workspace/AssertionExpectedInput.vue", import.meta.url), "utf8");
  assert.match(expectedInput, /:aria-label="label"/);
  assert.match(expectedInput, /data-testid="assertion-expected-type"/);
  assert.doesNotMatch(editor, /normalizeLengthGtExpected/);
});

test("API workspace draft remains structured and preserves body kinds", () => {
  const draft = normalizeDraft({
    config: { name: "登录", variables: { retry: 2 } },
    teststeps: [
      {
        request: {
          method: "post",
          url: "/login",
          json: { account: "${user}" },
        },
      },
      { request: { data: { username: "demo" } } },
      { request: { raw: "raw body" } },
    ],
  });
  assert.equal(draft.config.variables.retry, 2);
  assert.equal(draft.teststeps[0].request.method, "POST");
  assert.equal(bodyKind(draft.teststeps[0].request), "json");
  assert.equal(bodyKind(draft.teststeps[1].request), "form");
  assert.equal(bodyKind(draft.teststeps[2].request), "raw");
  assert.equal(normalizeDraft({}).teststeps.length, 0);
  assert.equal(defaultDraft().version, 1);
  assert.deepEqual(listItems({ data: { results: [{ id: 1 }] } }), [{ id: 1 }]);
  assert.deepEqual(listItems({ data: [{ id: 2 }] }), [{ id: 2 }]);
});

test("candidate display names structural changes without applying a draft", () => {
  const current = defaultDraft();
  const candidate = {
    draft: {
      ...current,
      config: { ...current.config, base_url: "https://example.test" },
      teststeps: [
        {
          name: "查询",
          request: {
            method: "GET",
            url: "/me",
            headers: { Authorization: "Bearer next" },
          },
          extract: { user_id: "body.id" },
        },
      ],
    },
  };
  const changes = candidateDiff(current, candidate);
  assert.ok(changes.some((item) => item.includes("基础地址")));
  assert.ok(changes.some((item) => item.includes("步骤数量")));
  assert.ok(
    changes.some(
      (item) =>
        item === "新增步骤 1：查询（GET /me；断言 0 条，提取 1 项）",
    ),
  );
  assert.doesNotMatch(changes.join("\n"), /Authorization/);
  assert.equal(current.config.base_url, "");
});

test("protected assertion candidates require their exact draft hash acknowledgement", () => {
  assert.deepEqual(
    candidateAssertionReview({
      draft_hash: "candidate-v2",
      review: {
        requires_confirmation: true,
        changes: ["步骤 1 将状态码断言从 200 调整为 201"],
        warnings: ["该调整仅适用于新建资源响应"],
      },
    }),
    {
      requiresConfirmation: true,
      changes: ["步骤 1 将状态码断言从 200 调整为 201"],
      warnings: ["该调整仅适用于新建资源响应"],
      draftHash: "candidate-v2",
    },
  );
  assert.equal(candidateAssertionReview({ review: { requires_confirmation: true } }).draftHash, null);
  assert.equal(candidateAssertionReview({ review: { requires_confirmation: false } }).requiresConfirmation, false);
});

test("cleanup steps only expose variables extracted by earlier steps", () => {
  const steps = [
    { extract: { token: "body.token", record_id: "body.id" } },
    { extract: { cleanup_key: "body.cleanup_key" } },
    { phase: "cleanup", requires: ["token"] },
  ];
  assert.deepEqual(cleanupVariablesBeforeStep(steps, 0), []);
  assert.deepEqual(cleanupVariablesBeforeStep(steps, 1), ["token", "record_id"]);
  assert.deepEqual(cleanupVariablesBeforeStep(steps, 2), ["token", "record_id", "cleanup_key"]);
  assert.equal(normalizeDraft({ teststeps: [steps[2]] }).teststeps[0].phase, "cleanup");
});

test("workspace execution history accepts only standard positive execution ids", () => {
  assert.deepEqual(
    workspaceExecutionHistory({
      execution_history: [
        { id: 9, status: "passed", source: "workspace_generation" },
        { id: "10", status: "failed", source: "workspace_debug" },
        { id: 0, status: "passed" },
        null,
      ],
    }).map((item) => item.id),
    [9, "10"],
  );
  assert.deepEqual(workspaceExecutionHistory({ execution_history: {} }), []);
});

test("repair is enabled only for actual failed debug evidence", () => {
  assert.equal(debugHasFailure({ status: "passed" }), false);
  assert.equal(debugHasFailure({ status: "failed" }), true);
  assert.equal(
    debugHasFailure({ steps: [{ status: "passed" }, { status: "skipped" }] }),
    false,
  );
  assert.equal(debugHasFailure({ steps: [{ status: "failed" }] }), true);
});

test("DebugResultPanel keeps successful exports and displays per-step extraction results", () => {
  const [step] = normalizeDebugSteps({
    steps: [
      {
        status: "failed",
        export_vars: { access_token: "ok" },
        extraction_results: {
          access_token: { path: "$.token", status: "passed", value: "ok" },
          user_id: { path: "$.user.id", status: "failed", error: "not found" },
        },
      },
    ],
  });
  assert.deepEqual(step.exportVariables, { access_token: "ok" });
  assert.deepEqual(step.extractionResults, {
    access_token: { path: "$.token", status: "passed", value: "ok" },
    user_id: { path: "$.user.id", status: "failed", error: "not found" },
  });
});

test("failure evidence safely retains failed steps and assertion actual/expected values", () => {
  const result = {
    success: false,
    step_datas: [
      {
        name: "读取订单",
        status: "failed",
        error: "状态码不匹配",
        validators: {
          validate_extractor: [
            {
              passed: false,
              check: "status_code",
              comparator: "eq",
              expect_value: 200,
              check_value: 503,
            },
          ],
        },
      },
    ],
  };
  assert.equal(debugHasFailure(result), true);
  assert.deepEqual(failureEvidence(result), [
    {
      name: "读取订单",
      error: "状态码不匹配",
      assertions: [
        {
          passed: false,
          check: "status_code",
          comparator: "eq",
          expect_value: 200,
          check_value: 503,
        },
      ],
    },
  ]);
});

test("selected failed scenarios expose only safe, explicitly gated recovery actions", () => {
  const generation = {
    status: "needs_review",
    source_revision: 4,
    rounds: [{ result: { success: false, steps: [{ status: "failed" }] } }],
  };
  const candidate = { source_revision: 4, draft: { teststeps: [{ name: "候选步骤" }] } };
  const ready = failureActionState({
    editingScenario: true,
    generation,
    workspaceRevision: 4,
    draft: { teststeps: [] },
    candidate,
    debugResult: { success: false },
    debugRevision: 4,
    modelAvailable: true,
    canRepair: true,
  });
  assert.equal(ready.visible, true);
  assert.equal(ready.view.disabled, false);
  assert.equal(ready.repair.disabled, false);
  assert.equal(ready.manual.disabled, false);
  assert.equal(ready.manual.usesCandidate, true);

  const staticOnly = failureActionState({
    editingScenario: true,
    generation: {
      status: "failed",
      source_revision: 4,
      rounds: [{ status: "failed", summary: "候选 JSON 缺少请求地址" }],
    },
    workspaceRevision: 4,
    draft: { teststeps: [] },
    modelAvailable: true,
  });
  assert.equal(staticOnly.view.disabled, false);
  assert.equal(staticOnly.view.hasRoundEvidence, true);
  assert.equal(staticOnly.repair.disabled, true);
  assert.match(staticOnly.repair.reason, /静态失败/);
  assert.equal(staticOnly.manual.disabled, true);
  assert.equal(staticOnly.manual.usesCandidate, false);
  assert.match(staticOnly.manual.reason, /草稿没有步骤/);

  const dirty = failureActionState({
    editingScenario: true,
    generation,
    workspaceRevision: 4,
    draft: { teststeps: [{ name: "本地步骤" }] },
    candidate,
    dirty: true,
    modelAvailable: true,
    canRepair: false,
  });
  assert.equal(dirty.repair.disabled, true);
  assert.match(dirty.repair.reason, /当前草稿已修改/);
  assert.equal(dirty.manual.disabled, false);
  assert.equal(dirty.manual.usesCandidate, false);
  assert.match(dirty.manual.note, /不会覆盖本地编辑/);

  const dirtyEmptyDraft = failureActionState({
    editingScenario: true,
    generation,
    workspaceRevision: 4,
    draft: { teststeps: [] },
    candidate,
    dirty: true,
    modelAvailable: true,
  });
  assert.equal(dirtyEmptyDraft.manual.disabled, true);
  assert.match(dirtyEmptyDraft.manual.reason, /草稿没有步骤/);

  const unavailable = failureActionState({
    editingScenario: true,
    generation,
    workspaceRevision: 4,
    draft: { teststeps: [] },
    modelAvailable: false,
  });
  assert.equal(unavailable.repair.disabled, true);
  assert.match(unavailable.repair.reason, /没有可用聊天模型/);

  const busy = failureActionState({
    editingScenario: true,
    generation,
    workspaceRevision: 4,
    draft: { teststeps: [] },
    busy: true,
    modelAvailable: true,
    canRepair: true,
  });
  assert.equal(busy.view.disabled, true);
  assert.equal(busy.repair.disabled, true);
  assert.equal(busy.manual.disabled, true);
  assert.match(busy.repair.reason, /仍在处理/);
  assert.equal(
    failureActionState({ editingScenario: false, generation }).visible,
    false,
  );
  assert.equal(
    failureActionState({
      editingScenario: true,
      workspaceRevision: null,
      debugRevision: null,
      debugResult: { success: false },
    }).visible,
    false,
  );
});

test("workspace only permits active LLM models and clears an unavailable saved model", () => {
  const models = availableChatModels({
    data: [
      { id: 1, is_active: true, model_type: "llm" },
      { id: 2, is_active: false, model_type: "llm" },
      { id: 3, is_active: true, model_type: "embedding" },
      { id: 4, is_active: false, model_type: "embedding" },
    ],
  });
  assert.deepEqual(
    models.map((model) => model.id),
    [1],
  );
  assert.equal(hasAvailableChatModel(models, 1), true);
  assert.equal(hasAvailableChatModel(models, 2), false);
  assert.equal(canGenerateWithModel(models, 1, true, false), true);
  assert.equal(canGenerateWithModel(models, 2, true, false), false);
  assert.equal(canGenerateWithModel(models, 1, false, false), false);
  assert.equal(canGenerateWithModel([], null, true, false), false);
  assert.deepEqual(reconcileWorkspaceModel(models, 1, true), {
    modelId: 1,
    unavailable: false,
  });
  assert.deepEqual(reconcileWorkspaceModel(models, 2, true), {
    modelId: null,
    unavailable: true,
  });
  assert.deepEqual(reconcileWorkspaceModel(models, 3, true), {
    modelId: null,
    unavailable: true,
  });
  assert.deepEqual(reconcileWorkspaceModel(models, 2, false), {
    modelId: 2,
    unavailable: false,
  });
  assert.deepEqual(reconcileWorkspaceModel(models, null, true, true), {
    modelId: null,
    unavailable: true,
  });
  assert.deepEqual(reconcileWorkspaceModel(models, 1, true, true), {
    modelId: 1,
    unavailable: false,
  });
});

test("workspace initialization honors explicit targets before history", () => {
  const history = [{ id: 9 }, { id: 8 }];
  assert.deepEqual(
    workspaceInitializationPlan({ workspace_id: "7", case_id: "4" }, history),
    { action: "load", workspaceId: 7, explicit: true },
  );
  assert.deepEqual(
    workspaceInitializationPlan({ case_id: "4", endpoint_id: "5" }, history),
    { action: "create", caseId: 4, endpointId: 5, explicit: true },
  );
  assert.deepEqual(workspaceInitializationPlan({}, history), {
    action: "load",
    workspaceId: 9,
    explicit: false,
  });
  assert.equal(
    workspaceInitializationPlan({ workspace_id: "bad" }, history).action,
    "invalid",
  );
  assert.equal(
    workspaceInitializationPlan({ endpoint_id: ["5"] }, history).action,
    "invalid",
  );
});

test("workspace source routes isolate document and browser history", () => {
  assert.deepEqual(
    completedDocumentApiSpecs({
      data: [
        { id: 1, status: "completed", spec_type: "openapi" },
        { id: 2, status: "completed", spec_type: "browser_capture" },
        { id: 3, status: "running", spec_type: "openapi" },
      ],
    }),
    [{ id: 1, status: "completed", spec_type: "openapi" }],
  );
  assert.equal(workspaceSourceType({}), "document");
  assert.equal(workspaceSourceType({ source_type: "browser_capture" }), "browser_capture");
  assert.equal(workspaceMatchesSource({ source_type: "browser_capture" }, "browser_capture"), true);
  assert.equal(workspaceMatchesSource({ source_type: "document" }, "browser_capture"), false);
  assert.equal(workspaceRouteForSource("document"), "/api-testing/workspace/documents");
  assert.equal(workspaceRouteForSource("browser_capture"), "/api-testing/workspace/browser");
  assert.deepEqual(
    workspaceInitializationPlan({}, [], { sourceType: "browser_capture" }),
    { action: "none", explicit: false },
  );
  assert.deepEqual(
    workspaceInitializationPlan({ case_id: "4" }, [], { sourceType: "browser_capture" }),
    { action: "documents", caseId: 4, endpointId: null, explicit: true },
  );
  assert.equal(
    shouldApplyWorkspaceModeResponse({
      requestSourceType: "browser_capture",
      currentSourceType: "document",
    }),
    false,
  );
});

test("workspace save metadata and reload guards preserve the intended local state", () => {
  assert.equal(savedCaseDescription({ saved_case_description: "已有描述" }), "已有描述");
  assert.equal(savedCaseDescription({}), "");
  assert.deepEqual(
    updateWorkspaceListItem(
      [{ id: 1, title: "旧标题" }, { id: 2, title: "其他" }],
      { id: 1, title: "新标题", saved_case_description: "描述" },
    ),
    [
      { id: 1, title: "新标题", saved_case_description: "描述" },
      { id: 2, title: "其他" },
    ],
  );
  assert.equal(
    shouldApplyWorkspaceReload({
      requestProjectId: 1,
      currentProjectId: 1,
      requestSequence: 3,
      latestSequence: 3,
      dirty: true,
      confirmedSnapshot: "accepted-draft",
      currentSnapshot: "accepted-draft",
    }),
    true,
  );
  assert.equal(
    shouldApplyWorkspaceReload({
      requestProjectId: 1,
      currentProjectId: 1,
      requestSequence: 3,
      latestSequence: 3,
      dirty: true,
      confirmedSnapshot: "accepted-draft",
      currentSnapshot: "new-edit",
    }),
    false,
  );
  assert.equal(
    shouldApplyWorkspaceReload({
      requestProjectId: 1,
      currentProjectId: 2,
      requestSequence: 3,
      latestSequence: 3,
      dirty: false,
    }),
    false,
  );
  assert.equal(shouldClearSubmittedMessage(true, "输入", "输入"), true);
  assert.equal(shouldClearSubmittedMessage(false, "输入", "输入"), false);
  assert.equal(shouldClearSubmittedMessage(true, "输入", "后续输入"), false);
  assert.equal(shouldClearRootGenerationPrompt(true, "目标", "目标  \n"), true);
  assert.equal(shouldClearRootGenerationPrompt(true, "目标", "后续目标 "), false);
});

test("navigation snapshot distinguishes automatic defaults from user edits", () => {
  const baseline = workspaceNavigationSnapshot({
    draft: defaultDraft(),
    modelId: null,
    specId: 7,
    endpointIds: [12, 11],
  });
  assert.equal(
    workspaceNavigationSnapshot({
      draft: defaultDraft(),
      modelId: null,
      specId: 7,
      endpointIds: [11, 12],
    }),
    baseline,
    "automatic endpoint selection has a stable baseline",
  );
  assert.notEqual(
    workspaceNavigationSnapshot({
      draft: {
        ...defaultDraft(),
        config: { ...defaultDraft().config, name: "已编辑" },
      },
      modelId: null,
      specId: 7,
      endpointIds: [11, 12],
    }),
    baseline,
    "draft edits remain protected",
  );
  assert.notEqual(
    workspaceNavigationSnapshot({
      draft: defaultDraft(),
      modelId: 3,
      specId: 7,
      endpointIds: [11, 12],
    }),
    baseline,
    "manual model selection remains protected",
  );
  assert.notEqual(
    workspaceNavigationSnapshot({
      draft: defaultDraft(),
      modelId: null,
      specId: 7,
      endpointIds: [11, 12],
      scenarioModelId: 4,
    }),
    baseline,
    "manual scenario model selection remains protected",
  );
  assert.equal(
    workspaceNavigationSnapshot({
      draft: defaultDraft(),
      modelId: null,
      specId: 7,
      endpointIds: [12, 11],
    }),
    baseline,
    "returning content to its baseline clears the navigation prompt",
  );
});

test("adopted verification stays current until the draft or context changes", () => {
  const passedGeneration = {
    status: "passed",
    source_revision: 4,
    adopted_revision: 5,
  };
  assert.equal(isGenerationStale(passedGeneration, 5, false), false);
  assert.equal(isGenerationStale(passedGeneration, 5, true), true);
  assert.equal(isGenerationStale(passedGeneration, 6, false), true);
});

test("repair accepts only current failure evidence and preserves failed candidate defaults", () => {
  const failedGeneration = {
    status: "failed",
    source_revision: 4,
    target_url: "https://failed-target.example.test",
    rounds: [{ draft: { teststeps: [{ name: "失败步骤" }] }, result: { status: "failed", success: false } }],
  };
  assert.equal(
    canRepairWorkspace({
      generation: { ...failedGeneration, rounds: [] },
      workspaceRevision: 4,
    }),
    false,
    "No candidate or execution evidence means regenerate, not a broken repair action",
  );
  assert.equal(
    canRepairWorkspace({
      dirty: false,
      generation: failedGeneration,
      workspaceRevision: 4,
    }),
    true,
  );
  assert.equal(
    canRepairWorkspace({
      dirty: false,
      generation: failedGeneration,
      workspaceRevision: 5,
    }),
    false,
  );
  assert.equal(
    canRepairWorkspace({
      dirty: false,
      generation: failedGeneration,
      workspaceRevision: 5,
      debugRevision: 5,
      debugResult: { status: "failed" },
    }),
    true,
  );
  assert.equal(
    canRepairWorkspace({
      dirty: true,
      generation: failedGeneration,
      workspaceRevision: 4,
      debugRevision: 4,
      debugResult: { status: "failed" },
    }),
    false,
  );
  assert.deepEqual(
    generationRepairDefaults({
      generation: failedGeneration,
      candidate: {
        draft: { config: { base_url: "https://candidate.example.test", variables: { run: "old" } } },
      },
      draft: { config: { base_url: "https://draft.example.test" } },
      useGenerationEvidence: true,
    }),
    { base_url: "https://failed-target.example.test", variables: { run: "old" } },
  );
  assert.deepEqual(
    generationRepairDefaults({
      generation: { target_url: "https://prior-run.example.test" },
      draft: { config: { base_url: "https://draft.example.test", variables: { tenant: "qa" } } },
      useGenerationEvidence: true,
    }),
    { base_url: "https://prior-run.example.test", variables: { tenant: "qa" } },
  );
});

test("generation helpers ignore incomplete specs and empty final rounds", () => {
  assert.deepEqual(
    completedApiSpecs({
      data: [
        { id: 1, status: "completed" },
        { id: 2, status: "running" },
        { id: 3, status: "failed" },
      ],
    }),
    [{ id: 1, status: "completed" }],
  );
  const usableDraft = { config: { name: "有效候选" }, teststeps: [{ name: "步骤" }] };
  assert.equal(
    latestGenerationDraft(
      { rounds: [{ draft: usableDraft }, { draft: {} }] },
      { draft: { config: { name: "备用" }, teststeps: [{ name: "备用步骤" }] } },
    ),
    usableDraft,
  );
});

test("multi-scenario helpers keep root metadata and distinguish current stale scenarios", () => {
  const root = {
    id: 10,
    title: "健康检查",
    revision: 2,
    scenarios: [
      { id: 21, status: "passed" },
      { id: 22, status: "stale", generation: { status: "passed" }, draft: { config: { name: "本地编辑" } } },
    ],
    generation: { status: "passed", scenario_ids: [22] },
  };
  assert.deepEqual(currentScenarioState(root), {
    label: "有场景已修改待重验",
    type: "warning",
    stale: true,
  });
  assert.equal(activeScenario(root).id, 22);
  assert.equal(rootWorkspaceBusy({ scenarios: [{ generation: { status: "running" } }] }), true);
  assert.deepEqual(normalizeCoverage({ total: 3, planned: 2, verified: 1 }), {
    total: 3,
    planned: 2,
    generated: 0,
    verified: 1,
    uncovered_endpoint_ids: [],
    planned_endpoint_ids: [],
    generated_endpoint_ids: [],
    verified_endpoint_ids: [],
  });
  const renamed = mergeRootWorkspaceMetadata(root, {
    id: 10,
    title: "新的根名称",
    revision: 3,
    updated_at: "2026-09-07T10:00:00Z",
  });
  assert.equal(renamed.title, "新的根名称");
  assert.equal(renamed.scenarios[1].draft.config.name, "本地编辑");
  assert.equal(root.scenarios[1].draft.config.name, "本地编辑");
});

test("child editor keeps frozen dependency endpoints separate from target coverage", () => {
  const child = {
    endpoint_ids: [20],
    generation: {
      scenario_context: {
        target_endpoint_ids: [20],
        available_endpoint_ids: [10, 20, 30],
        dependency_endpoint_ids: [10],
      },
    },
    draft: { teststeps: [{ endpoint_id: 10 }, { endpoint_id: 40 }] },
    candidate: { draft: { teststeps: [{ endpoint_id: 30 }, { endpoint_id: 50 }] } },
  };
  assert.deepEqual(childEditorEndpointIds(child), [20, 10, 30, 40, 50]);
  assert.deepEqual(
    childEditorEndpointIds({
      endpoint_ids: [20],
      draft: { teststeps: [{ endpoint_id: 10 }] },
      candidate: { draft: { teststeps: [{ endpoint_id: 30 }] } },
    }),
    [20],
    "legacy children keep the original target-only endpoint scope",
  );
});

test("generation draft summary safely exposes malformed raw candidate structures", () => {
  const summary = generationDraftSummary({
    config: 42,
    teststeps: [null, 42, { name: "有效步骤", request: "bad request" }],
  });
  assert.equal(summary.name, "未命名候选场景");
  assert.deepEqual(summary.steps, [
    { name: "有效步骤", method: "GET", url: "/" },
  ]);
  assert.ok(summary.errors.includes("候选 config 不是对象。"));
  assert.ok(summary.errors.includes("步骤 1 不是对象。"));
  assert.ok(summary.errors.includes("步骤 2 不是对象。"));
  assert.ok(summary.errors.includes("步骤 3 的 request 不是对象。"));
  assert.doesNotThrow(() =>
    candidateDiff(defaultDraft(), { draft: { config: 42, teststeps: [null, 42] } }),
  );
});

test("generation confirmation only accepts complete HTTP targets and documented endpoint context", () => {
  assert.equal(isHttpUrl("https://api.example.test/v1"), true);
  assert.equal(isHttpUrl("http://localhost:8080"), true);
  assert.equal(isHttpUrl("api.example.test"), false);
  assert.equal(isHttpUrl("ftp://api.example.test"), false);
  assert.equal(
    generationContextMessage({ specId: 1, specAvailable: true, endpointIds: [2] }),
    "",
  );
  assert.match(
    generationContextMessage({ specId: null, endpointIds: [2] }),
    /请选择 API 规范/,
  );
  assert.match(
    generationContextMessage({
      specId: 1,
      specAvailable: true,
      endpointIds: [],
    }),
    /至少选择一个/,
  );
  assert.match(
    generationContextMessage({
      specId: 1,
      specAvailable: true,
      endpointIds: Array.from({ length: 51 }, (_, index) => index),
    }),
    /最多选择 50 个/,
  );
  assert.deepEqual(generationStatusMeta("passed"), {
    label: "已验证通过",
    type: "success",
  });
  assert.equal(generationPhaseLabel("running"), "正在验证请求与断言");
});

test("workspace API, routing, navigation, and suite variables use the approved contract", async () => {
  const [
    api,
    legacyApi,
    router,
    mainLayout,
    suites,
    endpointCases,
    specDetail,
    workspace,
    debugPanel,
    verificationPanel,
    configEditor,
    conversation,
  ] = await Promise.all([
    readFile(new URL("../src/api/apiWorkspace.js", import.meta.url), "utf8"),
    readFile(new URL("../src/api/apiTesting.js", import.meta.url), "utf8"),
    readFile(new URL("../src/router/index.js", import.meta.url), "utf8"),
    readFile(new URL("../src/layouts/MainLayout.vue", import.meta.url), "utf8"),
    readFile(
      new URL("../src/views/api-testing/TestSuites.vue", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../src/views/api-testing/EndpointTestCases.vue",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL("../src/views/api-testing/APISpecDetail.vue", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../src/components/api-workspace/DebugResultPanel.vue",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../src/components/api-workspace/GenerationVerificationPanel.vue",
        import.meta.url,
      ),
      "utf8",
    ),
      readFile(
        new URL(
          "../src/components/api-workspace/WorkspaceConfigEditor.vue",
          import.meta.url,
        ),
        "utf8",
      ),
      readFile(
        new URL(
          "../src/components/api-workspace/WorkspaceConversation.vue",
          import.meta.url,
        ),
        "utf8",
      ),
  ]);
  assert.match(api, /`\/projects\/\$\{projectId\}\/api-testing\/workspaces\/`/);
  assert.match(api, /\$\{workspaceId\}\/messages\//);
  assert.match(api, /\$\{workspaceId\}\/debug\//);
  assert.match(api, /\$\{workspaceId\}\/save\//);
  assert.match(api, /\$\{workspaceId\}\/python\//);
  assert.match(api, /data: \{ revision, confirmed: true \}/);
  assert.match(router, /path: 'workspace', name: 'ApiWorkspace'/);
  assert.match(
    router,
    /path: 'workspace\/documents'.*title: 'API 对话工作区 · 接口文档'.*cache: false/,
  );
  assert.match(
    router,
    /path: 'workspace\/browser'.*title: 'API 对话工作区 · 网页探索'.*cache: false/,
  );
  assert.match(router, /path: '', redirect: '\/api-testing\/workspace'/);
  assert.match(
    router,
    /path: 'function-navigation', redirect: '\/api-testing\/workspace'/,
  );
  assert.doesNotMatch(router, /FunctionNavigation/);
  assert.doesNotMatch(mainLayout, /API功能导航/);
  assert.match(
    mainLayout,
    /if \(p === '\/api-testing'\) return '\/api-testing\/workspace'/,
  );
  assert.match(mainLayout, /currentRoute\.meta\.cache === false/);
  assert.match(mainLayout, /<keep-alive :max="15">/);
  assert.match(mainLayout, /v-if="currentRoute\.meta\.cache !== false"/);
  assert.doesNotMatch(mainLayout, /<keep-alive v-else/);
  assert.match(mainLayout, /currentRoute\.path/);
  assert.match(mainLayout, /const refreshScope =/);
  assert.match(suites, /v-model="suiteForm\.variables"/);
  assert.match(suites, /套件变量会覆盖用例变量/);
  assert.match(endpointCases, /query: \{ case_id: selectedList\[0\]\.id \}/);
  assert.match(specDetail, /query: \{ endpoint_id: endpoint\.id \}/);
  assert.match(workspace, /<main v-if="workspaceReady"/);
  assert.doesNotMatch(workspace, /label="工作区标题"/);
  assert.doesNotMatch(workspace, /:model-value="workspace\.title" disabled/);
  assert.match(workspace, /未命名工作区 #\$\{item\.id\}/);
  assert.match(workspace, /await router\.replace\(/);
  assert.match(workspace, /const pollWorkspace = async/);
  assert.match(workspace, /python\.value\.workspaceId/);
  assert.match(workspace, /payload\.revision != null/);
  assert.match(workspace, /availableChatModels\(modelsResult\.value\)/);
  assert.match(workspace, /reconcileWorkspaceModel/);
  assert.match(workspace, /ensureAvailableChatModel/);
  assert.match(workspace, /:generation-disabled="conversationGenerationDisabled"/);
  assert.match(workspace, /mergeRootWorkspaceMetadata\(rootWorkspace\.value, next\)/);
  assert.match(workspace, /保存工作区设置/);
  assert.match(workspace, /保存子场景模型/);
  assert.match(workspace, /:allow-scenario-regenerate="editingScenario"/);
  assert.match(workspace, /scenarioModelDirty\.value/);
  assert.match(workspace, /editingScenario\.value && mode === "generate"/);
  assert.match(workspace, /workspace-select-label/);
  assert.match(workspace, /execution_confirmed: true/);
  assert.match(workspace, /title="生成并验证确认"/);
  assert.match(workspace, /label="目标地址"/);
  assert.match(workspace, /aria-label="目标地址"/);
  assert.match(workspace, />确认并开始验证</);
  assert.match(workspace, /spec_id: selectedSpecId\.value/);
  assert.match(workspace, /GenerationVerificationPanel/);
  assert.match(workspace, /:failure-actions="failureActions"/);
  assert.match(workspace, /@view-failure="viewFailureEvidence"/);
  assert.match(workspace, /@repair="focusRepairConversation"/);
  assert.match(workspace, /@manual-edit="openManualEditor"/);
  assert.match(workspace, /const openManualEditor = async/);
  assert.match(workspace, /const focusRepairConversation = async/);
  assert.match(workspace, /const adoptingCandidate = ref\(false\)/);
  assert.match(workspace, /adoptingCandidate\.value \|\|/);
  assert.match(workspace, /adoptingCandidate\.value = true/);
  assert.match(workspace, /contextDirty\.value/);
  assert.match(workspace, /resetNavigationWorkspaceBaseline\(\["draft", "scenarioModelId"\]\)/);
  assert.match(workspace, /resetNavigationWorkspaceBaseline\(\["modelId", "specId", "endpointIds"\]\)/);
  assert.match(workspace, /Boolean\(rootPrompt\.value\.trim\(\)\)/);
  assert.match(workspace, /rootPrompt\.value = "请基于需求历史.*?";\s+resetRootPromptBaseline\(\)/);
  assert.match(workspace, /const switchedRootWorkspace =/);
  assert.match(workspace, /if \(switchedRootWorkspace\) \{\s+rootPrompt\.value = "";/s);
  assert.match(workspace, /:key="workspace\.id"/);
  assert.match(workspace, /@dirty-change="conversationDirty = \$event"/);
  assert.match(workspace, /execution_confirmed: true/);
  assert.match(workspace, /:send-message="prepareGeneration"/);
  assert.match(workspace, /workspaceInitializationPlan/);
  assert.match(workspace, /savedCaseDescription\(workspace\.value\)/);
  assert.match(workspace, /重新选择可用聊天模型/);
  assert.match(conversation, /generationDisabled/);
  assert.match(conversation, /props\.generationDisabled/);
  assert.match(conversation, /await props\.sendMessage/);
  assert.match(conversation, /const focusInput = async/);
  assert.match(conversation, /defineEmits\(\["adopt", "dirty-change"\]\)/);
  assert.doesNotMatch(conversation, /discardPendingMessage/);
  assert.match(conversation, /onBeforeUnmount\(\(\) => emit\("dirty-change", false\)\)/);
  assert.match(conversation, /submitting/);
  assert.match(conversation, />生成并验证</);
  assert.match(conversation, />重新生成本场景</);
  assert.match(conversation, />修复并验证</);
  assert.match(debugPanel, /result\.log/);
  assert.match(debugPanel, /step\.extractionResults/);
  assert.match(debugPanel, /导出变量/);
  assert.match(debugPanel, /step\?\.status\) === "skipped"/);
  assert.match(verificationPanel, /data-testid="api-generation-verification"/);
  assert.match(verificationPanel, /data-testid="api-scenario-failure-actions"/);
  assert.match(verificationPanel, />查看失败原因</);
  assert.match(verificationPanel, />AI 修复</);
  assert.match(verificationPanel, />手动编辑</);
  assert.match(verificationPanel, /defineEmits\(\["view-failure", "repair", "manual-edit"\]\)/);
  assert.match(verificationPanel, /const showFailureEvidence =/);
  assert.match(verificationPanel, /v-if="generation\?\.status \|\| failureActions\.visible"/);
  assert.match(verificationPanel, /data-testid="api-scenario-failure-summary"/);
  assert.match(verificationPanel, /实际 <span v-text="displayValue\(assertion\.check_value\)"/);
  assert.match(verificationPanel, /预期 <span v-text="displayValue\(assertion\.expect_value \?\? assertion\.expect\)"/);
  assert.match(verificationPanel, /第 \{\{ round\.attempt/);
  assert.match(configEditor, /timestamp_ns/);
  assert.match(configEditor, /uuid4/);
  assert.doesNotMatch(legacyApi, /generateSpecTestCases/);
  assert.doesNotMatch(legacyApi, /generateEndpointTestCases/);
  assert.doesNotMatch(legacyApi, /generateScenario/);
  assert.doesNotMatch(specDetail, /generateEndpointTestCases\(/);
  assert.doesNotMatch(specDetail, /generateSpecTestCases\(/);
});

test("workspace reliability UI uses cancellation, standard report history, and explicit assertion acknowledgement", async () => {
  const [api, workspace, history, conversation, verification, editor, debug] = await Promise.all([
    readFile(new URL("../src/api/apiWorkspace.js", import.meta.url), "utf8"),
    readFile(new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/components/api-workspace/WorkspaceExecutionHistory.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/components/api-workspace/WorkspaceConversation.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/components/api-workspace/GenerationVerificationPanel.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/components/api-workspace/VisualStepEditor.vue", import.meta.url), "utf8"),
    readFile(new URL("../src/components/api-workspace/DebugResultPanel.vue", import.meta.url), "utf8"),
  ]);
  assert.match(api, /\$\{basePath\(projectId\)\}\$\{workspaceId\}\/cancel\//);
  assert.match(workspace, /cancelRootWorkspace/);
  assert.match(workspace, /const historyWorkspaceId = ref\(null\)/);
  assert.match(workspace, /const selectHistoryWorkspace = \(id\)/);
  assert.match(workspace, /:workspace="historyWorkspace"/);
  assert.match(workspace, /reloadSequence \+= 1/);
  assert.match(workspace, /assertion_review_ack/);
  assert.match(history, /reportPath\("api", props\.projectId, row\.id\)/);
  assert.match(history, /api-workspace-history-scope-\$\{target\.kind\}/);
  assert.match(history, /select-history/);
  assert.match(conversation, /candidate\.assertion_provenance/);
  assert.match(conversation, /确认前不会采用、调试或保存该候选/);
  assert.match(verification, /不会把排队时间计为执行耗时/);
  assert.match(editor, /清理测试数据/);
  assert.match(editor, /availableExtractVariables/);
  assert.match(debug, /不建议自动重试/);
  assert.match(debug, /normalized \? "结果未知" : "未执行"/);
});
