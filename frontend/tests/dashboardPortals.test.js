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

test("dashboard uses a fixed CSS grid and keeps performance before security testing", () => {
  assert.match(
    source,
    /const dashboardItems = \[.*["']portal-api["'].*["']portal-web["'].*["']portal-perf["'].*["']portal-security["']/s,
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
    /const portalDisabled = \(i\) => \[["']portal-security["'], ["']portal-perf["']\]\.includes\(i\)/,
  );
  assert.match(source, /!portalDisabled\(item\.i\) && openPortal\(item\.i\)/);
  assert.match(source, /\.portal\.disabled/);
});

test("security placeholder has matching copy, icon and responsive grid areas", () => {
  assert.match(source, /安全测试（开发中）/);
  assert.match(source, /检测应用安全风险与常见漏洞，提供修复建议。/);
  assert.match(source, /<Lock\s+v-else-if="item\.i === 'portal-security'"/);
  assert.match(source, /\.portal-security\s*\{\s*grid-area: security;/);
  assert.match(source, /"api web perf security"/);
  assert.match(source, /"perf security"/);
  assert.doesNotMatch(source, /portal-app|App 自动化|Cellphone/);
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

test("AI case ratio describes immutable first-save origin instead of current script or workspace state", () => {
  assert.match(source, /AI 生成用例占比/);
  assert.match(source, /首次保存为 AI 生成的用例数 ÷ 已保存用例总数/);
  assert.match(source, /后续编辑、AI 修复或删除工作区不改变来源/);
  assert.match(source, /历史来源未知的用例计入总数但不计为 AI/);
  assert.doesNotMatch(source, /按仍保留的 AI 候选采纳记录统计/);
});

test("dashboard does not let project preference initialization block project loading forever", () => {
  assert.match(
    source,
    /Promise\.race\(\[projectStore\.initializeUserPreferences\(\), timeout\]\)/,
  );
  assert.match(source, /await loadProjectsAndSelect\(\)/);
});

test("AI case ratio has an explicit accessible question-mark tooltip trigger", () => {
  assert.match(source, /v-if="item\.i === 'metric-ai-rate'" class="metric-label-row"/);
  assert.match(source, /<el-tooltip\s+:content="metricHint\(item\.i\)"[\s\S]*?<button\s+type="button"\s+class="metric-help"\s+aria-label="查看 AI 生成用例占比说明"/);
  assert.match(source, /<QuestionFilled\s*\/>/);
  assert.match(source, /\.metric-help:focus-visible/);
});
