import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  browserWorkspaceView, browserWorkspacePageKey, workspaceInitializationPlan,
} from "../src/views/api-testing/apiWorkspace.js";

const taskA = "ba7831ba-cf78-4111-9772-54842085c9b0";
const taskB = "e8d04a0b-c03a-42dc-9112-a0ea3b37e020";

test("browser entry stays on task list even when old generated workspaces exist", () => {
  assert.deepEqual(browserWorkspaceView({}), { view: "list" });
  for (const query of [{}, { view: "new" }, { discovery_id: taskA }]) {
    assert.deepEqual(workspaceInitializationPlan(query, [{ id: 18 }], { sourceType: "browser_capture" }),
      { action: "none", explicit: false });
  }
  assert.deepEqual(workspaceInitializationPlan({}, [{ id: 18 }]),
    { action: "load", workspaceId: 18, explicit: false });
});

test("browser screens have explicit validated identity and do not accept ambiguous selections", () => {
  assert.deepEqual(browserWorkspaceView({ view: "new" }), { view: "create" });
  assert.deepEqual(browserWorkspaceView({ discovery_id: taskA }), { view: "detail", taskId: taskA });
  assert.deepEqual(browserWorkspaceView({ workspace_id: "18" }), { view: "workspace", workspaceId: 18 });
  for (const query of [
    { workspace_id: "18", discovery_id: taskA }, { workspace_id: "18", view: "new" },
    { discovery_id: taskA, view: "new" }, { discovery_id: "18" }, { discovery_id: [taskA] },
    { workspace_id: "broken" }, { workspace_id: "0" }, { view: "other" },
  ]) {
    assert.equal(browserWorkspaceView(query).view, "invalid");
    assert.equal(workspaceInitializationPlan(query, [{ id: 18 }], { sourceType: "browser_capture" }).action, "invalid");
  }
});

test("switching task/workspace resets the view, but consuming confirmation hint does not", () => {
  const keys = [{}, { view: "new" }, { discovery_id: taskA }, { discovery_id: taskB },
    { workspace_id: "18" }, { workspace_id: "19" }].map(browserWorkspacePageKey);
  assert.equal(new Set(keys).size, keys.length);
  assert.equal(browserWorkspacePageKey({ workspace_id: "18", confirm_generation: "1" }),
    browserWorkspacePageKey({ workspace_id: "18" }));
});

test("template separates discovery from editors and confirmation only opens the existing dialog", async () => {
  const source = await readFile(new URL("../src/views/api-testing/ApiWorkspace.vue", import.meta.url), "utf8");
  const wrapper = await readFile(new URL("../src/views/api-testing/ApiWorkspaceBrowser.vue", import.meta.url), "utf8");
  assert.match(wrapper, /:key="pageKey"/);
  assert.match(source, /v-if="isBrowserDiscoveryView"/);
  assert.match(source, /:view="browserScreen.view"/);
  assert.match(source, /workspaceReady && \(isDocumentSource \|\| isBrowserWorkspace\)/);
  assert.match(source, /@open-workspace="openBrowserWorkspace"/);
  assert.match(source, /confirm_generation: "1"/);
  const confirmation = source.split("let openingBrowserGenerationConfirmation = false;")[1].split("onMounted(initialize)")[0];
  assert.match(confirmation, /prepareRootGeneration\(\)/);
  assert.doesNotMatch(confirmation, /confirmGeneration\(\)|sendMessage\(|sendApiWorkspaceMessage\(/);
});
