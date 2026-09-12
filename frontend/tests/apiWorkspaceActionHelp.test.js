import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const conversationUrl = new URL(
  "../src/components/api-workspace/WorkspaceConversation.vue",
  import.meta.url,
);

test("workspace conversation documents action choices with keyboard-accessible help", async () => {
  const source = await readFile(conversationUrl, "utf8");

  assert.match(source, /data-testid="api-workspace-action-choice"/);
  assert.match(source, /<template v-if="allowGenerate">[\s\S]*从测试目标开始设计：填写需求，选「生成并验证」/);
  assert.match(source, /<template v-else-if="allowScenarioRegenerate">[\s\S]*流程整体不对或想调整思路：填写补充要求，选「重新生成本场景」[\s\S]*保留原目标，只处理当前场景/);
  assert.match(source, /<template v-else>[\s\S]*流程基本正确但运行报错：选「修复并验证」[\s\S]*修复建议可不填/);
  assert.match(source, /确认后可能实际增删改测试数据；候选不会自动采用或保存为测试用例/);

  const helpLabels = [
    "生成并验证说明",
    "重新生成本场景说明",
    "修复并验证说明",
    "采用候选并替换草稿说明",
  ];
  const helpButtons = source.match(/<button[\s\S]*?<\/button>/g)?.filter((button) =>
    button.includes('class="action-help"'),
  );
  assert.equal(helpButtons?.length, helpLabels.length);
  for (const label of helpLabels) {
    const button = helpButtons.find((item) => item.includes(`aria-label="${label}"`));
    assert.ok(button, `missing help control for ${label}`);
    assert.match(button, /type="button"/);
    assert.doesNotMatch(button, /@click|@keydown|@keyup/);
  }

  assert.equal((source.match(/<el-tooltip/g) || []).length, helpLabels.length);
  assert.equal(
    (source.match(/:trigger="\['hover', 'focus', 'click'\]"/g) || []).length,
    helpLabels.length,
  );
  assert.equal(
    (source.match(/:popper-style="\{ maxWidth: 'min\(360px, calc\(100vw - 32px\)\)', lineHeight: '1\.6' \}"/g) || []).length,
    helpLabels.length,
  );
  assert.match(source, /仅替换当前可视化草稿；不会自动保存为测试用例或执行/);
  assert.match(source, /<QuestionFilled \/>/);
  assert.doesNotMatch(source, /并非全清空|不是清空全部内容|默认修复/);
  assert.match(source, /submittedMessage\.trim\(\) \|\|\s+"请基于本次失败调试结果修复草稿，不要删除或放宽断言。"/);

  assert.equal((source.match(/@click="send\('generate'\)"/g) || []).length, 2);
  assert.equal((source.match(/@click="send\('repair'\)"/g) || []).length, 1);
  assert.match(source, /@click="\$emit\('adopt'\)"/);
  assert.match(source, /\.actions,[\s\S]*\.action-option \{\s+flex-wrap: wrap;/);
  assert.match(source, /\.action-help:focus-visible \{\s+outline: 2px solid var\(--el-color-primary\);/);
});
