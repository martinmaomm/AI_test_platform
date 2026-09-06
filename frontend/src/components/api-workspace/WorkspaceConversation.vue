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
    <div v-if="candidate" class="candidate">
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
        :disabled="disabled || busy"
        @click="$emit('adopt')"
        >采用候选并替换草稿</el-button
      >
    </div>
    <el-input
      v-model="message"
      type="textarea"
      :rows="3"
      maxlength="2000"
      show-word-limit
      :disabled="disabled || busy"
      placeholder="例如：先登录取得 token，再查询当前用户；需要覆盖未授权场景。"
      @keydown.ctrl.enter.prevent="send('generate')"
    />
    <div class="actions">
      <el-button
        type="primary"
        :loading="busy && mode === 'generate'"
        :disabled="disabled || busy || generationDisabled || !message.trim()"
        @click="send('generate')"
        >生成候选</el-button
      >
      <el-button
        :loading="busy && mode === 'repair'"
        :disabled="disabled || busy || generationDisabled || !canRepair"
        @click="send('repair')"
        >基于失败结果修复</el-button
      >
    </div>
    <p class="hint">
      生成和修复不会自动保存用例，也不会自动采用候选。修复仅使用已发生的失败调试证据。
    </p>
  </section>
</template>

<script setup>
import { ref } from "vue";

const props = defineProps({
  messages: { type: Array, default: () => [] },
  candidate: { type: Object, default: null },
  diff: { type: Array, default: () => [] },
  status: { type: Object, required: true },
  busy: Boolean,
  disabled: Boolean,
  generationDisabled: Boolean,
  canRepair: Boolean,
  workspaceError: String,
});
const emit = defineEmits(["send", "adopt"]);
const message = ref("");
const mode = ref("generate");
const send = (nextMode) => {
  if (props.generationDisabled) return;
  if (!message.value.trim() && nextMode === "generate") return;
  mode.value = nextMode;
  emit("send", {
    mode: nextMode,
    message:
      message.value.trim() ||
      "请基于本次失败调试结果修复草稿，不要删除或放宽断言。",
  });
  if (nextMode === "generate") message.value = "";
};
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
