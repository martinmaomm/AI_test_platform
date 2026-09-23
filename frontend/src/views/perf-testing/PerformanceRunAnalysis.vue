<template>
  <section
    v-if="available"
    class="performance-run-analysis"
    data-testid="performance-run-analysis"
  >
    <div class="analysis-heading">
      <div>
        <h3>AI 分析结果</h3>
        <p>只发送统计摘要，不发送原始请求或响应内容。</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新记录</el-button>
    </div>
    <el-alert v-if="!canReport" type="warning" :closable="false" show-icon
      >你没有查看分析结果的权限。</el-alert
    >
    <template v-else>
      <el-alert v-if="error" type="error" :closable="false" show-icon>{{
        error
      }}</el-alert>
      <el-alert v-if="activeAnalysis" type="info" :closable="false" show-icon
        >{{
          analysisStatusLabel(activeAnalysis.status)
        }}，结果生成后会自动刷新。</el-alert
      >
      <div class="analysis-actions">
        <el-select
          v-model="modelConfigId"
          :loading="modelsLoading"
          placeholder="选择可用 LLM 模型"
          :disabled="
            creating || Boolean(activeAnalysis) || Boolean(pendingPayload)
          "
          clearable
        >
          <el-option
            v-for="model in models"
            :key="model.id"
            :label="modelLabel(model)"
            :value="model.id"
          />
        </el-select>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!canCreate"
          @click="create"
          >AI 分析结果</el-button
        >
      </div>
      <el-alert
        v-if="!modelsLoading && !models.length"
        type="warning"
        :closable="false"
        >暂无可用 LLM 模型。请在模型配置中启用一个 LLM
        模型后刷新本页。</el-alert
      >
      <el-alert v-if="!canExecute" type="info" :closable="false"
        >你可以查看结果；发起 AI 分析需要执行权限。</el-alert
      >
      <el-form
        class="target-form"
        label-position="top"
        :disabled="
          creating || Boolean(activeAnalysis) || Boolean(pendingPayload)
        "
      >
        <el-form-item label="P95 上限（毫秒，可选）"
          ><el-input-number
            v-model="targets.p95_ms"
            :min="0"
            :precision="2"
            controls-position="right"
        /></el-form-item>
        <el-form-item label="错误率上限（%，可选）"
          ><el-input-number
            v-model="targets.error_rate_percent"
            :min="0"
            :max="100"
            :precision="2"
            controls-position="right"
        /></el-form-item>
        <el-form-item label="RPS 下限（可选）"
          ><el-input-number
            v-model="targets.rps_min"
            :min="0"
            :precision="2"
            controls-position="right"
        /></el-form-item>
      </el-form>
      <p class="target-note">目标仅用于本次分析；未填写时，只分析实际表现，不判定是否达标。</p>
      <el-empty
        v-if="!loading && !selectedAnalysis && !analyses.length"
        description="尚无 AI 分析记录"
      />
      <el-radio-group
        v-if="analyses.length"
        v-model="selectedId"
        class="analysis-history"
        @change="() => loadSelected()"
      >
        <el-radio-button
          v-for="item in analyses"
          :key="item.id"
          :label="item.id"
          ><el-tag :type="analysisStatusType(item.status)">{{
            analysisStatusLabel(item.status)
          }}</el-tag>
          {{ formatTime(item.created_at) }}</el-radio-button
        >
      </el-radio-group>
      <article v-if="selectedAnalysis" class="analysis-result">
        <div class="analysis-meta">
          <el-tag :type="analysisStatusType(selectedAnalysis.status)">{{
            analysisStatusLabel(selectedAnalysis.status)
          }}</el-tag
          ><span>{{ modelInfo(selectedAnalysis) }}</span
          ><span>创建：{{ formatTime(selectedAnalysis.created_at) }}</span
          ><span v-if="selectedAnalysis.finished_at"
            >完成：{{ formatTime(selectedAnalysis.finished_at) }}</span
          >
        </div>
        <el-alert
          v-if="selectedAnalysis.status === 'failed'"
          type="error"
          :closable="false"
          show-icon
          >{{
            selectedAnalysis.error_message || "分析失败，请重试。"
          }}</el-alert
        >
        <template
          v-if="
            selectedAnalysis.result && isAnalysisCompleted(selectedAnalysis)
          "
        >
          <h4>性能目标判断</h4>
          <el-descriptions :column="1" border
            ><el-descriptions-item label="结论">{{
              assessmentLabel(selectedAnalysis.result.assessment?.status)
            }}</el-descriptions-item
            ><el-descriptions-item
              v-for="check in selectedAnalysis.result.assessment?.checks || []"
              :key="check.key"
              :label="analysisCheckLabel(check.key)"
              >{{ displayAnalysisValue(check.actual) }} {{ check.unit || "" }} /
              目标 {{ displayAnalysisValue(check.target) }}
              {{ check.unit || "" }}（{{
                assessmentLabel(check.status)
              }}）</el-descriptions-item
            ></el-descriptions
          >
          <h4>分析摘要</h4>
          <p class="preserve-text">
            {{ selectedAnalysis.result.summary || "未返回摘要。" }}
          </p>
          <h4 v-if="findings.length">发现与建议</h4>
          <div
            v-for="finding in findings"
            :key="`${finding.title}-${finding.detail}`"
            class="finding"
          >
            <el-tag :type="findingKindType(finding.kind)">{{
              analysisFindingKindLabel(finding.kind)
            }}</el-tag
            ><el-tag :type="severityType(finding.severity)">{{
              analysisSeverityLabel(finding.severity)
            }}</el-tag
            ><strong>{{ finding.title }}</strong>
            <p class="preserve-text">{{ finding.detail }}</p>
            <p v-if="finding.recommendation" class="preserve-text">
              建议：{{ finding.recommendation }}
            </p>
            <p v-if="findingEvidence(finding).length" class="evidence-ids">
              依据：<span
                v-for="item in findingEvidence(finding)"
                :key="item.id"
                >{{ evidenceLabel(item) }}：{{ displayEvidenceValue(item.value)
                }}{{ item.unit || "" }}；</span
              >
            </p>
          </div>
          <template v-if="evidence.length"
            ><h4>统计证据</h4>
            <el-collapse v-model="evidenceExpanded" class="evidence-collapse"
              ><el-collapse-item
                :title="`查看统计证据（${evidence.length} 条）`"
                name="all"
                ><el-table :data="evidence" size="small"
                  ><el-table-column
                    prop="id"
                    label="ID"
                    min-width="120"
                  /><el-table-column label="指标" min-width="180"
                    ><template #default="{ row }">{{
                      evidenceLabel(row)
                    }}</template></el-table-column
                  ><el-table-column label="值" min-width="220"
                    ><template #default="{ row }"
                      >{{ displayEvidenceValue(row.value) }}
                      {{ row.unit || "" }}</template
                    ></el-table-column
                  ></el-table
                ></el-collapse-item
              ></el-collapse
            ></template
          >
          <template v-if="limitations.length"
            ><h4>限制条件</h4>
            <ul>
              <li v-for="item in limitations" :key="item" class="preserve-text">
                {{ item }}
              </li>
            </ul></template
          >
        </template>
      </article>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { getAvailableLLMConfigurations } from "@/api/aiConfig";
import {
  createPerformanceRunAnalysis,
  getPerformanceRunAnalyses,
  getPerformanceRunAnalysis,
  performanceErrorMessage,
} from "@/api/performance";
import {
  analysisCheckLabel,
  analysisFindingKindLabel,
  analysisItems,
  analysisRecord,
  analysisSeverityLabel,
  analysisStatusLabel,
  analysisStatusType,
  analysisTargetsIssue,
  buildAnalysisRequest,
  canAnalyzePerformanceRun,
  displayAnalysisValue,
  displayEvidenceValue,
  isAnalysisActive,
  isAnalysisCompleted,
  isDefinitiveAnalysisCreateError,
  modelLabel,
} from "./performanceAnalysisState";

const props = defineProps({
  projectId: { type: [String, Number], required: true },
  runId: { type: [String, Number], required: true },
  run: { type: Object, default: null },
  canReport: Boolean,
  canExecute: Boolean,
});
const available = computed(() => canAnalyzePerformanceRun(props.run));
const models = ref([]);
const modelConfigId = ref(null);
const modelsLoading = ref(false);
const analyses = ref([]);
const selectedId = ref(null);
const selectedAnalysis = ref(null);
const loading = ref(false);
const creating = ref(false);
const error = ref("");
const targets = ref({
  p95_ms: undefined,
  error_rate_percent: undefined,
  rps_min: undefined,
});
const evidenceExpanded = ref([]);
let pollTimer;
let scopeEpoch = 0;
let requestEpoch = 0;
let detailEpoch = 0;
const pendingPayload = ref(null);
const activeAnalysis = computed(
  () =>
    analyses.value.find(isAnalysisActive) ||
    (isAnalysisActive(selectedAnalysis.value) ? selectedAnalysis.value : null),
);
const canCreate = computed(
  () =>
    props.canExecute &&
    props.canReport &&
    available.value &&
    !modelsLoading.value &&
    models.value.some(
      (model) => String(model.id) === String(modelConfigId.value),
    ) &&
    !creating.value &&
    !activeAnalysis.value,
);
const findings = computed(() =>
  Array.isArray(selectedAnalysis.value?.result?.findings)
    ? selectedAnalysis.value.result.findings
    : [],
);
const evidence = computed(() =>
  Array.isArray(selectedAnalysis.value?.result?.evidence)
    ? selectedAnalysis.value.result.evidence
    : [],
);
const evidenceById = computed(
  () => new Map(evidence.value.map((item) => [String(item.id), item])),
);
const limitations = computed(() =>
  Array.isArray(selectedAnalysis.value?.result?.limitations)
    ? selectedAnalysis.value.result.limitations
    : [],
);
const scope = () => ({
  projectId: String(props.projectId || ""),
  runId: String(props.runId || ""),
  epoch: scopeEpoch,
});
const scopeCurrent = (value) =>
  value.epoch === scopeEpoch &&
  value.projectId === String(props.projectId || "") &&
  value.runId === String(props.runId || "");
const unwrapModels = (response) => {
  const body = response?.data ?? response;
  const list = Array.isArray(body)
    ? body
    : Array.isArray(body?.data)
      ? body.data
      : [];
  return list.filter(
    (item) => item?.is_active === true && item?.model_type === "llm",
  );
};
const stopPolling = () => {
  clearTimeout(pollTimer);
  pollTimer = undefined;
};
const schedulePolling = () => {
  stopPolling();
  pollTimer = window.setTimeout(refresh, 2000);
};
const formatTime = (value) =>
  value ? dayjs(value).format("YYYY-MM-DD HH:mm:ss") : "-";
const modelInfo = (analysis) => modelLabel(analysis.model_info || {});
const assessmentLabel = (status) =>
  ({
    met: "达标",
    not_met: "未达标",
    not_configured: "未配置目标",
    insufficient_data: "数据不足",
  })[status] ||
  status ||
  "-";
const severityType = (value) =>
  ({ critical: "danger", warning: "warning", info: "info" })[value] || "info";
const findingKindType = (value) =>
  ({ observation: "success", hypothesis: "warning" })[value] || "info";
const evidenceLabel = (item = {}) => {
  const match = /^(endpoint|node)\.(\d+)$/.exec(String(item.id || ""));
  if (!match) return item.label || item.id || "-";
  const index = Number(match[2]) - 1;
  const source =
    match[1] === "endpoint"
      ? props.run?.latest_metrics?.entries?.[index]
      : props.run?.nodes?.[index];
  const localName =
    match[1] === "endpoint"
      ? [source?.method, source?.name].filter(Boolean).join(" ")
      : source?.node_name || source?.node_id;
  return localName
    ? `${item.label || item.id}（${localName}）`
    : item.label || item.id;
};
const findingEvidence = (finding = {}) =>
  (Array.isArray(finding.evidence_ids) ? finding.evidence_ids : [])
    .map((id) => evidenceById.value.get(String(id)))
    .filter(Boolean);

async function loadModels(captured) {
  modelsLoading.value = true;
  try {
    const response = await getAvailableLLMConfigurations();
    if (!scopeCurrent(captured)) return;
    models.value = unwrapModels(response);
    if (
      !models.value.some(
        (item) => String(item.id) === String(modelConfigId.value),
      )
    )
      modelConfigId.value = models.value[0]?.id ?? null;
  } catch (cause) {
    if (!scopeCurrent(captured)) return;
    models.value = [];
    ElMessage.warning(performanceErrorMessage(cause, "可用模型列表加载失败"));
  } finally {
    if (scopeCurrent(captured)) modelsLoading.value = false;
  }
}
async function loadSelected(refreshSequence = null) {
  const captured = scope();
  const id = selectedId.value;
  const sequence = ++detailEpoch;
  if (!id) return null;
  if (String(selectedAnalysis.value?.id) !== String(id))
    selectedAnalysis.value = null;
  try {
    const response = await getPerformanceRunAnalysis(
      captured.projectId,
      captured.runId,
      id,
    );
    if (
      !scopeCurrent(captured) ||
      sequence !== detailEpoch ||
      (refreshSequence != null && refreshSequence !== requestEpoch) ||
      String(selectedId.value) !== String(id)
    )
      return null;
    selectedAnalysis.value = analysisRecord(response);
    if (isAnalysisActive(selectedAnalysis.value)) schedulePolling();
    return selectedAnalysis.value;
  } catch (cause) {
    if (
      scopeCurrent(captured) &&
      sequence === detailEpoch &&
      (refreshSequence == null || refreshSequence === requestEpoch) &&
      String(selectedId.value) === String(id)
    )
      error.value = performanceErrorMessage(cause, "加载分析详情失败");
    return null;
  }
}
async function refresh() {
  const captured = scope();
  const sequence = ++requestEpoch;
  stopPolling();
  if (!available.value || !props.canReport) return;
  loading.value = true;
  try {
    const response = await getPerformanceRunAnalyses(
      captured.projectId,
      captured.runId,
    );
    if (sequence !== requestEpoch || !scopeCurrent(captured)) return;
    analyses.value = analysisItems(response);
    const current = analyses.value.find(
      (item) => String(item.id) === String(selectedId.value),
    );
    selectedId.value = current?.id ?? analyses.value[0]?.id ?? null;
    if (
      !selectedId.value ||
      String(selectedAnalysis.value?.id) !== String(selectedId.value)
    )
      selectedAnalysis.value = null;
    error.value = "";
    if (selectedId.value) await loadSelected(sequence);
    if (sequence !== requestEpoch || !scopeCurrent(captured)) return;
    if (analyses.value.some(isAnalysisActive)) schedulePolling();
  } catch (cause) {
    if (sequence === requestEpoch && scopeCurrent(captured))
      error.value = performanceErrorMessage(cause, "加载 AI 分析记录失败");
  } finally {
    if (sequence === requestEpoch && scopeCurrent(captured))
      loading.value = false;
  }
}
async function create() {
  const captured = scope();
  if (!canCreate.value) return;
  const issue = pendingPayload.value ? "" : analysisTargetsIssue(targets.value);
  if (issue) return ElMessage.warning(issue);
  const retryingUncertainRequest = Boolean(pendingPayload.value);
  const payload =
    pendingPayload.value ||
    buildAnalysisRequest(modelConfigId.value, targets.value);
  creating.value = true;
  try {
    pendingPayload.value = payload;
    const response = await createPerformanceRunAnalysis(
      captured.projectId,
      captured.runId,
      payload,
    );
    if (!scopeCurrent(captured)) return;
    const record = analysisRecord(response);
    pendingPayload.value = null;
    ElMessage.success(
      isAnalysisActive(record) ? "已提交 AI 分析任务" : "AI 分析记录已创建",
    );
    selectedId.value = record.id;
    await refresh();
  } catch (cause) {
    if (!scopeCurrent(captured)) return;
    const status = cause?.response?.status;
    if (status === 409) {
      pendingPayload.value = null;
      await refresh();
      if (!scopeCurrent(captured)) return;
      error.value = performanceErrorMessage(
        cause,
        "无法创建分析，已刷新现有记录。",
      );
    } else if (isDefinitiveAnalysisCreateError(status)) {
      pendingPayload.value = null;
      error.value = performanceErrorMessage(cause, "创建 AI 分析失败");
    } else {
      error.value = `${performanceErrorMessage(cause, "创建 AI 分析请求未确认")}；再次点击将复用原模型和目标。`;
      if (!retryingUncertainRequest)
        ElMessage.warning("请求结果未确认，请勿为本次重试修改模型或目标。");
    }
  } finally {
    if (scopeCurrent(captured)) creating.value = false;
  }
}
function reset() {
  scopeEpoch += 1;
  requestEpoch += 1;
  detailEpoch += 1;
  stopPolling();
  analyses.value = [];
  selectedId.value = null;
  selectedAnalysis.value = null;
  evidenceExpanded.value = [];
  error.value = "";
  loading.value = false;
  creating.value = false;
  modelsLoading.value = false;
  models.value = [];
  modelConfigId.value = null;
  targets.value = {
    p95_ms: undefined,
    error_rate_percent: undefined,
    rps_min: undefined,
  };
  pendingPayload.value = null;
  if (available.value && props.canReport) {
    const captured = scope();
    loadModels(captured);
    refresh();
  }
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
  scopeEpoch += 1;
  detailEpoch += 1;
  stopPolling();
});
</script>

<style scoped>
.performance-run-analysis {
  margin-top: 24px;
  border-top: 1px solid var(--app-border-light);
  padding-top: 18px;
}
.analysis-heading,
.analysis-actions,
.analysis-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.analysis-heading {
  justify-content: space-between;
}
.analysis-heading h3 {
  margin: 0;
}
.analysis-heading p,
.target-note {
  color: var(--app-text-muted);
  font-size: 13px;
  margin: 5px 0;
}
.analysis-actions {
  margin: 14px 0;
}
.analysis-actions .el-select {
  width: 280px;
}
.target-form {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.target-form .el-form-item {
  margin-bottom: 0;
}
.analysis-history {
  margin: 16px 0;
  display: flex;
  flex-wrap: wrap;
}
.analysis-result {
  display: grid;
  gap: 10px;
}
.analysis-meta,
.evidence-ids {
  color: var(--app-text-muted);
  font-size: 13px;
}
.analysis-result h4 {
  margin: 12px 0 0;
}
.finding {
  border-left: 3px solid var(--el-color-primary);
  padding: 8px 12px;
  background: var(--el-fill-color-lighter);
}
.finding strong {
  margin-left: 8px;
}
.finding p {
  margin: 7px 0;
}
.preserve-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.evidence-collapse {
  margin-top: 4px;
}
</style>
