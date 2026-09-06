import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  documentStatus,
  errorMessage,
  indexReady,
  isActiveTask,
  listItems,
  modelLabel,
  moduleLabel,
  parseReady,
  requestId,
  sourceLocationLabel,
  sourceStateLabel,
  taskLabel,
} from "../src/views/project-knowledge/workspace.js";

test("knowledge envelopes consistently expose their items", () => {
  assert.deepEqual(listItems({ success: true, data: { items: [{ id: 1 }] } }), [
    { id: 1 },
  ]);
  assert.deepEqual(listItems({ data: [{ id: 2 }] }), [{ id: 2 }]);
  assert.deepEqual(listItems({ success: true, data: {} }), []);
});

test("API error envelope keeps the backend Chinese message visible", () => {
  assert.equal(
    errorMessage(
      {
        response: {
          data: { success: false, message: "所选资料尚未建立检索索引。" },
        },
      },
      "fallback",
    ),
    "所选资料尚未建立检索索引。",
  );
});

test("document readiness keeps parsed-only material out of question answering", () => {
  const parsedOnly = {
    current_revision: { parse_status: "completed", index_status: "pending" },
  };
  const indexed = {
    current_revision: { parse_status: "success", index_status: "ready" },
  };
  const failed = {
    current_revision: { parse_status: "failed", index_status: "pending" },
  };
  assert.equal(parseReady(parsedOnly), true);
  assert.equal(indexReady(parsedOnly), false);
  assert.deepEqual(documentStatus(parsedOnly), {
    label: "正文已解析，索引未就绪",
    type: "warning",
  });
  assert.equal(indexReady(indexed), true);
  assert.deepEqual(documentStatus(failed), {
    label: "解析失败",
    type: "danger",
  });
  assert.deepEqual(
    documentStatus({
      current_revision: { parse_status: "pending", index_status: "pending" },
    }),
    { label: "待处理", type: "info" },
  );
});

test("task status preserves queue, partial and cancellation semantics", () => {
  assert.equal(isActiveTask({ status: "queued" }), true);
  assert.equal(isActiveTask({ status: "partial" }), false);
  assert.equal(
    taskLabel({ status: "running", phase: "index" }),
    "处理中：index",
  );
  assert.equal(taskLabel({ status: "cancelled" }), "已取消");
});

test("labels are safe pure text fallbacks", () => {
  assert.equal(moduleLabel(""), "未分类");
  assert.equal(
    modelLabel({ name: "主模型", provider: "OpenAI", model_name: "gpt" }),
    "主模型",
  );
  assert.equal(modelLabel({}), "未配置模型");
  assert.match(
    requestId(),
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
});

test("source locations use parser-backed human labels rather than object coercion", () => {
  assert.equal(
    sourceLocationLabel({
      kind: "pdf",
      page_start: 2,
      page_end: 3,
      character_start: 0,
      character_end: 120,
    }),
    "PDF 第 2–3 页，0–120 字符",
  );
  assert.equal(
    sourceLocationLabel({
      kind: "docx",
      paragraph_start: 4,
      paragraph_end: 4,
      character_start: 0,
      character_end: 20,
    }),
    "Word 第 4 段，0–20 字符",
  );
  assert.equal(
    sourceLocationLabel({
      kind: "xlsx",
      sheet: "角色",
      row_start: 2,
      row_end: 5,
      character_start: 0,
      character_end: 33,
    }),
    "工作表 角色，第 2–5 行，0–33 字符",
  );
  assert.equal(
    sourceLocationLabel({
      kind: "markdown",
      character_start: 10,
      character_end: 22,
    }),
    "Markdown，10–22 字符",
  );
  assert.equal(
    sourceLocationLabel({
      kind: "docx",
      table_index: 1,
      row_start: 2,
      row_end: 4,
      character_start: 0,
      character_end: 30,
    }),
    "Word 表格 1，第 2–4 行，0–30 字符",
  );
  assert.equal(
    sourceStateLabel({ active: false, version: { number: 2 } }),
    "来源已更新、停用或删除 · 版本 2",
  );
});

test("workspace uses the approved API scope and safe plain-text rendering", async () => {
  const [apiSource, viewSource, routerSource] = await Promise.all([
    readFile(
      new URL("../src/api/projectKnowledge.js", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../src/views/project-knowledge/ProjectKnowledgeWorkspace.vue",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(new URL("../src/router/index.js", import.meta.url), "utf8"),
  ]);
  assert.match(apiSource, /`\/projects\/\$\{projectId\}\/knowledge`/);
  assert.match(apiSource, /documents\/\$\{documentId\}\/prepare\//);
  assert.match(apiSource, /case-generations\/\$\{generationId\}\/save\//);
  assert.match(apiSource, /manual-cases\/export\//);
  assert.match(apiSource, /conversations\/\$\{conversationId\}\/messages\//);
  assert.match(apiSource, /tasks\/\$\{taskId\}\/retry-cleanup\//);
  assert.match(viewSource, /<pre v-text="message\.content">/);
  assert.match(viewSource, /client_request_id: requestId\(\)/);
  assert.match(
    viewSource,
    /已记录取消请求；正在进行的网络调用不会承诺立即停止/,
  );
  assert.match(viewSource, /label="需求文档" value="requirement"/);
  assert.match(viewSource, /sourceLocationLabel\(row\.location\)/);
  assert.doesNotMatch(viewSource, /prop="location"/);
  assert.match(viewSource, /仅基于所选正文生成/);
  assert.match(viewSource, /text_only: generationForm\.text_only/);
  assert.match(
    viewSource,
    /if \(generationForm\.text_only\) payload\.supplemental_document_ids = \[\]/,
  );
  assert.match(viewSource, /options\.models\[0\]\?\.id \?\? null/);
  assert.doesNotMatch(viewSource, /当前项目为只读，无法取消任务/);
  assert.match(viewSource, /draft_id: String\(item\.draft_id \|\| item\.id\)/);
  assert.match(viewSource, /const task = response\.task \|\| response/);
  assert.match(
    viewSource,
    /questionDocumentIds\.value = readyQuestionDocuments\.value\.map/,
  );
  assert.match(viewSource, /document_type: documentEditForm\.document_type/);
  assert.doesNotMatch(viewSource, /metadata: \{ is_active/);
  assert.doesNotMatch(viewSource, /当前项目为只读，无法创建问答任务/);
  assert.doesNotMatch(viewSource, /v-html/);
  assert.match(viewSource, /编辑草稿 \/ 核对来源/);
  assert.match(viewSource, /function saveDraftEdit\(\)/);
  assert.match(viewSource, /我的生成记录/);
  assert.match(viewSource, /查看详情 \/ 来源/);
  assert.match(viewSource, /sourceStateLabel\(source\)/);
  assert.match(viewSource, /generationTask\?\.result\?\.coverage/);
  assert.match(viewSource, /maxlength="2000"/);
  assert.match(viewSource, /allow-create/);
  assert.match(viewSource, /retryCleanupTask/);
  assert.match(viewSource, /requestScope/);
  assert.match(viewSource, /const taskSummary/);
  assert.match(viewSource, /技术输出（原始流）/);
  assert.match(viewSource, /未完成回答，引用待核对/);
  assert.match(viewSource, /hasActiveGenerationTask/);
  assert.match(viewSource, /hasActiveAnswerTask/);
  assert.match(viewSource, /当前会话已有问答任务/);
  assert.match(viewSource, /inFlight: false, terminal: false/);
  assert.match(viewSource, /setTimeout\(tick, 2000\)/);
  assert.doesNotMatch(viewSource, /setInterval\(tick, 2000\)/);
  assert.match(viewSource, /isTerminalTask\(entry\.task\)/);
  assert.match(viewSource, /isDraftEditingLocked/);
  assert.match(viewSource, /生成任务仍在运行，结束后才能编辑草稿/);
  assert.match(
    routerSource,
    /name: 'WebKnowledgeBase', component: \(\) => import\('\@\/views\/project-knowledge\/ProjectKnowledgeWorkspace\.vue'\)/,
  );
  assert.match(
    routerSource,
    /name: 'ApiKnowledgeBase', component: \(\) => import\('\@\/views\/project\/KnowledgeBase\.vue'\)/,
  );
  assert.match(
    routerSource,
    /name: 'AppKnowledgeBase', component: \(\) => import\('\@\/views\/project\/KnowledgeBase\.vue'\)/,
  );
});
