import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse } from "@vue/compiler-sfc";

const source = async (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("shared action help is accessible without activating the surrounding action", async () => {
  const component = await source("components/ActionHelpTooltip.vue");
  const { descriptor, errors } = parse(component);
  assert.deepEqual(errors, []);
  assert.match(descriptor.template.content, /type="button"/);
  assert.match(descriptor.template.content, /:aria-label="`\$\{label\}说明`"/);
  assert.match(descriptor.template.content, /@click\.stop/);
  assert.match(descriptor.template.content, /@keydown\.stop/);
  assert.match(descriptor.template.content, /:trigger="\['hover', 'focus', 'click'\]"/);
  assert.doesNotMatch(descriptor.template.content, /:disabled|v-html|\$emit/);
  assert.doesNotMatch(descriptor.scriptSetup.content, /fetch|axios|watch|onMounted|defineEmits/);
});

test("API workspace helper text covers planning, editing, evidence, and export boundaries", async () => {
  const files = {
    "views/api-testing/ApiWorkspace.vue": ["工作区操作", "保存工作区设置", "保存子场景模型", "添加步骤"],
    "components/api-workspace/ScenarioOverview.vue": ["生成并验证全流程", "工作区设置"],
    "components/api-workspace/WorkspaceManagerDialog.vue": ["管理工作区操作"],
    "components/api-workspace/GenerationVerificationPanel.vue": ["失败处理操作"],
    "components/api-workspace/WorkspaceExecutionHistory.vue": ["运行历史操作", "打开执行详情"],
    "components/api-workspace/PythonExportPanel.vue": ["Python 导出操作"],
    "components/api-workspace/VisualStepEditor.vue": ["步骤编辑操作", "断言操作"],
    "components/api-testing/BrowserDiscoveryPanel.vue": ["开始探索", "探索任务操作", "允许或拒绝接口来源", "选择此来源", "确认接口并生成场景"],
  };
  for (const [path, labels] of Object.entries(files)) {
    const component = await source(path);
    assert.match(component, /import ActionHelpTooltip from "@\/components\/ActionHelpTooltip.vue"/, path);
    for (const label of labels) {
      assert.ok(component.includes(`<ActionHelpTooltip label="${label}"`) || component.includes(`label="${label}" content=`), `${path}: ${label}`);
    }
    assert.deepEqual(parse(component).errors, [], path);
  }
  assert.match(await source("components/api-workspace/GenerationVerificationPanel.vue"), /AI 修复只定位到对话输入框/);
  assert.match(await source("components/api-workspace/WorkspaceExecutionHistory.vue"), /已发生的测试数据操作不会回滚/);
  assert.match(await source("components/api-testing/BrowserDiscoveryPanel.vue"), /磁盘日志和截图保留/);
  assert.match(await source("components/api-testing/BrowserDiscoveryPanel.vue"), /生成前确认框/);
  assert.match(await source("components/api-testing/BrowserDiscoveryPanel.vue"), /不会立即执行或保存/);
  assert.match(await source("components/api-workspace/PythonExportPanel.vue"), /不能反向更新可视化草稿/);
});
