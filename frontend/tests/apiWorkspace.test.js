import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  availableChatModels,
  bodyKind,
  canGenerateWithModel,
  candidateDiff,
  debugHasFailure,
  defaultDraft,
  hasAvailableChatModel,
  listItems,
  normalizeDraft,
  reconcileWorkspaceModel,
} from "../src/views/api-testing/apiWorkspace.js";

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
      (item) => item.includes("新增步骤 1") && item.includes("Authorization"),
    ),
  );
  assert.equal(current.config.base_url, "");
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
  assert.match(router, /path: 'workspace', name: 'ApiWorkspace'/);
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
  assert.match(suites, /v-model="suiteForm\.variables"/);
  assert.match(suites, /套件变量会覆盖用例变量/);
  assert.match(endpointCases, /query: \{ case_id: selectedList\[0\]\.id \}/);
  assert.match(specDetail, /query: \{ endpoint_id: endpoint\.id \}/);
  assert.match(workspace, /<main v-if="workspaceReady"/);
  assert.match(workspace, /await router\.replace\(/);
  assert.match(workspace, /const pollWorkspace = async/);
  assert.match(workspace, /python\.value\.workspaceId/);
  assert.match(workspace, /payload\.revision != null/);
  assert.match(workspace, /availableChatModels\(modelsResult\.value\)/);
  assert.match(workspace, /reconcileWorkspaceModel/);
  assert.match(workspace, /ensureAvailableChatModel/);
  assert.match(workspace, /:generation-disabled="generationDisabled"/);
  assert.match(workspace, /重新选择可用聊天模型/);
  assert.match(conversation, /generationDisabled/);
  assert.match(conversation, /if \(props\.generationDisabled\) return/);
  assert.match(debugPanel, /result\.log/);
  assert.match(debugPanel, /step\?\.status\) === "skipped"/);
  assert.match(configEditor, /timestamp_ns/);
  assert.match(configEditor, /uuid4/);
  assert.doesNotMatch(legacyApi, /generateSpecTestCases/);
  assert.doesNotMatch(legacyApi, /generateEndpointTestCases/);
  assert.doesNotMatch(legacyApi, /generateScenario/);
  assert.doesNotMatch(specDetail, /generateEndpointTestCases\(/);
  assert.doesNotMatch(specDetail, /generateSpecTestCases\(/);
});
