<template>
  <section class="conversation-panel">
    <div class="panel-title">
      <strong>需求对话</strong
      ><el-tag :type="status.type" size="small">{{ status.label }}</el-tag>
    </div>
    <div class="messages" aria-live="polite">
      <el-empty
        v-if="!messages.length"
        description="描述要覆盖的 API 场景，生成结果会作为候选草稿等待确认。"
        :image-size="54"
      />
      <article
        v-for="(message, index) in messages"
        :key="message.id || `${message.role}-${index}`"
        class="message"
        :class="message.role === 'user' ? 'user' : 'assistant'"
      >
        <strong>{{ message.role === "user" ? "你" : "AI 助手" }}</strong>
        <pre v-text="message.content || message.message || ''" />
      </article>
    </div>
    <el-alert
      v-if="workspaceError"
      type="error"
      :title="workspaceError"
      :closable="false"
      show-icon
    />
    <div v-if="candidate?.draft" class="candidate">
      <strong>AI 候选草稿（尚未采用）</strong>
      <p v-if="candidate.summary">{{ candidate.summary }}</p>
      <p>变更：{{ diff.join("、") }}</p>
      <el-alert
        v-if="candidate.risks?.length"
        :title="`风险：${candidate.risks.join('；')}`"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-button
        type="primary"
        :disabled="disabled || busy || submitting || generationPending"
        @click="$emit('adopt')"
        >采用候选并替换草稿</el-button
      >
    </div>
    <el-input
      ref="messageInput"
      v-model="message"
      type="textarea"
      :rows="3"
      maxlength="2000"
      show-word-limit
      :disabled="disabled || busy || submitting || generationPending"
      :aria-label="inputAriaLabel"
      :placeholder="inputPlaceholder"
      @keydown.ctrl.enter.prevent="send(allowGenerate ? 'generate' : 'repair')"
    />
    <div class="actions">
      <el-button
        v-if="allowGenerate"
        type="primary"
        :loading="busy && mode === 'generate'"
        :disabled="
          disabled ||
          busy ||
          submitting ||
          generationPending ||
          generationDisabled ||
          !allowGenerate ||
          !message.trim()
        "
        @click="send('generate')"
        >生成并验证</el-button
      >
      <el-button
        v-else-if="allowScenarioRegenerate"
        type="primary"
        :loading="busy && mode === 'generate'"
        :disabled="
          disabled ||
          busy ||
          submitting ||
          generationPending ||
          generationDisabled ||
          !message.trim()
        "
        @click="send('generate')"
        >重新生成本场景</el-button
      >
      <el-button
        :loading="busy && mode === 'repair'"
        :disabled="
          disabled ||
          busy ||
          submitting ||
          generationPending ||
          generationDisabled ||
          !canRepair
        "
        @click="send('repair')"
        >修复并验证</el-button
      >
    </div>
    <p class="hint">
      {{ allowGenerate ? "生成、修复和验证都不会自动采用候选或保存用例；确认后才会发起真实验证请求。" : allowScenarioRegenerate ? "重新生成仅处理当前子场景：原目标会保留，请补充当前场景的要求；不会重新规划或生成其他场景。" : "仅补充当前子场景的修复说明；不会重新规划或生成其他场景。" }}
    </p>
  </section>
</template>

<script setup>
import { nextTick, ref } from "vue";
import { shouldClearSubmittedMessage } from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  messages: { type: Array, default: () => [] },
  candidate: { type: Object, default: null },
  diff: { type: Array, default: () => [] },
  status: { type: Object, required: true },
  busy: Boolean,
  disabled: Boolean,
  generationDisabled: Boolean,
  canRepair: Boolean,
  allowGenerate: { type: Boolean, default: true },
  allowScenarioRegenerate: Boolean,
  inputPlaceholder: {
    type: String,
    default: "例如：先登录取得 token，再查询当前用户；需要覆盖未授权场景。",
  },
  generationPending: Boolean,
  inputAriaLabel: { type: String, default: "补充说明" },
  workspaceError: String,
  sendMessage: { type: Function, required: true },
});
const emit = defineEmits(["adopt"]);
const message = ref("");
const messageInput = ref(null);
const mode = ref("generate");
const submitting = ref(false);
const send = async (nextMode) => {
  if (
    props.generationDisabled ||
    props.disabled ||
    props.busy ||
    props.generationPending ||
    submitting.value
  )
    return;
  const submittedMessage = message.value;
  if (!submittedMessage.trim() && nextMode === "generate") return;
  mode.value = nextMode;
  submitting.value = true;
  try {
    const accepted = await props.sendMessage({
      mode: nextMode,
      message:
        submittedMessage.trim() ||
        "请基于本次失败调试结果修复草稿，不要删除或放宽断言。",
    });
    if (shouldClearSubmittedMessage(accepted, submittedMessage, message.value))
      message.value = "";
  } finally {
    submitting.value = false;
  }
};
const clearSubmittedMessage = (submittedMessage) => {
  if (message.value === submittedMessage) message.value = "";
};
const focusInput = async () => {
  await nextTick();
  const input = messageInput.value?.textarea || messageInput.value?.$el?.querySelector("textarea");
  input?.focus();
  return Boolean(input);
};
defineExpose({ clearSubmittedMessage, focusInput });
</script>

<style scoped>
.conversation-panel {
  display: grid;
  gap: 12px;
}
.panel-title,
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-title {
  justify-content: space-between;
}
.messages {
  min-height: 160px;
  max-height: 330px;
  overflow: auto;
  display: grid;
  gap: 10px;
}
.message {
  border-radius: 8px;
  padding: 10px;
  background: var(--el-fill-color-light);
}
.message.user {
  background: var(--el-color-primary-light-9);
}
.message pre {
  margin: 6px 0 0;
  white-space: pre-wrap;
  font: inherit;
}
.candidate {
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 8px;
}
.candidate p,
.hint {
  margin: 0;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
