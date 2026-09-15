<template>
  <el-card v-if="failure" shadow="never" class="model-failure-notice" data-testid="api-model-failure">
    <el-alert :title="failure.message" type="error" :closable="false" show-icon />
    <p>{{ workspace.title || '当前工作区' }} · 失败阶段：{{ stageLabel(failure.stage) }}</p>
    <p v-if="retry.reason">{{ retry.reason }}</p>
    <div v-if="retry.available" class="retry-actions">
      <el-button type="primary" data-testid="api-retry-model-generation" :disabled="disabled || Boolean(disabledReason)" :loading="pending" @click="$emit('retry')">{{ retry.label }}</el-button>
      <ActionHelpTooltip :label="retry.label" :content="retry.confirmation" />
      <span v-if="disabledReason">{{ disabledReason }}</span>
    </div>
    <p class="retry-boundary">重试不会自动保存或覆盖已保存的测试用例。</p>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
import { modelFailure, modelFailureStageLabel as stageLabel, workspaceModelRetryState } from "@/utils/modelFailure";
const props = defineProps({ workspace: { type: Object, default: null }, disabled: Boolean, pending: Boolean, disabledReason: { type: String, default: "" } });
defineEmits(["retry"]);
const failure = computed(() => modelFailure(props.workspace));
const retry = computed(() => workspaceModelRetryState(props.workspace));
</script>

<style scoped>
.model-failure-notice :deep(.el-card__body) { display: grid; gap: 10px; }
.model-failure-notice p { margin: 0; font-size: 13px; }
.retry-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.retry-boundary { color: var(--el-text-color-secondary); }
</style>
