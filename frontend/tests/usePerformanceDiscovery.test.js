import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { effectScope, ref } from "vue";

const dataModule = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const deferred = () => {
  let reject;
  const promise = new Promise((_, rejectPromise) => { reject = rejectPromise; });
  return { promise, reject };
};
let moduleId = 0;

async function harness(t) {
  const projectId = ref(1);
  const calls = [];
  const handlers = {
    getPerformanceDiscoveryConfig: async () => ({ enabled: true }),
    getPerformanceDiscoveryTasks: async () => [],
    getPerformanceDiscoveryTask: async (_projectId, id) => ({
      id, version: 1, status: "completed", api_origin: "http://api.example.test",
    }),
    getPerformanceDiscoveryRecords: async () => ({ items: [] }),
    createPerformanceDiscoveryTask: async () => ({}),
    cancelPerformanceDiscoveryTask: async () => ({}),
    createPerformanceDiscoveryDraft: async () => ({}),
    deletePerformanceDiscoveryTask: async () => ({}),
    resolvePerformanceDiscoveryOrigin: async () => ({}),
    retryPerformanceDiscoveryTask: async () => ({}),
  };
  const names = Object.keys(handlers);
  const token = `__performanceDiscoveryTest${++moduleId}`;
  globalThis[token] = { handlers, calls };
  const apiModule = dataModule(`
const state = globalThis[${JSON.stringify(token)}];
${names.map((name) => `export const ${name} = (...args) => { state.calls.push({ name: ${JSON.stringify(name)}, args }); return state.handlers.${name}(...args); };`).join("\n")}
`);
  const errorModule = new URL(
    "../src/api/performanceError.js",
    import.meta.url,
  ).href;
  const original = await readFile(
    new URL("../src/composables/usePerformanceDiscovery.js", import.meta.url),
    "utf8",
  );
  const vueModule = dataModule(`export { computed, reactive, ref } from ${JSON.stringify(import.meta.resolve("vue"))}; export const onBeforeUnmount = () => {};`);
  const source = original
    .replace('from "vue"', `from ${JSON.stringify(vueModule)}`)
    .replace('from \'@/api/performanceError\'', `from ${JSON.stringify(errorModule)}`)
    .replace('from "@/api/performanceDiscovery"', `from ${JSON.stringify(apiModule)}`);
  const { usePerformanceDiscovery } = await import(dataModule(source));
  const scope = effectScope();
  const state = scope.run(() => usePerformanceDiscovery(projectId));
  t.after(() => { scope.stop(); delete globalThis[token]; });
  await state.select("task-1");
  return { calls, handlers, projectId, state };
}

test("draft 400 reaches the page caller and releases action loading", async (t) => {
  const { handlers, state } = await harness(t);
  const failure = Object.assign(new Error("Request failed with status code 400"), {
    response: { data: { error: { message: "所选性能目标 origin 与探索任务已确认 origin 不一致。" } } },
  });
  handlers.createPerformanceDiscoveryDraft = async () => {
    throw failure;
  };

  await assert.rejects(
    state.draft({ version: 1, record_ids: [1, 2], target_id: 3 }),
    (error) => error === failure,
  );
  assert.equal(state.loading.action, false);
  assert.match(state.errorMessage.value, /origin 与探索任务已确认 origin 不一致/);
});

test("an expired draft failure does not report an error on the new project", async (t) => {
  const { handlers, projectId, state } = await harness(t);
  const pending = deferred();
  handlers.createPerformanceDiscoveryDraft = () => pending.promise;
  const request = state.draft({ version: 1, record_ids: [1], target_id: 3 });

  projectId.value = 2;
  await state.refresh();
  pending.reject(new Error("old draft failed"));

  assert.equal(await request, null);
  assert.equal(state.errorMessage.value, "");
  assert.equal(state.loading.action, false);
});

test("an expired draft failure does not report an error on a newly selected task", async (t) => {
  const { handlers, state } = await harness(t);
  const pending = deferred();
  handlers.createPerformanceDiscoveryDraft = () => pending.promise;
  const request = state.draft({ version: 1, record_ids: [1], target_id: 3 });

  await state.select("task-2");
  pending.reject(new Error("old draft failed"));

  assert.equal(await request, null);
  assert.equal(state.task.value.id, "task-2");
  assert.equal(state.errorMessage.value, "");
  assert.equal(state.loading.action, false);
});
