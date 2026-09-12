import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse } from "@vue/compiler-sfc";
import { isReactive, nextTick, watchEffect } from "vue";

// Exercise the real component's state and watchers with a parent v-model echo.
// The browser regression separately covers Element Plus input/render behavior.
const source = await readFile(new URL(
  "../src/components/api-workspace/KeyValueRows.vue", import.meta.url,
), "utf8");
const script = parse(source).descriptor.scriptSetup.content
  .replace(/import .* from "vue";/, "");
const moduleSource = `
  import { ref, watch, reactive, effectScope } from ${JSON.stringify(import.meta.resolve("vue"))};
  export function createHarness(value, typed = false) {
    const parent = reactive({ modelValue: value, typed });
    const emitted = [];
    const defineProps = () => parent;
    const defineEmits = () => (event, payload) => {
      emitted.push([event, payload]);
      parent.modelValue = JSON.parse(JSON.stringify(payload));
    };
    const scope = effectScope();
    let hooks;
    scope.run(() => {
      ${script}
      hooks = { rows, emitValue, add, remove };
    });
    return { ...hooks, parent, emitted, stop: () => scope.stop() };
  }
`;
const { createHarness } = await import(
  `data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`,
);

test("numeric query inputs are reactive and support clear, type and parent echoes", async (t) => {
  const original = { keyword: "${role_name}", pageNum: 2, pageSize: 2 };
  const h = createHarness(original);
  t.after(h.stop);
  assert.ok(isReactive(h.rows.value[1]), "input updates must trigger a render");
  let displayed;
  const stopRender = watchEffect(() => { displayed = h.rows.value[1].value; });
  t.after(stopRender);
  h.rows.value[1].value = "";
  h.emitValue();
  await nextTick();
  assert.equal(displayed, "");
  h.rows.value[1].value = "1";
  h.emitValue();
  await nextTick();
  assert.equal(displayed, "1");
  assert.equal(String(h.parent.modelValue.pageNum), "1");
  assert.equal(h.parent.modelValue.pageSize, 2);
  assert.equal(h.parent.modelValue.keyword, "${role_name}");
  assert.deepEqual(original, { keyword: "${role_name}", pageNum: 2, pageSize: 2 });
  assert.equal(h.emitted.at(-1)[0], "update:modelValue");
});

test("editing a text value preserves untouched JSON, null, boolean and numeric values", (t) => {
  const h = createHarness({ text: "old", count: 2, enabled: true, ids: [1, 2], config: { limit: 1 }, empty: null });
  t.after(h.stop);
  h.rows.value[0].value = "new";
  h.emitValue();
  assert.deepEqual(h.parent.modelValue, {
    text: "new", count: 2, enabled: true, ids: [1, 2], config: { limit: 1 }, empty: null,
  });
});

test("new and renamed rows retain local edits without serializing blank keys", async (t) => {
  const h = createHarness({ "X-Test": "kept" });
  t.after(h.stop);
  h.add();
  h.add();
  assert.equal(h.rows.value.length, 3);
  h.rows.value[1].value = "draft value";
  h.emitValue();
  await nextTick();
  assert.equal(h.rows.value.length, 3);
  assert.deepEqual(h.parent.modelValue, { "X-Test": "kept" });
  h.rows.value[1].key = " X-New ";
  h.emitValue();
  await nextTick();
  assert.equal(h.rows.value[1].key, " X-New ");
  assert.equal(h.parent.modelValue["X-New"], "draft value");
  h.rows.value[1].key = "X-Renamed";
  h.emitValue();
  await nextTick();
  assert.deepEqual(h.parent.modelValue, { "X-Test": "kept", "X-Renamed": "draft value" });
  h.remove(1);
  await nextTick();
  assert.deepEqual(h.parent.modelValue, { "X-Test": "kept" });
  assert.equal(h.rows.value.length, 2);
});

test("typed number zero, boolean and incomplete JSON remain editable", async (t) => {
  const h = createHarness({ count: 2, enabled: true, payload: [1] }, true);
  t.after(h.stop);
  h.rows.value[0].value = "0";
  h.emitValue();
  await nextTick();
  assert.equal(h.parent.modelValue.count, 0);
  assert.equal(h.rows.value[0].type, "number");
  h.rows.value[1].value = "false";
  h.emitValue();
  await nextTick();
  assert.equal(h.parent.modelValue.enabled, false);
  h.rows.value[2].value = "[";
  h.emitValue();
  await nextTick();
  assert.equal(h.rows.value[2].type, "json");
  assert.equal(h.rows.value[2].value, "[");
  h.rows.value[2].value = "[2,3]";
  h.emitValue();
  await nextTick();
  assert.deepEqual(h.parent.modelValue.payload, [2, 3]);
  h.rows.value[0].type = "string";
  h.emitValue();
  await nextTick();
  assert.equal(h.parent.modelValue.count, "0");
});

test("external replacement resynchronizes rows, even when restoring a previously emitted value", async (t) => {
  const h = createHarness({ pageNum: 2 });
  t.after(h.stop);
  h.rows.value[0].value = "1";
  h.emitValue();
  await nextTick();
  const edited = JSON.parse(JSON.stringify(h.parent.modelValue));
  h.parent.modelValue = { other: "different scenario" };
  await nextTick();
  assert.equal(h.rows.value[0].key, "other");
  h.parent.modelValue = edited;
  await nextTick();
  assert.equal(h.rows.value[0].key, "pageNum");
  assert.equal(h.rows.value[0].value, "1");
});
