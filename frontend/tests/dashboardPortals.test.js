import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/views/Dashboard.vue", import.meta.url),
  "utf8",
);

test("dashboard API description explains both sources and the AI workflow", () => {
  assert.match(
    source,
    /["']portal-api["']:\s*["']基于接口文档或网页探索，通过 AI 对话生成、验证和修复接口测试用例。["']/,
  );
  assert.doesNotMatch(source, /多协议接口测试、复杂场景链路编排/);
});

test("dashboard uses a fixed CSS grid and keeps performance before app automation", () => {
  assert.match(
    source,
    /const dashboardItems = \[.*["']portal-api["'].*["']portal-web["'].*["']portal-perf["'].*["']portal-app["']/s,
  );
  assert.match(source, /grid-template-areas/);
  assert.doesNotMatch(
    source,
    /vue3-grid-layout-next|GridLayout|GridItem|localStorage|LAYOUT_MIGRATION/,
  );
});

test("unavailable dashboard portals remain grey and non-clickable", () => {
  assert.match(
    source,
    /const portalDisabled = \(i\) => \[["']portal-app["'], ["']portal-perf["']\]\.includes\(i\)/,
  );
  assert.match(source, /!portalDisabled\(item\.i\) && openPortal\(item\.i\)/);
  assert.match(source, /\.portal\.disabled/);
});

test("dashboard portals remain available when no project is selected", () => {
  assert.match(source, /<div class="dashboard-grid">/);
  assert.doesNotMatch(source, /v-else class="dashboard-grid"/);
  assert.match(source, /v-if="!selectedProjectId" class="state"/);
});

test("dashboard removes the infrastructure heading and its grid row while retaining configuration portals", () => {
  assert.doesNotMatch(source, /系统基础设施|section-infra|\binfra\b/);
  assert.match(source, /\{ i: "portal-ai-config" \}/);
  assert.match(source, /\{ i: "portal-settings" \}/);
});

test("dashboard does not let project preference initialization block project loading forever", () => {
  assert.match(
    source,
    /Promise\.race\(\[projectStore\.initializeUserPreferences\(\), timeout\]\)/,
  );
  assert.match(source, /await loadProjectsAndSelect\(\)/);
});
