<template>
  <el-card shadow="never" class="scenario-overview" data-testid="api-workspace-overview">
    <template #header>
      <div class="heading">
        <strong>场景计划与覆盖</strong>
        <span class="heading-actions">
          <el-button text size="small" :disabled="promptDisabled" @click="$emit('edit-root-context')">工作区设置</el-button>
          <ActionHelpTooltip label="工作区设置" content="定位到根工作区的模型、文档和接口范围设置；这里只用于调整配置，不会立即生成或运行。" />
          <el-tag :type="currentStatus.type">{{ currentStatus.label }}</el-tag>
        </span>
      </div>
    </template>
    <el-alert
      v-if="rootGeneration?.summary"
      :title="`本轮生成结果：${rootGeneration.summary}`"
      :type="roundStatus.type"
      :closable="false"
      show-icon
    />
    <el-descriptions :column="2" size="small" border>
      <el-descriptions-item label="当前进度">
        {{ progressLabel }}
      </el-descriptions-item>
      <el-descriptions-item label="本轮场景">
        {{ rootGeneration?.scenario_ids?.length || scenarios.length }} 个
      </el-descriptions-item>
      <el-descriptions-item label="计划端点">
        {{ coverage.planned }} / {{ coverage.total }}
      </el-descriptions-item>
      <el-descriptions-item label="已验证端点">
        {{ coverage.verified }} / {{ coverage.total }}
      </el-descriptions-item>
    </el-descriptions>
    <p class="coverage-hint">
      已生成 {{ coverage.generated }} 个去重端点；未覆盖 {{ coverage.uncovered_endpoint_ids.length }} 个。
      覆盖按端点去重统计，不表示已覆盖全部参数组合。
    </p>
    <details v-if="rootMessages.length" class="requirement-history">
      <summary>需求历史（{{ rootMessages.length }}）</summary>
      <ol>
        <li v-for="(message, index) in rootMessages" :key="message.id || index">
          <strong>{{ message.role === 'user' ? '你' : 'AI 助手' }}：</strong>
          {{ message.content || message.message || '（空消息）' }}
        </li>
      </ol>
    </details>
    <div class="root-prompt">
      <label for="root-scenario-prompt">描述测试目标（可生成多个独立场景）</label>
      <el-input
        id="root-scenario-prompt"
        :model-value="rootPrompt"
        aria-label="描述测试目标"
        type="textarea"
        :rows="5"
        resize="vertical"
        maxlength="2000"
        show-word-limit
        placeholder="例如：覆盖登录、创建订单和订单查询；每个场景须独立完成登录和数据准备。"
        :disabled="promptDisabled"
        @update:model-value="$emit('update:rootPrompt', $event)"
      />
      <div class="heading-actions">
        <el-button
          type="primary"
          :disabled="promptDisabled || generationDisabled || !rootPrompt.trim()"
          @click="$emit('generate')"
        >生成并验证全流程</el-button>
        <ActionHelpTooltip label="生成并验证全流程" content="按测试目标规划多个独立场景，调用 AI 生成候选并顺序验证。确认后会发送真实请求，可能增删改测试数据；这不是只重跑当前场景，生成结果仍需逐个检查、采用和保存。" />
      </div>
    </div>
    <el-alert
      v-if="!scenarios.length"
      title="尚未产生场景计划。提交测试目标后，计划和子场景会在此显示。"
      type="info"
      :closable="false"
      show-icon
    />
    <div v-else class="scenario-list" aria-label="场景列表" data-testid="api-workspace-scenarios">
      <p class="coverage-hint">点击场景查看其候选、草稿和验证结果；切换场景不会发起执行。</p>
      <button
        v-for="scenario in scenarios"
        :key="scenario.id"
        :data-testid="`api-workspace-scenario-${scenario.id}`"
        type="button"
        class="scenario-row"
        :class="{ active: String(scenario.id) === String(selectedScenarioId) }"
        @click="$emit('select', scenario.id)"
      >
        <span class="scenario-copy">
          <strong>{{ scenario.title || `场景 #${scenario.id}` }}</strong>
          <small>{{ scenario.scenario_description || "未提供场景说明" }}</small>
          <small>端点：{{ endpointText(scenario) }}</small>
          <small v-if="scenario.model_failure?.message">{{ scenario.model_failure.message }} 点击此场景查看处理入口。</small>
        </span>
        <el-tag size="small" :type="scenarioStatus(scenario).type">
          {{ scenarioStatus(scenario).label }}
        </el-tag>
      </button>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
import {
  normalizeCoverage,
  currentScenarioState,
  currentScenarioStatus,
  rootGenerationStatusMeta,
  scenarioStatusMeta,
} from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  root: { type: Object, default: null },
  endpointOptions: { type: Array, default: () => [] },
  selectedScenarioId: { type: [Number, String], default: null },
  rootPrompt: { type: String, default: "" },
  activeDirty: Boolean,
  promptDisabled: Boolean,
  generationDisabled: Boolean,
});
defineEmits(["generate", "select", "edit-root-context", "update:rootPrompt"]);

const scenarios = computed(() =>
  Array.isArray(props.root?.scenarios) ? props.root.scenarios : [],
);
const rootGeneration = computed(() => props.root?.generation || {});
const rootMessages = computed(() =>
  Array.isArray(props.root?.messages) ? props.root.messages : [],
);
const coverage = computed(() => normalizeCoverage(props.root?.coverage));
const roundStatus = computed(() => rootGenerationStatusMeta(rootGeneration.value?.status));
const currentStatus = computed(
  () =>
    (props.activeDirty
      ? { label: "有场景已修改待重验", type: "warning", stale: true }
      : currentScenarioState(props.root)) || roundStatus.value,
);
const progressLabel = computed(() => {
  const current = rootGeneration.value?.current_scenario;
  const active = rootGeneration.value?.active_scenario_id;
  const total = rootGeneration.value?.total_scenarios || scenarios.value.length;
  if (rootGeneration.value?.phase === "finished")
    return `已结束 ${total || 0} / ${total || 0}`;
  if (current?.title) return `${current.title}（${total || "?"} 个）`;
  if (active != null) return `正在处理场景 #${active}（${total || "?"} 个）`;
  return total ? `等待或已完成（${total} 个）` : "等待规划";
});
const scenarioStatus = (scenario) =>
  scenarioStatusMeta(
    currentScenarioStatus(scenario),
  );
const endpointText = (scenario) => {
  const endpoints = new Map(
    props.endpointOptions.map((endpoint) => [String(endpoint.id), endpoint]),
  );
  const ids = Array.isArray(scenario?.endpoint_ids) ? scenario.endpoint_ids : [];
  if (!ids.length) return "未分配";
  return ids
    .map((id) => {
      const endpoint = endpoints.get(String(id));
      if (!endpoint) return `接口 #${id}`;
      const method = String(endpoint.method || "GET").toUpperCase();
      const path = endpoint.path || endpoint.url || "/";
      const summary = endpoint.summary || endpoint.name || endpoint.description;
      return summary ? `${method} ${path} · ${summary}` : `${method} ${path}`;
    })
    .join("；");
};
</script>

<style scoped>
.scenario-overview,
.root-prompt,
.scenario-list {
  display: grid;
  gap: 12px;
}
.scenario-overview :deep(.el-card__body) {
  display: grid;
  gap: 12px;
}
.heading,
.scenario-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.heading-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.coverage-hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.requirement-history {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.requirement-history ol {
  margin: 8px 0 0;
  padding-left: 20px;
  display: grid;
  gap: 6px;
  white-space: pre-wrap;
}
.root-prompt label,
.scenario-copy {
  display: grid;
  gap: 4px;
}
.scenario-row {
  width: 100%;
  text-align: left;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color);
  padding: 10px;
  cursor: pointer;
}
.scenario-row:hover,
.scenario-row.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.scenario-copy small {
  color: var(--el-text-color-secondary);
}
</style>
