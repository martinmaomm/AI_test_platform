import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  createPlanStep,
  clonePlanStep,
  DEFAULT_CONNECT_TIMEOUT_SECONDS,
  DEFAULT_READ_TIMEOUT_SECONDS,
  formatJsonText,
  normalizePlanRequestTimeouts,
  serializePlanSteps,
  validatePlanRequestTimeouts,
} from "../src/views/perf-testing/performancePlanEditorState.js";

test("request timeout defaults, persisted values, and strict integer bounds are preserved", () => {
  assert.deepEqual(normalizePlanRequestTimeouts({}), {
    connect_timeout_seconds: DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: DEFAULT_READ_TIMEOUT_SECONDS,
  });
  assert.deepEqual(normalizePlanRequestTimeouts({
    connect_timeout_seconds: 12,
    read_timeout_seconds: 45,
  }), {
    connect_timeout_seconds: 12,
    read_timeout_seconds: 45,
  });
  assert.deepEqual(normalizePlanRequestTimeouts({
    connect_timeout_seconds: 0,
    read_timeout_seconds: 121,
  }), {
    connect_timeout_seconds: DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: DEFAULT_READ_TIMEOUT_SECONDS,
  });
  assert.deepEqual(validatePlanRequestTimeouts({
    connect_timeout_seconds: 1,
    read_timeout_seconds: 120,
  }), {
    connect_timeout_seconds: "",
    read_timeout_seconds: "",
  });
  for (const invalid of [0, true, 1.5, "10", null]) {
    const errors = validatePlanRequestTimeouts({
      connect_timeout_seconds: invalid,
      read_timeout_seconds: invalid,
    });
    assert.match(errors.connect_timeout_seconds, /整数秒/);
    assert.match(errors.read_timeout_seconds, /整数秒/);
  }
});

test('raw text and JSON scalar/null survive open, clone and save', () => {
  for (const [body_type, body] of [['raw', 'plain ${value}'], ['json', 'a string'], ['json', null]]) {
    const step = createPlanStep({ name: '请求', method: 'POST', path: '/items', body_type, body,
      assertions: [{ check: 'body.value', comparator: 'eq', expected: null }] });
    const copy = clonePlanStep(step);
    const result = serializePlanSteps([copy], ['POST']);
    assert.deepEqual(result.errors, []);
    assert.equal(result.value[0].body, body);
    assert.equal(result.value[0].assertions[0].expected, null);
  }
});

test("v2 plan serializes variables-safe request phases and typed assertions", () => {
  const setup = createPlanStep({
    name: "登录",
    phase: "setup",
    method: "POST",
    path: "/login",
    body_type: "json",
    body: { name: "${username}" },
    assertions: [{ check: "status_code", comparator: "eq", expected: 200 }],
    extract: [{ name: "token", check: "body.token" }],
  });
  const main = createPlanStep({
    name: "列表",
    phase: "main",
    method: "GET",
    path: "/users",
    assertions: [
      { check: "body.data", comparator: "length_gt", expected: 0 },
      { check: "status_code", comparator: "eq", expected: "200" },
    ],
  });
  const result = serializePlanSteps([setup, main], ["GET", "POST"]);
  assert.deepEqual(result.errors, []);
  assert.equal(result.value[0].body_type, "json");
  assert.deepEqual(result.value[0].extract, [
    { name: "token", check: "body.token" },
  ]);
  assert.equal(result.value[1].assertions[0].expected, 0);
  assert.equal(result.value[1].assertions[1].expected, "200");
  assert.equal("expected_status" in result.value[0], false);
});

test("v2 validation reports concrete phase, assertion and body JSON errors", () => {
  const step = createPlanStep({
    name: "",
    phase: "setup",
    method: "GET",
    path: "/x",
  });
  step.assertions = [];
  step.bodyType = "json";
  step.bodyText = "{";
  const result = serializePlanSteps([step], ["GET"]);
  assert.match(result.errors[0].name, /步骤名称/);
  assert.match(result.errors[0].phase, /至少需要一个 main/);
  assert.match(result.errors[0].assertions, /至少需要/);
  assert.match(result.errors[0].body, /JSON 第/);
  assert.equal(formatJsonText("200"), "200");
  assert.equal(formatJsonText('"200"'), '"200"');
});

test("editor state preserves nulls, JSON strings, empty assertions and duplicate Unicode query values", () => {
  const source = createPlanStep({
    name: "保真",
    method: "POST",
    phase: "main",
    path: "/搜索",
    query: { q: ["中文", "", "中文"], Q: "大写" },
    body_type: "json",
    body: "literal",
    assertions: [{ check: "body.value", comparator: "eq", expected: null }],
  });
  assert.equal(source.bodyText, '"literal"');
  assert.equal(source.assertions[0].expectedText, "null");
  const clone = createPlanStep({ ...source, assertions: source.assertions });
  assert.equal(clone.assertions[0].expectedText, "null");
  const result = serializePlanSteps([source], ["POST"]);
  assert.deepEqual(result.value[0].query, {
    q: ["中文", "", "中文"],
    Q: "大写",
  });
  assert.equal(result.value[0].body, "literal");
  const empty = createPlanStep({
    name: "空断言",
    method: "GET",
    phase: "main",
    path: "/",
    assertions: [],
  });
  assert.equal(empty.assertions.length, 0);
  assert.match(
    serializePlanSteps([empty], ["GET"]).errors[0].assertions,
    /至少需要/,
  );
});

test("editor preserves legacy selectors and exposes the v2 controls", async () => {
  const source = await readFile(
    new URL(
      "../src/views/perf-testing/PerformancePlanEditor/PerformancePlanEditor.vue",
      import.meta.url,
    ),
    "utf8",
  );
  for (const id of [
    "performance-plan-editor",
    "plan-name",
    "plan-target",
    "plan-target-refresh",
    "plan-target-error",
    "plan-target-empty",
    "plan-step-list",
    "step-name",
    "step-method",
    "step-path",
    "step-query-rows",
    "step-header-rows",
    "step-body-json",
    "step-url-preview",
    "plan-save",
    "plan-variables",
    "plan-unique-variables",
    "plan-request-timeouts",
    "plan-connect-timeout",
    "plan-read-timeout",
    "step-phase",
    "step-body-type",
    "step-extract",
    "step-assertions",
  ])
    assert.match(source, new RegExp(`data-testid="${id}"`));
  assert.doesNotMatch(source, /expected_status/);
  assert.match(source, /currentTarget:\s*Object,[\s\S]*targets:/);
  assert.match(source, /props\.plan\s*\|\|\s*props\.item/);
  assert.match(source, /normalizePlanRequestTimeouts\(source\)/);
  assert.match(source, /connect_timeout_seconds:\s*form\.connect_timeout_seconds/);
  assert.match(source, /read_timeout_seconds:\s*form\.read_timeout_seconds/);
});
