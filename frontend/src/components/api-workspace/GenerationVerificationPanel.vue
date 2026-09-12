<template>
  <el-card
    v-if="generation?.status || failureActions.visible"
    data-testid="api-generation-verification"
    shadow="never"
    class="verification-panel"
  >
    <template #header>
      <div class="heading">
        <strong>生成与验证</strong>
        <el-tag :type="status.type">{{ status.label }}</el-tag>
      </div>
    </template>
    <el-alert
      v-if="stale"
      title="当前草稿或上下文已变化；以下验证证据仅对应旧版本，不能据此判定当前草稿通过。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-descriptions :column="2" size="small" border>
      <el-descriptions-item label="当前阶段">{{ phase }}</el-descriptions-item>
      <el-descriptions-item label="验证轮次"
        >第 {{ generationState.attempt || 0 }} / {{ generationState.max_attempts || 3 }} 轮</el-descriptions-item
      >
      <el-descriptions-item label="排队状态" :span="2">{{ queueTiming }}</el-descriptions-item>
      <el-descriptions-item label="执行状态" :span="2">{{ executionTiming }}</el-descriptions-item>
      <el-descriptions-item label="验证目标" :span="2"
        >{{ generationState.target_url || "尚未开始请求验证" }}</el-descriptions-item
      >
      <el-descriptions-item label="接口范围" :span="2"
        >{{ endpointScope || "未记录接口范围" }}</el-descriptions-item
      >
    </el-descriptions>
    <p v-if="generationState.summary" class="summary">{{ generationState.summary }}</p>
    <section class="scenario-summary">
      <strong>场景与步骤概要</strong>
      <p>{{ scenario.name }}</p>
      <el-alert
        v-if="scenario.errors.length"
        :title="`候选草稿包含静态结构错误：${scenario.errors.join('；')}`"
        type="warning"
        :closable="false"
        show-icon
      />
      <ol v-if="scenario.steps.length">
        <li v-for="(step, index) in scenario.steps" :key="`${step.name}-${index}`">
          {{ step.name }}：{{ step.method }} {{ step.url }}<span v-if="step.phase === 'cleanup'">（清理）</span>
        </li>
      </ol>
      <p v-else>候选尚未产出可展示的步骤。</p>
    </section>
    <section
      v-if="failureActions.visible"
      class="failure-actions"
      data-testid="api-scenario-failure-actions"
    >
      <strong>当前场景需要人工处理</strong>
      <div class="failure-action-buttons">
        <el-button
          :disabled="failureActions.view.disabled"
          :title="failureActions.view.reason || '展开失败验证轮次'"
          @click="$emit('view-failure')"
        >查看失败原因</el-button>
        <el-button
          type="primary"
          plain
          :disabled="failureActions.repair.disabled"
          :title="failureActions.repair.reason || '聚焦修复补充说明，不会发起请求'"
          @click="$emit('repair')"
        >AI 修复</el-button>
        <el-button
          type="warning"
          plain
          :disabled="failureActions.manual.disabled"
          :title="failureActions.manual.reason || failureActions.manual.note"
          @click="$emit('manual-edit')"
        >手动编辑</el-button>
      </div>
      <el-alert
        v-if="failureActionReasons.length"
        :title="failureActionReasons.join('；')"
        type="info"
        :closable="false"
        show-icon
      />
      <section
        v-if="failureSummary.hasContent"
        class="failure-summary"
        data-testid="api-scenario-failure-summary"
      >
        <p v-if="failureSummary.message">最近失败原因：{{ failureSummary.message }}</p>
        <div
          v-for="(item, index) in failureSummary.steps"
          :key="`${item.name}-${index}`"
          class="failure-summary-step"
          data-testid="api-scenario-failure-step"
        >
          <p>失败步骤：{{ item.name }}</p>
          <p v-if="item.error">{{ item.error }}</p>
          <ul v-if="item.assertions.length">
            <li
              v-for="(assertion, assertionIndex) in item.assertions"
              :key="assertionIndex"
              data-testid="api-scenario-failure-assertion"
            >
              断言 {{ assertion.check || "未命名" }}（{{ assertion.comparator || "比较" }}）：
              实际 <span v-text="displayValue(assertion.check_value)" />；
              预期 <span v-text="displayValue(assertion.expect_value ?? assertion.expect)" />
            </li>
          </ul>
        </div>
      </section>
      <p class="manual-note">{{ failureActions.manual.note }}</p>
    </section>
    <el-collapse v-if="rounds.length" v-model="expandedRounds" class="rounds">
      <el-collapse-item
        v-for="round in rounds"
        :key="round.attempt || round.started_at || round.summary"
        :name="roundName(round)"
      >
        <template #title>
          <div class="round-title">
            <span>第 {{ round.attempt || "?" }} 轮：{{ round.summary || "等待结果" }}</span>
            <el-tag size="small" :type="roundStatus(round.status).type">{{
              roundStatus(round.status).label
            }}</el-tag>
          </div>
        </template>
        <p v-if="round.started_at || round.finished_at" class="time">
          {{ timeLabel(round.started_at, "开始") }}{{ timeLabel(round.finished_at, "结束") }}
        </p>
        <ul v-if="round.changes?.length" class="changes">
          <li v-for="change in round.changes" :key="change">{{ change }}</li>
        </ul>
        <DebugResultPanel v-if="round.result" :result="round.result" :stale="stale" />
        <details v-if="round.draft != null" class="candidate-json">
          <summary>查看只读候选 JSON</summary>
          <pre v-text="pretty(round.draft)" />
        </details>
      </el-collapse-item>
    </el-collapse>
    <el-alert
      :title="finalMessage"
      :type="status.type"
      :closable="false"
      show-icon
    />
  </el-card>
</template>

<script setup>
import { computed, ref } from "vue";
import DebugResultPanel from "./DebugResultPanel.vue";
import {
  generationPhaseLabel,
  generationStatusMeta,
  generationDraftSummary,
  isGenerationStale,
  latestGenerationDraft,
  debugHasFailure,
  hasReviewableGenerationRound,
} from "@/views/api-testing/apiWorkspace";
import { failureEvidence } from "./debugResult";

const props = defineProps({
  generation: { type: Object, default: null },
  candidate: { type: Object, default: null },
  workspaceRevision: { type: Number, default: null },
  dirty: Boolean,
  endpointScope: String,
  currentDebugResult: { type: Object, default: null },
  failureActions: {
    type: Object,
    default: () => ({
      visible: false,
      view: { disabled: true, reason: "" },
      repair: { disabled: true, reason: "" },
      manual: { disabled: true, reason: "", note: "" },
    }),
  },
});
defineEmits(["view-failure", "repair", "manual-edit"]);
const expandedRounds = ref([]);
const generationState = computed(() => props.generation || {});
const rounds = computed(() =>
  Array.isArray(props.generation?.rounds) ? props.generation.rounds : [],
);
const stale = computed(() =>
  isGenerationStale(props.generation, props.workspaceRevision, props.dirty),
);
const status = computed(() =>
  stale.value
    ? generationStatusMeta("stale")
    : generationStatusMeta(props.generation?.status),
);
const phase = computed(() => generationPhaseLabel(props.generation?.phase));
const displayTime = (value) => (value ? new Date(value).toLocaleString() : "未记录");
const queueTiming = computed(() => {
  const generation = generationState.value;
  if (!generation.queued_at) return "尚未进入队列。";
  if (!generation.claimed_at)
    return `已于 ${displayTime(generation.queued_at)} 排队；队列截止：${displayTime(generation.deadlines?.queue_at)}。`;
  return `已于 ${displayTime(generation.queued_at)} 排队，并于 ${displayTime(generation.claimed_at)} 被执行器领取。`;
});
const executionTiming = computed(() => {
  const generation = generationState.value;
  if (!generation.claimed_at) return "尚未开始执行；不会把排队时间计为执行耗时。";
  if (!generation.started_at)
    return `已被执行器领取，尚未记录开始时间；执行截止：${displayTime(generation.deadlines?.execution_at)}。`;
  if (!generation.finished_at)
    return `已于 ${displayTime(generation.started_at)} 开始执行；执行截止：${displayTime(generation.deadlines?.execution_at)}。`;
  return `执行于 ${displayTime(generation.started_at)} 开始，并于 ${displayTime(generation.finished_at)} 结束。`;
});
const latestDraft = computed(() =>
  latestGenerationDraft(props.generation, props.candidate),
);
const hasCandidateDraft = computed(
  () =>
    Array.isArray(props.candidate?.draft?.teststeps) &&
    props.candidate.draft.teststeps.length > 0,
);
const scenario = computed(() => generationDraftSummary(latestDraft.value));
const roundStatus = (value) => generationStatusMeta(value);
const roundName = (round) => String(round.attempt || round.started_at || round.summary);
const failedRoundNames = computed(() =>
  rounds.value.filter(hasReviewableGenerationRound).map(roundName),
);
const latestReviewableRound = computed(() =>
  [...rounds.value].reverse().find(hasReviewableGenerationRound) || null,
);
const failureSummary = computed(() => {
  const round = latestReviewableRound.value;
  const result = round?.result || props.currentDebugResult;
  const steps = failureEvidence(result);
  const staticErrors = Array.isArray(round?.static_errors)
    ? round.static_errors.filter(Boolean)
    : [];
  const message =
    round?.error ||
    result?.error ||
    round?.summary ||
    props.generation?.summary ||
    staticErrors.join("；") ||
    (result?.error_type ? `执行失败：${result.error_type}` : "");
  return { steps, message, hasContent: Boolean(message || steps.length) };
});
const failureActionReasons = computed(() =>
  [
    props.failureActions.view?.reason,
    props.failureActions.repair?.reason,
    props.failureActions.manual?.reason,
  ].filter((reason, index, values) => reason && values.indexOf(reason) === index),
);
const showFailureEvidence = () => {
  expandedRounds.value = [...failedRoundNames.value];
  return expandedRounds.value.length > 0;
};
defineExpose({ showFailureEvidence });
const pretty = (value) => JSON.stringify(value, null, 2);
const displayValue = (value) => {
  if (value === undefined) return "未记录";
  try {
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return String(value);
  }
};
const timeLabel = (value, label) =>
  value ? `${label}：${new Date(value).toLocaleString()} ` : "";
const finalMessage = computed(() => {
  if (stale.value) return "验证结果已过期，当前版本需要重新生成并验证。";
  const messages = {
    passed: hasCandidateDraft.value
      ? "候选已验证通过，但尚未采用；当前可视化草稿和测试用例都未被自动覆盖。"
      : "已验证通过：结果仅对应当前标注的草稿版本和验证目标。",
    needs_review: hasCandidateDraft.value
      ? "需要人工处理：请检查失败证据或候选后再决定采用、修复或保存。"
      : "需要人工处理：请检查失败证据后再决定修复或保存。",
    failed: hasCandidateDraft.value
      ? "验证失败：可采用候选作为待调试草稿，或继续修复并验证。"
      : "验证失败：请根据运行证据检查后重新生成或修复并验证。",
    stale: "结果已过期，不能显示为已通过。",
  };
  return messages[props.generation?.status] || "流程仍在进行或等待后端更新。";
});
</script>

<style scoped>
.verification-panel,
.scenario-summary,
.rounds,
.failure-actions {
  display: grid;
  gap: 10px;
}
.heading,
.round-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.summary,
.scenario-summary p,
.time {
  margin: 0;
}
.scenario-summary ol,
.changes {
  margin: 0;
  padding-left: 20px;
}
.failure-action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.failure-summary {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-color-warning-light-9);
}
.failure-summary p,
.failure-summary ul {
  margin: 0;
}
.failure-summary ul {
  padding-left: 20px;
}
.manual-note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.candidate-json pre {
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
