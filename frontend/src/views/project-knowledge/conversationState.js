const conversationKey = (conversationId) =>
  conversationId == null ? "" : String(conversationId);

export const conversationDisplayTitle = (conversation) =>
  conversation?.first_question || conversation?.title || "未命名会话";

export const truncateConversationTitle = (title, maximumLength = 80) => {
  const value = String(title || "");
  return value.length > maximumLength
    ? `${value.slice(0, maximumLength)}…`
    : value;
};

export const upsertConversation = (conversations, conversation) => [
  conversation,
  ...conversations.filter(
    (item) => String(item.id) !== String(conversation.id),
  ),
];

export const getConversationDraft = (drafts, conversationId) =>
  drafts.get(conversationKey(conversationId)) || "";

export const setConversationDraft = (drafts, conversationId, value) => {
  const key = conversationKey(conversationId);
  if (!key) return;
  if (value) drafts.set(key, value);
  else drafts.delete(key);
};

export const clearConversationDraftIfUnchanged = (
  drafts,
  conversationId,
  submittedValue,
) => {
  const key = conversationKey(conversationId);
  if (drafts.get(key) === submittedValue) drafts.delete(key);
};

export const mergeConversationMessages = (current, incoming) => [
  ...new Map(
    [...current, ...incoming].filter(Boolean).map((message) => [message.id, message]),
  ).values(),
];

export const createConversationRequestState = () => {
  let selectionVersion = 0;
  const messageVersions = new Map();

  const messageVersion = (conversationId) =>
    messageVersions.get(conversationKey(conversationId)) || 0;

  return {
    select() {
      selectionVersion += 1;
      return selectionVersion;
    },
    selectionVersion: () => selectionVersion,
    shouldAutoSelectCreated(selectionAtStart) {
      return selectionVersion === selectionAtStart;
    },
    startMessageLoad(conversationId) {
      const key = conversationKey(conversationId);
      const version = messageVersion(key) + 1;
      messageVersions.set(key, version);
      return { conversationId: key, selectionVersion, version };
    },
    invalidateMessages(conversationId) {
      const key = conversationKey(conversationId);
      messageVersions.set(key, messageVersion(key) + 1);
    },
    canApplyMessageLoad(request, activeConversationId) {
      return (
        request.selectionVersion === selectionVersion &&
        request.conversationId === conversationKey(activeConversationId) &&
        request.version === messageVersion(request.conversationId)
      );
    },
    reset() {
      selectionVersion = 0;
      messageVersions.clear();
    },
  };
};
