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
      <el-alert
        v-if="assertionReview.requiresConfirmation"
        data-testid="api-candidate-assertion-review"
        title="候选调整了既有断言。请核对差异与原因，并在采用时明确确认；确认前不会采用、调试或保存该候选。"
        type="warning"
        :closable="false"
        show-icon
      />
      <ul v-if="assertionReview.changes.length" class="review-list">
        <li v-for="change in assertionReview.changes" :key="change">断言变更：{{ change }}</li>
      </ul>
      <ul v-if="assertionReview.warnings.length" class="review-list">
        <li v-for="warning in assertionReview.warnings" :key="warning">提示：{{ warning }}</li>
      </ul>
      <details v-if="assertionProvenance.length" class="assertion-provenance">
        <summary>查看断言来源</summary>
        <ul>
          <li v-for="entry in assertionProvenance" :key="`${entry.step_index}-${entry.endpoint_id}`">
            步骤 {{ Number(entry.step_index) + 1 }}：{{ provenanceText(entry) }}
          </li>
        </ul>
      </details>
      <div class="action-option candidate-action">
        <el-button
          type="primary"
          :disabled="disabled || busy || submitting || generationPending"
          @click="$emit('adopt')"
          >采用候选并替换草稿</el-button
        >
        <el-tooltip
          content="仅替换当前可视化草稿；不会自动保存为测试用例或执行。"
          placement="top"
          :trigger="['hover', 'focus', 'click']"
          :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))', lineHeight: '1.6' }"
        >
          <button
            type="button"
            class="action-help"
            aria-label="采用候选并替换草稿说明"
          ><el-icon aria-hidden="true"><QuestionFilled /></el-icon></button>
        </el-tooltip>
      </div>
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
    <div class="action-choice" data-testid="api-workspace-action-choice">
      <template v-if="allowGenerate">
        <span class="choice-line">从测试目标开始设计：填写需求，选「生成并验证」。</span>
        <span class="choice-line">流程基本正确但运行报错：选「修复并验证」，AI 会参考失败证据；修复建议可不填。</span>
      </template>
      <template v-else-if="allowScenarioRegenerate">
        <span class="choice-line">流程整体不对或想调整思路：填写补充要求，选「重新生成本场景」（保留原目标，只处理当前场景）。</span>
        <span class="choice-line">流程基本正确但运行报错：选「修复并验证」，AI 会参考失败证据；修复建议可不填。</span>
      </template>
      <template v-else>
        <span class="choice-line">流程基本正确但运行报错：选「修复并验证」，AI 会参考失败证据；修复建议可不填。</span>
      </template>
    </div>
    <div class="actions">
      <div v-if="allowGenerate" class="action-option">
        <el-button
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
        <el-tooltip
          content="生成候选草稿。确认后会进行真实验证，可能影响测试数据；候选不会自动采用或保存。"
          placement="top"
          :trigger="['hover', 'focus', 'click']"
          :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))', lineHeight: '1.6' }"
        >
          <button type="button" class="action-help" aria-label="生成并验证说明"><el-icon aria-hidden="true"><QuestionFilled /></el-icon></button>
        </el-tooltip>
      </div>
      <div v-else-if="allowScenarioRegenerate" class="action-option">
        <el-button
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
        <el-tooltip
          content="保留原目标，只重新生成当前场景。确认后会进行真实验证，可能影响测试数据。"
          placement="top"
          :trigger="['hover', 'focus', 'click']"
          :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))', lineHeight: '1.6' }"
        >
          <button type="button" class="action-help" aria-label="重新生成本场景说明"><el-icon aria-hidden="true"><QuestionFilled /></el-icon></button>
        </el-tooltip>
      </div>
      <div class="action-option">
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
        <el-tooltip
          content="参考失败证据修补候选/草稿。修复建议可不填；确认后会进行真实验证，可能影响测试数据。"
          placement="top"
          :trigger="['hover', 'focus', 'click']"
          :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))', lineHeight: '1.6' }"
        >
          <button type="button" class="action-help" aria-label="修复并验证说明"><el-icon aria-hidden="true"><QuestionFilled /></el-icon></button>
        </el-tooltip>
      </div>
    </div>
    <p class="action-boundary">
      确认后可能实际增删改测试数据；候选不会自动采用或保存为测试用例。
    </p>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { QuestionFilled } from "@element-plus/icons-vue";
import { candidateAssertionReview, shouldClearSubmittedMessage } from "@/views/api-testing/apiWorkspace";

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
const emit = defineEmits(["adopt", "dirty-change"]);
const message = ref("");
const messageInput = ref(null);
const mode = ref("generate");
const submitting = ref(false);
const assertionReview = computed(() => candidateAssertionReview(props.candidate));
const assertionProvenance = computed(() =>
  Array.isArray(props.candidate?.assertion_provenance)
    ? props.candidate.assertion_provenance.filter(
        (entry) => entry && Number.isInteger(entry.step_index) && Array.isArray(entry.assertions),
      )
    : [],
);
const provenanceText = (entry) =>
  entry.assertions
    .filter((item) => item && typeof item === "object")
    .map((item) => `${item.selector || "未记录选择器"}（${item.source || "未记录来源"}）`)
    .join("；") || "未记录断言";
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
watch(
  () => message.value.trim(),
  (value) => emit("dirty-change", Boolean(value)),
  { immediate: true },
);
onBeforeUnmount(() => emit("dirty-change", false));
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
.actions,
.action-option {
  flex-wrap: wrap;
}
.action-option {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
}
.action-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border: 0;
  border-radius: 50%;
  padding: 0;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: help;
  font: inherit;
}
.action-help:hover,
.action-help:focus-visible {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.action-help:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
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
.review-list,
.assertion-provenance ul {
  margin: 8px 0;
  padding-left: 20px;
}
.candidate p,
.action-choice,
.action-boundary {
  margin: 0;
}
.action-boundary {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.action-choice {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.choice-line {
  display: block;
}
</style>
