import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse } from "@vue/compiler-sfc";
import { nextTick } from "vue";
import {
  assertionExpectedTypeLabel,
  formatAssertionExpected,
  parseAssertionExpected,
} from "../src/components/api-workspace/assertionValues.js";

test("complete literals distinguish numbers, quoted strings and structured expectations", () => {
  for (const [raw, expected] of [
    ["401", 401], ['"401"', "401"], ["0", 0], ["-5", -5],
    ["1.25", 1.25], ["1e3", 1000], [" 401 ", 401],
    ["true", true], ["false", false], ["null", null], ['"null"', "null"],
    ["[1,2]", [1, 2]], ['{"code":401}', { code: 401 }],
    ['"a\\nb"', "a\nb"], ['""', ""],
  ]) assert.deepEqual(parseAssertionExpected(raw), expected, raw);
});

test("plain text, variables and incomplete literals stay editable without evaluation", () => {
  for (const raw of ["", " ", "401状态", "list", "01", "-", "1.", "1e", '"40', "[", "{",
    "${expected_code}", "$expected_code", "{{ expected_code }}", "1 + 2", "NaN", "Infinity"])
    assert.equal(parseAssertionExpected(raw), raw);
  assert.equal(parseAssertionExpected(false), false);
  assert.equal(parseAssertionExpected(null), null);
});

test("numeric precision and finite-value boundaries do not silently change expectations", () => {
  for (const raw of ["9007199254740993", "-9007199254740993", "1e999", '[9007199254740993]', '{"id":9007199254740993}'])
    assert.equal(parseAssertionExpected(raw), raw);
  assert.equal(parseAssertionExpected("9007199254740991"), Number.MAX_SAFE_INTEGER);
});

test("loaded values round-trip without converting stored strings, null, arrays or objects", () => {
  for (const value of [401, "401", "true", "null", "[1]", '{"code":401}', '"quoted"',
    "ordinary text", "01", "", " ", null, true, false, [1, "2"], { code: 401 }, "${code}"])
    assert.deepEqual(parseAssertionExpected(formatAssertionExpected(value)), value);
  assert.equal(formatAssertionExpected(401), "401");
  assert.equal(formatAssertionExpected("401"), '"401"');
  assert.equal(formatAssertionExpected(null), "null");
  assert.equal(formatAssertionExpected(undefined), "");
});

test("type labels describe stored values and mark runtime variables", () => {
  for (const [value, label] of [[401, "数字"], ["401", "字符串"], [true, "布尔值"],
    [null, "空值（null）"], [[], "数组"], [{}, "对象"],
    ["${code}", "变量（运行时确定）"], ["$code", "变量（运行时确定）"],
    ["{{ code }}", "变量（运行时确定）"], ["prefix_${code}", "字符串"]])
    assert.equal(assertionExpectedTypeLabel(value), label);
});

// Exercise the actual input state/watchers; browser regressions cover its
// Element Plus rendering plus the workspace save/debug API round trip.
const original = await readFile(new URL(
  "../src/components/api-workspace/AssertionExpectedInput.vue", import.meta.url,
), "utf8");
const script = parse(original).descriptor.scriptSetup.content
  .replace(/import \{ ref, watch \} from "vue";/, "")
  .replace(/import \{[\s\S]*?\} from "\.\/assertionValues.js";/, "");
const moduleSource = `
  import { ref, watch, reactive, effectScope } from ${JSON.stringify(import.meta.resolve("vue"))};
  import { parseAssertionExpected, formatAssertionExpected, assertionExpectedTypeLabel }
    from ${JSON.stringify(new URL("../src/components/api-workspace/assertionValues.js", import.meta.url).href)};
  export function createHarness(value) {
    const parent = reactive({ modelValue: value, disabled: false });
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
      hooks = { text, updateText };
    });
    return { ...hooks, parent, emitted, stop: () => scope.stop() };
  }
`;
const { createHarness } = await import(
  `data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`,
);

test("typing quotes and decimals keeps the raw input while emitting typed values", async (t) => {
  const h = createHarness("401");
  t.after(h.stop);
  assert.equal(h.text.value, '"401"');
  for (const raw of ["", "4", "40", "401", "", '"', '"4', '"40', '"401', '"401"', "", "1", "1.", "1.2", "1.25"]) {
    h.updateText(raw);
    await nextTick();
    assert.equal(h.text.value, raw);
    assert.deepEqual(h.parent.modelValue, parseAssertionExpected(raw));
  }
  assert.equal(h.parent.modelValue, 1.25);
});

test("input accepts structured and variable edits and follows external replacement", async (t) => {
  const h = createHarness(null);
  t.after(h.stop);
  assert.equal(h.text.value, "null");
  for (const raw of ["{", '{"code":401}', "${expected_code}", "false", "[1,2]", "0"]) {
    h.updateText(raw);
    await nextTick();
    assert.equal(h.text.value, raw);
    assert.deepEqual(h.parent.modelValue, parseAssertionExpected(raw));
  }
  h.parent.modelValue = "401";
  await nextTick();
  assert.equal(h.text.value, '"401"');
  h.parent.modelValue = 0;
  await nextTick();
  assert.equal(h.text.value, "0");
  h.parent.disabled = true;
  h.updateText("403");
  assert.equal(h.parent.modelValue, 0);
});
