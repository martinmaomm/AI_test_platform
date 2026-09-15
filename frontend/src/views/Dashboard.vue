<template>
  <div class="cockpit">
    <div class="grid-pattern" />
    <div class="cockpit-toolbar">
      <h1>◈ 数据面板</h1>
      <el-select
        v-model="selectedProjectId"
        placeholder="选择项目"
        filterable
        clearable
        :loading="projectsLoading"
        style="width: 220px"
        @change="onProjectChange"
      >
        <el-option
          v-for="project in allProjects"
          :key="project.id"
          :label="project.name"
          :value="project.id"
        />
      </el-select>
    </div>
    <el-alert
      v-if="projectsError"
      class="dashboard-alert"
      type="error"
      :closable="false"
      show-icon
      :title="projectsError"
      ><template #default
        ><el-button link type="primary" @click="loadProjectsAndSelect"
          >重试</el-button
        ></template
      ></el-alert
    >
    <el-alert
      v-else-if="dataError"
      class="dashboard-alert"
      type="error"
      :closable="false"
      show-icon
      :title="dataError"
      ><template #default
        ><el-button
          link
          type="primary"
          :disabled="!selectedProjectId"
          @click="retryData"
          >重试</el-button
        ></template
      ></el-alert
    >
    <div class="dashboard-grid">
      <template v-for="item in visibleItems" :key="item.i">
        <div
          v-if="item.i.startsWith('metric-')"
          class="dashboard-item metric"
          :class="item.i"
        >
          <el-icon class="metric-icon"
            ><VideoPlay v-if="item.i === 'metric-pass-rate'" /><List
              v-else-if="item.i === 'metric-executions'" /><Cpu
              v-else-if="item.i === 'metric-ai-rate'" /><Document v-else
          /></el-icon>
          <div>
            <el-tooltip :content="metricHint(item.i)"
              ><span class="metric-label">{{
                metricLabel(item.i)
              }}</span></el-tooltip
            ><strong>{{ metricValue(item.i) }}</strong>
          </div>
        </div>
        <div v-else-if="item.i === 'chart'" class="dashboard-item chart">
          <h2>自动化执行态势 (近7日)</h2>
          <div v-if="!selectedProjectId" class="state">
            请选择项目以查看统计数据
          </div>
          <div v-else-if="trendStatus === 'error'" class="state">
            {{ trendError }}
            <el-button link type="primary" @click="retryData">重试</el-button>
          </div>
          <v-chart
            v-else-if="chartReady"
            class="chart-canvas"
            :option="chartOption"
            autoresize
          />
          <el-skeleton v-else :rows="6" animated />
        </div>
        <div
          v-else-if="item.i === 'top-failures'"
          class="dashboard-item top-failures"
        >
          <h2>
            <el-icon><WarningFilled /></el-icon
            ><el-tooltip
              content="近 7 日失败次数最多的用例，包含工作区调试和 AI 验证；未保存用例显示场景名称。"
              ><span>高频报错用例 Top 5</span></el-tooltip
            >
          </h2>
          <div v-if="!selectedProjectId" class="state">
            请选择项目以查看统计数据
          </div>
          <el-skeleton
            v-else-if="topFailuresStatus === 'loading'"
            :rows="4"
            animated
          />
          <div v-else-if="topFailuresStatus === 'error'" class="state">
            {{ topFailuresError }}
            <el-button link type="primary" @click="retryData">重试</el-button>
          </div>
          <el-table
            v-else-if="topFailures.length"
            :data="topFailures"
            size="small"
            ><el-table-column
              type="index"
              label="#"
              width="36" /><el-table-column
              prop="test_case_name"
              label="用例名称"
              min-width="120"
              show-overflow-tooltip /><el-table-column
              prop="fail_count"
              label="失败次数"
              width="80"
          /></el-table>
          <div v-else class="state">近 7 天无失败记录</div>
        </div>
        <div
          v-else
          class="dashboard-item portal"
          :class="[item.i, { disabled: portalDisabled(item.i) }]"
          @click="!portalDisabled(item.i) && openPortal(item.i)"
        >
          <el-icon
            ><Connection v-if="item.i === 'portal-api'" /><Monitor
              v-else-if="item.i === 'portal-web'" /><Cellphone
              v-else-if="item.i === 'portal-app'" /><Timer
              v-else-if="item.i === 'portal-perf'" /><Cpu
              v-else-if="item.i === 'portal-ai-config'" /><Setting v-else
          /></el-icon>
          <div>
            <h2>{{ portalTitle(item.i) }}</h2>
            <p>{{ portalDescription(item.i) }}</p>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { useRouter } from "vue-router";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart } from "echarts/charts";
import {
  TooltipComponent,
  GridComponent,
  LegendComponent,
} from "echarts/components";
import VChart from "vue-echarts";
import {
  Document,
  List,
  VideoPlay,
  Connection,
  Monitor,
  Cellphone,
  Timer,
  Cpu,
  WarningFilled,
  Setting,
} from "@element-plus/icons-vue";
import { useAppStore } from "@/stores/app";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";
import { visibleDashboardItems } from "@/utils/accessControl";
import {
  EMPTY_DASHBOARD_SUMMARY,
  createDashboardRequestGate,
  dashboardProjectsFromResponse,
} from "@/utils/dashboardState";
import { getProjects } from "@/api/projects";
import {
  getDashboardSummary,
  getDashboardTrend,
  getDashboardTopFailures,
} from "@/api/dashboard";

use([
  CanvasRenderer,
  LineChart,
  TooltipComponent,
  GridComponent,
  LegendComponent,
]);
const router = useRouter(),
  appStore = useAppStore(),
  projectStore = useProjectStore(),
  authStore = useAuthStore();
const { effectiveTheme } = storeToRefs(appStore);
const requestGate = createDashboardRequestGate();
const dashboardItems = [
  { i: "metric-pass-rate" },
  { i: "metric-executions" },
  { i: "metric-ai-rate" },
  { i: "metric-total-cases" },
  { i: "chart" },
  { i: "top-failures" },
  { i: "portal-api" },
  { i: "portal-web" },
  { i: "portal-perf" },
  { i: "portal-app" },
  { i: "portal-ai-config" },
  { i: "portal-settings" },
];
const visibleItems = computed(() =>
  visibleDashboardItems(dashboardItems, authStore.user),
);
const allProjects = ref([]),
  selectedProjectId = ref(null),
  projectsLoading = ref(false),
  projectsError = ref(""),
  dataError = ref("");
const summary = ref({ ...EMPTY_DASHBOARD_SUMMARY }),
  summaryStatus = ref("idle");
const chartReady = ref(false),
  trendStatus = ref("idle"),
  trendError = ref(""),
  chartData = ref({ dates: [], passed: [], failed: [], pass_rates: [] }),
  chartOption = ref({});
const topFailures = ref([]),
  topFailuresStatus = ref("idle"),
  topFailuresError = ref("");
const resetData = () => {
  dataError.value = "";
  summary.value = { ...EMPTY_DASHBOARD_SUMMARY };
  summaryStatus.value = "idle";
  chartReady.value = false;
  trendStatus.value = "idle";
  trendError.value = "";
  chartData.value = { dates: [], passed: [], failed: [], pass_rates: [] };
  topFailures.value = [];
  topFailuresStatus.value = "idle";
  topFailuresError.value = "";
};
const apiError = (error, fallback) =>
  error?.response?.data?.message || error?.message || fallback;
const setError = (message) => {
  dataError.value ||= message;
};
async function loadProjectsAndSelect() {
  projectsLoading.value = true;
  projectsError.value = "";
  try {
    allProjects.value = dashboardProjectsFromResponse(
      await getProjects({ page: 1, page_size: 1000 }),
    );
    const id = projectStore.currentProjectId;
    selectedProjectId.value = allProjects.value.some((p) => p.id === id)
      ? id
      : null;
    const request = requestGate.begin(selectedProjectId.value);
    resetData();
    if (selectedProjectId.value) fetchAllData(request);
  } catch (error) {
    allProjects.value = [];
    selectedProjectId.value = null;
    requestGate.begin(null);
    resetData();
    projectsError.value = apiError(error, "项目列表加载失败，请重试");
  } finally {
    projectsLoading.value = false;
  }
}
async function onProjectChange(id) {
  const projectId = id || null,
    request = requestGate.begin(projectId);
  resetData();
  try {
    if (!projectId) await projectStore.clearCurrentProject();
    else {
      const project = allProjects.value.find((p) => p.id === projectId);
      if (project) await projectStore.setCurrentProject(project);
    }
  } catch (error) {
    setError(apiError(error, "保存当前项目失败，请重试"));
  }
  if (requestGate.isCurrent(request) && projectId) fetchAllData(request);
}
async function fetchSummary(request) {
  summaryStatus.value = "loading";
  try {
    const response = await getDashboardSummary(request.projectId);
    if (!requestGate.isCurrent(request)) return;
    const data = response?.data ?? response;
    summary.value = {
      today_pass_rate: data?.today_pass_rate ?? 0,
      today_executions: data?.today_executions ?? 0,
      ai_contribution_rate: data?.ai_contribution_rate ?? 0,
      total_cases: data?.total_cases ?? 0,
    };
    summaryStatus.value = "ready";
  } catch (error) {
    if (!requestGate.isCurrent(request)) return;
    summaryStatus.value = "error";
    setError(apiError(error, "统计数据加载失败，请重试"));
  }
}
const emptyTrend = () => {
  const dates = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - 6 + i);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  });
  return {
    dates,
    passed: Array(7).fill(0),
    failed: Array(7).fill(0),
    pass_rates: Array(7).fill(0),
  };
};
async function fetchTrend(request) {
  trendStatus.value = "loading";
  try {
    const response = await getDashboardTrend(request.projectId);
    if (!requestGate.isCurrent(request)) return;
    const list = response?.data ?? response ?? [];
    const data =
      Array.isArray(list) && list.length
        ? {
            dates: list.map((d) =>
              String(d.date || "")
                .split("-")
                .slice(1)
                .join("/"),
            ),
            passed: list.map((d) => d.passed ?? 0),
            failed: list.map((d) => d.failed ?? 0),
            pass_rates: list.map((d) => d.pass_rate ?? 0),
          }
        : emptyTrend();
    chartData.value = data;
    chartOption.value = chartConfig(data);
    chartReady.value = true;
    trendStatus.value = "ready";
  } catch (error) {
    if (!requestGate.isCurrent(request)) return;
    trendStatus.value = "error";
    trendError.value = apiError(error, "趋势数据加载失败，请重试");
    setError(trendError.value);
  }
}
async function fetchTopFailures(request) {
  topFailuresStatus.value = "loading";
  try {
    const response = await getDashboardTopFailures(request.projectId);
    if (!requestGate.isCurrent(request)) return;
    const data = response?.data ?? response ?? [];
    topFailures.value = Array.isArray(data) ? data : [];
    topFailuresStatus.value = "ready";
  } catch (error) {
    if (!requestGate.isCurrent(request)) return;
    topFailuresStatus.value = "error";
    topFailuresError.value = apiError(error, "高频报错用例加载失败，请重试");
    setError(topFailuresError.value);
  }
}
function fetchAllData(request) {
  if (!request?.projectId || !requestGate.isCurrent(request)) return;
  void fetchSummary(request);
  void fetchTrend(request);
  void fetchTopFailures(request);
}
function retryData() {
  if (!selectedProjectId.value) return;
  const request = requestGate.begin(selectedProjectId.value);
  resetData();
  fetchAllData(request);
}
function chartConfig(data) {
  const dark = effectiveTheme.value === "dark";
  return {
    tooltip: { trigger: "axis" },
    legend: {
      data: ["通过", "失败"],
      bottom: 0,
      textStyle: { color: dark ? "#8b949e" : "#606266" },
    },
    grid: { left: "3%", right: "4%", bottom: "15%", containLabel: true },
    xAxis: { type: "category", data: data.dates },
    yAxis: { type: "value", minInterval: 1 },
    series: [
      { name: "通过", type: "line", smooth: true, data: data.passed },
      { name: "失败", type: "line", smooth: true, data: data.failed },
    ],
  };
}
const metricLabel = (i) =>
  ({
    "metric-pass-rate": "今日通过率",
    "metric-executions": "今日执行数",
    "metric-ai-rate": "AI 生成用例占比",
    "metric-total-cases": "总用例数",
  })[i];
const metricHint = (i) =>
  ({
    "metric-pass-rate":
      "今日已结束用例执行中，通过占通过与失败；进行中、跳过和未完成不计入。",
    "metric-executions": "今日已结束的顶层执行记录；套件按一次计。",
    "metric-ai-rate":
      "当前项目首次保存为 AI 生成的用例数 ÷ 已保存用例总数。后续编辑、AI 修复或删除工作区不改变来源；仅经 AI 修复的手工用例仍算手工。未保存草稿不计入，历史来源未知的用例计入总数但不计为 AI。",
    "metric-total-cases": "当前项目的用例总数。",
  })[i];
const metricValue = (i) => {
  if (summaryStatus.value !== "ready") return "--";
  const s = summary.value;
  return i === "metric-pass-rate"
    ? `${s.today_pass_rate}%`
    : i === "metric-executions"
      ? s.today_executions
      : i === "metric-ai-rate"
        ? `${s.ai_contribution_rate}%`
        : s.total_cases;
};
const portalDisabled = (i) => ["portal-app", "portal-perf"].includes(i);
const portalTitle = (i) =>
  ({
    "portal-api": "API 自动化",
    "portal-web": "Web 自动化",
    "portal-app": "App 自动化（开发中）",
    "portal-perf": "性能测试（开发中）",
    "portal-ai-config": "AI 实验室配置",
    "portal-settings": "全局系统设置",
  })[i];
const portalDescription = (i) =>
  ({
    "portal-api":
      "基于接口文档或网页探索，通过 AI 对话生成、验证和修复接口测试用例。",
    "portal-web":
      "基于 Playwright MCP 自动生成自动化代码，支持自动修复和运行。",
    "portal-app": "移动端 UI 自动化、POM 解析与智能体。",
    "portal-perf": "负载压测、性能分析与专项测试。",
    "portal-ai-config": "LLM 厂商对接、RAG 向量库配置、MCP 协议管理。",
    "portal-settings": "邮件通知配置、环境变量、用户权限。",
  })[i];
function openPortal(i) {
  const path = {
    "portal-api": "/api-testing/projects",
    "portal-web": "/web-testing/projects",
    "portal-ai-config": "/ai-config",
    "portal-settings": "/settings",
  }[i];
  if (path) router.push(path);
}
watch(effectiveTheme, () => {
  if (trendStatus.value === "ready")
    chartOption.value = chartConfig(chartData.value);
});
async function initializeProjectPreference() {
  let timeoutId;
  const timeout = new Promise((resolve) => {
    timeoutId = window.setTimeout(resolve, 5000);
  });

  try {
    await Promise.race([projectStore.initializeUserPreferences(), timeout]);
  } catch (error) {
    dataError.value = apiError(error, "当前项目初始化失败，请手动选择项目");
  } finally {
    window.clearTimeout(timeoutId);
  }
}

onMounted(async () => {
  await initializeProjectPreference();
  await loadProjectsAndSelect();
});
</script>

<style scoped>
.cockpit {
  min-height: 100vh;
  padding: 24px;
  position: relative;
  overflow-x: hidden;
  background: var(--cockpit-bg);
  color: var(--cockpit-text-primary);
}
.grid-pattern {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background-image:
    linear-gradient(var(--cockpit-grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--cockpit-grid-line) 1px, transparent 1px);
  background-size: 24px 24px;
}
.cockpit-toolbar,
.dashboard-alert,
.dashboard-grid {
  position: relative;
  z-index: 1;
}
.cockpit-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.dashboard-alert {
  margin-bottom: 16px;
}
.dashboard-item {
  min-width: 0;
  background: var(--cockpit-card-bg);
  backdrop-filter: blur(var(--cockpit-blur));
  border: 1px solid var(--cockpit-card-border);
  border-radius: 12px;
  box-shadow: var(--cockpit-card-shadow);
}
.dashboard-item:hover {
  background: var(--cockpit-card-bg-hover);
  box-shadow: var(--cockpit-card-shadow-hover);
}
.state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  color: var(--cockpit-text-muted);
}
.dashboard-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-template-areas: "pass exec ai cases" "chart chart chart failures" "chart chart chart failures" "api web perf app" "config config settings settings";
}
.metric-pass-rate {
  grid-area: pass;
}
.metric-executions {
  grid-area: exec;
}
.metric-ai-rate {
  grid-area: ai;
}
.metric-total-cases {
  grid-area: cases;
}
.chart {
  grid-area: chart;
  min-height: 380px;
}
.top-failures {
  grid-area: failures;
}
.portal-api {
  grid-area: api;
}
.portal-web {
  grid-area: web;
}
.portal-perf {
  grid-area: perf;
}
.portal-app {
  grid-area: app;
}
.portal-ai-config {
  grid-area: config;
}
.portal-settings {
  grid-area: settings;
}
.metric,
.portal {
  display: flex;
  gap: 16px;
  padding: 20px;
  align-items: center;
}
.metric-icon,
.portal > .el-icon {
  font-size: 28px;
  color: #409eff;
}
.metric-ai-rate .metric-icon {
  color: #764ba2;
}
.metric-label {
  display: block;
  color: var(--cockpit-text-muted);
  cursor: help;
}
.metric strong {
  font-size: 24px;
}
.chart,
.top-failures {
  padding: 20px;
}
.chart h2,
.top-failures h2,
.portal h2 {
  margin: 0 0 12px;
  font-size: 16px;
}
.chart-canvas {
  height: 300px;
}
.top-failures h2 {
  display: flex;
  gap: 8px;
  align-items: center;
}
.portal {
  cursor: pointer;
}
.portal:hover {
  transform: translateY(-4px);
}
.portal.disabled {
  filter: grayscale(1);
  opacity: 0.55;
  cursor: not-allowed;
}
.portal.disabled:hover {
  transform: none;
}
.portal p {
  margin: 0;
  color: var(--cockpit-text-secondary);
}
@media (max-width: 800px) {
  .dashboard-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-areas: "pass exec" "ai cases" "chart chart" "failures failures" "api web" "perf app" "config settings";
  }
}
@media (max-width: 520px) {
  .cockpit {
    padding: 16px;
  }
  .cockpit-toolbar {
    align-items: flex-start;
    gap: 12px;
    flex-direction: column;
  }
  .dashboard-grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: "pass" "exec" "ai" "cases" "chart" "failures" "api" "web" "perf" "app" "config" "settings";
  }
}
</style>
