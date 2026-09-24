<template>
  <section v-if="available" class="performance-run-comparison">
    <div class="comparison-heading">
      <div>
        <h3>历史压测对比</h3>
        <p>
          选择已结束的正式压测作为基准。对比只显示统计值，不包含脚本或请求响应内容。
        </p>
      </div>
      <el-button
        :loading="loadingCandidates || loadingComparison"
        @click="refresh"
      >
        刷新对比
      </el-button>
    </div>
    <el-alert v-if="!canReport" type="warning" :closable="false" show-icon>
      你没有查看运行对比的权限。
    </el-alert>
    <template v-else>
      <el-alert v-if="error" type="error" :closable="false" show-icon>
        {{ error }}
      </el-alert>
      <el-select
        v-model="baselineRunId"
        class="baseline-select"
        :loading="loadingCandidates"
        placeholder="选择历史正式压测作为基准"
        @change="loadComparison"
      >
        <el-option
          v-for="item in candidates"
          :key="item.id"
          :label="candidateLabel(item)"
          :value="item.id"
        />
      </el-select>
      <el-empty
        v-if="!loadingCandidates && !candidates.length"
        description="暂无可用于对比的历史正式压测"
      />
      <template v-if="comparison">
        <el-descriptions :column="2" border class="comparison-summary">
          <el-descriptions-item label="当前运行">
            {{ runLabel(comparison.current) }}
          </el-descriptions-item>
          <el-descriptions-item label="基准运行">
            {{ runLabel(comparison.baseline) }}
          </el-descriptions-item>
          <el-descriptions-item label="可比性">
            <el-tag :type="comparabilityType(comparison.comparability?.status)">
              {{ comparabilityLabel(comparison.comparability?.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="说明">
            {{ comparisonReasons.join("；") || "-" }}
          </el-descriptions-item>
        </el-descriptions>

        <h4>运行条件</h4>
        <el-table
          :data="comparison.comparability?.conditions || []"
          size="small"
        >
          <el-table-column prop="label" label="条件" min-width="150" />
          <el-table-column label="基准" min-width="150">
            <template #default="{ row }">{{
              valueText(row.baseline)
            }}</template>
          </el-table-column>
          <el-table-column label="当前" min-width="150">
            <template #default="{ row }">{{ valueText(row.current) }}</template>
          </el-table-column>
          <el-table-column label="是否一致" width="100">
            <template #default="{ row }">{{ equalText(row.equal) }}</template>
          </el-table-column>
        </el-table>

        <h4>总体差异</h4>
        <el-table :data="comparison.overall || []" size="small">
          <el-table-column prop="label" label="指标" min-width="170" />
          <el-table-column label="基准" min-width="120">
            <template #default="{ row }">{{
              metricText(row.baseline, row.unit)
            }}</template>
          </el-table-column>
          <el-table-column label="当前" min-width="120">
            <template #default="{ row }">{{
              metricText(row.current, row.unit)
            }}</template>
          </el-table-column>
          <el-table-column label="变化" min-width="130">
            <template #default="{ row }">{{ deltaText(row) }}</template>
          </el-table-column>
        </el-table>

        <h4>逐接口差异</h4>
        <el-collapse>
          <el-collapse-item
            v-for="endpoint in comparison.endpoints || []"
            :key="endpoint.id"
            :title="`${endpoint.name || endpoint.id}（${endpointStatusLabel(endpoint.match_status)}）`"
            :name="endpoint.id"
          >
            <el-table :data="endpoint.metrics || []" size="small">
              <el-table-column prop="label" label="指标" min-width="170" />
              <el-table-column label="基准" min-width="120">
                <template #default="{ row }">{{
                  metricText(row.baseline, row.unit)
                }}</template>
              </el-table-column>
              <el-table-column label="当前" min-width="120">
                <template #default="{ row }">{{
                  metricText(row.current, row.unit)
                }}</template>
              </el-table-column>
              <el-table-column label="变化" min-width="130">
                <template #default="{ row }">{{ deltaText(row) }}</template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
        <ul v-if="comparison.limitations?.length" class="limitations">
          <li v-for="item in comparison.limitations" :key="item">{{ item }}</li>
        </ul>
      </template>
      <PerformanceRunAnalysis
        v-if="baselineRunId"
        :project-id="projectId"
        :run-id="runId"
        :run="run"
        :can-report="canReport"
        :can-execute="canExecute"
        analysis-type="load_comparison"
        :comparison-run-id="baselineRunId"
        :comparison-data="comparison"
      />
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import dayjs from "dayjs";
import {
  getPerformanceRunComparison,
  getPerformanceRunComparisonCandidates,
  performanceErrorMessage,
} from "@/api/performance";
import PerformanceRunAnalysis from "./PerformanceRunAnalysis.vue";
import {
  canAnalyzeRunType,
  displayAnalysisValue,
  formatComparisonDelta,
  formatComparisonMetric,
  isCurrentComparisonResponse,
} from "./performanceAnalysisState";
import { performanceRunStatusLabel } from "./performanceExecutionState";

const props = defineProps({
  projectId: { type: [String, Number], required: true },
  runId: { type: [String, Number], required: true },
  run: { type: Object, default: null },
  canReport: Boolean,
  canExecute: Boolean,
});
const available = computed(() =>
  canAnalyzeRunType(props.run, "load_comparison"),
);
const candidates = ref([]);
const baselineRunId = ref(null);
const comparison = ref(null);
const error = ref("");
const loadingCandidates = ref(false);
const loadingComparison = ref(false);
let epoch = 0;
let comparisonRequestEpoch = 0;
const scope = () => ({
  projectId: String(props.projectId || ""),
  runId: String(props.runId || ""),
  epoch,
});
const scopeCurrent = (captured) =>
  captured.epoch === epoch &&
  captured.projectId === String(props.projectId || "") &&
  captured.runId === String(props.runId || "");
const unwrapItems = (response) => {
  const body = response?.data ?? response;
  return Array.isArray(body?.items) ? body.items : [];
};
const unwrapComparison = (response) => response?.data ?? response;
const comparisonReasons = computed(() =>
  Array.isArray(comparison.value?.comparability?.reasons)
    ? comparison.value.comparability.reasons
    : [],
);
const formatTime = (value) =>
  value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "-";
const candidateLabel = (item) =>
  `${item.same_plan ? "同计划" : "其他计划"} · ${item.plan_name || "未命名计划"} · ${formatTime(item.created_at)} · ${performanceRunStatusLabel(item.status)}`;
const runLabel = (item = {}) =>
  `${item.plan_name || "未命名计划"} · ${formatTime(item.created_at)} · ${performanceRunStatusLabel(item.status)}`;
const valueText = (value) => displayAnalysisValue(value);
const metricText = formatComparisonMetric;
const deltaText = formatComparisonDelta;
const equalText = (value) =>
  value === true ? "一致" : value === false ? "不一致" : "未记录";
const comparabilityLabel = (status) =>
  ({
    comparable: "可比较",
    conditions_changed: "条件有变化",
    insufficient_data: "数据不足",
  })[status] ||
  status ||
  "未记录";
const comparabilityType = (status) =>
  ({
    comparable: "success",
    conditions_changed: "warning",
    insufficient_data: "info",
  })[status] || "info";
const endpointStatusLabel = (status) =>
  ({
    matched: "已匹配",
    baseline_only: "仅基准存在",
    current_only: "仅当前存在",
    ambiguous: "无法唯一匹配",
  })[status] ||
  status ||
  "未记录";

async function loadCandidates() {
  const captured = scope();
  if (!available.value || !props.canReport) return;
  loadingCandidates.value = true;
  try {
    const response = await getPerformanceRunComparisonCandidates(
      captured.projectId,
      captured.runId,
    );
    if (!scopeCurrent(captured)) return;
    candidates.value = unwrapItems(response).filter(
      (item) => String(item.id) !== captured.runId,
    );
    if (
      !candidates.value.some(
        (item) => String(item.id) === String(baselineRunId.value),
      )
    ) {
      baselineRunId.value = null;
      comparison.value = null;
    }
    error.value = "";
  } catch (cause) {
    if (scopeCurrent(captured))
      error.value = performanceErrorMessage(cause, "加载历史运行失败");
  } finally {
    if (scopeCurrent(captured)) loadingCandidates.value = false;
  }
}
async function loadComparison() {
  const captured = scope();
  const baseline = baselineRunId.value;
  const sequence = ++comparisonRequestEpoch;
  comparison.value = null;
  if (!baseline || !available.value || !props.canReport) return;
  loadingComparison.value = true;
  try {
    const response = await getPerformanceRunComparison(
      captured.projectId,
      captured.runId,
      baseline,
    );
    if (
      !scopeCurrent(captured) ||
      !isCurrentComparisonResponse(
        sequence,
        comparisonRequestEpoch,
        baseline,
        baselineRunId.value,
      )
    )
      return;
    comparison.value = unwrapComparison(response);
    error.value = "";
  } catch (cause) {
    if (
      scopeCurrent(captured) &&
      isCurrentComparisonResponse(
        sequence,
        comparisonRequestEpoch,
        baseline,
        baselineRunId.value,
      )
    )
      error.value = performanceErrorMessage(cause, "加载运行对比失败");
  } finally {
    if (
      scopeCurrent(captured) &&
      isCurrentComparisonResponse(
        sequence,
        comparisonRequestEpoch,
        baseline,
        baselineRunId.value,
      )
    )
      loadingComparison.value = false;
  }
}
async function refresh() {
  await loadCandidates();
  if (baselineRunId.value) await loadComparison();
}
function reset() {
  epoch += 1;
  comparisonRequestEpoch += 1;
  candidates.value = [];
  baselineRunId.value = null;
  comparison.value = null;
  error.value = "";
  loadingCandidates.value = false;
  loadingComparison.value = false;
  loadCandidates();
}
watch(
  () => [
    props.projectId,
    props.runId,
    props.run?.status,
    props.run?.mode,
    props.canReport,
  ],
  reset,
  { immediate: true },
);
onBeforeUnmount(() => {
  epoch += 1;
  comparisonRequestEpoch += 1;
});
</script>

<style scoped>
.performance-run-comparison {
  margin-top: 24px;
  border-top: 1px solid var(--app-border-light);
  padding-top: 18px;
}
.comparison-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.comparison-heading h3 {
  margin: 0;
}
.comparison-heading p,
.limitations {
  color: var(--app-text-muted);
  font-size: 13px;
  margin: 5px 0;
}
.baseline-select {
  width: min(520px, 100%);
  margin: 14px 0;
}
.comparison-summary {
  margin: 12px 0;
}
h4 {
  margin: 18px 0 8px;
}
</style>
