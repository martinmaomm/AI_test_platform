import test from "node:test";
import assert from "node:assert/strict";
import {
  createDashboardRequestGate,
  dashboardProjectsFromResponse,
} from "../src/utils/dashboardState.js";
import { visibleDashboardItems } from "../src/utils/accessControl.js";

test("dashboard project selection reads every returned project page rather than assuming the default 20", () => {
  const items = Array.from({ length: 21 }, (_, index) => ({ id: index + 1 }));
  assert.equal(
    dashboardProjectsFromResponse({ success: true, data: { items } }).length,
    21,
  );
});

test("clearing the dashboard selection is a new generation and rejects late project responses", () => {
  const gate = createDashboardRequestGate();
  const selected = gate.begin(101);
  const cleared = gate.begin(null);
  assert.equal(gate.isCurrent(selected), false);
  assert.equal(gate.isCurrent(cleared), true);
});

test("rapid dashboard project changes reject the earlier response", () => {
  const gate = createDashboardRequestGate();
  const first = gate.begin(101);
  const second = gate.begin(202);
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
});

test("dashboard fixed item order removes global settings for ordinary users", () => {
  const items = [
    "metric-pass-rate",
    "chart",
    "portal-settings",
  ].map((i) => ({ i }));
  assert.deepEqual(
    visibleDashboardItems(items, { role: "user" }).map((item) => item.i),
    ["metric-pass-rate", "chart"],
  );
  assert.deepEqual(
    visibleDashboardItems(items, { role: "admin" }).map((item) => item.i),
    items.map((item) => item.i),
  );
});
