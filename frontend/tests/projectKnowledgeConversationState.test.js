import test from "node:test";
import assert from "node:assert/strict";
import {
  clearConversationDraftIfUnchanged,
  conversationDisplayTitle,
  createConversationRequestState,
  getConversationDraft,
  hasActiveConversationAnswerTask,
  mergeConversationMessages,
  removeConversation,
  setConversationDraft,
  truncateConversationTitle,
  upsertConversation,
} from "../src/views/project-knowledge/conversationState.js";

test("conversation drafts are isolated and a submitted draft only clears when unchanged", () => {
  const drafts = new Map();
  setConversationDraft(drafts, "first", "原会话草稿");
  setConversationDraft(drafts, "second", "新会话草稿");

  assert.equal(getConversationDraft(drafts, "first"), "原会话草稿");
  assert.equal(getConversationDraft(drafts, "second"), "新会话草稿");

  clearConversationDraftIfUnchanged(drafts, "first", "已提交的问题");
  assert.equal(getConversationDraft(drafts, "first"), "原会话草稿");

  clearConversationDraftIfUnchanged(drafts, "first", "原会话草稿");
  assert.equal(getConversationDraft(drafts, "first"), "");
  assert.equal(getConversationDraft(drafts, "second"), "新会话草稿");
});

test("late message loads cannot replace newer messages or a newly selected conversation", () => {
  const state = createConversationRequestState();
  state.select();
  const staleLoad = state.startMessageLoad("first");

  state.invalidateMessages("first");
  assert.equal(state.canApplyMessageLoad(staleLoad, "first"), false);

  const currentLoad = state.startMessageLoad("first");
  assert.equal(state.canApplyMessageLoad(currentLoad, "first"), true);
  state.select();
  assert.equal(state.canApplyMessageLoad(currentLoad, "second"), false);
});

test("a delayed create only selects its result when the user has not switched conversations", () => {
  const state = createConversationRequestState();
  state.select();
  const selectionAtCreate = state.selectionVersion();
  assert.equal(state.shouldAutoSelectCreated(selectionAtCreate), true);

  state.select();
  assert.equal(state.shouldAutoSelectCreated(selectionAtCreate), false);
});

test("conversation labels prefer the first question and retain a full tooltip value", () => {
  const firstQuestion = "q".repeat(81);
  assert.equal(
    conversationDisplayTitle({ title: "新建知识问答", first_question: firstQuestion }),
    firstQuestion,
  );
  assert.equal(truncateConversationTitle(firstQuestion), `${"q".repeat(80)}…`);
  assert.equal(
    conversationDisplayTitle({ title: "新建知识问答", first_question: "" }),
    "新建知识问答",
  );
});

test("created conversations stay visible when a subsequent list refresh fails", () => {
  const created = { id: "new", title: "新建知识问答", first_question: "" };
  const visible = upsertConversation([{ id: "old", title: "历史会话" }], created);

  assert.deepEqual(visible, [created, { id: "old", title: "历史会话" }]);
});

test("deleting one conversation preserves the remaining list order", () => {
  const conversations = [
    { id: "current", title: "当前会话" },
    { id: "next", title: "下一会话" },
    { id: "other", title: "其他会话" },
  ];

  assert.deepEqual(removeConversation(conversations, "current"), [
    conversations[1],
    conversations[2],
  ]);
  assert.deepEqual(removeConversation(conversations, "missing"), conversations);
});

test("only unfinished answer tasks block deleting their own conversation", () => {
  const tasks = [
    { kind: "answer", status: "completed", payload: { conversation_id: "a" } },
    { kind: "answer", status: "cancel_requested", payload: { conversation_id: "a" } },
    { kind: "answer", status: "running", payload: { conversation_id: "b" } },
    { kind: "generate", status: "running", payload: { conversation_id: "a" } },
  ];

  assert.equal(hasActiveConversationAnswerTask(tasks, "a"), true);
  assert.equal(hasActiveConversationAnswerTask(tasks, "b"), true);
  assert.equal(hasActiveConversationAnswerTask(tasks, "c"), false);
  assert.equal(
    hasActiveConversationAnswerTask(
      [{ kind: "answer", status: "cancelled", payload: { conversation_id: "a" } }],
      "a",
    ),
    false,
  );
});

test("accepted answer messages merge by id while retaining already loaded history", () => {
  assert.deepEqual(
    mergeConversationMessages(
      [
        { id: "history", content: "older" },
        { id: "user", content: "old" },
      ],
      [
        { id: "user", content: "new" },
        { id: "assistant", content: "answer" },
      ],
    ),
    [
      { id: "history", content: "older" },
      { id: "user", content: "new" },
      { id: "assistant", content: "answer" },
    ],
  );
});
