<template>
  <el-card
    v-if="generation?.status"
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
        >第 {{ generation.attempt || 0 }} / {{ generation.max_attempts || 3 }} 轮</el-descriptions-item
      >
      <el-descriptions-item label="验证目标" :span="2"
        >{{ generation.target_url || "尚未开始请求验证" }}</el-descriptions-item
      >
      <el-descriptions-item label="接口范围" :span="2"
        >{{ endpointScope || "未记录接口范围" }}</el-descriptions-item
      >
    </el-descriptions>
    <p v-if="generation.summary" class="summary">{{ generation.summary }}</p>
    <section class="scenario-summary">
      <strong>场景与步骤概要</strong>
      <p>{{ scenario.name }}</p>
      <ol v-if="scenario.steps.length">
        <li v-for="(step, index) in scenario.steps" :key="`${step.name}-${index}`">
          {{ step.name }}：{{ step.method }} {{ step.url }}
        </li>
      </ol>
      <p v-else>候选尚未产出可展示的步骤。</p>
    </section>
    <el-collapse v-if="rounds.length" class="rounds">
      <el-collapse-item
        v-for="round in rounds"
        :key="round.attempt || round.started_at || round.summary"
        :name="String(round.attempt || round.started_at)"
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
        <details v-if="round.draft" class="candidate-json">
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
import { computed } from "vue";
import DebugResultPanel from "./DebugResultPanel.vue";
import {
  generationPhaseLabel,
  generationStatusMeta,
  isGenerationStale,
  latestGenerationDraft,
} from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  generation: { type: Object, default: null },
  candidate: { type: Object, default: null },
  workspaceRevision: { type: Number, default: null },
  dirty: Boolean,
  endpointScope: String,
});
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
const latestDraft = computed(() =>
  latestGenerationDraft(props.generation, props.candidate),
);
const hasCandidateDraft = computed(
  () =>
    Array.isArray(props.candidate?.draft?.teststeps) &&
    props.candidate.draft.teststeps.length > 0,
);
const scenario = computed(() => {
  const draft = latestDraft.value || {};
  return {
    name: draft.config?.name || "候选场景尚未生成",
    steps: Array.isArray(draft.teststeps)
      ? draft.teststeps.map((step) => ({
          name: step.name || "未命名步骤",
          method: String(step.request?.method || "GET").toUpperCase(),
          url: step.request?.url || "/",
        }))
      : [],
  };
});
const roundStatus = (value) => generationStatusMeta(value);
const pretty = (value) => JSON.stringify(value, null, 2);
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
.rounds {
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
.candidate-json pre {
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
