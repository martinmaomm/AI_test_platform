<template>
  <section class="performance-run-detail">
    <div class="toolbar">
      <el-button @click="router.push({ name: 'PerfRuns' })"
        >返回执行记录</el-button
      ><el-button :loading="loading" @click="loadRun">刷新</el-button
      ><el-button
        v-if="canExecute && canStopPerformanceRun(run?.status)"
        type="danger"
        :loading="stopping"
        @click="stopRun"
        >停止执行</el-button
      >
    </div>
    <el-alert v-if="!canReport" type="warning" :closable="false" show-icon
      >你没有查看本项目执行详情的权限。</el-alert
    >
    <el-alert
      v-else-if="pollError"
      type="warning"
      :closable="false"
      show-icon
      >{{ pollError }}</el-alert
    >
    <template v-if="canReport && run">
      <el-alert
        v-if="completedWithFailures(run)"
        type="warning"
        :closable="false"
        show-icon
        >执行完成，存在失败请求。</el-alert
      >
      <el-descriptions :column="2" border class="summary">
        <el-descriptions-item label="计划">{{
          run.plan_name || "-"
        }}</el-descriptions-item
        ><el-descriptions-item label="节点">{{
          run.node_name || "-"
        }}</el-descriptions-item>
        <el-descriptions-item label="状态"
          ><el-tag :type="statusType(run.status)">{{
            runSummaryText(run)
          }}</el-tag></el-descriptions-item
        ><el-descriptions-item label="原因">{{
          run.reason || "-"
        }}</el-descriptions-item>
        <el-descriptions-item label="运行模式">{{
          run.mode === "validation" ? "单用户验证" : "正式压测"
        }}</el-descriptions-item
        ><el-descriptions-item label="验证结论"
          ><el-tag :type="validationStatusType(run.validation_status)">{{
            validationStatusLabel(run.validation_status)
          }}</el-tag></el-descriptions-item
        >
        <el-descriptions-item label="开始时间">{{
          formatTime(run.started_at)
        }}</el-descriptions-item
        ><el-descriptions-item label="结束时间">{{
          formatTime(run.finished_at)
        }}</el-descriptions-item>
      </el-descriptions>
      <div class="metrics">
        <div v-for="item in metricCards" :key="item.label" class="metric-card">
          <span>{{ item.label }}</span
          ><strong>{{ item.value }}</strong>
        </div>
      </div>
      <h3>指标趋势</h3>
      <v-chart
        v-if="samples.length"
        class="trend"
        :option="trendOption"
        autoresize
      /><el-empty v-else description="尚无指标采样" />
      <h3>接口明细</h3>
      <el-table :data="metricEntries(run.latest_metrics)"
        ><el-table-column
          prop="name"
          label="名称"
          min-width="190"
        /><el-table-column
          prop="method"
          label="方法"
          width="90"
        /><el-table-column
          prop="requests"
          label="请求"
          width="90"
        /><el-table-column
          prop="failures"
          label="失败"
          width="90"
        /><el-table-column label="平均响应(ms)" min-width="130"
          ><template #default="{ row }">{{
            formatMetric(row.avg_response_time)
          }}</template></el-table-column
        ><el-table-column label="P95(ms)" width="100"
          ><template #default="{ row }">{{
            formatMetric(row.p95)
          }}</template></el-table-column
        ><el-table-column label="P99(ms)" width="100"
          ><template #default="{ row }">{{
            formatMetric(row.p99)
          }}</template></el-table-column
        ></el-table
      >
      <template v-if="run.mode === 'validation'"
        ><h3>验证执行</h3>
        <el-descriptions :column="3" border
          ><el-descriptions-item label="执行完整">{{
            metrics.validation_complete ? "是" : "否"
          }}</el-descriptions-item
          ><el-descriptions-item label="断言通过">{{
            metrics.validation_passed ? "是" : "否"
          }}</el-descriptions-item
          ><el-descriptions-item label="主步骤"
            >{{ metrics.main_steps_completed ?? 0 }} /
            {{ metrics.main_steps_total ?? 0 }}</el-descriptions-item
          ></el-descriptions
        ></template
      >
      <template v-if="failureEvidence.length"
        ><h3>断言失败证据</h3>
        <el-table :data="failureEvidence"
          ><el-table-column
            prop="step_index"
            label="步骤"
            width="70" /><el-table-column
            prop="step_name"
            label="步骤名称"
            min-width="130" /><el-table-column
            prop="phase"
            label="阶段"
            width="80" /><el-table-column
            prop="check"
            label="检查字段"
            min-width="150" /><el-table-column
            prop="comparator"
            label="比较器"
            width="110" /><el-table-column label="期望 / 实际" min-width="220"
            ><template #default="{ row }"
              >{{ displayEvidenceValue(row.expected) }} /
              {{ displayEvidenceValue(row.actual) }}</template
            ></el-table-column
          ><el-table-column
            prop="error_type"
            label="错误类型"
            min-width="120" /><el-table-column
            prop="message"
            label="诊断"
            min-width="180" /></el-table
      ></template>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import dayjs from "dayjs";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import { getProject } from "@/api/projects";
import {
  getPerformanceRun,
  performanceErrorMessage,
  stopPerformanceRun,
} from "@/api/performance";
import {
  canStopPerformanceRun,
  completedWithFailures,
  displayEvidenceValue,
  failureSamples,
  formatErrorRate,
  formatMetric,
  isPerformanceRunActive,
  metricEntries,
  metricSamples,
  performanceExecutionPermissions,
  runSummaryText,
  samePerformanceRunScope,
  sampleMetrics,
  validationStatusLabel,
  validationStatusType,
} from "./performanceExecutionState";
use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
]);

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const projectStore = useProjectStore();
const run = ref(null);
const project = ref(null);
const loading = ref(false);
const stopping = ref(false);
const pollError = ref("");
const pollFailures = ref(0);
let pollTimer;
let scopeEpoch = 0;
let requestEpoch = 0;
const projectId = computed(() => projectStore.currentProjectId);
const runId = computed(() => route.params.runId);
const member = computed(() =>
  project.value?.members?.find(
    (item) => item.username === authStore.user?.username,
  ),
);
const permissions = computed(() =>
  performanceExecutionPermissions(authStore.user, member.value),
);
const canReport = computed(() => permissions.value.canReport);
const canExecute = computed(() => permissions.value.canExecute);
const samples = computed(() => metricSamples(run.value?.metrics_samples));
const metrics = computed(() => run.value?.latest_metrics || {});
const failureEvidence = computed(() => failureSamples(metrics.value));
const metricCards = computed(() => [
  { label: "请求数", value: metrics.value.requests ?? "-" },
  { label: "失败数", value: metrics.value.failures ?? "-" },
  { label: "RPS（累计）", value: formatMetric(metrics.value.rps) },
  { label: "错误率", value: formatErrorRate(metrics.value.error_rate) },
  {
    label: "平均响应(ms)",
    value: formatMetric(metrics.value.avg_response_time),
  },
  { label: "P95(ms)", value: formatMetric(metrics.value.p95) },
  { label: "P99(ms)", value: formatMetric(metrics.value.p99) },
  { label: "虚拟用户", value: metrics.value.users ?? "-" },
]);
const trendOption = computed(() => ({
  tooltip: { trigger: "axis", valueFormatter: (value) => formatMetric(value) },
  legend: { data: ["RPS（累计）", "失败数", "P95(ms)"] },
  grid: { left: 55, right: 135, top: 40, bottom: 32 },
  xAxis: {
    type: "category",
    data: samples.value.map((item) => item.timestamp || ""),
  },
  yAxis: [
    { type: "value", name: "RPS" },
    { type: "value", name: "失败数", position: "right" },
    { type: "value", name: "P95 (ms)", position: "right", offset: 58 },
  ],
  series: [
    {
      name: "RPS（累计）",
      type: "line",
      smooth: true,
      yAxisIndex: 0,
      data: samples.value.map((item) => sampleMetrics(item).rps ?? 0),
    },
    {
      name: "失败数",
      type: "line",
      yAxisIndex: 1,
      data: samples.value.map((item) => sampleMetrics(item).failures ?? 0),
    },
    {
      name: "P95(ms)",
      type: "line",
      yAxisIndex: 2,
      data: samples.value.map((item) => sampleMetrics(item).p95 ?? 0),
    },
  ],
}));
const formatTime = (value) =>
  value ? dayjs(value).format("YYYY-MM-DD HH:mm:ss") : "-";
const statusType = (status) =>
  ({
    completed: "success",
    failed: "danger",
    incomplete: "danger",
    cancelled: "info",
    stopping: "warning",
    preparing: "warning",
    queued: "warning",
    running: "primary",
  })[status] || "info";
function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = undefined;
}
function currentScope() {
  return { projectId: projectId.value, runId: runId.value, scopeEpoch };
}
function scopeIsCurrent(scope) {
  return samePerformanceRunScope(scope, currentScope());
}
function schedulePolling(delay = 2000) {
  stopPolling();
  pollTimer = window.setTimeout(loadRun, delay);
}
async function loadRun() {
  const scope = currentScope();
  const currentRequestEpoch = ++requestEpoch;
  stopPolling();
  if (!scope.projectId || !scope.runId) return;
  loading.value = true;
  try {
    const [access, response] = await Promise.all([
      getProject(scope.projectId),
      getPerformanceRun(scope.projectId, scope.runId),
    ]);
    if (currentRequestEpoch !== requestEpoch || !scopeIsCurrent(scope)) return;
    project.value = access?.data ?? access;
    run.value = response?.data ?? response;
    pollFailures.value = 0;
    pollError.value = "";
    if (isPerformanceRunActive(run.value?.status)) schedulePolling();
  } catch (error) {
    if (currentRequestEpoch === requestEpoch && scopeIsCurrent(scope)) {
      pollFailures.value += 1;
      if (pollFailures.value <= 3) {
        pollError.value = `自动刷新失败，正在重试（${pollFailures.value}/3）`;
        schedulePolling(Math.min(10000, pollFailures.value * 2000));
      } else pollError.value = "自动刷新已暂停，请点击“刷新”重试。";
    }
  } finally {
    if (currentRequestEpoch === requestEpoch && scopeIsCurrent(scope))
      loading.value = false;
  }
}
async function stopRun() {
  const scope = currentScope();
  if (stopping.value || !canStopPerformanceRun(run.value?.status)) return;
  try {
    await ElMessageBox.confirm(
      "将请求控制器停止本次运行，统计可能显示为不完整。",
      "停止执行",
      { type: "warning", confirmButtonText: "停止" },
    );
  } catch {
    return;
  }
  if (!scopeIsCurrent(scope) || stopping.value) return;
  stopping.value = true;
  try {
    await stopPerformanceRun(scope.projectId, scope.runId);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("已请求停止执行");
    await loadRun();
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "停止执行失败"));
  } finally {
    if (scopeIsCurrent(scope)) stopping.value = false;
  }
}
watch([projectId, runId], () => {
  scopeEpoch += 1;
  stopPolling();
  run.value = null;
  project.value = null;
  pollError.value = "";
  pollFailures.value = 0;
  stopping.value = false;
  loadRun();
});
onMounted(loadRun);
onBeforeUnmount(() => {
  scopeEpoch += 1;
  stopping.value = false;
  stopPolling();
});
</script>

<style scoped>
.performance-run-detail {
  max-width: 1280px;
  margin: 0 auto;
  padding: 4px 10px 28px;
}
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.summary {
  margin: 16px 0;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(110px, 1fr));
  gap: 12px;
  margin: 18px 0;
}
.metric-card {
  border: 1px solid var(--app-border-light);
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 5px;
}
.metric-card span {
  color: var(--app-text-muted);
  font-size: 13px;
}
.metric-card strong {
  font-size: 20px;
}
.trend {
  height: 280px;
  width: 100%;
}
@media (max-width: 760px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
