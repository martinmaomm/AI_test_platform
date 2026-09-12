import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const sourceUrl = (path) => new URL(path, import.meta.url);

test("endpoint and scenario UI reuse the shared action-help tooltip for consequential actions", async () => {
  const sources = await Promise.all([
    readFile(sourceUrl("../src/views/api-testing/EndpointTestCases.vue"), "utf8"),
    readFile(sourceUrl("../src/views/api-testing/EndpointTester.vue"), "utf8"),
    readFile(sourceUrl("../src/views/api-testing/ScenarioOrchestratorPage.vue"), "utf8"),
    readFile(sourceUrl("../src/components/scenario/ScenarioList.vue"), "utf8"),
    readFile(sourceUrl("../src/components/scenario/ScenarioOrchestrator.vue"), "utf8"),
    readFile(sourceUrl("../src/components/scenario/StepEditorDrawer.vue"), "utf8"),
    readFile(sourceUrl("../src/components/APICaseEditDetail.vue"), "utf8"),
  ]);

  for (const source of sources) {
    assert.match(source, /import ActionHelpTooltip from ['"]@\/components\/ActionHelpTooltip\.vue['"]/);
    assert.match(source, /<ActionHelpTooltip\b/);
  }

  assert.match(sources[0], /树中可拖动同层节点调整顺序并立即保存/);
  assert.match(sources[0], /可能新增、修改或删除测试数据/);
  assert.match(sources[1], /同步发起一次真实 HTTP 请求/);
  assert.match(sources[1], /可能新增、修改或删除测试数据/);
  assert.match(sources[1], /复制按钮只写入本机剪贴板/);
  assert.match(sources[3], /加入套件仅建立引用而不复制场景/);
  assert.match(sources[3], /删除会移除场景记录，无法从页面恢复/);
  assert.match(sources[4], /复制为独立副本到当前场景草稿/);
  assert.match(sources[4], /插入动态值函数/);
  assert.match(sources[4], /当前已保存的场景/);
  assert.match(sources[5], /依次执行前序步骤和当前草稿步骤/);
  assert.match(sources[5], /还需在场景编排页点击“保存”/);
  assert.match(sources[6], /从接口用例导入的步骤只进入当前编辑草稿/);
  assert.match(sources[6], /不代表断言已经验证通过/);
});
